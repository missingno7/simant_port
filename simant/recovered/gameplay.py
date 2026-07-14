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


def _sx32(v: int) -> int:
    """Sign-extend a 32-bit dword to a Python int."""
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


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


def dead_ant_here(dgroup, pack, new_x: int, new_y: int, mode: int) -> None:
    """Ring-buffer corpse-decay marker: record where an ant just died, and
    fade/remove the mark left ~100 ticks ago at the slot now cycling out.

    Recovered from `_DeadAntHere` (SIMANTW.SYM seg6:28C0, args: new_x,
    new_y, mode; FAR return).  A 100-slot ring buffer lives in PACK: a word
    counter at `[0x9EA8]` (incremented and wrapped to 0 at 100 on every
    call), a byte-per-slot X table at `[0x9C82..)`, and a word-per-slot
    (but only ever read masked to a byte, and only ever WRITTEN a byte) Y
    table at `[0x9D76..)` — both indexed by the raw counter value, not
    counter*width (the same convention the per-ant list arrays use).  Every
    call:

    1. Advances the counter and reads the OLD (x, y) recorded in the slot
       it now points at — the position from ~100 calls ago — then reads the
       yard map tile there.
    2. If PACK's `[0x9B6E]` "inside" flag is clear (outside the nest) and
       that tile is in `0x10..0x17`, replaces it with a fresh `_SRand16()`
       (0..15) — fading an old marker back to generic terrain.  If the flag
       is set (inside) and the tile is in `0x08..0x17`, replaces it with
       `(tile - 8) >> 2` instead — a coarser, non-random fade specific to
       the nest tile encoding.
    3. Overwrites the ring-buffer slot with the caller's (new_x, new_y).
    4. Reads the yard map tile at the NEW position; if it's below the same
       mode-specific threshold as step 2 (0x18 outside / 4 inside), plants
       a fresh marker there: outside, `_SRand4() + (0x14 if mode else
       0x10)`; inside, `_SRand1(2) + tile*4 + 0xA` when `mode` else
       `_SRand1(2) + (tile + 2)*4` (`tile` here is this same fresh read at
       the new position, not the ring buffer's evicted entry).
    5. Always clears the yard life-grid cell at the new position.

    Threads the shared `_SRand*` LFSR seed at `dgroup[SRAND_SEED_OFF]`
    through every RNG call, in ASM call order (up to two calls per
    invocation: one for the evicted slot's fade, one for the fresh marker).
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    counter = (pack.rw(0x9EA8) + 1) & 0xFFFF
    if counter >= 0x64:
        counter = 0
    pack.ww(0x9EA8, counter)

    old_x = pack.rb(0x9C82 + counter)
    old_y = pack.rw(0x9D76 + counter) & 0xFF
    old_off = MAP_PLANE_BASE[0] + (old_x << 6) + old_y
    old_tile = dgroup.rb(old_off)

    seed = dgroup.rw(SRAND_SEED_OFF)
    inside = pack.rw(0x9B6E) != 0

    if not inside:
        if 0x10 <= old_tile < 0x18:
            seed, val = srand_pow2(seed, 0xF)
            dgroup.wb(old_off, val & 0xFF)
    else:
        if 8 <= old_tile < 0x18:
            dgroup.wb(old_off, ((old_tile - 8) >> 2) & 0xFF)

    pack.wb(0x9C82 + counter, new_x & 0xFF)
    pack.wb(0x9D76 + counter, new_y & 0xFF)

    new_off = MAP_PLANE_BASE[0] + (new_x << 6) + new_y
    new_tile = dgroup.rb(new_off)

    if not inside:
        if new_tile < 0x18:
            seed, val = srand_pow2(seed, 3)
            val += 0x14 if mode else 0x10
            dgroup.wb(new_off, val & 0xFF)
    else:
        if new_tile < 4:
            seed, val = srand1(seed, 2)
            val += new_tile * 4 + 0xA if mode else (new_tile + 2) * 4
            dgroup.wb(new_off, val & 0xFF)

    dgroup.ww(SRAND_SEED_OFF, seed)
    dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, 0)


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


def tile_can_be_moved_on(dgroup, inside: bool, plane: int, x: int, y: int,
                          cand_plane: int, cand_x: int, cand_y: int,
                          check_adjacent: bool) -> int:
    """Whether an ant considering a move can step onto map cell (plane, x, y).

    Recovered from `_TileCanBeMovedOn` (SIMANTW.SYM seg5:9342, 7 args: plane,
    x, y, cand_plane, cand_x, cand_y, check_adjacent).  Bounds-checked exactly
    like `is_valid_a`/`is_valid_b`; out of range is never movable.

    On the yard planes (`plane <= 1`) this is just `is_not_obstacle`'s
    plane<=1 rule with a wider "inside" threshold (0x90 instead of 0x5F) —
    the 7 trailing args are unused on this path.

    On the nest planes (`plane > 1`; plane==2 selects the black-colony map,
    every other value selects red) the tile is first classified "clear":
    `tile <= 0x18` or a pebble (`0x30..0x31`) is unconditionally clear; when
    `check_adjacent` is set, the wider dirt band `0x1C..0x2E` also counts as
    clear (marked "extended" below) — a plain not-clear tile returns 0
    immediately.  A clear cell is then checked against a second candidate
    site (`cand_plane`/`cand_x`/`cand_y`) that the caller passes alongside
    its own position — the ASM's control flow reads as "assume clear unless
    this cell coincides with that other site", ported byte-exact below (the
    caller, `_GetMyBestDirs`, always passes its own current position, so in
    practice this excludes the ant's own square from being counted as a move
    target — but this routine has no way to know that; it only compares
    values):

    - `y > 1`, or `cand_plane != plane`: clear -> 1 (never reaches the
      candidate-site comparison).
    - `check_adjacent` is False:
        - `y == 0`: clear only if `x == cand_x and cand_y == 0`.
        - `y == 1`: clear if `x == cand_x or cand_y != 0`.
    - `check_adjacent` is True and NOT extended (hard-clear tile):
        - `y != 0`: clear -> 1.
        - `y == 0`: clear only if `x == cand_x and cand_y == 0`.
    - `check_adjacent` is True and extended (dirt-band tile):
        - `x != cand_x`: not clear -> 0.
        - `y != 0`: clear -> 1.
        - `y == 0`: reads the neighbour cell at `(x, y+1)` on the SAME plane;
          clear only if that neighbour is OUTSIDE the dirt band `0x20..0x2E`.
    """
    if plane <= 1:
        if not (0 <= x <= 0x7F and 0 <= y <= 0x3F):
            return 0
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        threshold = 0x90 if inside else 0x53
        return 1 if tile <= threshold else 0

    if not (0 <= x <= 0x3F and 0 <= y <= 0x3F):
        return 0
    base = MAP_PLANE_BASE[2] if plane == 2 else MAP_PLANE_BASE[3]
    idx = (x << 6) + y
    tile = dgroup.rb(base + idx)

    if tile <= 0x18 or 0x30 <= tile <= 0x31:
        clear, extended = 1, False
    elif check_adjacent and (0x1C <= tile <= 0x1F or 0x20 <= tile <= 0x2E):
        clear, extended = 1, True
    else:
        clear, extended = 0, False
    if not clear:
        return 0

    if y > 1 or cand_plane != plane:
        return 1

    if not check_adjacent:
        if y == 0:
            return 1 if (x == cand_x and cand_y == 0) else 0
        return 1 if (x == cand_x or cand_y != 0) else 0

    if extended:
        if x != cand_x:
            return 0
        if y != 0:
            return 1
        neighbor = dgroup.rb(base + idx + 1)
        return 0 if 0x20 <= neighbor <= 0x2E else 1

    if y != 0:
        return 1
    return 1 if (x == cand_x and cand_y == 0) else 0


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


def get_my_best_dirs(dgroup, pack, inside: bool, plane: int, cur_x: int,
                     cur_y: int, tgt_x: int, tgt_y: int) -> int:
    """The best 8-way step from (cur_x, cur_y) toward (tgt_x, tgt_y) for the
    player-controlled ("my") ant — the movement-candidate sibling of
    `get_best_dir`.

    Recovered from `_GetMyBestDirs` (SIMANTW.SYM seg6:8828, args: plane,
    cur_x, cur_y, tgt_x, tgt_y; FAR return).  Same scan-and-keep-the-closer-
    neighbour shape as `get_best_dir`, but composed from different building
    blocks: the movement gate is `tile_can_be_moved_on` (not
    `is_not_obstacle`/`is_this_pebble`), and it reads a genuine `_GetLife`-
    style life value for the occupied check, then re-derives the raw life
    byte and map tile for `is_clear_tile` — the two checks are provably the
    same (`_GetLife`'s 0-> 0xFFFF transform only changes the *empty* case,
    and empty means "not occupied" either way), so this reads the raw byte
    once and reuses it for both.

    Before the scan, three PACK-resident "candidate site" fields (a fixed
    world-state slot the ASM reads through DGROUP pointer-globals
    `[0xC3BE]`/`[0xC3B8]`/`[0xC3BC]`, all of which resolve to the PACK
    segment) and a PACK flag (`[0xC3AE]:[0x9BC4] == 2`, via `[0xC3AE]`) are
    read ONCE and threaded into every `tile_can_be_moved_on` call as its
    `cand_plane`/`cand_x`/`cand_y`/`check_adjacent` — the ant's own current
    position and a "strict adjacency" mode flag.
    """
    best_dist = get_dis(cur_x, cur_y, tgt_x, tgt_y)
    if best_dist <= 0:
        return -1
    check_adjacent = pack.rw(0x9BC4) == 2
    cand_plane = pack.rw(0x9BE0)
    cand_x = pack.rw(0x80C6)
    cand_y = pack.rw(0x80D2)
    best_clear, best_any = -1, -2
    for si in range(8):
        nx = cur_x + GET_BEST_DIR_DX[si]
        ny = cur_y + GET_BEST_DIR_DY[si]
        if not tile_can_be_moved_on(dgroup, inside, plane, nx, ny, cand_plane,
                                    cand_x, cand_y, check_adjacent):
            continue
        dist = get_dis(nx, ny, tgt_x, tgt_y)
        if dist >= best_dist:
            continue
        best_dist = dist
        life_off = life_cell_offset(plane, nx, ny)
        raw_life = dgroup.rb(life_off) if life_off is not None else 0
        if raw_life > 0:                                  # occupied -> fallback
            best_any = si
        else:
            tile_off = map_cell_offset(plane, nx, ny)
            tile = dgroup.rb(tile_off) if tile_off is not None else 0
            if is_clear_tile(plane, tile, raw_life):
                best_clear = si
            else:
                best_any = si
    return best_clear if best_clear >= 0 else best_any


def get_my_rand_dirs(dgroup, pack, out1, out2, inside: bool, plane: int,
                     cur_x: int, cur_y: int, tgt_x: int, tgt_y: int) -> int:
    """Sticky-direction search: keep the ant moving in roughly the same
    8-way direction across calls instead of re-picking from scratch each
    time, only recomputing when the remembered direction becomes blocked.

    Recovered from `_GetMyRandDirs` (SIMANTW.SYM seg6:8928, args: two FAR
    pointer outputs, then plane, cur_x, cur_y, tgt_x, tgt_y; FAR return).
    `out1`/`out2` model the two caller-owned far-pointer cells as 1-element
    lists: `out1[0]` is a tri-state mode flag (0 = "no direction committed
    yet", 1 = "committed, found via the forward scan", 0xFFFF/-1 =
    "committed, found via the backward scan") read on entry and written on
    exit; `out2[0]` is the committed direction index (0..7) on entry (when
    `out1[0] != 0`) or the compass direction `get_dir(cur, tgt) - 1` on a
    fresh commit.

    First builds a clearance mask over the 8 neighbours (same delta tables
    and `tile_can_be_moved_on` gate as `get_my_best_dirs`, plus a special
    case: a neighbour that exactly matches a PACK-resident "avoid" cell —
    `pack[0xA0D6]`/`[0xA0DA]` — is forced blocked without even calling
    `tile_can_be_moved_on`). Returns -1 immediately if already at the
    target, -2 if no neighbour is clear at all (no pointer writes in either
    case).

    - `out1[0] == 0` (no prior commitment): sweeps outward from `out2[0]`
      in both directions at once (`fwd` incrementing, `back` decrementing,
      mod 8) for the first clear cell; commits it (writes `out1[0]` = 1 for
      a forward hit, 0xFFFF for a backward hit, and `out2[0]` = the fresh
      compass direction) and returns its index.
    - `out1[0] != 0` (already committed): re-checks the SAME remembered
      index (`out2[0]`, tracked via `chosen1`/`chosen2` depending on which
      scan found it last time) each of up to 8 iterations; if it is STILL
      clear, recomputes (returns the index; if the neighbour's distance to
      target got worse than the last known best, only returns the index —
      no writes; otherwise also refreshes `out2[0]`/resets `out1[0]` = 0).
      If it became blocked, both trackers advance (`chosen1`+1, `chosen2`-1,
      mod 8) and the loop retries. Exhausting all 8 without a hit returns -1.
    """
    best_dist = get_dis(cur_x, cur_y, tgt_x, tgt_y)
    if best_dist <= 0:
        return -1

    check_adjacent = pack.rw(0x9BC4) == 2
    cand_plane = pack.rw(0x9BE0)
    cand_x = pack.rw(0x80C6)
    cand_y = pack.rw(0x80D2)
    avoid_x = pack.rw(0xA0D6)
    avoid_y = pack.rw(0xA0DA)

    mark = [0] * 8
    last_clear = -2
    for si in range(8):
        nx = cur_x + GET_BEST_DIR_DX[si]
        ny = cur_y + GET_BEST_DIR_DY[si]
        if nx == avoid_x and ny == avoid_y:
            continue
        if tile_can_be_moved_on(dgroup, inside, plane, nx, ny, cand_plane,
                                cand_x, cand_y, check_adjacent):
            mark[si] = 1
            last_clear = si

    if last_clear < 0:
        return -2

    def recompute(idx):
        nx = cur_x + GET_BEST_DIR_DX[idx]
        ny = cur_y + GET_BEST_DIR_DY[idx]
        dist = get_dis(nx, ny, tgt_x, tgt_y)
        if dist > best_dist:
            return idx
        out2[0] = (get_dir(cur_x, cur_y, tgt_x, tgt_y) - 1) & 0xFFFF
        out1[0] = 0
        return idx

    if out1[0] != 0:
        chosen1 = out2[0]
        chosen2 = out2[0]
        for _ in range(8):
            if _sx16(out1[0]) > 0:
                if mark[chosen1]:
                    return recompute(chosen1)
            else:
                if mark[chosen2]:
                    return recompute(chosen2)
            chosen1 = (chosen1 + 1) & 7
            chosen2 = (chosen2 - 1) & 7
        return -1

    fwd = out2[0]
    back = out2[0]
    for _ in range(8):
        if mark[fwd]:
            out2[0] = (get_dir(cur_x, cur_y, tgt_x, tgt_y) - 1) & 0xFFFF
            out1[0] = 1
            return fwd
        if mark[back]:
            out2[0] = (get_dir(cur_x, cur_y, tgt_x, tgt_y) - 1) & 0xFFFF
            out1[0] = 0xFFFF
            return back
        fwd = (fwd + 1) & 7
        back = (back - 1) & 7
    return -1


def check_my_best_dirs(dgroup, pack, out, inside: bool, plane: int, cur_x: int,
                       cur_y: int, tgt_x: int, tgt_y: int) -> int:
    """Walk `get_my_best_dirs` forward up to 64 steps toward the target,
    counting how many steps actually advance, and report the last direction
    taken.

    Recovered from `_CheckMyBestDirs` (SIMANTW.SYM seg6:8B40, args: one FAR
    pointer output, then plane, cur_x, cur_y, tgt_x, tgt_y; FAR return; a
    genuine caller of `_GetMyBestDirs` via the same NEAR-call-to-FAR-retf
    ABI bridge seen with `_TallyModePop` -> `_MakeRedInitiator`).  `out`
    models the far-pointer output cell as a 1-element list: `out[0]` is
    written (never read) with the final step count.

    Calls `get_my_best_dirs` once from (cur_x, cur_y); if that already fails
    (< 0), writes `out[0] = 0` and returns -1.  Otherwise walks the returned
    direction, then repeatedly calls `get_my_best_dirs` again from the new
    position — each success advances the position one more step; the loop
    always increments its step counter (even on the call that fails) before
    checking the failure and breaking, and separately stops once the counter
    reaches 0x40 (64) steps.  `out[0]` gets the final step count; the return
    value is the LAST direction's raw sign only: if the last `get_my_best_dirs`
    call succeeded (>= 0), the actual direction index is discarded and this
    returns exactly -1 (a plain "it worked" marker); only a failure (< 0)
    propagates its original value unchanged (so a caller CAN still tell -1
    "blocked" apart from -2 "nothing clear at all" on failure, but never
    learns the specific direction on success — only `out[0]`'s step count
    and the fact that `check_my_best_dirs` returns -1 either way when it did
    make progress).
    """
    si = get_my_best_dirs(dgroup, pack, inside, plane, cur_x, cur_y, tgt_x, tgt_y)
    step_count = 0
    if si >= 0:
        nx = cur_x + GET_BEST_DIR_DX[si]
        ny = cur_y + GET_BEST_DIR_DY[si]
        while step_count < 0x40:
            si = get_my_best_dirs(dgroup, pack, inside, plane, nx, ny, tgt_x, tgt_y)
            if si >= 0:
                nx += GET_BEST_DIR_DX[si]
                ny += GET_BEST_DIR_DY[si]
            step_count += 1
            if si < 0:
                break
    out[0] = step_count & 0xFFFF
    return si if si < 0 else -1


def get_red_best_dirs(dgroup, inside: bool, plane: int, cur_x: int, cur_y: int,
                      tgt_x: int, tgt_y: int) -> int:
    """The red-colony twin of `get_my_best_dirs`.

    Recovered from `_GetRedBestDirs` (SIMANTW.SYM seg6:9A18, args: plane,
    cur_x, cur_y, tgt_x, tgt_y; FAR return) — structurally identical to
    `get_my_best_dirs` (same 8-direction scan, same `tile_can_be_moved_on`
    gate, same occupied/clear split via a genuine `_GetLife`/`_IsClearTile`
    call pair), but simpler: it reads no PACK state at all.  Where
    `get_my_best_dirs` threads PACK-resident `cand_plane`/`cand_x`/`cand_y`/
    `check_adjacent` into `tile_can_be_moved_on`, this routine passes its
    OWN `plane`/`tgt_x`/`tgt_y` for the candidate site and hardcodes
    `check_adjacent` to False (confirmed the compass delta tables it reads
    via a different pair of DGROUP pointer-globals hold the exact same
    values as `GET_BEST_DIR_DX`/`GET_BEST_DIR_DY` by reading real memory).
    """
    best_dist = get_dis(cur_x, cur_y, tgt_x, tgt_y)
    if best_dist <= 0:
        return -1
    best_clear, best_any = -1, -2
    for si in range(8):
        nx = cur_x + GET_BEST_DIR_DX[si]
        ny = cur_y + GET_BEST_DIR_DY[si]
        if not tile_can_be_moved_on(dgroup, inside, plane, nx, ny, plane, tgt_x,
                                    tgt_y, False):
            continue
        dist = get_dis(nx, ny, tgt_x, tgt_y)
        if dist >= best_dist:
            continue
        best_dist = dist
        life_off = life_cell_offset(plane, nx, ny)
        raw_life = dgroup.rb(life_off) if life_off is not None else 0
        if raw_life > 0:                                  # occupied -> fallback
            best_any = si
        else:
            tile_off = map_cell_offset(plane, nx, ny)
            tile = dgroup.rb(tile_off) if tile_off is not None else 0
            if is_clear_tile(plane, tile, raw_life):
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


def exit_hole(dgroup, simant_data_group, pack, x: int, y: int, caste: int,
             field_c: int, field_e_hint: int) -> int:
    """Find a clear yard cell adjacent to (x, y) and append it to the A-list
    as a new record — an ant/object emerging from a nest hole.

    Recovered from `_ExitHole` (SIMANTW.SYM seg5:2DB6, args x=[bp+6],
    y=[bp+8], caste=[bp+0xa], field_c=[bp+0xc], field_e_hint=[bp+0xe]; FAR
    return).  Scans the 8 compass neighbours (the SAME direction-delta
    tables read LIVE from `simant_data_group[0+dir]/[8+dir]` as
    `_FixExitMapB`/`get_smell_t`), keeping the first one that is both
    `is_valid_a` and has a yard map tile `< 0x50` (unsigned).  Returns 0
    with no further effect if none qualifies.

    Otherwise appends a new A-list record at the found (x, y) — the SAME 5
    fields `add_ant_to_a_list` writes (`target0`/`target1`/`caste`/
    `field_c`/`field_e`, at `simant_data_group[0x23A4/0x278E/0x2F62/
    0x2B78/0x334C]`), but this routine is NOT a call to that one: it does
    NOT stamp the life grid, and `field_e` is computed rather than passed
    straight through — `field_e_hint` is used verbatim only when
    `field_c == 6`; `field_c` in `{3, 7}` forces `field_e = 0`; any other
    `field_c` picks 0 or 0x78 from a `caste` high-bit + original-x-vs-0x40
    comparison (byte-exact but the "why" isn't recovered here).

    Also diverges from `add_ant_to_a_list` in how it handles a FULL list
    (`pack[0x80F0] >= 0x3E8`): rather than silently refusing the append
    (already written into the just-past-cap slot by this point), it runs a
    single-pass mark-and-sweep compaction over the EXISTING 0..cap-1 slots
    (identical to `compact_list_a`'s hole-tracking convention) and only
    then re-derives the count from the shrunk total — the newly-written
    slot itself is never touched by that scan, so in the genuinely-full
    edge case the new entry can end up uncounted; ported byte-exact, not
    "fixed".  Returns 1 on success.
    """
    def sbyte(off):
        v = simant_data_group.rb(off)
        return v - 0x100 if v & 0x80 else v

    found = None
    for si in range(8):
        ny = y + sbyte(8 + si)
        nx = x + sbyte(si)
        if not is_valid_a(nx, ny):
            continue
        if dgroup.rb(MAP_PLANE_BASE[0] + (nx << 6) + ny) < 0x50:
            found = (nx, ny)
            break
    if found is None:
        return 0
    nx, ny = found

    if field_c == 6:
        new_field_e = field_e_hint
    elif field_c in (3, 7):
        new_field_e = 0
    elif not (caste & 0x80):
        new_field_e = 0x78 if x < 0x40 else 0
    else:
        new_field_e = 0x78 if x > 0x40 else 0

    count = pack.rw(0x80F0)
    simant_data_group.wb(0x23A4 + count, nx & 0xFF)
    simant_data_group.wb(0x278E + count, ny & 0xFF)
    simant_data_group.wb(0x2F62 + count, caste & 0xFF)
    simant_data_group.wb(0x2B78 + count, field_c & 0xFF)
    simant_data_group.wb(0x334C + count, new_field_e & 0xFF)

    if count < 0x3E8:
        pack.ww(0x80F0, (count + 1) & 0xFFFF)
    else:
        holes = 0
        si = 0
        cnt = pack.rw(0x80F0)
        while si < cnt:
            if simant_data_group.rb(0x2F62 + si) == 0:
                holes -= 1
            elif holes != 0:
                dst = si + holes
                for base in (0x2F62, 0x23A4, 0x278E, 0x2B78, 0x334C):
                    simant_data_group.wb(base + dst, simant_data_group.rb(base + si))
            si += 1
        cnt = (cnt + holes) & 0xFFFF
        pack.ww(0x80F0, cnt)
        if cnt < 0x3E8:
            pack.ww(0x80F0, (cnt + 1) & 0xFFFF)
    return 1


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


def _drown_list(pack, simant_data_group, count_off: int, x_off: int,
                caste_off: int, mark_off: int, x: int) -> None:
    """Shared body of drown_b_list/r_list: iterate the list BACKWARD (last
    slot to first), and for every alive ant (caste != 0) standing at X=`x`
    whose caste's bits [6:3] (extracted via `(caste & 0x78) >> 3`) fall in
    1..11 inclusive, mark it drowning (write 0x11 to its `mark_off` field).
    """
    count = pack.rw(count_off)
    for slot in range(count - 1, -1, -1):
        if simant_data_group.rb(x_off + slot) != x:
            continue
        caste = simant_data_group.rb(caste_off + slot)
        if caste == 0:
            continue
        sub = (caste & 0x78) >> 3
        if 1 <= sub <= 0x0B:
            simant_data_group.wb(mark_off + slot, 0x11)


def drown_b_list(pack, simant_data_group, x: int) -> None:
    """Mark black-colony ants standing at X=`x` as drowning (see `_drown_list`
    for the exact caste-subfield gate).  Uses the same per-slot arrays as
    `find_in_b_list`/`kill_tail_b` (0x392C x-field, 0x3D18 caste); the
    "drowning" mark is written to 0x3B22 (also written by
    `add_ant_to_b_list`/`set_ant_index`, meaning not yet given clearer
    semantics there).

    Recovered from `_DrownBList` (SIMANTW.SYM seg5:2D16, arg x=[bp+6]).
    """
    _drown_list(pack, simant_data_group, 0x99D4, 0x392C, 0x3D18, 0x3B22, x)


def drown_r_list(pack, simant_data_group, x: int) -> None:
    """The red-colony twin of `drown_b_list` (x-field 0x42FA, caste 0x46E6,
    mark 0x44F0).

    Recovered from `_DrownRList` (SIMANTW.SYM seg5:2D66, arg x=[bp+6]).
    """
    _drown_list(pack, simant_data_group, 0x72CC, 0x42FA, 0x46E6, 0x44F0, x)


def _fill_holes(simant_data_group, dgroup, hole_x_off: int, scent_base: int) -> None:
    """Shared body of fill_holes_bn/rn: refresh a colony's NEST home-scent
    beacon at each row's tracked hole position.  For each row `si` (0..63): if
    a hole X-position is tracked at that row (`simant_data_group[hole_x_off +
    si] != 0`), check whether the yard map cell at (hole_x, si) — plane 0,
    the OFFICIAL x=*64/y=+1 map convention — is still a hole tile (0x51); if
    the hole is STILL OPEN (tile == 0x51), CLEAR the colony-smell grid cell
    there (half-res: `((hole_x & 0xFFFE) << 4) + (si >> 1)`, on `scent_base`)
    to 0; if the hole has been FILLED IN (any other tile), JAM that scent cell
    to the maximum (0xFF).  (Byte-exact fixed after a state-diff regression
    caught the branches reversed in an earlier draft — see cont.82.)
    """
    for si in range(0x40):
        hole_x = simant_data_group.rb(hole_x_off + si)
        if hole_x == 0:
            continue
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (hole_x << 6) + si)
        idx = ((hole_x & 0xFFFE) << 4) + (si >> 1)
        simant_data_group.wb(scent_base + idx, 0 if tile == 0x51 else 0xFF)


def fill_holes_bn(simant_data_group, dgroup) -> None:
    """Refresh the black colony's NEST home-scent beacons at tracked hole
    positions (hole-X array at 0x82D2; scent grid at 0x62D2 — see
    `_fill_holes`).

    Recovered from `_FillHolesBN` (SIMANTW.SYM seg6:91DE, no args).
    """
    _fill_holes(simant_data_group, dgroup, 0x82D2, 0x62D2)


def fill_holes_rn(simant_data_group, dgroup) -> None:
    """The red-colony twin of `fill_holes_bn` (hole-X array at 0x8312; scent
    grid at 0x72D2).

    Recovered from `_FillHolesRN` (SIMANTW.SYM seg6:9244, no args).
    """
    _fill_holes(simant_data_group, dgroup, 0x8312, 0x72D2)


def make_red_initiator(dgroup, pack, simant_data_group) -> None:
    """Convert the last-found eligible yard ant (caste bit 0x80 set) into a
    red-colony initiator, if the black colony's hunger-decay reset-rate
    (`dgroup[0xAC82]`, see `dec_eat_b`) is at least 30 (0x1E) — a "well-fed
    enough" gate.

    Recovered from `_MakeRedInitiator` (SIMANTW.SYM seg6:967C, no args).
    Clears `simant_data_group[0x8A64]` (a success flag) unconditionally at the
    start.  If the gate passes, searches the yard ("A") list BACKWARD (same
    per-slot arrays as `find_in_a_list`) for the last ant whose caste
    (0x2F62) has bit 0x80 set; when found, overwrites that ant's caste to
    0xB0, field_c (0x2B78) to 0x13, field_e (0x334C) to 0, clears
    `pack[0x9D74]` (a "pending initiator" slot), and sets
    `simant_data_group[0x8A64] = 1` (success).  If the gate fails, the list
    is empty, or no candidate is found, the success flag is left at 0.
    """
    simant_data_group.wb(0x8A64, 0)
    if _sx16(dgroup.rw(0xAC82)) < 0x1E:
        return
    count = pack.rw(0x80F0)
    for slot in range(count - 1, -1, -1):
        if simant_data_group.rb(0x2F62 + slot) > 0x7F:
            simant_data_group.wb(0x2F62 + slot, 0xB0)
            simant_data_group.wb(0x2B78 + slot, 0x13)
            simant_data_group.wb(0x334C + slot, 0)
            pack.ww(0x9D74, 0)
            simant_data_group.wb(0x8A64, 1)
            return


def tally_mode_pop(pack, dgroup, simant_data_group) -> None:
    """Roll up specific mode-population buckets into an 11-field summary
    structure, then conditionally spawn a red-colony initiator.

    Recovered from `_TallyModePop` (SIMANTW.SYM seg6:038E, no args).  All
    fields are PACK-resident (the several DGROUP pointer-globals this routine
    reads all resolve to PACK).  Field mapping (sum-of-2 or copy-of-1):

        pack[0x9E70] = pack[0x786E] + pack[0x7870]
        pack[0x9E72] = pack[0x7872] + pack[0x7874]
        pack[0x9E74] = pack[0x786C]
        pack[0x9E76] = pack[0x7878]
        pack[0x9E78] = pack[0x7882]
        pack[0x9E7A] = pack[0x7876]
        pack[0xA084] = pack[0x7BE8] + pack[0x7BEA]
        pack[0xA086] = pack[0x7BEC] + pack[0x7BEE]
        pack[0xA088] = pack[0x7BE6]
        pack[0xA08A] = pack[0x7BF2]
        pack[0xA08C] = pack[0x7BFC]
        pack[0xA08E] = pack[0x7BF0]

    Then, if `pack[0x7C0A]` (signed) is < 1, calls `make_red_initiator` (a
    NEAR call in the ASM with a manually pushed CS, matching that callee's
    far-return ABI — confirmed by tracing the raw relative-call bytes, not
    assumed).
    """
    def add(dst, a, b):
        pack.ww(dst, (pack.rw(a) + pack.rw(b)) & 0xFFFF)

    def copy(dst, src):
        pack.ww(dst, pack.rw(src))

    add(0x9E70, 0x786E, 0x7870)
    add(0x9E72, 0x7872, 0x7874)
    copy(0x9E74, 0x786C)
    copy(0x9E76, 0x7878)
    copy(0x9E78, 0x7882)
    copy(0x9E7A, 0x7876)
    add(0xA084, 0x7BE8, 0x7BEA)
    add(0xA086, 0x7BEC, 0x7BEE)
    copy(0xA088, 0x7BE6)
    copy(0xA08A, 0x7BF2)
    copy(0xA08C, 0x7BFC)
    copy(0xA08E, 0x7BF0)

    if _sx16(pack.rw(0x7C0A)) < 1:
        make_red_initiator(dgroup, pack, simant_data_group)


def clr_mode_pop(pack) -> None:
    """Reset the two 20-word "mode population" count arrays to all zero
    (`pack[0x7BE4..+0x28)` and `pack[0x786A..+0x28)`), then decrement two
    unrelated scalar counters (`pack[0x7C44]`, `pack[0x8078]`) by 1 each,
    floored at 0.

    Recovered from `_ClrModePop` (SIMANTW.SYM seg6:034A, no args).  Its
    neighbor `_TallyModePop` (not yet recovered) presumably fills these arrays
    each tick by counting ants per behavior mode; this clears them for the
    next tick's tally.
    """
    for i in range(0x14):
        pack.ww(0x7BE4 + 2 * i, 0)
        pack.ww(0x786A + 2 * i, 0)
    if pack.rw(0x7C44) != 0:
        pack.ww(0x7C44, pack.rw(0x7C44) - 1)
    if pack.rw(0x8078) != 0:
        pack.ww(0x8078, pack.rw(0x8078) - 1)


def clear_list_b(pack) -> None:
    """Empty the black colony's ant list (just resets the count to 0 — the
    per-slot arrays are left as-is, matching `compact_list_b`'s "the count is
    the source of truth" convention).

    Recovered from `_ClearListB` (SIMANTW.SYM seg5:30E8, no args).
    """
    pack.ww(0x99D4, 0)


def clear_list_r(pack) -> None:
    """The red-colony twin of `clear_list_b` (count 0x72CC).

    Recovered from `_ClearListR` (SIMANTW.SYM seg5:30F4, no args).
    """
    pack.ww(0x72CC, 0)


def kill_spider(pack) -> None:
    """Reset the spider's state: mode 5 (presumably "dead"/"inactive" — the
    same numbering `_DoNestAntB`'s mode dispatch family likely shares, not yet
    cross-checked), health/timer reset to 500 (0x1F4), and a third field
    (0x7290) cleared to 0.

    Recovered from `_KillSpider` (SIMANTW.SYM seg5:53D4, no args).  All three
    fields live in PACK (0x729E mode, 0x72E0 health/timer, 0x7290 unconfirmed).
    """
    pack.ww(0x729E, 5)
    pack.ww(0x72E0, 0x1F4)
    pack.ww(0x7290, 0)


def _compact_list(pack, simant_data_group, count_off: int, caste_off: int,
                  f0: int, f1: int, fc: int, fe: int) -> None:
    """Shared body of compact_list_[abr]: sweep the list, removing every
    entry whose caste field is 0 (dead/empty) by shifting subsequent entries
    into the gaps in one stable pass, using a running (<=0) hole counter; then
    subtract the total hole count from the list's count.  Does NOT touch the
    life grid — the caller is expected to have already cleared it when marking
    an entry dead (unlike `remove_from_a_list`, which clears it itself).
    """
    count = pack.rw(count_off)
    holes = 0
    slot = 0
    while slot < count:
        if simant_data_group.rb(caste_off + slot) == 0:
            holes -= 1
        elif holes != 0:
            dst = slot + holes
            for base in (caste_off, f0, f1, fc, fe):
                simant_data_group.wb(base + dst, simant_data_group.rb(base + slot))
        slot += 1
    pack.ww(count_off, (count + holes) & 0xFFFF)


def compact_list_a(pack, simant_data_group) -> None:
    """Sweep the yard ("A") ant list, closing every dead-entry (caste==0) gap.

    Recovered from `_CompactListA` (SIMANTW.SYM seg5:2A16, no args).  Uses the
    same per-slot fields as `find_in_a_list`/`add_ant_to_a_list` (count
    0x80F0; caste 0x2F62; fields 0x23A4/0x278E/0x2B78/0x334C).
    """
    _compact_list(pack, simant_data_group, 0x80F0, 0x2F62, 0x23A4, 0x278E,
                 0x2B78, 0x334C)


def compact_list_b(pack, simant_data_group) -> None:
    """The black-colony twin of `compact_list_a` (count 0x99D4; caste 0x3D18;
    fields 0x3736/0x392C/0x3B22/0x3F0E).

    Recovered from `_CompactListB` (SIMANTW.SYM seg5:2A7A, no args).
    """
    _compact_list(pack, simant_data_group, 0x99D4, 0x3D18, 0x3736, 0x392C,
                 0x3B22, 0x3F0E)


def compact_list_r(pack, simant_data_group) -> None:
    """The red-colony twin of `compact_list_a` (count 0x72CC; caste 0x46E6;
    fields 0x4104/0x42FA/0x44F0/0x48DC).

    Recovered from `_CompactListR` (SIMANTW.SYM seg5:2ADE, no args).
    """
    _compact_list(pack, simant_data_group, 0x72CC, 0x46E6, 0x4104, 0x42FA,
                 0x44F0, 0x48DC)


def remove_from_a_list(pack, simant_data_group, dgroup, slot: int) -> None:
    """Remove the yard ("A") ant at `slot`, closing the gap.

    Recovered from `_RemoveFromAList` (SIMANTW.SYM seg5:2B42, arg slot=[bp+6]).
    Uses the same per-slot fields as `find_in_a_list`/`add_ant_to_a_list`
    (0x23A4/0x278E/0x2B78/0x2F62/0x334C).  First clears the removed ant's
    life-grid cell (plane 0, at its recorded target0/target1 — the SAME x/y
    roles `add_ant_to_a_list` uses), then decrements the list count
    (`pack[0x80F0]`, floored at 0), then shifts every field array's tail
    (indices `slot+1 .. old_count-1`) down by one position via a byte-exact
    copy (the ASM calls a shared far-memcpy helper; observably identical to a
    plain byte-by-byte shift regardless of its word/byte-pair optimization).
    `slot` must be a valid existing index (0 <= slot < count) — unlike
    `set_ant_index`, this routine does NOT bounds-check; the caller is trusted.
    """
    target1 = simant_data_group.rb(0x278E + slot)
    target0 = simant_data_group.rb(0x23A4 + slot) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[0] + target1 + (target0 << 6), 0)
    count = pack.rw(0x80F0)
    if count > 0:
        count -= 1
        pack.ww(0x80F0, count)
    n = count - slot
    if n > 0:
        for base in (0x23A4, 0x278E, 0x2B78, 0x2F62, 0x334C):
            tail = bytes(simant_data_group.rb(base + slot + 1 + i) for i in range(n))
            for i, b in enumerate(tail):
                simant_data_group.wb(base + slot + i, b)


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


def _fix_exit_map(dgroup, simant_data_group, map_base: int, exit_base: int,
                  x: int, y: int) -> None:
    """Shared body of `fix_exit_map_b`/`fix_exit_map_r`."""
    def sbyte(off):
        v = simant_data_group.rb(off)
        return v - 0x100 if v & 0x80 else v

    idx = (x << 6) + y
    if y < 2:
        tile = dgroup.rb(map_base + idx)
        simant_data_group.wb(exit_base + idx, 0xFF if tile == 0x18 else 0xFE)
        return

    best = 0
    for si in range(8):
        nx = x + sbyte(si)
        ny = y + sbyte(8 + si)
        if not (0 <= nx <= 0x3F and 0 <= ny <= 0x3F):
            continue
        v = simant_data_group.rb(exit_base + (nx << 6) + ny)
        if v > best:
            best = v
    simant_data_group.wb(exit_base + idx, (best - 1) & 0xFF if best else 0)


def fix_exit_map_b(dgroup, simant_data_group, x: int, y: int) -> None:
    """Refresh the black colony's "distance from the nest exit" map cell
    (x, y), used to steer digging ants back toward the surface.

    Recovered from `_FixExitMapB` (SIMANTW.SYM seg5:284E, args x=[bp+6],
    y=[bp+8]).  Rows 0-1 (right at the exit) are special-cased against the
    black nest map (`_GetMap` plane 2): tile `0x18` (the exit tile itself)
    marks the cell `0xFF`, anything else `0xFE` — sentinels, not real
    distances.  Every other row instead scans the 8 compass neighbours (the
    same direction-delta tables `get_smell_t` already reads LIVE from
    `simant_data_group[0+dir]/[8+dir]`, not hardcoded) and takes the
    HIGHEST existing exit-map value among the in-bounds ones; the cell
    becomes that max minus 1 (or 0 if every neighbour was still 0) — a
    flood-fill-by-one-step-per-call "distance from exit" gradient, seeded
    by the exit-tile sentinels above.  The exit-map array lives in
    SIMANT_DATA_GROUP at `[0x3A4..)`.
    """
    _fix_exit_map(dgroup, simant_data_group, MAP_PLANE_BASE[2], 0x3A4, x, y)


def fix_exit_map_r(dgroup, simant_data_group, x: int, y: int) -> None:
    """The red-colony twin of `fix_exit_map_b` (map plane 3, exit-map array
    at SIMANT_DATA_GROUP `[0x13A4..)`).

    Recovered from `_FixExitMapR` (SIMANTW.SYM seg5:2914, args x=[bp+6],
    y=[bp+8]).
    """
    _fix_exit_map(dgroup, simant_data_group, MAP_PLANE_BASE[3], 0x13A4, x, y)


def _get_exit_dir(dgroup, simant_data_group, map_base: int, exit_map_base: int,
                  x: int, y: int, exclude: int) -> int:
    """Shared body of `get_exit_dir_b`/`r`: pick a compass direction (1-8,
    `0` for none found) heading toward higher exit-distance, biased away
    from `exclude`'s opposite.

    At `y == 1` (right at the tunnel row): a fast path — if the tile at
    `(x, 0)` on the colony's nest map is exactly `0x18` (an open exit
    marker), returns `1` outright; otherwise a coin-flip (`_SRand2`) picks
    between `3` and `7` without consulting the exit-distance map at all.

    Otherwise: scans the 8 compass neighbors of `(x, y)` for the highest
    exit-distance value (`exit_map_base + (nx << 6) + ny`, in-bounds only),
    skipping the direction opposite `exclude` (`exclude ^ 4`) and ties
    (strictly `<=` current best never updates) — returns the winning
    neighbor's 1-based compass index, or `0` if none ever beat `0`.
    """
    if y == 1:
        if dgroup.rb(map_base + (x << 6)) == 0x18:
            return 1
        from .simone import SRAND_SEED_OFF, srand_pow2
        seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 1)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return (roll << 2) + 3

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    gate = exclude ^ 4
    best_dir = 0
    best_val = 0
    for i in range(8):
        nx = x + sx8(simant_data_group.rb(i))
        ny = y + sx8(simant_data_group.rb(8 + i))
        if not (0 <= nx <= 0x3F and 0 <= ny <= 0x3F):
            continue
        val = simant_data_group.rb(exit_map_base + (nx << 6) + ny)
        if val <= best_val:
            continue
        if gate == i:
            continue
        best_val = val
        best_dir = i + 1
    return best_dir


def get_exit_dir_b(dgroup, simant_data_group, x: int, y: int, exclude: int) -> int:
    """Recovered from `_GetExitDirB` (SIMANTW.SYM seg5:119C, args x=[bp+6],
    y=[bp+8], exclude=[bp+10]; FAR return). See `_get_exit_dir`.
    """
    return _get_exit_dir(dgroup, simant_data_group, MAP_PLANE_BASE[2], 0x3A4,
                         x, y, exclude)


def get_exit_dir_r(dgroup, simant_data_group, x: int, y: int, exclude: int) -> int:
    """The red-colony twin of `get_exit_dir_b`.

    Recovered from `_GetExitDirR` (SIMANTW.SYM seg5:1240, args x=[bp+6],
    y=[bp+8], exclude=[bp+10]; FAR return).
    """
    return _get_exit_dir(dgroup, simant_data_group, MAP_PLANE_BASE[3], 0x13A4,
                         x, y, exclude)


def _get_enter_dir(dgroup, simant_data_group, exit_map_base: int, x: int,
                   y: int, exclude: int) -> int:
    """Shared body of `get_enter_dir_b`/`r`: pick a compass direction (0-7,
    `-1` for none found) heading toward LOWER exit-distance — deeper into
    the nest, away from any exit.

    Starts `best_val` at the ant's OWN cell's exit-distance, then scans the
    8 compass neighbors (skipping the direction opposite `exclude`,
    `exclude ^ 4`, and any neighbor whose exit-distance is exactly `0` —
    unlike `_get_exit_dir`, matching `0` is never a valid target here).
    A neighbor with a STRICTLY lower exit-distance than `best_val` always
    wins; a tie is broken by a coin-flip (`_SRand2`); a strictly higher
    value never wins. `best_val` updates on every win, so later neighbors
    compete against the running best, not just the ant's starting cell.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    gate = exclude ^ 4
    best_dir = -1
    best_val = simant_data_group.rb(exit_map_base + (x << 6) + y)

    for i in range(8):
        if gate == i:
            continue
        nx = x + sx8(simant_data_group.rb(i))
        ny = y + sx8(simant_data_group.rb(8 + i))
        if not (0 <= nx <= 0x3F and 0 <= ny <= 0x3F):
            continue
        neighbor_val = simant_data_group.rb(exit_map_base + (nx << 6) + ny)
        if neighbor_val == 0:
            continue
        if best_val < neighbor_val:
            continue
        if best_val == neighbor_val:
            seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 1)
            dgroup.ww(SRAND_SEED_OFF, seed)
            if roll == 0:
                continue
        best_val = neighbor_val
        best_dir = i
    return best_dir


