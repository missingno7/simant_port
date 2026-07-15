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


def is_it_yellow(dgroup, pack, colony: int, x: int, y: int) -> int:
    """Whether the player's yellow ant occupies `(x, y)` on `colony`'s
    plane — gated on the current game mode matching `colony` at all.

    Recovered from `_IsItYellow` (SIMANTW.SYM seg5:96B6, args
    colony=[bp+6], x=[bp+8], y=[bp+10]; FAR return).  `colony == 0` is
    treated as `1` for the mode check ONLY (`dgroup[0xCE80]` must equal
    that, or the plane check below still uses the ORIGINAL `colony`,
    including `0`).  If `dgroup[0xCE80]` doesn't match, returns 0
    immediately — no tile read at all.

    Otherwise, `pack[0x9FE8] == 1` switches to a distance check instead
    of a tile read: only for `colony <= 1`, tests whether `(x, y)`
    (scaled to the SAME fixed-point cell-centre form as the attack
    marker, `(coord << 4) + 8`) is within squared distance `0x200` (512)
    of the RAW (un-scaled) marker at `dgroup[0xAC7C]`/`[0xAC7E]` (note:
    NOT the `>> 4` integer form `s_found_ant`/`get_defend_dir` use — this
    compares two fixed-point values directly); `colony > 1` returns 0.

    Otherwise (the common case), reads the life-plane tile at `(x, y)` on
    `LIFE_PLANE_BASE[colony]` and defers to `is_yellow_ant`.  `colony`
    outside `0..3` here reads uninitialized stack memory in the original
    binary (dead in practice — every established caller in this codebase
    uses `colony` in `0..3`) and is intentionally NOT modeled: `colony`
    out of `LIFE_PLANE_BASE`'s keys raises `KeyError` rather than
    guessing.
    """
    effective_colony = colony if colony != 0 else 1
    if dgroup.rw(0xCE80) != effective_colony:
        return 0

    if pack.rw(0x9FE8) == 1:
        if colony > 1:
            return 0
        x1 = ((x << 4) + 8) & 0xFFFF
        y1 = ((y << 4) + 8) & 0xFFFF
        dist = get_dis(x1, y1, dgroup.rw(0xAC7C), dgroup.rw(0xAC7E))
        return 1 if dist < 0x200 else 0

    tile = dgroup.rb(LIFE_PLANE_BASE[colony] + (x << 6) + y)
    return is_yellow_ant(tile)


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


def is_it_food_at(dgroup, pack, plane: int, x: int, y: int) -> int:
    """Whether the map tile at `(plane, x, y)` denotes food, after
    validating the coordinates and plane are in range.

    Recovered from `_IsItFoodAt` (SIMANTW.SYM seg5:5F7E, args plane=[bp+6],
    x=[bp+8], y=[bp+10]; FAR return).  Coordinate bounds depend on plane:
    plane<=1 (yard) allows `x` 0..0x7F, `y` 0..0x3F; plane>1 (nest) allows
    `x` 0..0x3F, `y` 0..0x3F.  Out-of-range coordinates OR a plane outside
    0..3 return 0 immediately (no tile read at all).  Otherwise reads the
    map tile at `MAP_PLANE_BASE[plane]` and tail-calls the already-
    recovered `is_this_food` (which composes `is_it_food`'s own
    world-state inside-nest flag read, here `pack[0x9B6E]`).
    """
    if plane <= 1:
        valid = 0 <= x <= 0x7F and 0 <= y <= 0x3F
    else:
        valid = 0 <= x <= 0x3F and 0 <= y <= 0x3F
    if not valid or not (0 <= plane <= 3):
        return 0
    tile = dgroup.rb(MAP_PLANE_BASE[plane] + (x << 6) + y)
    return is_this_food(plane, tile, pack.rw(0x9B6E) != 0)


def sg_i_rand(dgroup, n: int) -> int:
    """Return the LARGER of two independent `_SRand1(n)` rolls — a bias
    toward higher values.

    Recovered from `_SGIRand` (SIMANTW.SYM seg5:147C, arg n=[bp+6]; FAR
    return).  Rolls `_SRand1(n)` twice, threading the shared LFSR seed
    through both calls in order, and returns `max(roll1, roll2)`.
    """
    from .simone import SRAND_SEED_OFF, srand1
    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll1 = srand1(seed, n)
    seed, roll2 = srand1(seed, n)
    dgroup.ww(SRAND_SEED_OFF, seed)
    return max(roll1, roll2)


def sg_rand(dgroup, n: int) -> int:
    """Return the SMALLER of two independent `_SRand1(n)` rolls — the
    complementary bias to `sg_i_rand`.

    Recovered from `_SGRand` (SIMANTW.SYM seg5:14A4, arg n=[bp+6]; FAR
    return).  Same two-roll shape as `sg_i_rand`, `min` instead of `max`.
    """
    from .simone import SRAND_SEED_OFF, srand1
    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll1 = srand1(seed, n)
    seed, roll2 = srand1(seed, n)
    dgroup.ww(SRAND_SEED_OFF, seed)
    return min(roll1, roll2)


def sg_s_rand(dgroup, n: int) -> int:
    """Return the SMALLER of two independent `_SRand1(n)` rolls (same as
    `sg_rand`), then negate it half the time via a `_SRand2()` coin flip
    — a signed, symmetric-around-zero variant.

    Recovered from `_SGSRand` (SIMANTW.SYM seg5:14CC, arg n=[bp+6]; FAR
    return).  Consumes the shared LFSR seed 3 times in order: `_SRand1(n)`
    twice (for the min), then `_SRand2()` once (the sign roll).
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2
    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll1 = srand1(seed, n)
    seed, roll2 = srand1(seed, n)
    smaller = min(roll1, roll2)
    seed, sign_roll = srand_pow2(seed, 1)
    dgroup.ww(SRAND_SEED_OFF, seed)
    return -smaller if sign_roll != 0 else smaller


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


def is_valid_yard(x: int, y: int) -> int:
    """Whether (x, y) is a valid cell on the small 12x16 boy's-yard grid
    (a THIRD, smaller grid alongside `is_valid_a`'s 128x64 and
    `is_valid_b`'s 64x64).

    Recovered from `_IsValidYard` (SIMANTW.SYM seg7:2072): x must be
    0..0xB and y must be 0..0xF (signed compares).  Returns 1/0.
    """
    if not 0 <= x <= 0xB:
        return 0
    return 1 if 0 <= y <= 0xF else 0


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


def get_my_initial_rand_dir(dgroup, pack, plane: int, cur_x: int, cur_y: int,
                            tgt_x: int, tgt_y: int) -> int:
    """Commit a fresh sticky-direction search for `get_my_rand_dirs`,
    seeded from the straight-line compass direction to the target.

    Recovered from `_GetMyInitialRandDir` (SIMANTW.SYM seg6:8CDE, args
    plane=[bp+14], cur_x=[bp+16], cur_y=[bp+18], tgt_x=[bp+20],
    tgt_y=[bp+22]; FAR return — 4 leading stack words at `[bp+6..0xd]`
    are genuinely unused by this routine's body, likely present only for
    calling-convention uniformity with sibling routines).

    Initializes `get_my_rand_dirs`'s own PACK-resident output cells
    before calling it: `pack[0xA0D8]` (its "committed direction" cell,
    `out2`) to `get_dir(cur_x, cur_y, tgt_x, tgt_y) - 1`, and
    `pack[0x78A4]` (its "commitment mode" cell, `out1`) to `0` — a fresh
    "no prior commitment" state, forcing `get_my_rand_dirs`'s
    bidirectional sweep-from-this-direction path on this call.  Also
    always stamps `pack[0x72E4] = 0x10` (a new, unrelated field; not
    consumed by anything else already recovered).  Reads `pack[0x9B6E]`
    itself for `get_my_rand_dirs`'s "inside" flag — that routine's real
    ASM never takes it as a stack argument; every already-recovered
    caller in this chain (`get_my_best_dirs`, `check_my_best_dirs`)
    treats it as a caller-supplied convenience threading a world-state
    read, not a real parameter.  Returns `get_my_rand_dirs`'s own return
    value directly.
    """
    dir_minus_1 = (get_dir(cur_x, cur_y, tgt_x, tgt_y) - 1) & 0xFFFF
    pack.ww(0xA0D8, dir_minus_1)
    pack.ww(0x72E4, 0x10)
    pack.ww(0x78A4, 0)

    out1 = [pack.rw(0x78A4)]
    out2 = [pack.rw(0xA0D8)]
    inside = pack.rw(0x9B6E) != 0
    result = get_my_rand_dirs(dgroup, pack, out1, out2, inside, plane,
                              cur_x, cur_y, tgt_x, tgt_y)
    pack.ww(0x78A4, out1[0] & 0xFFFF)
    pack.ww(0xA0D8, out2[0] & 0xFFFF)
    return result


def get_my_next_rand_dirs(dgroup, pack, plane: int, x: int, y: int,
                          tgt_x: int, tgt_y: int) -> int:
    """Probe up to 64 steps of `get_my_best_dirs` ahead from `(x, y)`
    WITHOUT ever using the walked-to position for anything but deciding
    the final dispatch: if the walk hits a `-2` ("nothing clear at all")
    at any point, falls back to `get_my_rand_dirs` from the ORIGINAL
    `(x, y)`; otherwise (success throughout, or an ordinary `-1`
    failure) re-calls `get_my_best_dirs` from the ORIGINAL `(x, y)` one
    final time and returns THAT result directly — the walk only ever
    determines WHICH of the two routines gets the final say, never
    contributes its own answer.

    Recovered from `_GetMyNextRandDirs` (SIMANTW.SYM seg6:8BEA, args
    plane=[bp+6], x=[bp+8], y=[bp+10], tgt_x=[bp+12], tgt_y=[bp+14]; FAR
    return).  Composes the already-recovered `get_my_best_dirs` and
    `get_my_rand_dirs` (the latter via the SAME PACK-resident output
    cells `get_my_initial_rand_dir` uses: `pack[0x78A4]`/`[0xA0D8]`).

    Calls `get_my_best_dirs` once from `(x, y)`. If that fails
    immediately, the walk never starts (0 steps taken). Otherwise walks
    a SHADOW position forward: each successful `get_my_best_dirs` call
    advances the shadow by that direction's compass delta and
    continues (up to 64 total attempts); the first failure stops the
    walk early. After the walk, a LAST attempt result that's still
    non-negative (the walk ran the full 64 steps without ever failing)
    is forced to `-1` — only an actual failure mid-walk keeps its real
    value. Finally: `-2` specifically calls `get_my_rand_dirs(x, y,
    ...)`; anything else (including the forced `-1`) stamps
    `pack[0x72E4] = 0xFFFF` and calls `get_my_best_dirs(x, y, ...)` one
    more time — both are tail calls, returning THEIR result directly.
    """
    inside = pack.rw(0x9B6E) != 0

    si = get_my_best_dirs(dgroup, pack, inside, plane, x, y, tgt_x, tgt_y)
    step_count = 0

    if si >= 0:
        shadow_x = x + GET_BEST_DIR_DX[si]
        shadow_y = y + GET_BEST_DIR_DY[si]
        while step_count < 0x40:
            si = get_my_best_dirs(dgroup, pack, inside, plane, shadow_x,
                                  shadow_y, tgt_x, tgt_y)
            if si >= 0:
                shadow_x += GET_BEST_DIR_DX[si]
                shadow_y += GET_BEST_DIR_DY[si]
            step_count += 1
            if si < 0:
                break

    if si >= 0:
        si = -1

    if si == -2:
        out1 = [pack.rw(0x78A4)]
        out2 = [pack.rw(0xA0D8)]
        result = get_my_rand_dirs(dgroup, pack, out1, out2, inside, plane,
                                  x, y, tgt_x, tgt_y)
        pack.ww(0x78A4, out1[0] & 0xFFFF)
        pack.ww(0xA0D8, out2[0] & 0xFFFF)
        return result

    pack.ww(0x72E4, 0xFFFF)
    return get_my_best_dirs(dgroup, pack, inside, plane, x, y, tgt_x, tgt_y)


def get_my_best_dir(dgroup, pack, plane: int, x: int, y: int, tgt_x: int,
                    tgt_y: int) -> int:
    """Entry point that first checks a "stuck" sentinel (`pack[0x72E4]`,
    the SAME field `get_my_next_rand_dirs`/`get_my_initial_rand_dir`
    write) before falling into the exact same probe-and-dispatch walk
    `get_my_next_rand_dirs` performs.

    Recovered from `_GetMyBestDir` (SIMANTW.SYM seg6:8D3A, args
    plane=[bp+6], cur_x=[bp+8], cur_y=[bp+10], tgt_x=[bp+12],
    tgt_y=[bp+14]; FAR return).  Composes
    the already-recovered `get_my_best_dirs`, `get_dir`, and
    `get_my_rand_dirs`.  The "normal" path (`pack[0x72E4] >= 0`, signed)
    is compiler-duplicated but algorithmically IDENTICAL to
    `get_my_next_rand_dirs`'s own body (confirmed field-for-field:
    same `_GetMyBestDirs`/`_GetMyRandDirs` near-call targets, same
    `pack[0x78A4]`/`[0xA0D8]` out1/out2 cells, same 64-step bound, same
    forced-`-1`-on-success and `pack[0x72E4]=0xFFFF`-on-tail writes) —
    reused directly here rather than re-implementing a byte-identical
    copy, EXCEPT for one extra step every path through it takes here
    that `get_my_next_rand_dirs` itself does NOT have: an unconditional
    `pack[0x72E4] -= 1` at the very end (confirmed only by getting a
    state-diff mismatch on the very first test run and re-reading the
    disassembly past where the earlier hand-derivation had stopped —
    the real ASM has one more instruction, `dec es:[bx]`, after what
    looked like the routine's natural end).

    When `pack[0x72E4] < 0` instead (a "stuck" sentinel from a PREVIOUS
    call): retries `get_my_best_dirs` once from the ORIGINAL `(x, y)`.
    If that ISN'T `-2` (nothing clear), returns it as-is. If it IS `-2`
    but `pack[0x72E4]` no longer equals `-2` (state moved on since the
    sentinel was set), also just returns `-2` as-is. Only when BOTH are
    `-2` (double-confirmed still stuck) does it commit a fresh
    `get_my_rand_dirs` search — the EXACT same "commit" sequence
    `get_my_initial_rand_dir` performs (`pack[0xA0D8] = get_dir(...)-1`,
    `pack[0x72E4] = 0x10`, `pack[0x78A4] = 0`, then `get_my_rand_dirs`) —
    this branch does NOT get the extra `pack[0x72E4] -= 1` step; that
    only happens on the "normal path" above.
    """
    inside = pack.rw(0x9B6E) != 0

    if _sx16(pack.rw(0x72E4)) >= 0:
        result = get_my_next_rand_dirs(dgroup, pack, plane, x, y, tgt_x, tgt_y)
        pack.ww(0x72E4, (pack.rw(0x72E4) - 1) & 0xFFFF)
        return result

    result = get_my_best_dirs(dgroup, pack, inside, plane, x, y, tgt_x, tgt_y)
    if result != -2:
        return result
    if pack.rw(0x72E4) != 0xFFFE:
        return result

    direction = (get_dir(x, y, tgt_x, tgt_y) - 1) & 0xFFFF
    pack.ww(0xA0D8, direction)
    pack.ww(0x72E4, 0x10)
    pack.ww(0x78A4, 0)

    out1 = [pack.rw(0x78A4)]
    out2 = [pack.rw(0xA0D8)]
    result = get_my_rand_dirs(dgroup, pack, out1, out2, inside, plane,
                              x, y, tgt_x, tgt_y)
    pack.ww(0x78A4, out1[0] & 0xFFFF)
    pack.ww(0xA0D8, out2[0] & 0xFFFF)
    return result


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


def find_in_lion_list(pack, simant_data_group, val0: int, val1: int) -> int:
    """Search the antlion list backward for the last-added slot whose two
    recorded fields match `(val0, val1)` — `(x, y)`, per `set_ant_lion`'s
    own use of the SAME two arrays.

    Recovered from `_FindInLionList` (SIMANTW.SYM seg7:4B12, args
    val0=[bp+6], val1=[bp+8]; FAR return). The live slot count is read
    from `simant_data_group[0x8A88]` (NOT `pack`, unlike the sibling
    `find_in_a_list`/`find_in_b_list`/`find_in_r_list` searches, which
    all read their count from `pack`); the per-slot fields are parallel
    byte arrays in PACK instead (accessed via a hardcoded `0x5EF3`
    segment literal in the real ASM — independently confirmed to equal
    the PACK selector, not SIMANT_DATA_GROUP's): `pack[0x809C + slot]
    == val0` (x), `pack[0x80BC + slot] == val1` (y). Unlike
    `find_in_a_list`, there is no third nonzero-field gate. Search
    order is backward (highest slot first). Returns the found 0-based
    slot index, or `0xFFFF` if none match.
    """
    count = simant_data_group.rw(0x8A88)
    for slot in range(count - 1, -1, -1):
        if pack.rb(0x809C + slot) == val0 and pack.rb(0x80BC + slot) == val1:
            return slot
    return 0xFFFF


def set_ant_lion(dgroup, pack, slot: int) -> None:
    """Re-stamp an antlion's pit tile onto the yard map at its own
    recorded position.

    Recovered from `_SetAntLion` (SIMANTW.SYM seg7:4AD8, arg
    slot=[bp+6]; FAR return). Composes the already-recovered `set_map`.
    Reads the SAME per-slot PACK arrays `find_in_lion_list` searches —
    `pack[0x809C + slot]` (x), `pack[0x80BC + slot]` (y) — plus a THIRD
    per-slot growth/type byte at `pack[0x7D4E + slot]`, and writes
    `set_map(plane=1, x, y, value=that type byte + 0x38)`.
    """
    x = pack.rb(0x809C + slot)
    y = pack.rb(0x80BC + slot)
    value = (pack.rb(0x7D4E + slot) + 0x38) & 0xFF
    set_map(dgroup, 1, x, y, value)


def find_ant_index(pack, simant_data_group, colony: int, field0: int,
                   field1: int, caste: int) -> int:
    """Generalized reverse-linear list search across all three colonies,
    dispatching purely on `colony` and matching THREE fields per slot
    (unlike `find_in_a_list`'s two-field-plus-nonzero-check, or
    `find_in_b_list`/`find_in_r_list`'s plain three-field match — this is
    effectively a colony-dispatching sibling of the latter two, reusing
    their exact per-slot field bases).

    Recovered from `_FindAntIndex` (SIMANTW.SYM seg5:59FC, args
    colony=[bp+6], field0=[bp+8], field1=[bp+10], caste=[bp+0xc]; FAR
    return).  `colony<=1` selects the yard A-list (count `pack[0x80F0]`,
    fields `[0x23A4]`/`[0x278E]`/`[0x2F62]`); `colony==2` selects the
    B-list (count `pack[0x99D4]`, `[0x3736]`/`[0x392C]`/`[0x3D18]`);
    anything else selects the R-list (count `pack[0x72CC]`,
    `[0x4104]`/`[0x42FA]`/`[0x46E6]`) — the SAME arrays
    `find_in_a_list`/`find_in_b_list`/`find_in_r_list` use.  Searches
    backward from the last slot; returns the matching slot, or 0xFFFF if
    the list is empty or exhausted without a match.
    """
    if colony <= 1:
        count = pack.rw(0x80F0)
        f0_base, f1_base, c_base = 0x23A4, 0x278E, 0x2F62
    elif colony == 2:
        count = pack.rw(0x99D4)
        f0_base, f1_base, c_base = 0x3736, 0x392C, 0x3D18
    else:
        count = pack.rw(0x72CC)
        f0_base, f1_base, c_base = 0x4104, 0x42FA, 0x46E6

    for slot in range(count - 1, -1, -1):
        if (simant_data_group.rb(f0_base + slot) == field0
                and simant_data_group.rb(f1_base + slot) == field1
                and simant_data_group.rb(c_base + slot) == caste):
            return slot
    return 0xFFFF


def find_life_index(pack, simant_data_group, list_type: int, field0: int,
                    field1: int, lo: int, hi: int, mask: int) -> int:
    """A `find_ant_index` variant: matches `field0`/`field1` exactly, but
    instead of an exact caste match, requires `lo <= (caste & mask) <=
    hi` — a RANGE check on a masked caste sub-field (e.g. the same kind
    of `(caste & 0x78) >> 3` "mode" extraction `recruit_red` uses, here
    left as a caller-supplied mask/range rather than a fixed shift).

    Recovered from `_FindLifeIndex` (SIMANTW.SYM seg5:5922, args
    list_type=[bp+6], field0=[bp+8], field1=[bp+10], lo=[bp+12],
    hi=[bp+14], mask=[bp+16]; FAR return).  Same list dispatch and
    per-slot field bases as `find_ant_index`.  Searches backward from the
    last slot; returns the matching slot, or 0xFFFF if the list is empty
    or exhausted without a match.
    """
    if list_type <= 1:
        count = pack.rw(0x80F0)
        f0_base, f1_base, c_base = 0x23A4, 0x278E, 0x2F62
    elif list_type == 2:
        count = pack.rw(0x99D4)
        f0_base, f1_base, c_base = 0x3736, 0x392C, 0x3D18
    else:
        count = pack.rw(0x72CC)
        f0_base, f1_base, c_base = 0x4104, 0x42FA, 0x46E6

    for slot in range(count - 1, -1, -1):
        if simant_data_group.rb(f0_base + slot) != field0:
            continue
        if simant_data_group.rb(f1_base + slot) != field1:
            continue
        masked = simant_data_group.rb(c_base + slot) & mask
        if lo <= masked <= hi:
            return slot
    return 0xFFFF


def find_life_at(pack, simant_data_group, dgroup, list_type: int, x: int,
                 y: int) -> tuple:
    """Locate whatever ant occupies `(x, y)` on `list_type`'s life-plane,
    trusting a direct tile read when possible and falling back to a
    list search otherwise. Returns `(slot, caste)`, or `(0xFFFF,
    0xFFFF)` if nothing is found either way.

    Recovered from `_FindLifeAt` (SIMANTW.SYM seg5:8A96, args
    OUT_slot_ptr=[bp+6] (far pointer — ported as the first element of
    the returned tuple instead), list_type=[bp+10], x=[bp+12],
    y=[bp+14]; FAR return).  Composes the already-recovered
    `is_yellow_ant`, `find_ant_index`, `find_life_index`, and
    `get_ant_index`.

    If `(x, y)` is valid for `list_type`'s bounds AND `list_type` is in
    `0..3`, reads the life-plane tile there directly. A tile of `0`
    (empty) or one that IS the player's yellow-ant sentinel is NOT
    trusted — same "trust the encoded value UNLESS it's the yellow-ant
    marker" idiom the `_LostHead*` family already established.

    A trusted direct tile: looks up its exact slot via
    `find_ant_index(list_type, x, y, tile)` (returning `0xFFFF` if not
    found) and returns `(slot, tile)` regardless — no further fallback
    is attempted once a real tile was read.

    Otherwise (invalid position, empty cell, or the yellow-ant marker):
    falls back to `find_life_index(list_type, x, y, lo=1, hi=0x7F,
    mask=0x7F)` (any nonzero low-7-bits caste); on a match, fetches its
    full record via `get_ant_index` and returns `(slot, caste)`; on no
    match, returns `(0xFFFF, 0xFFFF)`.
    """
    if list_type <= 1:
        valid = 0 <= x <= 0x7F and 0 <= y <= 0x3F
    else:
        valid = 0 <= x <= 0x3F and 0 <= y <= 0x3F

    tile = None
    if valid and 0 <= list_type <= 3:
        raw = dgroup.rb(LIFE_PLANE_BASE[list_type] + (x << 6) + y)
        if raw != 0 and is_yellow_ant(raw) == 0:
            tile = raw

    if tile is not None:
        slot = find_ant_index(pack, simant_data_group, list_type, x, y, tile)
        return (slot, tile)

    slot = find_life_index(pack, simant_data_group, list_type, x, y, 1, 0x7F, 0x7F)
    if slot == 0xFFFF:
        return (0xFFFF, 0xFFFF)
    result = get_ant_index(pack, simant_data_group, list_type, slot)
    return (slot, result[2])


def find_egg_at(pack, simant_data_group, dgroup, list_type: int, x: int,
                y: int) -> tuple:
    """The egg/larva-specific twin of `find_life_at` — same shape, but
    ALSO requires the tile's `(caste & 0x7F)` to be in `1..7` (the egg/
    larva growth-stage range `sim_egg_b`/`r` operate on) before trusting
    a direct read, and narrows the list-fallback range to that SAME
    `1..7` band instead of `find_life_at`'s general `1..0x7F`.

    Recovered from `_FindEggAt` (SIMANTW.SYM seg5:88A2, args
    OUT_slot_ptr=[bp+6], list_type=[bp+10], x=[bp+12], y=[bp+14]; FAR
    return).  Confirmed a genuine, narrowly-scoped twin by independent
    disassembly — everything else (bounds check, yellow-ant distrust,
    `find_ant_index`/`find_life_index`/`get_ant_index` composition)
    matches `find_life_at` field-for-field.
    """
    tile = None
    if list_type <= 1:
        valid = 0 <= x <= 0x7F and 0 <= y <= 0x3F
    else:
        valid = 0 <= x <= 0x3F and 0 <= y <= 0x3F

    if valid and 0 <= list_type <= 3:
        raw = dgroup.rb(LIFE_PLANE_BASE[list_type] + (x << 6) + y)
        if raw != 0 and 1 <= (raw & 0x7F) <= 7 and is_yellow_ant(raw) == 0:
            tile = raw

    if tile is not None:
        slot = find_ant_index(pack, simant_data_group, list_type, x, y, tile)
        return (slot, tile)

    slot = find_life_index(pack, simant_data_group, list_type, x, y, 1, 7, 0x7F)
    if slot == 0xFFFF:
        return (0xFFFF, 0xFFFF)
    result = get_ant_index(pack, simant_data_group, list_type, slot)
    return (slot, result[2])


def s_found_ant(dgroup, simant_data_group, pack) -> int:
    """Locate an ant near the current attack-marker target
    (`dgroup[0xAC7C]`/`[0xAC7E]`, the SAME fixed-point `>>4` target
    `get_defend_dir`/`scan_for_ants` use), dispatched on `pack[0x7D60]`'s
    exact value.

    Recovered from `_SFoundAnt` (SIMANTW.SYM seg5:53F6, NO args; FAR
    return).  Composes the already-recovered `get_dis`, `is_valid_a`,
    `is_yellow_ant`, and `find_ant_index`.

    `pack[0x7D60]==7`: searches the yard A-list backward for an ant
    within squared distance `0x320` (800) of the target; returns the
    first (highest-slot) match.  If none found (or the list is empty),
    falls back to a fixed marker position (`dgroup[0xCD88]`/`[0xCE7E]`)
    ONLY when `pack[0x9FE8]==0` AND `dgroup[0xCE80]==1`: returns `0xFFFF`
    if that marker is within the same range, else `0xFFFE` throughout.

    Any other `pack[0x7D60]` value: instead walks up to 20 steps outward
    from the target along a FIXED compass direction
    (`simant_data_group[dir_idx]`/`[8+dir_idx]` where
    `dir_idx = dgroup[0xAC80]` — the SAME compass tables
    `sim_queen_a`/`make_blk_queen` use), each step requiring `is_valid_a`
    and squared distance `<= 0x190` (400) from the target, or the whole
    search fails (`0xFFFE`).  An empty yard-life cell there just advances
    to the next step.  An occupied cell that IS the player's yellow ant
    (`is_yellow_ant`) aborts the search immediately, returning `0xFFFF`.
    Otherwise looks the occupant up via `find_ant_index(colony=1, x, y,
    tile)`: a confirmed A-list match returns THAT slot directly; no match
    just advances.  Exhausting all 20 steps returns `0xFFFE`.
    """
    target_x = _sx16(dgroup.rw(0xAC7C)) >> 4
    target_y = _sx16(dgroup.rw(0xAC7E)) >> 4

    if pack.rw(0x7D60) == 7:
        count = pack.rw(0x80F0)
        for slot in range(count - 1, -1, -1):
            if simant_data_group.rb(0x2F62 + slot) == 0:
                continue
            slot_x = simant_data_group.rb(0x23A4 + slot)
            slot_y = simant_data_group.rb(0x278E + slot)
            if get_dis(target_x, target_y, slot_x, slot_y) <= 0x320:
                return slot

        if pack.rw(0x9FE8) != 0 or dgroup.rw(0xCE80) != 1:
            return 0xFFFE
        marker_x = dgroup.rw(0xCD88)
        marker_y = dgroup.rw(0xCE7E)
        if get_dis(target_x, target_y, marker_x, marker_y) <= 0x320:
            return 0xFFFF
        return 0xFFFE

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    dir_idx = dgroup.rw(0xAC80)
    dx = sx8(simant_data_group.rb(dir_idx))
    dy = sx8(simant_data_group.rb(8 + dir_idx))

    x, y = target_x, target_y
    for _ in range(0x14):
        y += dy
        x += dx
        if not is_valid_a(x, y):
            return 0xFFFE
        if get_dis(target_x, target_y, x, y) > 0x190:
            return 0xFFFE
        tile = dgroup.rb(LIFE_PLANE_BASE[0] + (x << 6) + y)
        if tile == 0:
            continue
        if is_yellow_ant(tile) != 0:
            return 0xFFFF
        found = find_ant_index(pack, simant_data_group, 1, x, y, tile)
        if _sx16(found) >= 0:
            return found
    return 0xFFFE


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


def get_ant_index(pack, simant_data_group, list_type: int, slot: int):
    """Read an EXISTING ant record's fields at `slot` — the read
    counterpart of `set_ant_index`.

    Recovered from `_GetAntIndex` (SIMANTW.SYM seg5:573C, args
    list_type=[bp+6], slot=[bp+8], plus 5 far-pointer OUT params at
    `[bp+0xa..0x1a]` the real ASM writes target0/target1/caste/field_c/
    field_e through one at a time — ported as a returned tuple instead
    of output pointers, since Python has no equivalent calling
    convention).  Same list dispatch and field layout as `set_ant_index`.
    Returns `(target0, target1, caste, field_c, field_e)` on success, or
    `None` when `slot` is out of range (`0 <= slot < count`, signed).
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
        return None
    return (
        simant_data_group.rb(f0 + slot),
        simant_data_group.rb(f1 + slot),
        simant_data_group.rb(caste_off + slot),
        simant_data_group.rb(fc_off + slot),
        simant_data_group.rb(fe_off + slot),
    )


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


def can_be_house_hole(dy: int) -> int:
    """Look up the yard tile ID for a "house" (ant-hill mound) entrance at
    row-offset `dy`, or `0` if `dy` isn't a valid house-hole row.

    Recovered from `_CanBeHouseHole` (SIMANTW.SYM seg5:1CBA, FAR return,
    arg: dy=[bp+6]) — a pure constant lookup, no calls at all. `dy in
    (0, 2, 3)` and `dy in (0x66, 0x68)` each map to a specific fixed tile;
    `0x5E <= dy < 0x62` maps to `dy + 0x22`; everything else is `0`.
    """
    if dy == 0:
        return 0x86
    if dy in (2, 3):
        return 0x8A
    if 0x5E <= dy < 0x62:
        return (dy + 0x22) & 0xFFFF
    if dy == 0x66:
        return 0x85
    if dy == 0x68:
        return 0x84
    return 0


def hole_border(dgroup, simant_data_group, x: int, y: int) -> None:
    """Border a newly-placed yard hole at `(x, y)`: for each of the 8
    compass neighbors, if its current tile is "soft" (`< 0x50`), overwrite
    it with a direction-specific border tile.

    Recovered from `_HoleBorder` (SIMANTW.SYM seg5:1F8E, FAR return, args
    x=[bp+6], y=[bp+8]) — no calls at all, just the standard compass delta
    tables (SIMANT_DATA_GROUP-resident) plus the SAME `HOLE_EDGE_TILES`
    constant table `_MakeNewHoleB`/`R` already use — a genuinely direct
    DGROUP read (`dgroup[0x230C..)`, no SIMANT_DATA_GROUP pointer-global
    indirection), confirmed by re-checking for an `ES:` override on that
    specific instruction rather than assuming it matched the nearby
    SIMANT_DATA_GROUP-prefixed compass-table reads.
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    for i in range(8):
        ny = y + sx8(simant_data_group.rb(8 + i))
        nx = x + sx8(simant_data_group.rb(i))
        if not (0 <= nx <= 0x7F and 0 <= ny <= 0x3F):
            continue
        idx = MAP_PLANE_BASE[0] + (nx << 6) + ny
        if dgroup.rb(idx) < 0x50:
            dgroup.wb(idx, HOLE_EDGE_TILES[i])


def get_from_a_list(dgroup, simant_data_group, pack, colony_bit: int) -> int:
    """Find and remove the last (highest-slot) yard ant of the given colony
    (`colony_bit`: caste's top bit, 0 or 1), searching backward.

    Recovered from `_GetFromAlist` (SIMANTW.SYM seg5:2FFE, FAR return, arg:
    colony_bit=[bp+6]). Skips dead/empty slots (`caste == 0`). Only
    callee: the already-recovered `remove_from_a_list`.

    A genuine quirk, ported literally rather than "fixed": if the search
    lands on slot `0` — whether because THAT slot matched, or because the
    list was exhausted without a match — the function returns `0` (as if
    nothing were found) and does NOT remove anything. Slot 0 itself can
    therefore never actually be returned as a match.
    """
    si = pack.rw(0x80F0)
    while si > 0:
        si -= 1
        caste = simant_data_group.rb(0x2F62 + si)
        if caste != 0 and (caste >> 7) == colony_bit:
            break

    if si == 0:
        return 0
    remove_from_a_list(pack, simant_data_group, dgroup, si)
    return 1


def build_ant_list_a(dgroup, simant_data_group, pack) -> None:
    """Rebuild the entire yard A-list from scratch by scanning the whole
    128x64 yard life plane — likely used on a load/restore or scenario-init
    pass where only the grid is authoritative and the ant-list metadata
    needs reconstructing.

    Recovered from `_BuildAntListA` (SIMANTW.SYM seg5:3046, FAR return, NO
    args). Only callee: the already-recovered `is_yellow_ant`.

    Resets `pack[0x80F0]` (the count) to `0`, then for every occupied cell
    (nonzero life-plane byte) that ISN'T a yellow-ant tile
    (`is_yellow_ant(tile) != 1`), appends a new A-list entry: `x`/`y` from
    the scan position, `field_c` hardcoded to `2`, `caste` set to the
    tile's OWN byte value (the life-plane byte doubles as the caste here),
    `field_e` cleared. A genuine silent cap at `0x3E5` (997) entries,
    ported literally: once the count reaches that cap it stops advancing,
    so any further matching cells keep overwriting slot `997` instead of
    appending — the caller is trusted not to have that many yard ants.
    """
    pack.ww(0x80F0, 0)
    count = 0
    for x in range(0x80):
        for y in range(0x40):
            tile = dgroup.rb(LIFE_PLANE_BASE[0] + (x << 6) + y)
            if tile == 0:
                continue
            if is_yellow_ant(tile) == 1:
                continue
            simant_data_group.wb(0x23A4 + count, x)
            simant_data_group.wb(0x278E + count, y)
            simant_data_group.wb(0x2B78 + count, 2)
            simant_data_group.wb(0x2F62 + count, tile)
            simant_data_group.wb(0x334C + count, 0)
            if count < 0x3E5:
                count += 1
                pack.ww(0x80F0, count)


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


def _pickup_food_br(dgroup, pack, map_base: int, food_count_off: int, x: int,
                    y: int) -> None:
    """Shared body of `pickup_food_b`/`r`: like `_try_eat_food` (gated on
    the tile being in `[0x10, 0x13]`, same reroll-or-decrement step), but
    with no colony-growth trigger tail at all.
    """
    idx = map_base + (x << 6) + y
    tile = dgroup.rb(idx)
    if not (0x10 <= tile <= 0x13):
        return
    _reroll_or_decrement_food_tile(dgroup, idx)
    if _sx16(pack.rw(food_count_off)) > 0:
        pack.ww(food_count_off, (pack.rw(food_count_off) - 1) & 0xFFFF)


def pickup_food_b(dgroup, pack, x: int, y: int) -> None:
    """The black-colony NEST-map sibling of `pickup_food_a` (which operates
    on the YARD map instead).

    Recovered from `_PickupFoodB` (SIMANTW.SYM seg5:0F40, FAR return, args
    x=[bp+6], y=[bp+8]). See `_pickup_food_br`.
    """
    _pickup_food_br(dgroup, pack, MAP_PLANE_BASE[2], 0x9EA4, x, y)


def pickup_food_r(dgroup, pack, x: int, y: int) -> None:
    """The red-colony twin of `pickup_food_b`.

    Recovered from `_PickupFoodR` (SIMANTW.SYM seg5:0FA2, FAR return, args
    x=[bp+6], y=[bp+8]).
    """
    _pickup_food_br(dgroup, pack, MAP_PLANE_BASE[3], 0x72DE, x, y)


def _place_egg(dgroup, simant_data_group, pack, count_off: int, dig_tile,
               add_list, life_plane_base: int, x: int, y: int, caste: int) -> None:
    """Shared body of `place_egg_b`/`r`: place a new egg at `(x, y)` — dig
    the tile there, append a new list record, and stamp the caste onto
    the life plane — unless the colony's list is already at its 500-slot
    cap, or `(x, y)` is out of bounds (`0 <= x <= 0x3F`, `1 <= y <= 0x3F`
    — note `y` excludes `0`, unlike `x`).

    `add_list`'s own `(y, x)` argument order genuinely takes THIS
    function's `x` into its `y` slot and vice versa — the same
    coordinate-role swap already seen in `_QueenMoveB`/`R`'s ant-list
    writes, ported as a literal positional pass-through rather than
    "corrected".
    """
    if pack.rw(count_off) >= 0x1F4:
        return
    if not (0 <= x <= 0x3F and 1 <= y <= 0x3F):
        return
    dig_tile(dgroup, simant_data_group, pack, x, y)
    add_list(pack, simant_data_group, dgroup, x, y, caste, 8, 0)
    dgroup.wb(life_plane_base + (x << 6) + y, caste & 0xFF)


def place_egg_b(dgroup, simant_data_group, pack, x: int, y: int, caste: int) -> None:
    """Recovered from `_PlaceEggB` (SIMANTW.SYM seg5:1004, FAR return, args
    x=[bp+6], y=[bp+8], caste=[bp+10]). See `_place_egg`.
    """
    _place_egg(dgroup, simant_data_group, pack, 0x99D4, dig_tile_b,
              add_ant_to_b_list, LIFE_PLANE_BASE[2], x, y, caste)


def place_egg_r(dgroup, simant_data_group, pack, x: int, y: int, caste: int) -> None:
    """The red-colony twin of `place_egg_b`.

    Recovered from `_PlaceEggR` (SIMANTW.SYM seg5:1068, FAR return, args
    x=[bp+6], y=[bp+8], caste=[bp+10]).
    """
    _place_egg(dgroup, simant_data_group, pack, 0x72CC, dig_tile_r,
              add_ant_to_r_list, LIFE_PLANE_BASE[3], x, y, caste)


def scan_for_ants(dgroup) -> int:
    """Count occupied yard life-plane cells in the 3x3 block around
    `(dgroup[0xAC7C] >> 4, dgroup[0xAC7E] >> 4)`.

    Recovered from `_ScanForAnts` (SIMANTW.SYM seg5:5362, FAR return, NO
    args) — no calls at all, a pure double-loop scan. Out-of-bounds
    neighbors (`x` outside `0..0x7F`, `y` outside `0..0x3F`) are simply
    skipped, not treated as occupied.
    """
    base_x = _sx16(dgroup.rw(0xAC7C)) >> 4
    base_y = _sx16(dgroup.rw(0xAC7E)) >> 4
    count = 0
    for ox in range(-1, 2):
        for oy in range(-1, 2):
            nx, ny = base_x + ox, base_y + oy
            if not (0 <= nx <= 0x7F and 0 <= ny <= 0x3F):
                continue
            if dgroup.rb(LIFE_PLANE_BASE[0] + (nx << 6) + ny) != 0:
                count += 1
    return count


def _make_new_tail(dgroup, simant_data_group, pack, add_list, caste_off: int,
                   x_off: int, y_off: int, slot: int) -> None:
    """Shared body of `make_new_tail_b`/`r`: append a trailing tail segment
    one step BEHIND `slot` (the OPPOSITE of its own facing direction,
    `caste & 7` XOR `4`), with a `caste + 8` "tail" caste, `field_c=9`,
    `field_e=0`. Pure DGROUP table math — no calls beyond `add_list`.

    `add_list`'s own `(y, x)` argument order again takes the DX-table
    delta added to the ant's OWN y-field into its `y` slot and the
    DY-table delta added to the OWN x-field into its `x` slot — the same
    swapped convention already seen in `_QueenMoveB`/`R`/`_PlaceEggB`/`R`,
    ported as a literal positional pass-through.
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    caste = simant_data_group.rb(caste_off + slot)
    dir_idx = (caste & 7) ^ 4
    dy_val = sx8(simant_data_group.rb(8 + dir_idx))
    dx_val = sx8(simant_data_group.rb(dir_idx))
    new_y = dx_val + simant_data_group.rb(y_off + slot)
    new_x = dy_val + simant_data_group.rb(x_off + slot)
    add_list(pack, simant_data_group, dgroup, new_y, new_x, caste + 8, 9, 0)


def make_new_tail_b(dgroup, simant_data_group, pack, slot: int) -> None:
    """Recovered from `_MakeNewTailB` (SIMANTW.SYM seg6:424A, FAR return,
    arg: slot=[bp+6]). See `_make_new_tail`.
    """
    _make_new_tail(dgroup, simant_data_group, pack, add_ant_to_b_list,
                   0x3D18, 0x392C, 0x3736, slot)


def make_new_tail_r(dgroup, simant_data_group, pack, slot: int) -> None:
    """The red-colony twin of `make_new_tail_b`.

    Recovered from `_MakeNewTailR` (SIMANTW.SYM seg6:66FC, FAR return, arg:
    slot=[bp+6]).
    """
    _make_new_tail(dgroup, simant_data_group, pack, add_ant_to_r_list,
                   0x46E6, 0x42FA, 0x4104, slot)


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


def make_blk_queen(dgroup, simant_data_group, pack, x: int, y: int, direction: int) -> None:
    """Carve a founding black queen's chamber: dig her own tile plus two
    farther cells along the compass opposite her facing, then drop two
    black ant-list records marking the chamber, and bump a black-queen
    counter.

    Recovered from `_MakeBlkQueen` (SIMANTW.SYM seg7:671A, args x=[bp+6],
    y=[bp+8], direction=[bp+10]; FAR return).  Composes `dig_tile_b`
    (seg5:1FE4, called 3x) and `add_ant_to_b_list` (seg5:2F4A, called 2x).

    Digs `(x, y)` itself, then `(x, y)` offset by 1x and 2x the compass
    delta for `direction ^ 4` (the OPPOSITE compass direction), reading
    the SAME `simant_data_group` compass tables `sim_queen_a` uses (dx at
    `[dir]`, dy at `[dir+8]`).  Then appends two ant-list records: one at
    the caller's own `(x, y)` with `caste = direction + 0x60` (per the
    established coordinate-role-swap convention — the caller's x lands in
    the list's y slot and vice versa), and one at the 1x-offset cell with
    `caste = direction + 0x68` (the SAME "+0x68" caste-encoding constant
    `queen_move_b`'s own transform uses). Both records use `field_c=9`,
    `field_e=0`. Finally increments `pack[0x78E8]` (a black-queen counter)
    unconditionally.
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    dig_tile_b(dgroup, simant_data_group, pack, x, y)

    dir_idx = direction ^ 4
    dx = sx8(simant_data_group.rb(dir_idx))
    dy = sx8(simant_data_group.rb(8 + dir_idx))
    dig_tile_b(dgroup, simant_data_group, pack, x + dx, y + dy)
    dig_tile_b(dgroup, simant_data_group, pack, x + 2 * dx, y + 2 * dy)

    add_ant_to_b_list(pack, simant_data_group, dgroup, y=x, x=y,
                       caste=direction + 0x60, field_c=9, field_e=0)
    add_ant_to_b_list(pack, simant_data_group, dgroup, y=x + dx, x=y + dy,
                       caste=direction + 0x68, field_c=9, field_e=0)

    pack.ww(0x78E8, (pack.rw(0x78E8) + 1) & 0xFFFF)


def make_red_queen(dgroup, simant_data_group, pack, x: int, y: int, direction: int) -> None:
    """The red-colony twin of `make_blk_queen` — same shape, red arrays and
    a different caste-encoding constant pair.

    Recovered from `_MakeRedQueen` (SIMANTW.SYM seg7:6906, args x=[bp+6],
    y=[bp+8], direction=[bp+10]; FAR return).  Composes `dig_tile_r`
    (seg5:21DE, called 3x) and `add_ant_to_r_list` (seg5:2FA4, called 2x).
    Confirmed a genuine twin by independent disassembly, not assumed:
    same dig-then-list-twice shape, but `caste = direction + 0xE0` /
    `direction + 0xE8` (not `+0x60`/`+0x68`) and the final counter is a
    DIFFERENT PACK field, `pack[0x79DC]`.
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    dig_tile_r(dgroup, simant_data_group, pack, x, y)

    dir_idx = direction ^ 4
    dx = sx8(simant_data_group.rb(dir_idx))
    dy = sx8(simant_data_group.rb(8 + dir_idx))
    dig_tile_r(dgroup, simant_data_group, pack, x + dx, y + dy)
    dig_tile_r(dgroup, simant_data_group, pack, x + 2 * dx, y + 2 * dy)

    add_ant_to_r_list(pack, simant_data_group, dgroup, y=x, x=y,
                       caste=direction + 0xE0, field_c=9, field_e=0)
    add_ant_to_r_list(pack, simant_data_group, dgroup, y=x + dx, x=y + dy,
                       caste=direction + 0xE8, field_c=9, field_e=0)

    pack.ww(0x79DC, (pack.rw(0x79DC) + 1) & 0xFFFF)


def place_red_queen(dgroup, simant_data_group, pack) -> None:
    """Carve a tunnel from deep in the red nest up toward the surface and
    found a red queen at its far end — the scenario-init/no-args sibling of
    `make_red_queen` (which takes an already-chosen position; this routine
    SEARCHES for one via a random walk, then inlines the same
    dig-plus-two-list-records tail).

    Recovered from `_PlaceRedQueen` (SIMANTW.SYM seg7:67DA, NO args; FAR
    return).  Composes `dig_tile_r` (seg5:21DE, called up to 15x) and
    `add_ant_to_r_list` (seg5:2FA4, called 2x); consumes `_SRand4()` once
    and `_SRand1(3)` up to 9 times from the shared LFSR seed.  Verified
    against an instrumented real-ASM trace of every `_AddAntToRList` call's
    actual arguments — a hand-derivation from the disassembly alone missed
    a hardcoded `+2` on x buried in a `lea ax,[si+2]` between the SDG
    scratch-store and the compass-offset digs; the trace caught it directly.

    - Rolls `_SRand4() + 7` (7..10) as a row count, then digs a wandering
      vertical tunnel from `(x=0x20, y=1)` up to `y=count-1`: each step
      digs the current cell, then nudges `x` by `_SRand1(3)-1` (-1, 0, or
      +1), keeping the nudge only if it stays within `8..0x38` (otherwise
      the wander holds its position for that step).
    - Digs 2 more cells stepping diagonally (`x+=1, y+=1` each time), then
      ONE more at the final diagonal position — that final `(x, y)` is
      recorded into SIMANT_DATA_GROUP scratch fields `[0x8366]`/`[0x8368]`
      (the red-colony analogue of `make_new_hole_b`'s black-side
      `[0x835A]`/`[0x835C]` "last placed" record).
    - Digs ONE more cell at `x+2` (same y) — a genuinely separate
      hardcoded offset, not the compass table — and from THAT bumped `x`
      (not the original), digs 2 more cells offset by 1x and 2x the FIXED
      compass-direction-6 delta (`simant_data_group[0x06]`/`[0x0E]` — not
      caller-supplied, unlike `make_red_queen`'s direction parameter).
    - Appends two ant-list records, also anchored on the bumped `x+2`
      (per the coordinate-role-swap convention), with LITERAL castes
      `0xE2`/`0xEA` (not a `direction + constant` formula, since there's
      no direction parameter here) and the SAME `field_c=9`, `field_e=0`
      as `make_red_queen`.
    - Finally increments the SAME `pack[0x79DC]` red-queen counter
      `make_red_queen` uses.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll4 = srand_pow2(seed, 3)
    count = roll4 + 7

    x = 0x20
    for i in range(1, count):
        dig_tile_r(dgroup, simant_data_group, pack, x, i)
        seed, roll3 = srand1(seed, 3)
        nx = x + (roll3 - 1)
        if 8 <= nx <= 0x38:
            x = nx
    y = count
    dgroup.ww(SRAND_SEED_OFF, seed)

    for _ in range(2):
        dig_tile_r(dgroup, simant_data_group, pack, x, y)
        x += 1
        y += 1
    dig_tile_r(dgroup, simant_data_group, pack, x, y)

    simant_data_group.ww(0x8366, x & 0xFFFF)
    simant_data_group.ww(0x8368, y & 0xFFFF)

    x2 = x + 2
    dig_tile_r(dgroup, simant_data_group, pack, x2, y)

    dy6 = sx8(simant_data_group.rb(0x0E))
    dx6 = sx8(simant_data_group.rb(0x06))

    dig_tile_r(dgroup, simant_data_group, pack, x2 + dx6, y + dy6)
    dig_tile_r(dgroup, simant_data_group, pack, x2 + 2 * dx6, y + 2 * dy6)

    add_ant_to_r_list(pack, simant_data_group, dgroup, y=x2, x=y,
                       caste=0xE2, field_c=9, field_e=0)
    add_ant_to_r_list(pack, simant_data_group, dgroup, y=x2 + dx6, x=y + dy6,
                       caste=0xEA, field_c=9, field_e=0)

    pack.ww(0x79DC, (pack.rw(0x79DC) + 1) & 0xFFFF)


def place_black_queen(dgroup, simant_data_group, pack) -> None:
    """Carve a tunnel from deep in the black nest up toward the surface
    and found a black queen at its far end — the black-colony sibling of
    `place_red_queen`, same overall shape but a GENUINELY DIFFERENT
    wander mechanism (confirmed against an instrumented real-ASM trace,
    not just a hand-derivation): each step first rolls `_SRand2()`, and
    only on a `0` (50/50) does it reroll the x-drift via `_SRand1(3)-1`
    — on the OTHER 50% of steps the drift is NOT reset to 0, it STAYS
    at whatever value it last rolled (a genuinely STICKY/persistent
    drift across iterations, initialized to `0` only once before the
    loop starts) — `_PlaceRedQueen` instead rerolls a fresh drift every
    single step. A hand-derivation from the disassembly alone assumed
    the drift reset to 0 each non-reroll step; an instrumented trace of
    the real ASM's register values caught the mistake directly (`di`
    held a nonzero, unchanging drift across consecutive steps that
    never re-entered the reroll branch).

    Recovered from `_PlaceBlackQueen` (SIMANTW.SYM seg7:65CE, NO args;
    FAR return).  Composes `dig_tile_b` (up to 15x) and
    `add_ant_to_b_list` (2x).

    - Rolls `_SRand4() + 7` (7..10) as a row count, then digs a
      wandering vertical tunnel from `(x=0x20, y=1)` up to `y=count-1`:
      each step digs the current cell, rolls `_SRand2()`, and only on a
      `0` further rolls `_SRand1(3)-1` to REPLACE the sticky drift
      (otherwise the drift carries over unchanged from the previous
      step); the drift is applied only if it keeps `x` within
      `8..0x38`.
    - Digs 2 more cells stepping diagonally, then ONE more at the final
      diagonal position — that final `(x, y)` is recorded into BOTH
      `simant_data_group[0x8362]`/`[0x8364]` (the black-colony analogue
      of `_PlaceRedQueen`'s `[0x8366]`/`[0x8368]`) AND `pack[0x9FEC]`/
      `[0x9FEE]` (a PACK-resident pair `_PlaceRedQueen` does NOT write
      at all — a genuine extra step, not just a renamed field).
    - Digs ONE more cell at `x+2` (same y, a hardcoded offset), then
      from that bumped `x`, digs 2 more cells offset by 1x and 2x the
      FIXED compass-direction-6 delta.
    - Appends two ant-list records anchored on the bumped `x+2`, with
      LITERAL castes `0x62`/`0x6A` (the SAME low bits as
      `_PlaceRedQueen`'s `0xE2`/`0xEA`, just the colony bit cleared).
    - Finally increments the SAME `pack[0x78E8]` black-queen counter
      `make_blk_queen` uses.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll4 = srand_pow2(seed, 3)
    count = roll4 + 7

    x = 0x20
    drift = 0
    for i in range(1, count):
        dig_tile_b(dgroup, simant_data_group, pack, x, i)
        seed, roll2 = srand_pow2(seed, 1)
        if roll2 == 0:
            seed, roll3 = srand1(seed, 3)
            drift = roll3 - 1
        nx = x + drift
        if 8 <= nx <= 0x38:
            x = nx
    y = count
    dgroup.ww(SRAND_SEED_OFF, seed)

    for _ in range(2):
        dig_tile_b(dgroup, simant_data_group, pack, x, y)
        x += 1
        y += 1
    dig_tile_b(dgroup, simant_data_group, pack, x, y)

    simant_data_group.ww(0x8362, x & 0xFFFF)
    simant_data_group.ww(0x8364, y & 0xFFFF)
    pack.ww(0x9FEC, x & 0xFFFF)
    pack.ww(0x9FEE, y & 0xFFFF)

    x2 = x + 2
    dig_tile_b(dgroup, simant_data_group, pack, x2, y)

    dy6 = sx8(simant_data_group.rb(0x0E))
    dx6 = sx8(simant_data_group.rb(0x06))

    dig_tile_b(dgroup, simant_data_group, pack, x2 + dx6, y + dy6)
    dig_tile_b(dgroup, simant_data_group, pack, x2 + 2 * dx6, y + 2 * dy6)

    add_ant_to_b_list(pack, simant_data_group, dgroup, y=x2, x=y,
                      caste=0x62, field_c=9, field_e=0)
    add_ant_to_b_list(pack, simant_data_group, dgroup, y=x2 + dx6, x=y + dy6,
                      caste=0x6A, field_c=9, field_e=0)

    pack.ww(0x78E8, (pack.rw(0x78E8) + 1) & 0xFFFF)


def _add_ants(dgroup, simant_data_group, pack, count: int, x_range, caste_bonus: int) -> None:
    """Shared body of `add_black_ants`/`add_red_ants`: scan the yard for
    empty walkable cells in a fixed `y=0x10..0x2F` middle band, and for
    each one found, roll a random caste and drop a scenario-init yard ant
    there via `add_ant_to_a_list` — both colonies' initial ants go into
    the SAME yard A-list, distinguished only by `caste_bonus`'s `0x80`
    colony bit.  Stops after `count` ants are placed or the A-list hits
    its `0x3E8` (1000) global cap, whichever comes first.

    A cell qualifies when the yard map tile is `< 0x50` (walkable) AND
    the yard life-plane cell is `0` (unoccupied).  The caste roll: a
    `_SRand1(10)` pick of `<=3` (4-in-10) uses base `0x30`/`field_c=2`,
    otherwise (6-in-10) base `0x10`/`field_c=4`; either way a `_SRand8()`
    (0..7) is added, plus `caste_bonus`.  Also directly stamps the caste
    onto the yard life plane before calling `add_ant_to_a_list` — the
    same "redundant but faithful" re-stamp `sim_egg_a`/`sim_queen_a` do
    (that call re-stamps the identical cell internally).
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    seed = dgroup.rw(SRAND_SEED_OFF)
    for x in x_range:
        for y in range(0x10, 0x30):
            offset = (x << 6) + y
            if dgroup.rb(MAP_PLANE_BASE[0] + offset) >= 0x50:
                continue
            if dgroup.rb(LIFE_PLANE_BASE[0] + offset) != 0:
                continue

            seed, roll10 = srand1(seed, 10)
            if roll10 <= 3:
                base, field_c = 0x30, 2
            else:
                base, field_c = 0x10, 4
            seed, roll8 = srand_pow2(seed, 7)
            caste = (roll8 + base + caste_bonus) & 0xFF

            dgroup.wb(LIFE_PLANE_BASE[0] + offset, caste)
            add_ant_to_a_list(pack, simant_data_group, dgroup, x, y, caste, field_c, 0)

            count -= 1
            if count <= 0 or pack.rw(0x80F0) >= 0x3E8:
                dgroup.ww(SRAND_SEED_OFF, seed)
                return
    dgroup.ww(SRAND_SEED_OFF, seed)


def add_black_ants(dgroup, simant_data_group, pack, count: int) -> None:
    """Populate the yard with up to `count` scenario-init black ants,
    scanning the LEFT half (`x=0..0x3F`) of the yard.

    Recovered from `_AddBlackAnts` (SIMANTW.SYM seg7:6C5A, arg count=[bp+6]
    (decremented in place, not returned); FAR return).  Composes the
    shared `_add_ants` helper with `caste_bonus=0` — geographically the
    black-colony (left-side-nest) half of the yard, `_AddRedAnts`'s twin
    scanning the right half.
    """
    _add_ants(dgroup, simant_data_group, pack, count, range(0, 0x40), 0)


def add_red_ants(dgroup, simant_data_group, pack, count: int) -> None:
    """Populate the yard with up to `count` scenario-init red ants,
    scanning the RIGHT half (`x=0x7F..0x40`, descending) of the yard.

    Recovered from `_AddRedAnts` (SIMANTW.SYM seg7:6CFE, arg count=[bp+6];
    FAR return).  Confirmed a genuine twin by independent disassembly:
    identical cell-qualify/caste-roll logic and the SAME shared
    `_SRand1(10)`/`_SRand8()` thresholds as `_AddBlackAnts`, but scans
    `x` from `0x7F` DOWN to `0x40` (the yard's other half) and adds
    `0x80` to every caste (the colony bit) — both colonies' ants land in
    the SAME yard A-list (`_AddAntToAList`), not a B/R list.
    """
    _add_ants(dgroup, simant_data_group, pack, count, range(0x7F, 0x3F, -1), 0x80)


def un_recruit_red(pack, simant_data_group) -> None:
    """Clear the "recruited" flag off every red yard ant.

    Recovered from `_UnRecruitRed` (SIMANTW.SYM seg7:08DA, NO args; FAR
    return).  Scans the yard A-list backward; for every red-colony ant
    (`caste > 0x7F`) whose `field_c` (`simant_data_group[0x2B78+slot]`)
    is exactly `6` (the "recruited" marker `recruit_red` stamps), clears
    it back to `0`.
    """
    count = pack.rw(0x80F0)
    for slot in range(count - 1, -1, -1):
        caste = simant_data_group.rb(0x2F62 + slot)
        if caste == 0 or caste <= 0x7F:
            continue
        if simant_data_group.rb(0x2B78 + slot) == 6:
            simant_data_group.wb(0x2B78 + slot, 0)


def recruit_red(pack, simant_data_group, count: int) -> None:
    """Mark up to `count` red yard ants as "recruited" for a task.

    Recovered from `_RecruitRed` (SIMANTW.SYM seg7:0866, arg count=[bp+6];
    FAR return; the companion `_UnRecruitRed` undoes this).  Scans the
    yard A-list backward; a slot qualifies when its caste is red
    (`>0x7F`), its caste's `(caste & 0x78) >> 3` "mode" sub-field is `2`
    or `6`, and its CURRENT `field_c` is neither `0x13` nor `6` (not
    already recruited/reserved).  Each qualifying slot gets `field_c=6`
    and `field_e` (`simant_data_group[0x334C+slot]`) cleared, counting
    down; stops as soon as `count` reaches 0 or the list is exhausted —
    the check happens BEFORE every slot, including the first, so
    `count<=0` is a pure no-op.
    """
    list_count = pack.rw(0x80F0)
    remaining = count
    for slot in range(list_count - 1, -1, -1):
        if remaining <= 0:
            break
        caste = simant_data_group.rb(0x2F62 + slot)
        if caste == 0 or caste <= 0x7F:
            continue
        field_c = simant_data_group.rb(0x2B78 + slot)
        mode = (caste & 0x78) >> 3
        if mode not in (2, 6):
            continue
        if field_c in (0x13, 6):
            continue
        simant_data_group.wb(0x2B78 + slot, 6)
        simant_data_group.wb(0x334C + slot, 0)
        remaining -= 1


def get_new_red_task(dgroup, simant_data_group, pack) -> None:
    """Reassign the red colony's recruitment task: either a chance-gated
    "raid" recruitment sized off a fixed PACK target, or a fallback
    "general" recruitment sized off a running population estimate.

    Recovered from `_GetNewRedTask` (SIMANTW.SYM seg6:9940, NO args; FAR
    return).  Always starts by clearing every red ant's "recruited"
    marker via the already-recovered `un_recruit_red`.

    If `dgroup[0xCE80] == 1` (a specific game mode): rolls
    `_SRand1(32) + 64` and requires it `< dgroup[0xCD88]`; if so, rolls
    `_SRand1(10)` and requires it `< pack[0x9E7A]` too — both gates
    passing sets `pack[0x9D74] = 2` (a "raid" task marker) and calls the
    already-recovered `recruit_red(pack[0x9E7A])`, returning immediately.

    Otherwise (mode isn't 1, or either gate failed): recomputes two
    PACK-resident running estimates from SIMANT_DATA_GROUP fields
    (`pack[0x9C22] = simant_data_group[0x836C]`, clamped back toward
    `20..40` by `+-5` if it drifted outside; `pack[0x9BEE] =
    simant_data_group[0x836A]`, capped by `-5` once it exceeds `30`),
    then recruits a count derived from `dgroup[0xACA2] + dgroup[0xACA4]`
    (`>> 2` if that sum is `< 20`, else `>> 3`) via `recruit_red`, and
    sets `pack[0x9D74] = 1` (a "general" task marker).
    """
    from .simone import SRAND_SEED_OFF, srand1

    un_recruit_red(pack, simant_data_group)

    if dgroup.rw(0xCE80) == 1:
        seed = dgroup.rw(SRAND_SEED_OFF)
        seed, roll32 = srand1(seed, 32)
        if roll32 + 64 < dgroup.rw(0xCD88):
            seed, roll10 = srand1(seed, 10)
            dgroup.ww(SRAND_SEED_OFF, seed)
            if roll10 < pack.rw(0x9E7A):
                pack.ww(0x9D74, 2)
                recruit_red(pack, simant_data_group, pack.rw(0x9E7A))
                return
        else:
            dgroup.ww(SRAND_SEED_OFF, seed)

    pack.ww(0x9C22, simant_data_group.rw(0x836C))
    val_9bee = simant_data_group.rw(0x836A)
    if val_9bee > 30:
        val_9bee -= 5
    pack.ww(0x9BEE, val_9bee & 0xFFFF)

    val_9c22 = pack.rw(0x9C22)
    if val_9c22 < 20:
        val_9c22 += 5
    elif val_9c22 > 40:
        val_9c22 -= 5
    pack.ww(0x9C22, val_9c22 & 0xFFFF)

    si = dgroup.rw(0xACA2) + dgroup.rw(0xACA4)
    count = (si >> 2) if si < 20 else (si >> 3)
    recruit_red(pack, simant_data_group, count)
    pack.ww(0x9D74, 1)


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


def leave_nest_b(dgroup, simant_data_group, pack, col: int, x: int) -> int:
    """Try to send the current black ant (`pack[0x9B6A]`'s slot) out
    through an above-ground hole at `col`, carving a fresh one via
    `make_new_hole_b` first if none is tracked yet for that column.

    Recovered from `_LeaveNestB` (SIMANTW.SYM seg6:515E, args col=[bp+6],
    x=[bp+8]; FAR return).  Composes the already-recovered
    `make_new_hole_b` and `exit_hole`.

    Clears the slot's caste field (`simant_data_group[0x3D18+slot]`) to
    `0` up front (a "claim this slot" marker, restored on failure).  If
    `_FillHolesBN`'s per-column tracking array
    (`simant_data_group[0x82D2+col]`) has no hole recorded yet, calls
    `make_new_hole_b(col)` to carve one.  Then rerolls a fresh caste —
    `_SRand8() + (original_caste & 0xF8)` (keeping the caste's high bits,
    replacing only the low 3 direction bits) — and calls `exit_hole` at
    `(x=simant_data_group[0x82D2+col], y=col)` with that caste and the
    slot's OWN `field_c`/`field_e` as the appended A-list entry's fields.

    On success (`exit_hole` returns nonzero): clears the black nest
    life-grid cell at `(col, x)` (plane 2) and returns `1`.  On failure:
    restores the slot's ORIGINAL caste and clears its `field_c` to `0`
    (undoing the "claim"), and returns `0`.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    slot = pack.rw(0x9B6A)
    orig_caste = simant_data_group.rb(0x3D18 + slot)
    simant_data_group.wb(0x3D18 + slot, 0)

    if simant_data_group.rb(0x82D2 + col) == 0:
        make_new_hole_b(dgroup, simant_data_group, pack, col)

    slot = pack.rw(0x9B6A)
    field_e = simant_data_group.rb(0x3F0E + slot)
    field_c = simant_data_group.rb(0x3B22 + slot)
    seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    new_caste = (roll8 + (orig_caste & 0xF8)) & 0xFF
    hole_row = simant_data_group.rb(0x82D2 + col)

    result = exit_hole(dgroup, simant_data_group, pack, hole_row, col,
                       new_caste, field_c, field_e)
    if result != 0:
        dgroup.wb(LIFE_PLANE_BASE[2] + (col << 6) + x, 0)
        return 1

    slot = pack.rw(0x9B6A)
    simant_data_group.wb(0x3D18 + slot, orig_caste & 0xFF)
    simant_data_group.wb(0x3B22 + slot, 0)
    return 0


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


def create_new_hole(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """Stamp a fresh hole marker on the shared yard map at (x, y) and dig
    the connecting nest tunnel — the low-level primitive both
    `make_new_hole_b`/`make_new_hole_r`'s own callers ultimately reach
    for, and `_DigMyNewHole`'s own composed tail.

    Recovered from `_CreateNewHole` (SIMANTW.SYM seg5:171A, args x=[bp+6],
    y=[bp+8]; FAR return, 506 bytes).  No-ops entirely unless `1 <= x <=
    0x7E` and `1 <= y <= 0x3E` (independently confirmed via the raw
    `jge`/`jl` pairs, NOT the ostensibly-symmetric `1 <= x/y <= 0x7F/0x3F`
    that `_DigMyNewHole`'s OWN, looser gate uses — the two gates are
    genuinely different by one, and `_DigMyNewHole` still calls this
    routine even when its own tighter check would reject the coordinate,
    relying on `_CreateNewHole`'s own gate to silently no-op).

    - `pack[0x9B6E]` ("inside the nest") NONZERO: stamps the yard map cell
      to `0x59`.
    - ZERO (outside): stamps it to `0x50` instead, then carves the SAME
      `HOLE_EDGE_TILES` 8-neighbour edge pattern `make_new_hole_b`/`r` use
      (compass deltas read from the SAME fixed SIMANT_DATA_GROUP table
      those routines' own `sbyte(0/8 + di)` reads reach — independently
      confirmed here via a THIRD access path, through DGROUP
      pointer-globals rather than a literal segment override, all
      resolving to the identical SIMANT_DATA_GROUP selector) into any
      in-bounds neighbour whose CURRENT tile is `< 0x50`.  Either way,
      execution then reconverges onto the SAME dispatch below (a genuine
      `jmp` back into the "inside" branch's own code) — the inside/outside
      split only changes what gets stamped/carved above; the colony
      dispatch is common to both.

    - `x < 0x40` ("black" territory): records `simant_data_group[0x82D2 +
      y] = x` (the SAME `_FillHolesBN` per-column array `make_new_hole_b`
      writes), composes `dig_tile_b(x=y, y=1)` — note the coordinate
      SWAP, independently re-derived from scratch: the near call at
      seg5:176F-1774 pushes `(1, y)` in the established last-pushed-first
      convention, so `_CreateNewHole`'s own `y` argument becomes
      `dig_tile_b`'s `x`, and the literal `1` becomes its `y`; this is
      genuinely NOT a call on `_CreateNewHole`'s own `(x, y)` pair — then
      records `(x, y)` into the SAME "last black hole" 4-word scratch
      `make_new_hole_b` uses (`[0x835A]`=x, `[0x835C]`=y, `[0x8352]`=y
      again, `[0x8354]`=0), and returns immediately (this branch never
      reaches the `x >= 0x40` code below).
    - `x >= 0x40` ("red" territory): records `simant_data_group[0x8312 +
      y] = x` (`_FillHolesRN`'s array), then runs a block that is
      BYTE-IDENTICAL to `dig_tile_r`'s own entire body called as
      `dig_tile_r(x=y, y=1)` (the SAME coordinate swap as the black
      branch above, independently confirmed field-by-field: the reroll
      gate's `_IsItDirt`/`_SRand8` calls, the `_acc_add32`-style
      accumulators at `pack[0x9DDC]`/`[0x9DE2]`, the counter at
      `pack[0x7A56]`, the two `__aFldiv` averages into `pack[0x9FBA]`/
      `[0x9FD2]`, and all four `_SmoothEdgesR` + one `_FixExitMapR` call
      at the SAME `(y, 1)` neighbourhood the reroll targeted — composed
      here as `dig_tile_r(dgroup, simant_data_group, pack, y, 1)` rather
      than re-derived), then records `(x, y)` into the "last red hole"
      4-word scratch (`[0x835E]`=x, `[0x8360]`=y, `[0x8356]`=y again,
      `[0x8358]`=0) and returns.
    """
    if not (1 <= x <= 0x7E):
        return
    if not (1 <= y <= 0x3E):
        return

    inside = pack.rw(0x9B6E) != 0
    idx = (x << 6) + y

    if inside:
        dgroup.wb(MAP_PLANE_BASE[0] + idx, 0x59)
    else:
        dgroup.wb(MAP_PLANE_BASE[0] + idx, 0x50)

        def sbyte(off):
            v = simant_data_group.rb(off)
            return v - 0x100 if v & 0x80 else v

        for di in range(8):
            nx = sbyte(0 + di) + x
            ny = sbyte(8 + di) + y
            if not (0 <= nx <= 0x7F and 0 <= ny <= 0x3F):
                continue
            off = MAP_PLANE_BASE[0] + (nx << 6) + ny
            if dgroup.rb(off) < 0x50:
                dgroup.wb(off, HOLE_EDGE_TILES[di])

    if x < 0x40:
        simant_data_group.wb(0x82D2 + y, x & 0xFF)
        dig_tile_b(dgroup, simant_data_group, pack, y, 1)
        simant_data_group.ww(0x835A, x)
        simant_data_group.ww(0x835C, y)
        simant_data_group.ww(0x8352, y)
        simant_data_group.ww(0x8354, 0)
    else:
        simant_data_group.wb(0x8312 + y, x & 0xFF)
        dig_tile_r(dgroup, simant_data_group, pack, y, 1)
        simant_data_group.ww(0x835E, x)
        simant_data_group.ww(0x8360, y)
        simant_data_group.ww(0x8356, y)
        simant_data_group.ww(0x8358, 0)


def dig_my_new_hole(dgroup, simant_data_group, pack, x: int, y: int) -> int:
    """Whether (x, y) is a clear enough spot for a new above-ground hole —
    and, if so, actually creates one via `create_new_hole`.

    Recovered from `_DigMyNewHole` (SIMANTW.SYM seg5:16AE, args x=[bp+6],
    y=[bp+8]; FAR return, 108 bytes).  No-ops (returns 0) unless `1 <= x
    <= 0x7F` and `1 <= y <= 0x3F` — a looser gate than `create_new_hole`'s
    own `0x7E`/`0x3E` upper bounds, confirmed genuinely asymmetric rather
    than assumed; a coordinate that passes THIS gate but fails
    `create_new_hole`'s own can still result in a silent no-op there.

    `pack[0x9B6E]` ("inside the nest") NONZERO: clear iff the yard map
    tile at (x, y) is `< 0xC8`.  ZERO (outside): clear iff the real
    `_IsClear3x3` predicate holds for the whole 3x3 block on plane 1 —
    composed here via the already-recovered `_clear_3x3(dgroup, 1, x,
    y)` helper (independently confirmed via the near-call's push order:
    `push y; push x; push 1`, i.e. `plane=1` is the LAST-pushed / first
    formal argument, matching `_IsClear3x3`'s own established `(plane, x,
    y)` signature).  Not clear: returns 0 immediately.  Clear: composes
    `create_new_hole(x, y)` and returns 1.
    """
    if not (1 <= x <= 0x7F):
        return 0
    if not (1 <= y <= 0x3F):
        return 0

    inside = pack.rw(0x9B6E) != 0
    if inside:
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        clear = 1 if tile < 0xC8 else 0
    else:
        clear = _clear_3x3(dgroup, 1, x, y)

    if clear != 1:
        return 0

    create_new_hole(dgroup, simant_data_group, pack, x, y)
    return clear


def _is_it_digable_at(dgroup, plane: int, x: int, y: int) -> int:
    """Read-the-map wrapper around the pure `is_it_digable(plane, tile)`
    predicate — the VM-touching counterpart, matching `_clear_3x3`'s own
    precedent.  `plane < 2` short-circuits to 0 with no map read at all
    (confirmed via `_IsItDigable`'s own island residue notes in
    `hooks.py`); an out-of-range `(plane, x, y)` (including any `plane >
    3`, since `map_cell_offset` has no base for those) also reads as 0.
    """
    if plane < 2:
        return 0
    off = map_cell_offset(plane, x, y)
    if off is None:
        return 0
    return is_it_digable(plane, dgroup.rb(off))


def dig_my_tile(dgroup, simant_data_group, pack, plane: int, x: int, y: int) -> None:
    """Dig the nest tile in front of the current ant, gated by
    `_IsItDigable` — the colony-dispatching orchestrator that composes
    `make_new_hole_b`/`make_new_hole_r` (row 0/1 only) and `dig_tile_b`/
    `dig_tile_r` (the actual reroll), plus one genuinely surprising extra
    call.

    Recovered from `_DigMyTile` (SIMANTW.SYM seg5:1914, args plane=[bp+6],
    x=[bp+8], y=[bp+10]; FAR return, 498 bytes).  Calls exactly 9 distinct
    routines (independently re-confirmed via `symbols.nearest_symbol` on
    every call site, not assumed from an earlier survey): `_IsItDigable`,
    `_MakeNewHoleB`, `_DigTileB`, `_MakeNewHoleR`, `_IsItDirt`, `_SRand8`
    (now composed via `srand_pow2(seed, 7)` inside `dig_tile_r`'s own
    `_dig_tile_reroll_and_track` helper — the LAST of the 9 to gain
    coverage this session), `__aFldiv`, `_SmoothEdgesR`, `_FixExitMapR`.

    Gated by `_is_it_digable_at(plane, x, y)`; not digable is a silent
    no-op (no state changes at all).

    - `plane == 2` ("black"): if `y <= 1`, unconditionally stamps
      `MAP_PLANE_BASE[2] + (x << 6)` (row 0 of column x — NOT `(x, y)`,
      confirmed via the raw address arithmetic: no `y` term is ever added)
      to `0x18`, and composes `make_new_hole_b(col=x)`; if `y != 1`
      (i.e. `y == 0`), returns immediately without digging anything
      further. Otherwise (`y == 1`, or the original `y > 1` skipping the
      row-0 prelude entirely): composes `dig_tile_b(x, y)` — a direct,
      UNSWAPPED pass-through of this routine's own `(x, y)`, confirmed
      genuinely different from `create_new_hole`'s own swapped
      `dig_tile_b(y, 1)` call — then returns immediately via a `jmp` that
      lands EXACTLY on `_FixExitMapR`'s own call site's return address
      (seg5:1961 -> 1AFF, the byte right after the `call near 2914`
      instruction at 1AFC) — i.e. it deliberately SKIPS the entire
      `_SmoothEdgesR`x4 + `_FixExitMapR` closing sequence below, not an
      extra call on top of `dig_tile_b`.  A first pass misread this jump
      target as landing BEFORE the call (composing a spurious extra
      `fix_exit_map_r`); re-disassembling the exact byte range
      (seg5:1AF8-1B05) and comparing the jump target against the call's
      own `ret=` annotation caught it immediately via a real-ASM state
      diff (one stray SDG byte at `_FixExitMapR`'s own target address
      that the real ASM never touched).
    - `plane != 2` ("red" / anything else that passed the digability
      gate): the SAME `y <= 1` row-0 prelude, but on `MAP_PLANE_BASE[3]`
      and composing `make_new_hole_r(col=x)` instead; same `y == 0`
      early-return.  The remainder — reached for `y == 1` after the
      prelude, OR directly for any `y > 1` — is BYTE-IDENTICAL to
      `dig_tile_r`'s own entire body (independently confirmed field by
      field: same reroll/`_IsItDirt`/`_SRand8` gate, same accumulator and
      average-position fields, same 4x `_SmoothEdgesR` + one
      `_FixExitMapR` closing sequence, at this routine's own unswapped
      `(x, y)`), so it composes `dig_tile_r(x, y)` directly rather than
      re-deriving the reroll/track/smooth logic a second time.
    """
    if not _is_it_digable_at(dgroup, plane, x, y):
        return

    if plane == 2:
        if y <= 1:
            dgroup.wb(MAP_PLANE_BASE[2] + (x << 6), 0x18)
            make_new_hole_b(dgroup, simant_data_group, pack, x)
            if y != 1:
                return
        dig_tile_b(dgroup, simant_data_group, pack, x, y)
        return

    if y <= 1:
        dgroup.wb(MAP_PLANE_BASE[3] + (x << 6), 0x18)
        make_new_hole_r(dgroup, simant_data_group, pack, x)
        if y != 1:
            return
    dig_tile_r(dgroup, simant_data_group, pack, x, y)


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


def _dig_out_nest(dgroup, simant_data_group, pack, count: int, dig_tile_fn,
                  dig_tile_them_fn, make_new_hole_fn, hole_track_off: int) -> None:
    """Shared body of `dig_out_b_nest`/`dig_out_r_nest`: carve a wandering
    tunnel up from a fixed starting cell, one `count`-bounded step at a
    time, occasionally triggering a fresh above-ground hole once the
    tunnel reaches the surface row.

    Digs `(32, 1)` once up front via `dig_tile_fn` (unconditionally, even
    if `count == 0`, in which case that's the routine's entire effect).
    Each step: rerolls the wander direction (`_SRand1(5) + direction - 3`,
    masked to `0..7`, seeded from `direction=4` on the very first step),
    steps by the compass delta at that direction (the SAME
    `simant_data_group` tables `sim_queen_a`/`make_blk_queen` use),
    clamping `x` to `1..0x3E` (forcing `direction` to `2`/`6` on a clamp)
    and `y` to `1..0x3E` (separately staging the NEXT step's starting
    direction: `4` if `y` clamped low, `0` if it clamped high, else
    whatever `direction` ended up as after the `x` clamp) — the direction
    used for the NEXT loop iteration is this staged value, not
    necessarily the one just used for this step's compass lookup.  Then
    calls `dig_tile_them_fn` at the (possibly clamped) candidate cell;
    only on success does the tunnel actually advance there, and only
    then — and only if the new `y == 1` (back at the surface row) and
    `_FillHolesBN`/`RN`'s per-column tracking array
    (`simant_data_group[hole_track_off+x]`) has nothing recorded yet —
    calls `make_new_hole_fn(x)`.  Runs until `count` reaches `0`.
    """
    from .simone import SRAND_SEED_OFF, srand1

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    dig_tile_fn(dgroup, simant_data_group, pack, 32, 1)
    if count == 0:
        return

    x, y = 32, 1
    direction = 4

    while True:
        seed = dgroup.rw(SRAND_SEED_OFF)
        seed, roll5 = srand1(seed, 5)
        dgroup.ww(SRAND_SEED_OFF, seed)
        direction = (roll5 + direction - 3) & 7

        cand_x = x + sx8(simant_data_group.rb(direction))
        cand_y = y + sx8(simant_data_group.rb(8 + direction))

        if cand_x < 1:
            cand_x = 1
            direction = 2
        elif cand_x > 0x3E:
            cand_x = 0x3E
            direction = 6

        if cand_y < 2:
            cand_y = 1
            next_direction = 4
        else:
            next_direction = direction
            if cand_y > 0x3E:
                cand_y = 0x3E
                next_direction = 0

        if dig_tile_them_fn(dgroup, simant_data_group, pack, cand_x, cand_y) == 1:
            x, y = cand_x, cand_y
            if y == 1 and simant_data_group.rb(hole_track_off + x) == 0:
                make_new_hole_fn(dgroup, simant_data_group, pack, x)

        count -= 1
        if count == 0:
            return
        direction = next_direction


def dig_out_b_nest(dgroup, simant_data_group, pack, count: int) -> None:
    """Carve a wandering black-nest tunnel up from `(32, 1)`.

    Recovered from `_DigOutBNest` (SIMANTW.SYM seg7:62DE, arg
    count=[bp+6]; FAR return).  See `_dig_out_nest` for the shared
    shape; uses `dig_tile_b`, `dig_tile_them_b`, `make_new_hole_b`, and
    `_FillHolesBN`'s tracking array (`simant_data_group[0x82D2+x]`).
    """
    _dig_out_nest(dgroup, simant_data_group, pack, count, dig_tile_b,
                 dig_tile_them_b, make_new_hole_b, 0x82D2)


def dig_out_r_nest(dgroup, simant_data_group, pack, count: int) -> None:
    """Carve a wandering red-nest tunnel up from `(32, 1)`.

    Recovered from `_DigOutRNest` (SIMANTW.SYM seg7:63B8, arg
    count=[bp+6]; FAR return).  Confirmed a genuine twin of
    `dig_out_b_nest` by independent disassembly — identical shape, only
    `dig_tile_r`/`dig_tile_them_r`/`make_new_hole_r` and `_FillHolesRN`'s
    OWN tracking array (`simant_data_group[0x8312+x]`) differ.
    """
    _dig_out_nest(dgroup, simant_data_group, pack, count, dig_tile_r,
                 dig_tile_them_r, make_new_hole_r, 0x8312)


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


def stay_in_r(dgroup, simant_data_group, pack, x: int, y: int, direction: int) -> None:
    """Handle a red ant idling in the nest at `(x, y)`: nibble a food
    pile if standing on one, otherwise try to keep wandering.

    Recovered from `_StayInR` (SIMANTW.SYM seg6:5C16, args x=[bp+6],
    y=[bp+8], direction=[bp+10]; FAR return).  Composes the already-
    recovered `try_move_dir_r` and `get_enter_dir_r`.

    If the red nest map tile at `(x, y)` is in `0x10..0x13` (the food-
    pile band `is_this_food` recognizes): a tile of exactly `0x10`
    (depleted) rerolls to a fresh `_SRand8()` value, otherwise decrements
    the tile by 1 (shrinking the pile). Either way, decrements
    `pack[0x72DE]` if positive (an unrelated counter), stamps the
    slot's R-list `field_c = 3` and ORs `0x08` into its caste
    (`simant_data_group[0x46E6+slot]`), then falls to the shared tail.

    Otherwise: rerolls a new direction (`_SRand1(3) + direction - 2`,
    masked to `0..7`), stamps it into the slot's caste (keeping the high
    bits), and tries `try_move_dir_r` there. If that fails, looks for an
    entry direction via `get_enter_dir_r(exclude=direction & 7)`,
    falling back to a fresh `_SRand1(8)` roll if none is found, and
    tries `try_move_dir_r` again with THAT direction. Either successful
    move returns immediately with no further writes.

    Shared tail (food branch, or both movement attempts failing):
    re-stamps the slot's CURRENT caste onto the red nest life-grid cell
    at `(x, y)` — a "make it visually consistent" write, not a move.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    idx = (x << 6) + y
    tile = dgroup.rb(MAP_PLANE_BASE[3] + idx)

    if 0x10 <= tile <= 0x13:
        if tile == 0x10:
            seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
            dgroup.ww(SRAND_SEED_OFF, seed)
            dgroup.wb(MAP_PLANE_BASE[3] + idx, roll8 & 0xFF)
        else:
            dgroup.wb(MAP_PLANE_BASE[3] + idx, (tile - 1) & 0xFF)

        if pack.rw(0x72DE) > 0:
            pack.ww(0x72DE, (pack.rw(0x72DE) - 1) & 0xFFFF)

        slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x44F0 + slot, 3)
        simant_data_group.wb(0x46E6 + slot,
                             simant_data_group.rb(0x46E6 + slot) | 8)
    else:
        seed = dgroup.rw(SRAND_SEED_OFF)
        seed, roll3 = srand1(seed, 3)
        dgroup.ww(SRAND_SEED_OFF, seed)
        new_direction = (roll3 + direction - 2) & 7

        slot = pack.rw(0x9B6A)
        caste = (simant_data_group.rb(0x46E6 + slot) & 0xF8) | new_direction
        simant_data_group.wb(0x46E6 + slot, caste & 0xFF)

        if try_move_dir_r(dgroup, simant_data_group, pack, x, y, new_direction) != 0:
            return

        enter_dir = get_enter_dir_r(dgroup, simant_data_group, x, y, direction & 7)
        if _sx16(enter_dir) < 0:
            seed = dgroup.rw(SRAND_SEED_OFF)
            seed, enter_dir = srand1(seed, 8)
            dgroup.ww(SRAND_SEED_OFF, seed)

        if try_move_dir_r(dgroup, simant_data_group, pack, x, y, enter_dir) != 0:
            return

    slot = pack.rw(0x9B6A)
    caste = simant_data_group.rb(0x46E6 + slot)
    dgroup.wb(LIFE_PLANE_BASE[3] + idx, caste & 0xFF)


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


def _do_drown(dgroup, simant_data_group, pack, map_base: int, life_base: int,
              field_c_off: int, caste_off: int, get_new_mode_fn, x: int,
              y: int, caste: int) -> int:
    """Shared body of `do_drown_b`/`do_drown_r`: age an ant standing on a
    nest water tile, occasionally drowning it outright.

    Below the drowning threshold (map tile `< 0x14`): just re-derives
    the slot's `field_c` from its caste's `(caste & 0x78) >> 3` mode
    sub-field via `get_new_mode_fn`, and returns that value.

    At or above the threshold: rerolls the caste's low 3 direction bits
    (`(_SRand1(3) + caste - 1) & 7`, keeping the high bits), stamps it
    onto both the slot's caste field and the SAME life-grid cell, then
    rolls `_SRand1(100)`: a nonzero roll (99-in-100) is a no-op that
    returns the roll itself; a `0` roll (1-in-100) drowns the ant —
    clears the life-grid cell and the slot's caste field to `0`, bumps
    one of two 32-bit PACK counters depending on the REROLLED caste's
    colony bit (`[0x9FC6:0x9FC8]` set / `[0x9B26:0x9B28]` clear — the
    SAME pair for both colonies, confirmed by independent disassembly of
    both `_DoDrownB` and `_DoDrownR`), and returns the rerolled caste.
    """
    from .simone import SRAND_SEED_OFF, srand1

    offset = (x << 6) + y
    if dgroup.rb(map_base + offset) < 0x14:
        mode = (caste & 0x78) >> 3
        result = get_new_mode_fn(dgroup, simant_data_group, pack, mode)
        slot = pack.rw(0x9B6A)
        simant_data_group.wb(field_c_off + slot, result & 0xFF)
        return result

    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll3 = srand1(seed, 3)
    new_caste = ((roll3 + caste - 1) & 7) | (caste & 0xF8)
    slot = pack.rw(0x9B6A)
    simant_data_group.wb(caste_off + slot, new_caste & 0xFF)
    dgroup.wb(life_base + offset, new_caste & 0xFF)

    seed, roll100 = srand1(seed, 100)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll100 != 0:
        return roll100

    dgroup.wb(life_base + offset, 0)
    slot = pack.rw(0x9B6A)
    simant_data_group.wb(caste_off + slot, 0)

    if new_caste & 0x80:
        _acc_add32(pack, 0x9FC6, 0x9FC8, 1)
    else:
        _acc_add32(pack, 0x9B26, 0x9B28, 1)

    return new_caste & 0xFF


def do_drown_b(dgroup, simant_data_group, pack, x: int, y: int, caste: int) -> int:
    """Age/occasionally-drown a black ant standing on a nest water tile.

    Recovered from `_DoDrownB` (SIMANTW.SYM seg6:37A4, args x=[bp+6],
    y=[bp+8], caste=[bp+10]; FAR return).  See `_do_drown` for the
    shared shape; uses black nest map/life planes, B-list field bases,
    and `get_new_mode_b`.
    """
    return _do_drown(dgroup, simant_data_group, pack, MAP_PLANE_BASE[2],
                     LIFE_PLANE_BASE[2], 0x3B22, 0x3D18, get_new_mode_b,
                     x, y, caste)


def do_drown_r(dgroup, simant_data_group, pack, x: int, y: int, caste: int) -> int:
    """Age/occasionally-drown a red ant standing on a nest water tile.

    Recovered from `_DoDrownR` (SIMANTW.SYM seg6:5EA8, args x=[bp+6],
    y=[bp+8], caste=[bp+10]; FAR return).  Confirmed a genuine twin of
    `do_drown_b` by independent disassembly — identical shape and even
    the SAME PACK drown counters, only the map/life planes, R-list field
    bases, and `get_new_mode_r` differ.
    """
    return _do_drown(dgroup, simant_data_group, pack, MAP_PLANE_BASE[3],
                     LIFE_PLANE_BASE[3], 0x44F0, 0x46E6, get_new_mode_r,
                     x, y, caste)


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


def sim_egg_b(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """Advance a black nest egg/larva's growth-stage counter, and once
    every 8 ticks (`counter & 0xF == 8`), possibly hatch it into a real
    ant via `get_new_mode_b`.

    Recovered from `_SimEggB` (SIMANTW.SYM seg6:3CA0, args x=[bp+6],
    y=[bp+8]; FAR return). Composes the already-recovered `sg_rand` and
    `get_new_mode_b`.

    A bitmask gate first: `pack[0x75FC] & (0x7F if dgroup[0xAC82] > 2
    else 0x1F)` nonzero skips everything below, leaving the counter at
    its CURRENT (un-incremented) value. Otherwise increments the
    counter; if its low nibble isn't exactly `8`, that's the whole
    effect (just the increment).

    On a `counter & 0xF == 8` tick: if `pack[0x9FCE] == 0`, rolls
    `sg_rand(0xFF)` and compares it against `pack[0x9C78] >> 7`; a
    roll that beats the threshold resets the counter to `0` and bumps a
    32-bit PACK accumulator (`[0x7C1E:0x7C20]`) instead of hatching.
    Otherwise (gate set, or the roll lost): reads a "hatch mode" byte
    from `simant_data_group[0x8A56]`, recomputes the counter as `(mode
    << 3) + 2`, and sets the slot's `field_c`: mode `== 2` hardcodes
    `1`; any other mode composes `get_new_mode_b(mode)`.

    Always finishes by stamping the (possibly updated) counter onto
    both the black nest life-grid cell at `(x, y)` and the slot's own
    caste field, clearing `field_e`. The real ASM also has a
    presentation-only speech-balloon path here (`ANTEDIT!_EggBalloons`,
    gated on `simant_data_group[0x85FC]!=0` AND no hatch-mode branch
    having run this tick) — deliberately NOT ported.
    """
    slot = pack.rw(0x9B6A)
    growth = simant_data_group.rb(0x3D18 + slot)

    mask = 0x7F if dgroup.rw(0xAC82) > 2 else 0x1F
    if (pack.rw(0x75FC) & mask) == 0:
        growth = (growth + 1) & 0xFF
        if (growth & 0x0F) == 8:
            if pack.rw(0x9FCE) == 0:
                roll = sg_rand(dgroup, 0xFF)
                threshold = pack.rw(0x9C78) >> 7
                do_hatch = threshold >= roll
            else:
                do_hatch = True

            if do_hatch:
                mode = simant_data_group.rw(0x8A56)
                growth = ((mode << 3) + 2) & 0xFFFF
                slot = pack.rw(0x9B6A)
                if mode == 2:
                    simant_data_group.wb(0x3B22 + slot, 1)
                else:
                    field_c = get_new_mode_b(dgroup, simant_data_group, pack, mode)
                    simant_data_group.wb(0x3B22 + slot, field_c & 0xFF)
            else:
                growth = 0
                _acc_add32(pack, 0x7C1E, 0x7C20, 1)

    slot = pack.rw(0x9B6A)
    dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, growth & 0xFF)
    simant_data_group.wb(0x3D18 + slot, growth & 0xFF)
    simant_data_group.wb(0x3F0E + slot, 0)


def sim_egg_r(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """The red-colony twin of `sim_egg_b` — same bitmask gate and
    growth-counter shape, but a GENUINELY DIFFERENT hatch mechanism
    (confirmed by independent disassembly, not assumed symmetric): no
    `pack[0x9FCE]` gate at all, and the hatch mode comes from a fresh
    `_SRand8()` roll combined with a `PACK[0x7690] % 7`-indexed table
    lookup, unconditionally composing `get_new_mode_r` every hatch tick
    (no `mode==2` special case like `_SimEggB` has).

    Recovered from `_SimEggR` (SIMANTW.SYM seg6:62A6, args x=[bp+6],
    y=[bp+8]; FAR return).

    Same bitmask gate as `_SimEggB` (`dgroup[0xAC84] == 1` selects mask
    `0x1F`, else `0x7F` — note: INVERTED comparison direction from
    `_SimEggB`'s `dgroup[0xAC82] > 2`, a different DGROUP field
    entirely) and same counter-increment/skip shape.

    On a hatch tick (`counter & 0xF == 8`): rolls `_SRand8()`, computes
    `remainder = PACK[0x7690] % 7` (C-style truncating remainder, since
    the real ASM sign-extends and uses a signed `idiv`), looks up
    `mode = simant_data_group[0x897E + (remainder << 3) + roll8]`
    (signed byte), recomputes the counter as `(mode << 3) + 0x82`, and
    ALWAYS sets `field_c = get_new_mode_r(mode)` — unconditionally,
    unlike `_SimEggB`'s conditional gate.

    Always finishes by stamping the counter onto the red nest life-grid
    cell and the slot's caste field, clearing `field_e`
    (`simant_data_group[0x48DC+slot]`). Same presentation-only
    `_EggBalloons` path omitted (called with a literal type `3` here,
    vs `_SimEggB`'s `2` — confirmed the SAME UI routine, just a
    different argument, not a different call).
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    slot = pack.rw(0x9B6A)
    growth = simant_data_group.rb(0x46E6 + slot)

    mask = 0x1F if dgroup.rw(0xAC84) == 1 else 0x7F
    if (pack.rw(0x75FC) & mask) == 0:
        growth = (growth + 1) & 0xFF
        if (growth & 0x0F) == 8:
            from .simone import SRAND_SEED_OFF, srand_pow2

            seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
            dgroup.ww(SRAND_SEED_OFF, seed)

            raw = _sx16(pack.rw(0x7690))
            q = abs(raw) // 7
            remainder = abs(raw) - q * 7
            if raw < 0:
                remainder = -remainder

            mode = sx8(simant_data_group.rb(0x897E + (remainder << 3) + roll8))
            growth = ((mode << 3) + 0x82) & 0xFFFF

            field_c = get_new_mode_r(dgroup, simant_data_group, pack, mode)
            slot = pack.rw(0x9B6A)
            simant_data_group.wb(0x44F0 + slot, field_c & 0xFF)

    slot = pack.rw(0x9B6A)
    dgroup.wb(LIFE_PLANE_BASE[3] + (x << 6) + y, growth & 0xFF)
    simant_data_group.wb(0x46E6 + slot, growth & 0xFF)
    simant_data_group.wb(0x48DC + slot, 0)


def sim_queen_a(dgroup, simant_data_group, pack, slot: int) -> None:
    """Stamp the yard queen's caste onto the life grid, and — once her
    caste's low 7 bits exceed `0x67` — check whether she should vanish
    into the nest via a marker cell one step in her facing direction.

    Recovered from `_SimQueenA` (SIMANTW.SYM seg6:0A74, NEAR return, arg:
    slot). Always stamps the caste onto `LIFE_PLANE_BASE[0]` at her own
    position (same "redundant but faithful" re-stamp as `sim_egg_a`). If
    `caste & 0x7F <= 0x67`, that's the entire effect.

    Otherwise: steps one cell in `caste & 7`'s compass direction and
    reads the yard life plane there. If that cell's value equals
    `caste - 8` (the SAME encoded-marker relationship `_LostHeadA` and
    `_LostHeadB`/`R` use), the marker is intact and nothing else happens.
    If it does NOT match, searches the A-list for an ant already at that
    cell (`find_in_a_list`): if one is found, still nothing happens; only
    when the tile doesn't match AND no ant is there does the queen
    vanish — clearing her own caste field and life-grid cell.
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)
    x = simant_data_group.rb(0x23A4 + slot)

    own_off = LIFE_PLANE_BASE[0] + (x << 6) + y
    dgroup.wb(own_off, caste & 0xFF)

    if (caste & 0x7F) <= 0x67:
        return

    dir_idx = caste & 7
    ny = y + sx8(simant_data_group.rb(8 + dir_idx))
    nx = x + sx8(simant_data_group.rb(dir_idx))

    tile = dgroup.rb(LIFE_PLANE_BASE[0] + (nx << 6) + ny)
    if (tile - caste) & 0xFFFF == 0xFFF8:
        return

    found = find_in_a_list(pack, simant_data_group, nx, ny)
    if _sx16(found) >= 0:
        return

    dgroup.wb(own_off, 0)
    simant_data_group.wb(0x2F62 + slot, 0)


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


def do_nest_fight_b(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """Resolve one tick of nest combat for the CURRENT black ant
    (`pack[0x9B6A]`'s slot) at `(x, y)` — the nest-list cousin of
    `do_fight_a`'s yard-list combat tick, same overall shape.

    Recovered from `_DoNestFightB` (SIMANTW.SYM seg6:3A54, args
    x=[bp+6], y=[bp+8]; FAR return). Composes the already-recovered
    `get_new_mode` and `add_ant_to_b_list`.

    Always: rerolls the caste's low 3 bits via `_SRand1(7)` (a genuine
    ADD into the `&0xF8`-masked caste, same idiom as `do_fight_a`) and
    stamps the result onto the black nest life grid at `(x, y)`.

    Then rolls `_SRand16()`; on a `0` (1-in-16): overwrites the life-
    grid cell AND the slot's caste with its `field_e`
    (`simant_data_group[0x3F0E+slot]`). If THAT value's mode sub-field
    (`&0x78`) is exactly `0x60` (a "died" stage), spawns a corpse-tail
    record via `add_ant_to_b_list` at a position derived from the
    slot's own `(x, y)` fields offset by the compass delta for
    `(caste&7)^4` (the opposite of the caste's facing direction) — per
    the established coordinate-role-swap convention, the OFFSET-8 (dy)
    delta lands on `x` and the OFFSET-0 (dx) delta lands on `y` — with
    caste `+8` and `field_c=9`.

    Either way (with or without a corpse spawn): re-reads the slot's
    CURRENT caste. If its colony bit (`0x80`) is unexpectedly SET (not
    black), sets `field_c=7` as a fallback and returns. Otherwise
    computes `field_c` via `get_new_mode(sub=(caste&0x78)>>3,
    full_byte=caste)` — the GENERAL mode-transition function, not
    `get_new_mode_b`, since the real caste's own `0x80` bit is passed
    through unmasked.

    On the 15-in-16 no-kill roll, the real ASM conditionally calls a
    presentation-only speech-balloon UI routine (gated on
    `simant_data_group[0x85FC]==1`) — deliberately NOT ported, same
    split as `_FightBalloons` in `do_fight_a`.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)

    slot = pack.rw(0x9B6A)
    caste = simant_data_group.rb(0x3D18 + slot)
    new_caste = ((caste & 0xF8) + roll) & 0xFF
    simant_data_group.wb(0x3D18 + slot, new_caste)

    life_off = LIFE_PLANE_BASE[2] + (x << 6) + y
    dgroup.wb(life_off, new_caste)

    seed, roll16 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 15)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll16 != 0:
        return

    slot = pack.rw(0x9B6A)
    field_e = simant_data_group.rb(0x3F0E + slot)
    dgroup.wb(life_off, field_e)
    simant_data_group.wb(0x3D18 + slot, field_e)

    if (field_e & 0x78) == 0x60:
        slot = pack.rw(0x9B6A)
        caste_now = simant_data_group.rb(0x3D18 + slot)
        x_field = simant_data_group.rb(0x392C + slot)
        y_field = simant_data_group.rb(0x3736 + slot)
        dir_idx = (caste_now & 7) ^ 4
        dx_val = sx8(simant_data_group.rb(dir_idx))
        dy_val = sx8(simant_data_group.rb(8 + dir_idx))
        add_ant_to_b_list(pack, simant_data_group, dgroup,
                          y=y_field + dx_val, x=x_field + dy_val,
                          caste=(caste_now + 8) & 0xFF, field_c=9, field_e=0)

    slot = pack.rw(0x9B6A)
    caste_now = simant_data_group.rb(0x3D18 + slot)
    if caste_now & 0x80:
        simant_data_group.wb(0x3B22 + slot, 7)
        return

    mode = (caste_now & 0x78) >> 3
    field_c = get_new_mode(dgroup, simant_data_group, pack, mode, caste_now)
    simant_data_group.wb(0x3B22 + slot, field_c & 0xFF)


def do_nest_fight_r(dgroup, simant_data_group, pack, x: int, y: int) -> None:
    """The red-colony twin of `do_nest_fight_b` — same caste-reroll and
    corpse-spawn shape, but a GENUINELY DIFFERENT mode-resolution tail
    (confirmed by independent disassembly, not assumed symmetric): no
    `get_new_mode` call at all — a direct fixed lookup table instead.

    Recovered from `_DoNestFightR` (SIMANTW.SYM seg6:6072, args
    x=[bp+6], y=[bp+8]; FAR return).

    Same caste-reroll/life-grid stamp and `_SRand16()` 1-in-16 kill-tick
    shape as `_DoNestFightB`, and the same corpse-spawn condition
    (`field_e`'s mode `== 0x60`) composing `add_ant_to_r_list`.

    The final dispatch is INVERTED and structurally different: if the
    re-read caste's colony bit is CLEAR (not red — the abnormal case
    here, opposite of `_DoNestFightB`'s "set is abnormal"), sets
    `field_c=7` as the fallback and returns. Otherwise (normal, colony
    bit set): `field_c = dgroup[0x22E6 + mode]` — a plain 16-entry
    static DGROUP table indexed by the SAME `(caste&0x78)>>3` mode, NOT
    a call to `get_new_mode`/`get_new_mode_r` at all.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)

    slot = pack.rw(0x9B6A)
    caste = simant_data_group.rb(0x46E6 + slot)
    new_caste = ((caste & 0xF8) + roll) & 0xFF
    simant_data_group.wb(0x46E6 + slot, new_caste)

    life_off = LIFE_PLANE_BASE[3] + (x << 6) + y
    dgroup.wb(life_off, new_caste)

    seed, roll16 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 15)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll16 != 0:
        return

    slot = pack.rw(0x9B6A)
    field_e = simant_data_group.rb(0x48DC + slot)
    dgroup.wb(life_off, field_e)
    simant_data_group.wb(0x46E6 + slot, field_e)

    if (field_e & 0x78) == 0x60:
        slot = pack.rw(0x9B6A)
        caste_now = simant_data_group.rb(0x46E6 + slot)
        x_field = simant_data_group.rb(0x42FA + slot)
        y_field = simant_data_group.rb(0x4104 + slot)
        dir_idx = (caste_now & 7) ^ 4
        dx_val = sx8(simant_data_group.rb(dir_idx))
        dy_val = sx8(simant_data_group.rb(8 + dir_idx))
        add_ant_to_r_list(pack, simant_data_group, dgroup,
                          y=y_field + dx_val, x=x_field + dy_val,
                          caste=(caste_now + 8) & 0xFF, field_c=9, field_e=0)

    slot = pack.rw(0x9B6A)
    caste_now = simant_data_group.rb(0x46E6 + slot)
    if not (caste_now & 0x80):
        simant_data_group.wb(0x44F0 + slot, 7)
        return

    mode = (caste_now & 0x78) >> 3
    field_c = dgroup.rb(0x22E6 + mode)
    simant_data_group.wb(0x44F0 + slot, field_c & 0xFF)


def check_nest_fight_b(dgroup, simant_data_group, pack, x: int, y: int,
                       attacker: int) -> int:
    """Whether the black nest ant occupying `(x, y)` fights the
    acting/attacking ant (caste `attacker`) — the black-nest combat-
    trigger gate `do_nest_fight_b` itself is the AFTERMATH of.

    Recovered from `_CheckNestFightB` (SIMANTW.SYM seg6:3BA2, args
    x=[bp+6], y=[bp+8], attacker=[bp+10]; FAR return). Composes
    `is_yellow_ant`, `find_in_b_list`, and `get_winner` — all already
    recovered.

    ALWAYS checks `is_yellow_ant` on the occupant tile first (before
    even checking whether it's in a valid caste range at all — the
    opposite order from `check_nest_fight_r`, independently confirmed
    via the raw disassembly, not assumed symmetric): if it IS the
    player's yellow ant AND `dgroup[0xCE98]` is nonzero, defers to the
    UNRECOVERED `_YellowFight(2, pack[0x9B6A])` and returns `1`
    unconditionally (its own return value is discarded by the real
    ASM). `_YellowFight`'s dependency chain is a materially larger body
    of work than this routine itself, so — per this project's fail-loud
    rule — that branch raises `NotImplementedError` rather than a
    silently-wrong guess; every other outcome below is fully byte-exact.

    Otherwise (not yellow, OR yellow but the gate flag is clear): the
    occupant tile must be `0x88..0xE7` or this returns `0` (no fight).
    In range: looks the occupant up via `find_in_b_list` (coordinate-
    role-swap convention: the callee's `y` gets THIS routine's `x` and
    vice versa) with `caste=`the occupant tile; a miss returns `0`.
    A hit: resolves `get_winner(occupant_tile, attacker)`, stamps the
    winner onto the occupant's `field_e`, recomputes its caste as
    `(winner & 0x80) + 0x70` (colony bit preserved, mode forced to a
    fixed "defeated" value) onto both its own caste field and the SAME
    life-grid cell, sets `field_c = 0x0A`, and returns `1`.
    """
    cell = LIFE_PLANE_BASE[2] + (x << 6) + y
    tile = dgroup.rb(cell)

    if is_yellow_ant(tile) == 1:
        if dgroup.rw(0xCE98) != 0:
            raise NotImplementedError(
                "check_nest_fight_b: _YellowFight branch reached (not "
                "recovered) -- x={!r} y={!r} attacker={!r}".format(x, y, attacker))

    if not (0x88 <= tile <= 0xE7):
        return 0

    found = find_in_b_list(pack, simant_data_group, y=x, x=y, caste=tile)
    if found == 0xFFFF:
        return 0

    winner = get_winner(dgroup, simant_data_group, pack, tile, attacker) & 0xFF
    simant_data_group.wb(0x3F0E + found, winner)
    new_caste = ((winner & 0x80) + 0x70) & 0xFF
    simant_data_group.wb(0x3D18 + found, new_caste)
    dgroup.wb(cell, new_caste)
    simant_data_group.wb(0x3B22 + found, 0x0A)
    return 1


def check_nest_fight_r(dgroup, simant_data_group, pack, x: int, y: int,
                       attacker: int) -> int:
    """The red-colony twin of `check_nest_fight_b` — NOT a mechanical
    twin (independently confirmed via the raw disassembly): the caste-
    range check runs FIRST here (opposite order from the black version),
    a range hit ALWAYS attempts the fight with no yellow-ant check at
    all, and a range MISS falls back to `is_yellow_ant` with the
    `_YellowFight` gate flag INVERTED (`dgroup[0xCE98] == 0` here,
    vs `!= 0` for black) and a different `_YellowFight` first argument
    (`3`, vs `2` for black) — plus a genuine behavioral difference: a
    non-yellow out-of-range tile returns `0` immediately here, where
    the black version still falls through to attempt a normal fight.

    Recovered from `_CheckNestFightR` (SIMANTW.SYM seg6:61A2, args
    x=[bp+6], y=[bp+8], attacker=[bp+10]; FAR return). Composes
    `find_in_r_list` and `get_winner` (both already recovered); the
    `_YellowFight` branch raises `NotImplementedError` for the same
    reason `check_nest_fight_b`'s does — everything else is byte-exact.
    """
    cell = LIFE_PLANE_BASE[3] + (x << 6) + y
    tile = dgroup.rb(cell)

    if 8 <= tile <= 0x67:
        found = find_in_r_list(pack, simant_data_group, y=x, x=y, caste=tile)
        if found == 0xFFFF:
            return 0

        winner = get_winner(dgroup, simant_data_group, pack, tile, attacker) & 0xFF
        simant_data_group.wb(0x48DC + found, winner)
        new_caste = ((winner & 0x80) + 0x70) & 0xFF
        simant_data_group.wb(0x46E6 + found, new_caste)
        dgroup.wb(cell, new_caste)
        simant_data_group.wb(0x44F0 + found, 0x0A)
        return 1

    if is_yellow_ant(tile) == 0:
        return 0
    if dgroup.rw(0xCE98) == 0:
        raise NotImplementedError(
            "check_nest_fight_r: _YellowFight branch reached (not "
            "recovered) -- x={!r} y={!r} attacker={!r}".format(x, y, attacker))
    return 0


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


def do_rest_ant(dgroup, simant_data_group, pack, slot: int) -> None:
    """A yard ant standing on a "rest spot" tile heads into the nest;
    otherwise it has a 1-in-4 chance of getting stuck ("resting" in
    place, marked via `field_c`).

    Recovered from `_DoRestAnt` (SIMANTW.SYM seg6:0B76, arg slot=[bp+4];
    NEAR return).  Composes the already-recovered `is_valid_a` and
    `go_in_nest`.

    Reads the yard A-list slot's own `(x, y)` (`simant_data_group[0x23A4
    +slot]`/`[0x278E+slot]`). If `(x, y)` is valid, checks the yard map
    tile there: `pack[0x9B6E] == 0` (outside) requires it to be exactly
    `0x50`; otherwise (inside) requires it in `0x80..0x8F`. Either match
    calls `go_in_nest(x, y, slot)` and returns.

    Otherwise: rolls `_SRand4()`. A `0` (1-in-4) sets the slot's
    `field_c` (`simant_data_group[0x2B78+slot]`) to `2` — a "resting"
    marker. A nonzero roll (3-in-4) is a presentation-only path in the
    original binary (a speech-balloon UI call, `ANTEDIT!_RestBalloons`,
    gated on `simant_data_group[0x85FC]==1`) — deliberately NOT ported,
    same core/presentation split as `_FightBalloons` in `do_fight_a`.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    x = simant_data_group.rb(0x23A4 + slot)
    y = simant_data_group.rb(0x278E + slot)

    found_rest_spot = False
    if is_valid_a(x, y):
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        if pack.rw(0x9B6E) == 0:
            found_rest_spot = tile == 0x50
        else:
            found_rest_spot = 0x80 <= tile <= 0x8F

    if found_rest_spot:
        go_in_nest(dgroup, simant_data_group, pack, x, y, slot)
        return

    seed, roll4 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 3)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll4 == 0:
        simant_data_group.wb(0x2B78 + slot, 2)


def do_rest_b(dgroup, simant_data_group, pack, x: int, y: int,
              attacker: int) -> None:
    """Despite the name shared with `do_rest_ant`, this is genuinely a
    NEST-combat-resolution routine, not a "take a rest" one — its
    opening phase is essentially `check_nest_fight_b` inlined again
    (same `is_yellow_ant`/`_YellowFight` gate, same `0x88..0xE7` tile
    range, same `find_in_b_list` + `get_winner` resolution), with a
    SECOND "retreat" phase appended for when no fight happens at all.

    Recovered from `_DoRestB` (SIMANTW.SYM seg6:367E, args x=[bp+6],
    y=[bp+8], attacker=[bp+10]; FAR return, 294 bytes). Composes
    `is_yellow_ant`, `find_in_b_list`, `get_winner`, and `get_new_mode`
    — all already recovered.

    Combat phase: `is_yellow_ant(tile) == 1 AND dgroup[0xCE98] != 0`
    defers to the UNRECOVERED `_YellowFight(2, pack[0x9B6A])` — raises
    `NotImplementedError` per this project's fail-loud rule (same
    precedent as `check_nest_fight_b`'s own gate); everything else is
    byte-exact. Otherwise: a tile in `0x88..0xE7` found via
    `find_in_b_list` (coordinate-role-swap convention) resolves combat
    exactly like `check_nest_fight_b` does, and a fight (of either
    kind) ends the routine here — its own presentation-only balloon
    tail is deliberately not ported.

    Retreat phase (only reached when NO fight happened — out of range,
    or a range hit with nothing found): stamps the ACTING ant's own
    caste (`simant_data_group[0x3D18 + pack[0x9B6A]]`) onto the target
    cell — it moves in. A `_SRand1(20)` roll of `0` (1-in-20)
    recomputes the acting ant's own `field_c` via `get_new_mode` on its
    own `(caste & 0x78) >> 3` mode and stores it; any other roll ends
    the routine in the SAME presentation-only balloon tail the combat
    phase's fight branch skips.
    """
    cell = LIFE_PLANE_BASE[2] + (x << 6) + y
    tile = dgroup.rb(cell)

    if is_yellow_ant(tile) == 1 and dgroup.rw(0xCE98) != 0:
        raise NotImplementedError(
            "do_rest_b: _YellowFight branch reached (not recovered) -- "
            "x={!r} y={!r} attacker={!r}".format(x, y, attacker))

    fought = False
    if 0x88 <= tile <= 0xE7:
        found = find_in_b_list(pack, simant_data_group, y=x, x=y, caste=tile)
        if found != 0xFFFF:
            winner = get_winner(dgroup, simant_data_group, pack, tile,
                                attacker) & 0xFF
            simant_data_group.wb(0x3F0E + found, winner)
            new_caste = ((winner & 0x80) + 0x70) & 0xFF
            simant_data_group.wb(0x3D18 + found, new_caste)
            dgroup.wb(cell, new_caste)
            simant_data_group.wb(0x3B22 + found, 0x0A)
            fought = True

    if fought:
        return

    from .simone import SRAND_SEED_OFF, srand1

    slot = pack.rw(0x9B6A)
    own_caste = simant_data_group.rb(0x3D18 + slot)
    dgroup.wb(cell, own_caste)

    seed, roll20 = srand1(dgroup.rw(SRAND_SEED_OFF), 20)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll20 != 0:
        return

    slot = pack.rw(0x9B6A)
    own_caste = simant_data_group.rb(0x3D18 + slot)
    mode = (own_caste & 0x78) >> 3
    field_c = get_new_mode(dgroup, simant_data_group, pack, mode, own_caste)
    simant_data_group.wb(0x3B22 + slot, field_c & 0xFF)


def do_rest_r(dgroup, simant_data_group, pack, x: int, y: int,
              attacker: int) -> None:
    """The red-colony twin of `do_rest_b` — NOT a mechanical twin
    (independently confirmed via the raw disassembly): the caste-range
    check runs FIRST here (opposite order from black, matching
    `check_nest_fight_r`'s own reordering vs `check_nest_fight_b`), and
    the `_YellowFight` gate polarity is inverted (`dgroup[0xCE98] == 0`
    triggers it here, vs `!= 0` for black) with a different
    `_YellowFight` first argument (`3`, vs `2` for black) — the SAME
    asymmetries `check_nest_fight_r` has vs `check_nest_fight_b`.

    Recovered from `_DoRestR` (SIMANTW.SYM seg6:5D7E, args x=[bp+6],
    y=[bp+8], attacker=[bp+10]; FAR return, 298 bytes). Composes
    `find_in_r_list`, `get_winner`, `is_yellow_ant`, and `get_new_mode`
    — all already recovered; the `_YellowFight` branch raises
    `NotImplementedError` for the same reason `do_rest_b`'s does.
    """
    cell = LIFE_PLANE_BASE[3] + (x << 6) + y
    tile = dgroup.rb(cell)

    fought = False
    if 8 <= tile <= 0x67:
        found = find_in_r_list(pack, simant_data_group, y=x, x=y, caste=tile)
        if found != 0xFFFF:
            winner = get_winner(dgroup, simant_data_group, pack, tile,
                                attacker) & 0xFF
            simant_data_group.wb(0x48DC + found, winner)
            new_caste = ((winner & 0x80) + 0x70) & 0xFF
            simant_data_group.wb(0x46E6 + found, new_caste)
            dgroup.wb(cell, new_caste)
            simant_data_group.wb(0x44F0 + found, 0x0A)
            fought = True
    elif is_yellow_ant(tile) == 1 and dgroup.rw(0xCE98) == 0:
        raise NotImplementedError(
            "do_rest_r: _YellowFight branch reached (not recovered) -- "
            "x={!r} y={!r} attacker={!r}".format(x, y, attacker))

    if fought:
        return

    from .simone import SRAND_SEED_OFF, srand1

    slot = pack.rw(0x9B6A)
    own_caste = simant_data_group.rb(0x46E6 + slot)
    dgroup.wb(cell, own_caste)

    seed, roll20 = srand1(dgroup.rw(SRAND_SEED_OFF), 20)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll20 != 0:
        return

    slot = pack.rw(0x9B6A)
    own_caste = simant_data_group.rb(0x46E6 + slot)
    mode = (own_caste & 0x78) >> 3
    field_c = get_new_mode(dgroup, simant_data_group, pack, mode, own_caste)
    simant_data_group.wb(0x44F0 + slot, field_c & 0xFF)


def do_rand_b(dgroup, simant_data_group, pack, x: int, y: int, attacker: int,
              sub: int) -> None:
    """A black nest ant's "random wander" tick: an occasional periodic
    `field_c` refresh, THEN the same `check_nest_fight_b`-shaped combat
    resolution `do_rest_b` composes, and — only when no fight happens —
    a plain `try_move_dir_b` wander step (retried once with a fresh
    `_SRand8()` direction on a `0` result, matching the SAME
    epilogue shape `do_nesting_b`'s `finish()` uses).

    Recovered from `_DoRandB` (SIMANTW.SYM seg6:3876, args x=[bp+6],
    y=[bp+8], attacker=[bp+10], sub=[bp+12]; FAR return, 246 bytes).
    Composes `get_new_mode_b`, `is_yellow_ant`, `find_in_b_list`,
    `get_winner`, and `try_move_dir_b` — all already recovered.

    Unconditionally first: a `_SRand32()` roll of `0` (1-in-32)
    refreshes the acting ant's own `field_c` via `get_new_mode_b(sub)`.
    Then the SAME `check_nest_fight_b`/`do_rest_b` combat shape (same
    `_YellowFight` gate, tile range, `find_in_b_list` +
    `get_winner` resolution — the `_YellowFight` branch raises
    `NotImplementedError` for the same reason those routines' does). A
    fight of either kind ends the routine here. Otherwise: attempts
    `try_move_dir_b(x, y, attacker & 7)`; a `0` result retries once
    more with a fresh `_SRand8()` direction, discarding that second
    call's result either way.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    seed, roll32 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 31)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll32 == 0:
        field_c = get_new_mode_b(dgroup, simant_data_group, pack, sub)
        slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x3B22 + slot, field_c & 0xFF)

    cell = LIFE_PLANE_BASE[2] + (x << 6) + y
    tile = dgroup.rb(cell)

    if is_yellow_ant(tile) == 1 and dgroup.rw(0xCE98) != 0:
        raise NotImplementedError(
            "do_rand_b: _YellowFight branch reached (not recovered) -- "
            "x={!r} y={!r} attacker={!r}".format(x, y, attacker))

    fought = False
    if 0x88 <= tile <= 0xE7:
        found = find_in_b_list(pack, simant_data_group, y=x, x=y, caste=tile)
        if found != 0xFFFF:
            winner = get_winner(dgroup, simant_data_group, pack, tile,
                                attacker) & 0xFF
            simant_data_group.wb(0x3F0E + found, winner)
            new_caste = ((winner & 0x80) + 0x70) & 0xFF
            simant_data_group.wb(0x3D18 + found, new_caste)
            dgroup.wb(cell, new_caste)
            simant_data_group.wb(0x3B22 + found, 0x0A)
            fought = True

    if fought:
        return

    mode7 = attacker & 7
    result = try_move_dir_b(dgroup, simant_data_group, pack, x, y, mode7)
    if result != 0:
        return
    seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    try_move_dir_b(dgroup, simant_data_group, pack, x, y, roll8)


def do_rand_r(dgroup, simant_data_group, pack, x: int, y: int, attacker: int,
              sub: int) -> None:
    """The red-colony twin of `do_rand_b` — NOT a mechanical twin
    (independently confirmed via the raw disassembly): the caste-range
    check runs FIRST here (opposite order from black, matching
    `check_nest_fight_r`/`do_rest_r`'s own reordering), and the
    `_YellowFight` gate polarity is inverted (`dgroup[0xCE98] == 0`
    triggers it here, vs `!= 0` for black) with a different
    `_YellowFight` first argument (`3`, vs `2` for black) — the SAME
    asymmetries `check_nest_fight_r`/`do_rest_r` have vs their black
    twins.

    Recovered from `_DoRandR` (SIMANTW.SYM seg6:5F7A, args x=[bp+6],
    y=[bp+8], attacker=[bp+10], sub=[bp+12]; FAR return, 248 bytes).
    Composes `get_new_mode_r`, `find_in_r_list`, `get_winner`,
    `is_yellow_ant`, and `try_move_dir_r` — all already recovered; the
    `_YellowFight` branch raises `NotImplementedError` for the same
    reason `do_rand_b`'s does.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    seed, roll32 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 31)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll32 == 0:
        field_c = get_new_mode_r(dgroup, simant_data_group, pack, sub)
        slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x44F0 + slot, field_c & 0xFF)

    cell = LIFE_PLANE_BASE[3] + (x << 6) + y
    tile = dgroup.rb(cell)

    fought = False
    if 8 <= tile <= 0x67:
        found = find_in_r_list(pack, simant_data_group, y=x, x=y, caste=tile)
        if found != 0xFFFF:
            winner = get_winner(dgroup, simant_data_group, pack, tile,
                                attacker) & 0xFF
            simant_data_group.wb(0x48DC + found, winner)
            new_caste = ((winner & 0x80) + 0x70) & 0xFF
            simant_data_group.wb(0x46E6 + found, new_caste)
            dgroup.wb(cell, new_caste)
            simant_data_group.wb(0x44F0 + found, 0x0A)
            fought = True
    elif is_yellow_ant(tile) == 1 and dgroup.rw(0xCE98) == 0:
        raise NotImplementedError(
            "do_rand_r: _YellowFight branch reached (not recovered) -- "
            "x={!r} y={!r} attacker={!r}".format(x, y, attacker))

    if fought:
        return

    mode7 = attacker & 7
    result = try_move_dir_r(dgroup, simant_data_group, pack, x, y, mode7)
    if result != 0:
        return
    seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    try_move_dir_r(dgroup, simant_data_group, pack, x, y, roll8)


def do_repo_fly(dgroup, simant_data_group, pack, slot: int) -> None:
    """A yard ant occasionally departs on a "reproductive flight" —
    vanishes from the yard A-list/life-grid, bumping a per-colony
    departure counter (each capped at 50 per some outer cycle) and,
    rarely, an additional milestone counter.

    Recovered from `_DoRepoFly` (SIMANTW.SYM seg6:0D4A, arg slot=[bp+4];
    NEAR return).

    Gated on a `_SRand32()` roll of exactly `0` (1-in-32) — anything
    else is a pure no-op. Then requires the slot's OWN colony's
    departure counter (`pack[0x807A]` black / `[0x9C26]` red) to be
    `< 50`, or aborts. Clears the slot's caste and its yard life-grid
    cell — the ant vanishes.

    If `pack[0x80B4] == 2` (an outer game-phase gate): increments that
    SAME colony counter, then rolls `_SRand16()`; a `0` (1-in-16) bumps
    a DGROUP milestone counter (`dgroup[0xAC8C]` black / `[0xAC8E]` red)
    — the real ASM also calls a presentation-only redraw-invalidation
    stub here (`SIMANT!_InvalQueenStorageDisp`), deliberately NOT
    ported (no simulation effect, same split as `_FightBalloons`).
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    caste = simant_data_group.rb(0x2F62 + slot)
    is_red = (caste & 0x80) != 0
    count_off = 0x9C26 if is_red else 0x807A

    seed, roll32 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 31)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll32 != 0:
        return

    if pack.rw(count_off) >= 50:
        return

    simant_data_group.wb(0x2F62 + slot, 0)
    x = simant_data_group.rb(0x23A4 + slot)
    y = simant_data_group.rb(0x278E + slot)
    dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)

    if pack.rw(0x80B4) != 2:
        return

    pack.ww(count_off, (pack.rw(count_off) + 1) & 0xFFFF)

    seed, roll16 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 15)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll16 != 0:
        return

    milestone_off = 0xAC8E if is_red else 0xAC8C
    dgroup.ww(milestone_off, (dgroup.rw(milestone_off) + 1) & 0xFFFF)


def do_return_food_ant(dgroup, simant_data_group, pack, slot: int) -> None:
    """A food-carrying yard ant heads for its nest: enters if already
    standing on a nest-entrance tile, otherwise takes one step via
    `get_nest_dir`'s gradient/homing direction — unless the destination
    is too crowded, in which case it just jitters caste in place.

    Recovered from `_DoReturnFoodAnt` (SIMANTW.SYM seg6:1CB4, arg
    slot=[bp+4]; NEAR return).  Composes the already-recovered
    `is_valid_a`, `go_in_nest`, `get_nest_dir`, `jam_scent_bt`, and
    `jam_scent_rt`.

    Nest-entrance check is IDENTICAL to `do_rest_ant`'s own: valid
    position, and the yard map tile is exactly `0x50` (outside) or in
    `0x80..0x8F` (inside, `pack[0x9B6E]!=0`). A match calls
    `go_in_nest(x, y, slot)` and returns immediately.

    Otherwise: calls `get_nest_dir(x, y, caste&7, colony_flag=caste)`
    for a direction, steps the compass delta for that SAME direction to
    get a candidate `(new_x, new_y)`, and checks the yard map tile
    there against `pack[0x7604]` (a density/crowding threshold).

    Tile TOO crowded (`> threshold`): jitters caste in place instead of
    moving — rolls `_SRand16()`, looks up a small SDG table
    (`simant_data_group[36 + (caste&7)*8 + roll16]`), ORs in the
    caste's high bits (`&0xF8`), and stamps that as the NEW caste at
    the CURRENT (unmoved) position.

    Otherwise (destination clear enough): actually moves — stamps the
    new caste (`direction | high_bits`) at `(new_x, new_y)`, clears the
    old life-grid cell, and updates the slot's recorded `(x, y)`. If
    the slot's `field_e` (a carried-food counter) is nonzero,
    decrements it and jams the mover's OWN colony's TRAIL scent at the
    NEW position with the post-decrement value (`jam_scent_rt` for red,
    `jam_scent_bt` for black) — a food-carrying ant leaves a trail
    behind it. `field_e == 0` skips this entirely (no trail, no jam
    call at all).
    """
    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    x = simant_data_group.rb(0x23A4 + slot)
    y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)

    at_nest_entrance = False
    if is_valid_a(x, y):
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        if pack.rw(0x9B6E) == 0:
            at_nest_entrance = tile == 0x50
        else:
            at_nest_entrance = 0x80 <= tile <= 0x8F

    if at_nest_entrance:
        go_in_nest(dgroup, simant_data_group, pack, x, y, slot)
        return

    high_bits = caste & 0xF8
    direction = get_nest_dir(dgroup, simant_data_group, x, y, caste & 7, caste)

    new_x = x + sx8(simant_data_group.rb(direction))
    new_y = y + sx8(simant_data_group.rb(8 + direction))

    tile = dgroup.rb(MAP_PLANE_BASE[0] + (new_x << 6) + new_y)

    if tile > pack.rw(0x7604):
        from .simone import SRAND_SEED_OFF, srand_pow2

        seed, roll16 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 15)
        dgroup.ww(SRAND_SEED_OFF, seed)
        table_idx = ((caste & 7) << 3) + roll16
        new_caste = (simant_data_group.rb(36 + table_idx) | high_bits) & 0xFF
        simant_data_group.wb(0x2F62 + slot, new_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, new_caste)
        return

    new_caste = (direction | high_bits) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste)
    simant_data_group.wb(0x2F62 + slot, new_caste)
    dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
    simant_data_group.wb(0x23A4 + slot, new_x & 0xFF)
    simant_data_group.wb(0x278E + slot, new_y & 0xFF)

    field_e = simant_data_group.rb(0x334C + slot)
    if field_e == 0:
        return
    field_e = (field_e - 1) & 0xFF
    simant_data_group.wb(0x334C + slot, field_e)
    if caste & 0x80:
        jam_scent_rt(simant_data_group, new_x, new_y, field_e)
    else:
        jam_scent_bt(simant_data_group, new_x, new_y, field_e)


def _forage_jitter(dgroup, simant_data_group, slot: int, x: int, y: int,
                   caste_low3: int, high_bits: int) -> None:
    """Shared "turn in place" step `do_forage_ant` uses for all THREE of its
    non-moving outcomes (too crowded, blocked by a same-colony ant, and the
    trophallaxis-gate-skipped case): reroll a random facing from the SAME
    caste-mode table `rand_turn`'s and `get_forage_dir`'s own random
    fallback read (`simant_data_group[0x24 + (caste_low3<<3) + _SRand8()]`),
    OR it with `high_bits` (the acting ant's own `caste & 0xF8`), and
    re-stamp both the slot's caste field and its (unmoved) `(x, y)`
    life-grid cell with the result — independently confirmed byte-identical
    across all three real-ASM call sites (seg6:207E/21CB/220E), not assumed
    from just one.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
    dgroup.ww(SRAND_SEED_OFF, seed)
    new_caste = (simant_data_group.rb(0x24 + (caste_low3 << 3) + roll8)
                 | high_bits) & 0xFF
    simant_data_group.wb(0x2F62 + slot, new_caste)
    dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, new_caste)


def do_forage_ant(dgroup, simant_data_group, pack, slot: int) -> None:
    """A yard ("A"-list) ant out foraging: heads home if it's standing on a
    nest entrance, otherwise picks a forage direction (scent-gradient or
    random) and either picks up food, moves, jitters in place if crowded/
    blocked, or fights an enemy-colony ant occupying its target cell.

    Recovered from `_DoForageAnt` (SIMANTW.SYM seg6:1E42, arg slot=[bp+4];
    NEAR return, 1126 bytes). Composes the already-recovered `is_valid_a`,
    `go_in_nest`, `get_new_mode`, `get_forage_dir`, `pickup_food_a`,
    `is_yellow_ant`, `find_in_a_list`, `get_winner`, `jam_scent_bn`/`rn`,
    `dec_t_smell`, `alarm_here2`, and the `_SRand8`/`16`/`32` LFSR family.

    Nest-entrance check is `do_return_food_ant`'s own (valid position, tile
    `0x50` outside / `0x80..0x8F` inside `pack[0x9B6E]`): a match calls
    `go_in_nest` and returns. An INVALID position is NOT special-cased —
    it collapses into the exact same "not at entrance" continuation as a
    valid-but-elsewhere position (independently confirmed: `is_valid_a`
    failing and the tile mismatching both set the SAME zero flag the ASM
    branches on), so an out-of-declared-range `(x, y)` still runs the rest
    of this routine on whatever raw byte the slot's position field holds.

    A `_SRand32()` roll of exactly `0` (1-in-32) short-circuits to an idle
    outcome (`field_c` (`simant_data_group[0x2B78+slot]`) set to `0x0D`)
    before anything else — even before the alarm-territory gate below.

    Otherwise: an ALARM-grid gate (`simant_data_group[0x52D2 + (hx<<5) +
    hy]`, hx=x>>1/hy=y>>1 — the SAME half-res grid `alarm_here`/`alarm_here2`/
    `smooth_alarm` read/write, independently confirmed via `alarm_here2`'s own
    `0x52D2` base) aborts with `field_c = 0x0B` if the cell is alarmed at all.

    `caste_sub = (caste & 0x78) >> 3`: if NOT `2` or `6`, calls
    `get_new_mode(caste_sub, caste)`, stores the result in `field_c`, zeroes
    `field_e` (`simant_data_group[0x334C+slot]`), and returns — no foraging
    this tick for these sub-modes.

    For `caste_sub` in `{2, 6}`: calls `get_forage_dir(x, y, caste&7,
    colony_flag=caste)`.

    - Direction `< 0` ("no better cell, stay put" sentinel): rolls
      `_SRand8()`; nonzero clears `field_c` to `0`, zero instead calls
      `get_new_mode(caste_sub, caste)` into `field_c`; either way
      `field_e` is zeroed, then `dec_t_smell` is called with `(x >> 1,
      y >> 1, caste & 0x80)` — note `dec_t_smell` ITSELF halves its x/y
      again internally, so this call site genuinely operates on a
      QUARTER-resolution cell relative to the ant's actual half-res trail
      cell. Independently re-verified against the OTHER `dec_t_smell` call
      site in this same routine (the move-path one, which passes full-
      resolution `new_x`/`new_y`) — a real asymmetry in the compiled code
      between the two call sites, not a transcription slip, ported
      verbatim rather than "corrected".

    - Direction `>= 0`: computes `new_x`/`new_y` from
      `simant_data_group[direction]`/`[direction+8]` (the SAME live
      8-entry compass dx/dy table `get_best_dir` reads, confirmed via
      `get_forage_dir`'s own established read of the identical table) and
      reads the yard map tile there.

      - Tile is a pickup spot (`0x48..0x4B` outside / `0x18..0x27` inside
        `pack[0x9B6E]`): stamps `direction | high_bits | 0x08` (`high_bits`
        = `caste & 0xF8`) as the new caste onto BOTH the slot's caste field
        and the life-grid cell AT THE OLD `(x, y)` — the ant does NOT step
        onto the food tile this tick, it just turns to face it — then calls
        `pickup_food_a(new_x, new_y)`, sets `field_c = 3`, and `field_e =
        0xC8` (200 — a carry/return-trip counter, the same field
        `do_return_food_ant` decrements each step home). Returns.

      - Tile's crowding value exceeds `pack[0x7604]`: too crowded to move —
        `_forage_jitter`s in place (see above), then on a `_SRand16()` roll
        of exactly `0` (1-in-16) ALSO calls `get_new_mode(caste_sub, caste)`
        into `field_c` (zeroing `field_e`) on top of the jitter. Returns.

      - Otherwise (clear enough to move): reads the life-grid occupant at
        `(new_x, new_y)`.

        - Occupant `0` (empty): moves — stamps `direction | high_bits` at
          the new cell, clears the old one, updates the slot's `(x, y)`;
          if `field_e` is nonzero, decrements it and calls `jam_scent_rn`/
          `bn` (the NEST scent grid — NOT `jam_scent_bt`/`rt`'s TRAIL grid
          `do_return_food_ant` uses; independently confirmed via the raw
          call targets, seg6:0x94F6/0x94B6) at the new position with the
          post-decrement value; then unconditionally calls
          `dec_t_smell(new_x, new_y, caste & 0x80)` (full-resolution this
          time). Returns.

        - Occupant is the player's yellow ant (`is_yellow_ant`): if
          `(caste ^ dgroup[0xCE98]) & 0x80` (a BYTE xor+test — genuinely
          different from `check_nest_fight_b`/`r`'s plain word `!= 0` test
          on the SAME `dgroup[0xCE98]` field, independently re-derived from
          the raw `xor al, ds:[CE98]; test al, 80h` rather than assumed
          identical to that precedent), calls the UNRECOVERED
          `SIMANT1!_YellowFight(slot, 1)` (seg6:823E, reached via the
          same-segment `push cs; call near` far-call-emulation idiom) and
          returns unconditionally — a call this routine's own dependency
          chain surfaces that the prior scoping survey's call-table did NOT
          list (independently found during this session's own from-scratch
          disassembly, not merely trusted from that report); per this
          project's fail-loud rule, this branch raises `NotImplementedError`
          instead of guessing at `_YellowFight`'s effect, matching the
          established `check_nest_fight_b`/`r` precedent for the same
          unrecovered routine. Otherwise (colony bit matches — same
          colony's yellow ant): falls through to the SAME trophallaxis gate
          below as if the occupant were merely a same-colony ant.

        - Occupant same colony, not yellow (falls through here too): gated
          on `pack[0x9AF2]`. This routine's own compiled comparison is a
          literal `== 1` (`cmp ..., 0001h`) — NOT `try_move_dir_b`'s `!= 0`
          test on the SAME field. Independently checked for equivalence
          (per this project's own house rule against assuming unverified
          equivalences): `pack[0x9AF2]`'s only write site anywhere in this
          codebase is `set_my_health`'s `pack.ww(0x9AF2, 0 if ... else 1)`
          — the field only ever holds `0` or `1`, so `== 1` and `!= 0` ARE
          behaviorally identical here; ported as the exact compiled `== 1`
          regardless, for maximum fidelity to the real instruction. If set:
          first stamps `high_bits | direction` onto the caste field AND the
          OLD-position life-grid cell (setting up the destination marker
          `_DoTroph` reads), then calls the UNRECOVERED
          `SIMANT!_DoTroph(x, y, direction)` (seg1:846E) — raises
          `NotImplementedError` here (the established `try_move_dir_b`
          precedent for this exact unrecovered routine) AFTER performing
          those two writes, since the real ASM performs them unconditionally
          before the call regardless of `_DoTroph`'s own (unknown) effect.
          If NOT set, or after a hypothetical `_DoTroph` return: unconditionally
          `_forage_jitter`s in place and returns — no move, no trophallaxis
          effect on state beyond the pre-call stamp.

        - Occupant is a DIFFERENT colony's ant (not yellow, colony bit
          differs): a fight. Clears the acting ant's own caste field and
          its life-grid cell at the OLD position (it "dies"/vanishes from
          the A-list), looks the occupant up via `find_in_a_list(new_x,
          new_y)` (a MISS — `0xFFFF` — ends the routine with no further
          effect), then resolves `get_winner(arg_a=occupant_caste,
          arg_b=acting_caste)`: stamps `(winner & 0x80) + 0x70` onto the
          found occupant's caste field AND the life-grid cell at the new
          position, sets its `field_c = 0x0A`, its `field_e = winner &
          0xFF`, and finally calls `alarm_here2(new_x, new_y, 0x28)`.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    x = simant_data_group.rb(0x23A4 + slot) & 0xFF
    y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)

    at_entrance = False
    if is_valid_a(x, y):
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        if pack.rw(0x9B6E) == 0:
            at_entrance = tile == 0x50
        else:
            at_entrance = 0x80 <= tile <= 0x8F

    if at_entrance:
        go_in_nest(dgroup, simant_data_group, pack, x, y, slot)
        return

    seed, roll32 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0x1F)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll32 == 0:
        simant_data_group.wb(0x2B78 + slot, 0x0D)
        return

    high_bits = caste & 0xF8
    caste_sub = (caste & 0x78) >> 3

    territory_idx = ((x & 0xFE) << 4) + (y >> 1)
    if simant_data_group.rb(0x52D2 + territory_idx) != 0:
        simant_data_group.wb(0x2B78 + slot, 0x0B)
        return

    caste_low3 = caste & 7
    if caste_sub not in (2, 6):
        result = get_new_mode(dgroup, simant_data_group, pack, caste_sub, caste)
        simant_data_group.wb(0x2B78 + slot, result & 0xFF)
        simant_data_group.wb(0x334C + slot, 0)
        return

    direction = get_forage_dir(dgroup, simant_data_group, x, y, caste_low3, caste)

    if direction < 0:
        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        if roll8 != 0:
            simant_data_group.wb(0x2B78 + slot, 0)
        else:
            result = get_new_mode(dgroup, simant_data_group, pack, caste_sub, caste)
            simant_data_group.wb(0x2B78 + slot, result & 0xFF)
        simant_data_group.wb(0x334C + slot, 0)
        dec_t_smell(simant_data_group, x >> 1, y >> 1, caste & 0x80)
        return

    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
    dest_tile = dgroup.rb(MAP_PLANE_BASE[0] + (new_x << 6) + new_y)

    is_pickup = False
    if pack.rw(0x9B6E) == 0:
        is_pickup = 0x48 <= dest_tile <= 0x4B
    else:
        is_pickup = 0x18 <= dest_tile <= 0x27

    if is_pickup:
        new_caste = (direction | high_bits | 0x08) & 0xFF
        simant_data_group.wb(0x2F62 + slot, new_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, new_caste)
        simant_data_group.wb(0x2B78 + slot, 3)
        pickup_food_a(dgroup, pack, new_x, new_y)
        simant_data_group.wb(0x334C + slot, 0xC8)
        return

    if dest_tile > pack.rw(0x7604):
        _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
        seed, roll16 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0xF)
        dgroup.ww(SRAND_SEED_OFF, seed)
        if roll16 == 0:
            result = get_new_mode(dgroup, simant_data_group, pack, caste_sub, caste)
            simant_data_group.wb(0x2B78 + slot, result & 0xFF)
            simant_data_group.wb(0x334C + slot, 0)
        return

    occupant = dgroup.rb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y)

    if occupant == 0:
        new_caste = (direction | high_bits) & 0xFF
        dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste)
        simant_data_group.wb(0x2F62 + slot, new_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
        simant_data_group.wb(0x23A4 + slot, new_x & 0xFF)
        simant_data_group.wb(0x278E + slot, new_y & 0xFF)

        field_e = simant_data_group.rb(0x334C + slot)
        if field_e != 0:
            field_e = (field_e - 1) & 0xFF
            simant_data_group.wb(0x334C + slot, field_e)
            if caste & 0x80:
                jam_scent_rn(simant_data_group, new_x, new_y, field_e)
            else:
                jam_scent_bn(simant_data_group, new_x, new_y, field_e)

        dec_t_smell(simant_data_group, new_x, new_y, caste & 0x80)
        return

    if is_yellow_ant(occupant):
        if (caste ^ dgroup.rb(0xCE98)) & 0x80:
            raise NotImplementedError(
                "do_forage_ant: _YellowFight branch reached (not recovered) "
                "-- slot={!r}".format(slot))
        # falls through to the same-colony trophallaxis-gated jitter below
    else:
        if (occupant ^ caste) & 0x80 == 0:
            _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
            return

        # different colony: fight
        acting_caste = caste
        simant_data_group.wb(0x2F62 + slot, 0)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
        found = find_in_a_list(pack, simant_data_group, new_x, new_y)
        if found == 0xFFFF:
            return
        occupant_caste = simant_data_group.rb(0x2F62 + found)
        winner = get_winner(dgroup, simant_data_group, pack, occupant_caste,
                            acting_caste) & 0xFF
        new_caste_occ = ((winner & 0x80) + 0x70) & 0xFF
        simant_data_group.wb(0x2F62 + found, new_caste_occ)
        dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste_occ)
        simant_data_group.wb(0x2B78 + found, 0x0A)
        simant_data_group.wb(0x334C + found, winner)
        alarm_here2(simant_data_group, new_x, new_y, 0x28)
        return

    # occupant was yellow, same colony -> trophallaxis gate then jitter
    if pack.rw(0x9AF2) == 1:
        pre_caste = (high_bits | direction) & 0xFF
        simant_data_group.wb(0x2F62 + slot, pre_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, pre_caste)
        raise NotImplementedError(
            "do_forage_ant: _DoTroph branch reached (not recovered) -- "
            "slot={!r}".format(slot))

    _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)


def do_food_in_b(dgroup, simant_data_group, pack, x: int, y: int, mode: int) -> None:
    """A black nest ("B"-list) ant carrying food back into the nest: head
    toward a chosen direction and move in (fighting/deferring on an
    occupied destination like `_DoDigInB`'s own move tail), OR — when no
    good direction exists, or a 1-in-16 roll overrides a found one — drop/
    grow a food pile at her CURRENT position instead and re-pick her mode.

    Recovered from `_DoFoodInB` (SIMANTW.SYM seg6:492A, FAR return, 678
    bytes). Only THREE args — `x=[bp+6]`, `y=[bp+8]`, `mode=[bp+10]` — NOT
    the caller-precomputed `caste_sub` fourth arg `_DoDigInB`/`_SimQueenB`
    both take (confirmed: the real ASM's own `enter 001Ch,0` frame never
    references `[bp+14]` anywhere in the routine). Composes the already-
    recovered `get_enter_dir_b`, `get_out_b`, `is_yellow_ant`,
    `find_in_b_list`, `get_winner`, `get_new_mode_b`, and — a genuine
    simplification this session found — the PRIVATE shared body
    `_eat_food` (not the public `try_eat_food_b`/`eat_food_b` wrappers)
    reused VERBATIM for the alt-branch's own food-supply tail: its own
    `(MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98, 0x7402, 0xAC86)` argument
    set matches this routine's own disassembled field accesses byte for
    byte (the SAME reroll-tile / decrement-food-count / growth-trigger
    sequence `eat_food_b` composes, just reached via a different outer
    gate — an `_SRand1(100)` roll here, vs `_EatFoodB`'s own unconditional
    call site elsewhere), confirming both independently rather than
    re-deriving a near-duplicate.

    Calls `get_enter_dir_b(x, y, mode & 7)`. A NEGATIVE result skips
    `_SRand16()` entirely and goes straight to the alt-branch below
    (confirmed via the raw disassembly: the `jge`/`jmp` pair branches PAST
    the `_SRand16()` call site on a negative direction, not just past the
    starvation-style check — the two readings consume a different number
    of `_SRand*` calls, the same class of polarity mistake `_SimQueenB`'s
    own mode-0x0C control flow caught this session). A non-negative
    direction then rolls `_SRand16()`; on an exact `0` (1-in-16) it ALSO
    takes the alt-branch — a probabilistic override even though a valid
    direction exists.

    Main branch (direction found, no 1-in-16 override): `dir_caste =
    (mode & 0xF8) | direction` is stamped onto BOTH the slot's caste field
    and the CURRENT (not yet moved) nest life-grid cell `(x, y)` — the
    SAME unconditional "turn to face" stamp `_DoDigInB` makes, made
    BEFORE any of the bounds/occupant logic below (and, like there, this
    exact value — not a later re-read — is what `get_winner`'s second
    argument uses in the fight case). `(new_x, new_y)` come from
    `simant_data_group[direction]`/`[8 + direction]` (the SAME live
    compass dx/dy table `_DoDigInB`/`_SimQueenB`/`_DoForageAnt` all read).
    Either coordinate out of `0..0x3F` is a silent no-op return. `new_y <
    1` instead calls `get_out_b(x)` and returns UNCONDITIONALLY,
    discarding its result — identical to `_DoDigInB`'s own precedent.
    Otherwise: the nest map tile at `(new_x, new_y)` `>= 0x30` is a silent
    no-op return. The CURRENT `(x, y)` nest life-grid cell is then
    unconditionally cleared to `0` (no dig step here at all — this
    routine never digs, unlike `_DoDigInB`).

    The `(new_x, new_y)` occupant is then read; bit `0x80` CLEAR falls
    straight through to the move below. Bit `0x80` SET:

    - `is_yellow_ant(occupant)` and `dgroup[0xCE98] == 0`: falls through
      to the move (treated as empty) — same as `_DoDigInB`'s own gate.
    - `is_yellow_ant(occupant)` and `dgroup[0xCE98] != 0`: calls the
      UNRECOVERED `SIMANT1!_YellowFight(2, pack[0x9B6A])` (seg6:823E, the
      SAME call/argument pair already established) and returns — raises
      `NotImplementedError` per this project's fail-loud rule. (The real
      ASM reaches this through a second, redundant `is_yellow_ant`
      re-call plus a second `dgroup[0xCE98]` re-check on the SAME
      unchanged occupant byte — confirmed via the raw disassembly that
      nothing writes to that byte in between — so it collapses to this
      same single-check gate without loss of byte-exactness.)
    - Not yellow, occupant in `0x88..0xE7`: looked up via
      `find_in_b_list(new_x, new_y, occupant)` (the established
      coordinate-role-swap convention). A miss falls through to the move.
      A hit resolves `get_winner(occupant, dir_caste)`: stamps the winner
      onto the found occupant's `field_e`, recomputes its caste as
      `(winner & 0x80) + 0x70` onto both its own caste field and the new-
      position life-grid cell, sets its `field_c = 0x0A`, and returns —
      no move, no field_c re-pick for the acting ant.
    - Not yellow, occupant outside `0x88..0xE7`: falls through to the
      move (same as a miss).

    The move: re-reads the slot's CURRENT caste field (always numerically
    equal to `dir_caste` here — nothing in this routine's own occupant-
    check paths writes to it first, unlike `_DoDigInB`'s dig-success bump
    — but ported as a fresh read to match the real ASM's own instruction,
    not the cached local), keeps its high bits, ORs in `direction`, stamps
    that at the new position AND the slot's caste field, and updates the
    slot's `x`/`y` fields (`[0x3736+slot]`/`[0x392C+slot]`). NO field_c
    re-pick happens on this path at all.

    Alt-branch (no direction, or the 1-in-16 override): re-reads `(x, y)`
    (the ORIGINAL position — no move at all on this path) and grows a
    food pile there: a tile `< 0x10` is forced to exactly `0x10`; a tile
    in `0x10..0x12` is incremented by one; `>= 0x13` is left unchanged.
    Unconditionally increments `pack[0x9EA4]` (the SAME food-nibble
    counter `_eat_food`'s own `food_count_off` decrements elsewhere) and
    clears the slot's caste field's `0x08` bit if set (`caste -= 8`).
    Rolls `_SRand1(100)`; only when the roll EXCEEDS `dgroup[0xAC86]`
    (the colony's food supply) does it run the `_eat_food`-equivalent
    tail at `(x, y)` (see above) — an inverted-looking gate (a HIGH roll,
    not a low one, triggers the food-supply nibble), ported exactly as
    disassembled, not "corrected" to feel more intuitive. Either way,
    finally re-picks the slot's `field_c` via
    `get_new_mode_b(sub=(mode & 0x78) >> 3)` and returns — this is the
    ONLY path in the whole routine that ever touches the acting ant's own
    `field_c`.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    caste_low3 = mode & 7
    direction = get_enter_dir_b(dgroup, simant_data_group, x, y, caste_low3)

    take_alt = direction < 0
    if not take_alt:
        seed, roll16 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 15)
        dgroup.ww(SRAND_SEED_OFF, seed)
        take_alt = (roll16 == 0)

    if take_alt:
        own_idx = MAP_PLANE_BASE[2] + (x << 6) + y
        tile = dgroup.rb(own_idx)
        if tile < 0x10:
            dgroup.wb(own_idx, 0x10)
        elif tile < 0x13:
            dgroup.wb(own_idx, (tile + 1) & 0xFF)

        pack.ww(0x9EA4, (pack.rw(0x9EA4) + 1) & 0xFFFF)

        slot = pack.rw(0x9B6A)
        caste_field = simant_data_group.rb(0x3D18 + slot)
        if caste_field & 0x08:
            simant_data_group.wb(0x3D18 + slot, (caste_field - 8) & 0xFF)

        seed, roll100 = srand1(dgroup.rw(SRAND_SEED_OFF), 100)
        dgroup.ww(SRAND_SEED_OFF, seed)
        if roll100 > _sx16(dgroup.rw(0xAC86)):
            _eat_food(dgroup, pack, MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98,
                     0x7402, 0xAC86, x, y)

        sub = ((mode & 0x78) >> 3) & 0xFFFF
        new_mode = get_new_mode_b(dgroup, simant_data_group, pack, sub) & 0xFF
        slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x3B22 + slot, new_mode)
        return

    high_bits = mode & 0xF8
    dir_caste = (high_bits | direction) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, dir_caste)
    slot = pack.rw(0x9B6A)
    simant_data_group.wb(0x3D18 + slot, dir_caste)

    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    if not (0 <= new_x <= 0x3F):
        return
    if not (0 <= new_y <= 0x3F):
        return
    if new_y < 1:
        get_out_b(dgroup, simant_data_group, pack, x)
        return

    idx = (new_x << 6) + new_y
    tile = dgroup.rb(MAP_PLANE_BASE[2] + idx)
    if tile >= 0x30:
        return

    dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, 0)

    occupant = dgroup.rb(LIFE_PLANE_BASE[2] + idx)
    if occupant & 0x80:
        if is_yellow_ant(occupant):
            if dgroup.rb(0xCE98) != 0:
                raise NotImplementedError(
                    "do_food_in_b: _YellowFight branch reached (not recovered) "
                    "-- x={!r} y={!r}".format(x, y))
            # else falls through to the move, below
        elif 0x88 <= occupant <= 0xE7:
            found = find_in_b_list(pack, simant_data_group, new_x, new_y, occupant)
            if found != 0xFFFF:
                winner = get_winner(dgroup, simant_data_group, pack, occupant,
                                    dir_caste) & 0xFF
                simant_data_group.wb(0x3F0E + found, winner)
                new_caste_occ = ((winner & 0x80) + 0x70) & 0xFF
                simant_data_group.wb(0x3D18 + found, new_caste_occ)
                dgroup.wb(LIFE_PLANE_BASE[2] + idx, new_caste_occ)
                simant_data_group.wb(0x3B22 + found, 0x0A)
                return

    slot = pack.rw(0x9B6A)
    cur_caste = simant_data_group.rb(0x3D18 + slot)
    new_caste = ((cur_caste & 0xF8) | direction) & 0xFF
    simant_data_group.wb(0x3D18 + slot, new_caste)
    dgroup.wb(LIFE_PLANE_BASE[2] + idx, new_caste)
    simant_data_group.wb(0x3736 + slot, new_x & 0xFF)
    simant_data_group.wb(0x392C + slot, new_y & 0xFF)


def do_dig_out_b(dgroup, simant_data_group, pack, x: int, y: int, mode: int) -> None:
    """A black nest ("B"-list) ant heading OUT of the nest through already-
    clear passages: pick an exit-seeking direction and either move into an
    already-open destination (fighting/deferring on an occupant, same
    shape as `_DoDigInB`/`_DoFoodInB`'s own move tails), bail out with a
    small local penalty if the destination is blocked, or do nothing at
    all if it's still dirt — unlike `_DoDigInB`, this routine never digs.

    Recovered from `_DoDigOutB` (SIMANTW.SYM seg6:4EB0, FAR return, 686
    bytes). Only THREE args — `x=[bp+6]`, `y=[bp+8]`, `mode=[bp+10]` — the
    SAME 3-arg signature `_DoFoodInB` has (no caller-precomputed
    `caste_sub` fourth arg, confirmed via the raw `enter 0014h,0` frame
    never referencing `[bp+14]`). Composes the already-recovered
    `get_exit_dir_b`, `rand_turn`, `get_out_b`, `is_it_dirt`,
    `is_yellow_ant`, `find_in_b_list`, `get_winner`, and — reused
    VERBATIM, byte-for-byte matching field set — `_try_eat_food` for the
    move-tail's own trailing food-nibble/colony-growth block: the SAME
    `(MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98, 0x7402, 0xAC86)`
    arguments AND the SAME `_SRand64() > dgroup[0xAC86]` outer gate
    `_DoDigInB`'s own analogous tail already established, confirming both
    independently rather than re-deriving a near-duplicate.

    Calls `get_exit_dir_b(x, y, mode & 7)`; a positive (1-based compass)
    result becomes `direction = result - 1`. A non-positive (`0`, "no exit
    direction found") result instead calls `rand_turn(mode & 7)` for a
    purely random direction. `dir_caste = (mode & 0xF8) | direction` is
    stamped UNCONDITIONALLY onto both the slot's caste field and the
    CURRENT (not yet moved) nest life-grid cell `(x, y)` — the SAME "turn
    to face" pattern `_DoDigInB`/`_DoFoodInB`/`_SimQueenB` all share, and
    (as there) this exact value, not a later re-read, is what
    `get_winner`'s second argument uses in the fight case below.
    `(new_x, new_y)` come from the SAME live compass dx/dy table read
    (`simant_data_group[direction]`/`[8 + direction]`). Either coordinate
    out of `0..0x3F` is a silent no-op return. `new_y < 1` instead calls
    `get_out_b(x)` and returns UNCONDITIONALLY, discarding its result —
    identical to `_DoDigInB`/`_DoFoodInB`'s own precedent.

    The nest map tile at `(new_x, new_y)` `>= 0x30` (blocked) does NOT
    just no-op: it decrements `simant_data_group[0x3A4 + (x << 6) + y]`
    (the ant's OWN current-position cell of the SAME exit-distance map
    `_GetExitDirB`/`_GetEnterDirB` read — a "this route turned out
    blocked, lower my own exit appeal" penalty) and, based on `sub =
    (mode & 0x78) >> 3`: `sub in (5, 9)` bumps the slot's caste field
    DOWN by `0x18` (byte-wrapping) and sets `field_c = 4`; `sub in (2,
    6)` sets `field_c = 4` alone (confirmed via the raw disassembly that
    the `sub in (5, 9)` case's own field_c write and this one are the
    SAME single write reused via fallthrough — not two separate writes);
    any other `sub` leaves `field_c` untouched. Either way, returns with
    no further action.

    Otherwise (tile `< 0x30`): `is_it_dirt(tile)` TRUE is an immediate
    no-op return — UNLIKE `_DoDigInB`, this routine makes NO attempt to
    dig through dirt at all. Only a genuinely clear (non-dirt, `< 0x30`)
    destination continues: the CURRENT `(x, y)` nest life-grid cell is
    unconditionally cleared to `0`, then the `(new_x, new_y)` occupant is
    read; bit `0x80` CLEAR falls straight through to the move below. Bit
    `0x80` SET:

    - `is_yellow_ant(occupant)` and `dgroup[0xCE98] == 0`: falls through
      to the move (treated as empty).
    - `is_yellow_ant(occupant)` and `dgroup[0xCE98] != 0`: calls the
      UNRECOVERED `SIMANT1!_YellowFight(2, pack[0x9B6A])` (seg6:823E, the
      SAME call/argument pair already established) and returns — raises
      `NotImplementedError` per this project's fail-loud rule. (Reached
      through the SAME textually-redundant double `is_yellow_ant`/`CE98`
      re-check `_DoFoodInB` has on the exact-unchanged occupant byte —
      collapses to this single-check gate without loss of byte-exactness.)
    - Not yellow, occupant in `0x88..0xE7`: looked up via
      `find_in_b_list(new_x, new_y, occupant)` (the established
      coordinate-role-swap convention). A miss falls through to the move.
      A hit resolves `get_winner(occupant, dir_caste)`: stamps the winner
      onto the found occupant's `field_e`, recomputes its caste as
      `(winner & 0x80) + 0x70` onto both its own caste field and the new-
      position life-grid cell, sets its `field_c = 0x0A`, and returns —
      no move.
    - Not yellow, occupant outside `0x88..0xE7`: falls through to the
      move (same as a miss).

    The move: re-reads the slot's CURRENT caste field (a fresh read, not
    the cached `dir_caste`, matching `_DoDigInB`/`_DoFoodInB`'s own
    precedent — and, like `_DoFoodInB`, numerically always equal to
    `dir_caste` here since nothing on this routine's occupant-check paths
    writes to it first), keeps its high bits, ORs in `direction`, stamps
    that at the new position AND the slot's caste field, and updates the
    slot's `x`/`y` fields (`[0x3736+slot]`/`[0x392C+slot]`). Finally
    (move path only): rolls `_SRand64()`; only when it exceeds
    `dgroup[0xAC86]` does it run `_try_eat_food` at the NEW position (see
    above) — verbatim-identical gate and call to `_DoDigInB`'s own
    analogous tail, just triggered from this routine's own move instead.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    caste_low3 = mode & 7
    result = get_exit_dir_b(dgroup, simant_data_group, x, y, caste_low3)
    if result > 0:
        direction = result - 1
    else:
        direction = rand_turn(dgroup, simant_data_group, caste_low3)

    high_bits = mode & 0xF8
    dir_caste = (high_bits | direction) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, dir_caste)
    slot = pack.rw(0x9B6A)
    simant_data_group.wb(0x3D18 + slot, dir_caste)

    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    if not (0 <= new_x <= 0x3F):
        return
    if not (0 <= new_y <= 0x3F):
        return
    if new_y < 1:
        get_out_b(dgroup, simant_data_group, pack, x)
        return

    idx = (new_x << 6) + new_y
    tile = dgroup.rb(MAP_PLANE_BASE[2] + idx)
    if tile >= 0x30:
        own_dist_off = 0x3A4 + (x << 6) + y
        simant_data_group.wb(own_dist_off,
                             (simant_data_group.rb(own_dist_off) - 1) & 0xFF)
        sub = (mode & 0x78) >> 3
        slot = pack.rw(0x9B6A)
        if sub in (5, 9):
            caste = simant_data_group.rb(0x3D18 + slot)
            simant_data_group.wb(0x3D18 + slot, (caste - 0x18) & 0xFF)
            simant_data_group.wb(0x3B22 + slot, 4)
        elif sub in (2, 6):
            simant_data_group.wb(0x3B22 + slot, 4)
        return

    if is_it_dirt(tile):
        return

    dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, 0)

    occupant = dgroup.rb(LIFE_PLANE_BASE[2] + idx)
    if occupant & 0x80:
        if is_yellow_ant(occupant):
            if dgroup.rb(0xCE98) != 0:
                raise NotImplementedError(
                    "do_dig_out_b: _YellowFight branch reached (not recovered) "
                    "-- x={!r} y={!r}".format(x, y))
            # else falls through to the move, below
        elif 0x88 <= occupant <= 0xE7:
            found = find_in_b_list(pack, simant_data_group, new_x, new_y, occupant)
            if found != 0xFFFF:
                winner = get_winner(dgroup, simant_data_group, pack, occupant,
                                    dir_caste) & 0xFF
                simant_data_group.wb(0x3F0E + found, winner)
                new_caste_occ = ((winner & 0x80) + 0x70) & 0xFF
                simant_data_group.wb(0x3D18 + found, new_caste_occ)
                dgroup.wb(LIFE_PLANE_BASE[2] + idx, new_caste_occ)
                simant_data_group.wb(0x3B22 + found, 0x0A)
                return

    slot = pack.rw(0x9B6A)
    cur_caste = simant_data_group.rb(0x3D18 + slot)
    new_caste = ((cur_caste & 0xF8) | direction) & 0xFF
    simant_data_group.wb(0x3D18 + slot, new_caste)
    dgroup.wb(LIFE_PLANE_BASE[2] + idx, new_caste)
    simant_data_group.wb(0x3736 + slot, new_x & 0xFF)
    simant_data_group.wb(0x392C + slot, new_y & 0xFF)

    seed, roll64 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0x3F)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll64 > _sx16(dgroup.rw(0xAC86)):
        _try_eat_food(dgroup, pack, MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98,
                      0x7402, 0xAC86, new_x, new_y)


def _dig_in_b_mode_refresh(dgroup, simant_data_group, pack, caste_sub: int) -> None:
    """Shared early-exit tail `do_dig_in_b` reuses at THREE distinct exit
    points (mode-sub shortcut, edge-of-nest `y == 0x3F`, and a rejected
    dig): refresh the acting slot's `field_c` from `get_new_mode_b`, no
    other state change. Independently confirmed byte-identical at all
    three real-ASM jump targets (seg6:4BED/4C5A, both landing on the SAME
    tail at 4BF6), not assumed from one.
    """
    slot = pack.rw(0x9B6A)
    result = get_new_mode_b(dgroup, simant_data_group, pack, caste_sub)
    simant_data_group.wb(0x3B22 + slot, result & 0xFF)


def do_dig_in_b(dgroup, simant_data_group, pack, x: int, y: int, mode: int,
                caste_sub: int) -> None:
    """A black nest ("B"-list) ant digging its way through the nest: face
    (or dig through) a chosen direction, and either move into the newly-
    opened cell, fight an occupant, or defer to `_YellowFight` — the
    `_DoNestAntB` orchestrator's actual per-tick dig-forward behavior.

    Recovered from `_DoDigInB` (SIMANTW.SYM seg6:4BD0, FAR return, 736
    bytes). FOUR args, not the three the prior scoping survey's summary
    named: `x=[bp+6]`, `y=[bp+8]`, `mode=[bp+10]` (the acting ant's caste/
    mode byte), and a FOURTH, unlisted arg `caste_sub=[bp+12]` — the
    caller's own precomputed `(mode & 0x78) >> 3`, confirmed by its use as
    `get_new_mode_b`'s sole `sub` argument at the very first instruction
    (a plain `push ss:[bp+12]`, no computation from `mode` at all here).
    Composes the already-recovered `get_new_mode_b`, `get_enter_dir_b`,
    `is_it_dirt`, `dig_tile_them_b`, `is_yellow_ant`, `find_in_b_list`,
    `get_out_b`, `get_winner`, `_try_eat_food` (reused verbatim for the
    tile-range-gated food-nibble + colony-growth-trigger tail — its own
    `MAP_PLANE_BASE[2]`/`0x9EA4`/`0xAC82`/`0xAC98`/`0x7402`/`0xAC86`
    argument set matches this routine's own disassembled field accesses
    exactly, byte for byte, confirming both independently), and
    `fix_exit_map_b`.

    `caste_sub NOT in (2, 6)`: an immediate shortcut — calls
    `get_new_mode_b(caste_sub)` into `field_c`
    (`simant_data_group[0x3B22 + slot]`, `slot = pack[0x9B6A]`) and
    returns, before computing anything else (see `_dig_in_b_mode_refresh`).
    SAME polarity as `do_forage_ant`'s own `caste_sub` shortcut (only sub
    `2`/`6` are the "digging" sub-modes that reach the real body below) —
    independently re-verified via a live register dump after the real
    ASM's own `enter`, not assumed from the mnemonic shape alone: the
    `jz` at seg6:4BDA/4BE0 jumps PAST the shortcut (to seg6:4C04, the main
    body) exactly when `caste_sub == 2` or `== 6`, so the two-instruction
    fallthrough at 4BE2 (the shortcut itself) is what runs when NEITHER
    matches — the opposite polarity from an early mis-reading of this
    session, caught by a real-ASM state-diff mismatch before being trusted.

    Otherwise (`caste_sub in (2, 6)`): calls `get_enter_dir_b(x, y, exclude=mode & 7)`; a negative
    result rerolls via `_SRand8()`. `dir_caste = (mode & 0xF8) | direction`
    is stamped onto BOTH the slot's caste field and the CURRENT (not yet
    moved) nest life-grid cell `(x, y)` — a "turn to face" stamp made
    UNCONDITIONALLY, before any of the edge/dig/occupant logic below (this
    exact value, NOT a later re-read, is also what `get_winner` uses as
    its second argument in the fight case below — independently confirmed
    via the raw disassembly, not assumed to always equal a fresh read).

    `y == 0x3F` (bottom nest row, can't go deeper): refreshes `field_c`
    the SAME way the `caste_sub` shortcut does and returns (the "turn to
    face" stamp above still took effect).

    Otherwise: computes `new_x`/`new_y` from `simant_data_group[direction]`/
    `[direction + 8]` (the SAME live compass dx/dy table
    `do_forage_ant`/`get_forage_dir` read — independently confirmed: the
    prior scoping survey's claimed `0xC364`/`0xC366` pointer-globals both
    resolve fresh to SIMANT_DATA_GROUP's own selector, `0x5294`, the SAME
    segment those other two established reads use). Either coordinate out
    of `0..0x3F` is a silent no-op return. `new_y < 1` instead calls
    `get_out_b(x)` and returns UNCONDITIONALLY, discarding its result (the
    real ASM's own `add sp,2; leave; ret` never touches AX afterward).

    Otherwise: reads the nest map tile at `(new_x, new_y)`; `>= 0x30` is a
    silent no-op return. If it's dirt (`is_it_dirt`): calls
    `dig_tile_them_b(new_x, new_y)`; a `0` (rejected) result runs the SAME
    `field_c`-refresh tail as the `y == 0x3F` case and returns; success
    bumps the slot's caste field by `0x18` (byte-wrapping) and sets
    `field_c = 5` — the real ASM also fires the presentation-only
    `GR!_myBeginSound(0x11, 0, 0)` here, omitted per this project's core/
    presentation split (stubbed in the oracle test, same as `_AddFood`'s
    own sound call). Either way (dirt-dug or already-clear), the CURRENT
    `(x, y)` nest life-grid cell is unconditionally cleared to `0` next —
    even on the fight branch below, which otherwise leaves the acting ant
    with no life-grid presence at either its old OR new cell (confirmed
    real, not a porting error: nothing after this write ever re-touches
    the acting slot's own caste/position fields on that branch).

    The `(new_x, new_y)` life-grid occupant is then read; bit `0x80` CLEAR
    (empty, or an own-colony ant — this routine does not special-case a
    non-empty-but-`0x80`-clear cell, confirmed via the raw `test ..., 80h`
    against the RAW byte before any `is_yellow_ant` call) falls straight
    through to the move below. Bit `0x80` SET:

    - `is_yellow_ant(occupant)` and `dgroup[0xCE98] == 0`: ALSO falls
      through to the move (treated as if empty).
    - `is_yellow_ant(occupant)` and `dgroup[0xCE98] != 0`: calls the
      UNRECOVERED `SIMANT1!_YellowFight(2, pack[0x9B6A])` (seg6:823E, same
      `push cs; call near` far-call-emulation idiom as `do_forage_ant`'s
      own gate, and the SAME `(2, slot)` argument pair
      `check_nest_fight_b` already established for this exact call) and
      returns — raises `NotImplementedError` per this project's fail-loud
      rule, matching that established precedent.
    - Not yellow, occupant in `0x88..0xE7`: looks it up via
      `find_in_b_list` (coordinate-role-swap convention, like
      `check_nest_fight_b`: the callee's `y` gets `new_x`, `x` gets
      `new_y`). A miss falls through to the move. A hit resolves
      `get_winner(arg_a=occupant, arg_b=dir_caste)` (the ORIGINAL stamped
      value, not a fresh read — see above): stamps the winner onto the
      found occupant's `field_e`, recomputes its caste as
      `(winner & 0x80) + 0x70` onto both its own caste field and the new-
      position life-grid cell, sets its `field_c = 0x0A`, and returns —
      no move, no growth-tail roll.
    - Not yellow, occupant outside `0x88..0xE7`: falls through to the move
      (same as a miss).

    The move: re-reads the slot's CURRENT caste field (which may already
    reflect the `+0x18` dig-success bump above — a fresh read, not reused
    `dir_caste`, independently confirmed via the raw disassembly), keeps
    its high bits, ORs in `direction`, stamps that at the new position AND
    the slot's caste field, and updates the slot's `x`/`y` fields
    (`[0x3736+slot]`/`[0x392C+slot]` — X and Y respectively for the B-list,
    confirmed by cross-checking `try_move_dir_b`'s own use of the SAME two
    offsets, not `find_in_b_list`'s own doc-comment coordinate-role-swapped
    parameter NAMES).

    Finally (move path only — every abort/fight/yellow-fight-gate return
    above skips this): rolls `_SRand64()`; only when it exceeds
    `dgroup[0xAC86]` does it run `_try_eat_food` at the new position (tile-
    range-gated nibble + growth trigger — see above), then unconditionally
    rolls `_SRand4()`; on an exact `0` (1-in-4), calls
    `fix_exit_map_b(new_x, new_y)`.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    if caste_sub not in (2, 6):
        _dig_in_b_mode_refresh(dgroup, simant_data_group, pack, caste_sub)
        return

    caste_low3 = mode & 7
    direction = get_enter_dir_b(dgroup, simant_data_group, x, y, caste_low3)
    if direction < 0:
        seed, direction = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)

    high_bits = mode & 0xF8
    dir_caste = (high_bits | direction) & 0xFF
    dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, dir_caste)
    slot = pack.rw(0x9B6A)
    simant_data_group.wb(0x3D18 + slot, dir_caste)

    if y == 0x3F:
        _dig_in_b_mode_refresh(dgroup, simant_data_group, pack, caste_sub)
        return

    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
    if not (0 <= new_x <= 0x3F):
        return
    if not (0 <= new_y <= 0x3F):
        return
    if new_y < 1:
        get_out_b(dgroup, simant_data_group, pack, x)
        return

    idx = (new_x << 6) + new_y
    tile = dgroup.rb(MAP_PLANE_BASE[2] + idx)
    if tile >= 0x30:
        return

    if is_it_dirt(tile):
        if not dig_tile_them_b(dgroup, simant_data_group, pack, new_x, new_y):
            _dig_in_b_mode_refresh(dgroup, simant_data_group, pack, caste_sub)
            return
        slot = pack.rw(0x9B6A)
        bumped = (simant_data_group.rb(0x3D18 + slot) + 0x18) & 0xFF
        simant_data_group.wb(0x3D18 + slot, bumped)
        simant_data_group.wb(0x3B22 + slot, 5)
        # GR!_myBeginSound(0x11, 0, 0) omitted -- presentation-only, no sim effect

    dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, 0)

    occupant = dgroup.rb(LIFE_PLANE_BASE[2] + idx)
    if occupant & 0x80:
        if is_yellow_ant(occupant):
            if dgroup.rb(0xCE98) != 0:
                raise NotImplementedError(
                    "do_dig_in_b: _YellowFight branch reached (not recovered) "
                    "-- x={!r} y={!r}".format(x, y))
            # else falls through to the move, below
        elif 0x88 <= occupant <= 0xE7:
            found = find_in_b_list(pack, simant_data_group, new_x, new_y, occupant)
            if found != 0xFFFF:
                winner = get_winner(dgroup, simant_data_group, pack, occupant,
                                    dir_caste) & 0xFF
                simant_data_group.wb(0x3F0E + found, winner)
                new_caste_occ = ((winner & 0x80) + 0x70) & 0xFF
                simant_data_group.wb(0x3D18 + found, new_caste_occ)
                dgroup.wb(LIFE_PLANE_BASE[2] + idx, new_caste_occ)
                simant_data_group.wb(0x3B22 + found, 0x0A)
                return

    slot = pack.rw(0x9B6A)
    cur_caste = simant_data_group.rb(0x3D18 + slot)
    new_caste = ((cur_caste & 0xF8) | direction) & 0xFF
    simant_data_group.wb(0x3D18 + slot, new_caste)
    dgroup.wb(LIFE_PLANE_BASE[2] + idx, new_caste)
    simant_data_group.wb(0x3736 + slot, new_x & 0xFF)
    simant_data_group.wb(0x392C + slot, new_y & 0xFF)

    seed, roll64 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0x3F)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll64 > _sx16(dgroup.rw(0xAC86)):
        _try_eat_food(dgroup, pack, MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98,
                      0x7402, 0xAC86, new_x, new_y)

    seed, roll4 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 3)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll4 == 0:
        fix_exit_map_b(dgroup, simant_data_group, new_x, new_y)


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


def _raid_in(dgroup, simant_data_group, pack, map_base: int, food_count_off: int,
            try_move_dir, get_enter_dir, life_plane_base: int, field_c_off: int,
            caste_off: int, x: int, y: int, exclude_direction: int) -> None:
    """Shared body of `raid_in_b`/`r`: an ant entering the nest carrying
    food, mirroring `raid_out_b`/`r`'s shape for the opposite trip.

    If `(x, y)`'s tile is a food-pile tile (`[0x10, 0x13]`, same as
    `_steal_food`/`_eat_food`): nibbles it exactly like those routines,
    then unconditionally sets the acting ant's `field_c` to `3` and ORs
    `0x08` into its caste (a "carrying food" bit), stamping the updated
    caste onto its OWN current cell — no movement at all on this path.

    Otherwise: tries a move biased by `exclude_direction`
    (`(_SRand1(3) + exclude_direction - 2) & 7`, a genuinely different
    roll — `_SRand1`, not `_SRand8` — from every other "try a direction"
    routine this session); if blocked, tries `get_enter_dir` (falling
    back to a fresh `_SRand1(8)` roll — again `_SRand1`, not the
    pow2-masked `_SRand8`, when it finds nothing); if THAT'S also
    blocked, gives up on moving and instead sets `field_c` to `1`
    (distinct from the food-pile branch's `3`) and re-stamps the
    unchanged caste onto the ant's current cell.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    idx = map_base + (x << 6) + y
    tile = dgroup.rb(idx)
    if 0x10 <= tile <= 0x13:
        if tile == 0x10:
            seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
            dgroup.ww(SRAND_SEED_OFF, seed)
            dgroup.wb(idx, roll)
        else:
            dgroup.wb(idx, (tile - 1) & 0xFF)

        if _sx16(pack.rw(food_count_off)) > 0:
            pack.ww(food_count_off, (pack.rw(food_count_off) - 1) & 0xFFFF)

        acting_slot = pack.rw(0x9B6A)
        simant_data_group.wb(field_c_off + acting_slot, 3)
        new_caste = simant_data_group.rb(caste_off + acting_slot) | 8
        simant_data_group.wb(caste_off + acting_slot, new_caste)
        dgroup.wb(life_plane_base + (x << 6) + y, new_caste & 0xFF)
        return

    seed, roll3 = srand1(dgroup.rw(SRAND_SEED_OFF), 3)
    dgroup.ww(SRAND_SEED_OFF, seed)
    direction = (roll3 + exclude_direction - 2) & 7
    if try_move_dir(dgroup, simant_data_group, pack, x, y, direction):
        return

    result = get_enter_dir(dgroup, simant_data_group, x, y, exclude_direction & 7)
    if result >= 0:
        direction2 = result
    else:
        seed, direction2 = srand1(dgroup.rw(SRAND_SEED_OFF), 8)
        dgroup.ww(SRAND_SEED_OFF, seed)

    if try_move_dir(dgroup, simant_data_group, pack, x, y, direction2):
        return

    acting_slot = pack.rw(0x9B6A)
    simant_data_group.wb(field_c_off + acting_slot, 1)
    caste = simant_data_group.rb(caste_off + acting_slot)
    dgroup.wb(life_plane_base + (x << 6) + y, caste & 0xFF)


def raid_in_b(dgroup, simant_data_group, pack, x: int, y: int,
               exclude_direction: int) -> None:
    """Recovered from `_RaidInB` (SIMANTW.SYM seg6:3524, FAR return, args
    x=[bp+6], y=[bp+8], exclude_direction=[bp+10]). See `_raid_in`.
    """
    _raid_in(dgroup, simant_data_group, pack, MAP_PLANE_BASE[2], 0x9EA4,
            try_move_dir_b, get_enter_dir_b, LIFE_PLANE_BASE[2], 0x3B22,
            0x3D18, x, y, exclude_direction)


def raid_in_r(dgroup, simant_data_group, pack, x: int, y: int,
               exclude_direction: int) -> None:
    """The red-colony twin of `raid_in_b`.

    Recovered from `_RaidInR` (SIMANTW.SYM seg6:5B2A, FAR return, args
    x=[bp+6], y=[bp+8], exclude_direction=[bp+10]).
    """
    _raid_in(dgroup, simant_data_group, pack, MAP_PLANE_BASE[3], 0x72DE,
            try_move_dir_r, get_enter_dir_r, LIFE_PLANE_BASE[3], 0x44F0,
            0x46E6, x, y, exclude_direction)


def _queen_move(dgroup, simant_data_group, pack, plane: int, target_x_off: int,
                target_y_off: int, try_move_dir, life_plane_base: int,
                find_list, y_off: int, x_off: int, caste_off: int,
                marker_add: int, final_transform, x: int, y: int,
                exclude_direction: int) -> int:
    """Shared body of `queen_move_b`/`r`: move the queen one step toward her
    stored target (`pack[target_x_off]`/`[target_y_off]`) via
    `get_best_dir`, falling back to a random direction when already there
    is impossible or no neighbor improves; near the yard's top edge
    (`y < 3`) the chosen direction must be 3-5 (roughly "downward") or the
    whole call is a no-op. On a successful move, clears the OLD trail
    marker one step in `exclude_direction`'s opposite, then relocates that
    marker's ant-list record (if any, and only if it's still alive) to the
    queen's OLD position, restamping it with a transformed direction byte
    — B and R do NOT use the same transform here, ported as passed-in
    constants/callables rather than assumed symmetric.

    Returns `1` on a successful move, `0` on any failure/no-op.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def read_map(pl, mx, my):
        o = map_cell_offset(pl, mx, my)
        return dgroup.rb(o) if o is not None else None

    def read_life(pl, mx, my):
        o = life_cell_offset(pl, mx, my)
        return dgroup.rb(o) if o is not None else None

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    inside = pack.rb(0x9B6E) != 0
    tgt_x = pack.rw(target_x_off)
    tgt_y = pack.rw(target_y_off)
    result = get_best_dir(plane, x, y, tgt_x, tgt_y, read_map, read_life, inside)

    if result >= 0:
        direction = result
    elif result == -1:
        return 0
    else:
        seed, direction = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)

    if y < 3 and not (3 <= direction <= 5):
        return 0

    if not try_move_dir(dgroup, simant_data_group, pack, x, y, direction):
        return 0

    opp_dir = (exclude_direction ^ 4) & 7
    ny2 = y + sx8(simant_data_group.rb(8 + opp_dir))
    nx2 = x + sx8(simant_data_group.rb(opp_dir))
    dgroup.wb(life_plane_base + (nx2 << 6) + ny2, 0)

    marker = ((exclude_direction & 7) + marker_add) & 0xFF
    found_slot = find_list(pack, simant_data_group, nx2, ny2, marker)
    if _sx16(found_slot) >= 0 and simant_data_group.rb(caste_off + found_slot) != 0:
        simant_data_group.wb(y_off + found_slot, x & 0xFF)
        simant_data_group.wb(x_off + found_slot, y & 0xFF)
        new_caste = final_transform(direction)
        simant_data_group.wb(caste_off + found_slot, new_caste)
        dgroup.wb(life_plane_base + (x << 6) + y, new_caste)

    return 1


def queen_move_b(dgroup, simant_data_group, pack, x: int, y: int,
                  exclude_direction: int) -> int:
    """Recovered from `_QueenMoveB` (SIMANTW.SYM seg6:4154, FAR return, args
    x=[bp+6], y=[bp+8], exclude_direction=[bp+10]). See `_queen_move`.
    """
    return _queen_move(dgroup, simant_data_group, pack, 2, 0x7C48, 0x7C90,
                       try_move_dir_b, LIFE_PLANE_BASE[2], find_in_b_list,
                       0x3736, 0x392C, 0x3D18, 0x68,
                       lambda d: (d + 0x68) & 0xFF, x, y, exclude_direction)


def queen_move_r(dgroup, simant_data_group, pack, x: int, y: int,
                  exclude_direction: int) -> int:
    """The red-colony twin of `queen_move_b` — NOT byte-symmetric: the
    marker offset (`0xE8`, not `0x68`) and the final caste transform
    (`direction - 0x18`, not `direction + 0x68`) genuinely differ,
    confirmed by independently disassembling this routine rather than
    assuming symmetry.

    Recovered from `_QueenMoveR` (SIMANTW.SYM seg6:6606, FAR return, args
    x=[bp+6], y=[bp+8], exclude_direction=[bp+10]).
    """
    return _queen_move(dgroup, simant_data_group, pack, 3, 0x9FBA, 0x9FD2,
                       try_move_dir_r, LIFE_PLANE_BASE[3], find_in_r_list,
                       0x4104, 0x42FA, 0x46E6, 0xE8,
                       lambda d: (d - 0x18) & 0xFF, x, y, exclude_direction)


def sim_queen_b(dgroup, simant_data_group, pack, x: int, y: int, mode: int,
                caste_sub: int) -> None:
    """The black queen's own per-tick `_DoNestAntB` dispatch arm: try to
    relocate her, and otherwise decide whether to expand the colony (place
    a new egg) or die.

    Recovered from `_SimQueenB` (SIMANTW.SYM seg6:3DC2, FAR return, 668
    bytes). FOUR args, the SAME `_DoNestAntB` dispatch signature
    `do_dig_in_b` already established: `x=[bp+6]`, `y=[bp+8]`,
    `mode=[bp+10]`, `caste_sub=[bp+12]` (the caller's own precomputed
    `(mode & 0x78) >> 3`). Only `mode == 0x0C` or `== 0x0D` do anything;
    any other value is a complete no-op (confirmed: the real ASM's own
    two back-to-back `cmp`/`jz` gates fall straight through to the shared
    tail with no side effects at all otherwise). Composes the already-
    recovered `queen_move_b`, `find_in_b_list`, `in_nest_bounds`,
    `place_egg_b`, and `dec_eat_b` — the last one reused VERBATIM for this
    routine's own trailing hunger-tick block (`pack[0x7402]`/
    `dgroup[0xAC82]`/`dgroup[0xAC86]`/`simant_data_group[0x8A60]`), whose
    disassembled field accesses match `_DecEatB`'s own byte for byte,
    confirming both independently rather than re-deriving a near-duplicate.
    Two presentation-only calls are omitted per this project's core/
    presentation split, same treatment as `_DoDigInB`'s own
    `GR!_myBeginSound` omission: `SIMANT!_PictStrnDialog(0, 0x271F, 1)`
    (mode 0x0C's starvation-death message) and
    `ANTEDIT!_QueenBalloons(x, y, 2)` (mode 0x0C's post-move speech
    balloon, gated on `simant_data_group[0x85FC] != 0`).

    `mode == 0x0C`: rolls `_SRand64()`. A NONZERO roll (63-in-64, the
    common case) skips `queen_move_b` ENTIRELY and falls straight to the
    occupancy pre-check below — confirmed via the raw disassembly's own
    `or ax,ax` / `jnz` pair, which jumps PAST the `queen_move_b` call site
    on a nonzero roll, not just past the starvation check (an early
    mis-reading of this session's own first draft, caught immediately by
    a real-ASM state-diff mismatch on the SRand LFSR seed itself — the
    two control-flow readings consume a different number of `_SRand*`
    calls and so are trivially distinguishable). Only on an exact `0`
    roll does `dgroup[0xAC86] == 0` (colony has no food at all) get
    checked at all: if so, kills this ant (`simant_data_group[0x3D18 +
    slot] = 0`, the SAME "dead slot" marker `find_in_b_list`'s own
    docstring cites for `kill_tail_b`) and clears her own nest life-grid
    cell to `0`; the presentation dialog fires here but is omitted; the
    routine returns immediately (no further logic, not even the shared
    tail below). If the roll is `0` but `dgroup[0xAC86] != 0`: calls
    `queen_move_b(x, y, caste_sub)`; a nonzero (moved) result returns
    immediately with NO further logic.

    Either way — a nonzero roll (queen_move_b never called), or a `0`
    roll with `queen_move_b` returning `0` (didn't move) — falls through
    to an occupancy pre-check on the cell one step in direction
    `(caste ^ 0xFC) & 7` from her own LIVE caste field
    (`simant_data_group[0x3D18 + slot]`, re-read fresh here, NOT
    `caste_sub`): if that cell already holds a byte equal
    to `(caste + 8) & 0xFF` (a fast direct compare), OR a
    `find_in_b_list(new_x, new_y, caste + 8)` search finds a matching
    slot, the cell counts as "occupied" and nothing else happens. If
    BOTH checks miss ("clear"): kills this ant the same way as the
    starvation branch above AND decrements the queen-count
    `pack[0x78E8]`. Either way, her own `(x, y)` nest cell is
    unconditionally rewritten — to `0` on the "clear"/kill path, or to her
    (unchanged) live caste byte on the "occupied" path (a harmless
    refresh) — confirmed via the raw disassembly: `ax` still holds the
    zeroed-or-original value from whichever branch ran. Finally, if
    `simant_data_group[0x85FC] != 0`, the (omitted) speech-balloon fires;
    either way the routine then returns via the shared 6-byte-stack-
    cleanup tail (reused by the starvation branch's own dialog call).
    NOTE: this occupancy pre-check dereferences the nest life-grid at
    `(new_x, new_y)` with NO bounds check beforehand (unlike
    `_DoDigInB`'s own `0..0x3F` gate) — an edge-adjacent queen can read
    outside the nominal 64x64 window; ported as flat 16-bit-wrapped
    address arithmetic, not artificially bounds-checked.

    `mode == 0x0D`: refreshes her own `(x, y)` nest cell to her live
    caste byte (unconditional, unlike mode 0x0C's conditional refresh).
    If `pack[0x78E8] > 0` (signed — at least one queen tracked): runs a
    SECOND, differently-derived occupancy pre-check one step in direction
    `caste & 7` (no XOR this time — confirmed via the raw disassembly,
    genuinely different from mode 0x0C's `(caste ^ 0xFC) & 7`) against an
    expected byte of `(caste - 8) & 0xFF` (SUBTRACT, not add) via the SAME
    direct-compare-then-`find_in_b_list`-fallback shape as mode 0x0C. If
    that cell is "clear" (both checks miss): decrements `pack[0x78E8]`,
    kills this ant, clears her own nest cell to `0`, and returns
    IMMEDIATELY — no placement attempt at all, and no shared-tail stack
    cleanup needed (this exit path never pushed anything left on the
    stack). If the cell was "occupied" (or `pack[0x78E8] <= 0` skipped the
    check entirely), falls through to a placement attempt using a THIRD
    direction, `(caste_sub ^ 0xFC) & 7` (the caller-supplied arg, like
    `_DoDigInB`'s own dig-direction — NOT a live caste re-read this time):
    computes `(new_x, new_y)`, stamps them onto
    `simant_data_group[0x8362]`/`[0x8364]` (the SAME "recorded dig
    position" field pair `_PlaceBlackQueen` initializes at colony
    founding) ONLY if `in_nest_bounds(new_x, new_y)` (an out-of-bounds
    result is a silent no-op return, no stamp at all). A `pack[0x75FC] & 0x0F`
    throttle gate and an `_SRand128()` roll against `dgroup[0xAC86]`
    (food supply — a roll exceeding the supply aborts) each silently
    return on failure. On success: calls `place_egg_b(new_x, new_y, 1)`,
    then `dec_eat_b()` (the hunger-tick block, see above), then
    unconditionally bumps a 32-bit PACK counter
    (`[0x9AF8]:[0x9AFA]`) before returning via the shared tail.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    def life2(nx: int, ny: int) -> int:
        return (LIFE_PLANE_BASE[2] + (nx << 6) + ny) & 0xFFFF

    if mode not in (0x0C, 0x0D):
        return

    if mode == 0x0C:
        seed, roll64 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0x3F)
        dgroup.ww(SRAND_SEED_OFF, seed)

        if roll64 == 0:
            if dgroup.rw(0xAC86) == 0:
                slot = pack.rw(0x9B6A)
                simant_data_group.wb(0x3D18 + slot, 0)
                dgroup.wb(life2(x, y), 0)
                # SIMANT!_PictStrnDialog(0, 0x271F, 1) omitted -- presentation-only
                return

            if queen_move_b(dgroup, simant_data_group, pack, x, y, caste_sub):
                return

        slot = pack.rw(0x9B6A)
        caste = simant_data_group.rb(0x3D18 + slot)
        direction = (caste ^ 0xFC) & 7
        new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
        new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
        expected = (caste + 8) & 0xFF
        occupant = dgroup.rb(life2(new_x, new_y))
        if occupant == expected:
            ahead_clear = False
        else:
            found = find_in_b_list(pack, simant_data_group, new_x, new_y, expected)
            ahead_clear = (found == 0xFFFF)

        own_val = caste
        if ahead_clear:
            slot = pack.rw(0x9B6A)
            simant_data_group.wb(0x3D18 + slot, 0)
            pack.ww(0x78E8, (pack.rw(0x78E8) - 1) & 0xFFFF)
            own_val = 0

        dgroup.wb(life2(x, y), own_val & 0xFF)

        if simant_data_group.rw(0x85FC) == 0:
            return
        # ANTEDIT!_QueenBalloons(x, y, 2) omitted -- presentation-only
        return

    # mode == 0x0D
    slot = pack.rw(0x9B6A)
    caste = simant_data_group.rb(0x3D18 + slot)
    own_off = life2(x, y)
    dgroup.wb(own_off, caste & 0xFF)

    if _sx16(pack.rw(0x78E8)) > 0:
        direction = caste & 7
        new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
        new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
        expected = (caste - 8) & 0xFF
        occupant = dgroup.rb(life2(new_x, new_y))
        if occupant == expected:
            ahead_clear = False
        else:
            found = find_in_b_list(pack, simant_data_group, new_x, new_y, expected)
            ahead_clear = (found == 0xFFFF)

        if ahead_clear:
            pack.ww(0x78E8, (pack.rw(0x78E8) - 1) & 0xFFFF)
            slot = pack.rw(0x9B6A)
            simant_data_group.wb(0x3D18 + slot, 0)
            dgroup.wb(own_off, 0)
            return

    direction = (caste_sub ^ 0xFC) & 7
    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF

    if not in_nest_bounds(new_x, new_y):
        return

    simant_data_group.ww(0x8362, new_x & 0xFFFF)
    simant_data_group.ww(0x8364, new_y & 0xFFFF)

    if pack.rb(0x75FC) & 0x0F:
        return

    seed, roll128 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0x7F)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll128 > _sx16(dgroup.rw(0xAC86)):
        return

    place_egg_b(dgroup, simant_data_group, pack, new_x, new_y, 1)
    dec_eat_b(dgroup, simant_data_group, pack)

    def inc_dword(view, off: int) -> None:
        v = (view.rw(off) | (view.rw(off + 2) << 16)) + 1
        view.ww(off, v & 0xFFFF)
        view.ww(off + 2, (v >> 16) & 0xFFFF)

    inc_dword(pack, 0x9AF8)


def _nest_ant_b_selfcheck(dgroup, simant_data_group, pack, x: int, y: int,
                          mode: int) -> bool:
    """Shared "did a fight already land on my own cell THIS tick?" check,
    reused at FIVE call sites inside `do_nest_ant_b`'s own-colony branch
    (field_c==0, the field_c>0x11 fallback, field_c==6 when
    `dgroup[0xCE80]==2`, field_c==0xD, and field_c==0xE — all five confirmed
    byte-identical via independent disassembly at seg6:0x2EA3/0x2FEE/
    0x30A4/0x31DD).  Because every acting ant's turn this tick writes
    through the SAME shared black-nest life-grid, an ant ticking late in
    the same frame can find its OWN `(x, y)` cell already overwritten by an
    earlier ant's move or fight resolution — this re-reads that cell fresh
    (not a cached value) and resolves accordingly, exactly mirroring the
    `do_dig_in_b`/`do_food_in_b`/`do_dig_out_b` occupant-fight shape but
    keyed on the ACTING ant's own position instead of a neighbor's.

    A yellow-ant occupant with `dgroup[0xCE98] != 0` defers to the
    UNRECOVERED `SIMANT1!_YellowFight(2, pack[0x9B6A])` (seg6:823E) —
    raises `NotImplementedError` per this tier's established fail-loud
    precedent.  A yellow ant with the gate clear is treated as empty.  An
    occupant in `0x88..0xE7` resolved via `find_in_b_list` at THIS ant's
    own `(x, y)` (the established coordinate-role-swap convention): a hit
    resolves `get_winner(arg_a=occupant, arg_b=mode)`, stamps the winner
    onto the found slot's `field_e`, recomputes its caste as
    `(winner&0x80)+0x70` onto both its own caste field AND this SAME
    `(x, y)` cell (the ACTING ant's own position — not the found slot's own
    recorded position), sets its `field_c=0x0A`, and returns `True`
    ("resolved — do nothing else this tick").  Anything else (empty, out
    of caste range, or a search miss) returns `False` ("clear").
    """
    life_off = LIFE_PLANE_BASE[2] + (x << 6) + y
    occupant = dgroup.rb(life_off)

    if is_yellow_ant(occupant):
        if dgroup.rw(0xCE98) != 0:
            raise NotImplementedError(
                "do_nest_ant_b: _YellowFight branch reached (not recovered) "
                "-- x={!r} y={!r}".format(x, y))
        return False

    if not (0x88 <= occupant <= 0xE7):
        return False

    found = find_in_b_list(pack, simant_data_group, x, y, occupant)
    if found == 0xFFFF:
        return False

    winner = get_winner(dgroup, simant_data_group, pack, occupant, mode) & 0xFF
    simant_data_group.wb(0x3F0E + found, winner)
    new_caste = ((winner & 0x80) + 0x70) & 0xFF
    simant_data_group.wb(0x3D18 + found, new_caste)
    dgroup.wb(life_off, new_caste)
    simant_data_group.wb(0x3B22 + found, 0x0A)
    return True


def do_nest_ant_b(dgroup, simant_data_group, pack, x: int, y: int,
                  mode: int) -> None:
    """Tick the CURRENT black nest-list ant (`pack[0x9B6A]`'s slot) at
    `(x, y)` — the per-tick orchestrator `_DoAntSimB` calls once per live
    B-list slot, and the ~18-arm jump-table dispatcher the whole seg6
    behavior tier exists to unblock.

    Recovered from `_DoNestAntB` (SIMANTW.SYM seg6:2DAE, FAR return, 1910
    bytes, args x=[bp+6], y=[bp+8], mode=[bp+10] — the acting ant's own
    live caste byte, THREE args only, no caller-precomputed `sub`: `sub =
    (mode&0x78)>>3` is computed fresh here, at the very top, matching
    `_DoDigInB`'s own `caste_sub` derivation).

    GENUINE SURPRISE beyond the pre-existing scoping pass (confirmed via a
    fresh from-scratch disassembly of the full 1910-byte body, not assumed):
    the routine is NOT a single 18-arm dispatcher.  `mode & 0x80` gates TWO
    entirely separate top-level bodies that only converge at the shared
    epilogue:

    - `mode & 0x80` CLEAR (the common case — genuine live black castes are
      `< 0x80`, confirmed via `make_blk_queen`/`place_black_queen`'s own
      literal caste constants, e.g. `0x60..0x6F`): the own-colony 18-arm
      dispatch documented below.
    - `mode & 0x80` SET: a SEPARATE ~450-byte body (`_do_nest_ant_b_foreign`)
      for a FOREIGN-colony ant physically occupying a slot in the BLACK
      list — i.e. a raider from the other colony that invaded the black
      nest and was added to the B-list via `raid_in_b`-style mechanics with
      a foreign-flavored caste byte, ticked here using the SAME B-list
      field offsets since it physically lives in this list's coordinate
      space.  See that function's own docstring.

    Both bodies call ONLY already-established primitives (independently
    audited: every unique `call far`/`call near` target across the WHOLE
    1910-byte body resolves to an already-recovered sibling, an inline
    `_SRand*`/`_GetNewMode*`/`_IsYellowAnt`/`_FindInBList`/`_GetWinner`
    primitive, the unrecovered `_YellowFight` raise-loudly gate, or the
    presentation-only `ANTEDIT!_RestBalloons`-family balloon call — no new
    unrecovered dependency was found), so both are fully recovered here
    rather than gated behind a stub.

    ---- Shared prologue (own-colony branch only) ----

    Reads the acting slot's `field_c` (`simant_data_group[0x3B22+slot]`,
    `slot=pack[0x9B6A]`) and unconditionally bumps a per-field_c word tally
    (`pack[0x786A + 2*field_c]` — the SAME "mode population" count array
    `tally_mode_pop`/`clr_mode_pop` already established, confirmed by their
    overlapping address ranges; UI-only bookkeeping, still real PACK state
    a byte-exact oracle must match).

    A starvation gate follows, BEFORE dispatch: rolls `_SRand256()`; only
    on an exact `0` (1-in-256) AND `field_c != 9` does it roll `_SRand32()`
    against `dgroup[0xAC86]` (food supply) — a roll EXCEEDING the supply
    kills this ant outright (caste and life-grid cell cleared, black-death
    counter `pack[0x9B26:0x9B28]` bumped) and returns immediately, no
    dispatch at all.  Ported with the SAME conditional-`_SRand32`-call-
    count discipline this tier's sessions keep re-discovering as a bug
    class: the second roll is NEVER made unless the first roll was exactly
    `0` and `field_c != 9`.

    `field_c > 0x11` (18, out of the table's 18-entry range) and `field_c
    == 0` behave IDENTICALLY (confirmed byte-for-byte via independent
    disassembly of both code sites): a `_SRand32()==0` (1-in-32) refresh of
    `field_c` via `get_new_mode_b(sub)`, then `_nest_ant_b_selfcheck`; a
    "clear" result finishes with `try_move_dir_b(x, y, mode&7)`, retried
    once with a fresh `_SRand8()` direction on failure (return value
    discarded either way — matching `do_nesting_b`'s own `finish()` shape).

    ---- The 18-arm table (`field_c` 0..0x11), each independently traced ----

    - `0`: see above (shares the fallback's own code, not a separate copy).
    - `1`: `do_nesting_b(x, y, mode, sub)`; return value discarded.
    - `2, 5, 7, 0xB, 0xC, 0xF, 0x10` (SEVEN arms, all sharing the identical
      jump-table cell `0x2FCF`): `do_dig_out_b(x, y, mode)`, UNCONDITIONALLY
      — no `dgroup[0xCE80]` gate (unlike `6`, below).
    - `3`: `do_food_in_b(x, y, mode)`.
    - `4`: `do_dig_in_b(x, y, mode, sub)`.
    - `6`: if `dgroup[0xCE80] != 2`: `do_dig_out_b(x, y, mode)` (identical
      to the unconditional arms above). If `dgroup[0xCE80] == 2`: instead
      runs `_nest_ant_b_selfcheck` directly (NO `_SRand32` refresh
      prologue this time, confirmed via independent disassembly at
      seg6:0x2FE0 vs 0x2E95/0x2EA3) and, on "clear", the SAME
      `try_move_dir_b(mode&7)`-with-retry finish as the `0`/fallback arms.
    - `8`: `sim_egg_b(x, y)`.
    - `9`: `sim_queen_b(...)` — see the dedicated note below; the SECOND
      genuine surprise this routine hid.
    - `0xA`: `do_nest_fight_b(x, y)`.
    - `0xD`: `_nest_ant_b_selfcheck`; a resolved fight returns immediately.
      "Clear": UNCONDITIONALLY refreshes the acting ant's own life-grid
      cell to its own (freshly re-read) live caste — a self-heal/reassert
      step distinct from `0`/`6`/`0xE`'s move attempt — then rolls
      `_SRand1(20)`; on a `0` (1-in-20) recomputes `sub` FRESH from the
      acting ant's own LIVE caste (`(caste&0x78)>>3` — NOT the caller's
      `sub`/`mode` args at all, independently confirmed via the raw
      disassembly's own register reload) and refreshes `field_c` via the
      GENERAL `get_new_mode(sub, full_byte=caste)` (not `get_new_mode_b`).
      A nonzero roll (19-in-20) only fires a presentation-only speech-
      balloon (`ANTEDIT!_RestBalloons`-family, gated on
      `simant_data_group[0x85FC]==1`) — omitted, core/presentation split.
    - `0xE`: the SAME `_SRand32()==0` `get_new_mode_b(sub)` refresh
      prologue as `0`/fallback, THEN `_nest_ant_b_selfcheck`.  A resolved
      fight jumps STRAIGHT into the population-cap tail below (no move
      attempt at all — genuinely different polarity from `0xD`'s "resolved
      -> return immediately", independently confirmed via the raw
      disassembly's own `jz`/`jnz` sense at the two sites).  "Clear":
      `try_move_dir_b(x, y, mode&7)` with a `_SRand8()` retry on failure
      (result discarded either way), THEN unconditionally falls into the
      SAME population-cap tail regardless of whether either move attempt
      succeeded.  Population-cap tail: `pack[0x7C44] > 0x64` (100, signed)
      sets `field_c = 0x0F`; otherwise no further change (the field_c the
      move/fight logic above already set, if any, stands).
    - `0x11`: fully INLINED (no `call` instruction at all, confirmed via
      the raw disassembly), but byte-for-byte structurally identical to
      the ALREADY-recovered `do_drown_b`'s own shared body (`_do_drown`) —
      same `< 0x14` non-drowning `get_new_mode_b` refresh, same
      `_SRand1(3)`-rerolled-direction / `_SRand1(100)` 1-in-100 drown
      shape, same `[0x9FC6:0x9FC8]`/`[0x9B26:0x9B28]` colony-keyed 32-bit
      counters — composed here as `do_drown_b(x, y, mode)` rather than
      re-derived, confirming both independently.

    ---- `_SimQueenB` argument-order surprise (arm `9`) ----

    `_SimQueenB`'s own prior recovery (commit `5aa2d91`) documented its
    signature BY ANALOGY to `_DoDigInB`'s (`x=[bp+6], y=[bp+8], mode=[bp+10],
    caste_sub=[bp+12]`) rather than independently re-verifying it against
    THIS caller.  This session's own fresh disassembly of arm `9`'s push
    sequence (`push [bp+10](mode); push [bp-2](sub); push y; push x`) —
    cross-checked against TWO independently-confirmed-correct call shapes
    in the SAME function (arm `1`'s `_DoNestingB` call and arm `4`'s
    `_DoDigInB` call, BOTH of which push `sub` before `mode`, landing `sub`
    at the callee's HIGHEST arg offset per this session's own verified
    push-order-to-frame-offset rule) — found arm `9` pushes `mode` BEFORE
    `sub`, the OPPOSITE order.  That means `_SimQueenB`'s REAL frame is
    `x=[bp+6], y=[bp+8], [bp+10]=sub, [bp+12]=mode` — `mode`/`caste_sub`
    are SWAPPED relative to `sim_queen_b`'s existing parameter NAMES.  This
    does not make the already-shipped, already-oracle-verified
    `sim_queen_b` function itself wrong — its own state-diff tests invoke
    it directly against `_SimQueenB`'s real entry point with a synthetic
    frame, positionally, so byte-exactness only depends on which VALUE
    lands at which OFFSET, never on the Python parameter's NAME. It also
    matches `sim_queen_b`'s own internal logic far better semantically:
    the value checked against literal `0x0C`/`0x0D` (a `sub`-shaped 0..15
    range) is a far more natural `sub` than a full caste byte, and the
    value XOR'd/masked for a compass direction and handed to
    `queen_move_b` is a far more natural full caste (`mode`) than a
    `sub` value. Ported here by calling `sim_queen_b`'s EXISTING
    positional signature with the two values swapped relative to their
    names: `sim_queen_b(x, y, sub, mode)` — i.e. this caller's `sub` lands
    in `sim_queen_b`'s `mode` PARAMETER SLOT (because that parameter is
    positionally `[bp+10]`), and this caller's `mode` lands in
    `sim_queen_b`'s `caste_sub` parameter slot (positionally `[bp+12]`).
    Its own return value is discarded here (matching every other own-
    colony dispatch arm).
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    if mode & 0x80:
        _do_nest_ant_b_foreign(dgroup, simant_data_group, pack, x, y, mode)
        return

    sub = ((mode & 0x78) >> 3) & 0xFFFF

    slot = pack.rw(0x9B6A)
    field_c = simant_data_group.rb(0x3B22 + slot)

    tally_off = (0x786A + 2 * field_c) & 0xFFFF
    pack.ww(tally_off, (pack.rw(tally_off) + 1) & 0xFFFF)

    seed, roll256 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0xFF)
    dgroup.ww(SRAND_SEED_OFF, seed)
    if roll256 == 0 and field_c != 9:
        seed, roll32 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0x1F)
        dgroup.ww(SRAND_SEED_OFF, seed)
        if roll32 > _sx16(dgroup.rw(0xAC86)):
            slot = pack.rw(0x9B6A)
            simant_data_group.wb(0x3D18 + slot, 0)
            dgroup.wb(LIFE_PLANE_BASE[2] + (x << 6) + y, 0)
            _acc_add32(pack, 0x9B26, 0x9B28, 1)
            return

    def finish_move(direction: int) -> None:
        if try_move_dir_b(dgroup, simant_data_group, pack, x, y, direction):
            return
        seed2, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed2)
        try_move_dir_b(dgroup, simant_data_group, pack, x, y, roll8)

    def idle_selfcheck_and_move() -> None:
        if _nest_ant_b_selfcheck(dgroup, simant_data_group, pack, x, y, mode):
            return
        finish_move(mode & 7)

    def refresh_1in32() -> None:
        seed3, roll32b = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 0x1F)
        dgroup.ww(SRAND_SEED_OFF, seed3)
        if roll32b != 0:
            return
        result = get_new_mode_b(dgroup, simant_data_group, pack, sub)
        cur_slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x3B22 + cur_slot, result & 0xFF)

    if field_c > 0x11 or field_c == 0:
        refresh_1in32()
        idle_selfcheck_and_move()
        return
    if field_c == 1:
        do_nesting_b(dgroup, simant_data_group, pack, x, y, mode, sub)
        return
    if field_c in (2, 5, 7, 0xB, 0xC, 0xF, 0x10):
        do_dig_out_b(dgroup, simant_data_group, pack, x, y, mode)
        return
    if field_c == 3:
        do_food_in_b(dgroup, simant_data_group, pack, x, y, mode)
        return
    if field_c == 4:
        do_dig_in_b(dgroup, simant_data_group, pack, x, y, mode, sub)
        return
    if field_c == 6:
        if dgroup.rw(0xCE80) != 2:
            do_dig_out_b(dgroup, simant_data_group, pack, x, y, mode)
            return
        idle_selfcheck_and_move()
        return
    if field_c == 8:
        sim_egg_b(dgroup, simant_data_group, pack, x, y)
        return
    if field_c == 9:
        sim_queen_b(dgroup, simant_data_group, pack, x, y, sub, mode)
        return
    if field_c == 0xA:
        do_nest_fight_b(dgroup, simant_data_group, pack, x, y)
        return
    if field_c == 0xD:
        if _nest_ant_b_selfcheck(dgroup, simant_data_group, pack, x, y, mode):
            return
        life_off = LIFE_PLANE_BASE[2] + (x << 6) + y
        slot2 = pack.rw(0x9B6A)
        own_caste = simant_data_group.rb(0x3D18 + slot2)
        dgroup.wb(life_off, own_caste)
        seed4, roll20 = srand1(dgroup.rw(SRAND_SEED_OFF), 20)
        dgroup.ww(SRAND_SEED_OFF, seed4)
        if roll20 != 0:
            # ANTEDIT _RestBalloons-family speech balloon omitted -- presentation-only
            return
        slot3 = pack.rw(0x9B6A)
        own_caste2 = simant_data_group.rb(0x3D18 + slot3)
        sub2 = (own_caste2 & 0x78) >> 3
        result = get_new_mode(dgroup, simant_data_group, pack, sub2, own_caste2)
        simant_data_group.wb(0x3B22 + slot3, result & 0xFF)
        return
    if field_c == 0xE:
        refresh_1in32()
        if not _nest_ant_b_selfcheck(dgroup, simant_data_group, pack, x, y, mode):
            finish_move(mode & 7)
        if _sx16(pack.rw(0x7C44)) > 0x64:
            slot5 = pack.rw(0x9B6A)
            simant_data_group.wb(0x3B22 + slot5, 0x0F)
        return
    # field_c == 0x11
    do_drown_b(dgroup, simant_data_group, pack, x, y, mode)


def _do_nest_ant_b_foreign(dgroup, simant_data_group, pack, x: int, y: int,
                           mode: int) -> None:
    """`do_nest_ant_b`'s OTHER top-level body: a per-tick behavior for a
    FOREIGN-colony ant occupying a black nest-list ("B-list") slot — a
    raider from the other colony that invaded the black nest, tracked in
    the SAME B-list array/field offsets as genuine black ants (it lives at
    the same physical position in the nest, so it needs the same
    coordinate space), but with a caste byte whose colony bit (`0x80`) is
    SET — a value no genuine black ant ever carries (confirmed via
    `make_blk_queen`/`place_black_queen`'s own literal caste constants,
    all `< 0x80`).

    Reached from `do_nest_ant_b` at seg6:0x335C (the `mode & 0x80` branch).
    Composes the already-recovered `find_in_b_list`, `get_winner`,
    `is_yellow_ant`, `do_nest_fight_b`, `raid_in_b`, and `raid_out_b`; the
    yellow-ant gate raises `NotImplementedError` for the UNRECOVERED
    `_YellowFight`, matching this tier's established precedent.

    Bumps a SEPARATE per-field_c word tally (`pack[0x7BE4 + 2*field_c]` —
    the SAME array `_DoAntSimB`'s own trace increments for THIS colony's
    B-list foreign-caste count, distinct from the own-colony `0x786A`
    array `do_nest_ant_b` bumps).

    `mode > 0xEF` (i.e. exactly the `(winner&0x80)+0x70 = 0xF0` "defeated
    by a red winner" marker every fight-resolution site in this tier
    stamps): calls `do_nest_fight_b(x, y)` directly and returns — the
    SAME "queue up `_DoNestFightB`" mechanism the own-colony branch's
    `field_c==0x0A` arm provides, just reached via the caste byte itself
    here rather than `field_c`.

    Otherwise (`mode` in `0x80..0xEF`): re-reads this slot's own `(x, y)`
    life-grid cell (the SAME same-tick race-check shape
    `_nest_ant_b_selfcheck` uses, but NOT that shared helper — this
    branch's own occupant handling and valid-caste RANGE are genuinely
    different, confirmed via independent disassembly):

    - Occupant is the player's yellow ant: the `_YellowFight` gate here is
      INVERTED relative to every other yellow-fight site in this tier
      (`dgroup[0xCE98] == 0` fires it, not `!= 0` — independently
      confirmed via the raw disassembly's own `cmp`/`jnz` sense, matching
      the SAME inversion `check_nest_fight_r`/`do_rest_r`/`do_rand_r`
      already established for red-flavored logic) — but the call
      ARGUMENTS are the unchanged `(2, slot)` pair, NOT the `(3, slot)`
      those R-flavored routines use (independently confirmed, not
      "corrected" to match that precedent). A gate-clear yellow ant is
      treated as empty (falls to the clear tail below).
    - Occupant in `1..0x67` (a GENUINELY different valid-caste range from
      `_nest_ant_b_selfcheck`'s own `0x88..0xE7` — this branch is looking
      for a genuine LOW-caste, i.e. black-colony, ant at its own position,
      the same "low range on the other colony's grid" shape
      `check_nest_fight_r`'s own `8..0x67` range already established):
      looked up via `find_in_b_list` at this ant's own `(x, y)`. A miss
      falls to the clear tail.  A hit:

        - `1..7` (an unhatched egg/larva stage — the SAME low range
          `sim_egg_b`'s own growth counter uses before its `&0xF==8` hatch
          check): the raider "eats" the egg — sets ITS OWN `field_c=3`
          and ORs `0x08` into its own caste (the SAME "carrying" bit
          `raid_in_b`'s own food-pickup branch sets), stamps that onto
          its own position, clears the found egg's caste to `0` (its OWN
          recorded-position cell is left untouched, unlike the queen case
          below), bumps the SAME 32-bit `pack[0x7C1E:0x7C20]` "egg lost"
          accumulator `sim_egg_b`'s own failed-hatch branch bumps, and
          returns immediately — no combat resolution at all.
        - `0x60..0x67` (the established black-QUEEN caste range —
          `make_blk_queen`/`place_black_queen`'s own `direction+0x60`/
          `+0x68` literals): clears the found queen's caste to `0` AND
          its OWN recorded-position life-grid cell (`[0x3736+found]`/
          `[0x392C+found]`) to `0` — a genuine "kill the queen at her own
          home cell too" extra step no other found-caste range gets —
          then FALLS THROUGH (no early return) into the SAME combat
          resolution below.
        - Anything else in range (`8..0x5F`), or after the queen's own
          extra clear above: resolves `get_winner(arg_a=found's CURRENT
          caste — freshly re-read, so `0` for the just-cleared queen case,
          the untouched original value otherwise — arg_b=mode)`.  The
          RAIDER's OWN caste is unconditionally cleared to `0` regardless
          of who wins (this branch's ants are one-shot: they engage once
          then convert into a defeat-marker at their OWN position, not
          the found ant's), the winner is stamped onto the found slot's
          `field_e`, its caste recomputed as `(winner&0x80)+0x70` onto
          both its own caste field AND — unlike every OTHER fight
          resolution in this tier — the RAIDER's OWN `(x, y)` cell (since
          that is where the raider itself was standing), and `field_c =
          0x0A`.  Returns.

    Clear tail (empty own-position, out-of-range occupant, a search miss,
    or a gate-clear yellow ant): `field_c == 7` calls
    `raid_in_b(x, y, exclude_direction=mode)` (continuing an inbound
    raid); anything else calls `raid_out_b(x, y)` (retreating) — the SAME
    `field_c` values `raid_in_b`'s own body sets on its two branches (`1`
    or `3`), confirming this raider alternates between the two established
    raid primitives depending on its own last-set stage.
    """
    slot = pack.rw(0x9B6A)
    field_c = simant_data_group.rb(0x3B22 + slot)

    tally_off = (0x7BE4 + 2 * field_c) & 0xFFFF
    pack.ww(tally_off, (pack.rw(tally_off) + 1) & 0xFFFF)

    if mode > 0xEF:
        do_nest_fight_b(dgroup, simant_data_group, pack, x, y)
        return

    life_off = LIFE_PLANE_BASE[2] + (x << 6) + y
    occupant = dgroup.rb(life_off)

    handled = False
    if is_yellow_ant(occupant):
        if dgroup.rw(0xCE98) == 0:
            raise NotImplementedError(
                "do_nest_ant_b: foreign-branch _YellowFight reached (not "
                "recovered) -- x={!r} y={!r}".format(x, y))
        # else: dgroup[0xCE98] != 0 -> treated as empty, falls to clear tail
    elif 1 <= occupant <= 0x67:
        found = find_in_b_list(pack, simant_data_group, x, y, occupant)
        if found != 0xFFFF:
            handled = True
            if occupant <= 7:
                slot2 = pack.rw(0x9B6A)
                simant_data_group.wb(0x3B22 + slot2, 3)
                new_caste = (simant_data_group.rb(0x3D18 + slot2) | 8) & 0xFF
                simant_data_group.wb(0x3D18 + slot2, new_caste)
                dgroup.wb(life_off, new_caste)
                simant_data_group.wb(0x3D18 + found, 0)
                _acc_add32(pack, 0x7C1E, 0x7C20, 1)
            else:
                if 0x60 <= occupant <= 0x67:
                    simant_data_group.wb(0x3D18 + found, 0)
                    fx = simant_data_group.rb(0x3736 + found)
                    fy = simant_data_group.rb(0x392C + found)
                    dgroup.wb(LIFE_PLANE_BASE[2] + (fx << 6) + fy, 0)

                found_caste = simant_data_group.rb(0x3D18 + found)
                winner = get_winner(dgroup, simant_data_group, pack,
                                    found_caste, mode) & 0xFF
                slot3 = pack.rw(0x9B6A)
                simant_data_group.wb(0x3D18 + slot3, 0)
                simant_data_group.wb(0x3F0E + found, winner)
                new_found_caste = ((winner & 0x80) + 0x70) & 0xFF
                simant_data_group.wb(0x3D18 + found, new_found_caste)
                dgroup.wb(life_off, new_found_caste)
                simant_data_group.wb(0x3B22 + found, 0x0A)

    if handled:
        return

    if field_c == 7:
        raid_in_b(dgroup, simant_data_group, pack, x, y, mode)
    else:
        raid_out_b(dgroup, simant_data_group, pack, x, y)


def do_ant_sim_b(dgroup, simant_data_group, pack) -> None:
    """Loop the live black ("B") nest-ant list in REVERSE order (last slot
    down to slot `0`), ticking each nonzero-caste slot via `do_nest_ant_b`.

    Recovered from `_DoAntSimB` (SIMANTW.SYM seg6:2D4E, NEAR return, 96
    bytes, NO args).  Composes the already-recovered `do_nest_ant_b`.

    `pack[0x9B6A]` (the SAME "current acting slot" pointer-global every
    B-list behavior routine in this tier reads) doubles as this loop's own
    counter: seeded from `pack[0x99D4]` (the live B-list count, confirmed
    via `ds:[0xC34E]` resolving fresh to the PACK selector, same as every
    other PACK pointer-global this tier uses) and decremented once per
    iteration BEFORE that iteration's slot fields are read — so slot
    `count-1` (the most-recently-added ant) runs first, slot `0` last. A
    non-positive count is a complete no-op (the whole loop is skipped, not
    just each iteration — confirmed via the raw disassembly's own `jle`
    gating the loop's very first entry, before the first decrement).  Per
    slot: reads `simant_data_group[0x3736+slot]` (X), `[0x392C+slot]` (Y,
    masked to a byte), `[0x3D18+slot]` (caste) — the SAME three fields
    `do_nest_ant_b`'s own callees already established.  A caste of exactly
    `0` (a dead/cleared slot — the SAME "dead slot" marker
    `find_in_b_list`'s own docstring cites) skips the call entirely for
    that slot; any other caste calls `do_nest_ant_b(x, y, caste)`.
    """
    count = pack.rw(0x99D4)
    pack.ww(0x9B6A, count & 0xFFFF)
    if _sx16(count) <= 0:
        return

    while True:
        slot = (pack.rw(0x9B6A) - 1) & 0xFFFF
        pack.ww(0x9B6A, slot)
        x = simant_data_group.rb(0x3736 + slot)
        y = simant_data_group.rb(0x392C + slot) & 0xFF
        caste = simant_data_group.rb(0x3D18 + slot)
        if caste != 0:
            do_nest_ant_b(dgroup, simant_data_group, pack, x, y, caste)
        if _sx16(pack.rw(0x9B6A)) <= 0:
            break


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


def do_rand_ant_a(dgroup, simant_data_group, pack, slot: int) -> None:
    """A yard ("A"-list) ant wandering/rand-tasked: heads home if standing on
    a nest entrance, otherwise picks a PURELY RANDOM direction (no scent
    gradient at all, unlike `do_forage_ant`'s `get_forage_dir`) and either
    picks up food (only for a narrower caste-sub gate than `do_forage_ant`'s),
    moves, jitters in place if crowded/blocked, or fights/trophallaxis-gates
    an occupant.

    Recovered from `_DoRandAntA` (SIMANTW.SYM seg6:0E66, arg slot=[bp+4];
    NEAR return, 974 bytes). Composes the already-recovered `is_valid_a`,
    `go_in_nest`, `get_rand_dir`, `pickup_food_a`, `is_yellow_ant`,
    `find_in_a_list`, `get_winner`, `get_new_mode`, `jam_scent_bn`/`rn`,
    `dec_t_smell`, `alarm_here2`, and `_forage_jitter`.

    Nest-entrance check is `do_forage_ant`'s own (valid position, tile
    `0x50` outside / `0x80..0x8F` inside `pack[0x9B6E]`): a match calls
    `go_in_nest` and returns.  Unlike `do_forage_ant`, there is NO
    `_SRand32()`-gated idle short-circuit and NO alarm-territory gate, and NO
    early "caste_sub not in {2, 6}" `get_new_mode` bailout — this routine
    always proceeds straight to a direction roll regardless of caste sub-mode
    (independently confirmed via the raw disassembly's own absence of an
    `_SRand32` call and of the `0x52D2` alarm-grid read anywhere in this
    function's body).

    Direction always comes from `get_rand_dir(x, y, caste&7)` — no
    "stay put" sentinel branch (`get_rand_dir` never returns negative), so
    `new_x`/`new_y` are always computed from the SAME live 8-entry compass
    dx/dy table `do_forage_ant`/`get_best_dir` read.

    The pickup-tile test (`0x48..0x4B` outside / `0x18..0x27` inside
    `pack[0x9B6E]`, same ranges as `do_forage_ant`) is evaluated
    UNCONDITIONALLY, but the actual pickup action additionally requires
    `caste_sub` (`(caste&0x78)>>3`) to be exactly `2` or `6` — a gate
    `do_forage_ant` does NOT have on its own pickup path (there, that same
    `{2, 6}` gate happens much earlier, before the direction roll, so by the
    time it reaches its own pickup check the gate is already guaranteed).
    A pickup-eligible tile with `caste_sub` outside `{2, 6}` jumps STRAIGHT
    to the move/occupant-resolution code below, BYPASSING the crowding
    check entirely (independently confirmed via the raw disassembly's own
    `jnz -> 0x1000`, which skips past the `0xFC0` crowd-check block rather
    than falling into it — a non-pickup tile, by contrast, DOES run the
    crowd check first). On a genuine pickup: stamps `direction | high_bits
    | 0x08` onto both the
    slot's caste field and the OLD-position life-grid cell, calls
    `pickup_food_a(new_x, new_y)`, sets `field_c = 3`, `field_e = 0xC8`, and
    returns.

    Crowding gate: `dest_tile > pack[0x7604]` calls `_forage_jitter` and
    returns (byte-identical computation to `do_forage_ant`'s own crowded
    path, independently confirmed against this routine's own raw
    disassembly at seg6:0FCE) — but WITHOUT `do_forage_ant`'s extra
    `_SRand16()`-gated `get_new_mode` refresh on top; just the jitter.

    Otherwise (clear enough to move): reads the life-grid occupant at
    `(new_x, new_y)`.

    - Occupant `0` (empty): moves exactly like `do_forage_ant`'s own empty
      case (stamp new cell, clear old, update position, conditionally
      decrement `field_e` and jam the NEST scent grid, unconditionally
      `dec_t_smell` at full resolution) — PLUS one extra step
      `do_forage_ant` does NOT have: a fresh `_SRand8()` roll; on a `0`
      (1-in-8) AND `caste_sub` in `{2, 6}`, sets `field_c = 2` (independently
      confirmed via the raw disassembly's own post-move `call far _SRand8`
      and the SAME `{2, 6}` `caste_sub` compares as the pickup gate above).

    - Occupant is the player's yellow ant: same `(caste ^ dgroup[0xCE98]) &
      0x80` gate as `do_forage_ant`; a colony mismatch calls the UNRECOVERED
      `SIMANT1!_YellowFight(slot, 1)` (seg6:823E) and raises
      `NotImplementedError` per this project's fail-loud rule (same
      precedent as `do_forage_ant`). A colony match falls through to the
      SAME trophallaxis gate below as a same-colony non-yellow occupant.

    - Occupant same colony, not yellow (falls through here too): a GENUINELY
      DIFFERENT tail from `do_forage_ant`'s own same-colony branch (which
      just `_forage_jitter`s) — this one, on `pack[0x9AF2] == 1`, stamps
      `high_bits | direction` onto the caste field and OLD-position life
      cell (the `_DoTroph` destination marker) then calls the UNRECOVERED
      `SIMANT!_DoTroph(x, y, direction)` (seg1:846E), raising
      `NotImplementedError` here (same established `try_move_dir_b`/
      `do_forage_ant` precedent) — independently confirmed the real ASM
      falls through to the SAME `_forage_jitter` tail after a hypothetical
      `_DoTroph` return (or immediately, if the gate is false), matching
      `do_forage_ant`'s own documented shape for this exact gate.

    - Occupant is a DIFFERENT colony's ant (not yellow, colony bit
      differs): a fight, structurally identical to `do_forage_ant`'s own
      (clear the acting ant's caste/life cell, `find_in_a_list` the new
      position — a miss ends the routine — `get_winner(occupant, acting)`,
      stamp the winner's colony bit `+0x70` onto the found occupant's caste
      and life cell, `field_c=0x0A`, `field_e=winner`, `alarm_here2(new_x,
      new_y, 0x28)`) — but this routine's OWN same-colony branch (above)
      additionally rerolls a fresh random facing (`_SRand8()`-indexed mode
      table, OR'd with `high_bits`) into the caste field AND life cell at
      the OLD position, THEN calls `get_new_mode(caste_sub, caste)` into
      `field_c` — a GENUINELY DIFFERENT resolution from `do_forage_ant`'s
      own same-colony branch (a plain `_forage_jitter` with no `field_c`
      change at all), independently confirmed via this routine's own raw
      disassembly at seg6:116D-11B6 (not assumed identical from
      `do_forage_ant`'s precedent).
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    x = simant_data_group.rb(0x23A4 + slot)
    y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)

    at_entrance = False
    if is_valid_a(x, y):
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        if pack.rw(0x9B6E) == 0:
            at_entrance = tile == 0x50
        else:
            at_entrance = 0x80 <= tile <= 0x8F

    if at_entrance:
        go_in_nest(dgroup, simant_data_group, pack, x, y, slot)
        return

    high_bits = caste & 0xF8
    caste_sub = (caste & 0x78) >> 3
    caste_low3 = caste & 7

    direction = get_rand_dir(dgroup, simant_data_group, x, y, caste_low3)
    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    dest_tile = dgroup.rb(MAP_PLANE_BASE[0] + (new_x << 6) + new_y)

    if pack.rw(0x9B6E) == 0:
        is_pickup = 0x48 <= dest_tile <= 0x4B
    else:
        is_pickup = 0x18 <= dest_tile <= 0x27

    if is_pickup:
        if caste_sub in (2, 6):
            new_caste = (direction | high_bits | 0x08) & 0xFF
            simant_data_group.wb(0x2F62 + slot, new_caste)
            dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, new_caste)
            simant_data_group.wb(0x2B78 + slot, 3)
            pickup_food_a(dgroup, pack, new_x, new_y)
            simant_data_group.wb(0x334C + slot, 0xC8)
            return
        # pickup-eligible tile, but caste_sub disqualifies the pickup itself:
        # the real ASM jumps STRAIGHT to the move/occupant code below,
        # bypassing the crowding check entirely (independently confirmed via
        # the raw disassembly's own `jnz -> 1000`, which skips past the
        # `0xFC0` crowd-check block rather than falling into it).
    elif dest_tile > pack.rw(0x7604):
        _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
        return

    occupant = dgroup.rb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y)

    if occupant == 0:
        new_caste = (direction | high_bits) & 0xFF
        dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste)
        simant_data_group.wb(0x2F62 + slot, new_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
        simant_data_group.wb(0x23A4 + slot, new_x & 0xFF)
        simant_data_group.wb(0x278E + slot, new_y & 0xFF)

        field_e = simant_data_group.rb(0x334C + slot)
        if field_e != 0:
            field_e = (field_e - 1) & 0xFF
            simant_data_group.wb(0x334C + slot, field_e)
            if caste & 0x80:
                jam_scent_rn(simant_data_group, new_x, new_y, field_e)
            else:
                jam_scent_bn(simant_data_group, new_x, new_y, field_e)

        dec_t_smell(simant_data_group, new_x, new_y, caste & 0x80)

        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        if roll8 == 0 and caste_sub in (2, 6):
            simant_data_group.wb(0x2B78 + slot, 2)
        return

    if is_yellow_ant(occupant):
        if (caste ^ dgroup.rb(0xCE98)) & 0x80:
            raise NotImplementedError(
                "do_rand_ant_a: _YellowFight branch reached (not recovered) "
                "-- slot={!r}".format(slot))
        # same-colony yellow ant -> falls through to the trophallaxis gate
    else:
        if (occupant ^ caste) & 0x80:
            # different colony: fight
            acting_caste = caste
            simant_data_group.wb(0x2F62 + slot, 0)
            dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
            found = find_in_a_list(pack, simant_data_group, new_x, new_y)
            if found == 0xFFFF:
                return
            occupant_caste = simant_data_group.rb(0x2F62 + found)
            winner = get_winner(dgroup, simant_data_group, pack, occupant_caste,
                                acting_caste) & 0xFF
            new_caste_occ = ((winner & 0x80) + 0x70) & 0xFF
            simant_data_group.wb(0x2F62 + found, new_caste_occ)
            dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste_occ)
            simant_data_group.wb(0x2B78 + found, 0x0A)
            simant_data_group.wb(0x334C + found, winner)
            alarm_here2(simant_data_group, new_x, new_y, 0x28)
            return

        # same colony, not yellow: reroll a random facing, then refresh field_c
        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        new_caste = (simant_data_group.rb(0x24 + (caste_low3 << 3) + roll8)
                     | high_bits) & 0xFF
        simant_data_group.wb(0x2F62 + slot, new_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, new_caste)
        result = get_new_mode(dgroup, simant_data_group, pack, caste_sub, caste)
        simant_data_group.wb(0x2B78 + slot, result & 0xFF)
        return

    # occupant was yellow, same colony -> trophallaxis gate then jitter
    if pack.rw(0x9AF2) == 1:
        pre_caste = (high_bits | direction) & 0xFF
        simant_data_group.wb(0x2F62 + slot, pre_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, pre_caste)
        raise NotImplementedError(
            "do_rand_ant_a: _DoTroph branch reached (not recovered) -- "
            "slot={!r}".format(slot))

    _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)


def do_rand_ant_aa(dgroup, simant_data_group, pack, slot: int) -> None:
    """A yard ("A"-list) ant's SIMPLER random-wander tick: the nest-entrance
    check and random-direction roll are `do_rand_ant_a`'s own, but there is
    NO pickup-food logic at all, NO post-move `field_c` reroll, and NO
    trophallaxis gate — same-colony occupants (yellow or not) always just
    reroll a fresh random facing in place.

    Recovered from `_DoRandAntAA` (SIMANTW.SYM seg6:1234, arg slot=[bp+4];
    NEAR return, 588 bytes). Composes the already-recovered `is_valid_a`,
    `go_in_nest`, `get_rand_dir`, `is_yellow_ant`, `find_in_a_list`,
    `get_winner`, `jam_scent_bn`/`rn`, and `_forage_jitter`.

    Nest-entrance check and direction roll are byte-identical in shape to
    `do_rand_ant_a`'s own (same tile ranges, same `get_rand_dir(x, y,
    caste&7)` call, no "stay put" sentinel). Independently confirmed via
    this routine's own raw disassembly that there is NO `PickupFoodA` far
    call anywhere in this function's body — the pickup-tile range check
    `do_rand_ant_a` has is simply absent here.

    Crowding gate (`dest_tile > pack[0x7604]`) calls `_forage_jitter` and
    returns — same computation and same early-return shape as
    `do_rand_ant_a`'s own crowded path.

    Otherwise: reads the life-grid occupant at `(new_x, new_y)`.

    - Occupant `0` (empty): moves (stamp new cell, clear old, update
      position) and returns IMMEDIATELY — independently confirmed via the
      raw disassembly that there is NO `field_e`/jam-scent/`dec_t_smell`
      step here at all (a genuinely simpler empty-move tail than BOTH
      `do_forage_ant`'s and `do_rand_ant_a`'s own).

    - Occupant is the player's yellow ant: same `(caste ^ dgroup[0xCE98]) &
      0x80` gate as `do_rand_ant_a`; a colony mismatch calls the UNRECOVERED
      `SIMANT1!_YellowFight(slot, 1)` and raises `NotImplementedError`
      (same precedent). A colony match falls through to the SAME
      `_forage_jitter` this routine's same-colony/non-yellow branch below
      uses — NOT a trophallaxis gate (independently confirmed: this
      routine's own raw disassembly has no `pack[0x9AF2]` read and no
      `_DoTroph` call anywhere).

    - Occupant same colony, not yellow: `_forage_jitter` (a plain reroll,
      no `get_new_mode`/`field_c` change) — the SAME jitter the
      same-colony-yellow branch above falls into, independently confirmed
      via the raw disassembly's own shared landing address for both cases.

    - Occupant is a DIFFERENT colony's ant (not yellow, colony bit
      differs): a fight, structurally identical to `do_rand_ant_a`'s own
      (clear caste/life cell, `find_in_a_list`, `get_winner`, stamp winner,
      `field_c=0x0A`, `field_e=winner`, `alarm_here2(new_x, new_y, 0x28)`).
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    x = simant_data_group.rb(0x23A4 + slot)
    y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)

    at_entrance = False
    if is_valid_a(x, y):
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        if pack.rw(0x9B6E) == 0:
            at_entrance = tile == 0x50
        else:
            at_entrance = 0x80 <= tile <= 0x8F

    if at_entrance:
        go_in_nest(dgroup, simant_data_group, pack, x, y, slot)
        return

    high_bits = caste & 0xF8
    caste_low3 = caste & 7

    direction = get_rand_dir(dgroup, simant_data_group, x, y, caste_low3)
    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    dest_tile = dgroup.rb(MAP_PLANE_BASE[0] + (new_x << 6) + new_y)

    if dest_tile > pack.rw(0x7604):
        _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
        return

    occupant = dgroup.rb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y)

    if occupant == 0:
        new_caste = (direction | high_bits) & 0xFF
        dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste)
        simant_data_group.wb(0x2F62 + slot, new_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
        simant_data_group.wb(0x23A4 + slot, new_x & 0xFF)
        simant_data_group.wb(0x278E + slot, new_y & 0xFF)
        return

    if is_yellow_ant(occupant):
        if (caste ^ dgroup.rb(0xCE98)) & 0x80:
            raise NotImplementedError(
                "do_rand_ant_aa: _YellowFight branch reached (not recovered) "
                "-- slot={!r}".format(slot))
        _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
        return

    if (occupant ^ caste) & 0x80 == 0:
        _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
        return

    # different colony: fight
    acting_caste = caste
    simant_data_group.wb(0x2F62 + slot, 0)
    dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
    found = find_in_a_list(pack, simant_data_group, new_x, new_y)
    if found == 0xFFFF:
        return
    occupant_caste = simant_data_group.rb(0x2F62 + found)
    winner = get_winner(dgroup, simant_data_group, pack, occupant_caste,
                        acting_caste) & 0xFF
    new_caste_occ = ((winner & 0x80) + 0x70) & 0xFF
    simant_data_group.wb(0x2F62 + found, new_caste_occ)
    dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste_occ)
    simant_data_group.wb(0x2B78 + found, 0x0A)
    simant_data_group.wb(0x334C + found, winner)
    alarm_here2(simant_data_group, new_x, new_y, 0x28)


def do_to_nest_ant(dgroup, simant_data_group, pack, slot: int) -> None:
    """A yard ("A"-list) ant heading toward its own colony's nest: same
    nest-entrance/pickup/crowd shape as `do_rand_ant_a`, but the direction
    comes from `get_nest_dir` (a scent-gradient-or-`get_dir` pick toward the
    nest) instead of a purely random roll, and BOTH the "empty move" and
    "same colony" tails are genuinely simpler than `do_rand_ant_a`'s own.

    Recovered from `_DoToNestAnt` (SIMANTW.SYM seg6:1676, arg slot=[bp+4];
    NEAR return, 916 bytes). Composes the already-recovered `is_valid_a`,
    `go_in_nest`, `get_nest_dir`, `pickup_food_a`, `is_yellow_ant`,
    `find_in_a_list`, `get_winner`, `jam_scent_bn`/`rn`, `alarm_here2`, and
    `_forage_jitter`.

    Nest-entrance check, pickup-tile range test, and the "pickup tile but
    caste_sub not in {2, 6} skips the crowd check entirely" asymmetry are
    ALL the same shape as `do_rand_ant_a`'s own (independently re-confirmed
    against this routine's own raw disassembly, not assumed from that
    precedent) — direction is `get_nest_dir(x, y, caste&7, colony_flag=
    caste)` in place of `get_rand_dir`.

    Empty-occupant move: stamps the new cell, clears the old one, updates
    the slot's position — THEN, unlike `do_forage_ant`/`do_rand_ant_a`,
    there is NO unconditional `dec_t_smell` call and NO post-move
    `_SRand8` field_c-refresh roll (independently confirmed via this
    routine's own raw disassembly's absence of both). A ZERO `field_e`
    returns IMMEDIATELY (no jam-scent attempt at all); a nonzero `field_e`
    decrements it and jams the NEST scent grid (`jam_scent_rn`/`bn`,
    keyed on the ORIGINAL caste's colony bit) at the new position.

    Occupant is the player's yellow ant: same `(caste ^ dgroup[0xCE98]) &
    0x80` gate as `do_rand_ant_a`; a colony mismatch raises
    `NotImplementedError` for the UNRECOVERED `SIMANT1!_YellowFight(slot,
    1)` (seg6:823E). A colony match falls through to the trophallaxis gate
    below.

    Occupant same colony, not yellow: a PLAIN `_forage_jitter` — no
    `get_new_mode`/`field_c` change at all (independently confirmed via
    this routine's own raw disassembly's absence of any `GetNewMode` call
    anywhere in its body; a genuinely different resolution from
    `do_rand_ant_a`'s own same-colony-nonyellow branch, which DOES call
    `get_new_mode`).

    Occupant was the player's yellow ant, same colony: `pack[0x9AF2]==1`
    stamps `high_bits | direction` onto the caste field and OLD-position
    life cell, then calls the UNRECOVERED `SIMANT!_DoTroph(x, y,
    direction)` (seg1:846E) — raises `NotImplementedError` here, same
    established precedent as `do_rand_ant_a`'s own trophallaxis gate
    (the real ASM falls through to the SAME plain `_forage_jitter` tail
    either way, independently confirmed via the raw disassembly's shared
    jump target for both the gate-false and post-`_DoTroph` paths).

    Occupant a DIFFERENT colony's ant (not yellow): a fight, structurally
    identical to `do_rand_ant_a`'s own (clear the acting ant's caste/life
    cell, `find_in_a_list` the new position — a miss ends the routine —
    `get_winner(occupant, acting)`, stamp the winner's colony bit `+0x70`
    onto the found occupant's caste/life cell, `field_c=0x0A`,
    `field_e=winner`, `alarm_here2(new_x, new_y, 0x28)`).
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def sx8(v: int) -> int:
        v &= 0xFF
        return v - 0x100 if v & 0x80 else v

    x = simant_data_group.rb(0x23A4 + slot)
    y = simant_data_group.rb(0x278E + slot)
    caste = simant_data_group.rb(0x2F62 + slot)

    at_entrance = False
    if is_valid_a(x, y):
        tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
        if pack.rw(0x9B6E) == 0:
            at_entrance = tile == 0x50
        else:
            at_entrance = 0x80 <= tile <= 0x8F

    if at_entrance:
        go_in_nest(dgroup, simant_data_group, pack, x, y, slot)
        return

    high_bits = caste & 0xF8
    caste_sub = (caste & 0x78) >> 3
    caste_low3 = caste & 7

    direction = get_nest_dir(dgroup, simant_data_group, x, y, caste_low3, caste)
    new_x = (x + sx8(simant_data_group.rb(direction))) & 0xFFFF
    new_y = (y + sx8(simant_data_group.rb(8 + direction))) & 0xFFFF
    dest_tile = dgroup.rb(MAP_PLANE_BASE[0] + (new_x << 6) + new_y)

    if pack.rw(0x9B6E) == 0:
        is_pickup = 0x48 <= dest_tile <= 0x4B
    else:
        is_pickup = 0x18 <= dest_tile <= 0x27

    if is_pickup:
        if caste_sub in (2, 6):
            new_caste = (direction | high_bits | 0x08) & 0xFF
            simant_data_group.wb(0x2F62 + slot, new_caste)
            dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, new_caste)
            simant_data_group.wb(0x2B78 + slot, 3)
            pickup_food_a(dgroup, pack, new_x, new_y)
            simant_data_group.wb(0x334C + slot, 0xC8)
            return
        # pickup-eligible tile, caste_sub disqualifies pickup: skip the
        # crowd check and fall straight through to the move/occupant code
        # (same asymmetry as do_rand_ant_a's own).
    elif dest_tile > pack.rw(0x7604):
        _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
        return

    occupant = dgroup.rb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y)

    if occupant == 0:
        new_caste = (direction | high_bits) & 0xFF
        dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste)
        simant_data_group.wb(0x2F62 + slot, new_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
        simant_data_group.wb(0x23A4 + slot, new_x & 0xFF)
        simant_data_group.wb(0x278E + slot, new_y & 0xFF)

        field_e = simant_data_group.rb(0x334C + slot)
        if field_e == 0:
            return
        field_e = (field_e - 1) & 0xFF
        simant_data_group.wb(0x334C + slot, field_e)
        if caste & 0x80:
            jam_scent_rn(simant_data_group, new_x, new_y, field_e)
        else:
            jam_scent_bn(simant_data_group, new_x, new_y, field_e)
        return

    if is_yellow_ant(occupant):
        if (caste ^ dgroup.rb(0xCE98)) & 0x80:
            raise NotImplementedError(
                "do_to_nest_ant: _YellowFight branch reached (not recovered) "
                "-- slot={!r}".format(slot))
        # same-colony yellow ant -> falls through to the trophallaxis gate
    else:
        if (occupant ^ caste) & 0x80:
            # different colony: fight
            acting_caste = caste
            simant_data_group.wb(0x2F62 + slot, 0)
            dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, 0)
            found = find_in_a_list(pack, simant_data_group, new_x, new_y)
            if found == 0xFFFF:
                return
            occupant_caste = simant_data_group.rb(0x2F62 + found)
            winner = get_winner(dgroup, simant_data_group, pack, occupant_caste,
                                acting_caste) & 0xFF
            new_caste_occ = ((winner & 0x80) + 0x70) & 0xFF
            simant_data_group.wb(0x2F62 + found, new_caste_occ)
            dgroup.wb(LIFE_PLANE_BASE[0] + (new_x << 6) + new_y, new_caste_occ)
            simant_data_group.wb(0x2B78 + found, 0x0A)
            simant_data_group.wb(0x334C + found, winner)
            alarm_here2(simant_data_group, new_x, new_y, 0x28)
            return

        # same colony, not yellow: plain jitter, no field_c change
        _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)
        return

    # occupant was yellow, same colony -> trophallaxis gate then jitter
    if pack.rw(0x9AF2) == 1:
        pre_caste = (high_bits | direction) & 0xFF
        simant_data_group.wb(0x2F62 + slot, pre_caste)
        dgroup.wb(LIFE_PLANE_BASE[0] + (x << 6) + y, pre_caste)
        raise NotImplementedError(
            "do_to_nest_ant: _DoTroph branch reached (not recovered) -- "
            "slot={!r}".format(slot))

    _forage_jitter(dgroup, simant_data_group, slot, x, y, caste_low3, high_bits)


def do_repo_exit(dgroup, simant_data_group, pack, slot: int) -> None:
    """A yard ("A"-list) ant deciding how to head home: dispatches to
    `do_to_nest_ant` when its own colony's NEST scent at its current cell is
    still low (`< 100`), else to `do_rand_ant_aa` (scent trail must have
    gone cold — wander randomly instead of following it) — then, on a
    per-colony population-cap roll, force-overrides the (possibly
    just-written) `field_c` to `0x10`.

    Recovered from `_DoRepoExit` (SIMANTW.SYM seg6:0xC7A, arg slot=[bp+4];
    NEAR return, 208 bytes). Composes the already-recovered `do_to_nest_ant`
    and `do_rand_ant_aa`.

    NEST-scent gate: reads the acting ant's colony bit
    (`simant_data_group[0x2F62+slot] & 0x80`) and current position, indexes
    the SAME half-res NEST scent grid `get_nest_dir` reads (`[0x72D2..)`
    red / `[0x62D2..)` black, `(x>>1)*32 + (y>>1)` — independently confirmed
    the `(x&0xFE)<<4 + (y>>1)` the raw ASM computes is byte-address-
    identical to that `(x>>1)<<5 + (y>>1)` formula) at the ant's OWN cell:
    `< 100` calls `do_to_nest_ant(slot)`, `>= 100` calls `do_rand_ant_aa
    (slot)`. Neither composed call's return value is used.

    Population-cap tail: re-reads the colony bit FRESH from
    `simant_data_group[0x2F62+slot]` (independently confirmed via the raw
    disassembly's own fresh `es:[si+12130]` test AFTER the dispatch call —
    not a cached pre-dispatch value, so a fight inside the composed call
    that clears the acting ant's own caste field changes which population
    counter this tail reads) and picks the matching PACK-resident
    population counter: `pack[0x8078]` (red) / `pack[0x7C44]` (black) — the
    SAME two counters `clr_mode_pop` decrements each tick.  A counter of
    `0` is a no-op (return as-is). A counter of exactly `1` is treated as an
    UNCONDITIONAL "population capped" hit WITHOUT a roll — the real ASM
    special-cases this because `_SRand1(1)` would deterministically return
    `0` anyway (`seed % 1 == 0`), so it skips the call entirely (independently
    confirmed via the raw disassembly's own `cmp ..., 1; jz` branching
    straight to the override, bypassing the `_SRand1` call site). Any
    OTHER count calls `_SRand1(count)`: a `0` result (or the forced `count
    == 1` case) writes `field_c = 0x10` onto `pack[0x9B6A]`'s CURRENT
    acting-slot's `simant_data_group[0x2B78+...]` — read fresh from
    `pack[0x9B6A]` rather than reusing this routine's own `slot` argument
    (independently confirmed via the raw disassembly's own `es:[9B6A]`
    read at the write site, matching the SAME "current acting slot"
    pointer-global `do_ant_sim_b`'s own loop uses) — any nonzero roll is a
    no-op.
    """
    from .simone import SRAND_SEED_OFF, srand1

    colony = simant_data_group.rb(0x2F62 + slot) & 0x80
    x = simant_data_group.rb(0x23A4 + slot)
    y = simant_data_group.rb(0x278E + slot)
    idx = ((x & 0xFE) << 4) + (y >> 1)
    nest_scent = simant_data_group.rb((0x72D2 if colony else 0x62D2) + idx)

    if nest_scent < 100:
        do_to_nest_ant(dgroup, simant_data_group, pack, slot)
    else:
        do_rand_ant_aa(dgroup, simant_data_group, pack, slot)

    colony2 = simant_data_group.rb(0x2F62 + slot) & 0x80
    count = pack.rw(0x8078 if colony2 else 0x7C44)

    if count == 0:
        return
    if count == 1:
        hit = True
    else:
        seed, roll = srand1(dgroup.rw(SRAND_SEED_OFF), count)
        dgroup.ww(SRAND_SEED_OFF, seed)
        hit = roll == 0

    if hit:
        acting_slot = pack.rw(0x9B6A)
        simant_data_group.wb(0x2B78 + acting_slot, 0x10)


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


def do_nesting_b(dgroup, simant_data_group, pack, x: int, y: int, mode: int,
                 sub: int) -> int:
    """Tick a black ant that is digging/tending the nest at `(x, y)` — the
    largest orchestrator recovered this session. Dispatches on `sub` (0/1/2)
    into three genuinely different behaviors, then always finishes by
    attempting a move via `try_move_dir_b` (retrying once with a fresh
    `_SRand8()` direction if the first attempt is rejected).

    Recovered from `_DoNestingB` (SIMANTW.SYM seg6:44A8, args x=[bp+6],
    y=[bp+8], mode=[bp+10], sub=[bp+12]; FAR return). Composes
    `get_enter_dir_b`, `get_exit_dir_b`, `place_egg_b`, `find_in_b_list`,
    `get_new_mode_b`, and `try_move_dir_b` — all already recovered.

    Up front (regardless of `sub`): reads the acting ant's own
    `simant_data_group[0x3F0E+slot]` ("field_e") once, splitting it into a
    low-3-bit `mode2` and a `field_e >> 3` "sub_field" gate, and the current
    B-nest life-plane tile at `(x, y)`.

    `sub == 1`: if `sub_field == 0`, tries `get_enter_dir_b`; success moves
    that way, failure stamps `field_e = mode2 | 8` and attempts a no-op
    move. Otherwise (`sub_field != 0`): if the tile is `0` or `> 8` (empty
    or a stale stage), bumps the acting ant's own caste `+8`, calls
    `place_egg_b(mode2)`, re-stamps the life-plane tile with the resulting
    caste, sets `field_e = 8`, and refreshes `field_c` via
    `get_new_mode_b`; either way then tries `get_exit_dir_b` — success
    moves that way, failure burns one `_SRand8()` roll and falls back to
    `mode`.

    `sub == 2`: if `sub_field != 0`, a `_SRand8()` roll of `0` clears
    `field_e` to `0`, then (regardless) tries `get_exit_dir_b` the same
    way `sub == 1`'s tail does. If `sub_field == 0` AND the tile is in
    `1..7`: looks the tile up via `find_in_b_list` (coordinate-role-swap
    convention: the callee's `y` gets THIS routine's `x` and vice versa);
    a hit clears that slot's caste, subtracts `8` from the ACTING ant's own
    caste, stamps its `field_e` to the tile value, and returns immediately
    — `0x5E00 | tile`, a genuine AH-clobber artifact (the compiler loads
    `AX = 0x5EF3`, the PACK segment literal, right before the final
    byte-only `mov al,...` and never clears AH before this early
    `ret far`; independently confirmed via the raw disassembly, not
    guessed). A tile in `1..7` that ISN'T found in the list falls straight
    to `mode` with no erosion/refresh at all. Only `sub_field == 0` with
    tile `0` or `>= 8` reaches a shared `_SRand1(100)`-gated "hole
    erosion" step (below) — if it doesn't fire, a `_SRand16()` roll of `0`
    refreshes `field_c` via `get_new_mode_b` instead (the two are
    mutually exclusive here, unlike `sub == 0`'s unconditional refresh).

    `sub == 0` (or any other value, the default): the SAME hole-erosion
    step always runs, followed by an UNCONDITIONAL `_SRand8()`-gated
    `field_c` refresh (both may fire in the same tick, unlike `sub == 2`'s
    either/or). The hole-erosion step itself: on a `_SRand1(100)` roll
    exceeding `dgroup[0xAC86]`, and only if the B-nest MAP tile at
    `(x, y)` is `0x10..0x13`: replaces a `0x10` tile with a fresh
    `_SRand8()` roll or decrements any other in-range tile by `1`, then
    ages a small dig-progress/food-supply state machine
    (`pack[0x9EA4]`/`pack[0x7402]`/`dgroup[0xAC82]`/`dgroup[0xAC98]`,
    capping `dgroup[0xAC86]` at `100` — the SAME food-supply counter
    `dec_eat_b` drains).
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    mode7 = mode & 7
    slot = pack.rw(0x9B6A)
    field_e = simant_data_group.rb(0x3F0E + slot)
    mode2 = field_e & 7
    sub_field = field_e >> 3
    life_idx = (x << 6) + y
    tile = dgroup.rb(LIFE_PLANE_BASE[2] + life_idx)

    def roll100_over_threshold():
        seed, roll100 = srand1(dgroup.rw(SRAND_SEED_OFF), 100)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return roll100 > dgroup.rw(0xAC86)

    def erode_hole():
        map_tile = dgroup.rb(MAP_PLANE_BASE[2] + life_idx)
        if not (0x10 <= map_tile <= 0x13):
            return
        if map_tile == 0x10:
            seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
            dgroup.ww(SRAND_SEED_OFF, seed)
            dgroup.wb(MAP_PLANE_BASE[2] + life_idx, roll8)
        else:
            dgroup.wb(MAP_PLANE_BASE[2] + life_idx, (map_tile - 1) & 0xFF)
        if pack.rw(0x9EA4) > 0:
            pack.ww(0x9EA4, (pack.rw(0x9EA4) - 1) & 0xFFFF)
        threshold = (dgroup.rw(0xAC82) + dgroup.rw(0xAC98)) >> 4
        pack.ww(0x7402, (pack.rw(0x7402) + 5) & 0xFFFF)
        if threshold < pack.rw(0x7402):
            pack.ww(0x7402, 0)
            if dgroup.rw(0xAC86) < 0x64:
                dgroup.ww(0xAC86, (dgroup.rw(0xAC86) + 1) & 0xFFFF)

    def refresh_field_c(mask):
        seed, roll = srand_pow2(dgroup.rw(SRAND_SEED_OFF), mask)
        dgroup.ww(SRAND_SEED_OFF, seed)
        if roll == 0:
            cur_slot = pack.rw(0x9B6A)
            field_c = get_new_mode_b(dgroup, simant_data_group, pack, sub)
            simant_data_group.wb(0x3B22 + cur_slot, field_c & 0xFF)

    def burn_srand8():
        seed, _ = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)

    def finish(direction):
        result = try_move_dir_b(dgroup, simant_data_group, pack, x, y, direction)
        if result != 0:
            return result
        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return try_move_dir_b(dgroup, simant_data_group, pack, x, y, roll8)

    if sub == 1:
        if sub_field == 0:
            direction = get_enter_dir_b(dgroup, simant_data_group, x, y, mode7)
            if direction < 0:
                simant_data_group.wb(0x3F0E + slot, (mode2 | 8) & 0xFF)
            return finish(direction)

        if not (1 <= tile <= 8):
            new_caste = (simant_data_group.rb(0x3D18 + slot) + 8) & 0xFF
            simant_data_group.wb(0x3D18 + slot, new_caste)
            place_egg_b(dgroup, simant_data_group, pack, x, y, mode2)
            slot2 = pack.rw(0x9B6A)
            caste_now = simant_data_group.rb(0x3D18 + slot2)
            dgroup.wb(LIFE_PLANE_BASE[2] + life_idx, caste_now)
            simant_data_group.wb(0x3F0E + slot2, 8)
            field_c = get_new_mode_b(dgroup, simant_data_group, pack, sub)
            simant_data_group.wb(0x3B22 + slot2, field_c & 0xFF)

        exit_dir = get_exit_dir_b(dgroup, simant_data_group, x, y, mode7)
        if exit_dir != 0:
            return finish(exit_dir - 1)
        burn_srand8()
        return finish(mode7)

    if sub == 2:
        if sub_field != 0:
            seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
            dgroup.ww(SRAND_SEED_OFF, seed)
            if roll8 == 0:
                simant_data_group.wb(0x3F0E + slot, 0)
            exit_dir = get_exit_dir_b(dgroup, simant_data_group, x, y, mode7)
            if exit_dir != 0:
                return finish(exit_dir - 1)
            burn_srand8()
            return finish(mode7)

        if 1 <= tile <= 7:
            found = find_in_b_list(pack, simant_data_group, y=x, x=y, caste=tile)
            if found != 0xFFFF:
                simant_data_group.wb(0x3D18 + found, 0)
                cur_slot = pack.rw(0x9B6A)
                simant_data_group.wb(
                    0x3D18 + cur_slot,
                    (simant_data_group.rb(0x3D18 + cur_slot) - 8) & 0xFF)
                simant_data_group.wb(0x3F0E + cur_slot, tile & 0xFF)
                return 0x5E00 | (tile & 0xFF)
            return finish(mode7)

        if roll100_over_threshold():
            erode_hole()
        else:
            refresh_field_c(15)
        return finish(mode7)

    # sub == 0 (or any other value): the default path.
    if roll100_over_threshold():
        erode_hole()
    refresh_field_c(7)
    return finish(mode7)


def do_nesting_r(dgroup, simant_data_group, pack, x: int, y: int, mode: int,
                 sub: int) -> int:
    """The red-colony twin of `do_nesting_b` — NOT a mechanical
    table-swap: every branch is independently, differently shaped (no
    upfront `field_e`/`sub_field`/tile read shared across cases, a THIRD
    `_SRand4()` gate B never rolls, an unconditional single `get_new_mode_r`
    refresh on the default path where B does a two-step erosion-then-roll,
    and a genuinely clean early-return value instead of B's AH-clobber
    artifact) — confirmed by independent disassembly, not assumed
    symmetric.

    Recovered from `_DoNestingR` (SIMANTW.SYM seg6:690A, args x=[bp+6],
    y=[bp+8], mode=[bp+10], sub=[bp+12]; FAR return). Composes
    `get_enter_dir_r`, `place_egg_r`, `find_in_r_list`, `get_new_mode_r`,
    and `try_move_dir_r` — all already recovered. Finishes the same way
    `do_nesting_b` does: `try_move_dir_r`, retried once with a fresh
    `_SRand8()` direction on a `0` result.

    `sub == 1`: a `_SRand4()` roll of `0`, AND the R-nest MAP tile at
    `(x, y)` being `< 0x10`, digs a fresh egg in place — bumps the acting
    ant's own caste `+8`, stamps the life-plane tile with it, calls
    `place_egg_r(caste=0x82)`, resets `field_e` to `0`, and returns
    `get_new_mode_r(sub)`'s result DIRECTLY (an early, clean return — no
    move attempt at all). Otherwise: a second independent `_SRand4()` roll
    of `0` tries `get_enter_dir_r`; a non-negative result finishes with
    it, anything else (either roll failing, or a negative result) falls
    back to a fresh `_SRand8()` roll as the direction.

    `sub == 2`: a `_SRand4()` roll of `0` gates everything below — on a
    miss, no erosion/refresh/search runs at all, straight to the final
    gate. On a hit: if the R-nest life-plane tile at `(x, y)` is `0` OR
    `(tile & 0x7F) >= 8`, runs the SAME `_SRand1(100)`-gated hole-erosion
    step `do_nesting_b`'s default path uses (own R-side counters:
    `pack[0x72DE]`/`pack[0x7C8E]`/`dgroup[0xAC84]`/`dgroup[0xACA4]`/
    `dgroup[0xAC88]`), mutually exclusive with an UNCONDITIONAL (no extra
    dice roll) `get_new_mode_r` refresh when the erosion roll itself
    doesn't clear the threshold. Otherwise (tile nonzero and `< 8`), looks
    the tile up via `find_in_r_list` (same coordinate-role-swap
    convention as `do_nesting_b`'s); a hit clears that slot's caste,
    subtracts `8` from the acting ant's own caste, and returns the found
    slot index DIRECTLY (early return, genuinely no clobber quirk here —
    R never reloads a segment via a literal immediate in this branch); a
    MISS runs neither erosion nor refresh, falling straight to the final
    gate. That final gate: a THIRD `_SRand4()` roll of `0` picks a fresh
    `_SRand8()` direction, otherwise falls back to `mode`.

    `sub == 0` (or any other value): no erosion step at all — just an
    unconditional `get_new_mode_r(sub)` refresh, then finishes with
    `mode` untouched.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    mode7 = mode & 7

    def finish(direction):
        result = try_move_dir_r(dgroup, simant_data_group, pack, x, y, direction)
        if result != 0:
            return result
        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return try_move_dir_r(dgroup, simant_data_group, pack, x, y, roll8)

    def roll4_zero():
        seed, roll4 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 3)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return roll4 == 0

    def fresh_srand8_dir():
        seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
        dgroup.ww(SRAND_SEED_OFF, seed)
        return roll8

    def erode_or_refresh():
        seed, roll100 = srand1(dgroup.rw(SRAND_SEED_OFF), 100)
        dgroup.ww(SRAND_SEED_OFF, seed)
        life_idx = (x << 6) + y
        if roll100 > dgroup.rw(0xAC88):
            map_tile = dgroup.rb(MAP_PLANE_BASE[3] + life_idx)
            if 0x10 <= map_tile <= 0x13:
                if map_tile == 0x10:
                    seed, roll8 = srand_pow2(dgroup.rw(SRAND_SEED_OFF), 7)
                    dgroup.ww(SRAND_SEED_OFF, seed)
                    dgroup.wb(MAP_PLANE_BASE[3] + life_idx, roll8)
                else:
                    dgroup.wb(MAP_PLANE_BASE[3] + life_idx, (map_tile - 1) & 0xFF)
                if pack.rw(0x72DE) > 0:
                    pack.ww(0x72DE, (pack.rw(0x72DE) - 1) & 0xFFFF)
                threshold = (dgroup.rw(0xAC84) + dgroup.rw(0xACA4)) >> 4
                pack.ww(0x7C8E, (pack.rw(0x7C8E) + 5) & 0xFFFF)
                if threshold < pack.rw(0x7C8E):
                    pack.ww(0x7C8E, 0)
                    if dgroup.rw(0xAC88) < 0x64:
                        dgroup.ww(0xAC88, (dgroup.rw(0xAC88) + 1) & 0xFFFF)
        else:
            field_c = get_new_mode_r(dgroup, simant_data_group, pack, sub)
            slot = pack.rw(0x9B6A)
            simant_data_group.wb(0x44F0 + slot, field_c & 0xFF)

    if sub == 1:
        life_idx = (x << 6) + y
        if roll4_zero() and dgroup.rb(MAP_PLANE_BASE[3] + life_idx) < 0x10:
            slot = pack.rw(0x9B6A)
            new_caste = (simant_data_group.rb(0x46E6 + slot) + 8) & 0xFF
            simant_data_group.wb(0x46E6 + slot, new_caste)
            dgroup.wb(LIFE_PLANE_BASE[3] + life_idx, new_caste)
            place_egg_r(dgroup, simant_data_group, pack, x, y, 0x82)
            slot = pack.rw(0x9B6A)
            simant_data_group.wb(0x48DC + slot, 0)
            field_c = get_new_mode_r(dgroup, simant_data_group, pack, sub)
            slot = pack.rw(0x9B6A)
            simant_data_group.wb(0x44F0 + slot, field_c & 0xFF)
            return field_c

        if roll4_zero():
            direction = get_enter_dir_r(dgroup, simant_data_group, x, y, mode7)
            if direction >= 0:
                return finish(direction)
        return finish(fresh_srand8_dir())

    if sub == 2:
        if roll4_zero():
            life_idx = (x << 6) + y
            tile = dgroup.rb(LIFE_PLANE_BASE[3] + life_idx)
            if tile == 0 or (tile & 0x7F) >= 8:
                erode_or_refresh()
            else:
                found = find_in_r_list(pack, simant_data_group, y=x, x=y,
                                       caste=tile)
                if found != 0xFFFF:
                    simant_data_group.wb(0x46E6 + found, 0)
                    slot = pack.rw(0x9B6A)
                    simant_data_group.wb(
                        0x46E6 + slot,
                        (simant_data_group.rb(0x46E6 + slot) - 8) & 0xFF)
                    return found
                # not found: no erosion/refresh, fall to the final gate below
        if roll4_zero():
            return finish(fresh_srand8_dir())
        return finish(mode7)

    # sub == 0 (or any other value): the default path.
    field_c = get_new_mode_r(dgroup, simant_data_group, pack, sub)
    slot = pack.rw(0x9B6A)
    simant_data_group.wb(0x44F0 + slot, field_c & 0xFF)
    return finish(mode7)


def get_my_dir(dgroup, simant_data_group, pack, plane: int, cur_x: int,
               cur_y: int, sub: int, tgt_x: int, tgt_y: int) -> int:
    """Resolve the player-controlled ("my") ant's next compass direction —
    picking, per `sub`, EITHER the caller-supplied `(tgt_x, tgt_y)` or one
    of four fixed SIMANT_DATA_GROUP "alternate destination" table entries,
    then running the SAME `pack[0x72E4]`-gated probe `get_my_best_dir`
    (cont.157) already established: while the stuck-sentinel is
    non-negative, try `check_my_best_dirs`; once it goes negative, try a
    single fresh `get_my_best_dirs`, escalating to `get_my_rand_dirs` on
    repeated total failure.

    Recovered from `_GetMyDir` (SIMANTW.SYM seg6:8ECA, args plane=[bp+6],
    cur_x=[bp+8], cur_y=[bp+10], sub=[bp+12], tgt_x=[bp+14], tgt_y=[bp+16];
    FAR return). Composes `check_my_best_dirs`, `get_my_best_dirs`,
    `get_my_rand_dirs`, and `get_dir` — all already recovered; no new
    primitives. `inside` is (as with every routine in this cluster) not a
    real stack argument — computed here the same way every other caller
    does: `pack[0x9B6E] != 0`.

    Target selection: `plane <= 1` (yard) with `sub <= 1` uses the
    caller's own `(tgt_x, tgt_y)`; `sub == 2` reads
    `simant_data_group[0x835A]`/`[0x835C]`; any other `sub` reads
    `[0x835E]`/`[0x8360]`. `plane > 1` (nest) with `sub == plane` uses the
    caller's own target; otherwise reads `[0x8352]`/`[0x8354]` when the
    ant's OWN `plane == 2`, else `[0x8356]`/`[0x8358]` — four genuinely
    distinct fixed-destination table slots (independently confirmed via
    disassembly, not assumed to be aliases of each other).

    The probe itself, given a resolved `(tx, ty)`:
      - `pack[0x72E4] >= 0` (signed): calls `check_my_best_dirs`. A total
        failure (`-2`) falls back to `get_my_rand_dirs`, reading its two
        far-pointer outputs from WHATEVER `pack[0x78A4]`/`[0xA0D8]`
        already hold (no reseed) and writing them back, then decrements
        `pack[0x72E4]`. Anything else: stamps `pack[0x72E4] = -1`, calls
        `get_my_best_dirs` fresh from `(cur_x, cur_y)`, decrements
        `pack[0x72E4]` again (landing on `-2`), and returns THAT fresh
        result directly (the `check_my_best_dirs` result itself is
        discarded either way — only its `-2`-vs-other verdict matters).
      - `pack[0x72E4] < 0`: calls `get_my_best_dirs` once; a non-`-2`
        result returns directly, no sentinel bookkeeping at all. A `-2`
        result checks whether `pack[0x72E4]` was ALREADY exactly `-2`
        (i.e. this is not the first tick to fail this way): if so, seeds
        a fresh `_GetDir(cur, target) - 1` compass direction into
        `get_my_rand_dirs`'s `out2` with `out1 = 0` (fresh commit),
        resets `pack[0x72E4] = 0x10` (a 16-step retry budget), and
        returns `get_my_rand_dirs`'s result; otherwise just returns the
        plain `-2`.
    """
    inside = pack.rw(0x9B6E) != 0

    def probe(tx, ty):
        if _sx16(pack.rw(0x72E4)) >= 0:
            out = [0]
            result = check_my_best_dirs(dgroup, pack, out, inside, plane,
                                        cur_x, cur_y, tx, ty)
            if result == -2:
                out1 = [pack.rw(0x78A4)]
                out2 = [pack.rw(0xA0D8)]
                r = get_my_rand_dirs(dgroup, pack, out1, out2, inside, plane,
                                     cur_x, cur_y, tx, ty)
                pack.ww(0x78A4, out1[0] & 0xFFFF)
                pack.ww(0xA0D8, out2[0] & 0xFFFF)
                pack.ww(0x72E4, (pack.rw(0x72E4) - 1) & 0xFFFF)
                return r
            pack.ww(0x72E4, 0xFFFF)
            r = get_my_best_dirs(dgroup, pack, inside, plane, cur_x, cur_y, tx, ty)
            pack.ww(0x72E4, (pack.rw(0x72E4) - 1) & 0xFFFF)
            return r

        r = get_my_best_dirs(dgroup, pack, inside, plane, cur_x, cur_y, tx, ty)
        if r != -2:
            return r
        if _sx16(pack.rw(0x72E4)) == -2:
            direction = (get_dir(cur_x, cur_y, tx, ty) - 1) & 0xFFFF
            pack.ww(0xA0D8, direction)
            pack.ww(0x72E4, 0x10)
            pack.ww(0x78A4, 0)
            out1, out2 = [0], [direction]
            r2 = get_my_rand_dirs(dgroup, pack, out1, out2, inside, plane,
                                  cur_x, cur_y, tx, ty)
            pack.ww(0x78A4, out1[0] & 0xFFFF)
            pack.ww(0xA0D8, out2[0] & 0xFFFF)
            return r2
        return r

    if plane <= 1:
        if sub <= 1:
            return probe(tgt_x, tgt_y)
        if sub == 2:
            atx, aty = simant_data_group.rw(0x835A), simant_data_group.rw(0x835C)
        else:
            atx, aty = simant_data_group.rw(0x835E), simant_data_group.rw(0x8360)
        return probe(atx, aty)

    if sub == plane:
        return probe(tgt_x, tgt_y)
    if plane == 2:
        atx, aty = simant_data_group.rw(0x8352), simant_data_group.rw(0x8354)
    else:
        atx, aty = simant_data_group.rw(0x8356), simant_data_group.rw(0x8358)
    return probe(atx, aty)


def get_my_dis(simant_data_group, plane: int, cur_x: int, cur_y: int,
               tgt_plane: int, tgt_x: int, tgt_y: int) -> int:
    """Cross-plane distance estimate: when `(cur_x, cur_y)` and
    `(tgt_x, tgt_y)` are interpreted on the SAME plane, a plain
    `_GetDis`; otherwise routes through the SAME fixed SDG "connector"
    coordinate slots `get_my_dir` reads as its alternate-destination
    table (`0x835A/0x835C`=table A, `0x835E/0x8360`=table B,
    `0x8352/0x8354`=table C, `0x8356/0x8358`=table D), summing 2 or 3
    leg distances as 16-bit adds (matching the real ASM's `add ax,cx`
    on `_GetDis`'s low word only — DX/the high word is never
    consulted here, unlike `get_dis`'s own full 32-bit-squared-distance
    return, so every intermediate sum is masked `& 0xFFFF`).

    Recovered from `_GetMyDis` (SIMANTW.SYM seg6:8682, args plane=[bp+6],
    cur_x=[bp+8], cur_y=[bp+10], tgt_plane=[bp+12], tgt_x=[bp+14],
    tgt_y=[bp+16]; FAR return, 0x1A6 bytes). Composes only `get_dis` — no
    other primitives.

    `tgt_plane == plane`: `dis(cur, tgt)` directly.

    `plane == 1` and `tgt_plane > 1` (the only case where `plane == 1`
    routes here instead of the shared fallback below): `dis(table C,
    tgt) + dis(cur, table A)` if `tgt_plane == 2`, else `dis(table C,
    tgt) + dis(cur, table B)`.

    Otherwise (`plane != 1`, OR `plane == 1` with `tgt_plane <= 1`
    — `tgt_plane == 1` is impossible here since `tgt_plane == plane`
    was already excluded above):
      - `tgt_plane == 1`: `dis(table A, tgt) + dis(cur, table C)` when
        `plane == 2`, else `dis(table B, tgt) + dis(cur, table D)`.
      - otherwise, a THREE-leg route: `plane == 2` sums `dis(table D,
        tgt) + dis(table A, table B) + dis(cur, table C)`; any other
        `plane` sums `dis(table C, tgt) + dis(table B, table A)`
        (independently confirmed the two anchor-to-anchor legs are
        pushed in SWAPPED order between these two branches — not a
        transcription slip) `+ dis(cur, table D)`.
    """
    def dis(x1, y1, x2, y2):
        return get_dis(x1, y1, x2, y2) & 0xFFFF

    def tbl(off_x, off_y):
        return simant_data_group.rw(off_x), simant_data_group.rw(off_y)

    if tgt_plane == plane:
        return dis(cur_x, cur_y, tgt_x, tgt_y)

    if plane == 1 and tgt_plane > 1:
        cx, cy = tbl(0x8352, 0x8354)
        leg1 = dis(cx, cy, tgt_x, tgt_y)
        if tgt_plane == 2:
            ax_, ay_ = tbl(0x835A, 0x835C)
        else:
            ax_, ay_ = tbl(0x835E, 0x8360)
        leg2 = dis(cur_x, cur_y, ax_, ay_)
        return (leg1 + leg2) & 0xFFFF

    if tgt_plane == 1:
        if plane == 2:
            ax_, ay_ = tbl(0x835A, 0x835C)
            leg1 = dis(ax_, ay_, tgt_x, tgt_y)
            cx, cy = tbl(0x8352, 0x8354)
            leg2 = dis(cur_x, cur_y, cx, cy)
        else:
            bx, by = tbl(0x835E, 0x8360)
            leg1 = dis(bx, by, tgt_x, tgt_y)
            dx, dy = tbl(0x8356, 0x8358)
            leg2 = dis(cur_x, cur_y, dx, dy)
        return (leg1 + leg2) & 0xFFFF

    if plane == 2:
        dx, dy = tbl(0x8356, 0x8358)
        leg1 = dis(dx, dy, tgt_x, tgt_y)
        ax_, ay_ = tbl(0x835A, 0x835C)
        bx, by = tbl(0x835E, 0x8360)
        leg2 = dis(ax_, ay_, bx, by)
        running = (leg1 + leg2) & 0xFFFF
        cx, cy = tbl(0x8352, 0x8354)
        leg3 = dis(cur_x, cur_y, cx, cy)
        return (running + leg3) & 0xFFFF

    cx, cy = tbl(0x8352, 0x8354)
    leg1 = dis(cx, cy, tgt_x, tgt_y)
    bx, by = tbl(0x835E, 0x8360)
    ax_, ay_ = tbl(0x835A, 0x835C)
    leg2 = dis(bx, by, ax_, ay_)
    running = (leg1 + leg2) & 0xFFFF
    dx, dy = tbl(0x8356, 0x8358)
    leg3 = dis(cur_x, cur_y, dx, dy)
    return (running + leg3) & 0xFFFF


def food_fall(dgroup, pack, x: int, y: int) -> int:
    """Falling-dirt/food physics on the yard map plane
    (`MAP_PLANE_BASE[0]`): starting at `(x, y)`, repeatedly steps by a
    FIXED per-call `(dx, dy)` delta read from a small table at
    `dgroup[pack[0x9C66] + 0x22BE]` (dx) / `[+0x22C2]` (dy) — both bytes
    read UNSIGNED (zero-extended), even though the table stores signed
    deltas (e.g. `0xFF` meaning `-1`); this is a genuine ASM quirk
    (independently confirmed via the raw disassembly — the compiler
    never emits a sign-extend here — and ported literally, not a
    transcription slip). Since `pack[0x9C66]` never changes mid-call,
    `dx`/`dy` are effectively CONSTANTS for the whole walk.

    Recovered from `_FoodFall` (SIMANTW.SYM seg5:0EAA, args x=[bp+6],
    y=[bp+8]; FAR return). Each step: if the current cell's tile is
    `< 4`, "hardens" it to `(tile + 6) << 2` and bumps `pack[0x9E84]`
    (clearing the walk's own "still falling" flag — the harden only
    ever fires once per call, on whichever step first lands on a
    hardenable tile, but the walk keeps going after it). The walk then
    advances by `(dx, dy)` and re-derives an x-in-`[0, 0x7F]` /
    y-in-`[0, 0x3F]` (both SIGNED range checks) bounds gate combined
    with the "still falling" flag; it stops once that combined flag
    goes false.

    Returns `dx` itself — the real ASM's natural fall-through leaves
    whatever the last x-delta byte read happened to be in AX (no
    explicit return value is ever set), a genuine leftover-register
    quirk. Since `dx` is constant per call, this simplifies to just the
    table byte itself, but is still a deliberately-preserved quirk, not
    a "clean" `0`/`1` the way `drop_food_a`'s own explicit `return 1`
    is for its OWN inlined copy of this same loop.
    """
    delta_base = pack.rw(0x9C66)
    dx = dgroup.rb(delta_base + 0x22BE)
    dy = dgroup.rb(delta_base + 0x22C2)
    dx_shifted = dx << 6

    cx, si, di = x, y, x << 6
    falling = 1
    while True:
        cell = MAP_PLANE_BASE[0] + di + si
        tile = dgroup.rb(cell)
        if tile < 4:
            dgroup.wb(cell, ((tile + 6) << 2) & 0xFF)
            pack.ww(0x9E84, (pack.rw(0x9E84) + 1) & 0xFFFF)
            falling = 0

        di = (di + dx_shifted) & 0xFFFF
        si = (si + dy) & 0xFFFF
        cx = (cx + dx) & 0xFFFF

        keep = falling if 0 <= _sx16(cx) <= 0x7F else 0
        if not (0 <= _sx16(si) <= 0x3F):
            keep = 0
        falling = keep
        if falling == 0:
            break
    return dx


def drop_food_a(dgroup, pack, x: int, y: int) -> int:
    """Drop a unit of food/dirt onto the yard map plane at `(x, y)` —
    behavior depends on `pack[0x9B6E]` ("inside") and the current tile
    value there.

    Recovered from `_DropFoodA` (SIMANTW.SYM seg5:0D86, args x=[bp+6],
    y=[bp+8]; FAR return, 0x124 bytes). Composes `food_fall` (its own
    inlined copy of the exact same loop, independently re-verified
    instruction-for-instruction — the only difference is the terminal
    return value: this routine forces `1` unconditionally instead of
    `food_fall`'s leftover-`dx` quirk).

    `pack[0x9B6E] == 1` ("inside"):
      - tile `< 4`: hardens to `(tile + 6) << 2`, bumps `pack[0x9E84]`,
        returns `1`.
      - tile `8..0x17`: reduces to `(tile - 8) >> 2` and re-applies the
        SAME harden step to that reduced value (a genuine recursive-
        style reduction in the original ASM — one re-entry, not a
        loop).
      - tile `0x18..0x26`: plain `tile += 1`, bumps `pack[0x9E84]`,
        returns `1` — no harden transform.
      - tile `4..7` OR `0x27..0x3F` (a genuine non-contiguous union,
        independently confirmed via the raw disassembly, not a
        transcription slip): runs `food_fall`'s walk for its side
        effects, then unconditionally returns `1`.
      - tile `>= 0x40`: no-op, returns `0`.

    `pack[0x9B6E] != 1` ("outside"): simpler —
      - tile `< 0x48`: force-sets the tile to exactly `0x48`, bumps
        `pack[0x9E84]`, returns `1`.
      - tile `0x48..0x4A`: plain `tile += 1`, bumps `pack[0x9E84]`,
        returns `1` (the SAME increment tail the "inside" `0x18..0x26`
        case uses).
      - tile `>= 0x4B`: no-op, returns `0`.
    """
    cell = MAP_PLANE_BASE[0] + (x << 6) + y
    tile = dgroup.rb(cell)

    def harden(t):
        dgroup.wb(cell, ((t + 6) << 2) & 0xFF)
        pack.ww(0x9E84, (pack.rw(0x9E84) + 1) & 0xFFFF)
        return 1

    def bump():
        dgroup.wb(cell, (dgroup.rb(cell) + 1) & 0xFF)
        pack.ww(0x9E84, (pack.rw(0x9E84) + 1) & 0xFFFF)
        return 1

    if pack.rw(0x9B6E) == 1:
        if tile < 4:
            return harden(tile)
        if 8 <= tile <= 0x17:
            return harden((tile - 8) >> 2)
        if 0x18 <= tile <= 0x26:
            return bump()
        if tile >= 0x40:
            return 0
        food_fall(dgroup, pack, x, y)
        return 1

    if tile < 0x48:
        dgroup.wb(cell, 0x48)
        pack.ww(0x9E84, (pack.rw(0x9E84) + 1) & 0xFFFF)
        return 1
    if tile <= 0x4A:
        return bump()
    return 0


def not_mowed(pack, index: int, bit: int) -> int:
    """Test-and-clear a per-cell "still has grass" bit: returns `1` the
    FIRST time called for a given `(index, bit)` and clears the bit so
    later calls with the same arguments return `0` (already mowed).

    Recovered from `_NotMowed` (SIMANTW.SYM seg7:203E, args index=[bp+6],
    bit=[bp+8]; FAR return, 52 bytes). A packed bit array in PACK
    (accessed via a hardcoded `0x5EF3` segment literal, independently
    confirmed to equal the real PACK selector): `index` selects a WORD
    slot at byte offset `0xA0B6 + index*2`; `bit` (0..15) selects a bit
    within that word.
    """
    mask = (1 << bit) & 0xFFFF
    off = (index << 1) & 0xFFFF
    word = pack.rw(0xA0B6 + off)
    if word & mask:
        pack.ww(0xA0B6 + off, (word - mask) & 0xFFFF)
        return 1
    return 0


def force_mode_a(dgroup, simant_data_group, slot: int, mode: int,
                 arg3: int) -> None:
    """Force a yard ("A") ant's mode-transition fields to a specific
    state, dispatching on `mode` (1..9; anything else is a no-op past
    the tail below) via a 9-entry jump table with several modes
    sharing one handler.

    Recovered from `_ForceModeA` (SIMANTW.SYM seg7:0550, args slot=[bp+6],
    mode=[bp+8], arg3=[bp+10]; FAR return, 210 bytes). Uses the
    already-recovered `field_c`/`field_e` A-list slots (`[0x2B78+slot]`/
    `[0x334C+slot]`, same fields `sim_egg_a`-family stamps) plus a
    third per-slot BYTE counter at `[0x2F62+slot]`.

    `mode == 1`: bumps the counter `+8`. `mode in (2, 6)`: no extra
    effect. `mode in (3, 7)`: bumps the counter `-8`, AND — a step none
    of the other modes have — if the slot's own yard map tile
    (`simant_data_group[0x23A4+slot]`/`[0x278E+slot]`) is `< 0x48`,
    force-sets it to `0x48`. `mode in (5, 9)`: bumps the counter
    `-0x18`. Every one of THOSE modes then stamps `field_c = arg3`,
    `field_e = 0`. `mode in (4, 8)` (or any other value, INCLUDING an
    out-of-1..9-range one): skips the counter bump AND the stamp
    entirely — the routine only does the ONE thing below.

    Finally, regardless of `mode`: if `arg3 == 6` AND `dgroup[0xCE80]
    == 1` (both read directly, no pointer-global indirection), OVERWRITES
    `field_e` again with `((dgroup[0xCE7E] & 0xFC) << 2) | ((dgroup[0xCD88]
    signed >> 3) & 0xFF)` — a small fixed bit-packed status byte.
    """
    stamp = True
    if mode == 1:
        simant_data_group.wb(
            0x2F62 + slot, (simant_data_group.rb(0x2F62 + slot) + 8) & 0xFF)
    elif mode in (2, 6):
        pass
    elif mode in (3, 7):
        simant_data_group.wb(
            0x2F62 + slot, (simant_data_group.rb(0x2F62 + slot) - 8) & 0xFF)
        x = simant_data_group.rb(0x23A4 + slot)
        y = simant_data_group.rb(0x278E + slot)
        cell = MAP_PLANE_BASE[0] + (x << 6) + y
        if dgroup.rb(cell) < 0x48:
            dgroup.wb(cell, 0x48)
    elif mode in (5, 9):
        simant_data_group.wb(
            0x2F62 + slot, (simant_data_group.rb(0x2F62 + slot) - 0x18) & 0xFF)
    else:
        stamp = False

    if stamp:
        simant_data_group.wb(0x2B78 + slot, arg3 & 0xFF)
        simant_data_group.wb(0x334C + slot, 0)

    if arg3 == 6 and dgroup.rw(0xCE80) == 1:
        al = ((dgroup.rb(0xCE7E) & 0xFC) << 2) & 0xFF
        cx = _sx16(dgroup.rw(0xCD88)) >> 3
        al |= cx & 0xFF
        simant_data_group.wb(0x334C + slot, al & 0xFF)


def force_mode_b(dgroup, simant_data_group, slot: int, mode: int,
                 arg3: int) -> None:
    """The black-colony twin of `force_mode_a` — NOT a mechanical twin
    (independently confirmed via the raw disassembly): it stamps a
    DIFFERENT field pair (`caste`/`field_e`, not `field_c`/`field_e`),
    and its `mode in (3, 7)` handler does NOT have the map-tile check
    `force_mode_a`'s does — it's a plain caste bump, nothing else.

    Recovered from `_ForceModeB` (SIMANTW.SYM seg7:0622, args slot=[bp+6],
    mode=[bp+8], arg3=[bp+10]; FAR return, 176 bytes).

    `mode == 1`: bumps `simant_data_group[0x3D18+slot]` (caste) `+8`.
    `mode in (2, 6)`: no extra effect. `mode in (3, 7)`: bumps caste
    `-8`. `mode in (5, 9)`: bumps caste `-0x18`. Every one of THOSE
    modes then stamps `field_c = arg3` (`[0x3B22+slot]`), `field_e = 0`
    (`[0x3F0E+slot]`). `mode in (4, 8)` (or any other value): skips
    both the caste bump and the stamp.

    Finally, the SAME `arg3 == 6` / `dgroup[0xCE80] == 1` tail
    `force_mode_a` has, but overwriting `field_e` (`[0x3F0E+slot]`)
    instead of `force_mode_a`'s `field_e` offset (they happen to be
    the SAME semantic field — just B's own offset, not a divergence).
    """
    stamp = True
    if mode == 1:
        simant_data_group.wb(
            0x3D18 + slot, (simant_data_group.rb(0x3D18 + slot) + 8) & 0xFF)
    elif mode in (2, 6):
        pass
    elif mode in (3, 7):
        simant_data_group.wb(
            0x3D18 + slot, (simant_data_group.rb(0x3D18 + slot) - 8) & 0xFF)
    elif mode in (5, 9):
        simant_data_group.wb(
            0x3D18 + slot, (simant_data_group.rb(0x3D18 + slot) - 0x18) & 0xFF)
    else:
        stamp = False

    if stamp:
        simant_data_group.wb(0x3B22 + slot, arg3 & 0xFF)
        simant_data_group.wb(0x3F0E + slot, 0)

    if arg3 == 6 and dgroup.rw(0xCE80) == 1:
        al = ((dgroup.rb(0xCE7E) & 0xFC) << 2) & 0xFF
        cx = _sx16(dgroup.rw(0xCD88)) >> 3
        al |= cx & 0xFF
        simant_data_group.wb(0x3F0E + slot, al & 0xFF)


def maintain_swarm(dgroup, pack) -> None:
    """Decay two swarm-size counters (one per colony) once per tick,
    each clamped to its own configured floor and a shared hard cap.

    Recovered from `_MaintainSwarm` (SIMANTW.SYM seg7:3580, NO args;
    FAR return, 120 bytes). Applies the SAME formula to `pack[0x807A]`
    (black) and `pack[0x9C26]` (red) in sequence — not a genuine B/R
    pair, just one self-contained routine touching both colonies'
    counters back to back: a value `<= 0` stays put; `< 4` decrements
    by `1`; otherwise decays by ~25% (`value -= value // 4`, an
    arithmetic-shift-right-by-2 in the real ASM). The result is then
    floored at `dgroup[0xAC8C]`/`[0xAC8E]` respectively (each colony's
    own configured minimum, read directly — no pointer-global
    indirection) and capped at `0x32` (50).
    """
    def decay(value):
        if value <= 0:
            return value
        if value < 4:
            return value - 1
        return value - (value // 4)

    def clamp(value, floor):
        if floor > value:
            value = floor
        return min(value, 0x32)

    b_val = clamp(decay(_sx16(pack.rw(0x807A))), _sx16(dgroup.rw(0xAC8C)))
    pack.ww(0x807A, b_val & 0xFFFF)

    r_val = clamp(decay(_sx16(pack.rw(0x9C26))), _sx16(dgroup.rw(0xAC8E)))
    pack.ww(0x9C26, r_val & 0xFFFF)


def feed_ants(dgroup, simant_data_group, pack, table_view, table_off) -> None:
    """Age both colonies' hunger-decay food supplies by one tick (the
    SAME `dgroup[0xAC86]`/`[0xAC88]` counters `dec_eat_b`/`dec_eat_r`
    drain, floored at `0`; the black side's decrement is skipped
    entirely while `simant_data_group[0x8A60]` — the SAME "no-starve"
    cheat flag `dec_eat_b` gates on — is set), then occasionally drops
    a fresh food pile once the map's live food count falls behind a
    rolling threshold.

    Recovered from `_FeedAnts` (SIMANTW.SYM seg6:0474, NO args; NEAR
    return, 100 bytes). `pack[0x80B4] == 3` skips the food-drop check
    entirely. Otherwise: if `pack[0x9E84]` (the SAME per-drop counter
    `food_fall`/`drop_food_a` bump) is still below
    `simant_data_group[0x8A62]` (a rolling threshold — a single `if`,
    not a loop, confirmed via a fresh disassembly now that `_AddFood`
    is recovered), composes `add_food(0x96, 1)` — `count=0x96` (150,
    so up to 150 random placements) and `flag=1` (fires the
    presentation-only `GR!_myBeginSound`, stubbed in the oracle test
    rather than modeled). The push order (`push 1` then `push 0x96`)
    initially misled a first-draft reading into swapping `count`/
    `flag` — corrected after the real ASM's `pack[0x9E84]` landed at
    75, not the `1` a `count=1` reading would predict — then rerolls
    the threshold to `_SRand1(50) + 1` (`simant_data_group[0x8A62]`,
    the SAME field, confirmed via the raw disassembly to be written
    through the identical selector the read used — no PACK/SDG
    asymmetry despite the surrounding fields' mix).
    """
    if simant_data_group.rw(0x8A60) == 0:
        val = (dgroup.rw(0xAC86) - 1) & 0xFFFF
        dgroup.ww(0xAC86, val)
        if _sx16(val) < 0:
            dgroup.ww(0xAC86, 0)

    val = (dgroup.rw(0xAC88) - 1) & 0xFFFF
    dgroup.ww(0xAC88, val)
    if _sx16(val) < 0:
        dgroup.ww(0xAC88, 0)

    if pack.rw(0x80B4) == 3:
        return

    threshold = simant_data_group.rw(0x8A62)
    if _sx16(pack.rw(0x9E84)) >= _sx16(threshold):
        return

    add_food(dgroup, pack, simant_data_group, table_view, table_off, 0x96, 1)

    from .simone import SRAND_SEED_OFF, srand1
    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll = srand1(seed, 50)
    dgroup.ww(SRAND_SEED_OFF, seed)
    simant_data_group.ww(0x8A62, (roll + 1) & 0xFFFF)


def set_caste_prod(dgroup, simant_data_group) -> None:
    """Pick the colony's next "hatch mode" by comparing target vs. actual
    caste-production percentages, writing the winner into
    `simant_data_group[0x8A56]` — the SAME field `sim_egg_b`'s hatch
    branch reads to decide what an egg turns into.

    Recovered from `_SetCasteProd` (SIMANTW.SYM seg7:026E, NO args; FAR
    return, 210 bytes — one of `_GetStrategy`'s own two unexplored
    callees, cont.172's deferral). Composes the already-recovered
    `a_f_ldiv` (signed 32-bit division).

    Sums 4 target counts (`dgroup[0xAC96..0xAC9C]`, direct — no
    pointer-global indirection) and 4 actual counts
    (`simant_data_group[0x8622..0x8628]`), then computes each slot's
    percentage of its own total (`100 * value // total`, `0` when that
    total isn't `> 0` — the SAME guard that avoids ever calling
    `a_f_ldiv` with a non-positive divisor). Finds the slot index
    with the MOST NEGATIVE `(target_pct - actual_pct)` (a strict `<`
    comparison, so the first slot wins ties; defaults to slot `0` if no
    difference is negative), looks that index up in a fixed
    `simant_data_group[0x89AE..]` table, and stores the result.
    """
    from .crt_math import a_f_ldiv

    total_target = 0
    total_actual = 0
    for i in range(4):
        total_target = (total_target + dgroup.rw(0xAC96 + i * 2)) & 0xFFFF
        total_actual = (total_actual + simant_data_group.rw(0x8622 + i * 2)) & 0xFFFF
    total_target = _sx16(total_target)
    total_actual = _sx16(total_actual)

    target_pct = [0, 0, 0, 0]
    actual_pct = [0, 0, 0, 0]
    for i in range(4):
        if total_target > 0:
            value = _sx16(dgroup.rw(0xAC96 + i * 2))
            target_pct[i] = _sx16(a_f_ldiv(100 * value, total_target) & 0xFFFF)
        if total_actual > 0:
            value = _sx16(simant_data_group.rw(0x8622 + i * 2))
            actual_pct[i] = _sx16(a_f_ldiv(100 * value, total_actual) & 0xFFFF)

    best_diff = 0
    best_index = 0
    for i in range(4):
        diff = target_pct[i] - actual_pct[i]
        if diff < best_diff:
            best_diff = diff
            best_index = i

    mode = simant_data_group.rw(0x89AE + best_index * 2)
    simant_data_group.ww(0x8A56, mode & 0xFFFF)


def set_mode_prod(simant_data_group, pack) -> None:
    """Pick the colony's next "mode" production target by comparing a
    fixed-scale production percentage against each mode's raw tallied
    population, writing the winner into `simant_data_group[0x8A58]` —
    the SAME fixed WORD `get_new_mode`'s own fallback path reads.

    Recovered from `_SetModeProd` (SIMANTW.SYM seg7:0326, NO args; FAR
    return, 156 bytes — `_GetStrategy`'s OTHER unexplored callee).
    Composes the already-recovered `a_f_ulmul` (unsigned 32-bit
    multiply); the matching unsigned divide, `__aFuldiv`, has no plain-
    Python composable form (it's a `hooks.py` VM-level island only, per
    that module's own comment), so this inlines the identical
    semantics as a local helper — a plain unsigned 32-bit floor divide,
    raising `ZeroDivisionError` on a zero divisor like the real ASM's
    `div` fault would (never actually reachable here since the
    divisor is the fixed constant `0xFFFF`).

    NOT a mechanical mirror of `set_caste_prod` (independently confirmed
    via the raw disassembly): only 3 slots (not 4), no total-positive
    guard before the divide (moot, since the divisor here is always the
    fixed `0xFFFF`, never a computed total), a GENUINE UNSIGNED
    multiply/divide pair instead of `a_f_ldiv`'s signed one (the ASM
    still sign-extends the 32-bit total via `cwd` before that unsigned
    multiply — a real quirk, replicated by passing the signed Python
    int through `a_f_ulmul`, whose own masking reproduces the
    two's-complement reinterpretation correctly), and finds the
    MAXIMUM difference (not the minimum) with a STRICT-greater tie
    rule (first slot wins ties, same direction as `set_caste_prod`'s
    own strict rule, just an argmax instead of an argmin).

    Sums `pack[0x9E70]`/`[0x9E72]`/`[0x9E74]` (the SAME per-mode
    population tallies `tally_mode_pop` writes) into `total`. For each
    of the 3 slots: `percent = (total * pack[0x9C74+slot*2]) // 0xFFFF`
    (unsigned), then `diff = percent - pack[0x9E70+slot*2]` (signed,
    16-bit-wrapped). The slot with the largest `diff` (ties keep the
    first) indexes a fixed `simant_data_group[0x89B6..]` table; that
    value is the new `[0x8A58]`.
    """
    from .crt_math import a_f_ulmul

    def uldiv(dividend, divisor):
        dividend &= 0xFFFFFFFF
        divisor &= 0xFFFFFFFF
        if divisor == 0:
            raise ZeroDivisionError(
                "set_mode_prod: uldiv divide by zero -- the ASM would #DE here")
        return dividend // divisor

    total = 0
    for i in range(3):
        total = (total + pack.rw(0x9E70 + i * 2)) & 0xFFFF

    percent = [0, 0, 0]
    for i in range(3):
        product = a_f_ulmul(_sx16(total), pack.rw(0x9C74 + i * 2))
        percent[i] = uldiv(product, 0xFFFF) & 0xFFFF

    best_diff = 0
    best_index = 0
    for i in range(3):
        diff = (percent[i] - pack.rw(0x9E70 + i * 2)) & 0xFFFF
        if _sx16(diff) > _sx16(best_diff):
            best_diff = diff
            best_index = i

    mode = simant_data_group.rw(0x89B6 + best_index * 2)
    simant_data_group.ww(0x8A58, mode & 0xFFFF)


def gstr_b(dgroup, pack) -> int:
    """Pick a black-colony "strategy" tier (0-5) from a handful of
    DGROUP population/activity fields plus two PACK-resident ones — a
    PURE predicate, no side effects at all (confirmed via the raw
    disassembly: no far/near calls, nothing written). This is a
    STANDALONE callable duplicate of the shape `_GetStrategy`'s own
    inline code also computes (that routine writes its copy straight
    into `pack[0x9B8A]` instead of returning it — the two are
    algorithmically identical but never literally call each other).

    Recovered from `_GstrB` (SIMANTW.SYM seg7:01CC, NO args; FAR
    return, 162 bytes). `dgroup[0xAC82]`/`[0xAC84]`/`[0xAC86]` are
    genuine direct DGROUP reads (SS-segment-prefixed, and SS == DGROUP
    for this small-model app); `pack[0x79DC]`/`[0x72C8]` are reached
    via the SAME hardcoded `0x5EF3` PACK segment literal seen
    throughout this session — independently confirmed by reading the
    raw ES-override bytes, not assumed uniform with the DGROUP fields.

    `dgroup[0xAC86] < 10` AND `dgroup[0xAC82] >> 1` (arithmetic shift)
    `> dgroup[0xAC84]` AND `dgroup[0xAC84] > 0` AND `pack[0x79DC] > 0`:
    `0`.

    Otherwise, by `dgroup[0xAC86]`: `< 0x1E` -> `5`; `< 0x32` -> `4`.

    Otherwise: `pack[0x72C8] < dgroup[0xAC82]` -> `3`;
    `pack[0x72C8] < (dgroup[0xAC82] << 1)` (16-bit-wrapped) -> `2`.

    Otherwise: if `dgroup[0xAC82] > 0x64` AND `dgroup[0xAC84] > 0` AND
    `pack[0x79DC] > 0`, and `dgroup[0xAC82] // 3` (C-style truncating
    division) is STRICTLY GREATER than `dgroup[0xAC84]`: `0`. Every
    other path: `1`.
    """
    def sx(v):
        v &= 0xFFFF
        return v - 0x10000 if v & 0x8000 else v

    def tdiv(v, d):
        q = abs(v) // d
        return -q if v < 0 else q

    ac86 = sx(dgroup.rw(0xAC86))
    if ac86 < 10:
        ac84 = sx(dgroup.rw(0xAC84))
        half = sx(dgroup.rw(0xAC82)) >> 1
        if half > ac84 and ac84 > 0 and sx(pack.rw(0x79DC)) > 0:
            return 0

    if ac86 < 0x1E:
        return 5
    if ac86 < 0x32:
        return 4

    ac82 = sx(dgroup.rw(0xAC82))
    if sx(pack.rw(0x72C8)) < ac82:
        return 3
    doubled = sx((ac82 << 1) & 0xFFFF)
    if sx(pack.rw(0x72C8)) < doubled:
        return 2

    if ac82 > 0x64:
        ac84_2 = sx(dgroup.rw(0xAC84))
        if ac84_2 > 0 and sx(pack.rw(0x79DC)) > 0:
            if tdiv(ac82, 3) > ac84_2:
                return 0

    return 1


def kill_ant_lion(dgroup, simant_data_group, pack, slot: int) -> None:
    """Remove an antlion: clears its pit tile back to open ground, then
    compacts the antlion list by shifting every LATER slot down by one
    across five parallel PACK arrays.

    Recovered from `_KillAntLion` (SIMANTW.SYM seg7:4B58, arg
    slot=[bp+6]; FAR return, 160 bytes). Composes the already-recovered
    `set_map`. Reads the SAME `pack[0x809C+slot]`/`[0x80BC+slot]`
    (x, y) arrays `find_in_lion_list`/`set_ant_lion` use, and
    `simant_data_group[0x8A88]` for the live count (the SAME field
    `find_in_lion_list` searches).

    `set_map(plane=1, x, y, 0x3F)` unconditionally first. Then: a
    non-positive count is a no-op (nothing to remove). Otherwise
    decrements the count; if the removed slot was the LAST live one
    (`new_count <= slot`), that's the whole effect. Otherwise shifts
    every slot from `slot` up to (not including) the new count down by
    one, across FIVE parallel PACK arrays: `[0x809C]` (x), `[0x80BC]`
    (y), `[0x7D4E]` (the antlion "type"/growth byte `set_ant_lion`
    reads), and two further per-slot fields at `[0x7A68]`/`[0x7D34]`
    whose exact meaning wasn't independently determined — ported
    literally by offset, not guessed at.
    """
    x = pack.rb(0x809C + slot)
    y = pack.rb(0x80BC + slot)
    set_map(dgroup, 1, x, y, 0x3F)

    count = simant_data_group.rw(0x8A88)
    if count <= 0:
        return
    count -= 1
    simant_data_group.ww(0x8A88, count & 0xFFFF)
    if count <= slot:
        return

    for si in range(slot, count):
        pack.wb(0x809C + si, pack.rb(0x809D + si))
        pack.wb(0x80BC + si, pack.rb(0x80BD + si))
        pack.wb(0x7D4E + si, pack.rb(0x7D4F + si))
        pack.wb(0x7A68 + si, pack.rb(0x7A69 + si))
        pack.wb(0x7D34 + si, pack.rb(0x7D35 + si))


def follow_cat_dir(pack) -> int:
    """Pick the cat's pursuit compass direction from its own countdown
    state — a PURE predicate, no side effects (confirmed via the raw
    disassembly: no calls, nothing written).

    Recovered from `_FollowCatDir` (SIMANTW.SYM seg7:32A6, NO args; FAR
    return, 68 bytes). All three fields are PACK-resident.

    `pack[0x77B0] < 5`: `1`. `pack[0x77B0] > 8`: `3`. Otherwise
    (`0x77B0` in `5..8`): `pack[0x789C] > 0` (both signed compares):
    `0`; else `pack[0x7A5C] & 3`.
    """
    value = _sx16(pack.rw(0x77B0))
    if value < 5:
        return 1
    if value > 8:
        return 3
    if _sx16(pack.rw(0x789C)) > 0:
        return 0
    return pack.rb(0x7A5C) & 3


def grab_map(dgroup, x: int, y: int) -> int:
    """Read the yard map tile at `(x, y)`, wrap-clamping each axis to
    its valid range — a PURE predicate (no calls, no side effects).

    Recovered from `_GrabMap` (SIMANTW.SYM seg7:6DAC, args x=[bp+6],
    y=[bp+8]; FAR return, 64 bytes).

    Per axis, a genuine WRAPAROUND clamp (independently confirmed via
    the raw disassembly, not the "clamp to the nearer bound" shape one
    might expect): `x > 0x7F` (signed) maps to `0`; `x < 0` maps to
    `0x7F` (the MAX, not `0`); otherwise `x` is used as-is. Same shape
    for `y` against `0x3F`. Reads `dgroup[MAP_PLANE_BASE[0] + (cx<<6) +
    cy]`.
    """
    sx = _sx16(x)
    if sx > 0x7F:
        cx = 0
    elif sx < 0:
        cx = 0x7F
    else:
        cx = sx

    sy = _sx16(y)
    if sy > 0x3F:
        cy = 0
    elif sy < 0:
        cy = 0x3F
    else:
        cy = sy

    return dgroup.rb(MAP_PLANE_BASE[0] + (cx << 6) + cy)


def get_nearby_patches(dgroup, simant_data_group, x: int, y: int) -> int:
    """Score the 6 cells `(x + dx[i], y + dy[i])` a small per-call
    DGROUP delta table names, on the SAME 12x16 boy's-yard grid
    `is_valid_yard` bounds-checks: `+3` for each in-bounds cell whose
    first SDG grid byte is nonzero, `-3` for each whose SECOND (a
    parallel 12x16 grid immediately following the first, `0xC0` bytes
    later) is nonzero. Out-of-bounds candidates contribute nothing
    either way. A PURE predicate — no calls, nothing written.

    Recovered from `_GetNearbyPatches` (SIMANTW.SYM seg7:3CE4, args
    x=[bp+6], y=[bp+8]; FAR return, 104 bytes). The 6-entry delta
    table (`dgroup[0x25DC+i]` for dx, `dgroup[0x25E2+i]` for dy, both
    zero-extended BYTES) is genuinely runtime-populated scratch data
    (confirmed all-zero on a fresh machine — not a fixed compass table
    like the 8-entry ones used throughout this session), so callers
    are expected to have already filled it in; this routine only
    reads it.
    """
    score = 0
    for i in range(6):
        di = (dgroup.rb(0x25DC + i) + x) & 0xFFFF
        si = (dgroup.rb(0x25E2 + i) + y) & 0xFFFF
        if _sx16(di) < 0 or _sx16(si) < 0:
            continue
        if _sx16(di) >= 0x0C or _sx16(si) >= 0x10:
            continue
        cell = (di << 4) + si
        if simant_data_group.rb(0xA4 + cell) != 0:
            score += 3
        if simant_data_group.rb(0x164 + cell) != 0:
            score -= 3
    return score


def start_migrate(pack, simant_data_group, x: int, y: int) -> None:
    """Begin a grass-patch "migration": project the screen-space
    `(x, y)` onto the SAME 12x16 grass-patch grid `get_nearby_patches`
    scores, recording the target slot in `pack[0x9CEE]` (`-1` marks
    "no migration in progress") — `end_migrate`'s own later call reads
    both this and `pack[0x9D72]` to know where the migration started.

    Recovered from `_StartMigrate` (SIMANTW.SYM seg7:3DF2, args
    x=[bp+6], y=[bp+8]; FAR return, 122 bytes).

    `y_bucket = (y - 0x42) // 10` (C-style truncating division) is
    stored in `pack[0x9D72]` unconditionally. `combined =
    (x + y - 0xEE) // 28` is stored in `pack[0x9CEE]`. If EITHER is
    negative, or `combined > 0xB`, or `y_bucket > 0xF`: `pack[0x9CEE]`
    is reset to `-1` (out of grid range, migration cancelled).
    Otherwise: the grid cell `(combined << 4) + y_bucket` is checked
    against `simant_data_group[0xA4 + cell]` (the SAME grass-patch
    grid `get_nearby_patches` reads) — `0` there ALSO cancels
    (`pack[0x9CEE] = -1`, nothing to migrate from); any other value
    leaves `pack[0x9CEE]` as the computed slot.
    """
    def tdiv(v, d):
        q = abs(v) // d
        return -q if v < 0 else q

    y_bucket = tdiv(y - 0x42, 10)
    pack.ww(0x9D72, y_bucket & 0xFFFF)

    combined = tdiv(x + y - 0xEE, 28)
    pack.ww(0x9CEE, combined & 0xFFFF)

    if combined < 0 or y_bucket < 0 or combined > 0xB or y_bucket > 0xF:
        pack.ww(0x9CEE, 0xFFFF)
        return

    cell = (combined << 4) + y_bucket
    if simant_data_group.rb(0xA4 + cell) == 0:
        pack.ww(0x9CEE, 0xFFFF)


def end_migrate(pack, simant_data_group, x: int, y: int) -> None:
    """Complete a grass-patch "migration" `start_migrate` began: move
    HALF of the origin cell's grass count into the destination cell at
    screen-space `(x, y)`, capped at `0xFA` (250).

    Recovered from `_EndMigrate` (SIMANTW.SYM seg7:3E6C, args x=[bp+6],
    y=[bp+8]; FAR return, 140 bytes). `pack[0x9CEE] < 0` (no migration
    in progress, per `start_migrate`'s own cancel convention) is a
    no-op. Otherwise projects `(x, y)` onto the grid the SAME way
    `start_migrate` does; out of range (either axis) is ALSO a no-op —
    no partial effect, the origin cell is untouched either way.

    In range: halves the ORIGIN cell (`simant_data_group[0xA4 +
    (pack[0x9CEE]<<4) + pack[0x9D72]]`, i.e. `start_migrate`'s saved
    slot/y-bucket pair — floor division by 2, so an odd count leaves a
    remainder behind) and subtracts that half from it; adds the SAME
    half onto the NEWLY computed destination cell, clamping the result
    to `0xFA` rather than letting it exceed that cap.
    """
    def tdiv(v, d):
        q = abs(v) // d
        return -q if v < 0 else q

    old_slot = _sx16(pack.rw(0x9CEE))
    if old_slot < 0:
        return

    y_bucket = tdiv(y - 0x42, 10)
    combined = tdiv(x + y - 0xEE, 28)
    if combined < 0 or y_bucket < 0 or combined > 0xB or y_bucket > 0xF:
        return

    old_y_bucket = _sx16(pack.rw(0x9D72))
    old_cell = ((old_slot << 4) + old_y_bucket) & 0xFFFF
    old_val = simant_data_group.rb(0xA4 + old_cell)
    half = old_val >> 1
    simant_data_group.wb(0xA4 + old_cell, (old_val - half) & 0xFF)

    new_cell = (combined << 4) + y_bucket
    total = simant_data_group.rb(0xA4 + new_cell) + half
    simant_data_group.wb(0xA4 + new_cell, 0xFA if total >= 0xFB else total & 0xFF)


def _frac_trig(table_view, table_off: int, angle: int, phase: int) -> int:
    """Shared body of `frac_sin`/`frac_cos`: an 8-bit-angle (0..255 =
    0..360 degrees), 16-bit-fixed-point lookup via quarter-wave
    symmetry into a 64-entry WORD table.

    `phase` is the angle offset (`0` for sine, `0xC0` for cosine — the
    real ASM computes `cos(a) = sin(a + 0x40)`, done here as
    `sin(a - 0xC0)`, the SAME shift modulo 256).  Folds
    `shifted = (angle - phase) & 0xFF` into the table's first quadrant
    (`shifted & 0x7F`, then reflected around `0x40` if `> 0x3F`) —
    `0x40` itself is the hardcoded max value `0x7FFF` (never looked up
    in the table, avoiding a 65th entry) — negating the result when
    `shifted`'s bit 7 is set (second half of the sign wave).
    """
    shifted = (angle - phase) & 0xFF
    bx = shifted & 0x7F
    if bx > 0x3F:
        bx = 0x80 - bx

    if bx == 0x40:
        value = 0x7FFF
    else:
        bx &= 0x3F
        value = _sx16(table_view.rw(table_off + (bx << 1)))

    return -value if shifted > 0x7F else value


def frac_sin(table_view, table_off: int, angle: int) -> int:
    """16-bit fixed-point sine of an 8-bit angle (`0..255` = `0..360`
    degrees). Composes `_frac_trig`.

    Recovered from `_fracSIN` (SIMANTW.SYM seg7:69C8, arg angle=
    [bp+6]; FAR return, 70 bytes). The 64-entry quarter-wave table is
    reached through a genuine runtime FAR POINTER stored at
    `pack[0x9FCA]` (offset) / `[0x9FCC]` (segment) — NOT a fixed
    compile-time address (confirmed zero on a fresh, pre-init
    machine), so this takes the already-resolved table as an explicit
    `(view, offset)` pair rather than trying to dereference an
    arbitrary runtime segment value itself.
    """
    return _frac_trig(table_view, table_off, angle, 0)


def frac_cos(table_view, table_off: int, angle: int) -> int:
    """16-bit fixed-point cosine of an 8-bit angle — `sin(angle +
    0x40)`, the SAME quarter-circle-is-64-units encoding `frac_sin`
    uses (confirmed via the `bx == 0x40 -> 0x7FFF` special case both
    routines share). Composes `_frac_trig`.

    Recovered from `_fracCOS` (SIMANTW.SYM seg7:6A0E, arg angle=
    [bp+6]; FAR return, 74 bytes). Reads the SAME runtime far-pointer
    table `frac_sin` does.
    """
    return _frac_trig(table_view, table_off, angle, 0xC0)


def place_pill_tile(dgroup, x: int, y: int, value: int) -> None:
    """Write a yard map tile at `(x, y)` after an `is_valid_a` bounds
    check — a validated cousin of `set_map(plane=0, ...)`, but gated
    on `is_valid_a` directly rather than `set_map`'s own
    `map_cell_offset` range check (independently confirmed via the raw
    disassembly to be the SAME `_IsValidA` call, not assumed
    equivalent).

    Recovered from `_PlacePillTile` (SIMANTW.SYM seg7:56DA, args
    x=[bp+6], y=[bp+8], value=[bp+10]; FAR return, 40 bytes). Composes
    the already-recovered `is_valid_a`.
    """
    if is_valid_a(x, y) == 1:
        dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y, value & 0xFF)


def pill_get_life(dgroup, x: int, y: int) -> int:
    """Read a yard life-plane tile at `(x, y)` after an `is_valid_a`
    bounds check; an invalid position returns `0` rather than an
    empty-cell sentinel.

    Recovered from `_PillGetLife` (SIMANTW.SYM seg7:5702, args
    x=[bp+6], y=[bp+8]; FAR return, 40 bytes). Composes the
    already-recovered `is_valid_a`.
    """
    if is_valid_a(x, y) == 0:
        return 0
    return dgroup.rb(LIFE_PLANE_BASE[0] + (x << 6) + y)


def _pillar_cache_index(pack, x: int, y: int) -> int:
    """Shared index rule `store_pillar_map`/`replace_pillar_map` use for
    their 6-entry PACK cache: `x % 6` when `pack[0x9B1E]`'s low bit is
    set, else `y % 6`."""
    return (x if pack.rw(0x9B1E) & 1 else y) % 6


def store_pillar_map(dgroup, pack, x: int, y: int) -> None:
    """Cache the yard map tile at `(x, y)` into a 6-entry PACK table,
    for `replace_pillar_map` to restore later.

    Recovered from `_StorePillarMap` (SIMANTW.SYM seg7:5304, args
    x=[bp+6], y=[bp+8]; FAR return, 110 bytes). Composes the
    already-recovered `is_valid_a`. A no-op when `(x, y)` isn't valid.
    Otherwise stores `dgroup[MAP_PLANE_BASE[0] + (x<<6) + y]` into
    `pack[0x7C0E + idx*2]` (a WORD slot, though the value is always a
    zero-extended byte), `idx` per `_pillar_cache_index`.
    """
    if is_valid_a(x, y) != 1:
        return
    tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
    idx = _pillar_cache_index(pack, x, y)
    pack.ww(0x7C0E + (idx << 1), tile)


def replace_pillar_map(dgroup, pack, x: int, y: int) -> None:
    """Restore a yard map tile at `(x, y)` from `store_pillar_map`'s
    6-entry PACK cache — the inverse operation.

    Recovered from `_ReplacePillarMap` (SIMANTW.SYM seg7:5372, args
    x=[bp+6], y=[bp+8]; FAR return, 104 bytes). Composes the
    already-recovered `is_valid_a`. A no-op when `(x, y)` isn't valid.
    Otherwise reads `pack[0x7C0E + idx*2]` (SAME `idx` rule as
    `store_pillar_map`) and writes it onto `dgroup[MAP_PLANE_BASE[0] +
    (x<<6) + y]`.
    """
    if is_valid_a(x, y) != 1:
        return
    idx = _pillar_cache_index(pack, x, y)
    tile = pack.rb(0x7C0E + (idx << 1))
    dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y, tile)


def pill_food_tile(dgroup, pack, x: int, y: int) -> None:
    """Restore `(x, y)`'s cached map tile (via `replace_pillar_map`),
    then stamp it to a fixed "food" tile (`0x4B`) if it's `< 0x18` —
    an `is_valid_a`-gated no-op otherwise.

    Recovered from `_PillFoodTile` (SIMANTW.SYM seg7:5A02, args
    x=[bp+6], y=[bp+8]; FAR return, 110 bytes). The real ASM calls
    `_IsValidA` TWICE with the identical `(x, y)` — a genuine redundant
    double-check (confirmed via the raw disassembly: same args both
    times, so the second call is provably always equal to the first
    for this pure, deterministic predicate) that has no observable
    effect beyond what a single check already establishes; ported as
    one check via composing `is_valid_a` and the already-recovered
    `replace_pillar_map` (whose body IS that exact "second call +
    restore" sequence, inlined again in the real ASM rather than
    called as a function).
    """
    if is_valid_a(x, y) != 1:
        return
    replace_pillar_map(dgroup, pack, x, y)
    cell = MAP_PLANE_BASE[0] + (x << 6) + y
    if dgroup.rb(cell) < 0x18:
        dgroup.wb(cell, 0x4B)


def is_pill_dead(dgroup, simant_data_group) -> int:
    """Scan the 3x3 neighborhood around the pillar's own recorded
    position (`simant_data_group[0x8A8C]`=x, `[0x8A8E]`=y — NO
    arguments, a genuinely self-contained predicate) for living
    neighbors on the yard life plane; `1` ("dead") once MORE than 5
    of the (up to 9) cells are alive, else `0`.

    Recovered from `_IsPillDead` (SIMANTW.SYM seg7:572A, NO args; FAR
    return, 168 bytes). Composes the already-recovered `is_valid_a`.
    Each of the 9 candidate cells is checked for validity first (an
    out-of-bounds cell contributes `0`, not counted as alive); a valid
    cell's life-plane byte being nonzero counts it.
    """
    px = _sx16(simant_data_group.rw(0x8A8C))
    py = _sx16(simant_data_group.rw(0x8A8E))
    count = 0
    for row in range(px - 1, px + 2):
        for col in range(py - 1, py + 2):
            life = 0
            if is_valid_a(row, col):
                life = dgroup.rb(LIFE_PLANE_BASE[0] + (row << 6) + col)
            if life != 0:
                count += 1
    return 1 if count > 5 else 0


def init_grass_map(pack) -> None:
    """Startup init: clears three PACK counters and fills a 9-entry
    WORD table with `0xFFFF` (an "empty"/unassigned sentinel).

    Recovered from `_InitGrassMap` (SIMANTW.SYM seg7:2096, NO args;
    FAR return, 32 bytes). All fields are PACK-resident.
    """
    pack.ww(0xA0B6, 0)
    pack.ww(0xA0B8, 0)
    pack.ww(0xA0BA, 0)
    for i in range(9):
        pack.ww(0xA0BC + i * 2, 0xFFFF)


def init_sim_vars(dgroup, simant_data_group, pack) -> None:
    """Startup init: a handful of fixed constants and zeroed counters,
    including the SAME `pack[0x9C26]`/`[0x807A]` `maintain_swarm`
    decays and `dgroup[0xAC8C]`/`[0xAC8E]` `start_migrate`/
    `end_migrate` floors this session already recovered.

    Recovered from `_InitSimVars` (SIMANTW.SYM seg7:5A70, NO args; FAR
    return, 62 bytes). `simant_data_group[0x8614] = 1`;
    `pack[0x9BEC]`/`[0x769C] = 0x1E` (30); `pack[0x79E2]`/`[0x9C26]`/
    `[0x807A] = 0`; `dgroup[0xAC8E]`/`[0xAC8C] = 0` (direct DGROUP
    writes, no pointer-global indirection).
    """
    simant_data_group.ww(0x8614, 1)
    pack.ww(0x9BEC, 0x1E)
    pack.ww(0x769C, 0x1E)
    pack.ww(0x79E2, 0)
    pack.ww(0x9C26, 0)
    pack.ww(0x807A, 0)
    dgroup.ww(0xAC8E, 0)
    dgroup.ww(0xAC8C, 0)


def recruit(pack, simant_data_group, count: int) -> None:
    """Convert up to `count` idle A-list (yard) then B-list (black
    nest) ants — whichever are in mode `2` or `6` (via the SAME
    `(v & 0x78) >> 3` extraction used throughout this session) and
    not already recruited — into "recruited" mode `6`
    (`field_c = 6`, `field_e = 0`), scanning each list backward and
    stopping once `count` conversions have happened or the list is
    exhausted.

    Recovered from `_Recruit` (SIMANTW.SYM seg7:06D2, arg count=
    [bp+6]; FAR return, 184 bytes, NO calls at all). Counts come from
    `pack[0x80F0]`/`pack[0x99D4]`; every per-slot field is on
    SIMANT_DATA_GROUP (`ds=5294h` in the raw disassembly, not PACK) —
    the A-list's `[0x2F62]` (a per-slot mode/counter byte, the SAME
    field `force_mode_a` bumps) and `[0x2B78]`/`[0x334C]` (`field_c`/
    `field_e`, matching `force_mode_a`'s own pair); the B-list's
    `[0x3D18]` (caste), `[0x3B22]`/`[0x3F0E]` (`field_c`/`field_e`).
    Never touches the R-list at all (confirmed via the raw
    disassembly — `un_recruit`'s own R-list pass is NOT mirrored
    here).
    """
    budget = count
    a_count = pack.rw(0x80F0)
    if budget > 0:
        for si in range(a_count - 1, -1, -1):
            if budget <= 0:
                break
            cx = simant_data_group.rb(0x2F62 + si)
            if cx == 0 or (cx & 0x80):
                continue
            mode = (cx & 0x78) >> 3
            if mode not in (2, 6):
                continue
            if simant_data_group.rb(0x2B78 + si) == 6:
                continue
            simant_data_group.wb(0x2B78 + si, 6)
            simant_data_group.wb(0x334C + si, 0)
            budget -= 1

    b_count = pack.rw(0x99D4)
    if budget > 0:
        for si in range(b_count - 1, -1, -1):
            if budget <= 0:
                break
            cx = simant_data_group.rb(0x3D18 + si)
            if cx == 0 or (cx & 0x80):
                continue
            mode = (cx & 0x78) >> 3
            if mode not in (2, 6):
                continue
            if simant_data_group.rb(0x3B22 + si) == 6:
                continue
            simant_data_group.wb(0x3B22 + si, 6)
            simant_data_group.wb(0x3F0E + si, 0)
            budget -= 1


def un_recruit(pack, simant_data_group, flag: int) -> None:
    """The inverse of `recruit` — clears "recruited" (`field_c == 6`)
    status across the A-list, B-list, AND (unlike `recruit`, which
    never touches it) the R-list, up to a computed budget.

    Recovered from `_UnRecruit` (SIMANTW.SYM seg7:078A, arg flag=
    [bp+6]; FAR return, 220 bytes, NO calls at all). The budget is
    `pack[0x7876] // 2` (C-style truncating division) when `flag == 0`,
    else `pack[0x7876] + 0x64`. Each list's gate is simpler than
    `recruit`'s (no mode-2-or-6 check — just `field_c == 6` directly);
    A/B-list hits reset `field_c` to `0`, but the R-list's OWN hits
    reset it to `7`, NOT `0` (independently confirmed via the raw
    disassembly, not a transcription slip). Counts/baseline are PACK
    (`[0x80F0]`/`[0x99D4]`/`[0x72CC]`/`[0x7876]`); every per-slot field
    is SIMANT_DATA_GROUP (same `ds=5294h` override as `recruit`): A-list
    `[0x2F62]`/`[0x2B78]`; B-list `[0x3D18]`/`[0x3B22]`; R-list
    `[0x46E6]`/`[0x44F0]` (caste/field_c, matching `find_in_r_list`'s
    own R-list fields).
    """
    def tdiv(v, d):
        q = abs(v) // d
        return -q if v < 0 else q

    baseline = _sx16(pack.rw(0x7876))
    budget = tdiv(baseline, 2) if flag == 0 else baseline + 0x64

    a_count = pack.rw(0x80F0)
    if budget > 0:
        for si in range(a_count - 1, -1, -1):
            if budget <= 0:
                break
            cx = simant_data_group.rb(0x2F62 + si)
            if cx == 0 or (cx & 0x80):
                continue
            if simant_data_group.rb(0x2B78 + si) != 6:
                continue
            simant_data_group.wb(0x2B78 + si, 0)
            budget -= 1

    b_count = pack.rw(0x99D4)
    if budget > 0:
        for si in range(b_count - 1, -1, -1):
            if budget <= 0:
                break
            cx = simant_data_group.rb(0x3D18 + si)
            if cx == 0 or (cx & 0x80):
                continue
            if simant_data_group.rb(0x3B22 + si) != 6:
                continue
            simant_data_group.wb(0x3B22 + si, 0)
            budget -= 1

    r_count = pack.rw(0x72CC)
    if budget > 0:
        for si in range(r_count - 1, -1, -1):
            if budget <= 0:
                break
            cx = simant_data_group.rb(0x46E6 + si)
            if cx == 0 or (cx & 0x80):
                continue
            if simant_data_group.rb(0x44F0 + si) != 6:
                continue
            simant_data_group.wb(0x44F0 + si, 7)
            budget -= 1


def reproduce(dgroup, pack, simant_data_group, x: int, y: int, colony: int) -> None:
    """Mark a jittered cell of a 12x16 "reproduction" grid as having produced,
    bumping a colony-wide first-time counter the first time any given cell
    is hit.

    Recovered from `_Reproduce` (SIMANTW.SYM seg7:3D4C, args x=[bp+6],
    y=[bp+8], colony=[bp+10]; FAR return, 166 bytes). Jitters `(x, y)` by
    two independent `sg_s_rand(4)` rolls (composing the already-recovered
    `sg_s_rand`), clamps the result into a 12-wide (`0..0x0B`) by 16-tall
    (`0..0x0F`) grid, and — UNLESS the jittered cell equals the untouched
    input `(x, y)` exactly, in which case the whole routine is a no-op —
    increments a per-cell BYTE counter at SIMANT_DATA_GROUP `[(di<<4)+si +
    base]`, where `base` is `0xA4` for `colony == 0` or `0x164` for
    `colony != 0` (two contiguous 0xC0-byte grids, one per colony). If that
    cell's counter was `0` before the increment (first hit), also bumps a
    colony-wide PACK WORD counter — `[0x80D4]` for `colony == 0`, `[0x9C80]`
    for `colony != 0`.
    """
    di = sg_s_rand(dgroup, 4) + x
    si = sg_s_rand(dgroup, 4) + y
    if di < 0:
        di = 0
    elif di > 0x0B:
        di = 0x0B
    if si < 0:
        si = 0
    elif si > 0x0F:
        si = 0x0F
    if x == di and y == si:
        return

    if colony != 0:
        base, counter_off = 0x164, 0x9C80
    else:
        base, counter_off = 0xA4, 0x80D4

    idx = (di << 4) + si + base
    if simant_data_group.rb(idx) == 0:
        pack.ww(counter_off, (pack.rw(counter_off) + 1) & 0xFFFF)
    simant_data_group.wb(idx, (simant_data_group.rb(idx) + 1) & 0xFF)


# The per-direction ring-tile table _AddAntLion reads at DGROUP[0x25E8+dir] —
# a plain compile-time DGROUP literal (no pointer-global indirection), same
# category as GET_BEST_DIR_DX/DY, confirmed by a direct memory read.
ADD_ANT_LION_RING_TILE = (1, 2, 4, 7, 6, 5, 3, 0)


def add_ant_lion(dgroup, pack, simant_data_group, x: int, y: int) -> None:
    """Stamp a new ant lion pit: the centre tile plus up to 8 clear
    neighbouring "ring" tiles, then append a slot to the PACK ant-lion
    array (capped at 10 live ant lions).

    Recovered from `_AddAntLion` (SIMANTW.SYM seg7:4340, args x=[bp+6],
    y=[bp+8]; FAR return, 186 bytes). Composes `set_map` (plane 1,
    the yard plane) to stamp the pit centre to tile `0x38` unconditionally,
    then for each of the 8 compass directions (`GET_BEST_DIR_DX`/`DY` —
    confirmed via a direct memory read that DGROUP's own direction table,
    reached through two pointer-globals at `[0xC57E]`/`[0xC580]` that both
    resolve to SIMANT_DATA_GROUP, is byte-for-byte this same constant
    table) checks the neighbour cell via the real `_IsClearTile` routine's
    own composition (`map_cell_offset` + `life_cell_offset` + the
    `is_clear_tile` predicate) and, if clear, stamps it to
    `ADD_ANT_LION_RING_TILE[dir] + 0x30`. Finally appends one slot to the
    PACK ant-lion arrays — `[0x809C+slot]`=x, `[0x80BC+slot]`=y (byte),
    zeroing `[0x7A68+slot]`/`[0x7D34+slot]`/`[0x7D4E+slot]` — at the live
    count `simant_data_group[0x8A88]` (the SAME field `find_in_lion_list`/
    `kill_ant_lion` read), bumping that count only while it's `< 9` (a
    hard cap at 10 slots — once full, further calls keep overwriting slot
    9 without growing the count further).
    """
    set_map(dgroup, 1, x, y, 0x38)

    for si in range(8):
        nx = x + GET_BEST_DIR_DX[si]
        ny = y + GET_BEST_DIR_DY[si]
        off = map_cell_offset(1, nx, ny)
        if off is None:
            continue
        map_tile = dgroup.rb(off)
        life = dgroup.rb(life_cell_offset(1, nx, ny))
        if is_clear_tile(1, map_tile, life) != 1:
            continue
        tile = (ADD_ANT_LION_RING_TILE[si] + 0x30) & 0xFF
        set_map(dgroup, 1, nx, ny, tile)

    slot = simant_data_group.rw(0x8A88)
    pack.wb(0x809C + slot, x & 0xFF)
    pack.wb(0x80BC + slot, y & 0xFF)
    pack.wb(0x7A68 + slot, 0)
    pack.wb(0x7D34 + slot, 0)
    pack.wb(0x7D4E + slot, 0)
    if slot < 9:
        simant_data_group.ww(0x8A88, (slot + 1) & 0xFFFF)


def add_rand_ant_lion(dgroup, pack, simant_data_group) -> None:
    """Search up to 200 random locations for a spot to place a new ant
    lion, preferring a fully-clear 3x3 block but falling back (after
    100 failed attempts) to a merely-clear centre tile; if a spot is
    found, composes `add_ant_lion` to do the actual placement.

    Recovered from `_AddRandAntLion` (SIMANTW.SYM seg7:4222, NO args;
    FAR return, 286 bytes). Each attempt rolls a candidate `x =
    _SRand1(0x40) + _SRand1(0x41)`, `y = _SRand1(0x20) + _SRand1(0x21)`
    (composing the already-recovered `srand1`; four LFSR draws EVERY
    attempt regardless of outcome) and checks it via the real
    `_IsClear3x3` routine's own composition — the same `map_cell_offset`
    + `life_cell_offset` + `is_clear_tile` pattern `add_ant_lion` already
    uses for its ring check, over the centre plus 8 neighbours (an
    out-of-range cell reads as blocked, matching `_IsClear3x3`'s own
    `_IsClearTile`-invalid-cell residue). A fully-clear 3x3 succeeds
    immediately at any attempt count; a merely-clear centre tile (the
    SAME cell already checked as `cells[0]` — reusing it rather than a
    redundant re-check, since it's a pure function of unchanged map/life
    state) only succeeds once the 0-indexed attempt count has reached
    100 (a two-tier fallback, confirmed via the raw `cmp di,0x64`).  On
    success, calls `add_ant_lion(dgroup, pack, simant_data_group, x, y)`
    — its placement body (centre stamp + ring stamp + PACK slot append)
    is byte-identical to this routine's own placement tail, confirmed by
    direct comparison of both disassemblies.  If all 200 attempts fail,
    the routine is a no-op (but has still burned up to 800 LFSR draws).
    """
    from .simone import SRAND_SEED_OFF, srand1

    seed = dgroup.rw(SRAND_SEED_OFF)
    placed = False
    x = y = 0
    for attempt in range(200):
        seed, r1 = srand1(seed, 0x40)
        seed, r2 = srand1(seed, 0x41)
        x = r1 + r2
        seed, r3 = srand1(seed, 0x20)
        seed, r4 = srand1(seed, 0x21)
        y = r3 + r4

        offsets = ((0, 0),) + tuple(zip(CLEAR_3X3_DX, CLEAR_3X3_DY))
        cells = [0] * 9
        for i, (ddx, ddy) in enumerate(offsets):
            cx, cy = x + ddx, y + ddy
            off = map_cell_offset(1, cx, cy)
            if off is None:
                continue
            tile = dgroup.rb(off)
            life = dgroup.rb(life_cell_offset(1, cx, cy))
            cells[i] = is_clear_tile(1, tile, life)

        if is_clear_3x3(cells):
            placed = True
            break
        if cells[0] and attempt >= 100:
            placed = True
            break

    dgroup.ww(SRAND_SEED_OFF, seed)
    if placed:
        add_ant_lion(dgroup, pack, simant_data_group, x, y)


def _place_two_random_rocks(dgroup, pack, simant_data_group) -> None:
    """Fill exactly 2 slots of a raw ASM loop counter stepping `4, 2` —
    used directly as the byte offset into 4 small PACK arrays, so array
    "slot 2" (`si=4`) is filled before slot 1 (`si=2`). Shared body of
    `_InitPillar`/`_InitSow`'s own random-placement loops (byte-identical
    in both disassemblies, down to the DGROUP pointer-globals used).

    For each slot, rolls a candidate yard cell via `_SRand1(0x80)`/
    `_SRand1(0x40)` (always in-bounds, so no `is_valid_a` check is
    needed) and, if its yard map tile is `>= 0x10` (blocked), RETRIES —
    an unbounded loop with no attempt cap, confirmed via the raw
    disassembly: the blocked-cell branch jumps directly past the `sub
    si,2` slot-advance instruction, so `si` is unchanged and the next
    iteration rerolls the SAME slot (unlike `add_rand_ant_lion`, which
    caps at 200 attempts and burns its draws every attempt regardless of
    outcome — here a blocked roll costs only the 2 `x`/`y` draws, never
    the 8-roll). Once a slot's roll lands on a clear (`< 0x10`) cell,
    records `x`/`y` into `pack[0x9BC8+si]`/`[0x9BDA+si]`, rolls a FRESH
    `_SRand1(8)` value into `pack[0x9C2A+si]`, snapshots the
    pre-overwrite tile into `pack[0x78CC+si]`, overwrites the yard map
    tile with `simant_data_group[0x8A90 + roll]` (an 8-entry rock-tile
    lookup table), then advances to the next slot.
    """
    from .simone import SRAND_SEED_OFF, srand1

    seed = dgroup.rw(SRAND_SEED_OFF)
    for si in (4, 2):
        while True:
            seed, x = srand1(seed, 0x80)
            seed, y = srand1(seed, 0x40)
            off = MAP_PLANE_BASE[0] + (x << 6) + y
            old_tile = dgroup.rb(off)
            if old_tile >= 0x10:
                continue
            pack.ww(0x9BC8 + si, x)
            pack.ww(0x9BDA + si, y)
            seed, roll = srand1(seed, 8)
            pack.ww(0x9C2A + si, roll)
            pack.ww(0x78CC + si, old_tile)
            new_tile = simant_data_group.rb(0x8A90 + roll)
            dgroup.wb(off, new_tile)
            break
    dgroup.ww(SRAND_SEED_OFF, seed)


def init_pillar(dgroup, pack, simant_data_group) -> None:
    """Reset the tracked pillar's own state, then — only when outside the
    nest (`pack[0x9B6E] == 0`) — randomly place up to 2 "rock" tiles at
    valid (clear) yard cells (composing `_place_two_random_rocks`).

    Recovered from `_InitPillar` (SIMANTW.SYM seg7:4BF8, NO args; FAR
    return, 228 bytes). Always zeroes: the tracked pillar's own
    position/state (`simant_data_group[0x8A8A]`/`[0x8A8C]`/`[0x8A8E]` —
    the SAME `[0x8A8C]`/`[0x8A8E]` position pair `is_pill_dead` reads),
    the `_pillar_cache_index` rule flag `pack[0x9B1E]`, a companion PACK
    field `pack[0x78D4]`, and the already-recovered `store_pillar_map`/
    `replace_pillar_map` 6-entry cache (`pack[0x7C0E..0x7C19]`).

    When `pack[0x9B6E]` ("inside the nest", the SAME flag `is_it_food`/
    `feed_ants`/etc. read) is nonzero, returns here without placing
    anything.
    """
    simant_data_group.ww(0x8A8A, 0)
    simant_data_group.ww(0x8A8C, 0)
    simant_data_group.ww(0x8A8E, 0)
    pack.ww(0x78D4, 0)
    pack.ww(0x9B1E, 0)
    for i in range(6):
        pack.ww(0x7C0E + (i << 1), 0)

    if pack.rw(0x9B6E) != 0:
        return

    _place_two_random_rocks(dgroup, pack, simant_data_group)


def init_sow(dgroup, pack, simant_data_group) -> None:
    """Unconditionally place 2 random "rock" tiles — the SAME
    `_place_two_random_rocks` loop `init_pillar` uses for its own
    placement tail, but with no state reset and no "outside the nest"
    gate.

    Recovered from `_InitSow` (SIMANTW.SYM seg7:3EF8, NO args; FAR
    return, 146 bytes). The two disassemblies are byte-identical past
    `_InitPillar`'s own gate/reset prologue — same DGROUP
    pointer-globals, same PACK slot offsets, same SDG rock-tile lookup
    table.
    """
    _place_two_random_rocks(dgroup, pack, simant_data_group)


def do_sow(dgroup, pack, simant_data_group) -> None:
    """Per-tick update for the 3 tracked "sown" rocks (slots `si=0,2,4`
    — unlike `init_pillar`/`init_sow`, which only ever fill slots 1/2,
    this processes ALL THREE, including slot 0): each slot independently
    may grow (re-stamp in place with a new lookup tile) and/or spread
    (move to a clear neighbouring cell), gated by two independent
    `_SRand4()` rolls.

    Recovered from `_DoSow` (SIMANTW.SYM seg7:3F8A, NO args; FAR
    return, 316 bytes; calls `_SRand4` x2, `_SRand1(3)`, `_IsValidA`).
    For each slot: rolls `_SRand4()`; a `0` skips the slot entirely
    (no growth, no movement). Otherwise rolls a SECOND `_SRand4()`; a
    `0` result (independently confirmed via the raw disassembly's
    `jnz`-skips-growth polarity — NOT a nonzero result) triggers
    "growth" — steps `pack[0x9C2A+si]` (the
    same 0..7 rock-tile-lookup roll `init_pillar`/`init_sow` seed) by
    `(roll + _SRand1(3) - 1) & 7` (a signed random walk wrapping mod
    8), then re-stamps the slot's CURRENT cell with
    `simant_data_group[0x8A90 + new_roll]`. Movement always follows
    (regardless of the growth gate): the (possibly just-updated) roll
    indexes the SAME `GET_BEST_DIR_DX`/`DY` compass table
    `add_ant_lion` uses (confirmed via the raw disassembly: the exact
    same `+0`/`+8` DGROUP-pointer-global-into-SIMANT_DATA_GROUP
    addressing pattern) to compute a candidate neighbour cell; if
    `is_valid_a` rejects it, or its life-plane cell is occupied, or its
    map tile is `>= 0x10` (blocked), the slot doesn't move this tick.
    Otherwise: the OLD cell is restored to `pack[0x78CC+si]`'s saved
    snapshot, the NEW cell's PRE-overwrite tile becomes the new
    snapshot, the NEW cell is stamped with the rock-tile lookup, and
    the slot's tracked `(x, y)` (`pack[0x9BC8+si]`/`[0x9BDA+si]`)
    updates to the new position.
    """
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    seed = dgroup.rw(SRAND_SEED_OFF)
    for si in (0, 2, 4):
        seed, gate1 = srand_pow2(seed, 3)
        if gate1 == 0:
            continue

        seed, gate2 = srand_pow2(seed, 3)
        x = pack.rw(0x9BC8 + si)
        y = pack.rw(0x9BDA + si)
        if gate2 == 0:
            roll = pack.rw(0x9C2A + si)
            seed, step = srand1(seed, 3)
            roll = (roll + step - 1) & 7
            pack.ww(0x9C2A + si, roll)
            simant_data_group_tile = simant_data_group.rb(0x8A90 + roll)
            dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y, simant_data_group_tile)

        roll = pack.rw(0x9C2A + si)
        new_x = x + GET_BEST_DIR_DX[roll]
        new_y = y + GET_BEST_DIR_DY[roll]
        if is_valid_a(new_x, new_y) == 0:
            continue

        new_cell = MAP_PLANE_BASE[0] + (new_x << 6) + new_y
        new_life_cell = LIFE_PLANE_BASE[0] + (new_x << 6) + new_y
        if dgroup.rb(new_life_cell) != 0:
            continue
        new_cell_tile = dgroup.rb(new_cell)
        if new_cell_tile >= 0x10:
            continue

        old_tile = pack.rb(0x78CC + si)
        dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y, old_tile)
        pack.ww(0x78CC + si, new_cell_tile)
        tile = simant_data_group.rb(0x8A90 + roll)
        dgroup.wb(new_cell, tile)
        pack.ww(0x9BC8 + si, new_x)
        pack.ww(0x9BDA + si, new_y)
    dgroup.ww(SRAND_SEED_OFF, seed)


def init_ant_lions(dgroup, pack, simant_data_group, count: int) -> None:
    """Reset the ant-lion count, then place up to `count` (clamped to a
    max of 10; a non-positive count places none) ant lions via
    `add_rand_ant_lion`.

    Recovered from `_InitAntLions` (SIMANTW.SYM seg7:40C6, arg
    count=[bp+6]; FAR return, 348 bytes). Zeroes
    `simant_data_group[0x8A88]` (the SAME ant-lion live-count field
    `add_ant_lion`/`find_in_lion_list`/`kill_ant_lion` already use) and
    a companion `pack[0x9C6E]`. The real ASM inlines the ENTIRE
    `_AddRandAntLion` body (identical instructions, confirmed by direct
    comparison of both disassemblies — same 200-attempt/100-threshold
    search, same placement tail) once per iteration rather than calling
    it; ported as `count` (clamped) calls to the already-recovered
    `add_rand_ant_lion` instead of re-deriving the same logic a second
    time. Finally stores the clamped count into `pack[0x9E8C]`
    (unconditionally, even when no placement happened).
    """
    simant_data_group.ww(0x8A88, 0)
    pack.ww(0x9C6E, 0)

    clamped = count if count <= 10 else 10
    if clamped > 0:
        for _ in range(clamped):
            add_rand_ant_lion(dgroup, pack, simant_data_group)

    pack.ww(0x9E8C, clamped & 0xFFFF)


def _paint_pillar_arm(dgroup, pack, simant_data_group, dx_step: int, dy_step: int) -> None:
    """Paint a 6-cell arm out from the tracked pillar's own position
    (`simant_data_group[0x8A8C]`/`[0x8A8E]`, the SAME position pair
    `is_pill_dead` reads), one step `(dx_step, dy_step)` at a time.
    Shared body of `_MakePillFood`'s 4 direction blocks (byte-identical
    across all 4 — same `_pillar_cache_index` rule, same food-tile
    stamp).

    For each of the 6 cells: an `is_valid_a` gate skips the whole cell
    if invalid (the real ASM calls it TWICE with identical args — a
    genuine redundant double-check, same precedent as `pill_food_tile`
    — collapsed to one call here since it's a pure, deterministic
    predicate). Restores the cell's map tile from the SAME 6-entry
    `store_pillar_map`/`replace_pillar_map` PACK cache
    (`_pillar_cache_index`, `pack[0x7C0E + idx*2]`), then stamps it to
    the fixed "food" tile `0x4B` if it's `< 0x18` (the SAME threshold
    `pill_food_tile` uses).
    """
    px = _sx16(simant_data_group.rw(0x8A8C))
    py = _sx16(simant_data_group.rw(0x8A8E))
    for i in range(6):
        x = px + i * dx_step
        y = py + i * dy_step
        if is_valid_a(x, y) != 1:
            continue
        idx = _pillar_cache_index(pack, x, y)
        tile = pack.rb(0x7C0E + (idx << 1))
        cell = MAP_PLANE_BASE[0] + (x << 6) + y
        dgroup.wb(cell, tile)
        if dgroup.rb(cell) < 0x18:
            dgroup.wb(cell, 0x4B)


def make_pill_food(dgroup, pack, simant_data_group) -> None:
    """Paint one 6-cell arm out from the tracked pillar, direction
    selected by `pack[0x9B1E]` (the SAME `_pillar_cache_index` rule
    flag `_InitPillar` resets) — `0`=south, `1`=west, `2`=north,
    `3`=east; any other value is a no-op.

    Recovered from `_MakePillFood` (SIMANTW.SYM seg7:57D2, NO args;
    FAR return, 560 bytes; calls `_IsValidA` x2 per cell). The four
    direction blocks are byte-identical past their own `(dx_step,
    dy_step)` — composed here as one shared `_paint_pillar_arm`.
    """
    mode = pack.rw(0x9B1E)
    if mode == 0:
        _paint_pillar_arm(dgroup, pack, simant_data_group, 0, 1)
    elif mode == 1:
        _paint_pillar_arm(dgroup, pack, simant_data_group, -1, 0)
    elif mode == 2:
        _paint_pillar_arm(dgroup, pack, simant_data_group, 0, -1)
    elif mode == 3:
        _paint_pillar_arm(dgroup, pack, simant_data_group, 1, 0)


def make_a_pill(dgroup, pack, simant_data_group) -> None:
    """Roll a fresh direction (`_SRand1(4)`, stored into `pack[0x9B1E]` —
    the SAME `_pillar_cache_index` rule flag `make_pill_food` reads),
    place the tracked pillar (`simant_data_group[0x8A8C]`/`[0x8A8E]`) at
    a random point along ONE of the yard's 4 edges, and — if that point
    is valid — cache its current tile (composing the already-recovered
    `store_pillar_map`) then stamp it with a mode-specific pillar tile.

    Recovered from `_MakeAPill` (SIMANTW.SYM seg7:53DA, NO args; FAR
    return, 768 bytes; calls `_SRand1` x2, `_IsValidA` x2). Mode `0`:
    `x=_SRand1(0x80)`, `y=0x3F` (south edge), tile `0x6C`. Mode `1`:
    `x=0` (west edge), `y=_SRand1(0x40)`, tile `0x6B`. Mode `2`:
    `x=_SRand1(0x80)`, `y=0` (north edge), tile `0x6F`. Mode `3`:
    `x=0x7F` (east edge), `y=_SRand1(0x40)`, tile `0x68`. The real ASM
    calls `_IsValidA` TWICE with identical `(x, y)` — once gating the
    cache-store, once (independently) gating the tile stamp — a
    genuine redundant double-check (same coordinates both times, a
    pure deterministic predicate) collapsed to one check here, same
    precedent as `pill_food_tile`/`_paint_pillar_arm`.
    """
    from .simone import SRAND_SEED_OFF, srand1

    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, mode = srand1(seed, 4)
    pack.ww(0x9B1E, mode)

    if mode == 0:
        seed, x = srand1(seed, 0x80)
        y, tile = 0x3F, 0x6C
    elif mode == 1:
        x, tile = 0, 0x6B
        seed, y = srand1(seed, 0x40)
    elif mode == 2:
        seed, x = srand1(seed, 0x80)
        y, tile = 0, 0x6F
    elif mode == 3:
        x, tile = 0x7F, 0x68
        seed, y = srand1(seed, 0x40)
    else:
        dgroup.ww(SRAND_SEED_OFF, seed)
        return

    simant_data_group.ww(0x8A8C, x)
    simant_data_group.ww(0x8A8E, y)
    dgroup.ww(SRAND_SEED_OFF, seed)

    if is_valid_a(x, y) == 1:
        store_pillar_map(dgroup, pack, x, y)
        dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y, tile)


# Per `pack[0x9B1E]` mode (0=south,1=west,2=north,3=east): which axis the
# pillar's front/growth/movement act along, the front-check sign (movement
# uses the SAME sign; growth/arm stamps use the OPPOSITE sign), the tile
# stamped at the pillar's own (just-moved) position, and the two arm-segment
# tiles shared between the north/south pair (0) and the east/west pair (1) --
# confirmed via the raw disassembly: modes 0 and 2 jump into each other's
# tile-stamp code, as do modes 1 and 3.
_DOPILLAR_MODE_AXIS = ('y', 'x', 'y', 'x')
_DOPILLAR_MODE_FRONT_SIGN = (-1, 1, 1, -1)
_DOPILLAR_MODE_OWN_TILE = (0x6C, 0x6B, 0x6F, 0x68)
_DOPILLAR_MODE_NEAR_TILE = (0x6D, 0x69, 0x6D, 0x69)
_DOPILLAR_MODE_FAR_TILE = (0x6E, 0x6A, 0x6E, 0x6A)


def do_pillar(dgroup, pack, simant_data_group) -> None:
    """Per-tick pillar lifecycle: sow, activate, grow (in a 5-tick
    cycle), retreat, or die of overcrowding.

    Recovered from `_DoPillar` (SIMANTW.SYM seg7:4CDC, NO args; FAR
    return, 1576 bytes; near-calls `_DoSow`/`_MakeAPill`/
    `_MakePillFood`, far-calls `_IsValidA` many times). Composes all
    three already-recovered near-callees plus `store_pillar_map`/
    `replace_pillar_map`.

    When `pack[0x9B6E]` ("inside the nest") is exactly `1`, no-ops
    entirely (before even `do_sow` runs). Otherwise always runs
    `do_sow` first. If the pillar isn't active
    (`simant_data_group[0x8A8A] == 0`): activates it via `make_a_pill`,
    sets `[0x8A8A] = 1`, seeds the growth counter `pack[0x78D4] = 4`,
    and returns.

    Once active, mode (`pack[0x9B1E]`) selects an axis/sign for the
    "front" cell one step ahead of the pillar. If that cell is valid
    and occupied (life != 0): counts occupied, valid cells in the
    surrounding 3x3 block; if MORE than 5 of 9 are occupied, the
    pillar dies (`[0x8A8A] = 0`, composes `make_pill_food`) — otherwise
    the tick is a no-op. If the front is clear (or invalid): decrements
    `pack[0x78D4]`. When the decremented counter is exactly `4` (i.e.
    it just wrapped from `5`), restores the cached tile (composing
    `replace_pillar_map`) at `counter+1` (`5`) steps out, in the
    OPPOSITE sign from the front check (the "growth" direction).
    OTHERWISE (counter != 4 — the two are mutually exclusive, confirmed
    via the raw disassembly's control flow, NOT a "both happen"
    reading) direct-stamps `_DOPILLAR_MODE_NEAR_TILE` at that SAME
    `counter+1` steps. Either way, always direct-stamps
    `_DOPILLAR_MODE_FAR_TILE` at `counter` steps (both growth
    direction, each independently `is_valid_a`-gated). If the counter
    is NOT `0` after all that, returns. If it IS `0`: shifts the
    pillar's own tracked position one step in the FRONT direction; if
    that pushes it outside a generous bounding box (`x` in `-6..134`,
    `y` in `-6..69` — signed, wider than the strict yard bounds),
    deactivates and returns. Otherwise caches the new position's
    current tile (composing `store_pillar_map`), stamps
    `_DOPILLAR_MODE_OWN_TILE` there, stamps `_DOPILLAR_MODE_NEAR_TILE`
    one more growth-direction step out, and resets
    `pack[0x78D4] = 5` (restarting the 5-tick cycle).
    """
    if pack.rw(0x9B6E) == 1:
        return

    do_sow(dgroup, pack, simant_data_group)

    if simant_data_group.rw(0x8A8A) == 0:
        make_a_pill(dgroup, pack, simant_data_group)
        simant_data_group.ww(0x8A8A, 1)
        pack.ww(0x78D4, 4)
        return

    mode = pack.rw(0x9B1E)
    axis = _DOPILLAR_MODE_AXIS[mode]
    front_sign = _DOPILLAR_MODE_FRONT_SIGN[mode]
    growth_sign = -front_sign
    px = _sx16(simant_data_group.rw(0x8A8C))
    py = _sx16(simant_data_group.rw(0x8A8E))

    def along(sign, delta):
        if axis == 'y':
            return px, py + sign * delta
        return px + sign * delta, py

    fx, fy = along(front_sign, 1)
    occupied = (is_valid_a(fx, fy) == 1
                and dgroup.rb(LIFE_PLANE_BASE[0] + (fx << 6) + fy) != 0)

    if occupied:
        count = 0
        for cx in (px - 1, px, px + 1):
            for cy in (py - 1, py, py + 1):
                if (is_valid_a(cx, cy) == 1
                        and dgroup.rb(LIFE_PLANE_BASE[0] + (cx << 6) + cy) != 0):
                    count += 1
        if count > 5:
            simant_data_group.ww(0x8A8A, 0)
            make_pill_food(dgroup, pack, simant_data_group)
        return

    counter = (_sx16(pack.rw(0x78D4)) - 1) & 0xFFFF
    pack.ww(0x78D4, counter)
    counter = _sx16(counter)

    if counter == 4:
        gx, gy = along(growth_sign, counter + 1)
        if is_valid_a(gx, gy) == 1:
            replace_pillar_map(dgroup, pack, gx, gy)
    else:
        nx, ny = along(growth_sign, counter + 1)
        if is_valid_a(nx, ny) == 1:
            dgroup.wb(MAP_PLANE_BASE[0] + (nx << 6) + ny, _DOPILLAR_MODE_NEAR_TILE[mode])

    fx2, fy2 = along(growth_sign, counter)
    if is_valid_a(fx2, fy2) == 1:
        dgroup.wb(MAP_PLANE_BASE[0] + (fx2 << 6) + fy2, _DOPILLAR_MODE_FAR_TILE[mode])

    if counter != 0:
        return

    if axis == 'y':
        py += front_sign
    else:
        px += front_sign

    if not (-6 <= px <= 134 and -6 <= py <= 69):
        simant_data_group.ww(0x8A8A, 0)
        return

    simant_data_group.ww(0x8A8C, px & 0xFFFF)
    simant_data_group.ww(0x8A8E, py & 0xFFFF)

    store_pillar_map(dgroup, pack, px, py)
    if is_valid_a(px, py) == 1:
        dgroup.wb(MAP_PLANE_BASE[0] + (px << 6) + py, _DOPILLAR_MODE_OWN_TILE[mode])

    def along2(sign, delta):
        if axis == 'y':
            return px, py + sign * delta
        return px + sign * delta, py

    nx2, ny2 = along2(growth_sign, 1)
    if is_valid_a(nx2, ny2) == 1:
        dgroup.wb(MAP_PLANE_BASE[0] + (nx2 << 6) + ny2, _DOPILLAR_MODE_NEAR_TILE[mode])

    pack.ww(0x78D4, 5)


def start_attack(dgroup, pack) -> None:
    """Roll a random attack-duration/timer value into `pack[0x78DC]`.

    Recovered from `_StartAttack` (SIMANTW.SYM seg7:050E, NO args; FAR
    return, 66 bytes). The ASM's only sim-state mutation is
    `pack[0x78DC] = _SRand1(100) + 0x1E` (a value in `0x1E..0x81`); the
    rest of the routine is two confirmed presentation-only far calls —
    `GR!_myBeginSong` (a fixed song-index/volume pair) and
    `SIMANT!_EditMessage` (two fields read from a far-pointer struct at
    `pack[0x7C94]`, plus three fixed args) — neither of which touches
    any sim state this session tracks (matching `set_map`'s own "the
    original then redraws the tile, a rendering side effect, not sim
    state" precedent). The oracle test stubs both calls out rather
    than modeling them.
    """
    from .simone import SRAND_SEED_OFF, srand1

    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll = srand1(seed, 100)
    dgroup.ww(SRAND_SEED_OFF, seed)
    pack.ww(0x78DC, (roll + 0x1E) & 0xFFFF)


def init_sim_yard(dgroup, pack, simant_data_group) -> None:
    """Startup init: resets a large batch of yard-simulation fields to
    fixed defaults (mostly zero, a few nonzero constants).

    Recovered from `_InitSimYard` (SIMANTW.SYM seg7:1378, NO args; FAR
    return, 304 bytes, NO calls). Every field below was independently
    resolved to PACK or SIMANT_DATA_GROUP via a direct machine memory
    read of its DGROUP pointer-global (never assumed); a few fields are
    direct DGROUP writes (no pointer-global indirection). One field is
    already-named: `simant_data_group[0x8A80]` is the SAME ant-lion
    live-count field `find_in_lion_list`/`kill_ant_lion`/`add_ant_lion`
    already use.

    PACK: `[0x7D4C]`/`[0x7D4A]`/`[0x9FBE]`/`[0x9FBC]`/`[0x7380]`/
    `[0x737E]`/`[0x99DA]`/`[0x78B0]`/`[0x78D2]`/`[0x72B6]`/`[0x7D5A]`/
    `[0x7A4E]`/`[0x7A58]`/`[0x9AF4]`/`[0x9AFC]`/`[0x7C92]`/`[0x99E6]`/
    `[0x9B30] = 0`; `[0x7A5A]`/`[0x8A72]` (SDG, see below) `= 2`;
    `[0x72C0]`/`[0x8118] = 1`; `[0x72F2]`/`[0x7A5C] = 0xFFFF`.
    SIMANT_DATA_GROUP: `[0x8A74] = 0x0C`; `[0x8A70] = 0x14`;
    `[0x8A80]`/`[0x8A84]`/`[0x8A86]`/`[0x8A7E]`/`[0x8A82] = 0`.
    DGROUP (direct): `[0xAC76] = 0xB4`; `[0xAC78] = 0x49`;
    `[0xAC5C] = 0xFA`; `[0xAC5E] = 0x96`; `[0xAC7A]`/`[0xAC74]`/
    `[0xAC64]`/`[0xAC6A]`/`[0xAC6C]`/`[0xAC60]`/`[0xAC72] = 0`;
    `[0xAC62] = 1`.
    """
    pack.ww(0x7D4C, 0)
    pack.ww(0x7D4A, 0)
    dgroup.ww(0xAC76, 0x00B4)
    dgroup.ww(0xAC78, 0x0049)
    simant_data_group.ww(0x8A74, 0x000C)
    simant_data_group.ww(0x8A70, 0x0014)
    pack.ww(0x9FBE, 0)
    pack.ww(0x9FBC, 0)
    pack.ww(0x7380, 0)
    pack.ww(0x737E, 0)
    dgroup.ww(0xAC5C, 0x00FA)
    dgroup.ww(0xAC5E, 0x0096)
    simant_data_group.ww(0x8A72, 2)
    pack.ww(0x7A5A, 2)
    pack.ww(0x99DA, 0)
    pack.ww(0x78B0, 0)
    pack.ww(0x78D2, 0)
    dgroup.ww(0xAC7A, 0)
    dgroup.ww(0xAC74, 0)
    simant_data_group.ww(0x8A80, 0)
    simant_data_group.ww(0x8A84, 0)
    simant_data_group.ww(0x8A86, 0)
    dgroup.ww(0xAC64, 0)
    pack.ww(0x72B6, 0)
    dgroup.ww(0xAC6A, 0)
    dgroup.ww(0xAC6C, 0)
    dgroup.ww(0xAC60, 0)
    pack.ww(0x7D5A, 0)
    simant_data_group.ww(0x8A7E, 0)
    simant_data_group.ww(0x8A82, 0)
    pack.ww(0x7A4E, 0)
    pack.ww(0x7A58, 0)
    pack.ww(0x9AF4, 0)
    pack.ww(0x9AFC, 0)
    pack.ww(0x7C92, 0)
    dgroup.ww(0xAC72, 0)
    pack.ww(0x99E6, 0)
    pack.ww(0x9B30, 0)
    dgroup.ww(0xAC62, 1)
    pack.ww(0x72C0, 1)
    pack.ww(0x8118, 1)
    pack.ww(0x72F2, 0xFFFF)
    pack.ww(0x7A5C, 0xFFFF)


def clr_arrays(dgroup, simant_data_group) -> None:
    """Startup/new-game reset: zeroes every world-sim array this session
    already tracks by name, plus one still-unrecovered companion field.

    Recovered from `_ClrArrays` (SIMANTW.SYM seg7:6DEC, NO args; FAR
    return, 274 bytes, NO calls). Zeroes, in order:

    - The full yard map + life planes (`MAP_PLANE_BASE[0]`/
      `LIFE_PLANE_BASE[0]`, 0x2000 bytes each — the 128x64 yard span).
    - The full nest-plane-2 and nest-plane-3 map + life planes
      (`MAP_PLANE_BASE[2]`/`[3]`, `LIFE_PLANE_BASE[2]`/`[3]`, 0x1000
      bytes each — the 64x64 nest span), plus their `_FixExitMap`/`_GetExitDir`/
      `_GetEnterDir` companion "exit map" arrays on SIMANT_DATA_GROUP
      (`[0x3A4..)`/`[0x13A4..)`, the SAME fields those routines use).
    - Six evenly-spaced (`0x800`-byte-apart) SIMANT_DATA_GROUP scent
      grids: the alarm grid `[0x52D2..)`, an UNRECOVERED companion
      field `[0x5AD2..)` (no consumer identified in this session yet),
      then the black/red nest and trail scent grids `[0x62D2..)`/
      `[0x6AD2..)`/`[0x72D2..)`/`[0x7AD2..)` (`colony_smell_decay_bn`/
      `rn`, `jam_scent_bt`/`rt`'s own grids), each `0x800` bytes.
    - The A/B/R-list per-slot arrays this session has used throughout
      (caste/field_c/field_e): A-list `[0x2F62]`/`[0x2B78]`/`[0x334C]`
      (1000 bytes each, the SAME 1000-slot cap `[0x80F0] >= 0x3E8`
      elsewhere gates on), B-list `[0x3D18]`/`[0x3B22]`/`[0x3F0E]` and
      R-list `[0x46E6]`/`[0x44F0]`/`[0x48DC]` (500 bytes each).
    - `reproduce`'s two 192-byte per-colony grids (`[0xA4..)`/
      `[0x164..)`).
    """
    for off in range(0x80 * 0x40):
        dgroup.wb(MAP_PLANE_BASE[0] + off, 0)
        dgroup.wb(LIFE_PLANE_BASE[0] + off, 0)

    for off in range(0x40 * 0x40):
        dgroup.wb(MAP_PLANE_BASE[2] + off, 0)
        dgroup.wb(MAP_PLANE_BASE[3] + off, 0)
        simant_data_group.wb(0x3A4 + off, 0)
        simant_data_group.wb(0x13A4 + off, 0)
        dgroup.wb(LIFE_PLANE_BASE[2] + off, 0)
        dgroup.wb(LIFE_PLANE_BASE[3] + off, 0)

    for base in (0x52D2, 0x5AD2, 0x62D2, 0x6AD2, 0x72D2, 0x7AD2):
        for off in range(0x800):
            simant_data_group.wb(base + off, 0)

    for base, n in ((0x334C, 1000), (0x2B78, 1000), (0x2F62, 1000),
                    (0x48DC, 500), (0x44F0, 500), (0x46E6, 500),
                    (0x3F0E, 500), (0x3B22, 500), (0x3D18, 500)):
        for off in range(n):
            simant_data_group.wb(base + off, 0)

    for off in range(0xC0):
        simant_data_group.wb(0xA4 + off, 0)
        simant_data_group.wb(0x164 + off, 0)


def gstr_r(dgroup, pack) -> int:
    """The red colony's "should we attack?" strategy pick. Returns a
    threat-tier code `1..5`, or `0` whenever it decides to actually
    fire an attack this tick (composing `start_attack`, inlined
    byte-identically in the real ASM rather than called).

    Recovered from `_GstrR` (SIMANTW.SYM seg7:03C2, NO args; FAR
    return, 332 bytes; calls `_SRand32`, `_SRand128`). The whole
    function runs with `DS` explicitly overridden to the raw PACK
    selector (`5EF3h`, a literal immediate, not a pointer-global —
    same precedent as `_StartAttack`'s own literal `ds=5EF3h`), so it
    reaches DGROUP fields via explicit `SS:` prefixes instead (SS ==
    DGROUP in this small-model app) — both segments are read here.

    First, an idle "attack timer" gate: if `pack[0x8078] == 0` and
    `dgroup[0xACA6] + dgroup[0xACA8] > 0x14`, seeds
    `pack[0x8078] = 0xC8`. Then, if `pack[0x78DC]` (the SAME timer
    `start_attack` sets) is nonzero, decrements it and returns `0`
    immediately (no attack fires — a pending attack is already
    cooling down).

    Otherwise, `cx = dgroup[0xAC88]` selects a tier:
    - `cx < 10`: if `(dgroup[0xAC84] >> 1) > dgroup[0xAC82] > 0` and
      `pack[0x78E8] > 0`, fires `start_attack` and returns `0`.
      Otherwise falls through to the tier checks below (so `cx < 10`
      without a fired attack still returns `5`, since `cx < 30`).
    - `cx < 30`: returns `5`.
    - `cx < 50`: returns `4`.
    - Otherwise (`cx >= 50`): `di = dgroup[0xAC84]`. Returns `3` if
      `pack[0x7A56] < di`, or `2` if `pack[0x7A56] < di*2` (the SHL
      truncates to 16 bits, same as the raw ASM register op, before
      the signed compare). Otherwise, if `di > 100` and
      `dgroup[0xAC82] > 0` and `pack[0x78E8] > 0` and
      `(di // 3, C-style truncating) > dgroup[0xAC82]`: fires
      `start_attack`, returns `0`. Otherwise (or whenever `di <= 100`):
      rolls `_SRand32()` and `_SRand128()` (each `== 0` is a
      long-odds hit) — if `_SRand32() == 0` AND `dgroup[0xAC84] > 20`
      AND `dgroup[0xAC82] < dgroup[0xAC84]` AND `_SRand128() == 0`:
      fires `start_attack`, returns `0`. Otherwise returns `1`.
    """
    from .simone import SRAND_SEED_OFF, srand_pow2

    def tdiv(v, d):
        q = abs(v) // d
        return -q if v < 0 else q

    if _sx16(pack.rw(0x8078)) == 0:
        if _sx16(dgroup.rw(0xACA6)) + _sx16(dgroup.rw(0xACA8)) > 0x14:
            pack.ww(0x8078, 0xC8)

    timer = _sx16(pack.rw(0x78DC))
    if timer != 0:
        pack.ww(0x78DC, (timer - 1) & 0xFFFF)
        return 0

    cx = _sx16(dgroup.rw(0xAC88))
    if cx < 10:
        ac84 = _sx16(dgroup.rw(0xAC84))
        ac82 = _sx16(dgroup.rw(0xAC82))
        if (ac84 >> 1) > ac82 and ac82 > 0 and _sx16(pack.rw(0x78E8)) > 0:
            start_attack(dgroup, pack)
            return 0

    if cx < 30:
        return 5
    if cx < 50:
        return 4

    di_raw = dgroup.rw(0xAC84)
    di = _sx16(di_raw)
    a7a56 = _sx16(pack.rw(0x7A56))
    if a7a56 < di:
        return 3
    if a7a56 < _sx16((di_raw << 1) & 0xFFFF):
        return 2

    if di > 100:
        ac82 = _sx16(dgroup.rw(0xAC82))
        if ac82 > 0 and _sx16(pack.rw(0x78E8)) > 0 and tdiv(di, 3) > ac82:
            start_attack(dgroup, pack)
            return 0

    seed = dgroup.rw(SRAND_SEED_OFF)
    seed, roll32 = srand_pow2(seed, 31)
    if roll32 == 0:
        ac84 = _sx16(dgroup.rw(0xAC84))
        ac82 = _sx16(dgroup.rw(0xAC82))
        if ac84 > 20 and ac82 < ac84:
            seed, roll128 = srand_pow2(seed, 127)
            dgroup.ww(SRAND_SEED_OFF, seed)
            if roll128 == 0:
                start_attack(dgroup, pack)
                return 0
            return 1
    dgroup.ww(SRAND_SEED_OFF, seed)
    return 1


def get_strategy(dgroup, simant_data_group, pack) -> None:
    """Per-tick top-level strategy update: jitters a "last known threat"
    marker, sets a nearby-danger flag, picks the black colony's own
    threat-tier code, and composes `gstr_r` (the red colony's own pick,
    which may itself fire an attack) plus `set_caste_prod`/
    `set_mode_prod`.

    Recovered from `_GetStrategy` (SIMANTW.SYM seg7:0000, NO args; FAR
    return, 460 bytes; calls `_SRand1(5)` x2, `_GetDis`, near-calls
    `_GstrR`/`_SetCasteProd`/`_SetModeProd`). Zeroes `pack[0x72EC]`
    (a "danger nearby" flag). If `dgroup[0xCE80] == 1` (the SAME
    world-state "mode" flag `is_it_yellow` reads): jitters two marker
    fields via `_SRand1(5) + dgroup[0xCD88 or 0xCE7E] - 2`, clamps them
    to the yard bounds (`0..0x7F`, `0..0x3F`), and stores them into
    `pack[0x9FE4]`/`[0x9FEA]` — genuinely CONFIRMED dead-but-executed
    work for `[0x9FE4]` specifically (it gets unconditionally
    overwritten below regardless of this branch), kept here only
    because the `_SRand1` draws it consumes are observable via the
    shared LFSR seed. Then, if `pack[0x9BD2]` is nonzero, composes
    `get_dis` on `(dgroup[0xAC7C]>>4, dgroup[0xAC7E]>>4)` (a signed
    arithmetic shift, i.e. `//16` toward zero) vs
    `(dgroup[0xCD88], dgroup[0xCE7E])`; a distance `< 100` sets the
    danger flag.

    Unconditionally sets `pack[0x9FE4] = dgroup[0xCD88]` (the dead-code
    overwrite above). Then, MIRRORING `gstr_r`'s own tier logic but
    over different fields (no `_SRand32`/`_SRand128` longshot here, and
    the "attack fires" result is just a plain code, not a call) —
    `dgroup[0xAC86]` selects a tier: `< 10` and `(dgroup[0xAC82]>>1) >
    dgroup[0xAC84] > 0` and `pack[0x79DC] > 0` picks code `0`;
    otherwise `< 30` -> `5`, `< 50` -> `4`; otherwise
    `dgroup[0xAC82]` vs `pack[0x72C8]` (code `3`/`2`, same `< di` /
    `< di*2` shape as `gstr_r`'s `[0x7A56]` checks) or, past that,
    `dgroup[0xAC82] > 100` and `dgroup[0xAC84] > 0` and
    `pack[0x79DC] > 0` and `(dgroup[0xAC82] // 3, C-style truncating)
    > dgroup[0xAC84]` picks code `0`, else code `1`. Stores the result
    into `pack[0x9B8A]`.

    Finally composes `gstr_r` (storing its own result into
    `pack[0x7690]`) then `set_caste_prod`/`set_mode_prod`.
    """
    from .simone import SRAND_SEED_OFF, srand1

    def tdiv(v, d):
        q = abs(v) // d
        return -q if v < 0 else q

    pack.ww(0x72EC, 0)

    if dgroup.rw(0xCE80) == 1:
        seed = dgroup.rw(SRAND_SEED_OFF)
        seed, r1 = srand1(seed, 5)
        v1 = _sx16((r1 + dgroup.rw(0xCD88) - 2) & 0xFFFF)
        seed, r2 = srand1(seed, 5)
        v2 = _sx16((r2 + dgroup.rw(0xCE7E) - 2) & 0xFFFF)
        dgroup.ww(SRAND_SEED_OFF, seed)

        v1 = max(0, min(v1, 0x7F))
        pack.ww(0x9FE4, v1 & 0xFFFF)
        v2 = max(0, min(v2, 0x3F))
        pack.ww(0x9FEA, v2 & 0xFFFF)

        if pack.rw(0x9BD2) != 0:
            x1 = _sx16(dgroup.rw(0xAC7C)) >> 4
            y1 = _sx16(dgroup.rw(0xAC7E)) >> 4
            x2 = _sx16(dgroup.rw(0xCD88))
            y2 = _sx16(dgroup.rw(0xCE7E))
            if get_dis(x1, y1, x2, y2) < 0x64:
                pack.ww(0x72EC, 1)

    pack.ww(0x9FE4, dgroup.rw(0xCD88))

    ac86 = _sx16(dgroup.rw(0xAC86))
    strategy = None
    if ac86 < 10:
        ac84 = _sx16(dgroup.rw(0xAC84))
        ac82 = _sx16(dgroup.rw(0xAC82))
        if (ac82 >> 1) > ac84 and ac84 > 0 and _sx16(pack.rw(0x79DC)) > 0:
            strategy = 0

    if strategy is None:
        if ac86 < 30:
            strategy = 5
        elif ac86 < 50:
            strategy = 4
        else:
            di_raw = dgroup.rw(0xAC82)
            di = _sx16(di_raw)
            a72c8 = _sx16(pack.rw(0x72C8))
            if a72c8 < di:
                strategy = 3
            elif a72c8 < _sx16((di_raw << 1) & 0xFFFF):
                strategy = 2
            else:
                fire = False
                if di > 0x64:
                    ac84 = _sx16(dgroup.rw(0xAC84))
                    if ac84 > 0 and _sx16(pack.rw(0x79DC)) > 0 and tdiv(di, 3) > ac84:
                        fire = True
                strategy = 0 if fire else 1

    pack.ww(0x9B8A, strategy & 0xFFFF)

    red_result = gstr_r(dgroup, pack)
    pack.ww(0x7690, red_result & 0xFFFF)

    set_caste_prod(dgroup, simant_data_group)
    set_mode_prod(simant_data_group, pack)


def add_food(dgroup, pack, simant_data_group, table_view, table_off,
             count: int, flag: int) -> None:
    """Scatter up to `count` food/rock piles in a roughly circular
    pattern around a center point, using fixed-point trig
    (`frac_sin`/`frac_cos`) to pick each candidate's offset.

    Recovered from `_AddFood` (SIMANTW.SYM seg7:6A58, args count=
    [bp+6], flag=[bp+8]; FAR return, 514 bytes; calls `_SRand1`/`8`/
    `48`/`64`/`128`/`256`, composes `frac_sin`/`frac_cos`/`a_f_ldiv`,
    far-calls `GR!_myBeginSound` when `flag == 1` — presentation-only,
    stubbed rather than modeled, matching `_StartAttack`'s own
    precedent).

    If `count >= 0`: the center is `(x=_SRand128(), y=_SRand64())` and
    the loop runs `count` times (the caller-supplied count IS the real
    loop bound here — `feed_ants` calls `add_food(0x96, 1)`, i.e. up
    to 150 placements at a fully random position each, independently
    confirmed against the real ASM after an inverted first-draft read
    of this branch pair). If `count < 0`: the center is FIXED at
    `(x=0x40, y=_SRand1(48)+8)` and the loop runs exactly `200` times
    regardless of the actual negative value (a large initial-scatter
    batch, centered on the yard's horizontal middle) — but the
    `_SRand128`/`_SRand64` draws for the `count >= 0` branch's OWN
    center still happen even when they'll be discarded... no: each
    branch computes ONLY its own center (never both), so there is no
    discarded work here — the "dead but LFSR-observable" pattern
    `get_strategy` showed does NOT apply to this pair. The resolved
    center is always mirrored into `simant_data_group[0x836A]`/
    `[0x836C]` (NOT `pack` — independently confirmed via a direct
    machine memory read of the pointer-global, not assumed from the
    surrounding PACK-heavy fields).

    Each iteration: rolls an angle (`_SRand256()`) and, using a radius
    cap rolled ONCE before the loop (`_SRand8() + 5`), a fresh radius
    magnitude (`_SRand1(radius_cap)`); the candidate offset is
    `frac_cos(angle) * radius // 0x7FFF` (composing `a_f_ldiv`, whose
    32-bit result is truncated to its low word — only `AX`, never
    `DX`, feeds the addition below, confirmed via the raw
    disassembly) for x, `frac_sin(angle) * radius // 0x7FFF` for y,
    added to the center.
    Out-of-yard-bounds or already-occupied (life != 0) candidates are
    skipped. Otherwise the existing yard map tile picks a branch by
    range, split by `pack[0x9B6E]` ("inside the nest"):
    inside — `>= 0x28`: skip; `[0x18, 0x28)`: skip if `tile % 4 == 3`
    else increment; `[4, 0x18)`: stamp `(((tile - 8) & 0xFC) + 0x18) &
    0xFF`; `< 4`: stamp `((tile + 6) & 0xFF) << 2 & 0xFF` (this pair
    was swapped in a first-draft reading — independently re-confirmed
    against the raw `cmp bx,4; jge` branch target, not just patched to
    fit the failing test).
    outside — `[0x18, 0x48)` or `>= 0x4B`: skip (note: NOT `> 0x4B` —
    `0x4B` itself is excluded, independently confirmed via the raw
    disassembly, a narrower range than `is_it_food`'s own `<= 0x4B`
    outside-food test); `[0x48, 0x4B)`: increment; `< 0x18`: stamp the
    fixed food tile `0x48`. Any successful stamp/increment bumps
    `pack[0x9E84]` (the SAME per-drop counter `food_fall`/
    `drop_food_a` already bump).
    """
    from .crt_math import a_f_ldiv
    from .simone import SRAND_SEED_OFF, srand1, srand_pow2

    seed = dgroup.rw(SRAND_SEED_OFF)

    if count >= 0:
        seed, center_x = srand_pow2(seed, 127)
        seed, center_y = srand_pow2(seed, 63)
        loop_count = count
    else:
        center_x = 0x40
        seed, r = srand1(seed, 48)
        center_y = (r + 8) & 0xFFFF
        loop_count = 200

    simant_data_group.ww(0x836A, center_x & 0xFFFF)
    simant_data_group.ww(0x836C, center_y & 0xFFFF)

    seed, radius_cap = srand1(seed, 8)
    radius_cap += 5

    if loop_count > 0:
        inside = pack.rw(0x9B6E) != 0
        for _ in range(loop_count):
            seed, angle = srand1(seed, 256)
            seed, radius = srand1(seed, radius_cap)

            x_trig = frac_cos(table_view, table_off, angle)
            x_delta = _sx16(a_f_ldiv(x_trig * radius, 0x7FFF) & 0xFFFF)
            cand_x = _sx16(center_x) + x_delta

            y_trig = frac_sin(table_view, table_off, angle)
            y_delta = _sx16(a_f_ldiv(y_trig * radius, 0x7FFF) & 0xFFFF)
            cand_y = _sx16(center_y) + y_delta

            if not (0 <= cand_x <= 0x7F):
                continue
            if not (0 <= cand_y <= 0x3F):
                continue
            cell = (cand_x << 6) + cand_y
            if dgroup.rb(LIFE_PLANE_BASE[0] + cell) != 0:
                continue
            map_cell = MAP_PLANE_BASE[0] + cell
            tile = dgroup.rb(map_cell)

            if inside:
                if tile >= 0x18:
                    if tile >= 0x28:
                        continue
                    if tile % 4 == 3:
                        continue
                    dgroup.wb(map_cell, (tile + 1) & 0xFF)
                elif tile >= 4:
                    dgroup.wb(map_cell, (((tile - 8) & 0xFC) + 0x18) & 0xFF)
                else:
                    dgroup.wb(map_cell, (((tile + 6) & 0xFF) << 2) & 0xFF)
            else:
                if tile >= 0x18:
                    if tile < 0x48 or tile >= 0x4B:
                        continue
                    dgroup.wb(map_cell, (tile + 1) & 0xFF)
                else:
                    dgroup.wb(map_cell, 0x48)

            pack.ww(0x9E84, (pack.rw(0x9E84) + 1) & 0xFFFF)

    dgroup.ww(SRAND_SEED_OFF, seed)


def is_liftable(pack, simant_data_group, dgroup, plane: int, x: int, y: int) -> int:
    """Whether an ant could pick up whatever's at `(plane, x, y)` — food,
    a specific "liftable object" tile, or an egg/larva in its early
    growth stages.

    Recovered from `_IsLiftable` (SIMANTW.SYM seg5:97CA, args plane=
    [bp+6], x=[bp+8], y=[bp+10]; FAR return, 276 bytes; composes the
    already-recovered `find_egg_at` and `is_it_food`). `find_egg_at`
    runs FIRST and unconditionally (it has its own internal validity
    gate, so calling it before this routine's own is fine) — only its
    SECOND tuple element (the AX-returned egg/larva tile value) is
    ever consulted below; the first (the OUT-pointer slot) is written
    but never read here, matching the real ASM's own unused
    `[bp-6]`/`[bp-4]` local. Separately reads the raw map tile at
    `(x, y)` — `< 4`, an OUT-of-bounds `is_valid_location` failure, OR
    (independently confirmed via the raw disassembly) a `plane` other
    than exactly `0`/`1`/`2`/`3` all fall back to the sentinel `-1`
    (never matching any of the ranges below), even though a `plane >
    3` could still pass `is_valid_location`'s own nest-bounds check.

    Liftable if ANY of: `plane <= 1` and `is_it_food` says so (reading
    the SAME `pack[0x9B6E]` "inside the nest" flag); `plane > 1` and
    the map tile is in `[0x10, 0x13]`; `plane == 1` and the tile is in
    `[0x51, 0x53]`; `plane > 1` and the tile is in `[0x30, 0x31]`
    (`plane == 0` never matches this pair — confirmed via the raw
    disassembly's `jnz`-skips-the-check polarity); or the egg tile's
    `(value & 0x7F)` is in `1..7` (the growth-stage range
    `find_egg_at`'s own docstring already established). Returns 1/0.
    """
    _, egg_tile = find_egg_at(pack, simant_data_group, dgroup, plane, x, y)

    if is_valid_location(plane, x, y) != 1:
        map_tile = -1
    elif plane <= 1:
        map_tile = dgroup.rb(MAP_PLANE_BASE[0] + (x << 6) + y)
    elif plane == 2:
        map_tile = dgroup.rb(MAP_PLANE_BASE[2] + (x << 6) + y)
    elif plane == 3:
        map_tile = dgroup.rb(MAP_PLANE_BASE[3] + (x << 6) + y)
    else:
        map_tile = -1

    if plane <= 1:
        is_food_like = is_it_food(map_tile, pack.rw(0x9B6E) != 0)
    else:
        is_food_like = 1 if 0x10 <= map_tile <= 0x13 else 0

    if is_food_like:
        return 1

    special = 0
    if plane == 1:
        if 0x51 <= map_tile <= 0x53:
            special = 1
    elif plane > 1:
        if 0x30 <= map_tile <= 0x31:
            special = 1

    if special:
        return 1

    if 1 <= (egg_tile & 0x7F) <= 7:
        return 1

    return 0


def place_drop(dgroup, pack, simant_data_group, slot: int) -> None:
    """Roll a random yard cell for water drop `slot`, record its
    position, and — if the current tile is clear enough (`< 0x0E`) —
    stamp it with the water tile `0x74` and wash away nearby scent at
    the SAME half-res (`>>1`, 64x32) grid cell the alarm/scent family
    already uses.

    Recovered from `_PlaceDrop` (SIMANTW.SYM seg5:0ACC, arg
    slot=[bp+6]; FAR return, 170 bytes; composes the already-recovered
    `r_rand`). Rolls `x = _RRand(0x80)`, `y = _RRand(0x40)` (the `_RRand`
    family's own C-runtime generator, NOT the `_SRand*` LFSR), recorded
    into `pack[0x79E6+slot]`/`[0x7A72+slot]` regardless of whether the
    stamp below actually happens. On a clear cell: stamps the water
    tile, zeroes `simant_data_group[0x52D2+idx]` (the alarm grid) and
    `[0x5AD2+idx]` (its still-unrecovered companion — the SAME two
    fields `clr_arrays` already zeroes in bulk) and the trail-scent
    grids `[0x6AD2+idx]`/`[0x7AD2+idx]` (`jam_scent_bt`/`rt`'s own
    grids) outright, and DECAYS (by `0x14`, floored at `0`, rather
    than zeroing) the nest-scent grids `[0x62D2+idx]`/`[0x72D2+idx]`
    (`colony_smell_decay_bn`/`rn`'s own grids) — `idx = (x>>1)*32 +
    (y>>1)`, the SAME half-resolution indexing the alarm grid's own
    `alarm_update`/`alarm_decay` already use.
    """
    from .crt_math import RAND_STATE_OFF
    from .simone import r_rand

    state = dgroup.rw(RAND_STATE_OFF) | (dgroup.rw(RAND_STATE_OFF + 2) << 16)
    state, x = r_rand(state, 0x80)
    state, y = r_rand(state, 0x40)
    dgroup.ww(RAND_STATE_OFF, state & 0xFFFF)
    dgroup.ww(RAND_STATE_OFF + 2, (state >> 16) & 0xFFFF)

    pack.wb(0x79E6 + slot, x & 0xFF)
    pack.wb(0x7A72 + slot, y & 0xFF)

    cell = MAP_PLANE_BASE[0] + (x << 6) + y
    if dgroup.rb(cell) >= 0x0E:
        return
    dgroup.wb(cell, 0x74)

    idx = ((x >> 1) << 5) + (y >> 1)
    simant_data_group.wb(0x52D2 + idx, 0)
    simant_data_group.wb(0x5AD2 + idx, 0)
    nest_bn = simant_data_group.rb(0x62D2 + idx)
    simant_data_group.wb(0x62D2 + idx, nest_bn - 0x14 if nest_bn >= 0x14 else 0)
    simant_data_group.wb(0x6AD2 + idx, 0)
    nest_rn = simant_data_group.rb(0x72D2 + idx)
    simant_data_group.wb(0x72D2 + idx, nest_rn - 0x14 if nest_rn >= 0x14 else 0)
    simant_data_group.wb(0x7AD2 + idx, 0)


def init_water(dgroup, pack, simant_data_group) -> None:
    """Place 100 random water drops (slots `0..99`).

    Recovered from `_InitWater` (SIMANTW.SYM seg5:0B76, NO args; FAR
    return, 20 bytes). Composes `place_drop`.
    """
    for slot in range(100):
        place_drop(dgroup, pack, simant_data_group, slot)


def add_water(dgroup, pack, simant_data_group, col: int) -> None:
    """Flood one full nest column `y=col`: mark any black/red ants
    standing there as drowning, then erode every cell's tile on BOTH
    nest planes 2 and 3 (`x` ranging the whole `0..63` column).

    Recovered from `_AddWater` (SIMANTW.SYM seg5:0B8A, arg col=[bp+6];
    FAR return, 202 bytes; composes the already-recovered
    `drown_b_list`/`drown_r_list`; far-calls `ANTEDIT!_ZapEuMapAt`
    twice per row (screen-redraw invalidation, presentation-only —
    stubbed in the oracle test rather than modeled, same convention as
    `_StartAttack`'s `GR!` calls)). For each `x` in `0..63`: a tile
    `< 0x20` becomes the fixed `0x4E`; otherwise it becomes
    `tile + 0x2F` (an "erosion" transform — both nest planes get the
    SAME formula independently applied).
    """
    drown_b_list(pack, simant_data_group, col)
    drown_r_list(pack, simant_data_group, col)

    for x in range(0x40):
        for plane in (2, 3):
            cell = MAP_PLANE_BASE[plane] + (x << 6) + col
            tile = dgroup.rb(cell)
            new_tile = 0x4E if tile < 0x20 else (tile + 0x2F) & 0xFF
            dgroup.wb(cell, new_tile)


def fill_map(dgroup, x1: int, x2: int, y1: int, y2: int, value: int) -> None:
    """Fill the yard map rectangle columns `x1..x2`, rows `y1..y2` (both
    inclusive) with the fixed byte `value`.

    Recovered from `_FillMap` (SIMANTW.SYM seg5:3F54, args x1=[bp+6],
    x2=[bp+8], y1=[bp+10], y2=[bp+12], value=[bp+14]; FAR return, 86
    bytes). No-ops entirely if `x1 > x2` (the ASM's own outer-loop guard,
    a raw `jg` skip to the epilogue). Independently, EACH column
    iteration re-checks `y2 < y1` and skips that column's fill if so —
    since neither bound changes across columns this is loop-invariant
    (either every column fills or none do), so it collapses to the same
    single `x1 <= x2 and y1 <= y2` gate as a whole-rectangle no-op,
    verified against the raw per-column re-check rather than assumed.
    Each column's `y1..y2` run is written via a single contiguous
    `rep stosb` (the map's `(x<<6)+y` layout makes one column's rows
    contiguous in memory) — ported here as a plain nested loop, since
    the effect on the DGROUP image is identical either way.
    """
    if x1 > x2 or y2 < y1:
        return
    for x in range(x1, x2 + 1):
        base = MAP_PLANE_BASE[0] + (x << 6)
        for y in range(y1, y2 + 1):
            dgroup.wb(base + y, value & 0xFF)


def _tile_frame(dgroup, x1: int, x2: int, y1: int, y2: int, *,
                 top: int, bottom: int, left: int, right: int,
                 tl: int, tr: int, bl: int, br: int) -> None:
    """Shared body of `_TileFrame1`/`_TileFrame2` — draws a rectangular
    border on the yard map from `(x1, y1)` to `(x2, y2)` (both inclusive):
    a `top`-tile row at `y1` and a `bottom`-tile row at `y2` (each spanning
    `x1..x2`, gated on `x1 <= x2` — a raw `jg` skip in the ASM, independent
    of the y-gate below), a `left`-tile column at `x1` and a `right`-tile
    column at `x2` (each spanning `y1..y2`, gated on `y1 <= y2`), and
    finally the four corners `(x1,y1)=tl`, `(x2,y1)=tr`, `(x1,y2)=bl`,
    `(x2,y2)=br` — written LAST and UNCONDITIONALLY (no `x1<=x2`/`y1<=y2`
    gate at all on the corner writes, confirmed via the raw disassembly),
    so they always land even when one or both edge-gates skip their pass,
    and they always overwrite whatever an edge pass wrote underneath.
    """
    if x1 <= x2:
        for x in range(x1, x2 + 1):
            dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y1, top)
        for x in range(x1, x2 + 1):
            dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y2, bottom)
    if y1 <= y2:
        for y in range(y1, y2 + 1):
            dgroup.wb(MAP_PLANE_BASE[0] + (x1 << 6) + y, left)
        for y in range(y1, y2 + 1):
            dgroup.wb(MAP_PLANE_BASE[0] + (x2 << 6) + y, right)
    dgroup.wb(MAP_PLANE_BASE[0] + (x1 << 6) + y1, tl)
    dgroup.wb(MAP_PLANE_BASE[0] + (x2 << 6) + y1, tr)
    dgroup.wb(MAP_PLANE_BASE[0] + (x1 << 6) + y2, bl)
    dgroup.wb(MAP_PLANE_BASE[0] + (x2 << 6) + y2, br)


def tile_frame1(dgroup, x1: int, x2: int, y1: int, y2: int) -> None:
    """Draw a `_TileFrame1`-style border: top=`0x54`, bottom=`0x51`,
    left=`0x5A`, right=`0x5B`, corners top-left=`0x53`, top-right=`0x55`,
    bottom-left=`0x50`, bottom-right=`0x52`.

    Recovered from `_TileFrame1` (SIMANTW.SYM seg5:3944, args x1=[bp+6],
    x2=[bp+8], y1=[bp+10], y2=[bp+12]; FAR return, 350 bytes). See
    `_tile_frame` for the shared edge/corner/gating logic.
    """
    _tile_frame(dgroup, x1, x2, y1, y2, top=0x54, bottom=0x51, left=0x5A,
                right=0x5B, tl=0x53, tr=0x55, bl=0x50, br=0x52)


def tile_frame2(dgroup, x1: int, x2: int, y1: int, y2: int) -> None:
    """Draw a `_TileFrame2`-style border — the SAME shape as `tile_frame1`
    but a different (also self-consistent) tile palette: top=`0x51`,
    bottom=`0x54`, left=`0x5B`, right=`0x5A`, corners top-left=`0x56`,
    top-right=`0x58`, bottom-left=`0x57`, bottom-right=`0x59`.

    Recovered from `_TileFrame2` (SIMANTW.SYM seg5:3AA2, args x1=[bp+6],
    x2=[bp+8], y1=[bp+10], y2=[bp+12]; FAR return, 350 bytes;
    instruction-for-instruction identical structure to `_TileFrame1`,
    independently confirmed byte-for-byte against its own disassembly —
    only the eight tile-id immediates differ).
    """
    _tile_frame(dgroup, x1, x2, y1, y2, top=0x51, bottom=0x54, left=0x5B,
                right=0x5A, tl=0x56, tr=0x58, bl=0x57, br=0x59)


def _stamp_glyph(dgroup, table_base: int, dest_base: int, width: int,
                  height: int, sparse: bool = False) -> None:
    """Blit a `width` x `height` tile-glyph raster read from DGROUP static
    data at `table_base` onto the yard map at `dest_base` (already the
    DGROUP offset of the glyph's own top-left destination cell): column
    `c` (0..width-1), row `r` (0..height-1) of the glyph goes to
    `dest_base + (c<<6) + r`, reading its source byte from
    `table_base + r*width + c` — i.e. the table is stored row-major,
    `width` bytes per row (confirmed from the raw disassembly: the
    destination's OUTER loop is the column, the INNER loop is the row,
    and the table pointer strides by `width` each inner step).

    When `sparse`, a table byte of `0` is a transparent "skip this cell"
    marker (`_MakeClip`'s own behaviour, vs. `_MakePenny`'s unconditional
    copy of every byte including a literal `0` — a genuine, independently
    confirmed asymmetry between the two otherwise-identical-shaped
    routines).
    """
    for c in range(width):
        for r in range(height):
            val = dgroup.rb(table_base + r * width + c)
            if sparse and val == 0:
                continue
            dgroup.wb(dest_base + (c << 6) + r, val)


def make_plug_v(dgroup, x: int, y: int) -> None:
    """Stamp the 5-wide x 4-tall "vertical plug" glyph at yard map `(x, y)`
    (glyph raster read from DGROUP static data `0x2314..0x2327`).

    Recovered from `_MakePlugV` (SIMANTW.SYM seg5:3D02, args x=[bp+6],
    y=[bp+8]; FAR return, 66 bytes). Composes `_stamp_glyph`.
    """
    _stamp_glyph(dgroup, 0x2314, MAP_PLANE_BASE[0] + (x << 6) + y, 5, 4)


def make_plug_h(dgroup, x: int, y: int) -> None:
    """Stamp the 4-wide x 5-tall "horizontal plug" glyph at yard map
    `(x, y)` (glyph raster read from DGROUP static data `0x2328..0x233B`
    — the transposed twin of `make_plug_v`'s own `0x2314` table,
    immediately contiguous with it).

    Recovered from `_MakePlugH` (SIMANTW.SYM seg5:3E46, args x=[bp+6],
    y=[bp+8]; FAR return, 66 bytes). Composes `_stamp_glyph`.
    """
    _stamp_glyph(dgroup, 0x2328, MAP_PLANE_BASE[0] + (x << 6) + y, 4, 5)


def make_knob(dgroup, x: int, y: int) -> None:
    """Stamp the 5-wide x 5-tall "knob" glyph at yard map `(x, y)` (glyph
    raster read from DGROUP static data `0x233C..0x2354`, immediately
    contiguous after `make_plug_h`'s own table).

    Recovered from `_MakeKnob` (SIMANTW.SYM seg5:3E88, args x=[bp+6],
    y=[bp+8]; FAR return, 66 bytes). Composes `_stamp_glyph`.
    """
    _stamp_glyph(dgroup, 0x233C, MAP_PLANE_BASE[0] + (x << 6) + y, 5, 5)


def make_penny(dgroup, x: int, y: int) -> None:
    """Stamp the 3-wide x 3-tall "penny" glyph at yard map `(x, y)` (glyph
    raster read from DGROUP static data `0x2356..0x235E`) — every table
    byte is written unconditionally, including a literal `0` (contrast
    `make_clip`'s sparse copy of the SAME-shaped table just below it).

    Recovered from `_MakePenny` (SIMANTW.SYM seg5:3ECA, args x=[bp+6],
    y=[bp+8]; FAR return, 66 bytes). Composes `_stamp_glyph`.
    """
    _stamp_glyph(dgroup, 0x2356, MAP_PLANE_BASE[0] + (x << 6) + y, 3, 3)


def make_clip(dgroup, x: int, y: int) -> None:
    """Stamp the 3-wide x 3-tall "clip" glyph at yard map `(x, y)` (glyph
    raster read from DGROUP static data `0x2360..0x2368`) — a `0` table
    byte is a transparent "leave the existing map tile alone" marker,
    independently confirmed via the raw disassembly's own `cmp ds:[si],0 /
    jz <skip write>` (the ONE structural difference from the otherwise
    textually-identical `make_penny`).

    Recovered from `_MakeClip` (SIMANTW.SYM seg5:3F0C, args x=[bp+6],
    y=[bp+8]; FAR return, 72 bytes). Composes `_stamp_glyph` (sparse).
    """
    _stamp_glyph(dgroup, 0x2360, MAP_PLANE_BASE[0] + (x << 6) + y, 3, 3,
                 sparse=True)


def make_outlet_v(dgroup, x: int, y: int) -> None:
    """Paint a vertical double-outlet wall panel anchored at yard map
    `(x, y)`: a `0x63`-filled 9-wide (`x..x+8`) x 13-tall (`y..y+12`)
    background panel, a `tile_frame1` border around that SAME rectangle,
    two `make_plug_v`-shaped glyphs (the SAME `0x2314` table `make_plug_v`
    itself reads) stamped inset at `(x+2, y+2)` and `(x+2, y+7)` — i.e.
    stacked vertically 5 rows apart — and a single `0x65` "screw" tile at
    `(x+4, y+6)`.

    Recovered from `_MakeOutletV` (SIMANTW.SYM seg5:3C00, args x=[bp+6],
    y=[bp+8]; FAR return, 258 bytes). The panel-size/frame-range
    computation is byte-for-byte the SAME inlined `_FillMap`-shaped
    preamble as `_FillMap` itself (including its own dead
    always-true/always-false redundant bound recheck), just with `x2`/`y2`
    hardcoded to `x+8`/`y+12` rather than taken as arguments — composed
    here directly as `fill_map(dgroup, x, x+8, y, y+12, 0x63)` rather than
    re-derived. Composes `fill_map`, `tile_frame1`, `_stamp_glyph`.
    """
    fill_map(dgroup, x, x + 8, y, y + 12, 0x63)
    tile_frame1(dgroup, x, x + 8, y, y + 12)
    _stamp_glyph(dgroup, 0x2314, MAP_PLANE_BASE[0] + ((x + 2) << 6) + (y + 2), 5, 4)
    _stamp_glyph(dgroup, 0x2314, MAP_PLANE_BASE[0] + ((x + 2) << 6) + (y + 7), 5, 4)
    dgroup.wb(MAP_PLANE_BASE[0] + ((x + 4) << 6) + (y + 6), 0x65)


def make_outlet_h(dgroup, x: int, y: int) -> None:
    """Paint a horizontal double-outlet wall panel anchored at yard map
    `(x, y)`: a `0x63`-filled 13-wide (`x..x+12`) x 9-tall (`y..y+8`)
    background panel (the TRANSPOSED dimensions of `make_outlet_v`'s own
    9x13 panel), a `tile_frame1` border around that SAME rectangle (still
    `_TileFrame1`, NOT `_TileFrame2` — independently confirmed via the raw
    call target, both outlet orientations share the same frame style), two
    `make_plug_h`-shaped glyphs (the SAME `0x2328` table `make_plug_h`
    itself reads) stamped inset at `(x+2, y+2)` and `(x+7, y+2)` — i.e.
    side by side horizontally 5 columns apart, the transposed twin of
    `make_outlet_v`'s vertical stacking — and a single `0x65` "screw" tile
    at `(x+6, y+4)`.

    Recovered from `_MakeOutletH` (SIMANTW.SYM seg5:3D44, args x=[bp+6],
    y=[bp+8]; FAR return, 258 bytes; instruction-for-instruction the same
    shape as `_MakeOutletV` with the x/y roles of every constant swapped).
    Composes `fill_map`, `tile_frame1`, `_stamp_glyph`.
    """
    fill_map(dgroup, x, x + 12, y, y + 8, 0x63)
    tile_frame1(dgroup, x, x + 12, y, y + 8)
    _stamp_glyph(dgroup, 0x2328, MAP_PLANE_BASE[0] + ((x + 2) << 6) + (y + 2), 4, 5)
    _stamp_glyph(dgroup, 0x2328, MAP_PLANE_BASE[0] + ((x + 7) << 6) + (y + 2), 4, 5)
    dgroup.wb(MAP_PLANE_BASE[0] + ((x + 6) << 6) + (y + 4), 0x65)


def make_kitchen_wall(dgroup, pack) -> None:
    """Repaint the ENTIRE yard-map plane (`MAP_PLANE_BASE[0]`, all 128
    columns) as the fixed "kitchen" house-interior scene: rows `0..23`
    become the floor tile `0x62` and rows `24..63` become `0` (this plane
    is shared between the outdoor yard and indoor house-interior views —
    only one is ever active, so repainting the whole array is safe); three
    full-width horizontal wall lines are stamped at `y=0`, `y=8`, `y=16`
    (tile `0x68`); then a grid of "stud" posts at every 8th column
    (`x=0,8,...,120`) for `y=0..23` is stamped `0x66` where the
    underlying tile is STILL the floor tile `0x62`, or `0x67` where it
    isn't (i.e. where it's one of the three horizontal wall-line rows
    just painted — genuinely conditioned on the CURRENT map byte, not on
    which `y` it is, independently re-derived from the raw `cmp
    ds:[si],0x62` rather than assumed); the bottom border row `y=23` gets
    the same floor-vs-not test, stamping `0x68` (still floor) or `0x69`
    (a post) across the full width; finally two `make_outlet_v` panels are
    painted at `(0x24, 2)` and `(0x54, 2)`, and `pack[0x9C66]` (the SAME
    "current fall-direction table index" word `food_fall` reads) is set
    to `2`.

    Recovered from `_MakeKitchenWall` (SIMANTW.SYM seg5:3698, NO args;
    FAR return, 196 bytes). The two clear passes are byte-for-byte the
    SAME `rep stosw`-driven whole-plane fill `_FillMap` performs (just
    inlined with `x1=0, x2=127` and `value` hardcoded per row-band) —
    composed here as `fill_map(dgroup, 0, 127, 0, 23, 0x62)` /
    `fill_map(dgroup, 0, 127, 24, 63, 0)` rather than re-derived. The
    `pack[0x9C66]` write goes through DGROUP pointer-global `0xC434`
    (independently resolved fresh against `runtime.create_machine()`'s
    own `seg_bases` — it lands on the PACK selector, a NEW pointer-global
    this session, distinct from `create_new_hole`'s own `0xC3FE`/`0xC400`
    pair which resolve to SIMANT_DATA_GROUP instead). Composes `fill_map`,
    `make_outlet_v`.
    """
    fill_map(dgroup, 0, 127, 0, 23, 0x62)
    fill_map(dgroup, 0, 127, 24, 63, 0x00)

    for y in (0, 8, 16):
        for x in range(128):
            dgroup.wb(MAP_PLANE_BASE[0] + (x << 6) + y, 0x68)

    for x in range(0, 128, 8):
        for y in range(24):
            off = MAP_PLANE_BASE[0] + (x << 6) + y
            dgroup.wb(off, 0x66 if dgroup.rb(off) == 0x62 else 0x67)

    for x in range(128):
        off = MAP_PLANE_BASE[0] + (x << 6) + 23
        dgroup.wb(off, 0x68 if dgroup.rb(off) == 0x62 else 0x69)

    make_outlet_v(dgroup, 0x24, 2)
    make_outlet_v(dgroup, 0x54, 2)
    pack.ww(0x9C66, 2)
