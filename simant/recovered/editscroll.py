"""The edit-view scroll family — ``_EditScroll{Right,Left,Down,Up}Color``.

Recovered from ``_TEXT`` (NE segment 4, 275F) at 4C60/4D25/4E1A/4F10.  All four
are called from one dispatcher in GR_MODULE with an identical 10-word argument
list, so they share a signature (far, caller cleans 20 bytes)::

    void _EditScrollXxxColor(
        char huge      *bits,       // [bp+06] the 4bpp edit-view DIB
        unsigned char far *flags,   // [bp+0A] one byte per tile   (editW*editH)
        unsigned short far *codes,  // [bp+0E] one word per tile   (editW*editH)
        int editHeight,             // [bp+12] view height, in tiles
        int editWidth,              // [bp+14] view width,  in tiles
        int tileHeight,             // [bp+16] _tileHeight
        int tileWidth);             // [bp+18] _tileWidth

Each scroller does three things:

1. **Shift the pixels** — one flat move of the whole DIB by exactly one tile
   (a tile column for Right/Left, a tile row for Down/Up).  The original walks
   it with ``lodsw``/``stosw`` and bumps the selector by ``__AHINCR`` whenever
   an offset wraps 64K; because a huge block's selectors map to *contiguous*
   linear memory, that is precisely a flat ``memmove`` — which is what the
   ``LinearBuffer`` below performs.  The original picks its copy direction
   (``cld``/``std``) so the overlap is always safe, so memmove semantics agree
   with it byte for byte.

2. **Shift both tile arrays** by one tile (Right/Left) or one row of tiles
   (Down/Up) — ``flags`` bytewise, ``codes`` wordwise.

3. **Invalidate the newly-exposed edge** — the vacated tile column/row is
   stamped 0xFF / 0xFFFF, the "needs redraw" marker the tile renderer looks for.

The pixel DIB is **bottom-up** (the Windows default) while the tile arrays are
top-down.  That is why the vertical scrollers move pixels and tile indices in
*opposite* address directions: ScrollDown moves screen content up, which is
toward higher addresses in a bottom-up DIB but toward lower indices in the
top-down tile arrays.

The count arithmetic is reproduced exactly as the original computes it — every
intermediate is a 16-bit register, and the right-scroller's word count is
subtracted *without propagating the borrow* into the high word.  Those
truncations are part of the observed behaviour, not accidents to be tidied up.
"""
from __future__ import annotations


class LinearBuffer:
    """A flat byte view — the VM-free stand-in for a huge far pointer.

    A Win16 huge block's selectors (`base + 8*k`) map to consecutive 64K of one
    linear range, so the whole block is addressable as one contiguous span.
    """

    __slots__ = ("data", "base")

    def __init__(self, data: bytearray, base: int = 0) -> None:
        self.data = data
        self.base = base

    def move(self, dst: int, src: int, count: int) -> None:
        """memmove `count` bytes within the buffer."""
        if count <= 0:
            return
        b = self.data
        o = self.base
        b[o + dst:o + dst + count] = bytes(b[o + src:o + src + count])

    def fill(self, off: int, pattern: bytes, count: int) -> None:
        """Stamp `pattern` `count` times from `off`."""
        if count <= 0:
            return
        o = self.base + off
        self.data[o:o + len(pattern) * count] = pattern * count


def dib_stride_4bpp(width_px: int) -> int:
    """DWORD-aligned row stride, in bytes, of a 4bpp DIB — as the ASM computes it.

    ``((width*4 + 31) >> 5) << 2``, every step truncated to 16 bits.
    """
    ax = (width_px * 4) & 0xFFFF
    ax = (ax + 31) & 0xFFFF
    ax >>= 5
    return (ax << 2) & 0xFFFF


def _mul32(a: int, b: int) -> int:
    """16x16 -> 32 bit, the x86 ``mul`` the original uses (result is dx:ax)."""
    return (a & 0xFFFF) * (b & 0xFFFF)


def _block_words(cx: int, blocks: int, per_block: int) -> int:
    """Word count moved by the original's block loop.

    The first pass runs `cx` iterations of lodsw/stosw — and because x86
    ``loop`` decrements *before* it tests, ``cx == 0`` means a full 65536
    iterations, not none.  Each further block then moves 0x8000 words.  At the
    real edit-view geometry ``_EditScrollUpColor`` genuinely lands on cx == 0.
    """
    return (cx or 0x10000) + blocks * per_block


def _tile_edges(flags, codes, editWidth: int, editHeight: int,
                first_index: int, step: int, count: int) -> None:
    """Stamp the invalid marker over `count` tiles, `step` apart, from `first_index`."""
    for i in range(count):
        idx = first_index + i * step
        flags.fill(idx, b"\xff", 1)
        codes.fill(idx * 2, b"\xff\xff", 1)


# --------------------------------------------------------------------------
# horizontal


