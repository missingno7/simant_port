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


def _sx16(v: int) -> int:
    """Sign-extend a 16-bit word to a Python int, matching the ASM's signed
    (`jl`/`jg`/`sar`) reads of a word field."""
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


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


def set_my_health(dgroup, simant_data_group, pack, new_health: int) -> int:
    """Set the player ant's health, with the game's clamp + status flags.

    Recovered from `_SetMyHealth` (SIMANTW.SYM seg5:8C70).  The routine spans
    THREE fixed NE data segments — DGROUP holds the health field directly, while
    the god-mode flag and the other status fields are reached through DGROUP
    globals that hold load-time-fixed pointers to SIMANT_DATA_GROUP / PACK (see
    `hooks.SIMANT_DATA_GROUP_SEG_INDEX` / `PACK_SEG_INDEX` — confirmed constant:
    no game code ever reassigns those pointer globals).  So this takes one word
    view per segment rather than a single flat view.

    In "god mode" (`simant_data_group[0x8A5E]` set) health is forced to 100.  A
    positive health clears the dead flag (`pack[0x9CF0]`); the value is then
    clamped to 0..100 and stored at `dgroup[0xAC8A]`.  A status flag
    (`pack[0x9AF2]`) records the change: 0 when actually healing (old health
    `pack[0x9BEC]` < new and new >= 10), 1 otherwise (damage, no change, or a
    near-death heal).  Returns the stored (clamped) health.
    """
    h = 0x64 if simant_data_group.rw(0x8A5E) != 0 else new_health
    if h > 0:
        pack.ww(0x9CF0, 0)                  # alive -> clear the "dead" flag
    h = 0 if h < 0 else (0x64 if h > 0x64 else h)       # clamp to 0..100
    dgroup.ww(0xAC8A, h)                     # store the health value
    pack.ww(0x9AF2, 0 if (pack.rw(0x9BEC) < h and h >= 0x0A) else 1)
    return h


def drop_water(view, x: int) -> None:
    """Flow / evaporate the water column at Y=x across the two nest planes.

    Recovered from `_DropWater` (SIMANTW.SYM seg5:0C54).  For each row si
    (0..0x3F), on plane 2 then plane 3, a water-source tile (0x4E) becomes a
    random 0..7 via `_SRand1(8)` (advancing the shared LFSR seed at 0xCBF2); any
    other tile drops by 0x2F (as a byte).  Mutates only the map bytes and the
    RNG seed; the original also redraws each changed tile (a side effect).
    `view` is a DGROUP byte/word view.
    """
    from .simone import SRAND_SEED_OFF, srand1
    for si in range(0x40):
        for base in (MAP_PLANE_BASE[2], MAP_PLANE_BASE[3]):
            off = base + (si << 6) + x
            tile = view.rb(off)
            if tile == 0x4E:
                seed, val = srand1(view.rw(SRAND_SEED_OFF), 8)
                view.ww(SRAND_SEED_OFF, seed)
            else:
                val = tile - 0x2F
            view.wb(off, val & 0xFF)


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


def find_in_a_list(pack, simant_data_group, target0: int, target1: int) -> int:
    """Search the yard ("A") ant list backward for the last-added slot whose
    recorded fields match (target0, target1) and whose third field is nonzero.

    Recovered from `_FindInAList` (SIMANTW.SYM seg5:2C42, args target0=[bp+6],
    target1=[bp+8]).  The live slot count is read from `pack[0x80F0]`; per-slot
    fields are parallel byte arrays in SIMANT_DATA_GROUP: `[0x23A4 + slot] ==
    target0`, `[0x278E + slot] == target1`, `[0x2F62 + slot] != 0`.  Search order
    is backward (highest slot first — the most recently added ant matches
    first).  Returns the found 0-based slot index, or 0xFFFF if none match.
    """
    count = pack.rw(0x80F0)
    for slot in range(count - 1, -1, -1):
        if (simant_data_group.rb(0x23A4 + slot) == target0
                and simant_data_group.rb(0x278E + slot) == target1
                and simant_data_group.rb(0x2F62 + slot) != 0):
            return slot
    return 0xFFFF


