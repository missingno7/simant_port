"""Recovered SimAnt gameplay / simulation logic — VM-free, byte-exact.

This is the *simulation core* — the part a modern native backend must preserve
exactly (unlike the rendering primitives, which a native backend would replace).
Reconstructed from the shipped code (names from SIMANTW.SYM), verified against
the original ASM by the A/B oracle in simant/tests/test_hooks.py.
"""
from __future__ import annotations

# The map/life plane layout (the DGROUP bases) is the state view's concern; this
# pure logic imports the "WHERE" from the bridge (recovered -> bridge is the
# sanctioned dependency) and owns only the "WHAT": validity + the (x<<6)+y index.
from ..bridge.dgroup_view import LIFE_PLANE_BASE, MAP_PLANE_BASE


def is_it_food(tile: int, inside_nest: bool) -> int:
    """Whether a map tile value denotes food.

    Recovered from `_IsItFood` (SIMANTW.SYM seg6:2D1A): a world-state flag
    (the word at `[0xC320]:[0x9B6E]`) selects which tile range counts as food —
    inside the nest, tiles 0x18..0x27; in the outside yard, tiles 0x48..0x4B
    (both inclusive; the original uses signed `jl`/`jg`/`jle` compares).  Returns
    1 for food, 0 otherwise.
    """
    if inside_nest:
        return 1 if 0x18 <= tile <= 0x27 else 0
    return 1 if 0x48 <= tile <= 0x4B else 0


def is_yellow_ant(caste: int) -> int:
    """Whether a caste/marker value denotes the player's "yellow ant".

    Recovered from `_IsYellowAnt` (SIMANTW.SYM seg5:5720): returns 1 when the
    value is 0xFE or 0xFF (the two yellow-ant sentinels the sim marks the
    player-controlled ant with), 0 otherwise.
    """
    return 1 if caste in (0xFE, 0xFF) else 0


def in_nest_bounds(x: int, y: int) -> int:
    """Whether (x, y) is a valid nest cell.

    Recovered from `_InNestBounds` (SIMANTW.SYM seg5:115C): the nest is a 64x64
    grid; a cell is in bounds when x is 0..0x3F and y is 1..0x3F (row 0 is
    excluded).  Signed compares (`jl`/`jg`).  Returns 1 in bounds, 0 otherwise.
    """
    return 1 if (0 <= x <= 0x3F and 1 <= y <= 0x3F) else 0


def is_it_dirt(tile: int) -> int:
    """Whether a map tile value is diggable dirt.

    Recovered from `_IsItDirt` (SIMANTW.SYM seg5:1182): dirt tiles are 0x20..0x2E
    inclusive (signed compares).  Returns 1 for dirt, 0 otherwise.  Companion of
    `is_it_food`.
    """
    return 1 if 0x20 <= tile <= 0x2E else 0


def r_is_it_dirt(tile: int) -> int:
    """Whether a tile is dirt for the red-colony digging code.

    Recovered from `_RIsItDirt` (SIMANTW.SYM seg5:26C4): a wider range than the
    black-colony `is_it_dirt` — dirt is 0x20..0x2F, OR anything >= 0x4F (signed
    compares).  The gap 0x30..0x4E is not dirt.  Returns 1 for dirt, 0 otherwise.
    """
    if tile < 0x20:
        return 0
    if tile <= 0x2F:
        return 1
    if tile < 0x4F:
        return 0
    return 1


def is_it_nfood(tile: int) -> int:
    """Whether a tile value is in-nest food.

    Recovered from `_IsItNFood` (SIMANTW.SYM seg5:5F64): nest food occupies the
    tile range 0x10..0x13 inclusive (signed compares).  Returns 1 / 0.  This is
    the in-nest food band that `is_this_food` also checks on the nest plane.
    """
    return 1 if 0x10 <= tile <= 0x13 else 0


def is_this_egg(marker: int) -> int:
    """Whether a life-grid marker denotes an egg/brood item.

    Recovered from `_IsThisEgg` (SIMANTW.SYM seg5:5EC8): the low byte is masked
    to 7 bits (the high bit 0x80 is a separate flag the sim carries), and a
    masked value of 1..7 is an egg/brood stage.  Returns 1 / 0.
    """
    return 1 if 1 <= (marker & 0x7F) <= 7 else 0