def get_enter_dir_b(dgroup, simant_data_group, x: int, y: int, exclude: int) -> int:
    """Recovered from `_GetEnterDirB` (SIMANTW.SYM seg5:12E4, args x=[bp+6],
    y=[bp+8], exclude=[bp+10]; FAR return). See `_get_enter_dir`.
    """
    return _get_enter_dir(dgroup, simant_data_group, 0x3A4, x, y, exclude)


def get_enter_dir_r(dgroup, simant_data_group, x: int, y: int, exclude: int) -> int:
    """The red-colony twin of `get_enter_dir_b`.

    Recovered from `_GetEnterDirR` (SIMANTW.SYM seg5:137C, args x=[bp+6],
    y=[bp+8], exclude=[bp+10]; FAR return).
    """
    return _get_enter_dir(dgroup, simant_data_group, 0x13A4, x, y, exclude)


def pickup_food_a(dgroup, pack, x: int, y: int) -> None:
    """An ant picking up food from the YARD tile map at `(x, y)` — a
    genuine `_DoForageAnt` dependency.

    Recovered from `_PickupFoodA` (SIMANTW.SYM seg5:0D18, FAR return, args
    x=[bp+6], y=[bp+8]).

    Behavior is gated on `pack[0x9B6E]` (the SAME "inside the nest" flag
    `_DeadAntHere` reads) — two ENTIRELY different tile transforms depending
    on it, not just a colony split like every other food routine this
    session:

    - Flag CLEAR (outside): tile `0x48` (72, a specific yard food-pile
      marker) rerolls fresh via `_SRand16`; any other tile just decrements
      by one (byte-wrapping, no underflow guard, same as `_steal_food`).
    - Flag SET (inside): a tile that's an exact multiple of 4 is REPLACED
      with `(tile - 0x18) >> 2` (a shrinking transform, not a decrement —
      no RNG involved); any other tile falls back to the SAME plain
      decrement as the flag-clear case.

    Either way, finally decrements `pack[0x9E84]` (a food-count-ish stat,
    distinct from every other food routine's counter) while it's still
    positive — the same "floor at exactly 0" guard `_steal_food`/
    `_eat_food` use.
    """
    idx = MAP_PLANE_BASE[0] + (x << 6) + y
    tile = dgroup.rb(idx)

    if pack.rw(0x9B6E) != 0:
        if tile % 4 == 0:
            dgroup.wb(idx, ((tile - 0x18) >> 2) & 0xFF)
        else:
            dgroup.wb(idx, (tile - 1) & 0xFF)
    else:
        if tile == 0x48:
            from .simone import SRAND_SEED_OFF, srand_pow2
            seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 15)
            dgroup.ww(SRAND_SEED_OFF, seed)
            dgroup.wb(idx, roll)
        else:
            dgroup.wb(idx, (tile - 1) & 0xFF)

    if _sx16(pack.rw(0x9E84)) > 0:
        pack.ww(0x9E84, (pack.rw(0x9E84) - 1) & 0xFFFF)