def find_in_b_list(pack, simant_data_group, y: int, x: int, caste: int) -> int:
    """Search the black colony's ant list backward for the last-added slot at
    (x, y) with the given caste.

    Recovered from `_FindInBList` (SIMANTW.SYM seg5:2C86, args y=[bp+6],
    x=[bp+8], caste=[bp+0xa]).  Uses the SAME per-ant record arrays
    `kill_tail_b` writes: `pack[0x99D4]` for the count, and SIMANT_DATA_GROUP's
    `[0x3736 + slot]` (Y), `[0x392C + slot]` (X), `[0x3D18 + slot]` (caste/type
    — the field `kill_tail_b` clears to 0 when a tail dies).  Returns the found
    0-based slot index, or 0xFFFF if none match.
    """
    count = pack.rw(0x99D4)
    for slot in range(count - 1, -1, -1):
        if (simant_data_group.rb(0x3736 + slot) == y
                and simant_data_group.rb(0x392C + slot) == x
                and simant_data_group.rb(0x3D18 + slot) == caste):
            return slot
    return 0xFFFF


def find_in_r_list(pack, simant_data_group, y: int, x: int, caste: int) -> int:
    """The red-colony twin of `find_in_b_list` (grid/array bases matching
    `kill_tail_r`'s: count `pack[0x72CC]`, arrays `[0x4104+slot]` (Y),
    `[0x42FA+slot]` (X), `[0x46E6+slot]` (caste)).

    Recovered from `_FindInRList` (SIMANTW.SYM seg5:2CCE, args y=[bp+6],
    x=[bp+8], caste=[bp+0xa]).
    """
    count = pack.rw(0x72CC)
    for slot in range(count - 1, -1, -1):
        if (simant_data_group.rb(0x4104 + slot) == y
                and simant_data_group.rb(0x42FA + slot) == x
                and simant_data_group.rb(0x46E6 + slot) == caste):
            return slot
    return 0xFFFF


def add_ant_to_a_list(pack, simant_data_group, dgroup, target0: int,
                      target1: int, caste: int, field_c: int,
                      field_e: int) -> None:
    """Append a new yard ("A") ant record, unless the list is already at its
    1000-slot (0x3E8) cap.

    Recovered from `_AddAntToAList` (SIMANTW.SYM seg5:2EF0, args target0=[bp+6],
    target1=[bp+8], caste=[bp+0xa], field_c=[bp+0xc], field_e=[bp+0xe]).  The
    new slot is the current count (`pack[0x80F0]`, appended at the end, then
    incremented).  Per-slot fields (SIMANT_DATA_GROUP byte arrays, matching
    `find_in_a_list`'s [0x23A4]/[0x278E]/[0x2F62]): `[0x23A4+slot]=target0`,
    `[0x278E+slot]=target1`, `[0x2F62+slot]=caste` (the same field
    `find_in_a_list` checks nonzero), plus two fields with unconfirmed meaning:
    `[0x2B78+slot]=field_c`, `[0x334C+slot]=field_e`.  Also stamps `caste` into
    the yard life-grid (plane 0) at `dgroup[LIFE_PLANE_BASE[0] + target1 +
    (target0 << 6)]` — target0 is the *64 (row) term here, target1 the +1 term
    (note: the OPPOSITE assignment from `map_cell_offset`'s x/y convention;
    this is this per-ant array's own established layout, matching
    `kill_tail_b`'s y/x roles one-for-one).
    """
    count = pack.rw(0x80F0)
    if count >= 0x3E8:
        return
    simant_data_group.wb(0x23A4 + count, target0 & 0xFF)
    simant_data_group.wb(0x278E + count, target1 & 0xFF)
    simant_data_group.wb(0x2B78 + count, field_c & 0xFF)
    simant_data_group.wb(0x2F62 + count, caste & 0xFF)
    simant_data_group.wb(0x334C + count, field_e & 0xFF)
    dgroup.wb(LIFE_PLANE_BASE[0] + target1 + ((target0 & 0xFF) << 6), caste & 0xFF)
    pack.ww(0x80F0, (count + 1) & 0xFFFF)