def is_this_grass(plane: int, tile: int) -> int:
    """Whether (plane, tile) denotes grass in the outside yard.

    Recovered from `_IsThisGrass` (SIMANTW.SYM seg5:5EE4): grass exists only on
    the yard planes (plane >= 2) and covers tiles 0x1C..0x1F (signed compares).
    Returns 1 / 0.
    """
    if plane < 2:
        return 0
    return 1 if 0x1C <= tile <= 0x1F else 0


def is_this_pebble(plane: int, tile: int) -> int:
    """Whether (plane, tile) denotes a pebble.

    Recovered from `_IsThisPebble` (SIMANTW.SYM seg5:5F32): on the yard planes
    (plane > 1) a pebble is tile 0x30..0x31; on the nest plane (plane == 1) it is
    tile 0x51..0x53.  Any other plane (<= 0) is never a pebble.  Signed compares.
    Returns 1 / 0.
    """
    if plane > 1:
        return 1 if 0x30 <= tile <= 0x31 else 0
    if plane == 1:
        return 1 if 0x51 <= tile <= 0x53 else 0
    return 0


def is_this_food(plane: int, tile: int, inside: bool) -> int:
    """Whether (plane, tile) denotes food.

    Recovered from `_IsThisFood` (SIMANTW.SYM seg5:5F04): on the nest planes
    (plane <= 1) it defers to `is_it_food` (which the world-state inside flag
    drives); on the yard planes (plane > 1) food is the nest-food band
    0x10..0x13 (signed compares).  Returns 1 / 0.
    """
    if plane <= 1:
        return is_it_food(tile, inside)
    return 1 if 0x10 <= tile <= 0x13 else 0


def is_valid_a(x: int, y: int) -> int:
    """Whether (x, y) is a valid cell on the wide (yard/overworld) grid.

    Recovered from `_IsValidA` (SIMANTW.SYM seg5:9C02): x must be 0..0x7F and y
    must be 0..0x3F (a 128-wide by 64-tall grid; signed compares).  Returns 1/0.
    """
    if not 0 <= x <= 0x7F:
        return 0
    return 1 if 0 <= y <= 0x3F else 0


def is_valid_b(x: int, y: int) -> int:
    """Whether (x, y) is a valid cell on the 64x64 nest grid.

    Recovered from `_IsValidB` (SIMANTW.SYM seg5:9C26): both x and y must be
    0..0x3F (signed compares).  Returns 1/0.  The nest-grid companion of
    `is_valid_a`.
    """
    if not 0 <= x <= 0x3F:
        return 0
    return 1 if 0 <= y <= 0x3F else 0


def is_less_than_hole(tile: int, inside: bool) -> int:
    """Whether a tile value sits below the hole-tile range.

    Recovered from `_IsLessThanHole` (SIMANTW.SYM seg5:9784): the world-state
    inside/outside flag (the same flag `is_it_food` reads, so inside == flag set)
    picks the hole threshold — inside the nest a tile is "less than hole" when it
    is < 0x59; in the outside yard when it is < 0x50 (signed compare).  Returns
    1 / 0.
    """
    threshold = 0x59 if inside else 0x50
    return 1 if tile < threshold else 0


def is_same_plane(plane: int, current_plane: int) -> int:
    """Whether `plane` selects the currently active map plane.

    Recovered from `_IsSamePlane` (SIMANTW.SYM seg5:97AA): a `plane` argument of
    0 is treated as plane 1 (the default), then compared for equality against the
    world-state current plane.  Returns 1 when they match, 0 otherwise.
    """
    p = 1 if plane == 0 else plane
    return 1 if current_plane == p else 0


def _cell_offset(plane: int, x: int, y: int, bases: dict) -> int | None:
    """Shared addressing for the plane arrays: coordinate validity is exactly
    `is_valid_a` on the yard planes (plane <= 1: x 0..0x7F, y 0..0x3F) and
    `is_valid_b` on the nest planes (plane > 1: x,y 0..0x3F); planes 0-3 select a
    base, every other plane (including negative) is out of range."""
    if plane <= 1:
        if not (0 <= x <= 0x7F and 0 <= y <= 0x3F):
            return None
    elif not (0 <= x <= 0x3F and 0 <= y <= 0x3F):
        return None
    base = bases.get(plane) if plane >= 0 else None
    if base is None:
        return None
    return base + (x << 6) + y