def _smooth_edges(dgroup, map_base: int, x: int, y: int) -> None:
    """Shared body of `smooth_edges_b`/`smooth_edges_r`."""
    from .simone import SRAND_SEED_OFF, srand_pow2

    if not (0 <= x <= 0x3F and 0 <= y <= 0x3F):
        return
    idx = (x << 6) + y

    if y == 0:
        tile = dgroup.rb(map_base + idx)
        if tile < 0x30:
            dgroup.wb(map_base + idx, 0x18)
        return

    tile = dgroup.rb(map_base + idx)
    if not (0x20 <= tile <= 0x2F or tile >= 0x4F):
        return
    center_class = 0 if tile <= 0x2F else 0x2F

    def dirt(delta):
        v = dgroup.rb(map_base + idx + delta)
        return 1 if (0x20 <= v <= 0x2F or v >= 0x4F) else 0

    bits = 1 if (y < 2 or dirt(-1)) else 0            # north (y-1)
    bits |= 2 if (x > 0x3E or dirt(0x40)) else 0       # east  (x+1)
    bits |= 4 if (y > 0x3E or dirt(1)) else 0          # south (y+1)
    bits |= 8 if (x < 1 or dirt(-0x40)) else 0         # west  (x-1)

    if bits:
        dgroup.wb(map_base + idx, (bits + center_class + 0x1F) & 0xFF)
        return

    if center_class == 0:
        seed, val = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        dgroup.wb(map_base + idx, val & 0xFF)
    else:
        dgroup.wb(map_base + idx, 0x4E)


def smooth_edges_b(dgroup, x: int, y: int) -> None:
    """Round off a black-colony dirt tile's exposed edges after a dig.

    Recovered from `_SmoothEdgesB` (SIMANTW.SYM seg5:255A, args x=[bp+6],
    y=[bp+8]).  Row 0 is special-cased: a tile < 0x30 there is forced to
    0x18 (the exit marker `_FixExitMapB` also uses); >= 0x30 is a no-op.
    Every other row only
    acts on "dirt-like" tiles (0x20..0x2F, or >=0x4F — the same
    classification used inline four times below, matching the separately-
    named leaf `_RIsItDirt` (seg5:26C4) byte-for-byte though this routine
    never calls it, always inlining instead).  It builds a 4-bit bitmask of
    which orthogonal neighbours are ALSO dirt-like (bit 1=north, 2=east,
    4=south, 8=west; a neighbour off the 64x64 grid always counts as
    "dirt"), and:

    - any bit set: writes `bits + (0 or 0x2F, depending on whether the
      centre tile was the 0x20-0x2F band or the >=0x4F band) + 0x1F` — a
      classic 4-bit auto-tile edge/corner variant selector.
    - no bits set (fully surrounded by non-dirt) and the centre was the
      0x20-0x2F band: rerolls to a random 0..7 via `_SRand8` (advancing the
      shared LFSR seed at `dgroup[SRAND_SEED_OFF]`).
    - no bits set and the centre was the >=0x4F band: writes the literal
      0x4E.
    """
    _smooth_edges(dgroup, MAP_PLANE_BASE[2], x, y)


def smooth_edges_r(dgroup, x: int, y: int) -> None:
    """The red-colony twin of `smooth_edges_b` (map plane 3).

    Recovered from `_SmoothEdgesR` (SIMANTW.SYM seg5:26E4, args x=[bp+6],
    y=[bp+8]).
    """
    _smooth_edges(dgroup, MAP_PLANE_BASE[3], x, y)


def _acc_add32(pack, lo_off: int, hi_off: int, delta: int) -> int:
    """Add a sign-extended 16-bit `delta` onto a 32-bit PACK accumulator
    (`add`/`adc` on the two words), write it back, and return the raw
    32-bit (unsigned-word-pair) total for immediate reuse (e.g. as
    `a_f_ldiv`'s dividend, which sign-extends its own inputs)."""
    total = (pack.rw(lo_off) | (pack.rw(hi_off) << 16))
    total = (total + _sx16(delta)) & 0xFFFFFFFF
    pack.ww(lo_off, total & 0xFFFF)
    pack.ww(hi_off, (total >> 16) & 0xFFFF)
    return total


def _dig_tile_reroll_and_track(dgroup, pack, map_base: int, xsum_off: int,
                               ysum_off: int, count_off: int, avgx_off: int,
                               avgy_off: int, x: int, y: int) -> bool:
    """Shared reroll + running-average-position bookkeeping `_DigTileB`/
    `_DigTileR` both do per colony (and `_DigTileB` does a SECOND time, for
    the red colony, on its rare tunnel-through branch): if the map tile at
    (x, y) is dirt, reroll it to a random 0..7 (`_SRand8`), accumulate x/y
    into a 32-bit running sum pair, bump a dig counter, and — once the
    counter is positive — recompute the running average dig position via
    two genuine `__aFldiv` calls (sum / count).  Returns whether the tile
    was dirt (both callers still do their post-dig smoothing either way;
    this is purely for `_DigTileB`'s "was the black tile dirt" gate around
    its own y>0x35 red-tunnel roll).
    """
    from .simone import SRAND_SEED_OFF, srand_pow2
    from .crt_math import a_f_ldiv

    idx = (x << 6) + y
    tile = dgroup.rb(map_base + idx)
    if not is_it_dirt(tile):
        return False

    seed, val = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    dgroup.wb(map_base + idx, val & 0xFF)

    xsum = _acc_add32(pack, xsum_off, xsum_off + 2, x)
    ysum = _acc_add32(pack, ysum_off, ysum_off + 2, y)
    count = (pack.rw(count_off) + 1) & 0xFFFF
    pack.ww(count_off, count)
    if _sx16(count) > 0:
        pack.ww(avgx_off, a_f_ldiv(xsum, _sx16(count)) & 0xFFFF)
        pack.ww(avgy_off, a_f_ldiv(ysum, _sx16(count)) & 0xFFFF)
    return True