def add_ant_to_b_list(pack, simant_data_group, dgroup, y: int, x: int,
                      caste: int, field_c: int, field_e: int) -> None:
    """Append a new black-colony ant record, unless the list is already at its
    500-slot (0x1F4) cap — the black-colony twin of `add_ant_to_a_list`.

    Recovered from `_AddAntToBList` (SIMANTW.SYM seg5:2F4A, args y=[bp+6],
    x=[bp+8], caste=[bp+0xa], field_c=[bp+0xc], field_e=[bp+0xe]).  New slot =
    `pack[0x99D4]` (appended, then incremented).  Per-slot fields match
    `find_in_b_list`/`kill_tail_b`: `[0x3736+slot]=y`, `[0x392C+slot]=x`,
    `[0x3D18+slot]=caste`, plus `[0x3B22+slot]=field_c`, `[0x3F0E+slot]=field_e`
    (unconfirmed meaning).  Stamps `caste` into life plane 2 at
    `dgroup[LIFE_PLANE_BASE[2] + x + (y << 6)]` (matching `kill_tail_b`'s x/y
    roles).
    """
    count = pack.rw(0x99D4)
    if count >= 0x1F4:
        return
    simant_data_group.wb(0x3736 + count, y & 0xFF)
    simant_data_group.wb(0x392C + count, x & 0xFF)
    simant_data_group.wb(0x3B22 + count, field_c & 0xFF)
    simant_data_group.wb(0x3D18 + count, caste & 0xFF)
    simant_data_group.wb(0x3F0E + count, field_e & 0xFF)
    dgroup.wb(LIFE_PLANE_BASE[2] + (x & 0xFF) + ((y & 0xFF) << 6), caste & 0xFF)
    pack.ww(0x99D4, (count + 1) & 0xFFFF)


def add_ant_to_r_list(pack, simant_data_group, dgroup, y: int, x: int,
                      caste: int, field_c: int, field_e: int) -> None:
    """The red-colony twin of `add_ant_to_b_list` (500-slot cap; matching
    `find_in_r_list`/`kill_tail_r`'s arrays and life plane 3).

    Recovered from `_AddAntToRList` (SIMANTW.SYM seg5:2FA4, args y=[bp+6],
    x=[bp+8], caste=[bp+0xa], field_c=[bp+0xc], field_e=[bp+0xe]).
    """
    count = pack.rw(0x72CC)
    if count >= 0x1F4:
        return
    simant_data_group.wb(0x4104 + count, y & 0xFF)
    simant_data_group.wb(0x42FA + count, x & 0xFF)
    simant_data_group.wb(0x44F0 + count, field_c & 0xFF)
    simant_data_group.wb(0x46E6 + count, caste & 0xFF)
    simant_data_group.wb(0x48DC + count, field_e & 0xFF)
    dgroup.wb(LIFE_PLANE_BASE[3] + (x & 0xFF) + ((y & 0xFF) << 6), caste & 0xFF)
    pack.ww(0x72CC, (count + 1) & 0xFFFF)


def _drop_food(dgroup, pack, simant_data_group, plane: int, counter_off: int,
               caste_off: int, x: int, y: int) -> int:
    """Shared body of drop_food_b/r: grow a food pile on the map, then clear
    the acting ant's "carrying food" flag.  Map cell =
    `MAP_PLANE_BASE[plane] + (x<<6) + y` (the map's own x=*64/y=+1 convention,
    unlike the per-ant arrays).  A tile below the food-pile minimum (0x10) is
    set to 0x10 (a pile starts); a tile below the maximum (0x13) grows by 1; a
    full tile (0x13) is left unchanged.  Returns 1 if food was dropped
    (either branch fired), 0 if the pile was already full.

    Bookkeeping runs UNCONDITIONALLY (even when the pile was full): increments
    a "total dropped" counter at `pack[counter_off]`, then clears bit 0x08 of
    the ACTING ant's caste byte (`simant_data_group[caste_off +
    pack[0x9B6A]]` — `pack[0x9B6A]` is a shared "which ant is dropping"
    context slot the caller sets, reused by both colonies) if that bit is set.
    """
    off = MAP_PLANE_BASE[plane] + (x << 6) + y
    tile = dgroup.rb(off)
    dropped = 0
    if tile < 0x10:
        dgroup.wb(off, 0x10)
        dropped = 1
    elif tile < 0x13:
        dgroup.wb(off, tile + 1)
        dropped = 1
    pack.ww(counter_off, (pack.rw(counter_off) + 1) & 0xFFFF)
    slot = caste_off + pack.rw(0x9B6A)
    caste = simant_data_group.rb(slot)
    if caste & 0x08:
        simant_data_group.wb(slot, (caste - 0x08) & 0xFF)
    return dropped