def map_cell_offset(plane: int, x: int, y: int) -> int | None:
    """DGROUP byte offset of map cell (plane, x, y), or None if out of range.

    Recovered from `_GetMap` (SIMANTW.SYM seg5:60E2): the yard array is at 0x28E8,
    the nest planes at 0x48E8 / 0x58E8.  The caller reads the byte at DS:offset;
    the ASM returns 0xFFFF for the None case.
    """
    return _cell_offset(plane, x, y, MAP_PLANE_BASE)


def set_map(view, plane: int, x: int, y: int, value: int) -> int | None:
    """Write map cell (plane, x, y) = value (low byte) into the DGROUP map planes.

    Recovered from `_SetMap` (SIMANTW.SYM seg5:617A) — the write-side twin of
    `map_cell_offset`/`get_map`.  The write lands only when the cell is in range
    (`map_cell_offset` is not None); an out-of-range (plane, x, y) is a no-op.
    The original then redraws the tile (a rendering side effect, not sim state);
    that is the caller's concern.  `view` is a DGROUP byte view (a bridge backend
    with `wb`).  Returns the written offset, or None if out of range.
    """
    off = map_cell_offset(plane, x, y)
    if off is not None:
        view.wb(off, value & 0xFF)
    return off


def life_cell_offset(plane: int, x: int, y: int) -> int | None:
    """DGROUP byte offset of life-grid cell (plane, x, y), or None if out of range.

    Recovered from `_GetLife` (SIMANTW.SYM seg5:6040): same validity and plane
    layout as `map_cell_offset` but over the life-grid arrays — yard at 0x68E8,
    nest planes at 0x88E8 / 0x98E8.
    """
    return _cell_offset(plane, x, y, LIFE_PLANE_BASE)


def get_life_value(byte: int) -> int:
    """`_GetLife`'s post-read rule: an empty life cell (byte 0) reads as 0xFFFF;
    any other byte is the life value itself.  (An out-of-range cell also reads as
    0xFFFF — handled by `life_cell_offset` returning None.)"""
    return 0xFFFF if byte == 0 else byte


def is_it_hole(tile: int, inside: bool) -> int:
    """Whether a yard-plane map tile is a nest hole / entrance.

    Recovered from `_IsItHole` (SIMANTW.SYM seg6:2CC0): the caller first bounds-
    checks (x, y) with `is_valid_a` and reads the tile from the yard map (plane 0,
    at `map_cell_offset(0, x, y)`); an out-of-bounds cell is never a hole.  The
    world-state inside/outside flag then picks the hole encoding — inside the nest
    a hole is 0x80..0x8F, in the outside yard it is exactly 0x50.  Returns 1 / 0.
    """
    if inside:
        return 1 if 0x80 <= tile <= 0x8F else 0
    return 1 if tile == 0x50 else 0


def is_not_barrier(tile: int, inside: bool) -> int:
    """Whether a tile is passable (not a barrier) for ant movement.

    Recovered from `_IsNotBarrier` (SIMANTW.SYM seg5:94A0): reads the same
    inside/outside world flag `is_less_than_hole` does (selector [0xC4AC]), and a
    tile is "not a barrier" when it is <= 0x5F inside the nest, <= 0x50 in the
    outside yard (signed compares).  Returns 1 / 0.
    """
    threshold = 0x5F if inside else 0x50
    return 1 if tile <= threshold else 0


def is_not_obstacle(plane: int, tile: int, inside: bool) -> int:
    """Whether a map cell is clear for an ant to move onto (not an obstacle).

    Recovered from `_IsNotObstacle` (SIMANTW.SYM seg5:94C6): after the `_GetMap`
    bounds check the tile is read from the plane arrays and classified.  On the
    nest planes (plane <= 1) it is clear when tile <= 0x5F inside / <= 0x53 in the
    outside yard (world inside/outside flag).  On the yard planes (plane > 1) it
    is clear when tile <= 0x18 OR it is a pebble (0x30..0x31).  An out-of-range
    cell is an obstacle.  Returns 1 (not an obstacle) / 0.
    """
    if plane <= 1:
        threshold = 0x5F if inside else 0x53
        return 1 if tile <= threshold else 0
    return 1 if (tile <= 0x18 or 0x30 <= tile <= 0x31) else 0


