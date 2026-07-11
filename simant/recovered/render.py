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


def _tile_blit_geometry_mono(dst_x: int, top: int, height: int, tile_w: int,
                             y_extent: int, map_w: int, src_tile: int):
    """The 1bpp (monochrome) counterpart of `_tile_blit_geometry`.

    Same padded-scanline destination geometry, but one BIT per pixel: the
    scanline stride packs `map_w * tile_w` bits (not 4bpp) into 32-bit-aligned
    bytes, byte offsets are pixel counts `>> 3`, and each source tile is 32
    bytes.  `start` is the block's LAST scanline (`(y_extent - top) * height -
    1`) because the mono blit walks the band bottom-up (see `xfer_tile_mono`).
    All products are 16-bit, matching the original's registers.
    """
    M = 0xFFFF
    stride = ((((map_w * tile_w) & M) + 0x1F) & M) >> 5 << 2
    row_bytes = tile_w >> 3
    band_last = ((((y_extent - top) & M) * height) & M) - 1 & M   # last scanline index
    start = band_last * stride + (((dst_x * tile_w) & M) >> 3)
    return stride, row_bytes, start, (src_tile << 5) & M


def xfer_tile_mono(read_src: Callable[[int], int],
                   write_dst: Callable[[int, int], None],
                   dst_x: int, top: int, height: int, tile_w: int,
                   y_extent: int, map_w: int, src_tile: int) -> None:
    """Blit a `height` x `tile_w` 1bpp (monochrome) tile block into a padded DIB.

    The monochrome sibling of :func:`xfer_tile_color`.  Each of `height` rows
    copies `tile_w // 8` bytes (eight 1bpp pixels per byte) from the source tile
    into the destination; the destination walks the band BOTTOM-UP, one scanline
    `stride` UP per row (the original decrements its huge pointer), so source row
    j lands in band row `height-1-j`.  `read_src`/`write_dst` are as in
    :func:`xfer_tile_color` (write offsets are resolved against the huge pointer
    by the caller).

    Recovered from `_XferTileMono` (SIMANTW.SYM seg4:486C, _TEXT).
    """
    M = 0xFFFF
    stride, row_bytes, start, src = _tile_blit_geometry_mono(
        dst_x, top, height, tile_w, y_extent, map_w, src_tile)
    for _row in range(height):
        for i in range(row_bytes):
            write_dst(start + i, read_src((src + i) & M))
        start -= stride                       # bottom-up (the huge ptr decrements)
        src = (src + row_bytes) & M


def xfer_life_tile_mono(read_src: Callable[[int], int],
                        read_dst: Callable[[int], int],
                        write_dst: Callable[[int, int], None],
                        dst_x: int, top: int, height: int, tile_w: int,
                        y_extent: int, map_w: int, src_tile: int) -> None:
    """Blit a 1bpp tile with per-pixel transparency (the monochrome "life"
    overlay) — the mono sibling of :func:`xfer_life_tile_color`.

    Same bottom-up mono geometry as :func:`xfer_tile_mono`, but the source has
    TWO planes: the data byte at offset `off`, and a transparency-mask byte at
    `off + mask_delta`, where `mask_delta = 0x3000 - (src_tile & 0xFF80) * 32`
    keeps the mask plane at a fixed source offset while the data tile roams.  A
    mask bit of 1 keeps the destination (transparent); 0 draws the source::

        new = (dst & mask) | (data & ~mask)

    `read_dst(off)` reads the destination byte to blend against.

    Recovered from `_XferLifeTileMono` (SIMANTW.SYM seg4:49B7, _TEXT).
    """
    M = 0xFFFF
    stride, row_bytes, start, src = _tile_blit_geometry_mono(
        dst_x, top, height, tile_w, y_extent, map_w, src_tile)
    mask_delta = (0x3000 - (((src_tile & 0xFF80) << 5) & M)) & M
    for _row in range(height):
        for i in range(row_bytes):
            off = (src + i) & M
            data = read_src(off)
            mask = read_src((off + mask_delta) & M)   # 1 = transparent (keep dest)
            write_dst(start + i,
                      (read_dst(start + i) & mask) | (data & (mask ^ 0xFF)))
        start -= stride                       # bottom-up (the huge ptr decrements)
        src = (src + row_bytes) & M