def drop_food_b(dgroup, pack, simant_data_group, x: int, y: int) -> int:
    """Drop food onto the black colony's nest map tile at (x, y) (plane 2,
    counter `pack[0x9EA4]`, caste array base 0x3D18 — see `_drop_food`).

    Recovered from `_DropFoodB` (SIMANTW.SYM seg6:3C3C, args x=[bp+6], y=[bp+8]).
    """
    return _drop_food(dgroup, pack, simant_data_group, 2, 0x9EA4, 0x3D18, x, y)


def drop_food_r(dgroup, pack, simant_data_group, x: int, y: int) -> int:
    """The red-colony twin of `drop_food_b` (plane 3, counter `pack[0x72DE]`,
    caste array base 0x46E6).

    Recovered from `_DropFoodR` (SIMANTW.SYM seg6:6242, args x=[bp+6], y=[bp+8]).
    """
    return _drop_food(dgroup, pack, simant_data_group, 3, 0x72DE, 0x46E6, x, y)


def set_ant_index(pack, simant_data_group, list_type: int, slot: int,
                  target0: int, target1: int, caste: int, field_c: int,
                  field_e: int) -> None:
    """Overwrite an EXISTING ant record's fields at `slot` — a no-op if `slot`
    is out of the list's current bounds.  Unlike `add_ant_to_*_list`, this does
    NOT append, does NOT touch the life grid, and does NOT change the count.

    Recovered from `_SetAntIndex` (SIMANTW.SYM seg5:584A, args
    list_type=[bp+6], slot=[bp+8], target0=[bp+0xa], target1=[bp+0xc],
    caste=[bp+0xe], field_c=[bp+0x10], field_e=[bp+0x12]).  `list_type` selects
    the list the same way `MAP_PLANE_BASE`/`LIFE_PLANE_BASE` select a plane:
    <=1 -> the yard ("A") list (count 0x80F0; fields 0x23A4/0x278E/0x2F62/
    0x2B78/0x334C), ==2 -> the black ("B") list (0x99D4; 0x3736/0x392C/0x3D18/
    0x3B22/0x3F0E), anything else -> the red ("R") list (0x72CC; 0x4104/
    0x42FA/0x46E6/0x44F0/0x48DC) — confirmed from the ASM's own `cmp/jg` +
    `cmp/jne` dispatch, mirroring the established plane-numbering convention.
    `slot` must satisfy `0 <= slot < count` (signed) or the write is skipped.
    """
    if list_type <= 1:
        count_off, f0, f1, caste_off, fc_off, fe_off = (
            0x80F0, 0x23A4, 0x278E, 0x2F62, 0x2B78, 0x334C)
    elif list_type == 2:
        count_off, f0, f1, caste_off, fc_off, fe_off = (
            0x99D4, 0x3736, 0x392C, 0x3D18, 0x3B22, 0x3F0E)
    else:
        count_off, f0, f1, caste_off, fc_off, fe_off = (
            0x72CC, 0x4104, 0x42FA, 0x46E6, 0x44F0, 0x48DC)
    if not (0 <= slot < pack.rw(count_off)):
        return
    simant_data_group.wb(f0 + slot, target0 & 0xFF)
    simant_data_group.wb(f1 + slot, target1 & 0xFF)
    simant_data_group.wb(caste_off + slot, caste & 0xFF)
    simant_data_group.wb(fc_off + slot, field_c & 0xFF)
    simant_data_group.wb(fe_off + slot, field_e & 0xFF)