def dig_tile_b(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """Dig one black-colony nest tile: reroll it if it's dirt, track a
    running average dig position, occasionally punch through into the red
    colony's map too, then refresh the smoothing/exit-distance state around
    the cell on both colonies' maps.

    Recovered from `_DigTileB` (SIMANTW.SYM seg5:1FE4, args x=[bp+6],
    y=[bp+8]; FAR return).  All of its callees were only just recovered
    this session (`__aFldiv`, `_FixExitMapB/R`, `_SmoothEdgesB/R`) or
    earlier (`_IsItDirt`, `_SRand1`, `_SRand8`) — this is the first routine
    ported specifically because a prior slice's recoveries unblocked it.

    - Rerolls/tracks the black tile via `_dig_tile_reroll_and_track`
      (running sums at `pack[0x8104:0x8108]`/`[0x811A:0x811E]`, counter
      `pack[0x72C8]`, averages at `pack[0x7C48]`/`[0x7C90]`); the black
      tile being dirt is also the gate for the next step.
    - If the black tile WAS dirt AND `y > 0x35` (near the yard-facing end
      of the nest) AND a `_SRand1(0x40)` roll comes up exactly 0 (a
      1-in-64 chance): marks the black tile `0x14` (a tunnel-through
      marker) and repeats the SAME reroll/track dance for the red
      colony's map at the identical (x, y) (separate PACK fields at
      `[0x9DDC..)`/`[0x9DE2..)`/`[0x7A56]`/`[0x9FBA]`/`[0x9FD2]`), then
      smooths the 4 red-map neighbours and the red exit-map at (x, y),
      and marks the red tile `0x14` too.
    - Always (regardless of every branch above — even a no-op "tile wasn't
      dirt" call still does this) smooths the 4 black-map neighbours and
      refreshes the black exit-map at (x, y).
    """
    was_dirt = _dig_tile_reroll_and_track(
        dgroup, pack, MAP_PLANE_BASE[2], 0x8104, 0x811A, 0x72C8, 0x7C48,
        0x7C90, x, y)

    if was_dirt:
        from .simone import SRAND_SEED_OFF, srand1

        dig_red = False
        if y > 0x35:
            seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 0x40)
            dgroup.ww(SRAND_SEED_OFF, seed)
            dig_red = roll == 0

        if dig_red:
            idx = (x << 6) + y
            dgroup.wb(MAP_PLANE_BASE[2] + idx, 0x14)
            _dig_tile_reroll_and_track(
                dgroup, pack, MAP_PLANE_BASE[3], 0x9DDC, 0x9DE2, 0x7A56,
                0x9FBA, 0x9FD2, x, y)
            smooth_edges_r(dgroup, x, y - 1)
            smooth_edges_r(dgroup, x + 1, y)
            smooth_edges_r(dgroup, x, y + 1)
            smooth_edges_r(dgroup, x - 1, y)
            fix_exit_map_r(dgroup, simant_data_group, x, y)
            dgroup.wb(MAP_PLANE_BASE[3] + idx, 0x14)

    smooth_edges_b(dgroup, x, y - 1)
    smooth_edges_b(dgroup, x + 1, y)
    smooth_edges_b(dgroup, x, y + 1)
    smooth_edges_b(dgroup, x - 1, y)
    fix_exit_map_b(dgroup, simant_data_group, x, y)


def dig_tile_r(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """Dig one red-colony nest tile — the simpler, no-tunnel-through
    sibling of `dig_tile_b`'s red-colony branch (this is what a red ant's
    OWN dig calls, versus the rare cross-colony punch-through `_DigTileB`
    occasionally triggers).

    Recovered from `_DigTileR` (SIMANTW.SYM seg5:21DE, args x=[bp+6],
    y=[bp+8]; FAR return).  Rerolls/tracks the red tile via the SAME shared
    `_dig_tile_reroll_and_track` helper and the SAME PACK fields
    `_DigTileB`'s red branch uses (`[0x9DDC..)`/`[0x9DE2..)`/`[0x7A56]`/
    `[0x9FBA]`/`[0x9FD2]`), then always smooths the 4 red-map neighbours
    and refreshes the red exit-map at (x, y) — no y-threshold gate, no
    RNG roll, no black-side interaction at all.
    """
    _dig_tile_reroll_and_track(
        dgroup, pack, MAP_PLANE_BASE[3], 0x9DDC, 0x9DE2, 0x7A56, 0x9FBA,
        0x9FD2, x, y)

    smooth_edges_r(dgroup, x, y - 1)
    smooth_edges_r(dgroup, x + 1, y)
    smooth_edges_r(dgroup, x, y + 1)
    smooth_edges_r(dgroup, x - 1, y)
    fix_exit_map_r(dgroup, simant_data_group, x, y)


HOLE_EDGE_TILES = (0x19, 0x1A, 0x1C, 0x1F, 0x1E, 0x1D, 0x1B, 0x18)  # dgroup[0x230C..)


def _clear_3x3(dgroup, plane: int, x: int, y: int) -> int:
    """Read the real map+life state for `is_clear_3x3`'s 9 cells (centre
    then its 8 compass neighbours) and evaluate it — the VM-touching
    counterpart of the already-pure `is_clear_3x3(cells_clear)`.  A cell
    off the grid counts as "not clear" (confirmed by the existing
    `_IsClear3x3` island test's corner cases)."""
    cells = [(x, y)] + [(x + dx, y + dy) for dx, dy in zip(CLEAR_3X3_DX, CLEAR_3X3_DY)]
    results = []
    for cx, cy in cells:
        moff = map_cell_offset(plane, cx, cy)
        loff = life_cell_offset(plane, cx, cy)
        if moff is None or loff is None:
            results.append(0)
            continue
        results.append(is_clear_tile(plane, dgroup.rb(moff), dgroup.rb(loff)))
    return is_clear_3x3(results)


def make_new_hole_b(dgroup, simant_data_group, pack, col: int) -> None:
    """Search for a new above-ground exit-hole position near yard column
    `col`, mark it, carve its 8-neighbour edge pattern, record it for
    `_FillHolesBN`, and trigger the connecting nest dig.

    Recovered from `_MakeNewHoleB` (SIMANTW.SYM seg5:1B06, arg: col;
    FAR return).  `col` plays two different coordinate roles in different
    parts of this routine (confirmed by tracing each address formula
    independently, not assumed): it is the fixed "row" in the yard-map
    search below, but also the value written into `_FillHolesBN`'s
    per-row hole-tracking array and the "x" argument of the final
    `dig_tile_b` call — ported byte-exact under both roles rather than
    forced into one consistent name.

    Rolls a `_SRand1(31)` starting offset once, then tries up to 34
    candidate positions `row = ((roll + i) % 32) + 2` for `i` in `0..33`:

    - PACK's `[0x9B6E]` "inside" flag SET: reads the yard map tile at
      `(row, col)` and classifies it into a priority/marker byte (0 means
      "not usable" — the search keeps going): tile `0` -> `0x86`; tile `2`
      or `3` -> `0x8A`; tile `0x5E..0x61` -> `tile + 0x22`; tile `0x66` ->
      `0x85`; tile `0x68` -> `0x84`; anything else -> not usable.  The
      first usable `row` wins; its marker byte is written to the yard map
      at `(row, col)`, and `(row, col)` is recorded into SIMANT_DATA_GROUP
      scratch fields `[0x8352]`/`[0x835A]`/`[0x835C]` (a "last placed hole"
      record whose fields' individual roles aren't further disambiguated
      here) with `[0x8354]` cleared.
    - "inside" flag CLEAR: instead calls `_IsClear3x3` for real (plane 1,
      centre `(row, col)`) and takes the first `row` where the whole 3x3
      block is clear; on success writes the literal tile `0x50` (the
      canonical hole marker) at `(row, col)` and the same SDG scratch
      fields (`[0x835A]`=row, `[0x835C]`=col this time, `[0x8352]`=col).

    Either way, on success: carves the 8-neighbour edge pattern
    (`HOLE_EDGE_TILES`, one fixed tile per compass direction) into any
    in-bounds yard-map neighbour whose current tile is `< 0x50`, records
    `row` into `_FillHolesBN`'s per-row hole-tracking array at
    `simant_data_group[0x82D2 + col]`, and calls `dig_tile_b(col, 1)` to
    dig the connecting nest tunnel segment.  Exhausting all candidates
    without success is a silent no-op.
    """
    from .simone import SRAND_SEED_OFF, srand1

    seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 0x1F)
    dgroup.ww(SRAND_SEED_OFF, seed)

    inside = pack.rw(0x9B6E) != 0
    found_row = None
    marker = None

    if inside:
        for i in range(0x22):
            row = ((roll + i) % 0x20) + 2
            tile = dgroup.rb(MAP_PLANE_BASE[0] + (row << 6) + col)
            if tile == 0:
                m = 0x86
            elif tile in (2, 3):
                m = 0x8A
            elif 0x5E <= tile <= 0x61:
                m = (tile + 0x22) & 0xFF          # ASM's `lea dx,[si+34]` -- decimal 34
            elif tile == 0x66:
                m = 0x85
            elif tile == 0x68:
                m = 0x84
            else:
                m = 0
            if m:
                found_row, marker = row, m
                break
        if found_row is not None:
            dgroup.wb(MAP_PLANE_BASE[0] + (found_row << 6) + col, marker & 0xFF)
            simant_data_group.ww(0x835A, found_row)
            simant_data_group.ww(0x835C, col)
            simant_data_group.ww(0x8352, col)
            simant_data_group.ww(0x8354, 0)
    else:
        for i in range(0x22):
            row = ((roll + i) % 0x20) + 2
            if _clear_3x3(dgroup, 1, row, col):
                found_row = row
                break
        if found_row is None:
            return
        dgroup.wb(MAP_PLANE_BASE[0] + (found_row << 6) + col, 0x50)
        simant_data_group.ww(0x835A, found_row)
        simant_data_group.ww(0x835C, col)
        simant_data_group.ww(0x8352, col)
        simant_data_group.ww(0x8354, 0)

        # The 8-neighbour edge carve is reached ONLY on this "not inside"
        # success path (the ASM's "inside" success path jumps straight past
        # it) -- confirmed empirically, not assumed, after an initial port
        # ran it unconditionally and a state-diff test caught the divergence.
        def sbyte(off):
            v = simant_data_group.rb(off)
            return v - 0x100 if v & 0x80 else v

        for di in range(8):
            ny = sbyte(8 + di) + col
            nx = sbyte(0 + di) + found_row
            if not (0 <= nx <= 0x7F and 0 <= ny <= 0x3F):
                continue
            off = MAP_PLANE_BASE[0] + (nx << 6) + ny
            if dgroup.rb(off) < 0x50:
                dgroup.wb(off, HOLE_EDGE_TILES[di])

    if found_row is None:
        return
    simant_data_group.wb(0x82D2 + col, found_row & 0xFF)
    dig_tile_b(dgroup, simant_data_group, pack, col, 1)


def make_new_hole_r(dgroup, simant_data_group, pack, col: int) -> None:
    """The red-colony twin of `make_new_hole_b` — same search/carve
    machinery over the SAME shared yard map, but a genuinely different
    (and more elaborate) closing step.

    Recovered from `_MakeNewHoleR` (SIMANTW.SYM seg5:1D02, arg: col; FAR
    return).  Confirmed via disassembly, not assumed by symmetry with
    `make_new_hole_b`: the candidate search, classification, marker
    values (same `_MakeNewHoleB` decimal/hex fix applies: `tile + 0x22`,
    not `0x34`), and the 8-neighbour edge carve ALL operate on
    `MAP_PLANE_BASE[0]` (the shared yard map) exactly like the black
    twin — the "R" in the name is about which nest the closing step
    tunnels into, not which map the search happens on.  Only the
    candidate row FORMULA differs: `row = 0x7E - ((roll + i) % 0x20)`
    (searching down from 126, vs. black's `+ 2` searching up from 2) —
    and the SDG scratch fields are R's own (`[0x835E]`/`[0x8360]`/
    `[0x8356]`/`[0x8358]`) with the hole-tracking write going to
    `_FillHolesRN`'s array at `[0x8312 + col]`.

    The closing step is where R diverges for real: instead of a single
    `dig_tile_b(col, 1)` call, it inlines the SAME reroll/track logic
    `dig_tile_r` uses (`_dig_tile_reroll_and_track` on the red nest map at
    the FIXED cell `(col, 1)`, not a variable position), then calls a
    specific 4-step sequence that is NOT the same as `dig_tile_r`'s own
    closing smooth (which would smooth all 4 neighbours of `(col, 1)`) --
    it smooths `(col, 0)` and `(col, 2)` and `(col-1, 1)`, then refreshes
    the exit-map at `(col, 1)` instead of smoothing `(col+1, 1)`.
    """
    from .simone import SRAND_SEED_OFF, srand1

    seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 0x1F)
    dgroup.ww(SRAND_SEED_OFF, seed)

    inside = pack.rw(0x9B6E) != 0
    found_row = None

    if inside:
        for i in range(0x22):
            row = 0x7E - ((roll + i) % 0x20)
            tile = dgroup.rb(MAP_PLANE_BASE[0] + (row << 6) + col)
            if tile == 0:
                m = 0x86
            elif tile in (2, 3):
                m = 0x8A
            elif 0x5E <= tile <= 0x61:
                m = (tile + 0x22) & 0xFF
            elif tile == 0x66:
                m = 0x85
            elif tile == 0x68:
                m = 0x84
            else:
                m = 0
            if m:
                found_row, marker = row, m
                break
        if found_row is None:
            return
        dgroup.wb(MAP_PLANE_BASE[0] + (found_row << 6) + col, marker & 0xFF)
        simant_data_group.ww(0x835E, found_row)
        simant_data_group.ww(0x8360, col)
        simant_data_group.ww(0x8356, col)
        simant_data_group.ww(0x8358, 0)
    else:
        for i in range(0x22):
            row = 0x7E - ((roll + i) % 0x20)
            if _clear_3x3(dgroup, 1, row, col):
                found_row = row
                break
        if found_row is None:
            return
        dgroup.wb(MAP_PLANE_BASE[0] + (found_row << 6) + col, 0x50)
        simant_data_group.ww(0x835E, found_row)
        simant_data_group.ww(0x8360, col)
        simant_data_group.ww(0x8356, col)
        simant_data_group.ww(0x8358, 0)

        def sbyte(off):
            v = simant_data_group.rb(off)
            return v - 0x100 if v & 0x80 else v

        for di in range(8):
            ny = sbyte(8 + di) + col
            nx = sbyte(0 + di) + found_row
            if not (0 <= nx <= 0x7F and 0 <= ny <= 0x3F):
                continue
            off = MAP_PLANE_BASE[0] + (nx << 6) + ny
            if dgroup.rb(off) < 0x50:
                dgroup.wb(off, HOLE_EDGE_TILES[di])

    simant_data_group.wb(0x8312 + col, found_row & 0xFF)
    _dig_tile_reroll_and_track(dgroup, pack, MAP_PLANE_BASE[3], 0x9DDC, 0x9DE2,
                               0x7A56, 0x9FBA, 0x9FD2, col, 1)
    smooth_edges_r(dgroup, col, 0)
    smooth_edges_r(dgroup, col, 2)
    smooth_edges_r(dgroup, col - 1, 1)
    fix_exit_map_r(dgroup, simant_data_group, col, 1)


def dig_tile_them_b(dgroup, simant_data_group, pack, x: int, y: int) -> int:
    """Open a new black-colony nest tile at (x, y), PROVIDED its existing
    dirt neighbours already look diggable — the routine that actually
    triggers `make_new_hole_b` (row 0) or reuses `_DigTileB`'s reroll/track
    bookkeeping (any other row).

    Recovered from `_DigTileThemB` (SIMANTW.SYM seg5:22D4, args x=[bp+6],
    y=[bp+8]; FAR return; returns 1 on success, 0 on any rejected case with
    NO state changes at all).

    - If `y < 0x3F`, the tile at `(x, y+1)` must be `is_it_dirt`; if
      `y > 2`, the tile at `(x, y-1)` must be too — either check failing
      rejects immediately (both together mean an edge row only needs the
      ONE neighbour that exists).
    - `x` must be in `1..0x3E` (0 and the far edge are rejected).
    - `y == 0`: writes `0x18` at `(x, y)` and calls `make_new_hole_b(x)` —
      row 0 doesn't reroll a tile here, it triggers a whole new hole
      search.  Any other `y`: rerolls `(x, y)` to a random 0..7 via
      `_SRand8`, exactly like `_DigTileB`'s own reroll step.
    - Either way: accumulates `x`/`y` into the SAME running-average dig-
      position fields `_DigTileB` uses (`pack[0x8104:0x8108]`/
      `[0x811A:0x811E]`, counter `[0x72C8]`, averages `[0x7C48]`/
      `[0x7C90]`, via genuine `__aFldiv` calls once the counter is
      positive) — confirmed by the identical PACK offsets, not assumed by
      naming — then smooths the 4 black-map neighbours and refreshes the
      black exit-map at (x, y), and returns 1.
    """
    if y < 0x3F:
        if not is_it_dirt(dgroup.rb(MAP_PLANE_BASE[2] + (x << 6) + y + 1)):
            return 0
    if y > 2:
        if not is_it_dirt(dgroup.rb(MAP_PLANE_BASE[2] + (x << 6) + y - 1)):
            return 0
    if x == 0 or x > 0x3E:
        return 0

    idx = (x << 6) + y
    if y == 0:
        dgroup.wb(MAP_PLANE_BASE[2] + idx, 0x18)
        make_new_hole_b(dgroup, simant_data_group, pack, x)
    else:
        from .simone import SRAND_SEED_OFF, srand_pow2

        seed, val = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        dgroup.wb(MAP_PLANE_BASE[2] + idx, val & 0xFF)

    xsum = _acc_add32(pack, 0x8104, 0x8106, x)
    ysum = _acc_add32(pack, 0x811A, 0x811C, y)
    count = (pack.rw(0x72C8) + 1) & 0xFFFF
    pack.ww(0x72C8, count)
    if _sx16(count) > 0:
        from .crt_math import a_f_ldiv

        pack.ww(0x7C48, a_f_ldiv(xsum, _sx16(count)) & 0xFFFF)
        pack.ww(0x7C90, a_f_ldiv(ysum, _sx16(count)) & 0xFFFF)

    smooth_edges_b(dgroup, x, y - 1)
    smooth_edges_b(dgroup, x + 1, y)
    smooth_edges_b(dgroup, x, y + 1)
    smooth_edges_b(dgroup, x - 1, y)
    fix_exit_map_b(dgroup, simant_data_group, x, y)
    return 1