#: Per-view-mode tile-map layout for `do_calc_tile`, keyed by the mode selector
#: (DGROUP:0xCC76).  Modes 0 and 1 share layout 0.  Fields: the tile-x mask, the
#: graphic map's DGROUP offset and additive bias, the attribute map's offset, and
#: the three attribute base constants (for the 0xFF / 0xFE / other cases).
_TILE_MODES = {
    #     x_mask  gfx_map  gfx_bias  attr_map  ff_base  fe_base  else_base
    0:   (0x7F,   0x28E8,  0x00,     0x68E8,   0x380,   0x388,   0x100),
    2:   (0x3F,   0x48E8,  0x90,     0x88E8,   0x300,   0x308,   0x200),
    3:   (0x3F,   0x58E8,  0x90,     0x98E8,   0x300,   0x308,   0x200),
}


def _tile_attr(cell, attr_map, ff_base, fe_base, else_base, read_byte, read_word):
    """The tile ATTRIBUTE (CE7A) shared by every _DoCalcTile mode.

    Reads the attribute map at `cell`; 0 leaves it clear, 0xFF/0xFE are animated
    specials assembled from DGROUP globals (a base + a season word CF54 + a phase
    word CC84, plus CF50 or CE92 depending on whether CC84 has reached 8), and any
    other value is a plain `attr + else_base`.
    """
    attr = read_byte((cell + attr_map) & 0xFFFF)
    if attr == 0x00:
        return 0
    if attr == 0xFF:
        phase = read_word(0xCC84)
        v = ff_base + read_word(0xCF54) + phase
        v += read_word(0xCF50) if phase >= 8 else read_word(0xCE92)
        return v & 0xFFFF
    if attr == 0xFE:
        return (fe_base + read_word(0xCC84) + read_word(0xCF54)
                + read_word(0xCF50)) & 0xFFFF
    return (attr + else_base) & 0xFFFF


def do_calc_tile(mode, tile_x, tile_y, sub_mode,
                 read_byte, read_word, read_layer):
    """Resolve a map cell to its graphic index (CE96) and attribute (CE7A).

    `mode` (DGROUP:0xCC76) selects the view: 0/1 the main map, 2 and 3 two
    alternate map pairs; mode >= 4 draws nothing (both outputs 0).  Returns
    `(ce96, ce7a)`.

    The graphic comes from `gfx_map[cell] + gfx_bias`, where `cell = (tile_x &
    x_mask) << 6 + tile_y`.  In modes 0/1 five half-resolution overlay layers
    (DGROUP far-pointer table at 0xACAE, selected by `sub_mode` = 0xAC58) are
    consulted first: a layer texel above 0x10 overrides the graphic with
    `((texel >> 4) & 0x1F) + 0xF0`.  The attribute is then `_tile_attr(...)`.

    `read_byte`/`read_word` read DGROUP; `read_layer(sub, index)` reads overlay
    layer `sub` at half-res `index`.  All injected, so this file never touches
    the VM.

    Recovered from `_DoCalcTile` (SIMANTW.SYM seg4:4A6B, _TEXT).
    """
    if mode >= 4:
        return 0, 0
    x_mask, gfx_map, gfx_bias, attr_map, ff_base, fe_base, else_base = \
        _TILE_MODES[0 if mode <= 1 else mode]
    x = tile_x & x_mask
    cell = ((x << 6) + tile_y) & 0xFFFF

    ce96 = None
    if mode <= 1 and sub_mode <= 4:
        texel = read_layer(sub_mode, (((x >> 1) << 5) + (tile_y >> 1)) & 0xFFFF)
        if texel > 0x10:
            ce96 = (((texel >> 4) & 0x1F) + 0xF0) & 0xFF
    if ce96 is None:
        ce96 = (read_byte((cell + gfx_map) & 0xFFFF) + gfx_bias) & 0xFF

    ce7a = _tile_attr(cell, attr_map, ff_base, fe_base, else_base,
                      read_byte, read_word)
    return ce96, ce7a


#: Top-`n`-bits masks for a glyph's partial edge column, indexed by `width & 7`
#: (the ASM's xlatb table at seg7:0xB02A).
GLYPH_PARTIAL_MASK = (0x00, 0x80, 0xC0, 0xE0, 0xF0, 0xF8, 0xFC, 0xFE, 0xFF)