def get_smell_t(simant_data_group, p: int, q: int, direction: int,
                is_red) -> int:
    """Read a colony's TRAIL scent grid at a cell offset from (p, q) by a
    per-direction delta looked up from two small tables in SIMANT_DATA_GROUP.

    Recovered from `_GetSmellT` (SIMANTW.SYM seg6:9612, args p=[bp+4],
    q=[bp+6], direction=[bp+8], is_red=[bp+0xa]).  Grid cell = `(si<<5) + di`
    where `si = p + sign_extend(simant_data_group[0 + direction])` (clamped
    0..63, else return 0 immediately) and `di = q + sign_extend(
    simant_data_group[8 + direction])` (clamped 0..31, else return 0).  Reads
    red colony's grid at 0x7AD2, black's at 0x6AD2 — the same trail-scent grids
    `jam_scent_bt`/`rt`, `colony_smell_decay_bt`/`rt`, and `dec_t_smell`
    operate on.  The two small direction-delta tables are read LIVE from
    `simant_data_group` (not hardcoded) — they are genuine game data, not a
    fixed constant this recovery should assume.
    """
    def sbyte(off):
        v = simant_data_group.rb(off)
        return v - 0x100 if v & 0x80 else v

    si = p + sbyte(0 + direction)
    di = q + sbyte(8 + direction)
    if si < 0 or si > 0x3F or di < 0 or di > 0x1F:
        return 0
    base = 0x7AD2 if is_red else 0x6AD2
    return simant_data_group.rb(base + (si << 5) + di)


def dec_t_smell(simant_data_group, x: int, y: int, is_red) -> None:
    """Decrement a single cell of a colony's TRAIL scent grid by 1, if nonzero.

    Recovered from `_DecTSmell` (SIMANTW.SYM seg6:95B6, args x=[bp+4], y=[bp+6],
    is_red=[bp+8]).  Cell = `((x>>1)<<5) + (y>>1)` (arithmetic shifts) on the
    SAME 64x32 half-res trail grid `jam_scent_bt`/`rt` and
    `colony_smell_decay_bt`/`rt` operate on — red colony's grid at 0x7AD2,
    black's at 0x6AD2 (`is_red` selects which; any nonzero value is "true",
    matching the ASM's `cmp ..., 0`).
    """
    idx = ((_sx16(x) >> 1) << 5) + (_sx16(y) >> 1)
    base = 0x7AD2 if is_red else 0x6AD2
    v = simant_data_group.rb(base + idx)
    if v != 0:
        simant_data_group.wb(base + idx, v - 1)


def kill_tail_b(dgroup, simant_data_group, ant_idx: int) -> None:
    """Remove a black-colony ant's tail segment from the sim.

    Recovered from `_KillTailB` (SIMANTW.SYM seg6:42B0, arg: ant_idx).  Clears
    the ant's has-tail flag (`simant_data_group[0x3D18 + ant_idx]`), reads its
    recorded position (x = `simant_data_group[0x392C + ant_idx]`, y = the low
    byte of `simant_data_group` word `[0x3736 + ant_idx]`), and clears the life
    grid cell at that position on plane 2 (the black colony's nest life plane;
    `dgroup`, since the ASM's life-grid write has no ES override — it targets
    the default DS/DGROUP, unlike the per-ant fields which go through ES =
    SIMANT_DATA_GROUP).  No bounds check on (x, y) — matches the ASM exactly,
    including wraparound if the recorded position were ever out of range.
    """
    simant_data_group.wb(0x3D18 + ant_idx, 0)
    x = simant_data_group.rb(0x392C + ant_idx)
    y = simant_data_group.rw(0x3736 + ant_idx) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[2] + x + (y << 6), 0)


def _colony_decay_linear(view, base: int) -> None:
    """Shared body of colony_smell_decay_[br]n: decrement every nonzero cell of
    a 64x32 (2048-cell) half-res scent grid by 1 (floor at 0, via an explicit
    nonzero guard — decrementing an already-0 byte would underflow to 0xFF)."""
    for i in range(0x800):
        v = view.rb(base + i)
        if v != 0:
            view.wb(base + i, v - 1)


def colony_smell_decay_bn(simant_data_group) -> None:
    """Decay the black colony's NEST home-scent grid by 1/tick (floor 0).

    Recovered from `_ColonySmellBN` (SIMANTW.SYM seg6:92AA).  A 64x32 half-res
    grid at `simant_data_group[0x62D2 .. +0x800)`.
    """
    _colony_decay_linear(simant_data_group, 0x62D2)