def dig_tile_them_r(dgroup, simant_data_group, pack, x: int, y: int) -> int:
    """The red-colony twin of `dig_tile_them_b` (map plane 3, `make_new_hole_r`
    on row 0, `_DigTileR`'s own PACK accumulator fields otherwise).

    Recovered from `_DigTileThemR` (SIMANTW.SYM seg5:241C, args x=[bp+6],
    y=[bp+8]; FAR return).
    """
    if y < 0x3F:
        if not is_it_dirt(dgroup.rb(MAP_PLANE_BASE[3] + (x << 6) + y + 1)):
            return 0
    if y > 2:
        if not is_it_dirt(dgroup.rb(MAP_PLANE_BASE[3] + (x << 6) + y - 1)):
            return 0
    if x == 0 or x > 0x3E:
        return 0

    idx = (x << 6) + y
    if y == 0:
        dgroup.wb(MAP_PLANE_BASE[3] + idx, 0x18)
        make_new_hole_r(dgroup, simant_data_group, pack, x)
    else:
        from .simone import SRAND_SEED_OFF, srand_pow2

        seed, val = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        dgroup.wb(MAP_PLANE_BASE[3] + idx, val & 0xFF)

    xsum = _acc_add32(pack, 0x9DDC, 0x9DDE, x)
    ysum = _acc_add32(pack, 0x9DE2, 0x9DE4, y)
    count = (pack.rw(0x7A56) + 1) & 0xFFFF
    pack.ww(0x7A56, count)
    if _sx16(count) > 0:
        from .crt_math import a_f_ldiv

        pack.ww(0x9FBA, a_f_ldiv(xsum, _sx16(count)) & 0xFFFF)
        pack.ww(0x9FD2, a_f_ldiv(ysum, _sx16(count)) & 0xFFFF)

    smooth_edges_r(dgroup, x, y - 1)
    smooth_edges_r(dgroup, x + 1, y)
    smooth_edges_r(dgroup, x, y + 1)
    smooth_edges_r(dgroup, x - 1, y)
    fix_exit_map_r(dgroup, simant_data_group, x, y)
    return 1


def try_move_dir_r(dgroup, simant_data_group, pack, x: int, y: int,
                   direction: int) -> int:
    """Attempt to move the acting red ant one step in `direction` from
    (x, y). Genuinely mutually recursive with `get_out_r` — this is the
    movement-EXECUTION tier one level below the movement-SELECTION tier
    (`get_red_best_dirs` etc.) this session already recovered.

    Recovered from `_TryMoveDirR` (SIMANTW.SYM seg6:6850, args x=[bp+6],
    y=[bp+8], direction=[bp+10]; FAR return).  `direction < 0` fails
    immediately.  Computes the candidate cell via the same
    `GET_BEST_DIR_DX`/`DY` compass tables `get_best_dir` uses (confirmed
    byte-identical by reading the actual DGROUP pointer-globals this
    routine dereferences, `[0xC396]`/`[0xC398]` — a THIRD alias pair for
    the same SIMANT_DATA_GROUP table, after `get_my_best_dirs`'s and
    `get_red_best_dirs`'s own); out of the 0..63 grid on either axis fails.
    A new_y below 1 (the exit/surface row) delegates entirely to
    `get_out_r(x)`, returning ITS result verbatim — not a fixed value.

    Otherwise: the destination nest tile must be `< 0x1C` (unsigned) or
    the move fails.  On success, writes a "direction-encoded" byte
    (`(existing_caste & 0xF8) | direction`) into the LIFE grid at the new
    cell, clears the LIFE grid at the old cell, then writes the new
    position into `simant_data_group[0x4104 + slot]` (new_x)/`[0x42FA +
    slot]` (new_y) — matching `kill_tail_b`'s X/Y roles for these two
    fields, though swapped relative to which field is "X" vs "Y" there;
    not fully reconciled, ported from THIS routine's own directly-traced
    register flow rather than assumed from `kill_tail_b`'s naming — and
    the direction-encoded byte into `[0x46E6 + slot]` (the caste field).
    An initial port had this exactly backwards (assumed `[0x4104]` held
    the direction-encoded byte and never touched `[0x46E6]` at all) from
    misreading the disassembly: `mov ax,si` a few instructions before the
    `[0x4104]` write silently overwrites AL with `new_x`, clobbering the
    direction-encoded byte that had been sitting in AL since two
    instructions earlier — caught only by a state-diff test and a register-
    level instrumented trace of the real ASM, not by re-reading the
    listing. Returns 1 on a successful move.
    """
    if direction < 0:
        return 0

    new_y = y + GET_BEST_DIR_DY[direction]
    new_x = x + GET_BEST_DIR_DX[direction]
    if new_x > 0x3F or new_x < 0:
        return 0
    if new_y > 0x3F:
        return 0
    if new_y < 1:
        return get_out_r(dgroup, simant_data_group, pack, x)

    idx = (new_x << 6) + new_y
    if dgroup.rb(MAP_PLANE_BASE[3] + idx) >= 0x1C:
        return 0

    slot = pack.rw(0x9B6A)
    dir_byte = (simant_data_group.rb(0x46E6 + slot) & 0xF8) | direction
    dgroup.wb(LIFE_PLANE_BASE[3] + idx, dir_byte)
    old_idx = (x << 6) + y
    dgroup.wb(LIFE_PLANE_BASE[3] + old_idx, 0)
    simant_data_group.wb(0x4104 + slot, new_x & 0xFF)
    simant_data_group.wb(0x42FA + slot, new_y & 0xFF)
    simant_data_group.wb(0x46E6 + slot, dir_byte)
    return 1


def get_out_r(dgroup, simant_data_group, pack, x: int) -> int:
    """Handle the acting red ant reaching row 0 (the surface): either
    complete an already-marked exit hole, or nudge the dig frontier
    forward and retry the move one row in from the surface.

    Recovered from `_GetOutR` (SIMANTW.SYM seg6:74BA, arg: x; FAR return).
    Genuinely mutually recursive with `try_move_dir_r` (calls it once,
    unconditionally, near the end — its own return value is discarded,
    this routine ALWAYS returns 0 on that path).

    - Nest map tile at `(x, 0) == 0x18` (the marker `dig_tile_them_r`
      writes on row 0): clears the acting ant's caste field, then — if
      `_FillHolesRN`'s per-column hole-tracking value at
      `simant_data_group[0x8312 + x]` is exactly ZERO (an initial port had
      this condition backwards — `jnz` after the `cmp ...,0` SKIPS the
      call when the value is nonzero, so the call fires on zero, not on
      nonzero; caught by reading `_ExitHole`'s real stack arguments off an
      instrumented run and finding an x nowhere near what a correct-if-
      inverted port would produce) — calls `make_new_hole_r` (which can
      itself, transitively through `dig_tile_b`/`_them_r`, reach a wide
      swath of already-recovered dig-subsystem code).  Then re-fetches the
      acting ant's `field_e`/`field_c` fields (and the hole-tracking value
      AGAIN, since `make_new_hole_r` may have just changed it) and rerolls
      via `_SRand8`, and calls `exit_hole` with the tracked hole position
      as `(x, y)` and the caller's own `x` and rerolled caste as the
      "candidate site" — confirmed by tracing the exact push order against
      `exit_hole`'s established arg positions, not assumed.  On success:
      clears `LIFE_PLANE_BASE[3][x][1]`, returns 1.  On failure: restores
      the caste field to its original value, clears `field_c`, returns 0.
    - Tile != `0x18`: decrements the exit-map cell at `(x, 0)` if nonzero,
      rerolls `_SRand2`; on a `1` roll (and `x > 0`) or a `0` roll (and
      `x < 0x3F`), checks the neighbouring column's tile at row 1 via
      `is_it_dirt` and calls `dig_tile_them_r` there if so.  Either way,
      rerolls `_SRand8` and recurses into `try_move_dir_r(x, 1, roll)`,
      discarding its result, and always returns 0.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    base = x << 6
    if dgroup.rb(MAP_PLANE_BASE[3] + base) == 0x18:
        slot = pack.rw(0x9B6A)
        old_caste = simant_data_group.rb(0x46E6 + slot)
        simant_data_group.wb(0x46E6 + slot, 0)

        if simant_data_group.rb(0x8312 + x) == 0:
            make_new_hole_r(dgroup, simant_data_group, pack, x)

        slot = pack.rw(0x9B6A)
        field_e = simant_data_group.rb(0x48DC + slot)
        field_c = simant_data_group.rb(0x44F0 + slot)
        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        caste_arg = (roll8 + (old_caste & 0xF8)) & 0xFFFF
        hole_x = simant_data_group.rb(0x8312 + x)

        ok = exit_hole(dgroup, simant_data_group, pack, hole_x, x, caste_arg,
                       field_c, field_e)
        if ok:
            dgroup.wb(LIFE_PLANE_BASE[3] + base + 1, 0)
            return 1
        slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x46E6 + slot, old_caste)
        simant_data_group.wb(0x44F0 + slot, 0)
        return 0

    exit_map_val = simant_data_group.rb(0x13A4 + base)
    if exit_map_val != 0:
        simant_data_group.wb(0x13A4 + base, exit_map_val - 1)

    seed, roll2 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 1)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll2 != 0:
        if x > 0:
            tile = dgroup.rb(MAP_PLANE_BASE[3] + ((x - 1) << 6) + 1)
            if is_it_dirt(tile):
                dig_tile_them_r(dgroup, simant_data_group, pack, x - 1, 1)
    else:
        if x < 0x3F:
            tile = dgroup.rb(MAP_PLANE_BASE[3] + ((x + 1) << 6) + 1)
            if is_it_dirt(tile):
                dig_tile_them_r(dgroup, simant_data_group, pack, x + 1, 1)

    seed, roll8b = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    try_move_dir_r(dgroup, simant_data_group, pack, x, 1, roll8b)
    return 0


def try_move_dir_b(dgroup, simant_data_group, pack, x: int, y: int,
                   direction: int) -> int:
    """The black-colony twin of `try_move_dir_r` — structurally identical
    move-tail (same `[0x3736]`=new_x/`[0x392C]`=new_y/`[0x3D18]`=dir-byte
    field layout, same AL-clobber-then-write pattern independently
    re-verified for THIS routine, not assumed from the red twin), but with
    ONE extra gated branch `_TryMoveDirR` does not have: trophallaxis
    (food-sharing) with a blocking ant, which calls the UNRECOVERED
    `SIMANT!_DoTroph` (seg1:846E).

    Recovered from `_TryMoveDirB` (SIMANTW.SYM seg6:439E, args x=[bp+6],
    y=[bp+8], direction=[bp+10]; FAR return). Bounds/obstacle checks and
    the `new_y < 1` -> `get_out_b(x)` delegation are identical to the red
    twin. The gate for the trophallaxis branch — reached only when: the
    destination LIFE cell is exactly `0xFF` (empty), AND `pack[0x9AF2]`
    (the "not-healing" `_SetMyHealth` status flag) is nonzero, AND the
    acting ant's `simant_data_group[slot + 0x3736]` (a status-ish byte —
    NOT the position field this SAME offset holds after a completed move;
    the field is genuinely dual-purpose across calls, not a naming error)
    is `< 0x80` — is fully computed here; if it evaluates true, this
    function raises `NotImplementedError` (per this project's fail-loud
    rule: `_DoTroph`'s own dependency chain bottoms out in a real sound-
    engine routine and a dialog/busy-wait UI routine, a materially larger
    body of work than the rest of this session, so it is a deliberate,
    documented gap rather than a silently-wrong guess). Every other move
    outcome — including a plain successful move with NO trophallaxis, and
    every rejection case — is fully byte-exact.
    """
    if direction < 0:
        return 0

    new_y = y + GET_BEST_DIR_DY[direction]
    new_x = x + GET_BEST_DIR_DX[direction]
    if new_x > 0x3F or new_x < 0:
        return 0
    if new_y > 0x3F:
        return 0
    if new_y < 1:
        return get_out_b(dgroup, simant_data_group, pack, x)

    idx = (new_x << 6) + new_y
    if dgroup.rb(MAP_PLANE_BASE[2] + idx) >= 0x1C:
        return 0

    if dgroup.rb(LIFE_PLANE_BASE[2] + idx) == 0xFF and pack.rw(0x9AF2) != 0:
        slot = pack.rw(0x9B6A)
        if simant_data_group.rb(0x3736 + slot) < 0x80:
            raise NotImplementedError(
                "try_move_dir_b: trophallaxis branch reached (_DoTroph not "
                "recovered) -- x={!r} y={!r} direction={!r}".format(x, y, direction))

    slot = pack.rw(0x9B6A)
    dir_byte = (simant_data_group.rb(0x3D18 + slot) & 0xF8) | direction
    dgroup.wb(LIFE_PLANE_BASE[2] + idx, dir_byte)
    old_idx = (x << 6) + y
    dgroup.wb(LIFE_PLANE_BASE[2] + old_idx, 0)
    simant_data_group.wb(0x3736 + slot, new_x & 0xFF)
    simant_data_group.wb(0x392C + slot, new_y & 0xFF)
    simant_data_group.wb(0x3D18 + slot, dir_byte)
    return 1


def get_out_b(dgroup, simant_data_group, pack, x: int) -> int:
    """The black-colony twin of `get_out_r` — identical shape (same
    inverted `hole_x == 0` trigger condition for `make_new_hole_b`,
    verified against THIS routine's own disassembly, not assumed).

    Recovered from `_GetOutB` (SIMANTW.SYM seg6:520A, arg: x; FAR return).
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    base = x << 6
    if dgroup.rb(MAP_PLANE_BASE[2] + base) == 0x18:
        slot = pack.rw(0x9B6A)
        old_caste = simant_data_group.rb(0x3D18 + slot)
        simant_data_group.wb(0x3D18 + slot, 0)

        if simant_data_group.rb(0x82D2 + x) == 0:
            make_new_hole_b(dgroup, simant_data_group, pack, x)

        slot = pack.rw(0x9B6A)
        field_e = simant_data_group.rb(0x3F0E + slot)
        field_c = simant_data_group.rb(0x3B22 + slot)
        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        caste_arg = (roll8 + (old_caste & 0xF8)) & 0xFFFF
        hole_x = simant_data_group.rb(0x82D2 + x)

        ok = exit_hole(dgroup, simant_data_group, pack, hole_x, x, caste_arg,
                       field_c, field_e)
        if ok:
            dgroup.wb(LIFE_PLANE_BASE[2] + base + 1, 0)
            return 1
        slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x3D18 + slot, old_caste)
        simant_data_group.wb(0x3B22 + slot, 0)
        return 0

    exit_map_val = simant_data_group.rb(0x3A4 + base)
    if exit_map_val != 0:
        simant_data_group.wb(0x3A4 + base, exit_map_val - 1)

    seed, roll2 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 1)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll2 != 0:
        if x > 0:
            tile = dgroup.rb(MAP_PLANE_BASE[2] + ((x - 1) << 6) + 1)
            if is_it_dirt(tile):
                dig_tile_them_b(dgroup, simant_data_group, pack, x - 1, 1)
    else:
        if x < 0x3F:
            tile = dgroup.rb(MAP_PLANE_BASE[2] + ((x + 1) << 6) + 1)
            if is_it_dirt(tile):
                dig_tile_them_b(dgroup, simant_data_group, pack, x + 1, 1)

    seed, roll8b = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    try_move_dir_b(dgroup, simant_data_group, pack, x, 1, roll8b)
    return 0