def shift_glyph_word(word: int, x_sub: int, y_sub: int, hi_mask: int = 0xFF) -> int:
    """Position a source glyph word at a sub-byte bit offset for OR-compositing.

    The word is read big-endian (the ASM's `xchg al,ah`), shifted left by
    `x_sub`, reduced to its high byte (also masked to `hi_mask` for a partial
    edge column), shifted right by `y_sub`, and byte-swapped back to
    little-endian — the exact `shl / xor al,al / and / shr` dance of _DrawChar.
    """
    w = ((word & 0xFF) << 8) | (word >> 8)
    w = ((w << x_sub) & 0xFF00) & ((hi_mask << 8) & 0xFF00)
    w >>= y_sub
    return ((w & 0xFF) << 8) | (w >> 8)


def draw_char(read_src: Callable[[int, int], int],
              read_dst: Callable[[int, int], int],
              write_dst: Callable[[int, int, int], None],
              width: int, height: int, x_sub: int, y_sub: int,
              partial_mask: int) -> None:
    """OR-composite a `width`-bit x `height`-row 1bpp glyph, sub-byte aligned.

    Each row draws `width // 8` full words plus, when `width` is not a byte
    multiple, one partial edge column masked to its top `width & 7` bits.
    Positions advance one BYTE per step (overlapping words), so a sub-byte
    x-shift smears each source word across the destination byte boundary;
    compositing is OR (a 1bpp glyph over whatever is already there).

    `read_src(row, col)` / `read_dst(row, col)` read the source / destination
    word at row `row`, byte column `col`; `write_dst(row, col, value)` stores it.

    Recovered from `_DrawChar` (SIMANTW.SYM seg7:B033, SIMTWO_MODULE).
    """
    full_words = width >> 3
    partial_bits = width & 7
    for row in range(height):
        for col in range(full_words):
            w = shift_glyph_word(read_src(row, col), x_sub, y_sub)
            write_dst(row, col, read_dst(row, col) | w)
        if partial_bits:
            w = shift_glyph_word(read_src(row, full_words), x_sub, y_sub,
                                 partial_mask)
            write_dst(row, full_words, read_dst(row, full_words) | w)


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


#: Tile-pair counts of the two zoomed-mono MakeTable halves (the ASM's `mov cx`):
#: the "a" half does 0x40 pairs, the "b" half 0x20 — the only difference between
#: `_WindowsMono_MakeTable4x4a` (442C) and `_WindowsMono_MakeTable4x4b` (44B9),
#: which also sets the output scanline stride equal to the pair count.
MONO_MAKETABLE_PAIRS = 0x40


def windows_mono_make_table_4x4(tiles, table, pairs=MONO_MAKETABLE_PAIRS):
    """Build the first four scanlines of a zoomed monochrome tile band.

    Each output byte packs TWO tiles — the even tile in the high nibble, the odd
    tile in the low nibble — so for each of `pairs` tile pairs `(t0, t1)` and each
    scanline `r` in 0..3::

        out[r][j] = (table[t0][r] & 0xF0) | (table[t1][r] & 0x0F)

    `table[tile]` is the tile's per-scanline pattern row (8 bytes; these two
    halves use scanlines 0..3).  Returns four rows of `pairs` bytes.

    Recovered from `_WindowsMono_MakeTable4x4a`/`b` (SIMANTW.SYM, seg4:442C/44B9):
    identical two-`lodsb` loops (0x40 / 0x20 iterations), four `ss:[bx+r]` reads
    each masked to a nibble and stored/OR'd at the destination's `pairs`-strided
    scanlines.
    """
    rows = [bytearray(pairs) for _ in range(4)]
    for j in range(pairs):
        t0, t1 = tiles[2 * j], tiles[2 * j + 1]
        for r in range(4):
            rows[r][j] = (table[t0][r] & 0xF0) | (table[t1][r] & 0x0F)
    return [bytes(r) for r in rows]


