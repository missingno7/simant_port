"""Recovered SimAnt window helpers — VM-free, byte-exact.

Reconstructed from the shipped code (names from SIMANTW.SYM), verified against
the original ASM by the A/B oracle in simant/tests/test_hooks.py.
"""
from __future__ import annotations

from typing import Callable


def _sar16(value: int, count: int) -> int:
    """Arithmetic (sign-preserving) right shift of a 16-bit word, like `SAR`."""
    value &= 0xFFFF
    if value & 0x8000:
        value -= 0x10000
    return value >> count


def win_is_win_open(obj_handle: int,
                    hwnd_of_slot: Callable[[int], int],
                    is_window_visible: Callable[[int], int]) -> int:
    """Is the window named by `obj_handle` currently open (mapped and visible)?

    A SimAnt window "object handle" carries its window-table slot in the HIGH
    byte.  The window counts as open exactly when that slot holds a live `HWND`
    and USER reports the window visible — the original reads:

        HWND hwnd = g_window_hwnd[objHandle >> 8];   // word table at DGROUP:0xBCA6
        return hwnd && IsWindowVisible(hwnd);

    `hwnd_of_slot(slot)` reads that table; `is_window_visible(hwnd)` is
    USER.IsWindowVisible.  Both are injected so this file never imports the VM.

    Recovered from `_win_IsWinOpen` (SIMANTW.SYM seg7:C256, SIMTWO_MODULE): the
    slot index is `sar si,8` (arithmetic), the table pointer `shl` + `add 0xBCA6`,
    the emptiness test `cmp word [bx],0`, then a far `IsWindowVisible` call whose
    result is folded to a 0/1 boolean.
    """
    hwnd = hwnd_of_slot(_sar16(obj_handle, 8))
    if hwnd == 0:
        return 0
    return 1 if is_window_visible(hwnd) else 0


def win_get_obj_rect(obj_handle: int,
                     resolve_rect: Callable[[int, int], tuple],
                     inclusive_adjust: int) -> tuple:
    """Fetch object `obj_handle`'s rectangle, inclusive->exclusive if flagged.

    The handle's HIGH byte selects a window-table slot, its LOW byte an object
    index within that window; `resolve_rect(slot, obj)` returns that object's
    stored `(left, top, right, bottom)`.  In the original the resolver walks a
    far-pointer table at DGROUP:0xCE9A (slot -> window record), then the
    record's object-rect far-pointer array at record+0x2C (obj -> RECT).

    When `inclusive_adjust` (the global at DGROUP:0xBD0A) is set the stored rect
    is inclusive, so `right` and `bottom` are bumped by one to make it a
    half-open (exclusive) rect.  Injected resolver => this file never touches
    the VM.

    Recovered from `_win_GetObjRect` (SIMANTW.SYM seg7:C2D2, SIMTWO_MODULE),
    which brackets the copy with `_win_LockWin` / `_win_UnlockWin` — no-op stubs
    under the fixed Win16 memory model, so they carry no behaviour to recover.
    """
    left, top, right, bottom = resolve_rect(_sar16(obj_handle, 8), obj_handle & 0xFF)
    if inclusive_adjust:
        right = (right + 1) & 0xFFFF
        bottom = (bottom + 1) & 0xFFFF
    return left, top, right, bottom