def get_new_mode(dgroup, simant_data_group, pack, sub: int, full_byte: int) -> int:
    """Pick a caste's new "mode" byte given its current sub-mode and the
    full status byte it came from — used by combat resolution (`_DoFightA`
    and siblings) to look up a demoted/promoted caste after a kill or
    similar transition.

    Recovered from `_GetNewMode` (SIMANTW.SYM seg7:0910, args sub=[bp+6],
    full_byte=[bp+8]; FAR return).  `full_byte`'s bit `0x80` selects one of
    two symmetric branches (each reading its OWN colony's PACK-resident
    "mode table base" — `pack[0x7690]` for the `0x80`-set branch,
    `pack[0x9B8A]` for the other, gated there additionally by
    `pack[0x9FCE] == 1`); within either, `sub == 2` and `sub == 6` reroll
    via `_SRand8` and index into one of two small SIMANT_DATA_GROUP tables
    (`[0x89E6..)` for `sub==2`, `[0x8A16..)` for `sub==6`, both indexed as
    `(table_base << 3) + roll`), while every other `sub` (or the `0x80`-
    clear branch when `pack[0x9FCE] != 1`) reads a fixed per-sub byte
    table at `[0x8A46 + sub]`.  The one remaining case — `0x80` clear,
    `pack[0x9FCE] != 1`, AND `sub` in `{2, 6}` — instead reads a single
    fixed WORD at `[0x8A58]` (not sign-extended, unlike every other path,
    which reads a BYTE and sign-extends it via `cbw`).  All of these
    mode-transition tables are read LIVE (never hardcoded), matching this
    project's established convention for such game-data tables.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    def rolled_lookup(table_base: int, mode_base: int) -> int:
        seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        idx = pack.rw(mode_base)
        return sx8(simant_data_group.rb(((idx << 3) + roll + table_base) & 0xFFFF))

    if full_byte & 0x80:
        if sub == 2:
            return rolled_lookup(0x89E6, 0x7690)
        if sub == 6:
            return rolled_lookup(0x8A16, 0x7690)
        return sx8(simant_data_group.rb((sub + 0x8A46) & 0xFFFF))

    if pack.rw(0x9FCE) == 1:
        if sub == 2:
            return rolled_lookup(0x89E6, 0x9B8A)
        if sub == 6:
            return rolled_lookup(0x8A16, 0x9B8A)
        return sx8(simant_data_group.rb((sub + 0x8A46) & 0xFFFF))

    if sub in (2, 6):
        return simant_data_group.rw(0x8A58)
    return sx8(simant_data_group.rb((sub + 0x8A46) & 0xFFFF))


def get_new_mode_b(dgroup, simant_data_group, pack, sub: int) -> int:
    """The black-colony specialization of `get_new_mode`: byte-for-byte the
    same as its `full_byte & 0x80 == 0` branch (the gate-checked, PACK
    `[0x9B8A]`-mode-based one) with an implicit `full_byte = 0`.

    Recovered from `_GetNewModeB` (SIMANTW.SYM seg7:09D0, arg: sub=[bp+6];
    FAR return) — confirmed byte-identical control flow to `get_new_mode`'s
    non-`0x80` branch (same `pack[0x9FCE]` gate, same `pack[0x9B8A]` mode
    base, same three SDG table bases) via independent disassembly, not
    assumed from the name alone.
    """
    return get_new_mode(dgroup, simant_data_group, pack, sub, 0)


def get_new_mode_r(dgroup, simant_data_group, pack, sub: int) -> int:
    """The red-colony specialization of `get_new_mode`: byte-for-byte the
    same as its `full_byte & 0x80` branch (the ungated, PACK `[0x7690]`-mode-
    based one) with an implicit `full_byte = 0x80`.

    Recovered from `_GetNewModeR` (SIMANTW.SYM seg7:0A50, arg: sub=[bp+6];
    FAR return) — confirmed byte-identical control flow to `get_new_mode`'s
    `0x80` branch (same `pack[0x7690]` mode base, same three SDG table
    bases, no gate check) via independent disassembly, not assumed from the
    name alone.
    """
    return get_new_mode(dgroup, simant_data_group, pack, sub, 0x80)


def get_winner(dgroup, simant_data_group, pack, arg_a: int, arg_b: int) -> int:
    """Resolve a one-on-one combat matchup between two caste/mode bytes,
    returning whichever of `arg_a`/`arg_b` wins, and bumping the winner's
    colony win-count stats along the way.

    Recovered from `_GetWinner` (SIMANTW.SYM seg6:26F4, args arg_a=[bp+4],
    arg_b=[bp+6]; NEAR return).

    A test/cheat gate first: if `simant_data_group[0x8A5C] == 1`, skips the
    real calculation entirely — bumps `pack[0x9E96]` (dword) and returns
    `arg_b` if `arg_a`'s colony bit (`0x80`) is set, else `arg_a` (i.e. the
    non-colony-bit-set side always "wins" without ever consulting strength).

    Otherwise: looks each side's "sub" (`(v & 0x78) >> 3`) up in a per-caste
    strength table (`dgroup[0x8902 + sub]`, direct DGROUP — no pointer-
    global indirection, unlike everything else this session), combines them
    (`strength_a*4 + strength_b`) into an outcome-probability table
    (`dgroup[0x8918 + ...]`), and rolls `_RRand(10)` (the C-runtime
    generator, NOT the `_SRand*` LFSR — genuinely unpredictable) against
    it: `roll >= outcome` -> `arg_b` wins, else `arg_a` wins. Either way,
    bumps the SAME pair of PACK win-count stats, keyed only on the winner's
    own colony bit: `[0x79E4]` (word) + `[0x99E0]` (dword) for red
    (`0x80` set), or `[0xA0E4]` (word) + `[0x9E96]` (dword) for black — the
    two branches are otherwise byte-identical, ported as one shared tail.
    """
    from .crt_math import RAND_STATE_OFF
    from .simone import r_rand

    def inc_dword(view, off: int) -> None:
        v = (view.rw(off) | (view.rw(off + 2) << 16)) + 1
        view.ww(off, v & 0xFFFF)
        view.ww(off + 2, (v >> 16) & 0xFFFF)

    if simant_data_group.rb(0x8A5C) == 1:
        inc_dword(pack, 0x9E96)
        return arg_b if arg_a & 0x80 else arg_a

    def strength(v: int) -> int:
        return dgroup.rb(0x8902 + ((v & 0x78) >> 3))

    outcome = dgroup.rb(0x8918 + (strength(arg_a) << 2) + strength(arg_b))

    state = dgroup.rw(RAND_STATE_OFF) | (dgroup.rw(RAND_STATE_OFF + 2) << 16)
    state, roll = r_rand(state, 10)
    dgroup.ww(RAND_STATE_OFF, state & 0xFFFF)
    dgroup.ww(RAND_STATE_OFF + 2, (state >> 16) & 0xFFFF)

    winner = arg_b if roll >= outcome else arg_a
    if winner & 0x80:
        pack.ww(0x79E4, (pack.rw(0x79E4) + 1) & 0xFFFF)
        inc_dword(pack, 0x99E0)
    else:
        pack.ww(0xA0E4, (pack.rw(0xA0E4) + 1) & 0xFFFF)
        inc_dword(pack, 0x9E96)
    return winner


def sim_egg_a(dgroup, simant_data_group, slot: int) -> None:
    """Stamp a yard ("A"-list) egg's caste onto the life grid, and on a
    1-in-200 roll, remove it (hatched or died — either way, gone).

    Recovered from `_SimEggA` (SIMANTW.SYM seg6:0A1C, NEAR return, arg:
    slot). Always stamps `simant_data_group[slot].caste` into
    `LIFE_PLANE_BASE[0]` at the egg's own recorded position — even on a
    tick where nothing else happens, matching the caste value already
    displayed there (a redundant-looking but faithful re-stamp). Then
    rolls `_SRand1(200)`; only on an exact `0` does it clear the egg's
    caste field and its life-grid cell.
    """
    from .simone import SRAND_SEED_OFF, srand1

    a_y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)
    a_x = simant_data_group.rb(0x23A4 + slot)

    life_off = LIFE_PLANE_BASE[0] + (a_x << 6) + a_y
    dgroup.wb(life_off, caste)

    seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 200)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll != 0:
        return

    simant_data_group.wb(0x2F62 + slot, 0)
    dgroup.wb(life_off, 0)


def lost_head_a(dgroup, simant_data_group, pack, x: int, y: int,
                 direction: int) -> int:
    """Check whether the yard trail-head marker one step ahead in
    `direction` has come unclaimed.

    Recovered from `_LostHeadA` (SIMANTW.SYM seg6:0B1E, NEAR return, args
    x=[bp+4], y=[bp+6], direction=[bp+8]).

    Steps one cell in `direction & 7` (the same compass delta tables every
    `_Get*Dir`/`_Bounce` routine uses) and reads the yard life plane there.
    If that cell's value equals `direction - 8` (a specific encoded
    "trail head" marker tied to the direction itself), the marker's
    intact and returns `0` immediately — no need to consult the A-list.
    Otherwise the tile has changed, so it falls back to actually
    searching the A-list for an ant AT that cell (`find_in_a_list`): found
    still returns `0`, not found returns `1` (the head is lost).
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    dir_idx = direction & 7
    ny = y + sx8(simant_data_group.rb(8 + dir_idx))
    nx = x + sx8(simant_data_group.rb(dir_idx))
    tile = dgroup.rb(LIFE_PLANE_BASE[0] + (nx << 6) + ny)
    check = (tile - direction) & 0xFFFF
    if check == 0xFFF8:
        return 0
    found = find_in_a_list(pack, simant_data_group, nx, ny)
    return 0 if _sx16(found) >= 0 else 1


def _lost_head(dgroup, simant_data_group, pack, life_plane_base: int,
               find_list, x: int, y: int, direction: int) -> int:
    """Shared body of `lost_head_b`/`r`."""
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    dir_idx = direction & 7
    ny = y + sx8(simant_data_group.rb(8 + dir_idx))
    caste_check = (direction - 8) & 0xFFFF
    nx = x + sx8(simant_data_group.rb(dir_idx))
    tile = dgroup.rb(life_plane_base + (nx << 6) + ny)
    if tile == caste_check:
        return 0
    found = find_list(pack, simant_data_group, nx, ny, caste_check)
    return 0 if _sx16(found) >= 0 else 1


def lost_head_b(dgroup, simant_data_group, pack, x: int, y: int,
                 direction: int) -> int:
    """The black-colony NEST-map twin of `lost_head_a` — the trail-head
    marker is `direction - 8` on the black nest life plane (`kill_tail_b`'s
    `_FindInBList` for occupancy).

    Recovered from `_LostHeadB` (SIMANTW.SYM seg6:42DE, FAR return, args
    x=[bp+6], y=[bp+8], direction=[bp+10]).
    """
    return _lost_head(dgroup, simant_data_group, pack, LIFE_PLANE_BASE[2],
                      find_in_b_list, x, y, direction)


def lost_head_r(dgroup, simant_data_group, pack, x: int, y: int,
                 direction: int) -> int:
    """The red-colony twin of `lost_head_b`.

    Recovered from `_LostHeadR` (SIMANTW.SYM seg6:6790, FAR return, args
    x=[bp+6], y=[bp+8], direction=[bp+10]).
    """
    return _lost_head(dgroup, simant_data_group, pack, LIFE_PLANE_BASE[3],
                      find_in_r_list, x, y, direction)


def _lost_tail(dgroup, simant_data_group, pack, life_plane_base: int,
               find_list, x: int, y: int, direction: int) -> int:
    """Shared body of `lost_tail_b`/`r` — `lost_head_b`/`r`'s twin, checking
    the OPPOSITE direction (`direction ^ 4`) for a tail marker
    (`direction + 8`) instead of a head marker one step ahead."""
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    dir_idx = (direction ^ 4) & 7
    ny = y + sx8(simant_data_group.rb(8 + dir_idx))
    caste_check = (direction + 8) & 0xFFFF
    nx = x + sx8(simant_data_group.rb(dir_idx))
    tile = dgroup.rb(life_plane_base + (nx << 6) + ny)
    if tile == caste_check:
        return 0
    found = find_list(pack, simant_data_group, nx, ny, caste_check)
    return 0 if _sx16(found) >= 0 else 1


def lost_tail_b(dgroup, simant_data_group, pack, x: int, y: int,
                 direction: int) -> int:
    """Recovered from `_LostTailB` (SIMANTW.SYM seg6:433C, FAR return, args
    x=[bp+6], y=[bp+8], direction=[bp+10]). See `_lost_tail`.
    """
    return _lost_tail(dgroup, simant_data_group, pack, LIFE_PLANE_BASE[2],
                      find_in_b_list, x, y, direction)


def lost_tail_r(dgroup, simant_data_group, pack, x: int, y: int,
                 direction: int) -> int:
    """The red-colony twin of `lost_tail_b`.

    Recovered from `_LostTailR` (SIMANTW.SYM seg6:67EE, FAR return, args
    x=[bp+6], y=[bp+8], direction=[bp+10]).
    """
    return _lost_tail(dgroup, simant_data_group, pack, LIFE_PLANE_BASE[3],
                      find_in_r_list, x, y, direction)