def gen_over_map(cx0, dx0, tbl1, tbl2, mode, read_byte):
    """Composite two source layers into an overlay map (64 rows x 128 columns).

    For each of the 64x128 cells (destination index advancing 0..8191):

    * read the primary layer byte `a = src[cx]` (the source cursor `cx` steps by
      0x40 per column and by +1 per row — a column-major read into a row-major
      write);
    * if `a != 0` the cell is `table1[a >> 3]`;
    * else if `mode == 0` it is `table2[src2[dx]]` (the secondary layer, same
      cursor pattern via `dx`);
    * else the cell is SKIPPED (its destination byte is left unchanged).

    `read_byte(off)` reads DS:[off] (all sources + both tables live in DGROUP).
    Returns `{dst_index: value}` — only the cells actually written (skips absent),
    so a caller applies them over the existing destination.

    Recovered from `_GenOverMap` (SIMANTW.SYM, seg4:46E9): a 0x40 x 0x80 nested
    `lodsb`/`stosb` loop; `pushaw`/`popaw` preserve every register, so the only
    effect is this write set (plus echoing tbl1/tbl2 to scratch globals).
    """
    writes = {}
    di = 0
    cx, dx = cx0, dx0
    for _row in range(0x40):
        for _col in range(0x80):
            a = read_byte(cx & 0xFFFF)
            if a != 0:
                writes[di] = read_byte((tbl1 + (a >> 3)) & 0xFFFF)
            elif mode == 0:
                writes[di] = read_byte((tbl2 + read_byte(dx & 0xFFFF)) & 0xFFFF)
            di += 1
            cx = (cx + 0x40) & 0xFFFF
            dx = (dx + 0x40) & 0xFFFF
        cx = (cx - 0x1FFF) & 0xFFFF
        dx = (dx - 0x1FFF) & 0xFFFF
    return writes


#: The four 2-bit destination slots of the half-resolution mono packer, one per
#: tile of the group of four (`and 0xC0 / 0x30 / 0x0C / 0x03`).
_MONO_2X2_MASKS = (0xC0, 0x30, 0x0C, 0x03)


def windows_mono_make_table_2x2(tiles, table, count):
    """Build the two scanlines of a half-resolution monochrome tile band.

    Each output byte packs FOUR tiles at 2 bits each — tile k of the group lands
    in slot `_MONO_2X2_MASKS[k]` — so for each of `count` groups `(t0..t3)` and
    each scanline `r` in 0..1::

        out[r][j] = sum(table[t_k][r] & _MONO_2X2_MASKS[k] for k in 0..3)

    `table[tile]` is the tile's per-scanline pattern row (this variant uses
    scanlines 0..1).  Returns two rows of `count` bytes.

    Recovered from `_WindowsMono_MakeTable2x2a`/`b` (SIMANTW.SYM, seg4:4542/45DB):
    identical four-`lodsb` loops (0x20 / 0x10 iterations), two `ss:[bx+r]` reads
    per tile each masked to a 2-bit slot and stored/OR'd at the destination's
    `count`-strided scanlines.
    """
    rows = [bytearray(count) for _ in range(2)]
    for j in range(count):
        ts = tiles[4 * j:4 * j + 4]
        for r in range(2):
            b = 0
            for k in range(4):
                b |= table[ts[k]][r] & _MONO_2X2_MASKS[k]
            rows[r][j] = b
    return [bytes(r) for r in rows]


def copy_char_rep(src, x, y, stride, rep):
    """Blit a 16-row glyph into a DIB, replicating each source byte `rep` times
    horizontally per row.  Returns {dst_off: byte} (relative to the DIB
    far-pointer offset, with 16-bit offset wrap).

    Recovered from `_CopyCharRep` (seg4:6CAA).  The DIB byte stride is the header
    width word >> 3; row 0 lands at `4 + x + ((y*2)&0xFF)*(stride&0xFF)` past the
    DIB offset (the +4 skips the two-word inline header the routine reads the
    width from, and the y term is an 8-bit `mul dl`), each next row steps by the
    full 16-bit `stride`.  Per row the routine `lodsb`s one source byte and
    `rep stosb`s it `rep` times.
    """
    di = (4 + x + (((y * 2) & 0xFF) * (stride & 0xFF))) & 0xFFFF
    out = {}
    for row in range(16):
        b = src[row] & 0xFF
        for k in range(rep):
            out[(di + k) & 0xFFFF] = b
        di = (di + stride) & 0xFFFF
    return out


def copy_char(src, x, y, stride):
    """Blit a 16-row, 1-byte-wide glyph column into a DIB (one source byte per
    row).  Recovered from `_CopyChar` (seg4:6C62) — the `rep`==1 specialisation
    of `copy_char_rep` (`movsb` per row instead of `rep stosb`)."""
    return copy_char_rep(src, x, y, stride, 1)
