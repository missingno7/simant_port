"""Recovered SimAnt rendering primitives — VM-free, byte-exact.

Reconstructed from the shipped code (names from SIMANTW.SYM), verified against
the original ASM by the A/B oracle in simant/tests/test_hooks.py.
"""
from __future__ import annotations

from typing import Callable, Iterator

#: The nest map is a fixed 64x64 grid (the ASM's two nested `dl=0x40` counters).
NEST_MAP_DIM = 64


def gen_nest_map_cells(terrain: Callable[[int, int], int],
                       alt: Callable[[int, int], int],
                       empty_lookup: Callable[[int], int],
                       mode: int, col_a: int, col_b: int, col_c: int
                       ) -> Iterator[int | None]:
    """Yield one colour byte per cell of the 64x64 nest map (or None to leave a
    cell unchanged), in the ASM's write order: column-major (outer=column,
    inner=row), so cell (col, row) is the (col*64 + row)-th value.

    Each terrain byte is classified to a colour:

      - 0xFE / 0xFF (the border sentinels)      -> `col_a`
      - high bit set (0x80..0xFD)               -> `col_b`
      - low, non-zero (0x01..0x7F)              -> `col_c`
      - 0x00 (empty): when `mode` is non-zero, the cell is LEFT as it is
        (yield None); otherwise it is filled from a secondary map —
        `empty_lookup(alt(col, row))`.

    `terrain(col, row)` / `alt(col, row)` read the two source maps; both are the
    same 64x64 shape addressed row-major (offset col + row*64).  `empty_lookup`
    is the original's `table[alt_byte >> 2]` (a 2-bit-reduced index into a byte
    palette).  All injected, so this file never touches the VM.

    Recovered from `_GenNestMap` (SIMANTW.SYM seg4:4754, _TEXT): `pusha`/`popa`
    frame (every register restored); per cell a `lodsb` from the terrain map, a
    cmp-ladder (0 / 0xFE / 0xFF / test 0x80), then a `stosb` of the chosen
    palette byte (DGROUP globals 0x1B7A/7B/7C), with the empty-cell branch doing
    `al = alt; shr al,2; stosb table[al]` (globals 0x1B78 = table base).
    """
    for col in range(NEST_MAP_DIM):
        for row in range(NEST_MAP_DIM):
            b = terrain(col, row) & 0xFF
            if b == 0x00:
                yield None if mode else empty_lookup(alt(col, row) & 0xFF) & 0xFF
            elif b in (0xFE, 0xFF):
                yield col_a & 0xFF
            elif b & 0x80:
                yield col_b & 0xFF
            else:
                yield col_c & 0xFF


def _tile_blit_geometry(dst_x: int, top: int, height: int, tile_w: int,
                        y_extent: int, map_w: int, src_tile: int):
    """The shared destination-DIB geometry of the tile-colour blits.

    Returns `(stride, row_bytes, start, src)`: the 4bpp scanline byte stride
    (padded to a 32-bit boundary), the bytes copied per row, the destination's
    starting byte offset (the `(y_extent - top - 1)`-th band of `height` rows,
    plus a `dst_x`-pixel horizontal offset), and the source tile's byte offset
    (128 bytes per tile).  All products are 16-bit (the original's registers).
    """
    M = 0xFFFF
    stride = (((((map_w * tile_w) & M) << 2) + 0x1F) & M) >> 5 << 2
    row_bytes = tile_w >> 1
    band = (((y_extent - top - 1) & M) * height) & M      # 16-bit before the stride mul
    start = band * stride + (((dst_x * tile_w) & M) >> 1)
    return stride, row_bytes, start, (src_tile << 7) & M


def xfer_tile_color(read_src: Callable[[int], int],
                    write_dst: Callable[[int, int], None],
                    dst_x: int, top: int, height: int, tile_w: int,
                    y_extent: int, map_w: int, src_tile: int) -> None:
    """Blit a `height` x `tile_w` 4bpp tile-colour block into a padded DIB.

    Each of `height` rows copies `tile_w // 2` bytes (two 4bpp pixels per byte)
    from the source tile straight into the destination, which advances one full
    scanline `stride` per row (see :func:`_tile_blit_geometry`).

    `read_src(off)` reads the source tile stream; `write_dst(linear_off, byte)`
    writes the destination — the original walks a >64K huge pointer (es += 8 per
    64K), which our contiguous selector model presents as one linear span, so
    the caller resolves `linear_off` to the right selector.

    Recovered from `_XferTileColor` (SIMANTW.SYM seg4:47DD, _TEXT).
    """
    M = 0xFFFF
    stride, row_bytes, start, src = _tile_blit_geometry(
        dst_x, top, height, tile_w, y_extent, map_w, src_tile)
    for _row in range(height):
        for i in range(row_bytes):
            write_dst(start + i, read_src((src + i) & M))
        start += stride
        src = (src + row_bytes) & M