def edit_scroll_right_color(bits, flags, codes, editHeight: int, editWidth: int,
                            tileHeight: int, tileWidth: int) -> None:
    """275F:4C60 — view scrolls right, so content moves left by one tile column."""
    rows = _mul32(editHeight, tileHeight) & 0xFFFF
    stride = dib_stride_4bpp(_mul32(editWidth, tileWidth) & 0xFFFF)

    total = _mul32(stride >> 1, rows)                 # dx:ax, in WORDS
    shift_words = (tileWidth >> 2) & 0xFFFF
    # `sub cx,ax` — the borrow is NOT carried into dx.  Reproduced verbatim.
    high = total >> 16
    cx = ((total & 0xFFFF) - shift_words) & 0xFFFF
    # dx == 0 goes to a single `rep movsw` (cx == 0 there really does move
    # nothing); otherwise the block loop, whose counter is a WORD count, so
    # each of its `dx*2` extra blocks is 0x10000 words.
    words = cx if high == 0 else _block_words(cx, high, 0x10000)
    shift_bytes = (shift_words << 1) & 0xFFFF

    bits.move(0, shift_bytes, words * 2)              # cld: forward, src ahead

    tiles = (_mul32(editWidth, editHeight) - 1) & 0xFFFF
    flags.move(0, 1, tiles)
    codes.move(0, 2, tiles * 2)
    _tile_edges(flags, codes, editWidth, editHeight,
                editWidth - 1, editWidth, editHeight)   # last column


def edit_scroll_left_color(bits, flags, codes, editHeight: int, editWidth: int,
                           tileHeight: int, tileWidth: int) -> None:
    """275F:4D25 — view scrolls left, so content moves right by one tile column."""
    rows = _mul32(editHeight, tileHeight) & 0xFFFF
    stride = dib_stride_4bpp(_mul32(editWidth, tileWidth) & 0xFFFF)

    total = _mul32(stride, rows)                      # dx:ax, in BYTES
    shift_bytes = (tileWidth >> 1) & 0xFFFF
    end = (total - 2 - shift_bytes) & 0xFFFFFFFF      # dx:bx, with borrow
    high, cx = end >> 16, (end & 0xFFFF) >> 1
    words = cx if high == 0 else _block_words(cx, high, 0x8000)

    # std: walks down from the last word; dst is `shift_bytes` above src.
    dst_end = (total - 2) & 0xFFFFFFFF
    lo = dst_end - (words - 1) * 2 if words else dst_end
    bits.move(lo, lo - shift_bytes, words * 2)

    # The backward tile shift walks down from index (w*h-1), but the original
    # decrements the counter BETWEEN `add di,ax` and `mov cx,ax` -- so it moves
    # w*h-2 tiles into [2 .. w*h-1] and leaves index 1 unshifted.  Verbatim.
    tiles = (_mul32(editWidth, editHeight) - 1) & 0xFFFF
    count = (tiles - 1) & 0xFFFF
    flags.move(2, 1, count)
    codes.move(4, 2, count * 2)
    _tile_edges(flags, codes, editWidth, editHeight,
                0, editWidth, editHeight)               # first column


# --------------------------------------------------------------------------
# vertical  (bottom-up DIB: screen-up is toward HIGHER addresses)


def edit_scroll_down_color(bits, flags, codes, editHeight: int, editWidth: int,
                           tileHeight: int, tileWidth: int) -> None:
    """275F:4E1A — view scrolls down: content moves up one tile row."""
    rows = _mul32(editHeight, tileHeight) & 0xFFFF
    stride = dib_stride_4bpp(_mul32(editWidth, tileWidth) & 0xFFFF)

    shift_bytes = _mul32(stride, tileHeight) & 0xFFFF  # one tile row
    total = _mul32(stride, rows)                       # whole DIB, in bytes
    end = (total - 2 - shift_bytes) & 0xFFFFFFFF
    high, cx = end >> 16, (end & 0xFFFF) >> 1
    dst_end = (total - 2) & 0xFFFFFFFF                 # std: backward
    # Even at dx == 0 the original only uses `rep movsw` when dst and src came
    # out in the SAME 64K selector (it compares es against ds first).
    same_selector = (dst_end >> 16) == (end >> 16)
    words = cx if (high == 0 and same_selector) else _block_words(cx, high, 0x8000)

    lo = dst_end - (words - 1) * 2 if words else dst_end
    bits.move(lo, lo - shift_bytes, words * 2)

    # tile arrays are top-down: screen-up == toward lower indices
    tiles = _mul32((editHeight - 1) & 0xFFFF, editWidth) & 0xFFFF
    flags.move(0, editWidth, tiles)
    codes.move(0, editWidth * 2, tiles * 2)
    flags.fill(tiles, b"\xff", editWidth)              # last row
    codes.fill(tiles * 2, b"\xff\xff", editWidth)


def edit_scroll_up_color(bits, flags, codes, editHeight: int, editWidth: int,
                         tileHeight: int, tileWidth: int) -> None:
    """275F:4F10 — view scrolls up: content moves down one tile row."""
    rows = _mul32((editHeight - 1) & 0xFFFF, tileHeight) & 0xFFFF
    stride = dib_stride_4bpp(_mul32(editWidth, tileWidth) & 0xFFFF)

    total = _mul32(stride, rows)                       # (editHeight-1) tile rows
    shift_bytes = _mul32(stride, tileHeight) & 0xFFFF
    high, cx = total >> 16, (total & 0xFFFF) >> 1
    # At dx == 0 the original uses `rep movsw` only if advancing si by the
    # whole run would not carry out of the 16-bit offset.
    fits = shift_bytes + ((cx << 1) & 0xFFFF) <= 0xFFFF
    words = cx if (high == 0 and fits) else _block_words(cx, high, 0x8000)

    bits.move(0, shift_bytes, words * 2)               # cld: forward, src ahead

    tiles = _mul32((editHeight - 1) & 0xFFFF, editWidth) & 0xFFFF
    flags.move(editWidth, 0, tiles)
    codes.move(editWidth * 2, 0, tiles * 2)
    flags.fill(0, b"\xff", editWidth)                  # first row
    codes.fill(0, b"\xff\xff", editWidth)
