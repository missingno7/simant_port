"""Recovered SimAnt NetBIOS helpers (_TEXT, seg4) — VM-free, byte-exact.

The multiplayer transport's small utilities.  Verified against the original ASM
by the A/B oracle in ../tests/test_hooks.py.
"""
from __future__ import annotations


def cstrlen(data) -> int:
    """C `strlen`: bytes before the first NUL (unbounded, like `repne scasb`)."""
    n = 0
    while data[n] != 0:
        n += 1
    return n


def copy_name(src) -> bytes:
    """Format a 16-byte NetBIOS name field from `src`.

    Space-fill 16 bytes, copy the first `min(strlen(src), 16)` bytes of `src`
    over them, then force byte 15 to NUL — the fixed-width, space-padded,
    NUL-anchored name records NetBIOS uses.  Returns the 16-byte field.

    Recovered from `_CopyName` (SIMANTW.SYM, seg4:7438): `rep stosb` a 0x20 fill,
    `repne scasb` the length, clamp to 0x10, `rep movs` the copy, `mov es:[bx+0xF],0`.
    """
    dst = bytearray(b"\x20" * 0x10)
    n = min(cstrlen(src), 0x10)
    dst[:n] = bytes(src[:n])
    dst[0x0F] = 0
    return bytes(dst)