def is_clear_tile(plane: int, map_tile: int, life_value: int) -> int:
    """Whether a cell is clear for an ant to step onto.

    Recovered from `_IsClearTile` (SIMANTW.SYM seg5:5B2C): reads both the map
    tile and the life-grid cell.  The cell is blocked if a non-yellow ant sits on
    it — life not in {0 (empty), 0xFE, 0xFF (the yellow-ant sentinels)}.
    Otherwise it is clear iff the map tile is below 0x10 on the nest planes
    (plane <= 1) / below 8 on the yard planes.  Returns 1 (clear) / 0.
    """
    if life_value not in (0, 0xFE, 0xFF):
        return 0
    threshold = 0x10 if plane <= 1 else 8
    return 1 if map_tile < threshold else 0


def is_valid_location(plane: int, x: int, y: int) -> int:
    """Whether (plane, x, y) is a valid cell — the plane-aware coordinate check.

    Recovered from `_IsValidLocation` (SIMANTW.SYM seg5:56DA): the yard planes
    (plane <= 1) use the wide bounds `is_valid_a` (x 0..0x7F, y 0..0x3F); the
    nest planes (plane > 1) use `is_valid_b` (x, y 0..0x3F).  Returns 1 / 0.
    """
    return is_valid_a(x, y) if plane <= 1 else is_valid_b(x, y)


def is_it_digable(plane: int, tile: int) -> int:
    """Whether a yard-plane map tile can be dug.

    Recovered from `_IsItDigable` (SIMANTW.SYM seg5:95C6): only the yard planes
    (plane >= 2) are diggable, and a tile is diggable when it is dirt
    (`is_it_dirt`) or grass (0x1C..0x1F).  Returns 1 / 0.
    """
    if plane < 2:
        return 0
    if is_it_dirt(tile):
        return 1
    return 1 if 0x1C <= tile <= 0x1F else 0


def is_it_a_hole(plane: int, x: int, y: int, tile: int, inside: bool) -> int:
    """Whether cell (plane, x, y) is a hole, on any plane.

    Recovered from `_IsItAHole` (SIMANTW.SYM seg5:9B4A): on the nest planes
    (plane <= 1) it defers to `is_it_hole` (plane-0 yard map + the inside flag);
    on the yard planes (plane > 1) a hole is the top row (y <= 0, i.e. y == 0 for
    a valid cell) whose plane tile is exactly 0x18.  `tile` is the relevant map
    read (plane-0 for the nest planes, the plane's own array for the yard).
    Returns 1 / 0.
    """
    if plane <= 1:
        return is_it_hole(tile, inside)
    if y > 0:
        return 0
    return 1 if tile == 0x18 else 0


# The 8-way step offsets _GetBestDir scans (the DGROUP direction tables at
# [0xC364]/[0xC366]) — the same compass order as CLEAR_3X3_DX/DY.
GET_BEST_DIR_DX = (0, 1, 1, 1, 0, -1, -1, -1)
GET_BEST_DIR_DY = (-1, -1, 0, 1, 1, 1, 0, -1)