def colony_smell_decay_rn(simant_data_group) -> None:
    """The red-colony twin of `colony_smell_decay_bn` (grid at [0x72D2..)).

    Recovered from `_ColonySmellRN` (SIMANTW.SYM seg6:92D8).
    """
    _colony_decay_linear(simant_data_group, 0x72D2)


def _colony_decay_exponential(view, base: int) -> None:
    """Shared body of colony_smell_decay_[br]t: halve every cell of a 64x32
    half-res TRAIL scent grid (new = v - (v >> 1), i.e. ceil(v/2)); a cell
    already below 8 snaps straight to 0 instead of decaying gradually."""
    for i in range(0x800):
        v = view.rb(base + i)
        view.wb(base + i, 0 if v < 8 else v - (v >> 1))


def colony_smell_decay_bt(simant_data_group) -> None:
    """Decay the black colony's TRAIL scent grid (exponential; grid at
    [0x6AD2..), see `_colony_decay_exponential`).

    Recovered from `_ColonySmellBT` (SIMANTW.SYM seg6:9306).
    """
    _colony_decay_exponential(simant_data_group, 0x6AD2)


def colony_smell_decay_rt(simant_data_group) -> None:
    """The red-colony twin of `colony_smell_decay_bt` (grid at [0x7AD2..)).

    Recovered from `_ColonySmellRT` (SIMANTW.SYM seg6:9344).
    """
    _colony_decay_exponential(simant_data_group, 0x7AD2)


def _jam_scent(view, base: int, x: int, y: int, value: int) -> None:
    """Shared body of jam_scent_{b,r}{n,t}: set-if-greater a cell on a 64x32
    half-res scent grid at `base`.  Cell = `((x & 0xFFFE) << 4) + (y >> 1)`
    (arithmetic shift on y) — equal to `(x >> 1) * 32 + (y >> 1)` for
    non-negative x, matching the colony_smell_decay_* iteration order.  Only
    writes when `value` is (signed) greater than the existing cell; the stored
    value is truncated to a byte.
    """
    idx = ((x & 0xFFFE) << 4) + (_sx16(y) >> 1)
    if _sx16(view.rb(base + idx)) < _sx16(value):
        view.wb(base + idx, value & 0xFF)


def jam_scent_bn(simant_data_group, x: int, y: int, value: int) -> None:
    """Jam the black colony's NEST scent (grid at [0x62D2..), see `_jam_scent`).

    Recovered from `_JamScentBN` (SIMANTW.SYM seg6:94B6, args x, y, value).
    """
    _jam_scent(simant_data_group, 0x62D2, x, y, value)


def jam_scent_rn(simant_data_group, x: int, y: int, value: int) -> None:
    """The red-colony twin of `jam_scent_bn` (grid at [0x72D2..)).

    Recovered from `_JamScentRN` (SIMANTW.SYM seg6:94F6, args x, y, value).
    """
    _jam_scent(simant_data_group, 0x72D2, x, y, value)


def jam_scent_bt(simant_data_group, x: int, y: int, value: int) -> None:
    """Jam the black colony's TRAIL scent (grid at [0x6AD2..)).

    Recovered from `_JamScentBT` (SIMANTW.SYM seg6:9536, args x, y, value).
    """
    _jam_scent(simant_data_group, 0x6AD2, x, y, value)


def jam_scent_rt(simant_data_group, x: int, y: int, value: int) -> None:
    """The red-colony twin of `jam_scent_bt` (grid at [0x7AD2..)).

    Recovered from `_JamScentRT` (SIMANTW.SYM seg6:9576, args x, y, value).
    """
    _jam_scent(simant_data_group, 0x7AD2, x, y, value)


def alarm_here(simant_data_group, x: int, y: int, delta: int) -> int:
    """Add `delta` to the alarm level at (x, y), clamped to a max of 200
    (0xC8) but with NO lower clamp — a large negative delta wraps the stored
    byte, matching the ASM exactly (only the upper bound is checked before the
    byte-truncating store).  Grid cell = `((x>>1)<<5) + (y>>1)` (arithmetic
    shifts) on the 64x32 half-res alarm grid at `simant_data_group[0x52D2..)`.
    Returns the stored (post-clamp, post-truncation) byte value.

    Recovered from `_AlarmHere` (SIMANTW.SYM seg6:943C, args x, y, delta).
    """
    idx = ((_sx16(x) >> 1) << 5) + (_sx16(y) >> 1)
    summed = (simant_data_group.rb(0x52D2 + idx) + delta) & 0xFFFF
    v = 0xC8 if _sx16(summed) > 0xC8 else summed
    stored = v & 0xFF
    simant_data_group.wb(0x52D2 + idx, stored)
    return stored


