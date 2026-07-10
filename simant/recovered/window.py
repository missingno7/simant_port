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