def get_best_dir(plane, cur_x, cur_y, tgt_x, tgt_y, read_map, read_life, inside):
    """The best 8-way step from (cur_x, cur_y) toward (tgt_x, tgt_y) — ant
    pathfinding.

    Recovered from `_GetBestDir` (SIMANTW.SYM seg6:405E), the routine that
    composes the recovered movement predicates.  It scans the eight neighbours
    and keeps the one that most reduces the squared distance to the target
    (`get_dis`) while being passable (`is_not_obstacle`), not a pebble
    (`is_this_pebble`), and strictly closer than any kept so far; it prefers a
    genuinely clear cell (`is_clear_tile`) but falls back to an occupied/blocked
    one if that is the only improvement.

    `read_map(plane, x, y)` returns the map tile (0..0xFF) or None out of range;
    `read_life(plane, x, y)` returns the life byte (>= 0) or None; `inside` is the
    world inside/outside flag.  Returns the chosen direction 0..7, -1 when already
    at the target (distance 0), or -2 (0xFFFE) when no neighbour improves.

    NOTE: this is a behaviour routine reconstructed as source and verified against
    the original ASM's RETURN VALUE (its callers read only that); its full
    register residue is not modelled as a lifted island.
    """
    best_dist = get_dis(cur_x, cur_y, tgt_x, tgt_y)
    if best_dist <= 0:
        return -1
    best_clear, best_any = -1, -2
    for si in range(8):
        nx = cur_x + GET_BEST_DIR_DX[si]
        ny = cur_y + GET_BEST_DIR_DY[si]
        tile = read_map(plane, nx, ny)
        if tile is None or not is_not_obstacle(plane, tile, inside):
            continue
        if is_this_pebble(plane, tile):
            continue
        dist = get_dis(nx, ny, tgt_x, tgt_y)
        if dist >= best_dist:
            continue
        best_dist = dist
        life = read_life(plane, nx, ny)
        if life is not None and life > 0:               # occupied -> fallback
            best_any = si
        elif is_clear_tile(plane, tile, life if life is not None else 0):
            best_clear = si
        else:
            best_any = si
    return best_clear if best_clear >= 0 else best_any


# The 3x3 neighbour offsets _IsClear3x3 walks (a DGROUP direction table): the 8
# compass directions around the centre, in the order N, NE, E, SE, S, SW, W, NW.
CLEAR_3X3_DX = (0, 1, 1, 1, 0, -1, -1, -1)
CLEAR_3X3_DY = (-1, -1, 0, 1, 1, 1, 0, -1)


def is_clear_3x3(cells_clear) -> int:
    """Whether a whole 3x3 block is clear for movement.

    Recovered from `_IsClear3x3` (SIMANTW.SYM seg5:5AD2): the centre cell and its
    eight neighbours (offsets `CLEAR_3X3_DX`/`DY`) must all be clear per
    `is_clear_tile`.  `cells_clear` is the iterable of the nine per-cell results
    (centre first), so this is 1 iff every one is clear.
    """
    return 1 if all(cells_clear) else 0


def get_dir(x1: int, y1: int, x2: int, y2: int) -> int:
    """Compass direction (0..8) from point 1 to point 2.

    Recovered from `_GetDir` (SIMANTW.SYM seg5:10CC): classifies the step by the
    sign of dx = x2 - x1 and dy = y2 - y1 (signed).  0 = same cell; then, by
    (sign dx, sign dy): (0,-)=1 (0,+)=5 (+,-)=2 (+,0)=3 (+,+)=4 (-,+)=6 (-,0)=7
    (-,-)=8.  A pure leaf used all over the ant movement code.
    """
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0:
        if dy == 0:
            return 0
        return 1 if dy < 0 else 5
    if dx > 0:
        if dy < 0:
            return 2
        return 3 if dy == 0 else 4
    if dy > 0:
        return 6
    return 7 if dy == 0 else 8


def get_dis(x1: int, y1: int, x2: int, y2: int) -> int:
    """Squared Euclidean distance between point 1 and point 2.

    Recovered from `_GetDis` (SIMANTW.SYM seg5:1122): returns dx*dx + dy*dy where
    dx = x2 - x1 and dy = y2 - y1 (the original squares each 16-bit delta via the
    C runtime's 32-bit multiply and returns the sum as a long).  The sim uses the
    squared distance directly — it never takes the root.
    """
    dx = x2 - x1
    dy = y2 - y1
    return dx * dx + dy * dy


def s_get_dis(x1: int, y1: int, x2: int, y2: int) -> int:
    """Manhattan (taxicab) distance between point 1 and point 2.

    Recovered from `_SGetDis` (SIMANTW.SYM seg5:56BA): |x2 - x1| + |y2 - y1| — the
    cheap distance metric the spider AI uses instead of the squared-Euclidean
    `get_dis` (the S prefix marks the spider routines).
    """
    return abs(x2 - x1) + abs(y2 - y1)
