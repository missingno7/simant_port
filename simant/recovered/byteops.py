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


def exchange(count, read1, read2, write1, write2):
    """Swap `count` bytes between two buffers, byte by byte in order.

    Each step reads BOTH current bytes before writing BOTH, so overlapping
    buffers behave exactly as the ASM's in-order loop.  The reader/writer
    closures address VM (or native) memory with the routine's 16-bit offset
    wrap, so this stays VM-free.

    Recovered from `_exchange` (SIMANTW.SYM, seg4:6E05): a `cx`-count loop of
    `lodsb` (buffer 2) + `mov ah, es:[di]` (buffer 1) + `stosb` + `mov [si-1], ah`
    — buffer 1 takes buffer 2's byte and vice versa; `pushaw`/`popaw` preserve
    every register.
    """
    for i in range(count):
        a = read2(i)          # ds:[si]  (buffer 2)
        b = read1(i)          # es:[di]  (buffer 1)
        write1(i, a)          # buffer 1 <- buffer 2's byte
        write2(i, b)          # buffer 2 <- buffer 1's byte