def do_fight_a(dgroup, simant_data_group, pack, slot: int) -> None:
    """Resolve one tick of combat for a yard ("A"-list) ant: jitter its
    caste, and on a 1-in-16 roll, kill it — recovered from SIMANT1's
    `_DoFightA`, the first genuinely TOP-LEVEL `_Do*Ant*` behavior routine
    this project has recovered (one call-hop below `_DoAntSimA`).

    Recovered from `_DoFightA` (SIMANTW.SYM seg6:27E6, arg: slot — the
    A-list index of the ant being resolved, read via
    `simant_data_group[0x23A4/0x278E + slot]` for its position, same as
    `find_in_a_list`; NEAR return, no meaningful AX contract).

    Always: rerolls the low 3 bits of the ant's caste
    (`simant_data_group[0x2F62 + slot]`) via `_SRand1(7)` (a genuine
    ADD, not OR, into the pre-masked `& 0xF8` value — behaviourally
    identical to OR since the roll is always < 8, ported as the literal
    ADD anyway) and stamps the result into the yard life grid at the
    ant's position.

    Then rolls `_SRand16()`; on a `0` (1-in-16 chance): overwrites the
    life-grid cell AND the caste field with the ant's `field_e`
    (`simant_data_group[0x334C + slot]`), computes a new mode via
    `get_new_mode(sub=(field_e & 0x78) >> 3, full_byte=field_e)`, writes
    that into the ACTING ant's (not this ant's — `pack[0x9B6A]`)
    `field_c` (`[0x2B78]`), clears this ant's `field_e`, and calls
    `dead_ant_here(a_x, a_y, colony_bit)` — where `colony_bit` is the
    now-current caste's `0x80` bit — recording this death into the
    ring-buffer `_DeadAntHere` maintains.

    On any OTHER `_SRand16()` roll (no kill this tick): the ASM
    conditionally calls `ANTEDIT!_FightBalloons` (a speech-balloon UI
    routine, gated on a SIMANT_DATA_GROUP flag at `[0x85FC]`) — a pure
    presentation side effect with no simulation-state impact, deliberately
    NOT ported here (matches this project's existing redraw/sound-stub
    convention, e.g. `_ZapEuMapAt`); this recovered function is a no-op
    beyond the caste reroll already done above on that path.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    a_x = simant_data_group.rb(0x23A4 + slot)
    a_y = simant_data_group.rb(0x278E + slot)

    seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)

    caste_off = 0x2F62 + slot
    new_caste = ((simant_data_group.rb(caste_off) & 0xF8) + roll) & 0xFF
    simant_data_group.wb(caste_off, new_caste)
    life_off = LIFE_PLANE_BASE[0] + (a_x << 6) + a_y
    dgroup.wb(life_off, new_caste)

    seed, roll16 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 15)
    dgroup.ww(SRAND_SEED_OFF, seed)

    if roll16 != 0:
        return

    field_e = simant_data_group.rb(0x334C + slot)
    dgroup.wb(life_off, field_e)
    simant_data_group.wb(caste_off, field_e)

    sub = (field_e & 0x78) >> 3
    new_mode = get_new_mode(dgroup, simant_data_group, pack, sub, field_e) & 0xFF

    acting_slot = pack.rw(0x9B6A)
    simant_data_group.wb(0x2B78 + acting_slot, new_mode)
    simant_data_group.wb(0x334C + slot, 0)

    colony_bit = simant_data_group.rb(caste_off) & 0x80
    dead_ant_here(dgroup, pack, a_x, a_y, colony_bit)


def start_fight_a(dgroup, simant_data_group, pack, slot1: int, x1: int,
                   y1: int, x2: int, y2: int) -> None:
    """Initiate combat between a yard ant at `(x1, y1)` (A-list slot
    `slot1`) and whatever ant occupies `(x2, y2)`, if any.

    Recovered from `_StartFightA` (SIMANTW.SYM seg6:266A, NEAR return,
    args slot1=[bp+4], x1=[bp+6], y1=[bp+8], x2=[bp+10], y2=[bp+12]).

    UNCONDITIONALLY, before even looking for a target: clears the
    attacker's own caste field and its yard life-grid cell at `(x1, y1)` —
    it "vanishes" whether or not a fight actually resolves. Then searches
    the A-list for an ant at `(x2, y2)` via the already-recovered
    `find_in_a_list`; if none is found, that's the entire effect (no
    fight). Otherwise, resolves the matchup via the already-recovered
    `get_winner(arg_a=defender's caste, arg_b=attacker's caste)`, stamps
    the DEFENDER's slot with a "defeated" caste
    (`(winner & 0x80) + 0x70`, written to both its caste field and the
    life-grid cell at `(x2, y2)`), sets its `field_c` to `10` and
    `field_e` to the raw winner byte, and finally bumps the ALARM grid at
    `(x2, y2)` by `40` via the already-recovered `alarm_here2`.
    """
    caste1 = simant_data_group.rb(0x2F62 + slot1)
    simant_data_group.wb(0x2F62 + slot1, 0)
    dgroup.wb(LIFE_PLANE_BASE[0] + (x1 << 6) + y1, 0)

    slot2 = find_in_a_list(pack, simant_data_group, x2, y2)
    if slot2 == 0xFFFF:
        return

    caste2 = simant_data_group.rb(0x2F62 + slot2)
    winner = get_winner(dgroup, simant_data_group, pack, caste2, caste1)

    new_caste2 = (winner & 0x80) + 0x70
    simant_data_group.wb(0x2F62 + slot2, new_caste2)
    dgroup.wb(LIFE_PLANE_BASE[0] + (x2 << 6) + y2, new_caste2)
    simant_data_group.wb(0x2B78 + slot2, 10)
    simant_data_group.wb(0x334C + slot2, winner & 0xFF)

    alarm_here2(simant_data_group, x2, y2, 40)


def go_in_nest(dgroup, simant_data_group, pack, x: int, y: int, slot: int) -> None:
    """Move a yard ant (A-list slot `slot`, standing at `(x, y)`) into a
    colony's nest — `x < 0x40` picks black, `x >= 0x40` picks red (the yard
    is split down the middle at the map's x-midpoint).

    Recovered from `_GoInNest` (SIMANTW.SYM seg6:257A, NEAR return, args
    x=[bp+4], y=[bp+6], slot=[bp+8]).

    Compacts the target colony's list first if it's at its 500-slot cap
    (via the already-recovered `compact_list_b`/`r`); if it's STILL full
    afterward, the ant stays exactly where it is — no further effect at
    all, not even the final vanish. Otherwise, appends a new nest-list
    record via the already-recovered `add_ant_to_b_list`/`r_list`, copying
    the yard ant's `field_c`/`field_e` and a `+4`-bumped caste, at a fixed
    nest-entrance column (`x=1` in the callee's own argument order) and
    `y` set to THIS function's `y` argument — the nest entrance is a
    single column, `y` is which row/lane the ant enters at. If that row's
    exit-distance map cell is nonzero (`simant_data_group[0x82D2 + y]`
    black / `[0x8312 + y]` red — the SAME arrays `_FillHolesBN`/`RN`
    maintain), immediately digs that entrance tile too (`dig_tile_b`/`r`,
    again at the fixed column `y=1` in the callee's own argument order).
    Finally, regardless of which of the two add-then-maybe-dig branches
    ran: clears the ant's own A-list caste field and its yard life-grid
    cell at `(x, y)` — it vanishes from the yard, now living only in the
    nest list.
    """
    if x < 0x40:
        count_off, add_list, dig_tile, exit_map, compact = (
            0x99D4, add_ant_to_b_list, dig_tile_b, 0x82D2, compact_list_b)
    else:
        count_off, add_list, dig_tile, exit_map, compact = (
            0x72CC, add_ant_to_r_list, dig_tile_r, 0x8312, compact_list_r)

    if pack.rw(count_off) >= 0x1F4:
        compact(pack, simant_data_group)
    if pack.rw(count_off) >= 0x1F4:
        return

    field_e = simant_data_group.rb(0x334C + slot)
    field_c = simant_data_group.rb(0x2B78 + slot)
    caste = (simant_data_group.rb(0x2F62 + slot) & 0xF8) + 4
    add_list(pack, simant_data_group, dgroup, y, 1, caste, field_c, field_e)

    if simant_data_group.rb(exit_map + y) != 0:
        dig_tile(dgroup, simant_data_group, pack, y, 1)

    simant_data_group.wb(0x2F62 + slot, 0)
    dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)


def rand_turn(dgroup, simant_data_group, caste_low3: int) -> int:
    """Pick a purely random direction from the caste-mode table — no
    yard-edge handling, no gradient, just a fresh `_SRand8()` roll.

    Recovered from `_RandTurn` (SIMANTW.SYM seg6:2A22, NEAR return, arg:
    caste_low3=[bp+4]). Byte-identical to the tail every seg7 `_Get*Dir`
    routine's random fallback shares (`simant_data_group[0x24 + roll +
    (caste_low3 << 3)]`), minus the `_Bounce` edge check those all have —
    this one is unconditional.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    return sx8(simant_data_group.rb(0x24 + roll + (caste_low3 << 3)))


def _steal_food(dgroup, pack, map_base: int, count_off: int, x: int, y: int) -> None:
    """Shared body of `steal_food_b`/`r`: an ant nibbling stored food at
    `(x, y)` on the colony's nest map. If the cell is EXACTLY the "full
    food pile" tile (`0x10`), rerolls it to a fresh `_SRand8()` value
    instead of decrementing — otherwise decrements the tile by one (a
    byte-wrapping decrement, matching the ASM's plain `dec` with no
    underflow guard). Also decrements the colony's food-count stat
    (`pack[count_off]`), but only while it's still positive (a signed
    `> 0` guard, so it floors at exactly `0`).
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    idx = map_base + (x << 6) + y
    tile = dgroup.rb(idx)
    if tile == 0x10:
        seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        dgroup.wb(idx, roll)
    else:
        dgroup.wb(idx, (tile - 1) & 0xFF)

    if _sx16(pack.rw(count_off)) > 0:
        pack.ww(count_off, (pack.rw(count_off) - 1) & 0xFFFF)


def steal_food_b(dgroup, pack, x: int, y: int) -> None:
    """Recovered from `_StealFoodB` (SIMANTW.SYM seg6:48B4, FAR return,
    args x=[bp+6], y=[bp+8]). See `_steal_food`.
    """
    _steal_food(dgroup, pack, MAP_PLANE_BASE[2], 0x9EA4, x, y)


def steal_food_r(dgroup, pack, x: int, y: int) -> None:
    """The red-colony twin of `steal_food_b`.

    Recovered from `_StealFoodR` (SIMANTW.SYM seg6:6C26, FAR return,
    args x=[bp+6], y=[bp+8]).
    """
    _steal_food(dgroup, pack, MAP_PLANE_BASE[3], 0x72DE, x, y)


def _reroll_or_decrement_food_tile(dgroup, idx: int) -> None:
    """The same "nibble a food tile" step `_steal_food` uses: reroll via
    `_SRand8` on the "full pile" tile (`0x10`), else a genuine
    byte-wrapping decrement with no underflow guard."""
    from .simone import SRAND_SEED_OFF, srand_pow2

    tile = dgroup.rb(idx)
    if tile == 0x10:
        seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        dgroup.wb(idx, roll)
    else:
        dgroup.wb(idx, (tile - 1) & 0xFF)


def _food_growth_trigger(dgroup, pack, ant_count1: int, ant_count2: int,
                         timer_off: int, cap_off: int) -> None:
    """Shared tail of `eat_food_b`/`r`/`try_eat_food_b`/`r`: accumulate a
    per-colony "time to grow" timer (`pack[timer_off]`, `+5` every call)
    against a threshold derived from two DGROUP ant-count-ish stats
    (`(dgroup[ant_count1] + dgroup[ant_count2]) >> 4`); once the timer
    catches up, resets it to `0` and bumps a DGROUP counter
    (`dgroup[cap_off]`, capped at `100`) — presumably a colony-growth
    trigger (new ant/egg spawn eligibility), though the caller/consumer of
    `cap_off` is not yet recovered.
    """
    threshold = _sx16(dgroup.rw(ant_count1)) + _sx16(dgroup.rw(ant_count2))
    threshold >>= 4
    pack.ww(timer_off, (pack.rw(timer_off) + 5) & 0xFFFF)
    if threshold >= _sx16(pack.rw(timer_off)):
        return
    pack.ww(timer_off, 0)
    if dgroup.rw(cap_off) >= 100:
        return
    dgroup.ww(cap_off, (dgroup.rw(cap_off) + 1) & 0xFFFF)


def _eat_food(dgroup, pack, map_base: int, food_count_off: int,
             ant_count1: int, ant_count2: int, timer_off: int,
             cap_off: int, x: int, y: int) -> None:
    """Shared body of `eat_food_b`/`r`: UNCONDITIONALLY nibbles the food
    tile at `(x, y)` (like `_steal_food`, but always — no tile-range gate)
    and always runs the colony-growth trigger afterward.
    """
    idx = map_base + (x << 6) + y
    _reroll_or_decrement_food_tile(dgroup, idx)
    if _sx16(pack.rw(food_count_off)) > 0:
        pack.ww(food_count_off, (pack.rw(food_count_off) - 1) & 0xFFFF)
    _food_growth_trigger(dgroup, pack, ant_count1, ant_count2, timer_off, cap_off)


def eat_food_b(dgroup, pack, x: int, y: int) -> None:
    """Recovered from `_EatFoodB` (SIMANTW.SYM seg6:4844, FAR return, args
    x=[bp+6], y=[bp+8]). See `_eat_food`.
    """
    _eat_food(dgroup, pack, MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98,
             0x7402, 0xAC86, x, y)


def eat_food_r(dgroup, pack, x: int, y: int) -> None:
    """The red-colony twin of `eat_food_b`.

    Recovered from `_EatFoodR` (SIMANTW.SYM seg6:6BB6, FAR return, args
    x=[bp+6], y=[bp+8]).
    """
    _eat_food(dgroup, pack, MAP_PLANE_BASE[3], 0x72DE, 0xAC84, 0xACA4,
             0x7C8E, 0xAC88, x, y)


def _try_eat_food(dgroup, pack, map_base: int, food_count_off: int,
                  ant_count1: int, ant_count2: int, timer_off: int,
                  cap_off: int, x: int, y: int) -> None:
    """Shared body of `try_eat_food_b`/`r`: like `_eat_food`, but GATED —
    a complete no-op unless the tile at `(x, y)` is in `[0x10, 0x13]` (the
    valid food-pile range).
    """
    idx = map_base + (x << 6) + y
    tile = dgroup.rb(idx)
    if not (0x10 <= tile <= 0x13):
        return
    _reroll_or_decrement_food_tile(dgroup, idx)
    if _sx16(pack.rw(food_count_off)) > 0:
        pack.ww(food_count_off, (pack.rw(food_count_off) - 1) & 0xFFFF)
    _food_growth_trigger(dgroup, pack, ant_count1, ant_count2, timer_off, cap_off)


def try_eat_food_b(dgroup, pack, x: int, y: int) -> None:
    """Recovered from `_TryEatFoodB` (SIMANTW.SYM seg6:47C6, FAR return,
    args x=[bp+6], y=[bp+8]). See `_try_eat_food`.
    """
    _try_eat_food(dgroup, pack, MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98,
                  0x7402, 0xAC86, x, y)


def try_eat_food_r(dgroup, pack, x: int, y: int) -> None:
    """The red-colony twin of `try_eat_food_b`.

    Recovered from `_TryEatFoodR` (SIMANTW.SYM seg6:6B38, FAR return, args
    x=[bp+6], y=[bp+8]).
    """
    _try_eat_food(dgroup, pack, MAP_PLANE_BASE[3], 0x72DE, 0xAC84, 0xACA4,
                  0x7C8E, 0xAC88, x, y)


def _raid_out(dgroup, simant_data_group, pack, get_exit_dir, try_move_dir,
             life_plane_base: int, caste_off: int, x: int, y: int) -> None:
    """Shared body of `raid_out_b`/`r`: move the acting ant one step toward
    an exit (or a random direction if none found); if that's blocked, try
    ONE more random direction; if THAT'S also blocked, give up on moving
    and just re-stamp the acting ant's own caste onto its current cell
    (a visual/state correction with no position change).
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    result = get_exit_dir(dgroup, simant_data_group, x, y, 8)
    if result != 0:
        direction = result - 1
    else:
        seed, direction = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)

    if try_move_dir(dgroup, simant_data_group, pack, x, y, direction):
        return

    seed, direction2 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if try_move_dir(dgroup, simant_data_group, pack, x, y, direction2):
        return

    acting_slot = pack.rw(0x9B6A)
    caste = simant_data_group.rb(caste_off + acting_slot)
    dgroup.wb(life_plane_base + (x << 6) + y, caste)


def raid_out_b(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """Recovered from `_RaidOutB` (SIMANTW.SYM seg6:3610, FAR return, args
    x=[bp+6], y=[bp+8]). See `_raid_out`.
    """
    _raid_out(dgroup, simant_data_group, pack, get_exit_dir_b, try_move_dir_b,
             LIFE_PLANE_BASE[2], 0x3D18, x, y)


def raid_out_r(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """The red-colony twin of `raid_out_b`.

    Recovered from `_RaidOutR` (SIMANTW.SYM seg6:5D10, FAR return, args
    x=[bp+6], y=[bp+8]).
    """
    _raid_out(dgroup, simant_data_group, pack, get_exit_dir_r, try_move_dir_r,
             LIFE_PLANE_BASE[3], 0x46E6, x, y)


def bounce(dgroup, x: int, y: int) -> int:
    """Pick a "bounce back into the map" compass value for an ant sitting at
    the yard edge, or `0` for a strictly interior position.

    Recovered from `_Bounce` (SIMTWO.SYM seg7:12EC, args: x=[bp+6], y=[bp+8];
    FAR return). The yard is 128x64 (`x` in `0..0x7F`, `y` in `0..0x3F` — the
    same axes `LIFE_PLANE_BASE[0] + (x << 6) + y` indexes elsewhere). Eight
    cases — four edges, four corners — each roll `_SRand1(3)` (corners, a
    narrower jitter) or `_SRand1(5)` (edges) and add a per-edge offset
    (left=1, top=3, right=5, bottom=7) that keeps the result inside a single
    contiguous `1..11` band; the caller (`_DoDigOutAntA`) turns a nonzero
    result into a 0-based octant index via `(result - 1) & 7`. Strictly
    interior (`1 <= x <= 0x7E`, `1 <= y <= 0x3E`) returns `0` — the sentinel
    the caller reads as "no edge to bounce off of, use the mode-random
    direction instead."
    """
    from .simone import SRAND_SEED_OFF, srand1

    def roll(n: int) -> int:
        seed, r = srand1(dgroup.rw(SRAND_SEED_OFF), n)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return r

    if x == 0:
        if y == 0:
            return roll(3) + 3
        return roll(3 if y == 0x3F else 5) + 1
    if y == 0:
        if x == 0x7F:
            return roll(3) + 5
        return roll(5) + 3
    if x == 0x7F:
        if y == 0x3F:
            return roll(3) + 7
        return roll(5) + 5
    if y == 0x3F:
        return roll(5) + 7
    return 0


def get_forage_dir(dgroup, simant_data_group, x: int, y: int, caste_low3: int,
                    colony_flag: int) -> int:
    """Pick a foraging direction: follow the gradient of the caller's colony
    TRAIL scent grid around `(x, y)`'s half-res cell, or handle the yard
    edge with a simpler (non-`_Bounce`) scheme.

    Recovered from `_GetForageDir` (SIMTWO.SYM seg7:0AB0, args x=[bp+6],
    y=[bp+8], caste_low3=[bp+10], colony_flag=[bp+12]; FAR return). The edge
    handling looks like `_Bounce` but is NOT it — a genuinely different,
    simpler scheme independently disassembled and confirmed distinct: all
    four corners return a FIXED constant with no RNG at all (left=1, top=3,
    TL=3/BL=1/TR=5/BR=7, no per-corner jitter), the three "left/top/right"
    general edges roll `_SRand1(3)` plus the same offset as the adjacent
    corner, and the general BOTTOM edge alone uses a different
    transform, `(_SRand1(3) - 1) & 7`, not `+7`.

    Strictly interior: scans the 8 compass neighbors of the half-res cell
    `(x >> 1, y >> 1)` on the colony's TRAIL grid (`colony_flag & 0x80` picks
    red `[0x7AD2..)` vs black `[0x6AD2..)`, the SAME grids `jam_scent_bt`/
    `rt` write), tracking the neighbor with the highest scent (ties keep the
    lowest index, pre-seeded with a random `_SRand8()` roll so an
    all-tied-at-zero scan still picks *some* index). If no neighbor beats
    zero, the direction comes from the same `caste_low3`-indexed mode table
    `_DoDigOutAntA` uses (`simant_data_group[0x24 + (caste_low3 << 3) + i]`)
    at a fresh random roll. Otherwise: if the ant's OWN cell already out-
    scents every neighbor, returns `-1` (a "no better direction, stay put"
    sentinel); else the mode table is read again, at the winning neighbor's
    index.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    def roll3() -> int:
        seed, r = srand1(dgroup.rw(SRAND_SEED_OFF), 3)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return r

    def roll8() -> int:
        seed, r = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return r

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    if x == 0:
        if y == 0:
            return 3
        if y == 0x3F:
            return 1
        return roll3() + 1
    if y == 0:
        if x == 0x7F:
            return 5
        return roll3() + 3
    if x == 0x7F:
        if y == 0x3F:
            return 7
        return roll3() + 5
    if y == 0x3F:
        return (roll3() - 1) & 7

    hx, hy = x >> 1, y >> 1
    trail_base = 0x7AD2 if colony_flag & 0x80 else 0x6AD2
    own_scent = simant_data_group.rb(trail_base + (hx << 5) + hy)

    best_dir = roll8()
    best_val = 0
    for i in range(8):
        dx = sx8(simant_data_group.rb(i))
        dy = sx8(simant_data_group.rb(8 + i))
        nx = (hx + dx) & 0x3F
        ny = (hy + dy) & 0x1F
        val = simant_data_group.rb(trail_base + (nx << 5) + ny)
        if val > best_val:
            best_val = val
            best_dir = i

    if best_val <= 0:
        return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + roll8()))
    if own_scent > best_val:
        return -1
    return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + best_dir))


def get_nest_dir(dgroup, simant_data_group, x: int, y: int, caste_low3: int,
                  colony_flag: int) -> int:
    """Pick a nest-ward direction: follow the gradient of the caller's
    colony NEST scent grid around `(x, y)`'s half-res cell if it has any
    scent at all, else steer straight toward the colony's queen/nest-
    entrance target via `get_dir`.

    Recovered from `_GetNestDir` (SIMTWO.SYM seg7:0C30, args x=[bp+6],
    y=[bp+8], caste_low3=[bp+10], colony_flag=[bp+12]; FAR return).

    Unlike `_GetForageDir`, the yard-edge handling here is `_Bounce`'s
    OWN formula, compiled inline rather than called (confirmed
    byte-identical offset-per-edge/corner to `bounce()` by independent
    disassembly) — ported as a literal `bounce()` call plus the same
    `(result - 1) & 7` conversion `_DoDigOutAntA` applies to its own
    `_Bounce` result.

    Strictly interior: if the ant's own NEST-grid cell (`[0x62D2..)` black /
    `[0x72D2..)` red, same grids `jam_scent_bn`/`rn` write) is nonzero,
    scans its 8 compass neighbors for the highest-scent direction (ties
    keep the lowest index — no random tie-break seed this time, unlike
    `_GetForageDir`), rolls a `_SRand2()` purely for its LFSR-advancing
    side effect (the roll's VALUE never affects the outcome — both its
    branches converge on the identical mode-table read, a genuine
    dead-code artifact of the original compile, reproduced here only to
    keep the LFSR in sync with later calls), then reads the mode table at
    the found direction.

    If the own cell has NO scent at all, it skips the neighbor scan
    entirely and instead calls the already-recovered `get_dir` toward the
    colony's stored queen/nest-entrance position (`simant_data_group`
    words at `[0x835E]`/`[0x8360]` for red, `[0x835A]`/`[0x835C]` for
    black); on a `get_dir` result of `0` (already there) or a failed
    `_SRand4()` roll (1-in-4), falls back to a fresh `_SRand8()`-random
    mode-table pick, matching `_GetForageDir`'s and `_DoDigOutAntA`'s
    fallback shape.  The mode table itself is the SAME one `get_forage_dir`
    uses (`simant_data_group[0x24 + ...]`) — the ASM reads it through a
    `[0x23 + ...]` base with `get_dir`'s native `1..8` result instead of
    `[0x24 + ...]` with a `- 1` adjustment, which is byte-address-identical
    and ported here as the latter for consistency with the rest of this
    module.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def roll(mask: int) -> int:
        seed, r = srand_pow2(dgroup.rw(SRAND_SEED_OFF), mask)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return r

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    edge = bounce(dgroup, x, y)
    if edge != 0:
        return (edge - 1) & 7

    hx, hy = x >> 1, y >> 1
    colony_r = colony_flag & 0x80
    nest_base = 0x72D2 if colony_r else 0x62D2
    own_scent = simant_data_group.rb(nest_base + (hx << 5) + hy)

    if own_scent != 0:
        best_dir = 0
        best_val = 0
        for i in range(8):
            dx = sx8(simant_data_group.rb(i))
            dy = sx8(simant_data_group.rb(8 + i))
            nx = (hx + dx) & 0x3F
            ny = (hy + dy) & 0x1F
            val = simant_data_group.rb(nest_base + (nx << 5) + ny)
            if val > best_val:
                best_val = val
                best_dir = i
        roll(1)   # _SRand2 -- consumed for its seed advance only, result unused
        return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + best_dir))

    tx_off, ty_off = (0x835E, 0x8360) if colony_r else (0x835A, 0x835C)
    target_x = simant_data_group.rw(tx_off)
    target_y = simant_data_group.rw(ty_off)
    dir_result = get_dir(x, y, target_x, target_y)
    if dir_result != 0 and roll(3) != 0:
        return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + (dir_result - 1)))
    return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + roll(7)))


def get_alarm_dir(dgroup, simant_data_group, x: int, y: int, caste_low3: int) -> int:
    """Pick a direction away from danger: follow the gradient of the single,
    colony-neutral ALARM scent grid around `(x, y)`'s half-res cell.

    Recovered from `_GetAlarmDir` (SIMTWO.SYM seg7:0E54, args x=[bp+6],
    y=[bp+8], caste_low3=[bp+10]; FAR return) — unlike `_GetForageDir`/
    `_GetNestDir`, there is no colony argument at all; the ALARM grid at
    `simant_data_group[0x52D2..)` is shared by both colonies.

    Yard-edge handling is (like `_GetNestDir`) `_Bounce`'s own formula
    compiled inline, ported the same way: a `bounce()` call plus the
    `(r - 1) & 7` conversion.

    Strictly interior: scans the 8 compass neighbors (never the ant's own
    cell — there is no "own cell already best" check here, unlike
    `_GetForageDir`) for the highest ALARM value, ties keeping the lowest
    index (no random tie-break seed, same as `_GetNestDir`). If every
    neighbor is zero, falls back to a fresh `_SRand8()`-random mode-table
    pick; otherwise reads the mode table at the winning neighbor's index.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    edge = bounce(dgroup, x, y)
    if edge != 0:
        return (edge - 1) & 7

    hx, hy = x >> 1, y >> 1
    best_dir = 0
    best_val = 0
    for i in range(8):
        dx = sx8(simant_data_group.rb(i))
        dy = sx8(simant_data_group.rb(8 + i))
        nx = (hx + dx) & 0x3F
        ny = (hy + dy) & 0x1F
        val = simant_data_group.rb(0x52D2 + (nx << 5) + ny)
        if val > best_val:
            best_val = val
            best_dir = i

    if best_val == 0:
        seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + roll))
    return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + best_dir))


def get_rand_dir(dgroup, simant_data_group, x: int, y: int, caste_low3: int) -> int:
    """Pick a purely random direction: no gradient-following at all — just
    the yard-edge `_Bounce` handling shared with `_GetNestDir`/
    `_GetAlarmDir`, or (strictly interior) a fresh `_SRand8()`-random
    mode-table pick.

    Recovered from `_GetRandDir` (SIMTWO.SYM seg7:0F72, args x=[bp+6],
    y=[bp+8], caste_low3=[bp+10]; FAR return) — the simplest of the seg7
    `_Get*Dir` family; byte-identical to the random-fallback tail every
    other member of the family shares.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    edge = bounce(dgroup, x, y)
    if edge != 0:
        return (edge - 1) & 7

    seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + roll))


def get_defend_dir(dgroup, simant_data_group, pack, x: int, y: int,
                    caste_low3: int) -> int:
    """Pick a "defend the colony" direction — behavior depends entirely on
    `dgroup[0xCE80]`, a global game-mode selector: mode 2/3 just delegate
    wholesale to `get_nest_dir` (colony B/R); mode 1 steers toward a fixed
    attack-marker point, either directly or at random once close enough;
    any other mode is a no-op that echoes `caste_low3` back as if it were
    already a direction.

    Recovered from `_GetDefendDir` (SIMTWO.SYM seg7:1026, args x=[bp+6],
    y=[bp+8], caste_low3=[bp+10]; FAR return).

    Yard-edge handling is `_Bounce`'s formula compiled inline again (same
    as `_GetNestDir`/`_GetAlarmDir`).

    Strictly interior: mode 2 calls `get_nest_dir(..., colony_flag=0x00)`;
    mode 3 calls `get_nest_dir(..., colony_flag=0x80)` — both are near-
    calls to `_GetNestDir`'s own address in the original ASM (this project's
    established near-call-to-far-retf bridge pattern), ported as ordinary
    Python calls. Any mode other than 1/2/3 returns `caste_low3` unchanged
    (a defensive no-op, presumably unreachable during normal play).

    Mode 1: if `pack[0x72EC] == 1`, calls `get_dir` toward a DGROUP-resident
    attack marker (`dgroup[0xAC7C]`/`[0xAC7E]`, each `>> 4` — a coarser
    coordinate system scaled down to the map grid) and uses that direction
    (or `caste_low3` if already there). Otherwise, checks the squared
    distance (`get_dis`, truncated to a signed word, matching the ASM's own
    `mov si,ax` truncation) from `(x, y)` to a PACK-resident target
    (`pack[0x9FE4]`/`[0x9FEA]`) against half of `pack[0x9E7A]`: close enough
    picks a `_SRand1(8)`-random direction; too far calls `get_dir` toward
    that same target directly (no RNG on this path).
    """
    from .simone import SRAND_SEED_OFF, srand1

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    def use_dir(dir_result: int) -> int:
        if dir_result == 0:
            return caste_low3
        return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + (dir_result - 1)))

    edge = bounce(dgroup, x, y)
    if edge != 0:
        return (edge - 1) & 7

    mode = dgroup.rw(0xCE80)
    if mode == 2:
        return get_nest_dir(dgroup, simant_data_group, x, y, caste_low3, 0x00)
    if mode == 3:
        return get_nest_dir(dgroup, simant_data_group, x, y, caste_low3, 0x80)
    if mode != 1:
        return caste_low3

    if pack.rw(0x72EC) == 1:
        target_x = _sx16(dgroup.rw(0xAC7C)) >> 4
        target_y = _sx16(dgroup.rw(0xAC7E)) >> 4
        return use_dir(get_dir(x, y, target_x, target_y))

    target_x = pack.rw(0x9FE4)
    target_y = pack.rw(0x9FEA)
    threshold_half = _sx16(pack.rw(0x9E7A)) >> 1
    dist = _sx16(get_dis(x, y, target_x, target_y) & 0xFFFF)
    if threshold_half >= dist:
        seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 8)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return use_dir(roll + 1)
    return use_dir(get_dir(x, y, target_x, target_y))


def get_red_defend_dir(dgroup, simant_data_group, pack, x: int, y: int,
                        caste_low3: int) -> int:
    """The red-colony-specific sibling of `get_defend_dir`: same overall
    shape (yard-edge `_Bounce`, mode 2/3 delegate to `get_nest_dir`, other
    modes echo `caste_low3`), but the mode selector and mode-1 target come
    from different, PACK-resident fields, and mode 1 has no
    `pack[0x72EC]`-style attack-marker alternative — it's always the
    distance-gated geometric branch.

    Recovered from `_GetRedDefendDir` (SIMTWO.SYM seg7:1194, args x=[bp+6],
    y=[bp+8], caste_low3=[bp+10]; FAR return). Mode comes from
    `pack[0x7606]` (not `dgroup[0xCE80]`); mode 1's target is
    `pack[0x80A6]`/`[0x80AC]` and its distance threshold is
    `pack[0xA08E]`, checked against `get_dis` the same truncated-signed-word
    way, with the same close-random/far-`get_dir` split (and the same "no
    RNG on the far path" asymmetry).
    """
    from .simone import SRAND_SEED_OFF, srand1

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    def use_dir(dir_result: int) -> int:
        if dir_result == 0:
            return caste_low3
        return sx8(simant_data_group.rb(0x24 + (caste_low3 << 3) + (dir_result - 1)))

    edge = bounce(dgroup, x, y)
    if edge != 0:
        return (edge - 1) & 7

    mode = pack.rw(0x7606)
    if mode == 2:
        return get_nest_dir(dgroup, simant_data_group, x, y, caste_low3, 0x00)
    if mode == 3:
        return get_nest_dir(dgroup, simant_data_group, x, y, caste_low3, 0x80)
    if mode != 1:
        return caste_low3

    target_x = pack.rw(0x80A6)
    target_y = pack.rw(0x80AC)
    threshold_half = _sx16(pack.rw(0xA08E)) >> 1
    dist = _sx16(get_dis(x, y, target_x, target_y) & 0xFFFF)
    if threshold_half >= dist:
        seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 8)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return use_dir(roll + 1)
    return use_dir(get_dir(x, y, target_x, target_y))


def do_dig_out_ant_a(dgroup, simant_data_group, pack, slot: int) -> None:
    """Resolve one tick of a yard ant "digging out" — aging/mode-transition,
    or a move (with a natural-decay kill chance) toward a `_Bounce`-biased or
    mode-table-random direction — the second top-level `_Do*Ant*` routine
    recovered, after `_DoFightA`.

    Recovered from `_DoDigOutAntA` (SIMANTW.SYM seg6:1480, NEAR call/return,
    arg: `slot`). Always picks a candidate direction: an `_SRand8`-plus-
    caste-low-3-bits index into an 8-row/8-caste mode table
    (`simant_data_group[0x24 + roll8 + ((caste & 7) << 3)]`), overridden by
    `bounce()` when the ant is at (or adjacent to) the yard edge.

    `sub = (caste & 0x78) >> 3` splits the routine in two:
    - `sub not in (5, 9)`: no movement at all — just a `get_new_mode`
      transition (written to THIS ant's own `field_c`, not an "acting ant"
      like `do_fight_a`) and a `field_e` clear. (The ASM computes the
      candidate new x/y before checking `sub`, then discards them on this
      path — the recovered version skips that dead computation.)
    - `sub in (5, 9)`: rolls `_SRand8()`; on a `0` (1-in-8, natural decay):
      the caste field is decremented by `0x18` in place (a slow aging-to-
      death clock, distinct from `_DoFightA`'s combat kill), stamped via
      `get_new_mode`/`field_c`/`field_e` same as above, and the decayed
      caste is written back into the yard life grid at the CURRENT
      position (no movement). On any other roll: reads the yard TILE map
      (not life grid) at the candidate position and compares it to
      `pack[0x7604]` (a diggability threshold); if the tile is too hard,
      OR the life grid there is already occupied, the ant doesn't move —
      it just rerolls a fresh mode-table direction and re-stamps its
      caste in place. Otherwise it moves: occupies the new life-grid cell,
      vacates the old one, updates its recorded position, and — if
      `field_e` (its carried-dirt counter) is nonzero — decrements it and
      jams the corresponding colony's NEST scent grid at the new position
      (`jam_scent_rn` for colony bit `0x80` set, else `jam_scent_bn`) with
      the decremented count.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def roll8() -> int:
        seed, r = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return r

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    a_x = simant_data_group.rb(0x23A4 + slot)
    a_y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)
    caste_masked = caste & 0xF8
    sub = (caste & 0x78) >> 3
    low3_shifted = (caste & 7) << 3

    dir_idx = sx8(simant_data_group.rb(0x24 + roll8() + low3_shifted))
    bounced = bounce(dgroup, a_x, a_y)
    if bounced != 0:
        dir_idx = (bounced - 1) & 7

    new_x = (a_x + sx8(simant_data_group.rb(dir_idx))) & 0xFF
    new_y = (a_y + sx8(simant_data_group.rb(8 + dir_idx))) & 0xFF

    if sub not in (5, 9):
        new_mode = get_new_mode(dgroup, simant_data_group, pack, sub, caste) & 0xFF
        simant_data_group.wb(0x2B78 + slot, new_mode)
        simant_data_group.wb(0x334C + slot, 0)
        return

    def stamp_in_place(new_caste: int) -> None:
        simant_data_group.wb(0x2F62 + slot, new_caste & 0xFF)
        dgroup.wb(LIFE_PLANE_BASE[0] + (a_x << 6) + a_y, new_caste & 0xFF)

    if roll8() == 0:
        decayed = (caste - 0x18) & 0xFF
        simant_data_group.wb(0x2F62 + slot, decayed)
        new_mode = get_new_mode(dgroup, simant_data_group, pack, sub, caste) & 0xFF
        simant_data_group.wb(0x2B78 + slot, new_mode)
        simant_data_group.wb(0x334C + slot, 0)
        dgroup.wb(LIFE_PLANE_BASE[0] + (a_x << 6) + a_y, decayed)
        return

    tile = dgroup.rb(MAP_PLANE_BASE[0] + (new_x << 6) + new_y)
    threshold = _sx16(pack.rw(0x7604))
    if tile > threshold:
        newdir = simant_data_group.rb(0x24 + roll8() + low3_shifted)
        stamp_in_place(caste_masked | newdir)
        return

    if dgroup.rb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y) != 0:
        newdir = simant_data_group.rb(0x24 + roll8() + low3_shifted)
        stamp_in_place(caste_masked | newdir)
        return

    moved_caste = caste_masked | dir_idx
    dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, moved_caste)
    simant_data_group.wb(0x2F62 + slot, moved_caste)
    dgroup.wb(LIFE_PLANE_BASE[0] + (a_x << 6) + a_y, 0)
    simant_data_group.wb(0x23A4 + slot, new_x)
    simant_data_group.wb(0x278E + slot, new_y)

    field_e = simant_data_group.rb(0x334C + slot)
    if field_e == 0:
        return
    field_e = (field_e - 1) & 0xFF
    simant_data_group.wb(0x334C + slot, field_e)
    if caste & 0x80:
        jam_scent_rn(simant_data_group, new_x, new_y, field_e)
    else:
        jam_scent_bn(simant_data_group, new_x, new_y, field_e)


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


def smooth_alarm(simant_data_group) -> None:
    """Blur the 64x32 half-res alarm grid (same grid as `alarm_here`, base
    `simant_data_group[0x52D2..)`) by one step of a 4-neighbour box filter,
    read-old/write-new: snapshots the whole grid into a scratch buffer at
    `simant_data_group[0x4AD2..)` first (the ASM's own scratch copy, mirrored
    here byte-for-byte though nothing else reads it), then for each cell sums
    the (up to 4) in-bounds orthogonal neighbours from the snapshot, computes
    `(4*center + sum) >> 3`, and stores 0 if that's <= 8 else the truncated
    byte into the live grid.  Out-of-bounds neighbours are omitted from the
    sum, not treated as zero -- and the divisor stays a fixed >>3 regardless
    of how many neighbours were in range, so edge/corner cells (fewer terms
    in the sum) decay faster than interior ones, even on a uniform field.

    Recovered from `_SmoothAlarm` (SIMANTW.SYM seg6:9380, no args).
    """
    base, scratch = 0x52D2, 0x4AD2
    snap = bytearray(0x800)
    for i in range(0x800):
        snap[i] = simant_data_group.rb(base + i)
        simant_data_group.wb(scratch + i, snap[i])
    for row in range(0, 0x800, 0x20):
        for col in range(0x20):
            idx = row + col
            total = 0
            if row > 0:
                total += snap[idx - 0x20]
            if col > 0:
                total += snap[idx - 1]
            if row < 0x7E0:
                total += snap[idx + 0x20]
            if col < 0x1F:
                total += snap[idx + 1]
            v = (4 * snap[idx] + total) >> 3
            simant_data_group.wb(base + idx, 0 if v <= 8 else v & 0xFF)


def kill_tail_r(dgroup, simant_data_group, ant_idx: int) -> None:
    """Remove a red-colony ant's tail segment from the sim — the twin of
    `kill_tail_b` on the red colony's per-ant fields and life plane 3.

    Recovered from `_KillTailR` (SIMANTW.SYM seg6:6762, arg: ant_idx).
    """
    simant_data_group.wb(0x46E6 + ant_idx, 0)
    x = simant_data_group.rb(0x42FA + ant_idx)
    y = simant_data_group.rw(0x4104 + ant_idx) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[3] + x + (y << 6), 0)


def flood_nest_b(dgroup) -> None:
    """Flood the black colony's nest map (plane 2): every cell whose tile is
    in the dirt-tile band (0x20..0x2D) is bumped by 0x31 into the flooded-dirt
    band (0x51..0x5E); every cell whose tile is a nest-food/floor tile
    (<=0x13) is replaced by the canonical hole tile (0x50).  Cells in
    0x14..0x1F or > 0x2D are left untouched.  Scans rows 0..63, columns
    3..63 (columns 0..2 are never touched).

    Recovered from `_FloodNestB` (SIMANTW.SYM seg5:29DA, no args).
    """
    base = MAP_PLANE_BASE[2]
    for row in range(0x40):
        row_base = base + (row << 6)
        for col in range(3, 0x40):
            off = row_base + col
            tile = dgroup.rb(off)
            if 0x20 <= tile <= 0x2D:
                dgroup.wb(off, (tile + 0x31) & 0xFF)
            elif tile <= 0x13:
                dgroup.wb(off, 0x50)


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