def alarm_here2(simant_data_group, x: int, y: int, value: int) -> None:
    """Set-if-not-less the alarm level at (x, y) to `value` (same grid as
    `alarm_here`): only overwrites when the existing (always non-negative
    byte) cell is <= `value` (signed compare); the stored value is truncated
    to a byte.

    Recovered from `_AlarmHere2` (SIMANTW.SYM seg6:947E, args x, y, value).
    """
    idx = ((_sx16(x) >> 1) << 5) + (_sx16(y) >> 1)
    existing = simant_data_group.rb(0x52D2 + idx)
    if _sx16(existing) <= _sx16(value):
        simant_data_group.wb(0x52D2 + idx, value & 0xFF)


def kill_tail_r(dgroup, simant_data_group, ant_idx: int) -> None:
    """Remove a red-colony ant's tail segment from the sim — the twin of
    `kill_tail_b` on the red colony's per-ant fields and life plane 3.

    Recovered from `_KillTailR` (SIMANTW.SYM seg6:6762, arg: ant_idx).
    """
    simant_data_group.wb(0x46E6 + ant_idx, 0)
    x = simant_data_group.rb(0x42FA + ant_idx)
    y = simant_data_group.rw(0x4104 + ant_idx) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[3] + x + (y << 6), 0)


def dec_eat_b(dgroup, simant_data_group, pack) -> None:
    """Tick the black colony's hunger-decay clock; starve it by one food unit
    on each expiry, unless the "no-starve" cheat flag is set.

    Recovered from `_DecEatB` (SIMANTW.SYM seg6:48F8).  A per-tick countdown at
    `pack[0x7402]` is decremented; when it goes negative (signed), it is reset to
    `dgroup[0xAC82] >> 5` (the colony's configured hunger-decay rate, arithmetic
    shift).  If the colony's food supply `dgroup[0xAC86]` is > 0 AND the
    black-colony "no-starve" flag at `simant_data_group[0x8A60]` is clear, the
    food supply is decremented by 1.  (`simant_data_group`/`pack` are the fixed
    NE data segments the game reaches through DGROUP pointer-globals; see
    `hooks.SIMANT_DATA_GROUP_SEG_INDEX` / `PACK_SEG_INDEX`.)
    """
    t = (pack.rw(0x7402) - 1) & 0xFFFF
    pack.ww(0x7402, t)
    if _sx16(t) < 0:
        pack.ww(0x7402, (_sx16(dgroup.rw(0xAC82)) >> 5) & 0xFFFF)
        if _sx16(dgroup.rw(0xAC86)) > 0 and simant_data_group.rw(0x8A60) == 0:
            dgroup.ww(0xAC86, (dgroup.rw(0xAC86) - 1) & 0xFFFF)


def dec_eat_r(dgroup, pack) -> None:
    """Tick the red colony's hunger-decay clock; starve it by one food unit on
    each expiry (no cheat-flag gate — that check exists only for the black
    colony's `dec_eat_b`).

    Recovered from `_DecEatR` (SIMANTW.SYM seg6:6C6A).  A per-tick countdown at
    `pack[0x7C8E]` is decremented; when it goes negative, it is reset to
    `dgroup[0xAC84] >> 5`.  If the colony's food supply `dgroup[0xAC88]` is > 0
    it is decremented by 1.
    """
    t = (pack.rw(0x7C8E) - 1) & 0xFFFF
    pack.ww(0x7C8E, t)
    if _sx16(t) < 0:
        pack.ww(0x7C8E, (_sx16(dgroup.rw(0xAC84)) >> 5) & 0xFFFF)
        if _sx16(dgroup.rw(0xAC88)) > 0:
            dgroup.ww(0xAC88, (dgroup.rw(0xAC88) - 1) & 0xFFFF)
