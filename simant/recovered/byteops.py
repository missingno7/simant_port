"""Recovered SimAnt byte/word swap helpers (_TEXT, seg4) — VM-free, byte-exact.

Tiny endian/word-order primitives the renderer and asset code use.  Verified
against the original ASM by the A/B oracle in ../tests/test_hooks.py.
"""
from __future__ import annotations


def flip_word(w: int) -> int:
    """Byte-swap a 16-bit word — the ASM's `xchg ah, al`.

    Recovered from `_FlipWord` (seg4:7356): loads the word arg, swaps its bytes,
    returns it in AX.
    """
    w &= 0xFFFF
    return ((w << 8) | (w >> 8)) & 0xFFFF


def flip_long(lo: int, hi: int) -> tuple[int, int]:
    """Byte-swap each half of a 32-bit long, returned as (AX, DX).

    Recovered from `_FlipLong` (seg4:7360): the long arrives as two words (low at
    [bp+6], high at [bp+8]); the routine byte-swaps each and returns AX =
    flip(high), DX = flip(low).
    """
    return flip_word(hi), flip_word(lo)