def xfer_life_tile_color(read_src: Callable[[int], int],
                         read_dst: Callable[[int], int],
                         write_dst: Callable[[int, int], None],
                         dst_x: int, top: int, height: int, tile_w: int,
                         y_extent: int, map_w: int, src_tile: int) -> None:
    """Blit a 4bpp tile with per-pixel transparency (the "life" overlay).

    Same destination geometry as :func:`xfer_tile_color`, but each source byte
    is a two-pixel 4bpp pair blended over the destination rather than copied:

      - the whole-byte sentinel 0xDD leaves the destination byte untouched;
      - a pixel whose 4bpp index is 0xD is transparent — that nibble is kept
        from the destination and the source nibble is not drawn.

    So each opaque pixel overwrites and each 0xD pixel shows through:
    `dst = (dst & keep) | draw`, where `keep` marks the transparent nibbles.

    `read_dst(off)` reads the destination byte to blend against; the other
    callbacks are as in :func:`xfer_tile_color`.

    Recovered from `_XferLifeTileColor` (SIMANTW.SYM seg4:48FA, _TEXT).
    """
    M = 0xFFFF
    stride, row_bytes, start, src = _tile_blit_geometry(
        dst_x, top, height, tile_w, y_extent, map_w, src_tile)
    for _row in range(height):
        for i in range(row_bytes):
            sb = read_src((src + i) & M)
            if sb == 0xDD:
                continue                              # both pixels transparent -> skip
            keep, draw = 0x00, sb
            if sb & 0x0F == 0x0D:                     # low pixel transparent
                keep |= 0x0F
                draw &= 0xF0
            if draw & 0xF0 == 0xD0:                   # high pixel transparent
                keep |= 0xF0
                draw &= 0x0F
            write_dst(start + i, (read_dst(start + i) & keep) | draw)
        start += stride
        src = (src + row_bytes) & M


def windows_make_table_4x4(tiles, table):
    """Expand a row of terrain tiles into a 4-scanline pixel band.

    Each source byte is a colour index; `table[row][tile]` is the 16-bit fill
    word (four packed 4bpp pixels) that colour draws as on scanline `row`.  The
    band is four scanlines tall, each `len(tiles)` words wide, and every column
    is the same tile's word repeated down the four rows.

    Returns four rows, each a list of `len(tiles)` words.

    Recovered from `_Windows_MakeTable4x4` (SIMANTW.SYM, seg4:4674): the ASM
    loops per column doing one `lodsb` (the tile) then four `stosw`, each row's
    word read from a 4x32-word table at `SS:0x1A56` with a 0x40-byte row stride
    (`ss:[0x1A56 + row*0x40 + tile*2]`).
    """
    return [[table[row][tile] for tile in tiles] for row in range(4)]


def windows_make_table_1x1(tiles, table):
    """Pack pairs of terrain tiles into 4bpp pixel bytes, 1:1 (no zoom).

    For each consecutive pair `(t0, t1)` the output byte is
    `table[t0] | table[0x10 + t1]` — the even tile contributes the high nibble
    (its `table` entry), the odd tile the low nibble (from the +0x10 half of the
    table).  Returns `len(tiles) // 2` bytes (a trailing odd tile is dropped, as
    the ASM's `count >> 1` loop count does).

    `table` is the 256+-byte XLAT table at `SS:0x1B56`.  Recovered from
    `_Windows_MakeTable1x1` (SIMANTW.SYM, seg4:46BB): per iteration two `lodsb`
    + two `ss:xlat` (the second with BX bumped by 0x10) OR'd into one `stosb`.
    """
    return bytes(table[tiles[2 * i]] | table[0x10 + tiles[2 * i + 1]]
                 for i in range(len(tiles) // 2))
