"""State-diff oracle — the verification tier for *mutating* sim routines.

The predicate/accessor tier is proven by return value (test_hooks.py).  A routine
that mutates world state needs a different oracle: run the ORIGINAL ASM (with its
screen-redraw / sound side calls stubbed, so only sim state changes) over the real
DGROUP, then apply the recovered Python mutator to a COPY of that same DGROUP and
require the two resulting images to be byte-identical.  Because both start from the
identical pre-state, only the mutation itself has to match.

This is the harness the seg6 behavior layer (`_DoForageAnt`, ...) will use; it is
bootstrapped here on leaf mutators: `_SetMap` (one map-cell write), `_DropWater`
(RNG-threaded), and `_SetMyHealth` (spans three FIXED NE data segments — DGROUP,
SIMANT_DATA_GROUP, PACK — via `hooks.SIMANT_DATA_GROUP_SEG_INDEX` / `PACK_SEG_INDEX`;
`_run_and_diff_segs` generalizes the single-DGROUP harness to N named segment
windows for this case).

IMPORTANT (see cont.82 in the journal): `runtime.create_machine()` returns an
INDEPENDENT memory image every call.  `_run_and_diff_segs`/`_run_and_get_ax` each
create their OWN internal machine — writing to a machine created outside those
functions has NO effect on the one the ASM actually runs on.  Seed via the
`seed`/`seed_fn` parameters (which run against the function's own internal
machine), never via a separately-constructed `m`.
"""
from __future__ import annotations

import pytest

import simant.hooks as hooks
from simant import runtime
from simant.bridge.dgroup_view import ByteBackend
from simant.recovered.crt_math import RAND_STATE_OFF

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="SimAnt assets not present")

SENT_CS, SENT_IP = 0xDEAD, 0xBEEF
DGROUP_SIZE = 0x10000
# For a small-model app ss == ds == DGROUP, so the call's stack frame lives at
# the TOP of DGROUP.  The sim state these routines mutate is all well below this
# (the highest field seen is ~0xC4A0), so diff only [0, SIM_HI) and leave the
# stack band — execution scaffolding, not sim state — out of the comparison.
SIM_HI = 0xF000

_SDG = hooks.SIMANT_DATA_GROUP_SEG_INDEX
_PACK = hooks.PACK_SEG_INDEX


def _far_return_stub(cpu):
    """Replacement for a stubbed side call (screen redraw / sound): a plain far
    return that leaves the args for the cdecl caller to clean."""
    s = cpu.s
    ret_ip = cpu.mem.rw(s.ss, s.sp)
    ret_cs = cpu.mem.rw(s.ss, (s.sp + 2) & 0xFFFF)
    s.sp = (s.sp + 4) & 0xFFFF
    s.cs, s.ip = ret_cs, ret_ip


def _run_and_diff(seg, off, args, apply_recovered, *, seed=None, seed_fn=None,
                  stubs=()):
    """Run ASM `seg:off`(args) over the real DGROUP with `stubs` far routines
    neutralized, then apply `apply_recovered(view)` to a copy of the same DGROUP.
    Returns (asm_after, recovered_after) as bytes of the sim region.

    `seed` is {dgroup_offset: word} written before the run (e.g. pointing the
    world-state selector globals at DGROUP itself, and setting read-only inputs);
    `seed_fn(mem, dg)` seeds arbitrary bytes/regions (map cells, the RNG seed).
    """
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    s = m.cpu.s
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = dg
    for o, v in (seed or {}).items():
        m.mem.ww(dg, o, v & 0xFFFF)
    if seed_fn is not None:
        seed_fn(m.mem, dg)

    before = bytes(m.mem.block(dg, 0, DGROUP_SIZE))     # pre-state (real data + seed)

    for stub_seg, stub_off in stubs:
        m.cpu.replacement_hooks[(m.seg_bases[stub_seg], stub_off)] = _far_return_stub

    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[seg], off
    sp = s.sp
    for v in (*reversed(args), SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    for _ in range(200_000):                             # loop mutators need headroom
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
            break
    else:
        raise AssertionError(f"ASM {seg}:{off:#06x} did not return")
    asm_after = bytes(m.mem.block(dg, 0, DGROUP_SIZE))

    rec = bytearray(before)
    apply_recovered(ByteBackend(rec, 0))
    return asm_after[:SIM_HI], bytes(rec[:SIM_HI])


def _run_and_diff_segs(seg, off, args, apply_recovered, regions, *, seed=None,
                       near=False, seed_fn=None, stubs=()):
    """Like `_run_and_diff`, but for a routine that mutates through MULTIPLE
    fixed NE data segments (e.g. via a DGROUP pointer-global into
    SIMANT_DATA_GROUP/PACK — see `hooks.SIMANT_DATA_GROUP_SEG_INDEX`/
    `PACK_SEG_INDEX`).  `regions` is [(seg_index, lo, hi), ...]: the byte window
    of each touched segment to snapshot/diff.  `apply_recovered` receives one
    `ByteBackend` per region, positional, in `regions` order, each addressed by
    the SAME real offsets the ASM uses (so `view.rw(0x8A5E)` reads the byte at
    that real offset within its segment, not window-relative index 0).

    `near=True` for a routine that returns via a NEAR `ret` (pops only IP, CS
    unchanged) rather than a far `retf` — common for calls between routines in
    the SAME NE segment.  A near-return routine only needs SENT_IP pushed (no
    return CS slot), and the completion check is IP-only (CS never changes).

    `seed_fn(m)`, if given, runs on THIS function's own internal machine
    before the pre-state snapshot — the only correct way to seed PACK/
    SIMANT_DATA_GROUP/etc. beyond the `seed` dict (DGROUP words only).

    `stubs`, like `_run_and_diff`'s, neutralizes far side calls (screen
    redraw / sound / UI) with a plain far return so only sim state changes.

    Returns a list of (label, asm_window, recovered_window) for each region.
    """
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    s = m.cpu.s
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = dg
    for o, v in (seed or {}).items():
        m.mem.ww(dg, o, v & 0xFFFF)
    if seed_fn is not None:
        seed_fn(m)
    for stub_seg, stub_off in stubs:
        m.cpu.replacement_hooks[(m.seg_bases[stub_seg], stub_off)] = _far_return_stub

    befores = [bytes(m.mem.block(m.seg_bases[si], lo, hi - lo))
              for si, lo, hi in regions]

    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[seg], off
    sp = s.sp
    tail = (SENT_IP,) if near else (SENT_CS, SENT_IP)
    for v in (*reversed(args), *tail):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    target = (s.cs & 0xFFFF, SENT_IP) if near else (SENT_CS, SENT_IP)
    for _ in range(200_000):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == target:
            break
    else:
        raise AssertionError(f"ASM {seg}:{off:#06x} did not return")
    asm_afters = [bytes(m.mem.block(m.seg_bases[si], lo, hi - lo))
                  for si, lo, hi in regions]

    recs = [bytearray(b) for b in befores]
    views = [ByteBackend(rec, -lo) for rec, (_si, lo, _hi) in zip(recs, regions)]
    apply_recovered(*views)

    return [(f"seg{si}[{lo:#06x}:{hi:#06x}]", asm_after, bytes(rec))
            for (si, lo, hi), asm_after, rec in zip(regions, asm_afters, recs)]


def _run_and_get_ax(seg, off, args, *, seed_fn=None, near=False):
    """Pure-read-predicate variant: seed a fresh machine (via `seed_fn(m)`), run
    the ASM to return, and return (ax, m) so the caller can feed the SAME
    machine's post-seed memory to the recovered function for comparison."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if seed_fn is not None:
        seed_fn(m)
    s = m.cpu.s
    s.ds = m.seg_bases[hooks.DG_SEG_INDEX]
    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[seg], off
    sp = s.sp
    tail = (SENT_IP,) if near else (SENT_CS, SENT_IP)
    for v in (*reversed(args), *tail):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    target = (s.cs & 0xFFFF, SENT_IP) if near else (SENT_CS, SENT_IP)
    for _ in range(200_000):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == target:
            break
    else:
        raise AssertionError(f"ASM {seg}:{off:#06x} did not return")
    return s.ax, m


# ---- _SetMap (seg5:617A) — one map-cell write ------------------------------
# _SetMap's screen redraw (_ZapEuMapAt, seg3:0) is stubbed; only the map byte
# changes.
_ZAP_STUB = [(3, 0x0000)]


@pytest.mark.parametrize("plane,x,y,value", [
    (0, 0x00, 0x00, 0x42), (1, 0x7F, 0x3F, 0x99), (1, 0x40, 0x20, 0x01),  # yard
    (2, 0x00, 0x00, 0x55), (2, 0x3F, 0x3F, 0xAA), (3, 0x10, 0x20, 0x7E),  # nest
    (0, 0x80, 0x00, 0x11),   # x out of range -> no write
    (2, 0x40, 0x00, 0x22),   # x out of range on the nest plane -> no write
    (4, 0x10, 0x10, 0x33),   # plane out of range -> no write
    (0, 0x10, 0x10, 0x180),  # value written as a byte (low 8 bits)
])
def test_setmap_state_diff_matches_asm(plane, x, y, value):
    from simant.recovered.gameplay import set_map
    asm_after, rec_after = _run_and_diff(
        5, 0x617A, (plane, x, y, value),
        lambda v: set_map(v, plane, x, y, value), stubs=_ZAP_STUB)
    assert asm_after == rec_after, _first_diff(asm_after, rec_after)


# ---- _SetMyHealth (seg5:8C70) — spans 3 FIXED NE data segments -------------
# _SetMyHealth writes DGROUP:[0xAC8A] directly, and (through DGROUP pointer-
# globals that are load-time-fixed, never reassigned — see hooks.py) reads the
# god-mode flag from SIMANT_DATA_GROUP:[0x8A5E] and writes PACK:[0x9CF0]/
# [0x9AF2] (+ reads PACK:[0x9BEC]).  These are REAL, permanent segments — not
# seeded pointers — so the test uses their actual NE segment indices, only
# seeding the god-mode flag and the current-health input within them.
_HEALTH_REGIONS = [
    (hooks.DG_SEG_INDEX, 0xAC00, 0xAD00),   # covers [0xAC8A]
    (_SDG, 0x8A00, 0x8B00),                 # covers [0x8A5E] (god-mode flag)
    (_PACK, 0x9A00, 0x9D00),                # covers [0x9AF2],[0x9BEC],[0x9CF0]
]


@pytest.mark.parametrize("god", [False, True])
@pytest.mark.parametrize("new_health,current", [
    (50, 20), (100, 100), (0, 40), (120, 10), (5, 80), (9, 0), (10, 5),
    (30, 30), (0xFFF6, 40),          # negative-as-word -> clamps to 0
])
def test_setmyhealth_state_diff_matches_asm(new_health, current, god):
    from simant.recovered.gameplay import set_my_health

    def seed(m):
        m.mem.wb(m.seg_bases[_SDG], 0x8A5E, 1 if god else 0)
        m.mem.ww(m.seg_bases[_PACK], 0x9BEC, current & 0xFFFF)

    results = _run_and_diff_segs(
        5, 0x8C70, (new_health,),
        lambda dg, sdg, pack: set_my_health(dg, sdg, pack, _sx(new_health)),
        _HEALTH_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _HEALTH_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _DropWater (seg5:0C54) — RNG-threaded map column update ---------------
# Proves the oracle threads RNG state: _DropWater calls the real _SRand1 (which
# advances the DGROUP seed at 0xCBF2), and the recovered drop_water must advance
# the recovered LFSR identically.  Only the redraw is stubbed; _SRand1 runs.
_NEST2, _NEST3 = 0x48E8, 0x58E8
_SEED_OFF = 0xCBF2


def _water_seed(x, seed_val):
    """Seed the column at Y=x on planes 2/3 with a mix that includes water
    sources (0x4E, which fire the RNG) and other tiles, plus the LFSR seed."""
    def seed(mem, dg):
        mem.ww(dg, _SEED_OFF, seed_val)
        for si in range(0x40):
            # every 5th row is a water source; others cycle through tile values
            t2 = 0x4E if si % 5 == 0 else (0x30 + (si % 0x20))
            t3 = 0x4E if si % 7 == 0 else (0x10 + (si % 0x30))
            mem.wb(dg, _NEST2 + (si << 6) + x, t2)
            mem.wb(dg, _NEST3 + (si << 6) + x, t3)
    return seed


@pytest.mark.parametrize("x,seed_val", [
    (0x10, 0x1234), (0x00, 0xABCD), (0x3F, 0x0001), (0x20, 0x8000),
    (0x05, 0xFFFF),
])
def test_dropwater_state_diff_matches_asm(x, seed_val):
    from simant.recovered.gameplay import drop_water
    asm_after, rec_after = _run_and_diff(
        5, 0x0C54, (x,), lambda v: drop_water(v, x),
        seed_fn=_water_seed(x, seed_val), stubs=_ZAP_STUB)
    assert asm_after == rec_after, _first_diff(asm_after, rec_after)


# ---- _DeadAntHere (seg6:28C0) — 100-slot corpse ring buffer ----------------
# One combined DGROUP window spans the yard map plane through the SRand seed
# (0x28E8..0xCBF4) -- _run_and_diff_segs takes one window per SEGMENT, so the
# map/life/seed reads (all DGROUP) share a single wide region, not three.
_DEADANT_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_PACK, 0x9B00, 0x9F00),  # covers 0x9B6E/0x9C82+/0x9D76+/0x9EA8
]


def _deadant_seed(counter, old_x, old_y, old_tile, inside, seed_val):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(pack, 0x9EA8, counter)
        next_slot = (counter + 1) % 0x64
        m.mem.wb(pack, 0x9C82 + next_slot, old_x)
        m.mem.ww(pack, 0x9D76 + next_slot, old_y)     # word slot, high byte poison-free
        m.mem.wb(dg, 0x28E8 + (old_x << 6) + old_y, old_tile)
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        m.mem.ww(dg, 0xCBF2, seed_val)
    return seed


@pytest.mark.parametrize("counter,old_x,old_y,old_tile,inside,new_x,new_y,mode,seed_val", [
    # outside, evicted tile in the fade band -> _SRand16 replace; new pos clear -> plant
    (5, 10, 10, 0x14, False, 20, 20, 0, 0x1234),
    (5, 10, 10, 0x14, False, 20, 20, 1, 0xABCD),
    # outside, evicted tile OUTSIDE the fade band -> no replace at old pos
    (5, 10, 10, 0x20, False, 20, 20, 0, 0x1234),
    # outside, new-position tile already >= 0x18 -> no plant
    (5, 10, 10, 0x14, False, 20, 20, 0, 0x5678),
    # inside, evicted tile in its fade band -> (tile-8)>>2 replace (no RNG call)
    (5, 10, 10, 0x12, True, 20, 20, 0, 0x1234),
    (5, 10, 10, 0x12, True, 20, 20, 1, 0x1234),
    # inside, new-position tile below threshold -> plant via _SRand1(2)
    (5, 10, 10, 0x00, True, 20, 20, 0, 0x9999),
    (5, 10, 10, 0x00, True, 20, 20, 1, 0x9999),
    # inside, new-position tile at/above threshold -> no plant
    (5, 10, 10, 0x00, True, 20, 20, 0, 0x2222),
    # counter at the wrap boundary (99 -> wraps to 0)
    (99, 5, 5, 0x14, False, 30, 30, 0, 0x4321),
    # ring-buffer slot == the new position (self-overlap): the "old" read and
    # the ring-buffer overwrite touch the SAME cell
    (5, 20, 20, 0x14, False, 20, 20, 0, 0x1234),
])
def test_deadanthere_state_diff_matches_asm(counter, old_x, old_y, old_tile, inside,
                                            new_x, new_y, mode, seed_val):
    from simant.recovered.gameplay import dead_ant_here
    results = _run_and_diff_segs(
        6, 0x28C0, (new_x, new_y, mode),
        lambda dg, p: dead_ant_here(dg, p, new_x, new_y, mode),
        _DEADANT_REGIONS,
        seed_fn=_deadant_seed(counter, old_x, old_y, old_tile, inside, seed_val))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DEADANT_REGIONS):
        assert asm_after == rec_after, (
            f"counter={counter} old=({old_x},{old_y},{old_tile:#x}) in={inside} "
            f"new=({new_x},{new_y}) mode={mode} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _FixExitMapB / _FixExitMapR (seg5:284E / 2914) — exit-distance map ----
@pytest.mark.parametrize("routine,off,map_base,exit_base,fn_name", [
    ("_FixExitMapB", 0x284E, 0x48E8, 0x03A4, "fix_exit_map_b"),
    ("_FixExitMapR", 0x2914, 0x58E8, 0x13A4, "fix_exit_map_r"),
])
@pytest.mark.parametrize("x,y,tile,neighbors", [
    (10, 0, 0x18, {}),        # row 0, exit tile -> 0xFF
    (10, 1, 0x20, {}),        # row 1, non-exit tile -> 0xFE
    (10, 5, 0x00, {}),        # row>=2, all neighbours 0 -> writes 0
    (10, 5, 0x00, {2: 5, 5: 9, 0: 1}),   # row>=2, picks the max (9) -> writes 8
    (0, 5, 0x00, {5: 3}),       # x=0, some neighbours off-grid (x-1<0) -> skipped
    (0x3F, 5, 0x00, {1: 4}),    # x=0x3F, some neighbours off-grid (x+1>0x3F)
    (10, 0x3F, 0x00, {3: 6}),   # y=0x3F, some neighbours off-grid (y+1>0x3F)
])
def test_fixexitmap_state_diff_matches_asm(routine, off, map_base, exit_base,
                                           fn_name, x, y, tile, neighbors):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)
    GDX, GDY = G.GET_BEST_DIR_DX, G.GET_BEST_DIR_DY

    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        m.mem.wb(dg, map_base + (x << 6) + y, tile)
        for si, val in neighbors.items():
            nx, ny = x + GDX[si], y + GDY[si]
            if 0 <= nx <= 0x3F and 0 <= ny <= 0x3F:
                m.mem.wb(sdg, exit_base + (nx << 6) + ny, val)

    regions = [(hooks.DG_SEG_INDEX, map_base, map_base + 0x1000),
              (_SDG, 0, exit_base + 0x1000)]   # SDG[0..16) holds the delta tables too
    results = _run_and_diff_segs(5, off, (x, y), lambda d, s: fn(d, s, x, y),
                                 regions, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, regions):
        assert asm_after == rec_after, (
            f"{routine} x={x} y={y} tile={tile:#x} neighbors={neighbors} "
            f"{label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _SmoothEdgesB / _SmoothEdgesR (seg5:255A / 26E4) — dig-edge autotile --
_NEIGHBOR_DELTA = {"north": -1, "east": 0x40, "south": 1, "west": -0x40}


@pytest.mark.parametrize("routine,off,map_base,fn_name", [
    ("_SmoothEdgesB", 0x255A, 0x48E8, "smooth_edges_b"),
    ("_SmoothEdgesR", 0x26E4, 0x58E8, "smooth_edges_r"),
])
@pytest.mark.parametrize("x,y,tile,dirt_neighbors,seed_val", [
    (10, 0, 0x20, (), 0x1234),      # row 0, tile<0x30 -> forced to 0x18
    (10, 0, 0x35, (), 0x1234),      # row 0, tile>=0x30 -> no-op
    (10, 5, 0x10, (), 0x1234),      # tile<0x20 -> no-op
    (10, 5, 0x38, (), 0x1234),      # tile in the excluded 0x30..0x4E band -> no-op
    (10, 5, 0x25, (), 0x1234),      # centre 0x20-0x2F, all neighbours clear -> SRand8 reroll
    (10, 5, 0x25, (), 0xABCD),      # same, different LFSR seed
    (10, 5, 0x55, (), 0x1234),      # centre >=0x4F, all neighbours clear -> literal 0x4E
    (10, 5, 0x25, ("north",), 0x1234),          # one neighbour dirt -> bits=1
    (10, 5, 0x25, ("north", "east", "south", "west"), 0x1234),   # all 4 -> bits=15
    (10, 5, 0x55, ("east", "west"), 0x1234),    # centre high band, some neighbours dirt
    (0, 5, 0x25, (), 0x1234),        # x=0 -> west defaults to dirt
    (0x3F, 5, 0x25, (), 0x1234),     # x=0x3F -> east defaults to dirt
    (10, 1, 0x25, (), 0x1234),       # y=1 (<2) -> north defaults to dirt
    (10, 0x3F, 0x25, (), 0x1234),    # y=0x3F -> south defaults to dirt
    (-1, 5, 0x25, (), 0x1234),       # x out of range -> no-op
    (10, 0x40, 0x25, (), 0x1234),    # y out of range -> no-op
])
def test_smoothedges_state_diff_matches_asm(routine, off, map_base, fn_name, x, y,
                                            tile, dirt_neighbors, seed_val):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)

    def seed(mem, dg):
        if 0 <= x <= 0x3F and 0 <= y <= 0x3F:
            mem.wb(dg, map_base + (x << 6) + y, tile)
            for name, delta in _NEIGHBOR_DELTA.items():
                nv = 0x25 if name in dirt_neighbors else 0x05
                mem.wb(dg, map_base + (x << 6) + y + delta, nv)
        mem.ww(dg, 0xCBF2, seed_val)

    asm_after, rec_after = _run_and_diff(5, off, (x, y), lambda v: fn(v, x, y),
                                         seed_fn=seed)
    assert asm_after == rec_after, (
        f"{routine} x={x} y={y} tile={tile:#x} dirt={dirt_neighbors}: "
        f"{_first_diff(asm_after, rec_after)}")


# ---- _DigTileB (seg5:1FE4) — dig one nest tile, occasionally into red too --
_DIGTILEB_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # nest map planes 2+3 through the SRand seed
    (_SDG, 0, 0x23A4 + 0x1000),             # delta tables + both exit-map arrays
    (_PACK, 0x7200, 0xA000),                # both colonies' dig accumulator fields
]


def _digtileb_seed(x, y, tile, rtile, seed_val, count, rcount):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        idx = (x << 6) + y
        m.mem.wb(dg, 0x48E8 + idx, tile)
        m.mem.wb(dg, 0x58E8 + idx, rtile)
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x72C8, count)
        m.mem.ww(pack, 0x7A56, rcount)
        m.mem.ww(pack, 0x8104, 100)
        m.mem.ww(pack, 0x8106, 0)
        m.mem.ww(pack, 0x811A, 200)
        m.mem.ww(pack, 0x811C, 0)
        m.mem.ww(pack, 0x9DDC, 50)
        m.mem.ww(pack, 0x9DDE, 0)
        m.mem.ww(pack, 0x9DE2, 75)
        m.mem.ww(pack, 0x9DE4, 0)
    return seed


@pytest.mark.parametrize("x,y,tile,rtile,seed_val,count,rcount", [
    (20, 20, 0x40, 0x40, 0x1234, 3, 2),        # not dirt, y<=0x35 -> smoothing tail only
    (20, 20, 0x25, 0x40, 0x1234, 3, 2),        # dirt, y<=0x35 -> reroll + running average
    (20, 0, 0x25, 0x40, 0x1234, 0, 0),         # dirt, count starts at 0 -> becomes 1
    (20, 0x36, 0x25, 0x40, 0x0001, 3, 2),      # dirt, y>0x35, SRand1(64) rolls nonzero
    (20, 0x36, 0x25, 0x40, 0x0000, 3, 2),      # dirt, y>0x35, SRand1(64) rolls 0, red not dirt
    (20, 0x36, 0x25, 0x25, 0x0000, 3, 2),      # same, red tile also dirt -> red stats too
    (0, 0x36, 0x25, 0x25, 0x0000, 3, 2),       # x=0 boundary (west neighbour off-grid)
    (0x3F, 0x36, 0x25, 0x25, 0x0000, 3, 2),    # x=0x3F boundary (east neighbour off-grid)
])
def test_digtileb_state_diff_matches_asm(x, y, tile, rtile, seed_val, count, rcount):
    from simant.recovered.gameplay import dig_tile_b
    results = _run_and_diff_segs(
        5, 0x1FE4, (x, y),
        lambda d, s, p: dig_tile_b(d, s, p, x, y),
        _DIGTILEB_REGIONS,
        seed_fn=_digtileb_seed(x, y, tile, rtile, seed_val, count, rcount))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DIGTILEB_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} tile={tile:#x} rtile={rtile:#x} seed={seed_val:#x} "
            f"count={count} rcount={rcount} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _DigTileR (seg5:21DE) — dig one red-colony nest tile, no tunnelling --
_DIGTILER_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x58E8, 0xCBF4),   # red nest map plane through the SRand seed
    (_SDG, 0, 0x23A4 + 0x1000),              # delta tables + red exit-map array
    (_PACK, 0x7A00, 0xA000),   # count [0x7A56], sums/averages [0x9DDC..0x9FD4)
]


def _digtiler_seed(x, y, tile, seed_val, count):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        idx = (x << 6) + y
        m.mem.wb(dg, 0x58E8 + idx, tile)
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x7A56, count)
        m.mem.ww(pack, 0x9DDC, 50)
        m.mem.ww(pack, 0x9DDE, 0)
        m.mem.ww(pack, 0x9DE2, 75)
        m.mem.ww(pack, 0x9DE4, 0)
    return seed


@pytest.mark.parametrize("x,y,tile,seed_val,count", [
    (20, 20, 0x40, 0x1234, 3),      # not dirt -> smoothing tail only
    (20, 20, 0x25, 0x1234, 3),      # dirt -> reroll + running average
    (20, 0, 0x25, 0x1234, 0),       # count starts at 0 -> becomes 1
    (0, 20, 0x25, 0x1234, 3),       # x=0 boundary (west neighbour off-grid)
    (0x3F, 20, 0x25, 0x1234, 3),    # x=0x3F boundary (east neighbour off-grid)
])
def test_digtiler_state_diff_matches_asm(x, y, tile, seed_val, count):
    from simant.recovered.gameplay import dig_tile_r
    results = _run_and_diff_segs(
        5, 0x21DE, (x, y),
        lambda d, s, p: dig_tile_r(d, s, p, x, y),
        _DIGTILER_REGIONS, seed_fn=_digtiler_seed(x, y, tile, seed_val, count))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DIGTILER_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} tile={tile:#x} seed={seed_val:#x} count={count} "
            f"{label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _MakeBlkQueen/_MakeRedQueen (seg7:671A/6906) — carve a founding queen -
# chamber.  Regions merge dig_tile_b/r's own footprint with add_ant_to_b/r_
# list's (both real segments touched by each — SDG and PACK regions from the
# two composed routines are UNIONed into one window per segment, never split).
_MAKEBLKQUEEN_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0, 0x4200),
    (_PACK, 0x7200, 0xA000),
]
_MAKEREDQUEEN_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0, 0x4C00),
    (_PACK, 0x7200, 0xA000),
]


def _makequeen_seed(seed_val, count_list, count_field):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCBF2, seed_val)
        for off in (0x72C8, 0x8104, 0x8106, 0x811A, 0x811C,
                    0x7A56, 0x9DDC, 0x9DDE, 0x9DE2, 0x9DE4):
            m.mem.ww(pack, off, 0)
        m.mem.ww(pack, count_field, count_list)
    return seed


@pytest.mark.parametrize("x,y,direction,seed_val,count_b", [
    (20, 20, 3, 0x1234, 0),
    (20, 20, 0, 0x1234, 5),
    (60, 30, 5, 0x0001, 0x1F3),   # near the B-list 500-slot cap
])
def test_makeblkqueen_state_diff_matches_asm(x, y, direction, seed_val, count_b):
    from simant.recovered.gameplay import make_blk_queen
    results = _run_and_diff_segs(
        7, 0x671A, (x, y, direction),
        lambda d, s, p: make_blk_queen(d, s, p, x, y, direction),
        _MAKEBLKQUEEN_REGIONS,
        seed_fn=_makequeen_seed(seed_val, count_b, 0x99D4))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKEBLKQUEEN_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} dir={direction} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


@pytest.mark.parametrize("x,y,direction,seed_val,count_r", [
    (20, 20, 3, 0x1234, 0),
    (20, 20, 0, 0x1234, 5),
    (60, 30, 5, 0x0001, 0x1F3),   # near the R-list 500-slot cap
])
def test_makeredqueen_state_diff_matches_asm(x, y, direction, seed_val, count_r):
    from simant.recovered.gameplay import make_red_queen
    results = _run_and_diff_segs(
        7, 0x6906, (x, y, direction),
        lambda d, s, p: make_red_queen(d, s, p, x, y, direction),
        _MAKEREDQUEEN_REGIONS,
        seed_fn=_makequeen_seed(seed_val, count_r, 0x72CC))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKEREDQUEEN_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} dir={direction} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _PlaceRedQueen (seg7:67DA) — dig a tunnel + found a red queen, NO args
_PLACEREDQUEEN_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x58E8, 0xCBF4),
    (_SDG, 0, 0x8400),   # dig tables + R-list fields + the [0x8366]/[0x8368] scratch
    (_PACK, 0x7200, 0xA000),
]


def _placeredqueen_seed(seed_val, count_r):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCBF2, seed_val)
        for off in (0x7A56, 0x9DDC, 0x9DDE, 0x9DE2, 0x9DE4):
            m.mem.ww(pack, off, 0)
        m.mem.ww(pack, 0x72CC, count_r)
        m.mem.ww(pack, 0x79DC, 3)
    return seed


@pytest.mark.parametrize("seed_val,count_r", [
    (0x1234, 0),
    (0x0001, 5),          # different SRand4/SRand1 rolls -> different wander/count
    (0xBEEF, 0x1F3),       # near the R-list 500-slot cap
])
def test_placeredqueen_state_diff_matches_asm(seed_val, count_r):
    from simant.recovered.gameplay import place_red_queen
    results = _run_and_diff_segs(
        7, 0x67DA, (),
        lambda d, s, p: place_red_queen(d, s, p),
        _PLACEREDQUEEN_REGIONS, seed_fn=_placeredqueen_seed(seed_val, count_r))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _PLACEREDQUEEN_REGIONS):
        assert asm_after == rec_after, (
            f"seed={seed_val:#x} count_r={count_r} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _PlaceBlackQueen (seg7:65CE) — dig a tunnel + found a black queen ----
# NO args.  Same shape as _PlaceRedQueen but a genuinely different wander
# mechanism (SRand2-gated drift) and 2 extra PACK writes.
_PLACEBLACKQUEEN_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x48E8, 0xCBF4),
    (_SDG, 0, 0x8400),   # dig tables + B-list fields + the [0x8362]/[0x8364] scratch
    (_PACK, 0x7200, 0xA000),
]


def _placeblackqueen_seed(seed_val, count_b):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCBF2, seed_val)
        for off in (0x72C8, 0x8104, 0x8106, 0x811A, 0x811C,
                    0x9DDC, 0x9DDE, 0x9DE2, 0x9DE4):
            m.mem.ww(pack, off, 0)
        m.mem.ww(pack, 0x99D4, count_b)
        m.mem.ww(pack, 0x78E8, 3)
    return seed


@pytest.mark.parametrize("seed_val,count_b", [
    (0x1234, 0),
    (0x0001, 5),          # different SRand4/SRand2/SRand1 rolls -> different wander/count
    (0xBEEF, 0x1F3),       # near the B-list 500-slot cap
])
def test_placeblackqueen_state_diff_matches_asm(seed_val, count_b):
    from simant.recovered.gameplay import place_black_queen
    results = _run_and_diff_segs(
        7, 0x65CE, (),
        lambda d, s, p: place_black_queen(d, s, p),
        _PLACEBLACKQUEEN_REGIONS, seed_fn=_placeblackqueen_seed(seed_val, count_b))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _PLACEBLACKQUEEN_REGIONS):
        assert asm_after == rec_after, (
            f"seed={seed_val:#x} count_b={count_b} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _AddBlackAnts/_AddRedAnts (seg7:6C5A/6CFE) — scenario-init yard ants --
_ADDANTS_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # yard map + yard life plane + SRand seed
    (_SDG, 0x2300, 0x3800),                 # covers 0x23A4/278E/2B78/2F62/334C+slot
    (_PACK, 0x80E0, 0x8100),                # covers [0x80F0] (A-list count)
]


def _addants_seed(seed_val, alist_count, x_lo, x_hi):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        for x in range(x_lo, x_hi):
            for y in range(0x10, 0x30):
                off = (x << 6) + y
                m.mem.wb(dg, 0x28E8 + off, 0)   # map: walkable everywhere in-band
                m.mem.wb(dg, 0x68E8 + off, 0)   # life: unoccupied everywhere in-band
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x80F0, alist_count)
    return seed


@pytest.mark.parametrize("count,seed_val,alist_count", [
    (1, 0x1234, 0),
    (5, 0x0001, 0),
    (5, 0xBEEF, 0x3E7),   # near the global 0x3E8 A-list cap -> exits after 1 ant
])
def test_addblackants_state_diff_matches_asm(count, seed_val, alist_count):
    from simant.recovered.gameplay import add_black_ants
    results = _run_and_diff_segs(
        7, 0x6C5A, (count,),
        lambda d, s, p: add_black_ants(d, s, p, count),
        _ADDANTS_REGIONS, seed_fn=_addants_seed(seed_val, alist_count, 0, 0x40))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _ADDANTS_REGIONS):
        assert asm_after == rec_after, (
            f"count={count} seed={seed_val:#x} alist={alist_count:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


@pytest.mark.parametrize("count,seed_val,alist_count", [
    (1, 0x1234, 0),
    (5, 0x0001, 0),
    (5, 0xBEEF, 0x3E7),
])
def test_addredants_state_diff_matches_asm(count, seed_val, alist_count):
    from simant.recovered.gameplay import add_red_ants
    results = _run_and_diff_segs(
        7, 0x6CFE, (count,),
        lambda d, s, p: add_red_ants(d, s, p, count),
        _ADDANTS_REGIONS, seed_fn=_addants_seed(seed_val, alist_count, 0x40, 0x80))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _ADDANTS_REGIONS):
        assert asm_after == rec_after, (
            f"count={count} seed={seed_val:#x} alist={alist_count:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _UnRecruitRed/_RecruitRed (seg7:08DA/0866) — A-list "recruited" flag -
_RECRUITRED_REGIONS = [
    (_PACK, 0x80E0, 0x8100),   # covers [0x80F0] (A-list count)
    (_SDG, 0x2300, 0x3800),    # covers 0x2B78/2F62/334C+slot
]


def _recruitred_seed(slots):
    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, 0x80F0, len(slots))
        for slot, (caste, field_c) in enumerate(slots):
            m.mem.wb(sdg, 0x2F62 + slot, caste)
            m.mem.wb(sdg, 0x2B78 + slot, field_c)
            m.mem.wb(sdg, 0x334C + slot, 0xAA)   # sentinel to verify clearing
    return seed


@pytest.mark.parametrize("slots", [
    [(0x82, 6), (0x86, 6), (0x03, 6)],   # 2 red-recruited (cleared), 1 black (untouched)
    [(0x82, 0), (0x00, 6)],              # red not-recruited (no-op), empty slot (no-op)
    [],                                    # empty list
])
def test_unrecruitred_state_diff_matches_asm(slots):
    from simant.recovered.gameplay import un_recruit_red
    results = _run_and_diff_segs(
        7, 0x8DA, (),
        lambda p, s: un_recruit_red(p, s),
        _RECRUITRED_REGIONS, seed_fn=_recruitred_seed(slots))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _RECRUITRED_REGIONS):
        assert asm_after == rec_after, f"slots={slots} {label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("count,slots", [
    # mode 2 (caste&0x78>>3==2 -> caste bits 0x10) red ant, recruitable -> 1 recruit
    (1, [(0x90, 0)]),
    # mode 6 (caste bits 0x30) red ant, recruitable -> 1 recruit
    (1, [(0xB0, 0)]),
    # already field_c==0x13 -> skipped, second slot (mode 2) recruited instead
    (1, [(0x90, 0x13), (0x90, 0)]),
    # already field_c==6 -> skipped
    (1, [(0x90, 6)]),
    # mode not 2/6 -> skipped entirely
    (1, [(0x88, 0)]),
    # black ant (caste<=0x7F) -> skipped
    (1, [(0x10, 0)]),
    # count exhausted after 1 -> second eligible slot untouched
    (1, [(0x90, 0), (0x90, 0)]),
    # count=0 -> pure no-op even though eligible slots exist
    (0, [(0x90, 0)]),
    # empty list
    (1, []),
])
def test_recruitred_state_diff_matches_asm(count, slots):
    from simant.recovered.gameplay import recruit_red
    results = _run_and_diff_segs(
        7, 0x866, (count,),
        lambda p, s: recruit_red(p, s, count),
        _RECRUITRED_REGIONS, seed_fn=_recruitred_seed(slots))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _RECRUITRED_REGIONS):
        assert asm_after == rec_after, (
            f"count={count} slots={slots} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _GetNewRedTask (seg6:9940) — reassign the red colony's recruit task --
# Composes un_recruit_red + recruit_red, NO args.
_GETNEWREDTASK_REGIONS = [
    (hooks.DG_SEG_INDEX, 0xAC00, 0xCF00),   # ACA2/ACA4/CD88/CE80 + SRand seed
    (_SDG, 0x2300, 0x8400),                  # A-list fields + 836A/836C
    (_PACK, 0x8000, 0xA000),                 # 80F0/9BEE/9C22/9D74/9E7A
]


def _getnewredtask_seed(mode, cd88, seed_val, e7a, aca2, aca4, sdg_836a,
                        sdg_836c, pack_9c22, pack_9bee, slots):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCE80, mode)
        m.mem.ww(dg, 0xCD88, cd88)
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(dg, 0xACA2, aca2)
        m.mem.ww(dg, 0xACA4, aca4)
        m.mem.ww(pack, 0x9E7A, e7a)
        m.mem.ww(pack, 0x9C22, pack_9c22)
        m.mem.ww(pack, 0x9BEE, pack_9bee)
        m.mem.ww(sdg, 0x836A, sdg_836a)
        m.mem.ww(sdg, 0x836C, sdg_836c)
        m.mem.ww(pack, 0x80F0, len(slots))
        for slot, (caste, field_c) in enumerate(slots):
            m.mem.wb(sdg, 0x2F62 + slot, caste)
            m.mem.wb(sdg, 0x2B78 + slot, field_c)
    return seed


@pytest.mark.parametrize(
    "mode,cd88,seed_val,e7a,aca2,aca4,sdg_836a,sdg_836c,pack_9c22,pack_9bee,"
    "slots,label", [
    (1, 1000, 0, 3, 5, 5, 20, 20, 25, 25, [(0x90, 0)], "raid-both-gates-pass"),
    (5, 1000, 0, 3, 5, 5, 20, 15, 15, 35, [(0x90, 0)], "fallback-mode-not-1"),
    (1, 10, 0, 3, 5, 5, 20, 15, 15, 35, [(0x90, 0)], "fallback-gate1-fails"),
    (1, 1000, 0, 0, 5, 5, 20, 15, 15, 35, [(0x90, 0)], "fallback-gate2-fails"),
    (5, 1000, 0, 3, 15, 10, 20, 50, 15, 35, [(0x90, 0)], "fallback-9c22-over-40-clamp"),
    (5, 1000, 0, 3, 5, 5, 20, 30, 15, 35, [(0x90, 0)], "fallback-9c22-inrange-count-lo"),
])
def test_getnewredtask_state_diff_matches_asm(
        mode, cd88, seed_val, e7a, aca2, aca4, sdg_836a, sdg_836c, pack_9c22,
        pack_9bee, slots, label):
    from simant.recovered.gameplay import get_new_red_task
    results = _run_and_diff_segs(
        6, 0x9940, (),
        lambda d, s, p: get_new_red_task(d, s, p),
        _GETNEWREDTASK_REGIONS,
        seed_fn=_getnewredtask_seed(mode, cd88, seed_val, e7a, aca2, aca4,
                                    sdg_836a, sdg_836c, pack_9c22, pack_9bee,
                                    slots))
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _GETNEWREDTASK_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _IsItFoodAt (seg5:5F7E) — bounds-checked (plane,x,y) food predicate --
@pytest.mark.parametrize("plane,x,y,tile,inside", [
    (0, 10, 20, 0x18, True),     # nest food range via is_it_food
    (0, 10, 20, 0x48, False),    # yard food range
    (0, 10, 20, 0x00, True),     # not food
    (2, 10, 20, 0x10, True),     # yard-plane band, left edge
    (2, 10, 20, 0x13, False),    # yard-plane band, right edge
    (2, 10, 20, 0x14, True),     # just outside the band
    (5, 10, 20, 0x18, True),     # plane out of range -> 0, no tile read
    (0, 0x80, 20, 0x18, True),   # x out of range for plane<=1 (max 0x7F)
    (2, 0x40, 20, 0x10, True),   # x out of range for plane>1 (max 0x3F)
    (0, 10, 0x40, 0x18, True),   # y out of range
])
def test_isitfoodat_matches_asm(plane, x, y, tile, inside):
    from simant.recovered.gameplay import is_it_food_at, MAP_PLANE_BASE

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        if 0 <= plane <= 3 and 0 <= x <= 0x7F and 0 <= y <= 0x3F:
            m.mem.wb(dg, MAP_PLANE_BASE[plane] + (x << 6) + y, tile)

    ax, m = _run_and_get_ax(5, 0x5F7E, (plane, x, y), seed_fn=seed)
    dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    expect = is_it_food_at(dgroup_view, pack_view, plane, x, y)
    assert ax == (expect & 0xFFFF), (
        f"plane={plane} x={x} y={y} tile={tile:#x} inside={inside}: "
        f"asm={ax:#06x} rec={expect:#06x}")


# ---- _MakeNewHoleB (seg5:1B06) — search + carve a new above-ground hole ---
_MAKENEWHOLEB_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # yard map through both nest planes + SRand seed
    (_SDG, 0, 0x9000),                       # delta tables, exit-map arrays, scratch fields
    (_PACK, 0x7200, 0xA000),                 # inside flag + both colonies' dig accumulators
]


def _makenewholeb_seed(col, seed_val, inside, row_tiles, blocked_rows):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        # blanket-clear a thin strip (map+life) around every candidate row so
        # _IsClear3x3 (inside=False path) sees a deterministic neighbourhood.
        for row in range(0, 36):
            for c in (col - 1, col, col + 1):
                if not (0 <= c <= 0x3F):
                    continue
                m.mem.wb(dg, 0x28E8 + (row << 6) + c, 0x00)
                m.mem.wb(dg, 0x68E8 + (row << 6) + c, 0x00)
        for row in range(2, 34):
            if row in row_tiles:
                m.mem.wb(dg, 0x28E8 + (row << 6) + col, row_tiles[row])
            elif inside:
                m.mem.wb(dg, 0x28E8 + (row << 6) + col, 0x10)   # never a marker tile
            if row in blocked_rows:
                m.mem.wb(dg, 0x28E8 + (row << 6) + col, 0xFF)
    return seed


@pytest.mark.parametrize("col,seed_val,inside,row_tiles,blocked_rows", [
    # inside=True, roll=0 (seed=0) -> first candidate row=2; tile 0 -> marker 0x86
    (10, 0, True, {2: 0x00}, ()),
    # tile 2 -> marker 0x8A
    (10, 0, True, {2: 0x02}, ()),
    # tile in [0x5E,0x61] -> marker = tile+0x22
    (10, 0, True, {2: 0x5E}, ()),
    # tile 0x66 -> marker 0x85
    (10, 0, True, {2: 0x66}, ()),
    # row 2 not usable, row 3 (next candidate) is -> search advances
    (10, 0, True, {2: 0x10, 3: 0x00}, ()),
    # every candidate excluded -> no-op
    (10, 0, True, {}, ()),
    # inside=False: row 2's 3x3 is clear -> writes 0x50 + edges + dig_tile_b
    (10, 0, False, {}, ()),
    # inside=False: row 2 blocked, row 3 clear -> search advances
    (10, 0, False, {}, (2,)),
    # boundary column
    (0, 0, False, {}, ()),
    (0x3F, 0, False, {}, ()),
])
def test_makenewholeb_state_diff_matches_asm(col, seed_val, inside, row_tiles,
                                             blocked_rows):
    from simant.recovered.gameplay import make_new_hole_b
    results = _run_and_diff_segs(
        5, 0x1B06, (col,),
        lambda d, s, p: make_new_hole_b(d, s, p, col),
        _MAKENEWHOLEB_REGIONS,
        seed_fn=_makenewholeb_seed(col, seed_val, inside, row_tiles, blocked_rows))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKENEWHOLEB_REGIONS):
        assert asm_after == rec_after, (
            f"col={col} seed={seed_val:#x} inside={inside} tiles={row_tiles} "
            f"blocked={blocked_rows} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _LeaveNestB (seg6:515E) — send a black ant out through a hole --------
# Reuses _MAKENEWHOLEB_REGIONS (a superset of _EXITHOLE_REGIONS too) since it
# composes both already-recovered routines.
def _leavenestb_seed(col, x, seed_val, slot, orig_caste, field_c, field_e,
                     hole_row_val, alist_count, exit_tiles):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x3D18 + slot, orig_caste)
        m.mem.wb(sdg, 0x3B22 + slot, field_c)
        m.mem.wb(sdg, 0x3F0E + slot, field_e)
        m.mem.wb(sdg, 0x82D2 + col, hole_row_val)   # hole already tracked -> no MakeNewHoleB
        m.mem.ww(pack, 0x80F0, alist_count)
        for si in range(8):
            nx = hole_row_val + GET_BEST_DIR_DX[si]
            ny = col + GET_BEST_DIR_DY[si]
            if 0 <= nx <= 0x7F and 0 <= ny <= 0x3F:
                m.mem.wb(dg, 0x28E8 + (nx << 6) + ny, exit_tiles.get(si, 0x50))
    return seed


@pytest.mark.parametrize(
    "col,x,seed_val,slot,orig_caste,field_c,field_e,hole_row_val,alist_count,exit_tiles", [
    # exit_hole succeeds (a clear neighbour exists) -> life cell cleared, returns 1
    (10, 20, 0x1234, 0, 0x85, 7, 3, 15, 5, {3: 0x10}),
    # exit_hole fails (nothing clear anywhere) -> caste/field_c restored, returns 0
    (10, 20, 0x1234, 0, 0x85, 7, 3, 15, 5, {}),
])
def test_leavenestb_hole_tracked_state_diff_matches_asm(
        col, x, seed_val, slot, orig_caste, field_c, field_e, hole_row_val,
        alist_count, exit_tiles):
    from simant.recovered.gameplay import leave_nest_b
    results = _run_and_diff_segs(
        6, 0x515E, (col, x),
        lambda d, s, p: leave_nest_b(d, s, p, col, x),
        _MAKENEWHOLEB_REGIONS,
        seed_fn=_leavenestb_seed(col, x, seed_val, slot, orig_caste, field_c,
                                 field_e, hole_row_val, alist_count, exit_tiles))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKENEWHOLEB_REGIONS):
        assert asm_after == rec_after, (
            f"col={col} x={x} exit_tiles={exit_tiles} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


def _leavenestb_notracked_seed(col, x, seed_val, slot, orig_caste, field_c,
                               field_e, alist_count, row_tiles, exit_tiles):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x3D18 + slot, orig_caste)
        m.mem.wb(sdg, 0x3B22 + slot, field_c)
        m.mem.wb(sdg, 0x3F0E + slot, field_e)
        m.mem.wb(sdg, 0x82D2 + col, 0)     # no hole tracked -> triggers MakeNewHoleB
        m.mem.wb(pack, 0x9B6E, 1)          # inside=True (make_new_hole_b's own gate)
        m.mem.ww(pack, 0x80F0, alist_count)
        for row in range(0, 36):
            for c in (col - 1, col, col + 1):
                if 0 <= c <= 0x3F:
                    m.mem.wb(dg, 0x28E8 + (row << 6) + c, 0x00)
                    m.mem.wb(dg, 0x68E8 + (row << 6) + c, 0x00)
        for row in range(2, 34):
            m.mem.wb(dg, 0x28E8 + (row << 6) + col,
                    row_tiles.get(row, 0x10))
        found_row = next(r for r in range(2, 34) if row_tiles.get(r) is not None)
        for si in range(8):
            nx = found_row + GET_BEST_DIR_DX[si]
            ny = col + GET_BEST_DIR_DY[si]
            if 0 <= nx <= 0x7F and 0 <= ny <= 0x3F:
                m.mem.wb(dg, 0x28E8 + (nx << 6) + ny, exit_tiles.get(si, 0x50))
    return seed


def test_leavenestb_no_hole_tracked_state_diff_matches_asm():
    from simant.recovered.gameplay import leave_nest_b
    col, x = 10, 20
    results = _run_and_diff_segs(
        6, 0x515E, (col, x),
        lambda d, s, p: leave_nest_b(d, s, p, col, x),
        _MAKENEWHOLEB_REGIONS,
        seed_fn=_leavenestb_notracked_seed(
            col, x, 0x1234, 0, 0x85, 7, 3, 5, {2: 0x00}, {3: 0x10}))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKENEWHOLEB_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _DigOutBNest/_DigOutRNest (seg7:62DE/63B8) — wander a nest tunnel up -
# from (32,1).  Composes dig_tile_b/r + dig_tile_them_b/r + make_new_hole_b/r,
# so reuses _MAKENEWHOLEB_REGIONS (identical bounds to _MAKENEWHOLER_REGIONS).
def _digoutnest_seed(seed_val, map_base, hole_track_off, dirt_tile, hole_tracked):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        for x in range(0, 64):
            for y in range(0, 40):
                m.mem.wb(dg, map_base + (x << 6) + y, dirt_tile)
            m.mem.wb(sdg, hole_track_off + x, 1 if hole_tracked else 0)
        m.mem.wb(pack, 0x9B6E, 1)
        for off in (0x8104, 0x8106, 0x811A, 0x811C, 0x72C8, 0x9DDC, 0x9DDE,
                   0x9DE2, 0x9DE4, 0x7A56, 0x9FBA, 0x9FD2):
            m.mem.ww(pack, off, 0)
    return seed


@pytest.mark.parametrize("which,off,map_base,hole_track_off", [
    ("b", 0x62DE, 0x48E8, 0x82D2),
    ("r", 0x63B8, 0x58E8, 0x8312),
])
@pytest.mark.parametrize("count,seed_val,hole_tracked", [
    (0, 0x1234, True),    # count=0 -> only the up-front dig at (32,1)
    (3, 0x1234, True),    # a few wander steps, holes already tracked
    (5, 0xBEEF, False),   # more steps, holes NOT tracked -> can trigger make_new_hole
])
def test_digoutnest_state_diff_matches_asm(which, off, map_base, hole_track_off,
                                           count, seed_val, hole_tracked):
    import simant.recovered.gameplay as G
    fn = G.dig_out_b_nest if which == "b" else G.dig_out_r_nest
    results = _run_and_diff_segs(
        7, off, (count,),
        lambda d, s, p: fn(d, s, p, count),
        _MAKENEWHOLEB_REGIONS,
        seed_fn=_digoutnest_seed(seed_val, map_base, hole_track_off, 0x25,
                                 hole_tracked))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKENEWHOLEB_REGIONS):
        assert asm_after == rec_after, (
            f"{which} count={count} seed={seed_val:#x} tracked={hole_tracked} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _MakeNewHoleR (seg5:1D02) — search on the SAME yard map, red closing --
_MAKENEWHOLER_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # yard map through both nest planes + SRand seed
    (_SDG, 0, 0x9000),                       # delta tables, exit-map arrays, scratch fields
    (_PACK, 0x7200, 0xA000),                 # inside flag + red dig accumulators
]


def _makenewholer_seed(col, seed_val, inside, row_tiles, blocked_rows, red_tile1):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        for row in range(93, 128):
            for c in (col - 1, col, col + 1):
                if not (0 <= c <= 0x3F):
                    continue
                m.mem.wb(dg, 0x28E8 + (row << 6) + c, 0x00)
                m.mem.wb(dg, 0x68E8 + (row << 6) + c, 0x00)
        for row in range(95, 127):
            if row in row_tiles:
                m.mem.wb(dg, 0x28E8 + (row << 6) + col, row_tiles[row])
            elif inside:
                m.mem.wb(dg, 0x28E8 + (row << 6) + col, 0x10)   # never a marker tile
            if row in blocked_rows:
                m.mem.wb(dg, 0x28E8 + (row << 6) + col, 0xFF)
        # the fixed (col, 1) cell the closing step digs on the red nest map
        m.mem.wb(dg, 0x58E8 + (col << 6) + 1, red_tile1)
        m.mem.ww(pack, 0x9DDC, 50)
        m.mem.ww(pack, 0x9DDE, 0)
        m.mem.ww(pack, 0x9DE2, 75)
        m.mem.ww(pack, 0x9DE4, 0)
        m.mem.ww(pack, 0x7A56, 3)
    return seed


@pytest.mark.parametrize("col,seed_val,inside,row_tiles,blocked_rows,red_tile1", [
    # inside=True, roll=0 -> first candidate row=126; tile 0 -> marker 0x86
    (10, 0, True, {126: 0x00}, (), 0x25),
    # tile in [0x5E,0x61] -> marker = tile+0x22 (the same decimal/hex fix as B)
    (10, 0, True, {126: 0x5E}, (), 0x25),
    # row 126 not usable, row 125 (next candidate) is -> search advances
    (10, 0, True, {126: 0x10, 125: 0x00}, (), 0x25),
    # every candidate excluded -> no-op
    (10, 0, True, {}, (), 0x25),
    # inside=False: row 126's 3x3 is clear -> writes 0x50 + edges + red closing step
    (10, 0, False, {}, (), 0x25),
    # inside=False: row 126 blocked -> search advances (and the closing step's
    # (col,1) red tile is NOT dirt this time -> reroll/track skipped)
    (10, 0, False, {}, (126,), 0x40),
    # boundary column
    (0, 0, False, {}, (), 0x25),
    (0x3F, 0, False, {}, (), 0x25),
])
def test_makenewholer_state_diff_matches_asm(col, seed_val, inside, row_tiles,
                                             blocked_rows, red_tile1):
    from simant.recovered.gameplay import make_new_hole_r
    results = _run_and_diff_segs(
        5, 0x1D02, (col,),
        lambda d, s, p: make_new_hole_r(d, s, p, col),
        _MAKENEWHOLER_REGIONS,
        seed_fn=_makenewholer_seed(col, seed_val, inside, row_tiles, blocked_rows,
                                   red_tile1))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKENEWHOLER_REGIONS):
        assert asm_after == rec_after, (
            f"col={col} seed={seed_val:#x} inside={inside} tiles={row_tiles} "
            f"blocked={blocked_rows} red_tile1={red_tile1:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _DigTileThemB (seg5:22D4) — open a new tile given diggable neighbours -
_DIGTILETHEMB_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # yard map through both nest planes + SRand seed
    (_SDG, 0, 0x9000),                       # delta tables, exit-map arrays, scratch fields
    (_PACK, 0x7200, 0xA000),                 # inside flag + both colonies' dig accumulators
]


def _digtilethemb_seed(x, y, tile_yplus1, tile_yminus1, seed_val, inside):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        if 0 <= y + 1 <= 0x3F:
            m.mem.wb(dg, 0x48E8 + (x << 6) + y + 1, tile_yplus1)
        if 0 <= y - 1 <= 0x3F:
            m.mem.wb(dg, 0x48E8 + (x << 6) + y - 1, tile_yminus1)
        # keep any triggered make_new_hole_b's search inert: no marker tiles
        # in its candidate rows (2..33) at this same column
        for row in range(2, 34):
            m.mem.wb(dg, 0x28E8 + (row << 6) + x, 0x10)
        for c in (x - 1, x, x + 1):
            if 0 <= c <= 0x3F:
                m.mem.wb(dg, 0x68E8 + c, 0x00)   # yard life row 0, for smooth_edges' is_clear_tile
    return seed


@pytest.mark.parametrize("x,y,tile_yplus1,tile_yminus1,seed_val,inside", [
    (10, 20, 0x40, 0x20, 0x1234, True),        # y+1 not dirt -> reject, no changes
    (10, 20, 0x20, 0x40, 0x1234, True),        # y-1 not dirt -> reject
    (0, 20, 0x20, 0x20, 0x1234, True),         # x==0 -> reject
    (0x3F, 20, 0x20, 0x20, 0x1234, True),      # x>0x3E -> reject
    (10, 20, 0x20, 0x20, 0x1234, True),        # both neighbours dirt -> reroll + accumulate
    (10, 63, 0x00, 0x20, 0x1234, True),        # y=63 (y+1 check skipped, out of range)
    (10, 3, 0x20, 0x00, 0x1234, True),         # y=3 (y-1 check applies at the boundary)
    (10, 0, 0x20, 0x00, 0x1234, True),         # y=0 -> writes 0x18 + triggers make_new_hole_b
])
def test_digtilethemb_state_diff_matches_asm(x, y, tile_yplus1, tile_yminus1,
                                             seed_val, inside):
    from simant.recovered.gameplay import dig_tile_them_b
    results = _run_and_diff_segs(
        5, 0x22D4, (x, y),
        lambda d, s, p: dig_tile_them_b(d, s, p, x, y),
        _DIGTILETHEMB_REGIONS,
        seed_fn=_digtilethemb_seed(x, y, tile_yplus1, tile_yminus1, seed_val, inside))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DIGTILETHEMB_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} y+1={tile_yplus1:#x} y-1={tile_yminus1:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _DigTileThemR (seg5:241C) — red-colony twin ---------------------------
_DIGTILETHEMR_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0, 0x9000),
    (_PACK, 0x7200, 0xA000),
]


def _digtilethemr_seed(x, y, tile_yplus1, tile_yminus1, seed_val, inside):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        if 0 <= y + 1 <= 0x3F:
            m.mem.wb(dg, 0x58E8 + (x << 6) + y + 1, tile_yplus1)
        if 0 <= y - 1 <= 0x3F:
            m.mem.wb(dg, 0x58E8 + (x << 6) + y - 1, tile_yminus1)
        # keep any triggered make_new_hole_r's search inert
        for row in range(95, 127):
            m.mem.wb(dg, 0x28E8 + (row << 6) + x, 0x10)
        m.mem.wb(dg, 0x58E8 + (x << 6) + 1, 0x40)   # (col,1) not dirt -> closing reroll skipped
    return seed


@pytest.mark.parametrize("x,y,tile_yplus1,tile_yminus1,seed_val,inside", [
    (10, 20, 0x40, 0x20, 0x1234, True),
    (10, 20, 0x20, 0x40, 0x1234, True),
    (0, 20, 0x20, 0x20, 0x1234, True),
    (0x3F, 20, 0x20, 0x20, 0x1234, True),
    (10, 20, 0x20, 0x20, 0x1234, True),
    (10, 63, 0x00, 0x20, 0x1234, True),
    (10, 3, 0x20, 0x00, 0x1234, True),
    (10, 0, 0x20, 0x00, 0x1234, True),
])
def test_digtilethemr_state_diff_matches_asm(x, y, tile_yplus1, tile_yminus1,
                                             seed_val, inside):
    from simant.recovered.gameplay import dig_tile_them_r
    results = _run_and_diff_segs(
        5, 0x241C, (x, y),
        lambda d, s, p: dig_tile_them_r(d, s, p, x, y),
        _DIGTILETHEMR_REGIONS,
        seed_fn=_digtilethemr_seed(x, y, tile_yplus1, tile_yminus1, seed_val, inside))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DIGTILETHEMR_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} y+1={tile_yplus1:#x} y-1={tile_yminus1:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _TryMoveDirR / _GetOutR (seg6:6850 / 74BA) — movement EXECUTION,   ----
# a genuine mutual-recursion pair.  Broad shared regions since either can
# transitively reach almost the whole dig subsystem.
_TRYMOVE_GETOUT_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0, 0x9000),
    (_PACK, 0x7200, 0xA000),
]


def _trymove_seed(x, y, tile_at_dest, slot, caste, seed_val):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x46E6 + slot, caste)
        m.mem.ww(dg, 0xCBF2, seed_val)
        if tile_at_dest is not None:
            for si in range(8):
                nx = x + GET_BEST_DIR_DX[si]
                ny = y + GET_BEST_DIR_DY[si]
                if 0 <= nx <= 0x3F and 0 <= ny <= 0x3F:
                    m.mem.wb(dg, 0x58E8 + (nx << 6) + ny, tile_at_dest)
        # keep a GetOutR delegation (new_y<1) predictable: row-0 tile != 0x18
        # (the "not a marked hole" branch), no candidate dirt neighbours, so
        # it just rerolls RNG and recurses once into TryMoveDirR(x,1,roll)
        m.mem.wb(dg, 0x58E8 + (x << 6) + 0, 0x10)
        m.mem.wb(sdg, 0x13A4 + (x << 6), 0)
        for c in (x - 1, x, x + 1):
            if 0 <= c <= 0x3F:
                m.mem.wb(dg, 0x58E8 + (c << 6) + 1, 0x40)   # not dirt -> no side dig
    return seed


@pytest.mark.parametrize("x,y,direction,tile_at_dest,slot,caste,seed_val", [
    (30, 30, -1, 0x10, 0, 0x03, 0x1234),        # direction<0 -> fail
    (0, 30, 5, 0x10, 0, 0x03, 0x1234),          # new_x<0 -> fail (dir=5 -> dx=-1)
    (30, 0, 0, 0x10, 0, 0x03, 0x1234),          # new_y<1 -> delegates to GetOutR
    (30, 30, 3, 0x20, 0, 0x03, 0x1234),         # destination tile>=0x1C -> fail
    (30, 30, 3, 0x10, 0, 0x03, 0x1234),         # successful move
    (30, 30, 3, 0x10, 2, 0xAA, 0x1234),         # successful move, different slot/caste
])
def test_trymovedirr_state_diff_matches_asm(x, y, direction, tile_at_dest, slot,
                                            caste, seed_val):
    from simant.recovered.gameplay import try_move_dir_r
    results = _run_and_diff_segs(
        6, 0x6850, (x, y, direction),
        lambda d, s, p: try_move_dir_r(d, s, p, x, y, direction),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_trymove_seed(x, y, tile_at_dest, slot, caste, seed_val))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} dir={direction} tile={tile_at_dest} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _StayInR (seg6:5C16) — idle-in-nest: nibble food or keep wandering ---
# Composes try_move_dir_r + get_enter_dir_r; reuses _TRYMOVE_GETOUT_REGIONS.
def _stayinr_seed(x, y, tile, caste, field_c, counter_72de, slot, seed_val,
                  move_tile):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(dg, 0x58E8 + (x << 6) + y, tile)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x46E6 + slot, caste)
        m.mem.wb(sdg, 0x44F0 + slot, field_c)
        m.mem.ww(pack, 0x72DE, counter_72de)
        if move_tile is not None:
            for si in range(8):
                nx, ny = x + GET_BEST_DIR_DX[si], y + GET_BEST_DIR_DY[si]
                if 0 <= nx <= 0x3F and 0 <= ny <= 0x3F:
                    m.mem.wb(dg, 0x58E8 + (nx << 6) + ny, move_tile)
            m.mem.wb(dg, 0x58E8 + (x << 6) + 0, 0x10)
            m.mem.wb(sdg, 0x13A4 + (x << 6), 0)
            for c in (x - 1, x, x + 1):
                if 0 <= c <= 0x3F:
                    m.mem.wb(dg, 0x58E8 + (c << 6) + 1, 0x40)
    return seed


@pytest.mark.parametrize(
    "x,y,tile,caste,field_c,counter_72de,slot,seed_val,move_tile,label", [
    (30, 30, 0x10, 0x03, 1, 5, 0, 0x1234, None, "food-reroll"),
    (30, 30, 0x12, 0x03, 1, 5, 1, 0x1234, None, "food-decrement-counter-nonzero"),
    (30, 30, 0x12, 0x03, 1, 0, 0, 0x1234, None, "food-decrement-counter-zero"),
    (30, 30, 0x40, 0x03, 1, 5, 0, 0x1234, 0x10, "move-first-try-succeeds"),
    (30, 30, 0x40, 0xAA, 1, 5, 2, 0x1234, 0x20, "move-first-try-fails-fallback"),
])
def test_stayinr_state_diff_matches_asm(x, y, tile, caste, field_c,
                                        counter_72de, slot, seed_val,
                                        move_tile, label):
    from simant.recovered.gameplay import stay_in_r
    direction = 3
    results = _run_and_diff_segs(
        6, 0x5C16, (x, y, direction),
        lambda d, s, p: stay_in_r(d, s, p, x, y, direction),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_stayinr_seed(x, y, tile, caste, field_c, counter_72de, slot,
                              seed_val, move_tile))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


def _getout_seed(x, hole_marker, hole_x_val, slot, caste, seed_val, exit_dest_tiles):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x46E6 + slot, caste)
        m.mem.wb(sdg, 0x48DC + slot, 5)     # field_e
        m.mem.wb(sdg, 0x44F0 + slot, 7)     # field_c
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(dg, 0x58E8 + (x << 6) + 0, hole_marker)
        m.mem.wb(sdg, 0x8312 + x, hole_x_val)
        # exit_hole's own 8-direction search around (hole_x_val, x) -- give
        # it a fully clear neighbourhood so it reliably succeeds when tested
        for si in range(8):
            nx = hole_x_val + GET_BEST_DIR_DX[si]
            ny = x + GET_BEST_DIR_DY[si]
            if 0 <= nx <= 0x7F and 0 <= ny <= 0x3F:
                m.mem.wb(dg, 0x28E8 + (nx << 6) + ny, exit_dest_tiles)
        m.mem.wb(pack, 0x9B6E, 0)   # is_valid_a-style flag used by tile_can_be_moved_on
        # not-a-hole branch: keep it inert (no side digs, single RNG reroll +
        # one TryMoveDirR recursion into a clean fail)
        m.mem.wb(sdg, 0x13A4 + (x << 6), 0)
        for c in (x - 1, x, x + 1):
            if 0 <= c <= 0x3F:
                m.mem.wb(dg, 0x58E8 + (c << 6) + 1, 0x40)
    return seed


@pytest.mark.parametrize(
    "x,hole_marker,hole_x_val,slot,caste,seed_val,exit_dest_tiles", [
    (20, 0x10, 0, 0, 0x03, 0x1234, 0x00),        # not a marked hole -> RNG dance + recurse
    (20, 0x18, 0, 0, 0x03, 0x1234, 0x00),        # marked hole, hole_x==0 -> no MakeNewHoleR
    (20, 0x18, 15, 1, 0x83, 0x0000, 0x00),       # marked hole, hole_x!=0 -> triggers MakeNewHoleR
])
def test_getoutr_state_diff_matches_asm(x, hole_marker, hole_x_val, slot, caste,
                                        seed_val, exit_dest_tiles):
    from simant.recovered.gameplay import get_out_r
    results = _run_and_diff_segs(
        6, 0x74BA, (x,),
        lambda d, s, p: get_out_r(d, s, p, x),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_getout_seed(x, hole_marker, hole_x_val, slot, caste, seed_val,
                             exit_dest_tiles))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} marker={hole_marker:#x} hole_x={hole_x_val} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _TryMoveDirB / _GetOutB (seg6:439E / 520A) — movement EXECUTION,   ----
# black colony. _TryMoveDirB additionally gates a trophallaxis branch this
# session deliberately does NOT recover (_DoTroph is out of scope) -- kept
# out of every seeded scenario below (destination LIFE cell != 0xFF), and
# a dedicated test confirms the gate itself raises loudly when it WOULD fire.
def _trymoveb_seed(x, y, tile_at_dest, slot, caste, seed_val, life_at_dest=0x00,
                   pack_9af2=0):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x3D18 + slot, caste)
        m.mem.wb(sdg, 0x3736 + slot, 0)   # trophallaxis threshold field, kept < 0x80
        m.mem.ww(pack, 0x9AF2, pack_9af2)
        m.mem.ww(dg, 0xCBF2, seed_val)
        if tile_at_dest is not None:
            for si in range(8):
                nx = x + GET_BEST_DIR_DX[si]
                ny = y + GET_BEST_DIR_DY[si]
                if 0 <= nx <= 0x3F and 0 <= ny <= 0x3F:
                    m.mem.wb(dg, 0x48E8 + (nx << 6) + ny, tile_at_dest)
                    m.mem.wb(dg, 0x88E8 + (nx << 6) + ny, life_at_dest)
        m.mem.wb(dg, 0x48E8 + (x << 6) + 0, 0x10)
        m.mem.wb(sdg, 0x3A4 + (x << 6), 0)
        for c in (x - 1, x, x + 1):
            if 0 <= c <= 0x3F:
                m.mem.wb(dg, 0x48E8 + (c << 6) + 1, 0x40)
    return seed


@pytest.mark.parametrize("x,y,direction,tile_at_dest,slot,caste,seed_val", [
    (30, 30, -1, 0x10, 0, 0x03, 0x1234),
    (0, 30, 5, 0x10, 0, 0x03, 0x1234),
    (30, 0, 0, 0x10, 0, 0x03, 0x1234),
    (30, 30, 3, 0x20, 0, 0x03, 0x1234),
    (30, 30, 3, 0x10, 0, 0x03, 0x1234),
    (30, 30, 3, 0x10, 2, 0xAA, 0x1234),
])
def test_trymovedirb_state_diff_matches_asm(x, y, direction, tile_at_dest, slot,
                                            caste, seed_val):
    from simant.recovered.gameplay import try_move_dir_b
    results = _run_and_diff_segs(
        6, 0x439E, (x, y, direction),
        lambda d, s, p: try_move_dir_b(d, s, p, x, y, direction),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_trymoveb_seed(x, y, tile_at_dest, slot, caste, seed_val))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} dir={direction} tile={tile_at_dest} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


def test_trymovedirb_trophallaxis_gate_raises():
    # destination LIFE cell == 0xFF, pack[0x9AF2] != 0, and the acting
    # ant's [0x3736+slot] < 0x80 -> the trophallaxis branch WOULD fire.
    from simant.recovered.gameplay import try_move_dir_b
    from simant.bridge.dgroup_view import ByteBackend

    x, y, direction = 30, 30, 3
    dg = bytearray(0x10000)
    sdg = bytearray(0x10000)
    pack = bytearray(0x10000)
    nx, ny = x + 1, y + 1   # direction 3 -> (+1, +1)
    dg[0x48E8 + (nx << 6) + ny] = 0x10        # passable
    dg[0x88E8 + (nx << 6) + ny] = 0xFF        # empty LIFE cell
    pack_view = ByteBackend(pack, 0)
    pack_view.ww(0x9AF2, 1)
    sdg[0x3736] = 0x10                         # < 0x80
    with pytest.raises(NotImplementedError):
        try_move_dir_b(ByteBackend(dg, 0), ByteBackend(sdg, 0), pack_view, x, y,
                       direction)


def _getoutb_seed(x, hole_marker, hole_x_val, slot, caste, seed_val, exit_dest_tiles):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x3D18 + slot, caste)
        m.mem.wb(sdg, 0x3F0E + slot, 5)     # field_e
        m.mem.wb(sdg, 0x3B22 + slot, 7)     # field_c
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(dg, 0x48E8 + (x << 6) + 0, hole_marker)
        m.mem.wb(sdg, 0x82D2 + x, hole_x_val)
        for si in range(8):
            nx = hole_x_val + GET_BEST_DIR_DX[si]
            ny = x + GET_BEST_DIR_DY[si]
            if 0 <= nx <= 0x7F and 0 <= ny <= 0x3F:
                m.mem.wb(dg, 0x28E8 + (nx << 6) + ny, exit_dest_tiles)
        m.mem.wb(pack, 0x9B6E, 0)
        m.mem.wb(sdg, 0x3A4 + (x << 6), 0)
        for c in (x - 1, x, x + 1):
            if 0 <= c <= 0x3F:
                m.mem.wb(dg, 0x48E8 + (c << 6) + 1, 0x40)
    return seed


@pytest.mark.parametrize(
    "x,hole_marker,hole_x_val,slot,caste,seed_val,exit_dest_tiles", [
    (20, 0x10, 0, 0, 0x03, 0x1234, 0x00),
    (20, 0x18, 0, 0, 0x03, 0x1234, 0x00),
    (20, 0x18, 15, 1, 0x83, 0x0000, 0x00),
])
def test_getoutb_state_diff_matches_asm(x, hole_marker, hole_x_val, slot, caste,
                                        seed_val, exit_dest_tiles):
    from simant.recovered.gameplay import get_out_b
    results = _run_and_diff_segs(
        6, 0x520A, (x,),
        lambda d, s, p: get_out_b(d, s, p, x),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_getoutb_seed(x, hole_marker, hole_x_val, slot, caste, seed_val,
                              exit_dest_tiles))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} marker={hole_marker:#x} hole_x={hole_x_val} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _RaidOutB / _RaidOutR (seg6:3610 / 5D10) — move an ant toward an exit,
# or give up and re-stamp its caste in place. Composes get_exit_dir_b/r and
# try_move_dir_b/r; reuses their own established seed helpers/regions since
# every dependency is already fully seeded there.
@pytest.mark.parametrize("tile_at_dest,label", [
    (0x10, "first attempt succeeds -> moves"),
    (0x20, "every neighbour blocked -> both attempts fail, caste re-stamped"),
])
def test_raidoutb_state_diff_matches_asm(tile_at_dest, label):
    from simant.recovered.gameplay import raid_out_b
    x, y, slot, caste, seed_val = 30, 30, 0, 0x03, 0x1234
    results = _run_and_diff_segs(
        6, 0x3610, (x, y),
        lambda d, s, p: raid_out_b(d, s, p, x, y),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_trymoveb_seed(x, y, tile_at_dest, slot, caste, seed_val))
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("tile_at_dest,label", [
    (0x10, "first attempt succeeds -> moves"),
    (0x20, "every neighbour blocked -> both attempts fail, caste re-stamped"),
])
def test_raidoutr_state_diff_matches_asm(tile_at_dest, label):
    from simant.recovered.gameplay import raid_out_r
    x, y, slot, caste, seed_val = 30, 30, 0, 0x03, 0x1234
    results = _run_and_diff_segs(
        6, 0x5D10, (x, y),
        lambda d, s, p: raid_out_r(d, s, p, x, y),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_trymove_seed(x, y, tile_at_dest, slot, caste, seed_val))
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _RaidInB / _RaidInR (seg6:3524 / 5B2A) — an ant entering the nest with
# food, or trying to. Composes try_move_dir_b/r + get_enter_dir_b/r; reuses
# try_move_dir_b/r's own established seed helpers, layering the ant's OWN
# tile and food-count field on top.
def _raidin_seed(base_seed_fn, map_base, food_count_off, x, y, own_tile,
                 food_count):
    def seed(m):
        base_seed_fn(m)
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(dg, map_base + (x << 6) + y, own_tile)
        m.mem.ww(pack, food_count_off, food_count)
    return seed


@pytest.mark.parametrize("own_tile,tile_at_dest,label", [
    (0x10, 0x40, "own tile is a food pile -> nibble + carry-food stamp, no move"),
    (0x40, 0x10, "not a food pile, first attempt succeeds -> moves"),
    (0x40, 0x20, "not a food pile, every neighbour blocked -> field_c=1 fallback stamp"),
])
def test_raidinb_state_diff_matches_asm(own_tile, tile_at_dest, label):
    from simant.recovered.gameplay import raid_in_b
    x, y, slot, caste, seed_val, exclude = 30, 30, 0, 0x03, 0x1234, 3
    results = _run_and_diff_segs(
        6, 0x3524, (x, y, exclude),
        lambda d, s, p: raid_in_b(d, s, p, x, y, exclude),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_raidin_seed(
            _trymoveb_seed(x, y, tile_at_dest, slot, caste, seed_val),
            0x48E8, 0x9EA4, x, y, own_tile, 5))
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("own_tile,tile_at_dest,label", [
    (0x10, 0x40, "own tile is a food pile -> nibble + carry-food stamp, no move"),
    (0x40, 0x10, "not a food pile, first attempt succeeds -> moves"),
    (0x40, 0x20, "not a food pile, every neighbour blocked -> field_c=1 fallback stamp"),
])
def test_raidinr_state_diff_matches_asm(own_tile, tile_at_dest, label):
    from simant.recovered.gameplay import raid_in_r
    x, y, slot, caste, seed_val, exclude = 30, 30, 0, 0x03, 0x1234, 3
    results = _run_and_diff_segs(
        6, 0x5B2A, (x, y, exclude),
        lambda d, s, p: raid_in_r(d, s, p, x, y, exclude),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_raidin_seed(
            _trymove_seed(x, y, tile_at_dest, slot, caste, seed_val),
            0x58E8, 0x72DE, x, y, own_tile, 5))
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _QueenMoveB / _QueenMoveR (seg6:4154 / 6606) — queen movement + trail-
# marker relocation. Composes get_best_dir, try_move_dir_b/r, find_in_b/
# r_list -- all already recovered. NOT byte-symmetric between colonies (the
# marker offset and the final caste transform genuinely differ).
def _queenmove_seed(x, y, tgt_x, tgt_y, map_base, life_base, inside_flag,
                    target_x_off, target_y_off, count_off, count,
                    marker_slot=None, marker_val=0, marker_pos=None,
                    y_off=None, x_off=None, caste_off=None, seed_val=0x1234):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x9B6E, inside_flag)
        m.mem.ww(pack, target_x_off, tgt_x)
        m.mem.ww(pack, target_y_off, tgt_y)
        m.mem.ww(pack, count_off, count)
        for si in range(8):
            nx, ny = x + GET_BEST_DIR_DX[si], y + GET_BEST_DIR_DY[si]
            if 0 <= nx <= 0x3F and 0 <= ny <= 0x3F:
                m.mem.wb(dg, map_base + (nx << 6) + ny, 0)
                m.mem.wb(dg, life_base + (nx << 6) + ny, 0)
        if marker_slot is not None:
            mx, my = marker_pos
            m.mem.wb(sdg, y_off + marker_slot, mx)
            m.mem.wb(sdg, x_off + marker_slot, my)
            m.mem.wb(sdg, caste_off + marker_slot, marker_val)
    return seed


@pytest.mark.parametrize("colony,seg,off,map_base,life_base,target_x_off,"
                         "target_y_off,count_off,y_off,x_off,caste_off", [
    ("B", 6, 0x4154, 0x48E8, 0x88E8, 0x7C48, 0x7C90, 0x99D4, 0x3736, 0x392C, 0x3D18),
    ("R", 6, 0x6606, 0x58E8, 0x98E8, 0x9FBA, 0x9FD2, 0x72CC, 0x4104, 0x42FA, 0x46E6),
])
def test_queenmove_already_there_matches_asm(colony, seg, off, map_base,
                                             life_base, target_x_off,
                                             target_y_off, count_off, y_off,
                                             x_off, caste_off):
    from simant.recovered.gameplay import queen_move_b, queen_move_r
    fn = queen_move_b if colony == "B" else queen_move_r
    x, y, exclude = 30, 30, 3
    results = _run_and_diff_segs(
        seg, off, (x, y, exclude),
        lambda d, s, p: fn(d, s, p, x, y, exclude),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_queenmove_seed(x, y, x, y, map_base, life_base, 0,
                                target_x_off, target_y_off, count_off, 0))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{colony} already-there {label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("colony,seg,off,map_base,life_base,target_x_off,"
                         "target_y_off,count_off,y_off,x_off,caste_off,"
                         "marker_add", [
    ("B", 6, 0x4154, 0x48E8, 0x88E8, 0x7C48, 0x7C90, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x68),
    ("R", 6, 0x6606, 0x58E8, 0x98E8, 0x9FBA, 0x9FD2, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0xE8),
])
def test_queenmove_no_marker_matches_asm(colony, seg, off, map_base, life_base,
                                         target_x_off, target_y_off, count_off,
                                         y_off, x_off, caste_off, marker_add):
    from simant.recovered.gameplay import queen_move_b, queen_move_r
    fn = queen_move_b if colony == "B" else queen_move_r
    x, y, exclude = 30, 30, 3
    results = _run_and_diff_segs(
        seg, off, (x, y, exclude),
        lambda d, s, p: fn(d, s, p, x, y, exclude),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_queenmove_seed(x, y, x + 10, y, map_base, life_base, 0,
                                target_x_off, target_y_off, count_off, 0))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{colony} no-marker {label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("colony,seg,off,map_base,life_base,target_x_off,"
                         "target_y_off,count_off,y_off,x_off,caste_off,"
                         "marker_add", [
    ("B", 6, 0x4154, 0x48E8, 0x88E8, 0x7C48, 0x7C90, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x68),
    ("R", 6, 0x6606, 0x58E8, 0x98E8, 0x9FBA, 0x9FD2, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0xE8),
])
def test_queenmove_relocates_marker_matches_asm(colony, seg, off, map_base,
                                                life_base, target_x_off,
                                                target_y_off, count_off,
                                                y_off, x_off, caste_off,
                                                marker_add):
    from simant.recovered.gameplay import queen_move_b, queen_move_r
    fn = queen_move_b if colony == "B" else queen_move_r
    x, y, exclude = 30, 30, 3
    opp_dir = (exclude ^ 4) & 7
    nx2 = x + GET_BEST_DIR_DX[opp_dir]
    ny2 = y + GET_BEST_DIR_DY[opp_dir]
    marker = ((exclude & 7) + marker_add) & 0xFF
    results = _run_and_diff_segs(
        seg, off, (x, y, exclude),
        lambda d, s, p: fn(d, s, p, x, y, exclude),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_queenmove_seed(x, y, x + 10, y, map_base, life_base, 0,
                                target_x_off, target_y_off, count_off, 1,
                                marker_slot=0, marker_val=marker,
                                marker_pos=(nx2, ny2), y_off=y_off,
                                x_off=x_off, caste_off=caste_off))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{colony} relocates {label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("colony,seg,off,map_base,life_base,target_x_off,"
                         "target_y_off,count_off,y_off,x_off,caste_off", [
    ("B", 6, 0x4154, 0x48E8, 0x88E8, 0x7C48, 0x7C90, 0x99D4, 0x3736, 0x392C, 0x3D18),
    ("R", 6, 0x6606, 0x58E8, 0x98E8, 0x9FBA, 0x9FD2, 0x72CC, 0x4104, 0x42FA, 0x46E6),
])
def test_queenmove_top_edge_restriction_matches_asm(colony, seg, off, map_base,
                                                     life_base, target_x_off,
                                                     target_y_off, count_off,
                                                     y_off, x_off, caste_off):
    from simant.recovered.gameplay import queen_move_b, queen_move_r
    fn = queen_move_b if colony == "B" else queen_move_r
    # near the top edge (y<3), target due WEST -> best dir is index 6 (dx=-1,
    # dy=0), outside the allowed [3,5] band -> the whole call is a no-op.
    x, y, exclude = 30, 1, 3
    results = _run_and_diff_segs(
        seg, off, (x, y, exclude),
        lambda d, s, p: fn(d, s, p, x, y, exclude),
        _TRYMOVE_GETOUT_REGIONS,
        seed_fn=_queenmove_seed(x, y, x - 10, y, map_base, life_base, 0,
                                target_x_off, target_y_off, count_off, 0))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _TRYMOVE_GETOUT_REGIONS):
        assert asm_after == rec_after, f"{colony} top-edge {label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _GetNewMode (seg7:0910) — caste mode-transition lookup ---------------
_GETNEWMODE_REGIONS = [
    (hooks.DG_SEG_INDEX, 0xCBF0, 0xCBF4),   # SRand seed
    (_SDG, 0x8900, 0x8B00),                 # tables at 0x89E6/0x8A16/0x8A46/0x8A58
    (_PACK, 0x7600, 0xA000),   # mode_base [0x7690]/[0x9B8A], gate flag [0x9FCE]
]


def _getnewmode_seed(seed_val, mode_base_hi, mode_base_lo, gate_flag, tbl2, tbl6,
                     tbl_direct, tbl_word):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x7690, mode_base_hi)
        m.mem.ww(pack, 0x9B8A, mode_base_lo)
        m.mem.ww(pack, 0x9FCE, gate_flag)
        for i in range(8):
            m.mem.wb(sdg, 0x89E6 + ((mode_base_hi << 3) + i), tbl2)
            m.mem.wb(sdg, 0x89E6 + ((mode_base_lo << 3) + i), tbl2)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_hi << 3) + i), tbl6)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_lo << 3) + i), tbl6)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, tbl_direct)
        m.mem.ww(sdg, 0x8A58, tbl_word)
    return seed


@pytest.mark.parametrize(
    "sub,full_byte,seed_val,mode_base_hi,mode_base_lo,gate_flag,tbl2,tbl6,"
    "tbl_direct,tbl_word", [
    (2, 0x80, 0x1234, 1, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # 0x80 set, sub=2 -> rolled
    (6, 0x80, 0x1234, 1, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # 0x80 set, sub=6 -> rolled
    (4, 0x80, 0x1234, 1, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # 0x80 set, other sub -> direct
    (2, 0x00, 0x1234, 1, 3, 1, 0x25, 0x30, 0x40, 0x1122),   # 0x80 clear, gate=1, sub=2
    (6, 0x00, 0x1234, 1, 3, 1, 0x25, 0x30, 0x40, 0x1122),   # 0x80 clear, gate=1, sub=6
    (4, 0x00, 0x1234, 1, 3, 1, 0x25, 0x30, 0x40, 0x1122),   # 0x80 clear, gate=1, other
    (2, 0x00, 0x1234, 1, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # 0x80 clear, gate!=1, sub=2 -> word
    (6, 0x00, 0x1234, 1, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # 0x80 clear, gate!=1, sub=6 -> word
    (4, 0x00, 0x1234, 1, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # 0x80 clear, gate!=1, other
])
def test_getnewmode_state_diff_matches_asm(sub, full_byte, seed_val, mode_base_hi,
                                           mode_base_lo, gate_flag, tbl2, tbl6,
                                           tbl_direct, tbl_word):
    from simant.recovered.gameplay import get_new_mode
    results = _run_and_diff_segs(
        7, 0x910, (sub, full_byte),
        lambda d, s, p: get_new_mode(d, s, p, sub, full_byte),
        _GETNEWMODE_REGIONS,
        seed_fn=_getnewmode_seed(seed_val, mode_base_hi, mode_base_lo, gate_flag,
                                 tbl2, tbl6, tbl_direct, tbl_word))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _GETNEWMODE_REGIONS):
        assert asm_after == rec_after, (
            f"sub={sub} full_byte={full_byte:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _GetNewModeB / _GetNewModeR (seg7:09D0 / 0A50) — per-colony ------------
# specializations of _GetNewMode, proven against the REAL ASM (not just
# against get_new_mode itself, which would be circular).
@pytest.mark.parametrize(
    "sub,seed_val,mode_base_lo,gate_flag,tbl2,tbl6,tbl_direct,tbl_word", [
    (2, 0x1234, 3, 1, 0x25, 0x30, 0x40, 0x1122),   # gate=1, sub=2 -> rolled
    (6, 0x1234, 3, 1, 0x25, 0x30, 0x40, 0x1122),   # gate=1, sub=6 -> rolled
    (4, 0x1234, 3, 1, 0x25, 0x30, 0x40, 0x1122),   # gate=1, other -> direct
    (2, 0x1234, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # gate=0, sub=2 -> word
    (4, 0x1234, 3, 0, 0x25, 0x30, 0x40, 0x1122),   # gate=0, other -> direct
])
def test_getnewmodeb_state_diff_matches_asm(sub, seed_val, mode_base_lo,
                                            gate_flag, tbl2, tbl6, tbl_direct,
                                            tbl_word):
    from simant.recovered.gameplay import get_new_mode_b
    results = _run_and_diff_segs(
        7, 0x9D0, (sub,),
        lambda d, s, p: get_new_mode_b(d, s, p, sub),
        _GETNEWMODE_REGIONS,
        seed_fn=_getnewmode_seed(seed_val, 1, mode_base_lo, gate_flag,
                                 tbl2, tbl6, tbl_direct, tbl_word))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _GETNEWMODE_REGIONS):
        assert asm_after == rec_after, (
            f"sub={sub} {label}: {_first_diff(asm_after, rec_after, lo)}")


@pytest.mark.parametrize(
    "sub,seed_val,mode_base_hi,tbl2,tbl6,tbl_direct,tbl_word", [
    (2, 0x1234, 2, 0x25, 0x30, 0x40, 0x1122),   # sub=2 -> rolled via mode_base_hi
    (6, 0x1234, 2, 0x25, 0x30, 0x40, 0x1122),   # sub=6 -> rolled via mode_base_hi
    (4, 0x1234, 2, 0x25, 0x30, 0x40, 0x1122),   # other -> direct (no gate, no word table)
])
def test_getnewmoder_state_diff_matches_asm(sub, seed_val, mode_base_hi, tbl2,
                                            tbl6, tbl_direct, tbl_word):
    from simant.recovered.gameplay import get_new_mode_r
    results = _run_and_diff_segs(
        7, 0xA50, (sub,),
        lambda d, s, p: get_new_mode_r(d, s, p, sub),
        _GETNEWMODE_REGIONS,
        seed_fn=_getnewmode_seed(seed_val, mode_base_hi, 3, 0,
                                 tbl2, tbl6, tbl_direct, tbl_word))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _GETNEWMODE_REGIONS):
        assert asm_after == rec_after, (
            f"sub={sub} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _DoDrownB/_DoDrownR (seg6:37A4/5EA8) — age/occasionally-drown an ant --
# on a nest water tile.  Composes get_new_mode_b/r, so the region merges the
# _GETNEWMODE_REGIONS tables with the B/R-list field bases and drown counters.
_DODROWN_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0x3700, 0x8B00),
    (_PACK, 0x7600, 0xA000),
]


def _dodrown_seed(seed_val, map_tile, x, y, map_base, slot, gate_flag, tbl2,
                  tbl6, tbl_direct, tbl_word):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(dg, map_base + (x << 6) + y, map_tile)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.ww(pack, 0x7690, 1)
        m.mem.ww(pack, 0x9B8A, 1)
        m.mem.ww(pack, 0x9FCE, gate_flag)
        for i in range(8):
            m.mem.wb(sdg, 0x89E6 + (1 << 3) + i, tbl2)
            m.mem.wb(sdg, 0x8A16 + (1 << 3) + i, tbl6)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, tbl_direct)
        m.mem.ww(sdg, 0x8A58, tbl_word)
    return seed


@pytest.mark.parametrize("which,off,map_base,field_c_off,caste_off", [
    ("b", 0x37A4, 0x48E8, 0x3B22, 0x3D18),
    ("r", 0x5EA8, 0x58E8, 0x44F0, 0x46E6),
])
@pytest.mark.parametrize("map_tile,x,y,seed_val,slot,caste", [
    (0x10, 20, 30, 0x1234, 0, 0x85),         # below threshold -> field_c only
    (0x14, 20, 30, 0x0001, 1, 0x05),         # at threshold, no drown -> roll100 return
    (0x50, 20, 30, 0x0000, 2, 0x05),         # drown, colony bit clear -> [0x9B26] bumps
    (0x50, 20, 30, 0x0000, 3, 0x85),         # drown, colony bit set -> [0x9FC6] bumps
])
def test_dodrown_state_diff_matches_asm(which, off, map_base, field_c_off,
                                        caste_off, map_tile, x, y, seed_val,
                                        slot, caste):
    import simant.recovered.gameplay as G
    fn = G.do_drown_b if which == "b" else G.do_drown_r
    results = _run_and_diff_segs(
        6, off, (x, y, caste),
        lambda d, s, p: fn(d, s, p, x, y, caste),
        _DODROWN_REGIONS,
        seed_fn=_dodrown_seed(seed_val, map_tile, x, y, map_base, slot, 1,
                              0x25, 0x30, 0x40, 0x1122))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DODROWN_REGIONS):
        assert asm_after == rec_after, (
            f"{which} tile={map_tile:#x} caste={caste:#x} seed={seed_val:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _GetWinner (seg6:26F4) — one-on-one combat matchup resolution --------
# NEAR call/return. Uses _RRand (the C-runtime generator, RAND_STATE_OFF in
# DGROUP), NOT the _SRand* LFSR.
_GETWINNER_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x8900, 0xAE40),   # strength/outcome tables + RAND_STATE_OFF
    (_SDG, 0x8A00, 0x8B00),                 # cheat-gate flag [0x8A5C]
    (_PACK, 0x7900, 0xA100),   # win-count stats [0x79E4]/[0x9E96]/[0x99E0]/[0xA0E4]
]


def _getwinner_seed(cheat_flag, rand_state, strength_tbl, outcome_tbl):
    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        m.mem.wb(sdg, 0x8A5C, cheat_flag)
        m.mem.ww(dg, RAND_STATE_OFF, rand_state & 0xFFFF)
        m.mem.ww(dg, (RAND_STATE_OFF + 2) & 0xFFFF, (rand_state >> 16) & 0xFFFF)
        for sub, v in strength_tbl.items():
            m.mem.wb(dg, 0x8902 + sub, v)
        for idx, v in outcome_tbl.items():
            m.mem.wb(dg, 0x8918 + idx, v)
    return seed


@pytest.mark.parametrize("cheat_flag,arg_a,arg_b,rand_state,strength_tbl,outcome_tbl", [
    (1, 0x08, 0x88, 0, {}, {}),         # cheat gate, arg_a's 0x80 clear -> arg_b wins
    (1, 0x88, 0x08, 0, {}, {}),         # cheat gate, arg_a's 0x80 set -> arg_a wins
    (0, 0x08, 0x88, 0, {1: 1}, {5: 5}),   # roll(state=0)=8 >= outcome=5 -> arg_b (colony R) wins
    (0, 0x08, 0x88, 1, {1: 1}, {5: 5}),   # roll(state=1)=1 <  outcome=5 -> arg_a (colony B) wins
])
def test_getwinner_state_diff_matches_asm(cheat_flag, arg_a, arg_b, rand_state,
                                          strength_tbl, outcome_tbl):
    from simant.recovered.gameplay import get_winner
    results = _run_and_diff_segs(
        6, 0x26F4, (arg_a, arg_b),
        lambda d, s, p: get_winner(d, s, p, arg_a, arg_b),
        _GETWINNER_REGIONS, near=True,
        seed_fn=_getwinner_seed(cheat_flag, rand_state, strength_tbl, outcome_tbl))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _GETWINNER_REGIONS):
        assert asm_after == rec_after, (
            f"arg_a={arg_a:#x} arg_b={arg_b:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _SimEggA (seg6:0A1C) — yard egg tick ----------------------------------
_SIMEGGA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x68E8, 0xCBF4),   # yard life plane + SRand seed
    (_SDG, 0x2300, 0x3800),                 # A-list fields
]


@pytest.mark.parametrize("slot,a_x,a_y,caste,seed_val,label", [
    (0x10, 5, 7, 0x81, 0x1234, "roll != 0 -> caste re-stamped, nothing removed"),
    (0x10, 5, 7, 0x81, 0x0000, "roll == 0 -> egg removed"),
])
def test_simegga_state_diff_matches_asm(slot, a_x, a_y, caste, seed_val, label):
    from simant.recovered.gameplay import sim_egg_a

    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(sdg, 0x23A4 + slot, a_x)
        m.mem.wb(sdg, 0x278E + slot, a_y)
        m.mem.wb(sdg, 0x2F62 + slot, caste)

    results = _run_and_diff_segs(
        6, 0x0A1C, (slot,),
        lambda d, s: sim_egg_a(d, s, slot),
        _SIMEGGA_REGIONS, near=True, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _SIMEGGA_REGIONS):
        assert asm_after == rec_after, (
            f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _SimEggB (seg6:3CA0) — black nest egg/larva growth tick --------------
# Composes sg_rand + get_new_mode_b.
_SIMEGG_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0x3700, 0x8B00),
    (_PACK, 0x7500, 0xA000),
]


def _simeggb_seed(seed_val, ac82, mask_flags, slot, caste, gate_9fce,
                  threshold_9c78, mode_8a56, tbl_direct):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(dg, 0xAC82, ac82)
        m.mem.ww(pack, 0x75FC, mask_flags)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x3D18 + slot, caste)
        m.mem.ww(pack, 0x9FCE, gate_9fce)
        m.mem.ww(pack, 0x9C78, threshold_9c78)
        m.mem.ww(sdg, 0x8A56, mode_8a56)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, tbl_direct)
        m.mem.ww(pack, 0x7C1E, 0)
        m.mem.ww(pack, 0x7C20, 0)
    return seed


@pytest.mark.parametrize(
    "x,y,ac82,mask_flags,slot,caste,gate_9fce,threshold_9c78,mode_8a56,"
    "tbl_direct,seed_val,label", [
    (20, 30, 1, 0x1F, 0, 0x25, 0, 0, 4, 0x50, 0x1234, "bitmask-blocks-noop"),
    (20, 30, 1, 0x00, 0, 0x21, 0, 0, 4, 0x50, 0x1234, "increment-not-a-hatch-tick"),
    (20, 30, 1, 0x00, 1, 0x27, 1, 0, 2, 0x50, 0x1234, "hatch-tick-gate-set-mode2-fc1"),
    (20, 30, 1, 0x00, 1, 0x27, 1, 0, 4, 0x50, 0x1234, "hatch-tick-gate-set-mode4-getnewmode"),
    (20, 30, 1, 0x00, 2, 0x27, 0, 4000, 4, 0x50, 0x1234, "hatch-tick-roll-favors-hatch"),
    (20, 30, 1, 0x00, 3, 0x27, 0, 0, 4, 0x50, 0x1234, "hatch-tick-roll-fails-reset-counter"),
])
def test_simeggb_state_diff_matches_asm(x, y, ac82, mask_flags, slot, caste,
                                        gate_9fce, threshold_9c78, mode_8a56,
                                        tbl_direct, seed_val, label):
    from simant.recovered.gameplay import sim_egg_b
    results = _run_and_diff_segs(
        6, 0x3CA0, (x, y),
        lambda d, s, p: sim_egg_b(d, s, p, x, y),
        _SIMEGG_REGIONS,
        seed_fn=_simeggb_seed(seed_val, ac82, mask_flags, slot, caste,
                              gate_9fce, threshold_9c78, mode_8a56, tbl_direct))
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _SIMEGG_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _SimEggR (seg6:62A6) — red nest egg/larva growth tick, genuinely -----
# NOT symmetric with _SimEggB — no gate, always hatches via a table lookup.
def _simeggr_seed(seed_val, ac84, mask_flags, slot, caste, task_7690,
                  table_bytes):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(dg, 0xAC84, ac84)
        m.mem.ww(pack, 0x75FC, mask_flags)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, 0x46E6 + slot, caste)
        m.mem.ww(pack, 0x7690, task_7690)
        for i in range(56):
            m.mem.wb(sdg, 0x897E + i, table_bytes)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, 0x50)   # get_new_mode_r's direct-lookup table
    return seed


@pytest.mark.parametrize(
    "x,y,ac84,mask_flags,slot,caste,task_7690,table_bytes,seed_val,label", [
    (20, 30, 1, 0x1F, 0, 0x25, 3, 4, 0x1234, "bitmask-blocks-noop"),
    (20, 30, 1, 0x00, 0, 0x21, 3, 4, 0x1234, "increment-not-a-hatch-tick"),
    (20, 30, 1, 0x00, 1, 0x27, 3, 4, 0x1234, "hatch-tick-unconditional"),
    (20, 30, 2, 0x00, 2, 0x27, 3, 4, 0x1234, "hatch-tick-other-ac84-mask"),
])
def test_simeggr_state_diff_matches_asm(x, y, ac84, mask_flags, slot, caste,
                                        task_7690, table_bytes, seed_val, label):
    from simant.recovered.gameplay import sim_egg_r
    results = _run_and_diff_segs(
        6, 0x62A6, (x, y),
        lambda d, s, p: sim_egg_r(d, s, p, x, y),
        _SIMEGG_REGIONS,
        seed_fn=_simeggr_seed(seed_val, ac84, mask_flags, slot, caste,
                              task_7690, table_bytes))
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _SIMEGG_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _SimQueenA (seg6:0A74) — yard queen tick, may vanish into the nest ---
_SIMQUEENA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x68E8, 0x78E8),   # yard life plane
    (_SDG, 0, 0x3800),                      # compass tables + A-list fields
    (_PACK, 0x80E0, 0x8100),                # covers [0x80F0] (A-list count)
]
_QDX8 = [0, 1, 1, 1, 0, 0xFF, 0xFF, 0xFF]
_QDY8 = [0xFF, 0xFF, 0, 1, 1, 1, 0, 0xFF]


@pytest.mark.parametrize("slot,x,y,caste,ant_count,neighbor_tile,label", [
    (5, 20, 20, 0x81, 0, 0, "low 7 bits <= 0x67 -> stamp only, no check"),
    (5, 20, 20, 0x70, 0, 0x68, "marker intact (tile == caste-8) -> no vanish"),
    (5, 20, 20, 0x70, 1, 0x00, "no marker match, but an ant is there -> no vanish"),
    (5, 20, 20, 0x70, 0, 0x00, "no marker match, no ant -> vanishes"),
])
def test_simqueena_state_diff_matches_asm(slot, x, y, caste, ant_count,
                                          neighbor_tile, label):
    from simant.recovered.gameplay import sim_queen_a
    dir_idx = caste & 7
    nx, ny = x + _QDX8[dir_idx], y + _QDY8[dir_idx]

    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.wb(sdg, 0x23A4 + slot, x)
        m.mem.wb(sdg, 0x278E + slot, y)
        m.mem.wb(sdg, 0x2F62 + slot, caste)
        for i in range(8):
            m.mem.wb(sdg, i, _QDX8[i])
            m.mem.wb(sdg, 8 + i, _QDY8[i])
        m.mem.wb(dg, 0x68E8 + (nx << 6) + ny, neighbor_tile)
        m.mem.ww(pack, 0x80F0, ant_count)
        if ant_count:
            m.mem.wb(sdg, 0x23A4, nx)
            m.mem.wb(sdg, 0x278E, ny)
            m.mem.wb(sdg, 0x2F62, 1)

    results = _run_and_diff_segs(
        6, 0x0A74, (slot,),
        lambda d, s, p: sim_queen_a(d, s, p, slot),
        _SIMQUEENA_REGIONS, near=True, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _SIMQUEENA_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _BuildAntListA (seg5:3046) — rebuild the whole yard A-list -----------
# Scans all 128x64 yard cells; kept sparse (mostly-empty grid) so the real
# ASM run stays well inside the 200k-instruction budget.
_BUILDANTLISTA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x68E8, 0x88E8),   # yard life plane (128x64, read-only here)
    (_SDG, 0x2300, 0x3800),                 # covers 0x23A4/278E/2B78/2F62/334C+slot
    (_PACK, 0x80E0, 0x8100),                # covers [0x80F0] (count)
]


def test_buildantlista_state_diff_matches_asm():
    from simant.recovered.gameplay import build_ant_list_a
    occupied = {
        (0, 0): 0x05,
        (0x7F, 0x3F): 0x81,
        (50, 30): 0xFE,        # yellow-ant sentinel -> must be skipped
        (10, 10): 0xFF,        # yellow-ant sentinel -> must be skipped
        (60, 40): 0x33,
    }

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.data[dg + 0x68E8:dg + 0x88E8] = bytes(0x2000)
        for (x, y), tile in occupied.items():
            m.mem.wb(dg, 0x68E8 + (x << 6) + y, tile)
        m.mem.ww(pack, 0x80F0, 0x11)   # pre-existing (stale) count, must be reset

    results = _run_and_diff_segs(
        5, 0x3046, (),
        lambda d, s, p: build_ant_list_a(d, s, p),
        _BUILDANTLISTA_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _BUILDANTLISTA_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _LostHeadA (seg6:0B1E) — yard trail-head marker occupancy check ------
# Pure predicate: no mutation at all, so the recovered call safely reuses
# `_run_and_get_ax`'s own (unmutated) machine directly.
_DX8 = [0, 1, 1, 1, 0, 0xFF, 0xFF, 0xFF]
_DY8 = [0xFF, 0xFF, 0, 1, 1, 1, 0, 0xFF]
_DX8_S = [d - 0x100 if d & 0x80 else d for d in _DX8]
_DY8_S = [d - 0x100 if d & 0x80 else d for d in _DY8]
@pytest.mark.parametrize("tile_matches,ant_present,label", [
    (True, False, "tile matches the marker -> 0, no A-list check needed"),
    (False, True, "tile changed, but the ant is still there -> 0"),
    (False, False, "tile changed, no ant found -> 1 (lost)"),
])
def test_losthead_a_matches_asm(tile_matches, ant_present, label):
    from simant.recovered.gameplay import lost_head_a
    x, y, direction = 20, 20, 11
    dir_idx = direction & 7
    nx, ny = x + _DX8_S[dir_idx], y + _DY8_S[dir_idx]
    marker = (direction - 8) & 0xFF

    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        m.mem.wb(dg, 0x68E8 + (nx << 6) + ny,
                 marker if tile_matches else 0)
        m.mem.ww(pack, 0x80F0, 1 if ant_present else 0)
        if ant_present:
            m.mem.wb(sdg, 0x23A4, nx)
            m.mem.wb(sdg, 0x278E, ny)
            m.mem.wb(sdg, 0x2F62, 1)

    ax, m = _run_and_get_ax(6, 0x0B1E, (x, y, direction), seed_fn=seed, near=True)
    dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                     m.seg_bases[_PACK])
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    rec_ax = lost_head_a(dgroup_view, sdg_view, pack_view, x, y, direction)

    assert ax == (rec_ax & 0xFFFF), label


# ---- _LostHeadB / _LostHeadR (seg6:42DE / 6790) — NEST trail-head marker --
_DX8_S = [d - 0x100 if d & 0x80 else d for d in _DX8]
_DY8_S = [d - 0x100 if d & 0x80 else d for d in _DY8]


@pytest.mark.parametrize("colony,seg,off,life_base,count_off,arrays", [
    ("B", 6, 0x42DE, 0x88E8, 0x99D4, (0x3736, 0x392C, 0x3D18)),
    ("R", 6, 0x6790, 0x98E8, 0x72CC, (0x4104, 0x42FA, 0x46E6)),
])
@pytest.mark.parametrize("tile_matches,ant_present,label", [
    (True, False, "tile matches the marker -> 0, no A-list check needed"),
    (False, True, "tile changed, but the ant is still there -> 0"),
    (False, False, "tile changed, no ant found -> 1 (lost)"),
])
def test_losthead_br_matches_asm(colony, seg, off, life_base, count_off,
                                 arrays, tile_matches, ant_present, label):
    from simant.recovered.gameplay import lost_head_b, lost_head_r
    fn = lost_head_b if colony == "B" else lost_head_r
    x, y, direction = 20, 20, 11
    dir_idx = direction & 7
    nx, ny = x + _DX8_S[dir_idx], y + _DY8_S[dir_idx]
    marker = (direction - 8) & 0xFF
    y_off, x_off, caste_off = arrays

    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        m.mem.wb(dg, life_base + (nx << 6) + ny, marker if tile_matches else 0)
        m.mem.ww(pack, count_off, 1 if ant_present else 0)
        if ant_present:
            m.mem.wb(sdg, y_off, nx)
            m.mem.wb(sdg, x_off, ny)
            m.mem.wb(sdg, caste_off, marker)

    ax, m = _run_and_get_ax(seg, off, (x, y, direction), seed_fn=seed)
    dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                     m.seg_bases[_PACK])
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    rec_ax = fn(dgroup_view, sdg_view, pack_view, x, y, direction)

    assert ax == (rec_ax & 0xFFFF), f"{colony} {label}"


# ---- _LostTailB / _LostTailR (seg6:433C / 67EE) — NEST trail-tail marker --
@pytest.mark.parametrize("colony,seg,off,life_base,count_off,arrays", [
    ("B", 6, 0x433C, 0x88E8, 0x99D4, (0x3736, 0x392C, 0x3D18)),
    ("R", 6, 0x67EE, 0x98E8, 0x72CC, (0x4104, 0x42FA, 0x46E6)),
])
@pytest.mark.parametrize("tile_matches,ant_present,label", [
    (True, False, "tile matches the marker -> 0, no A-list check needed"),
    (False, True, "tile changed, but the ant is still there -> 0"),
    (False, False, "tile changed, no ant found -> 1 (lost)"),
])
def test_losttail_br_matches_asm(colony, seg, off, life_base, count_off,
                                 arrays, tile_matches, ant_present, label):
    from simant.recovered.gameplay import lost_tail_b, lost_tail_r
    fn = lost_tail_b if colony == "B" else lost_tail_r
    x, y, direction = 20, 20, 11
    dir_idx = (direction ^ 4) & 7
    nx, ny = x + _DX8_S[dir_idx], y + _DY8_S[dir_idx]
    marker = (direction + 8) & 0xFF
    y_off, x_off, caste_off = arrays

    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        m.mem.wb(dg, life_base + (nx << 6) + ny, marker if tile_matches else 0)
        m.mem.ww(pack, count_off, 1 if ant_present else 0)
        if ant_present:
            m.mem.wb(sdg, y_off, nx)
            m.mem.wb(sdg, x_off, ny)
            m.mem.wb(sdg, caste_off, marker)

    ax, m = _run_and_get_ax(seg, off, (x, y, direction), seed_fn=seed)
    dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                     m.seg_bases[_PACK])
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    rec_ax = fn(dgroup_view, sdg_view, pack_view, x, y, direction)

    assert ax == (rec_ax & 0xFFFF), f"{colony} {label}"


# ---- _StartFightA (seg6:266A) — initiate yard combat ----------------------
# NEAR call/return. Composes the already-recovered _FindInAList, _GetWinner,
# and _AlarmHere2.
_STARTFIGHTA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x68E8, 0xAE40),   # yard life plane + GetWinner's tables/RAND_STATE
    (_SDG, 0x2300, 0x8B00),                 # A-list fields + ALARM grid [0x52D2..) + cheat flag [0x8A5C]
    (_PACK, 0x7900, 0xA100),   # A-list count [0x80F0] + GetWinner's win-count stats
]


def _startfighta_seed(slot1, x1, y1, x2, y2, caste1, has_target, slot2=0x30,
                      caste2=0x08, cheat_flag=0, rand_state=0,
                      strength_tbl=None, outcome_tbl=None):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        count = slot2 + 1
        for i in range(count):
            m.mem.wb(sdg, 0x2F62 + i, 0)
        m.mem.wb(sdg, 0x2F62 + slot1, caste1)
        m.mem.wb(sdg, 0x23A4 + slot1, x1)
        m.mem.wb(sdg, 0x278E + slot1, y1)
        m.mem.ww(pack, 0x80F0, count)
        if has_target:
            m.mem.wb(sdg, 0x23A4 + slot2, x2)
            m.mem.wb(sdg, 0x278E + slot2, y2)
            m.mem.wb(sdg, 0x2F62 + slot2, caste2)
        m.mem.wb(sdg, 0x8A5C, cheat_flag)
        m.mem.ww(dg, RAND_STATE_OFF, rand_state & 0xFFFF)
        m.mem.ww(dg, (RAND_STATE_OFF + 2) & 0xFFFF, (rand_state >> 16) & 0xFFFF)
        for sub, v in (strength_tbl or {}).items():
            m.mem.wb(dg, 0x8902 + sub, v)
        for idx, v in (outcome_tbl or {}).items():
            m.mem.wb(dg, 0x8918 + idx, v)
    return seed


@pytest.mark.parametrize(
    "has_target,caste1,caste2,cheat_flag,rand_state,strength_tbl,outcome_tbl", [
    (False, 0x08, 0x08, 0, 0, None, None),                     # no target at (x2,y2) -> attacker vanishes only
    (True, 0x08, 0x88, 1, 0, None, None),                       # cheat gate
    (True, 0x08, 0x88, 0, 0, {1: 1}, {5: 5}),                     # roll(0)=8 >= outcome=5 -> attacker(caste1) wins
    (True, 0x08, 0x88, 0, 1, {1: 1}, {5: 5}),                     # roll(1)=1 <  outcome=5 -> defender(caste2) wins
])
def test_startfighta_state_diff_matches_asm(has_target, caste1, caste2,
                                            cheat_flag, rand_state,
                                            strength_tbl, outcome_tbl):
    from simant.recovered.gameplay import start_fight_a
    slot1, x1, y1, x2, y2 = 0x10, 5, 5, 20, 20
    results = _run_and_diff_segs(
        6, 0x266A, (slot1, x1, y1, x2, y2),
        lambda d, s, p: start_fight_a(d, s, p, slot1, x1, y1, x2, y2),
        _STARTFIGHTA_REGIONS, near=True,
        seed_fn=_startfighta_seed(slot1, x1, y1, x2, y2, caste1, has_target,
                                  caste2=caste2, cheat_flag=cheat_flag,
                                  rand_state=rand_state,
                                  strength_tbl=strength_tbl,
                                  outcome_tbl=outcome_tbl))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _STARTFIGHTA_REGIONS):
        assert asm_after == rec_after, (
            f"has_target={has_target} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _GoInNest (seg6:257A) — move a yard ant into a colony's nest ---------
# NEAR call/return. Composes the already-recovered compact_list_b/r,
# add_ant_to_b_list/r_list, and (not exercised here -- covered by dig_tile_b/
# r's own dedicated tests) dig_tile_b/r.
_GOINNEST_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x68E8, 0xA8E8),   # yard life plane 0 + nest life planes 2+3
    (_SDG, 0x2300, 0x8400),                 # slot fields, both list arrays, both exit-maps
    (_PACK, 0x7200, 0x9A00),                # both colonies' list counts
]


def _goinnest_seed(x, y, slot, count_off, caste, field_c, field_e,
                   full=False):
    def seed(m):
        sdg, pack = m.seg_bases[_SDG], m.seg_bases[_PACK]
        m.mem.wb(sdg, 0x2F62 + slot, caste)
        m.mem.wb(sdg, 0x2B78 + slot, field_c)
        m.mem.wb(sdg, 0x334C + slot, field_e)
        if full:
            m.mem.ww(pack, count_off, 0x1F4)
            for i in range(0x1F4):
                m.mem.wb(sdg, 0x2F62 + i, 0xFF)   # every slot alive -> compaction frees nothing
                m.mem.wb(sdg, 0x46E6 + i, 0xFF)   # (also cover the R-list caste array, same range)
            m.mem.wb(sdg, 0x2F62 + slot, caste)   # restore the acting ant's own record
        else:
            m.mem.ww(pack, count_off, 5)
        m.mem.wb(sdg, 0x82D2 + y, 0)   # exit-map cell clear -> never triggers dig_tile_b/r here
        m.mem.wb(sdg, 0x8312 + y, 0)
    return seed


@pytest.mark.parametrize("x,y,slot,count_off,caste,field_c,field_e,full", [
    (0x10, 20, 0x10, 0x99D4, 0x81, 3, 7, False),   # black colony, room available
    (0x50, 20, 0x10, 0x72CC, 0x02, 5, 9, False),   # red colony, room available
    (0x10, 20, 0x10, 0x99D4, 0x81, 3, 7, True),    # black colony, still full after compaction -> no-op
])
def test_goinnest_state_diff_matches_asm(x, y, slot, count_off, caste,
                                         field_c, field_e, full):
    from simant.recovered.gameplay import go_in_nest
    results = _run_and_diff_segs(
        6, 0x257A, (x, y, slot),
        lambda d, s, p: go_in_nest(d, s, p, x, y, slot),
        _GOINNEST_REGIONS, near=True,
        seed_fn=_goinnest_seed(x, y, slot, count_off, caste, field_c, field_e,
                               full=full))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _GOINNEST_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} full={full} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _DoRestAnt (seg6:0B76) — a yard ant on a rest spot heads into the ----
# nest, or has a 1-in-4 chance of getting stuck resting.  Composes
# is_valid_a + go_in_nest; widens _GOINNEST_REGIONS's DGROUP bound to also
# cover the yard map tile read and the SRand seed.
_DORESTANT_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0x2300, 0x8400),
    (_PACK, 0x7200, 0x9C00),
]


def _dorestant_seed(x, y, slot, inside, tile, seed_val, count_off):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.wb(sdg, 0x23A4 + slot, x)
        m.mem.wb(sdg, 0x278E + slot, y)
        m.mem.wb(sdg, 0x2F62 + slot, 0x81)
        m.mem.wb(sdg, 0x2B78 + slot, 3)
        m.mem.wb(sdg, 0x334C + slot, 7)
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        if 0 <= x <= 0x7F and 0 <= y <= 0x3F:
            m.mem.wb(dg, 0x28E8 + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(sdg, 0x85FC, 0)   # keep the omitted UI-balloon path a clean no-op
        m.mem.ww(pack, count_off, 5)
        m.mem.wb(sdg, 0x82D2 + y, 0)
        m.mem.wb(sdg, 0x8312 + y, 0)
    return seed


@pytest.mark.parametrize("x,y,slot,inside,tile,seed_val,count_off,label", [
    (0x10, 20, 0x10, False, 0x50, 0x1234, 0x99D4, "outside-rest-spot-go-in-nest"),
    (0x50, 20, 0x10, True, 0x85, 0x1234, 0x72CC, "inside-rest-band-go-in-nest"),
    (0x10, 20, 0x10, False, 0x40, 0x0000, 0x99D4, "no-rest-spot-roll4-zero-rest"),
    (0x10, 20, 0x10, False, 0x40, 0x0001, 0x99D4, "no-rest-spot-roll4-nonzero-noop"),
    (0x90, 20, 0x10, False, 0x40, 0x0001, 0x99D4, "x-out-of-range-invalid"),
])
def test_dorestant_state_diff_matches_asm(x, y, slot, inside, tile, seed_val,
                                          count_off, label):
    from simant.recovered.gameplay import do_rest_ant
    results = _run_and_diff_segs(
        6, 0xB76, (slot,),
        lambda d, s, p: do_rest_ant(d, s, p, slot),
        _DORESTANT_REGIONS, near=True,
        seed_fn=_dorestant_seed(x, y, slot, inside, tile, seed_val, count_off))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DORESTANT_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _DoRepoFly (seg6:0D4A) — a yard ant departs on a reproductive flight -
_DOREPOFLY_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # yard map/life + AC8C/AC8E + SRand seed
    (_SDG, 0x2300, 0x3800),                  # A-list slot fields
    (_PACK, 0x7200, 0x9E00),                 # 807A/9C26/80B4
]


def _dorepofly_seed(is_red, x, y, seed_val, count, gate_80b4):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(sdg, 0x2F62 + 0, 0x81 if is_red else 0x01)
        m.mem.wb(sdg, 0x23A4 + 0, x)
        m.mem.wb(sdg, 0x278E + 0, y)
        m.mem.ww(pack, 0x9C26 if is_red else 0x807A, count)
        m.mem.ww(pack, 0x80B4, gate_80b4)
        m.mem.ww(dg, 0xAC8C, 10)
        m.mem.ww(dg, 0xAC8E, 20)
    return seed


@pytest.mark.parametrize("is_red", [False, True])
@pytest.mark.parametrize("seed_val,count,gate_80b4,label", [
    (0x0001, 5, 2, "roll32-nonzero-noop"),
    (0x0000, 50, 2, "count-at-cap-noop"),
    (0x0000, 5, 0, "vanish-only-gate-fails"),
    (0x4000, 5, 2, "vanish-plus-count-no-milestone"),
    (0x0000, 5, 2, "vanish-plus-count-plus-milestone"),
])
def test_dorepofly_state_diff_matches_asm(is_red, seed_val, count, gate_80b4, label):
    from simant.recovered.gameplay import do_repo_fly
    x, y = 20, 25
    results = _run_and_diff_segs(
        6, 0xD4A, (0,),
        lambda d, s, p: do_repo_fly(d, s, p, 0),
        _DOREPOFLY_REGIONS, near=True,
        seed_fn=_dorepofly_seed(is_red, x, y, seed_val, count, gate_80b4))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DOREPOFLY_REGIONS):
        assert asm_after == rec_after, (
            f"is_red={is_red} {label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _DoReturnFoodAnt (seg6:1CB4) — a food-carrying ant heads for its nest
# Composes is_valid_a, go_in_nest, get_nest_dir, jam_scent_bt/rt.
_DORETURNFOODANT_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0, 0x9000),
    (_PACK, 0x7200, 0xA000),
]


def _doreturnfoodant_seed(x, y, caste, field_e, at_entrance, inside,
                          dest_tile, threshold, target_x, target_y,
                          count_off, alist_count):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.wb(sdg, 0x23A4, x)
        m.mem.wb(sdg, 0x278E, y)
        m.mem.wb(sdg, 0x2F62, caste)
        m.mem.wb(sdg, 0x334C, field_e)
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        if at_entrance:
            m.mem.wb(dg, 0x28E8 + (x << 6) + y, 0x50 if not inside else 0x80)
        else:
            m.mem.wb(dg, 0x28E8 + (x << 6) + y, 0x00)
        m.mem.ww(pack, count_off, alist_count)
        # get_nest_dir: clear the whole NEST scent grid so the "no scent"
        # get_dir-toward-target branch is deterministic, and set the target.
        nest_base = 0x72D2 if caste & 0x80 else 0x62D2
        m.mem.data[sdg + nest_base:sdg + nest_base + 0x800] = bytes(0x800)
        tx_off, ty_off = (0x835E, 0x8360) if caste & 0x80 else (0x835A, 0x835C)
        m.mem.ww(sdg, tx_off, target_x & 0xFFFF)
        m.mem.ww(sdg, ty_off, target_y & 0xFFFF)
        # the resulting destination cell's tile + crowding threshold
        m.mem.ww(pack, 0x7604, threshold)
        for dxo in range(-2, 3):
            for dyo in range(-2, 3):
                nx, ny = x + dxo, y + dyo
                if 0 <= nx <= 0x7F and 0 <= ny <= 0x3F:
                    m.mem.wb(dg, 0x28E8 + (nx << 6) + ny, dest_tile)
    return seed


@pytest.mark.parametrize(
    "x,y,caste,field_e,at_entrance,inside,dest_tile,threshold,target_x,"
    "target_y,label", [
    (20, 25, 0x03, 0, True, False, 0x00, 0, 0, 0, "outside-nest-entrance"),
    (20, 25, 0x03, 0, False, False, 0x10, 0x30, 40, 40, "move-no-field-e"),
    (20, 25, 0x03, 5, False, False, 0x10, 0x30, 40, 40, "move-black-jam"),
    (20, 25, 0x83, 5, False, False, 0x10, 0x30, 40, 40, "move-red-jam"),
    (20, 25, 0x03, 0, False, False, 0x50, 0x05, 40, 40, "crowded-jitter-in-place"),
])
def test_doreturnfoodant_state_diff_matches_asm(
        x, y, caste, field_e, at_entrance, inside, dest_tile, threshold,
        target_x, target_y, label):
    from simant.recovered.gameplay import do_return_food_ant
    results = _run_and_diff_segs(
        6, 0x1CB4, (0,),
        lambda d, s, p: do_return_food_ant(d, s, p, 0),
        _DORETURNFOODANT_REGIONS, near=True,
        seed_fn=_doreturnfoodant_seed(
            x, y, caste, field_e, at_entrance, inside, dest_tile, threshold,
            target_x, target_y, 0x80F0, 5))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _DORETURNFOODANT_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _RandTurn (seg6:2A22) — purely random caste-mode-table direction -----
# Pure(ish): its only mutation is the SRand LFSR seed, same pattern as
# `_Bounce`/`_GetForageDir`.
@pytest.mark.parametrize("caste_low3,seed_val", [
    (0, 0x1234), (5, 0x1234), (7, 0xABCD),
])
def test_randturn_matches_asm(caste_low3, seed_val):
    from simant.recovered.gameplay import rand_turn

    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        m.mem.ww(dg, 0xCBF2, seed_val)
        for i in range(64):
            m.mem.wb(sdg, 0x24 + i, i % 8)

    ax, m = _run_and_get_ax(6, 0x2A22, (caste_low3,), seed_fn=seed, near=True)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg_view = ByteBackend(m.mem.block(m.seg_bases[_SDG], 0, 0x10000), 0)
    rec_ax = rand_turn(dgroup_view, sdg_view, caste_low3)

    assert ax == (rec_ax & 0xFFFF), f"caste_low3={caste_low3} seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, (
        f"caste_low3={caste_low3} seed={seed_val:#x}: seed mismatch")


# ---- _StealFoodB / _StealFoodR (seg6:48B4 / 6C26) — nibble stored food ----
_STEALFOOD_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x48E8, 0xCBF4),   # nest map planes 2+3 through the SRand seed
    (_PACK, 0x7200, 0x9F00),                # both colonies' food-count stats
]


@pytest.mark.parametrize("colony,seg,off,map_base,count_off", [
    ("B", 6, 0x48B4, 0x48E8, 0x9EA4),
    ("R", 6, 0x6C26, 0x58E8, 0x72DE),
])
@pytest.mark.parametrize("tile,count,seed_val,label", [
    (0x20, 5, 0x1234, "not the full-pile tile -> plain decrement"),
    (0x00, 5, 0x1234, "tile byte 0 -> wraps to 0xFF (no underflow guard)"),
    (0x10, 5, 0x1234, "full-pile tile -> rerolled via _SRand8"),
    (0x20, 0, 0x1234, "count already 0 -> stat stays at the floor"),
])
def test_stealfood_state_diff_matches_asm(colony, seg, off, map_base,
                                          count_off, tile, count, seed_val,
                                          label):
    from simant.recovered.gameplay import steal_food_b, steal_food_r
    fn = steal_food_b if colony == "B" else steal_food_r
    x, y = 10, 20

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(dg, map_base + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, count_off, count)

    results = _run_and_diff_segs(
        seg, off, (x, y),
        lambda d, p: fn(d, p, x, y),
        _STEALFOOD_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _STEALFOOD_REGIONS):
        assert asm_after == rec_after, (
            f"{colony} {label} {label2}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _EatFoodB / _EatFoodR (seg6:4844 / 6BB6) — unconditional food nibble +
# colony-growth trigger. _TryEatFoodB / _TryEatFoodR (seg6:47C6 / 6B38) — the
# SAME, but gated on the tile being in [0x10, 0x13].
@pytest.mark.parametrize("colony,seg,off,map_base,count_off,ant1,ant2,"
                         "timer_off,cap_off", [
    ("B", 6, 0x4844, 0x48E8, 0x9EA4, 0xAC82, 0xAC98, 0x7402, 0xAC86),
    ("R", 6, 0x6BB6, 0x58E8, 0x72DE, 0xAC84, 0xACA4, 0x7C8E, 0xAC88),
])
@pytest.mark.parametrize("tile,ant_sum,timer,cap,label", [
    (0x10, 0, 0, 10, "reroll tile; low ant sum -> growth triggers"),
    (0x12, 0x800, 0, 10, "decrement tile; high ant sum -> no growth"),
    (0x05, 0, 0, 10, "OUTSIDE the food-pile range -> still processed unconditionally"),
    (0x12, 0, 0, 100, "growth would trigger, but cap already at 100"),
])
def test_eatfood_state_diff_matches_asm(colony, seg, off, map_base, count_off,
                                        ant1, ant2, timer_off, cap_off, tile,
                                        ant_sum, timer, cap, label):
    from simant.recovered.gameplay import eat_food_b, eat_food_r
    fn = eat_food_b if colony == "B" else eat_food_r
    x, y = 10, 20

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(dg, map_base + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCBF2, 0x1234)
        m.mem.ww(pack, count_off, 5)
        m.mem.ww(dg, ant1, ant_sum)
        m.mem.ww(dg, ant2, 0)
        m.mem.ww(pack, timer_off, timer)
        m.mem.ww(dg, cap_off, cap)

    results = _run_and_diff_segs(
        seg, off, (x, y),
        lambda d, p: fn(d, p, x, y),
        _STEALFOOD_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _STEALFOOD_REGIONS):
        assert asm_after == rec_after, (
            f"{colony} {label} {label2}: {_first_diff(asm_after, rec_after, lo)}")


@pytest.mark.parametrize("colony,seg,off,map_base,count_off,ant1,ant2,"
                         "timer_off,cap_off", [
    ("B", 6, 0x47C6, 0x48E8, 0x9EA4, 0xAC82, 0xAC98, 0x7402, 0xAC86),
    ("R", 6, 0x6B38, 0x58E8, 0x72DE, 0xAC84, 0xACA4, 0x7C8E, 0xAC88),
])
@pytest.mark.parametrize("tile,label", [
    (0x10, "in range, reroll tile"),
    (0x12, "in range, decrement tile"),
    (0x05, "OUTSIDE the food-pile range -> complete no-op"),
    (0x14, "one past the range -> complete no-op"),
])
def test_tryeatfood_state_diff_matches_asm(colony, seg, off, map_base,
                                           count_off, ant1, ant2, timer_off,
                                           cap_off, tile, label):
    from simant.recovered.gameplay import try_eat_food_b, try_eat_food_r
    fn = try_eat_food_b if colony == "B" else try_eat_food_r
    x, y = 10, 20

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(dg, map_base + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCBF2, 0x1234)
        m.mem.ww(pack, count_off, 5)
        m.mem.ww(dg, ant1, 0)
        m.mem.ww(dg, ant2, 0)
        m.mem.ww(pack, timer_off, 0)
        m.mem.ww(dg, cap_off, 10)

    results = _run_and_diff_segs(
        seg, off, (x, y),
        lambda d, p: fn(d, p, x, y),
        _STEALFOOD_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _STEALFOOD_REGIONS):
        assert asm_after == rec_after, (
            f"{colony} {label} {label2}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _PickupFoodA (seg5:0D18) — yard food pickup, gated on the "inside" ---
# flag pack[0x9B6E] (the same one _DeadAntHere reads) -- a genuine
# _DoForageAnt dependency.
_PICKUPFOODA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # yard tile map + SRand seed
    (_PACK, 0x9B00, 0x9F00),                # inside flag [0x9B6E] + food-count stat [0x9E84]
]


@pytest.mark.parametrize("flag,tile,count,seed_val,label", [
    (0, 0x48, 5, 0x1234, "outside, tile==0x48 -> reroll via _SRand16"),
    (0, 0x50, 5, 0x1234, "outside, other tile -> plain decrement"),
    (1, 0x20, 5, 0x1234, "inside, multiple of 4 -> replaced with (tile-0x18)>>2"),
    (1, 0x21, 5, 0x1234, "inside, not a multiple of 4 -> plain decrement"),
    (1, 0x20, 0, 0x1234, "count already 0 -> stat stays at the floor"),
])
def test_pickupfooda_state_diff_matches_asm(flag, tile, count, seed_val, label):
    from simant.recovered.gameplay import pickup_food_a
    x, y = 10, 20

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(dg, 0x28E8 + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x9B6E, flag)
        m.mem.ww(pack, 0x9E84, count)

    results = _run_and_diff_segs(
        5, 0x0D18, (x, y),
        lambda d, p: pickup_food_a(d, p, x, y),
        _PICKUPFOODA_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _PICKUPFOODA_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _PickupFoodB / _PickupFoodR (seg5:0F40 / 0FA2) — NEST-map food pickup
# gated on [0x10, 0x13], no colony-growth trigger tail (unlike _EatFood*/
# _TryEatFood*).
@pytest.mark.parametrize("colony,seg,off,map_base,count_off", [
    ("B", 5, 0x0F40, 0x48E8, 0x9EA4),
    ("R", 5, 0x0FA2, 0x58E8, 0x72DE),
])
@pytest.mark.parametrize("tile,count,label", [
    (0x10, 5, "reroll tile via _SRand8"),
    (0x12, 5, "in range, decrement tile"),
    (0x05, 5, "OUTSIDE the range -> complete no-op"),
    (0x14, 5, "one past the range -> complete no-op"),
    (0x12, 0, "count already 0 -> stat stays at the floor"),
])
def test_pickupfoodbr_state_diff_matches_asm(colony, seg, off, map_base,
                                             count_off, tile, count, label):
    from simant.recovered.gameplay import pickup_food_b, pickup_food_r
    fn = pickup_food_b if colony == "B" else pickup_food_r
    x, y = 10, 20

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(dg, map_base + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCBF2, 0x1234)
        m.mem.ww(pack, count_off, count)

    results = _run_and_diff_segs(
        seg, off, (x, y),
        lambda d, p: fn(d, p, x, y),
        _STEALFOOD_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _STEALFOOD_REGIONS):
        assert asm_after == rec_after, f"{colony} {label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _PlaceEggB / _PlaceEggR (seg5:1004 / 1068) — place a new egg ---------
# Composes dig_tile_b/r + add_ant_to_b/r_list; reuses dig_tile_b's own
# established seed helper/regions, widened to cover the B/R-list arrays too.
_PLACEEGG_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0, 0x4900),
    (_PACK, 0x7200, 0xA000),
]


@pytest.mark.parametrize("colony,seg,off,count_off", [
    ("B", 5, 0x1004, 0x99D4),
    ("R", 5, 0x1068, 0x72CC),
])
@pytest.mark.parametrize("x,y,count,caste,label", [
    (20, 20, 5, 0x81, "valid position, room available -> placed"),
    (20, 20, 0x1F4, 0x81, "list already at cap -> no-op"),
    (20, 0, 5, 0x81, "y==0 is out of range -> no-op"),
])
def test_placeegg_state_diff_matches_asm(colony, seg, off, count_off, x, y,
                                         count, caste, label):
    from simant.recovered.gameplay import place_egg_b, place_egg_r
    fn = place_egg_b if colony == "B" else place_egg_r

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        idx = (x << 6) + y
        m.mem.wb(dg, 0x48E8 + idx, 0x40)   # not dirt -> dig_tile_b/r's smoothing tail only
        m.mem.wb(dg, 0x58E8 + idx, 0x40)
        m.mem.ww(dg, 0xCBF2, 0x1234)
        m.mem.ww(pack, 0x72C8, 3)
        m.mem.ww(pack, 0x7A56, 2)
        m.mem.ww(pack, 0x8104, 100)
        m.mem.ww(pack, 0x8106, 0)
        m.mem.ww(pack, 0x811A, 200)
        m.mem.ww(pack, 0x811C, 0)
        m.mem.ww(pack, 0x9DDC, 50)
        m.mem.ww(pack, 0x9DDE, 0)
        m.mem.ww(pack, 0x9DE2, 75)
        m.mem.ww(pack, 0x9DE4, 0)
        m.mem.ww(pack, count_off, count)

    results = _run_and_diff_segs(
        seg, off, (x, y, caste),
        lambda d, s, p: fn(d, s, p, x, y, caste),
        _PLACEEGG_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _PLACEEGG_REGIONS):
        assert asm_after == rec_after, f"{colony} {label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _ScanForAnts (seg5:5362) — count occupied cells in a 3x3 block -------
# Pure predicate, no args, no mutation at all.
@pytest.mark.parametrize("word_x,word_y,base_x,base_y,occupied,label", [
    (0x200, 0x200, 0x20, 0x20, [], "nothing occupied -> 0"),
    (0x200, 0x200, 0x20, 0x20, [(0x20, 0x20), (0x21, 0x21), (0x1F, 0x1F)], "3 of 9 occupied"),
    (0x000, 0x200, 0x00, 0x20, [(0x00, 0x20)], "base_x==0 -> west neighbours off-grid, skipped"),
])
def test_scanforants_matches_asm(word_x, word_y, base_x, base_y, occupied, label):
    from simant.recovered.gameplay import scan_for_ants

    def seed(m):
        dg = m.seg_bases[hooks.DG_SEG_INDEX]
        m.mem.ww(dg, 0xAC7C, word_x)
        m.mem.ww(dg, 0xAC7E, word_y)
        for ox in range(-1, 2):
            for oy in range(-1, 2):
                nx, ny = base_x + ox, base_y + oy
                if 0 <= nx <= 0x7F and 0 <= ny <= 0x3F:
                    m.mem.wb(dg, 0x68E8 + (nx << 6) + ny, 0)
        for (nx, ny) in occupied:
            m.mem.wb(dg, 0x68E8 + (nx << 6) + ny, 1)

    ax, m = _run_and_get_ax(5, 0x5362, (), seed_fn=seed)
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    rec_ax = scan_for_ants(dgroup_view)
    assert ax == (rec_ax & 0xFFFF), label


# ---- _GetExitDirB / _GetExitDirR (seg5:119C / 1240) — exit-distance-------
# gradient direction, biased away from `exclude`'s opposite.
@pytest.mark.parametrize("colony,seg,off,map_base,exit_base", [
    ("B", 5, 0x119C, 0x48E8, 0x3A4),
    ("R", 5, 0x1240, 0x58E8, 0x13A4),
])
@pytest.mark.parametrize("x,y,exclude,seed_val,tile,neighbor_dir,neighbor_val,label", [
    (10, 1, 0, 0x1234, 0x18, None, 0, "y=1, tile==0x18 -> fast-path 1"),
    (10, 1, 0, 0x1234, 0x00, None, 0, "y=1, tile!=0x18 -> coin-flip 3/7 (seed A)"),
    (10, 1, 0, 0x8000, 0x00, None, 0, "y=1, tile!=0x18 -> coin-flip 3/7 (seed B)"),
    (20, 20, 0, 0x1234, 0x00, 3, 50, "interior, gradient found"),
    (20, 20, 0, 0x1234, 0x00, None, 0, "interior, no candidate -> 0"),
    (20, 20, 3 ^ 4, 0x1234, 0x00, 3, 50, "interior, gate excludes the only candidate"),
])
def test_getexitdir_matches_asm(colony, seg, off, map_base, exit_base, x, y,
                                exclude, seed_val, tile, neighbor_dir,
                                neighbor_val, label):
    from simant.recovered.gameplay import get_exit_dir_b, get_exit_dir_r
    fn = get_exit_dir_b if colony == "B" else get_exit_dir_r

    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(dg, map_base + (x << 6), tile)
        m.mem.data[sdg + exit_base:sdg + exit_base + 0x1000] = bytes(0x1000)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        if neighbor_dir is not None:
            dx = _DX8[neighbor_dir]
            dy = _DY8[neighbor_dir]
            dx = dx - 0x100 if dx & 0x80 else dx
            dy = dy - 0x100 if dy & 0x80 else dy
            nx, ny = x + dx, y + dy
            m.mem.wb(sdg, exit_base + (nx << 6) + ny, neighbor_val)

    ax, m = _run_and_get_ax(seg, off, (x, y, exclude), seed_fn=seed)
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    asm_seed_after = m.mem.rw(dg, 0xCBF2)

    buf = bytearray(m.mem.block(dg, 0, 0x10000))
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg_view = ByteBackend(m.mem.block(m.seg_bases[_SDG], 0, 0x10000), 0)
    rec_ax = fn(dgroup_view, sdg_view, x, y, exclude)

    assert ax == (rec_ax & 0xFFFF), f"{colony} {label}: seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, f"{colony} {label}: seed mismatch"


# ---- _GetEnterDirB / _GetEnterDirR (seg5:12E4 / 137C) — inverse gradient --
# (toward LOWER exit-distance), biased away from `exclude`'s opposite.
@pytest.mark.parametrize("colony,seg,off,exit_base", [
    ("B", 5, 0x12E4, 0x3A4),
    ("R", 5, 0x137C, 0x13A4),
])
@pytest.mark.parametrize(
    "own_val,exclude,seed_val,neighbor_dir,neighbor_val,label", [
    (50, 0, 0x1234, 3, 20, "neighbor strictly lower -> wins"),
    (50, 0, 0x1234, 3, 80, "neighbor strictly higher -> not selected -> -1"),
    (50, 0, 0x1234, 3, 50, "tie -> coin-flip (seed A)"),
    (50, 0, 0x8000, 3, 50, "tie -> coin-flip (seed B)"),
    (50, 0, 0x1234, 3, 0, "neighbor==0 -> skipped -> -1"),
    (50, 3 ^ 4, 0x1234, 3, 20, "gate excludes the only candidate -> -1"),
])
def test_getenterdir_matches_asm(colony, seg, off, exit_base, own_val,
                                 exclude, seed_val, neighbor_dir,
                                 neighbor_val, label):
    from simant.recovered.gameplay import get_enter_dir_b, get_enter_dir_r
    fn = get_enter_dir_b if colony == "B" else get_enter_dir_r
    x, y = 20, 20

    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.data[sdg + exit_base:sdg + exit_base + 0x1000] = bytes(0x1000)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        m.mem.wb(sdg, exit_base + (x << 6) + y, own_val)
        dx = _DX8[neighbor_dir]
        dy = _DY8[neighbor_dir]
        dx = dx - 0x100 if dx & 0x80 else dx
        dy = dy - 0x100 if dy & 0x80 else dy
        nx, ny = x + dx, y + dy
        m.mem.wb(sdg, exit_base + (nx << 6) + ny, neighbor_val)

    ax, m = _run_and_get_ax(5, off, (x, y, exclude), seed_fn=seed)
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    asm_seed_after = m.mem.rw(dg, 0xCBF2)

    buf = bytearray(m.mem.block(dg, 0, 0x10000))
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg_view = ByteBackend(m.mem.block(m.seg_bases[_SDG], 0, 0x10000), 0)
    rec_ax = fn(dgroup_view, sdg_view, x, y, exclude)

    assert ax == (rec_ax & 0xFFFF), f"{colony} {label}: seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, f"{colony} {label}: seed mismatch"


# ---- _CanBeHouseHole (seg5:1CBA) — house-hole tile lookup, no calls -------
@pytest.mark.parametrize("dy", [0, 1, 2, 3, 4, 0x5D, 0x5E, 0x60, 0x61, 0x62,
                                0x65, 0x66, 0x67, 0x68, 0x69, 0x100])
def test_canbehousehole_matches_asm(dy):
    from simant.recovered.gameplay import can_be_house_hole
    ax, m = _run_and_get_ax(5, 0x1CBA, (dy,))
    assert ax == (can_be_house_hole(dy) & 0xFFFF), f"dy={dy:#x}"


# ---- _HoleBorder (seg5:1F8E) — border a new hole's 8 neighbors ------------
_HOLEBORDER_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x230C, 0x38E8),   # HOLE_EDGE_TILES (read-only) + yard tile map
    (_SDG, 0, 0x20),                        # compass tables
]


@pytest.mark.parametrize("tile_at_neighbors,label", [
    (0x40, "all neighbours soft (<0x50) -> all bordered"),
    (0x50, "all neighbours already >=0x50 -> none touched"),
])
def test_holeborder_state_diff_matches_asm(tile_at_neighbors, label):
    from simant.recovered.gameplay import hole_border
    x, y = 20, 20

    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
            dx = _DX8[i]
            dy = _DY8[i]
            dx = dx - 0x100 if dx & 0x80 else dx
            dy = dy - 0x100 if dy & 0x80 else dy
            m.mem.wb(dg, 0x28E8 + ((x + dx) << 6) + (y + dy), tile_at_neighbors)

    results = _run_and_diff_segs(
        5, 0x1F8E, (x, y),
        lambda d, s: hole_border(d, s, x, y),
        _HOLEBORDER_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _HOLEBORDER_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _DoFightA (seg6:27E6) — yard combat resolution (first top-level -----
# `_Do*Ant*` behavior routine recovered) -------------------------------------
# NEAR call/return. `_FightBalloons` (ANTEDIT seg3:0x499A, presentation-only
# speech-balloon UI) is stubbed -- the recovered function omits it entirely.
_DOFIGHTA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # life plane (via _DeadAntHere's window) + SRand seed
    (_SDG, 0x2300, 0x8B00),                 # A-list fields [0x23A4.."0x334C]+slot/acting_slot + GetNewMode tables
    (_PACK, 0x7600, 0xA000),   # GetNewMode's pack fields + acting-slot ptr [0x9B6A] + _DeadAntHere ring buffer
]
_FIGHTBALLOONS_STUB = [(3, 0x499A)]


def _dofighta_seed(slot, acting_slot, a_x, a_y, caste_init, field_e, seed_val,
                   mode_base_hi=2, mode_base_lo=3, gate_flag=0, tbl2=0x25,
                   tbl6=0x30, tbl_direct=0x40, tbl_word=0x1122,
                   dead_counter=5, dead_old_x=1, dead_old_y=1, dead_old_tile=0x14,
                   dead_inside=False):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(sdg, 0x23A4 + slot, a_x)
        m.mem.wb(sdg, 0x278E + slot, a_y)
        m.mem.wb(sdg, 0x2F62 + slot, caste_init)
        m.mem.wb(sdg, 0x334C + slot, field_e)
        m.mem.wb(sdg, 0x2B78 + acting_slot, 0x11)   # sentinel -- must change only on a kill
        m.mem.ww(pack, 0x9B6A, acting_slot)
        m.mem.ww(pack, 0x7690, mode_base_hi)
        m.mem.ww(pack, 0x9B8A, mode_base_lo)
        m.mem.ww(pack, 0x9FCE, gate_flag)
        for i in range(8):
            m.mem.wb(sdg, 0x89E6 + ((mode_base_hi << 3) + i), tbl2)
            m.mem.wb(sdg, 0x89E6 + ((mode_base_lo << 3) + i), tbl2)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_hi << 3) + i), tbl6)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_lo << 3) + i), tbl6)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, tbl_direct)
        m.mem.ww(sdg, 0x8A58, tbl_word)
        m.mem.ww(pack, 0x9EA8, dead_counter)
        next_slot = (dead_counter + 1) % 0x64
        m.mem.wb(pack, 0x9C82 + next_slot, dead_old_x)
        m.mem.ww(pack, 0x9D76 + next_slot, dead_old_y)
        m.mem.wb(dg, 0x28E8 + (dead_old_x << 6) + dead_old_y, dead_old_tile)
        m.mem.wb(pack, 0x9B6E, 1 if dead_inside else 0)
        m.mem.wb(sdg, 0x85FC, 1)   # exercise the (stubbed) FightBalloons gate too
    return seed


@pytest.mark.parametrize("slot,acting_slot,a_x,a_y,caste_init,field_e,seed_val,"
                         "mode_base_hi,mode_base_lo,gate_flag", [
    (0x10, 0x40, 5, 7, 0x02, 0x00, 0x02, 2, 3, 0),    # SRand16 != 0 -> no kill, no side calls
    (0x10, 0x40, 5, 7, 0x03, 0x08, 0x0C, 2, 3, 0),    # kill, 0x80 clear, sub=1 not in (2,6) -> direct table
    (0x10, 0x40, 10, 12, 0x85, 0x94, 0x0C, 2, 3, 0),  # kill, 0x80 SET, sub=2 -> rolled via mode_base_hi
    (0x10, 0x40, 10, 12, 0x05, 0x36, 0x0C, 2, 3, 1),  # kill, 0x80 clear, gate=1, sub=6 -> rolled via mode_base_lo
])
def test_dofighta_state_diff_matches_asm(slot, acting_slot, a_x, a_y, caste_init,
                                         field_e, seed_val, mode_base_hi,
                                         mode_base_lo, gate_flag):
    from simant.recovered.gameplay import do_fight_a
    results = _run_and_diff_segs(
        6, 0x27E6, (slot,),
        lambda d, s, p: do_fight_a(d, s, p, slot),
        _DOFIGHTA_REGIONS, near=True, stubs=_FIGHTBALLOONS_STUB,
        seed_fn=_dofighta_seed(slot, acting_slot, a_x, a_y, caste_init, field_e,
                               seed_val, mode_base_hi=mode_base_hi,
                               mode_base_lo=mode_base_lo, gate_flag=gate_flag),
        )
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DOFIGHTA_REGIONS):
        assert asm_after == rec_after, (
            f"slot={slot:#x} field_e={field_e:#x} seed_val={seed_val:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _DoNestFightB/_DoNestFightR (seg6:3A54/6072) — nest combat tick ------
# Composes get_new_mode (B) / a direct DGROUP table (R), and
# add_ant_to_b_list/r_list for the corpse-spawn branch.
_DONESTFIGHT_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x22E0, 0xCBF4),   # 0x22E6 table (R) through both nest life planes + SRand seed
    (_SDG, 0, 0x8B00),                       # compass table, B/R-list fields, GetNewMode tables
    (_PACK, 0x7200, 0xA000),                 # slot ptr + GetNewMode fields + A-list counts
]
_NESTFIGHTBALLOONS_STUB = [(3, 0x499A)]


def _donestfight_seed(x, y, slot, caste_off, field_e_off, field_c_off,
                      xfield_off, yfield_off, count_off, caste_init, field_e,
                      seed_val, mode_base_hi=2, mode_base_lo=3, gate_flag=0,
                      tbl2=0x25, tbl6=0x30, tbl_direct=0x40, tbl_word=0x1122):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, caste_off + slot, caste_init)
        m.mem.wb(sdg, field_e_off + slot, field_e)
        m.mem.wb(sdg, xfield_off + slot, 20)
        m.mem.wb(sdg, yfield_off + slot, 30)
        m.mem.ww(pack, count_off, 5)
        m.mem.ww(pack, 0x7690, mode_base_hi)
        m.mem.ww(pack, 0x9B8A, mode_base_lo)
        m.mem.ww(pack, 0x9FCE, gate_flag)
        for i in range(8):
            m.mem.wb(sdg, 0x89E6 + ((mode_base_hi << 3) + i), tbl2)
            m.mem.wb(sdg, 0x89E6 + ((mode_base_lo << 3) + i), tbl2)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_hi << 3) + i), tbl6)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_lo << 3) + i), tbl6)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, tbl_direct)
        m.mem.ww(sdg, 0x8A58, tbl_word)
        m.mem.wb(sdg, 0x85FC, 1)   # exercise the (stubbed) balloon gate too
    return seed


@pytest.mark.parametrize("which,off,map_base,caste_off,field_e_off,field_c_off,"
                         "xfield_off,yfield_off,count_off", [
    ("b", 0x3A54, 0x48E8, 0x3D18, 0x3F0E, 0x3B22, 0x392C, 0x3736, 0x99D4),
    ("r", 0x6072, 0x58E8, 0x46E6, 0x48DC, 0x44F0, 0x42FA, 0x4104, 0x72CC),
])
@pytest.mark.parametrize("x,y,slot,caste_init,field_e,seed_val,label", [
    (20, 25, 0, 0x02, 0x00, 0x01, "roll16-nonzero-noop"),
    (20, 25, 0, 0x03, 0x08, 0x00, "kill-no-corpse-spawn"),
    (20, 25, 1, 0x83, 0x60, 0x00, "kill-corpse-spawn-then-normal-tail"),
    (20, 25, 2, 0x83, 0xE0, 0x00, "kill-corpse-spawn-then-wrong-colony-fallback"),
])
def test_donestfight_state_diff_matches_asm(which, off, map_base, caste_off,
                                            field_e_off, field_c_off, xfield_off,
                                            yfield_off, count_off, x, y, slot,
                                            caste_init, field_e, seed_val, label):
    import simant.recovered.gameplay as G
    fn = G.do_nest_fight_b if which == "b" else G.do_nest_fight_r
    results = _run_and_diff_segs(
        6, off, (x, y),
        lambda d, s, p: fn(d, s, p, x, y),
        _DONESTFIGHT_REGIONS, stubs=_NESTFIGHTBALLOONS_STUB,
        seed_fn=_donestfight_seed(x, y, slot, caste_off, field_e_off, field_c_off,
                                  xfield_off, yfield_off, count_off, caste_init,
                                  field_e, seed_val))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DONESTFIGHT_REGIONS):
        assert asm_after == rec_after, f"{which} {label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _Bounce (seg7:12EC) — yard-edge "bounce back into the map" compass ----
# Pure(ish): its only mutation is the SRand LFSR seed, so (unlike
# `find_in_a_list`'s reuse of `_run_and_get_ax`'s post-execution machine) the
# recovered call needs the PRE-state seed — `m` is already past the ASM's own
# _SRand1 call(s) by the time `_run_and_get_ax` returns.
@pytest.mark.parametrize("x,y,seed_val", [
    (0x00, 0x00, 0x1234),   # top-left corner
    (0x00, 0x00, 0xABCD),
    (0x00, 0x3F, 0x1234),   # bottom-left corner (left-edge branch, n=3)
    (0x00, 0x20, 0x1234),   # left edge, general (n=5)
    (0x7F, 0x00, 0x1234),   # top-right corner
    (0x40, 0x00, 0x1234),   # top edge, general
    (0x7F, 0x3F, 0x1234),   # bottom-right corner
    (0x7F, 0x20, 0x1234),   # right edge, general
    (0x40, 0x3F, 0x1234),   # bottom edge, general
    (0x40, 0x20, 0x1234),   # strictly interior -> 0, no RNG call at all
    (0x01, 0x01, 0x1234),   # interior, adjacent to the top-left corner
    (0x7E, 0x3E, 0x9999),   # interior, adjacent to the bottom-right corner
])
def test_bounce_matches_asm(x, y, seed_val):
    from simant.recovered.gameplay import bounce

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)

    ax, m = _run_and_get_ax(7, 0x12EC, (x, y), seed_fn=seed)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    view = ByteBackend(buf, 0)
    rec_ax = bounce(view, x, y)

    assert ax == (rec_ax & 0xFFFF), f"x={x:#x} y={y:#x} seed={seed_val:#x}"
    assert view.rw(0xCBF2) == asm_seed_after, (
        f"x={x:#x} y={y:#x} seed={seed_val:#x}: seed mismatch")


# ---- _SGIRand/_SGRand/_SGSRand (seg5:147C/14A4/14CC) — two-roll RNG -------
# combinators.  Pure aside from the SRand seed, same pattern as `_Bounce`.
@pytest.mark.parametrize("routine,off,fn_name", [
    ("_SGIRand", 0x147C, "sg_i_rand"),
    ("_SGRand", 0x14A4, "sg_rand"),
    ("_SGSRand", 0x14CC, "sg_s_rand"),
])
@pytest.mark.parametrize("n,seed_val", [
    (4, 0x1234), (8, 0x0001), (10, 0xBEEF), (1, 0x5555), (16, 0x7ACE), (3, 0x0000),
])
def test_sgrand_family_matches_asm(routine, off, fn_name, n, seed_val):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)

    ax, m = _run_and_get_ax(5, off, (n,), seed_fn=seed)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    view = ByteBackend(buf, 0)
    rec_ax = fn(view, n)

    assert ax == (rec_ax & 0xFFFF), f"{routine}: n={n} seed={seed_val:#x} asm={ax:#06x} rec={rec_ax & 0xFFFF:#06x}"
    assert view.rw(0xCBF2) == asm_seed_after, f"{routine}: n={n} seed={seed_val:#x}: seed mismatch"


# ---- _IsItYellow (seg5:96B6) — is the player's yellow ant at (x,y)? -------
def _isityellow_seed(mode, mode9fe8, marker_x, marker_y, tile):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(dg, 0xCE80, mode)
        m.mem.ww(pack, 0x9FE8, mode9fe8)
        m.mem.ww(dg, 0xAC7C, marker_x & 0xFFFF)
        m.mem.ww(dg, 0xAC7E, marker_y & 0xFFFF)
        for plane, base in ((0, 0x68E8), (2, 0x88E8), (3, 0x98E8)):
            for x in range(0, 5):
                for y in range(0, 5):
                    m.mem.wb(dg, base + (x << 6) + y, tile)
    return seed


@pytest.mark.parametrize("colony,x,y,mode,mode9fe8,marker_x,marker_y,tile,label", [
    (1, 2, 2, 2, 0, 0, 0, 0xFE, "mode-mismatch"),            # dgroup[CE80]=2 != colony=1 -> 0
    (0, 2, 2, 1, 0, 0, 0, 0xFE, "colony0-defaults-to-1-tile"),  # colony 0 -> mode check vs 1
    (1, 2, 2, 1, 1, (2 << 4) + 8, (2 << 4) + 8, 0, "distance-close"),   # exact match -> dist 0
    (1, 2, 2, 1, 1, 2000, 2000, 0, "distance-far"),                     # far away -> 0
    (2, 2, 2, 2, 1, 0, 0, 0, "colony-gt1-distance-mode"),   # colony>1 under distance mode -> 0
    (0, 2, 2, 1, 0, 0, 0, 0xFE, "tile-yellow-fe"),
    (0, 2, 2, 1, 0, 0, 0, 0xFF, "tile-yellow-ff"),
    (2, 2, 2, 2, 0, 0, 0, 0xFF, "colony2-plane-tile"),
    (3, 2, 2, 3, 0, 0, 0, 0x50, "colony3-not-yellow"),
])
def test_isityellow_matches_asm(colony, x, y, mode, mode9fe8, marker_x, marker_y,
                                tile, label):
    from simant.recovered.gameplay import is_it_yellow
    ax, m = _run_and_get_ax(
        5, 0x96B6, (colony, x, y),
        seed_fn=_isityellow_seed(mode, mode9fe8, marker_x, marker_y, tile))
    dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    expect = is_it_yellow(dgroup_view, pack_view, colony, x, y)
    assert ax == (expect & 0xFFFF), f"{label}: asm={ax:#06x} rec={expect:#06x}"


# ---- _GetForageDir (seg7:0AB0) — TRAIL-scent gradient direction -----------
# Like `_Bounce`: pure aside from the SRand seed, so the recovered call needs
# a fresh view seeded with the PRE-state seed, not `_run_and_get_ax`'s own
# (post-execution) machine -- but SDG is untouched by this routine, so its
# post-execution state is safe to reuse directly.
def _getforagedir_seed(own_scent, neighbor_dir, neighbor_scent, colony_flag):
    def seed(m):
        sdg = m.seg_bases[_SDG]
        trail_base = 0x7AD2 if colony_flag & 0x80 else 0x6AD2
        m.mem.data[sdg + trail_base:sdg + trail_base + 0x800] = bytes(0x800)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        for i in range(64):
            m.mem.wb(sdg, 0x24 + i, i % 8)
        hx, hy = 20 >> 1, 20 >> 1
        m.mem.wb(sdg, trail_base + (hx << 5) + hy, own_scent)
        if neighbor_dir is not None:
            dx = _DX8[neighbor_dir]
            dy = _DY8[neighbor_dir]
            dx = dx - 0x100 if dx & 0x80 else dx
            dy = dy - 0x100 if dy & 0x80 else dy
            nx, ny = (hx + dx) & 0x3F, (hy + dy) & 0x1F
            m.mem.wb(sdg, trail_base + (nx << 5) + ny, neighbor_scent)
    return seed


@pytest.mark.parametrize("x,y,caste_low3,colony_flag,seed_val", [
    (0x00, 0x00, 3, 0x00, 0x1234),   # TL corner -> fixed 3, no RNG
    (0x7F, 0x3F, 3, 0x00, 0x1234),   # BR corner -> fixed 7, no RNG
    (0x00, 0x20, 3, 0x00, 0x1234),   # general left edge -> SRand1(3)+1
    (0x40, 0x3F, 3, 0x00, 0x1234),   # general bottom edge -> (SRand1(3)-1)&7
])
def test_getforagedir_edges_matches_asm(x, y, caste_low3, colony_flag, seed_val):
    from simant.recovered.gameplay import get_forage_dir

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)

    ax, m = _run_and_get_ax(7, 0xAB0, (x, y, caste_low3, colony_flag), seed_fn=seed)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    rec_ax = get_forage_dir(dgroup_view, None, x, y, caste_low3, colony_flag)

    assert ax == (rec_ax & 0xFFFF), f"x={x:#x} y={y:#x} seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, (
        f"x={x:#x} y={y:#x} seed={seed_val:#x}: seed mismatch")


@pytest.mark.parametrize(
    "own_scent,neighbor_dir,neighbor_scent,colony_flag,seed_val,label", [
    (1, 2, 50, 0x00, 0x1234, "gradient found, colony B"),
    (1, 2, 50, 0x80, 0x1234, "gradient found, colony R"),
    (100, 2, 50, 0x00, 0x1234, "own cell already best -> -1"),
    (0, None, 0, 0x00, 0x1234, "no gradient anywhere -> random fallback"),
])
def test_getforagedir_interior_matches_asm(own_scent, neighbor_dir,
                                           neighbor_scent, colony_flag,
                                           seed_val, label):
    from simant.recovered.gameplay import get_forage_dir
    x, y, caste_low3 = 20, 20, 5

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)
        _getforagedir_seed(own_scent, neighbor_dir, neighbor_scent, colony_flag)(m)

    ax, m = _run_and_get_ax(7, 0xAB0, (x, y, caste_low3, colony_flag), seed_fn=seed)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg = m.seg_bases[_SDG]
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    rec_ax = get_forage_dir(dgroup_view, sdg_view, x, y, caste_low3, colony_flag)

    assert ax == (rec_ax & 0xFFFF), f"{label}: seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, f"{label}: seed mismatch"


# ---- _GetNestDir (seg7:0C30) — NEST-scent gradient / queen-homing direction
# Yard-edge handling is `_Bounce`'s own formula, compiled inline (not a
# call) -- ported as a literal `bounce()` call + the `(r-1)&7` conversion.
def _getnestdir_seed(own_scent, neighbor_dir, neighbor_scent, colony_flag,
                     target_x=None, target_y=None):
    def seed(m):
        sdg = m.seg_bases[_SDG]
        nest_base = 0x72D2 if colony_flag & 0x80 else 0x62D2
        m.mem.data[sdg + nest_base:sdg + nest_base + 0x800] = bytes(0x800)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        for i in range(64):
            m.mem.wb(sdg, 0x24 + i, i % 8)
        hx, hy = 20 >> 1, 20 >> 1
        m.mem.wb(sdg, nest_base + (hx << 5) + hy, own_scent)
        if neighbor_dir is not None:
            dx = _DX8[neighbor_dir]
            dy = _DY8[neighbor_dir]
            dx = dx - 0x100 if dx & 0x80 else dx
            dy = dy - 0x100 if dy & 0x80 else dy
            nx, ny = (hx + dx) & 0x3F, (hy + dy) & 0x1F
            m.mem.wb(sdg, nest_base + (nx << 5) + ny, neighbor_scent)
        if target_x is not None:
            tx_off, ty_off = (0x835E, 0x8360) if colony_flag & 0x80 else (0x835A, 0x835C)
            m.mem.ww(sdg, tx_off, target_x & 0xFFFF)
            m.mem.ww(sdg, ty_off, target_y & 0xFFFF)
    return seed


def _run_getnestdir(x, y, caste_low3, colony_flag, seed_val, seeder):
    from simant.recovered.gameplay import get_nest_dir

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)
        seeder(m)

    ax, m = _run_and_get_ax(7, 0xC30, (x, y, caste_low3, colony_flag), seed_fn=seed)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg = m.seg_bases[_SDG]
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    rec_ax = get_nest_dir(dgroup_view, sdg_view, x, y, caste_low3, colony_flag)

    assert ax == (rec_ax & 0xFFFF), f"seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, f"seed={seed_val:#x}: seed mismatch"


def test_getnestdir_edge_matches_asm():
    _run_getnestdir(0x00, 0x00, 5, 0x00, 0x1234, lambda m: None)   # TL corner


@pytest.mark.parametrize("seed_val", [0x0, 0x8000])   # SRand2 roll = 0, then = 1
def test_getnestdir_gradient_matches_asm(seed_val):
    _run_getnestdir(20, 20, 5, 0x00, seed_val,
                    _getnestdir_seed(1, 3, 50, 0x00))


@pytest.mark.parametrize("colony_flag,seed_val", [(0x00, 0x1), (0x80, 0x1)])
def test_getnestdir_homing_matches_asm(colony_flag, seed_val):
    # own cell has no scent -> get_dir toward the colony's stored target;
    # seed_val=0x1 gives a nonzero _SRand4 roll, so the homing dir is used.
    _run_getnestdir(20, 20, 5, colony_flag, seed_val,
                    _getnestdir_seed(0, None, 0, colony_flag,
                                     target_x=25, target_y=20))


def test_getnestdir_homing_srand4_zero_falls_back_matches_asm():
    _run_getnestdir(20, 20, 5, 0x00, 0x0,   # SRand4 roll == 0 -> fallback
                    _getnestdir_seed(0, None, 0, 0x00, target_x=25, target_y=20))


def test_getnestdir_homing_no_target_falls_back_matches_asm():
    _run_getnestdir(20, 20, 5, 0x00, 0x1234,   # get_dir==0 (target==self) -> fallback
                    _getnestdir_seed(0, None, 0, 0x00, target_x=20, target_y=20))


# ---- _GetAlarmDir (seg7:0E54) — ALARM-scent gradient direction ------------
# No colony argument -- one shared ALARM grid. Edge handling is (like
# _GetNestDir) _Bounce's own formula compiled inline.
def _getalarmdir_seed(neighbor_dir, neighbor_scent):
    def seed(m):
        sdg = m.seg_bases[_SDG]
        m.mem.data[sdg + 0x52D2:sdg + 0x52D2 + 0x800] = bytes(0x800)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        for i in range(64):
            m.mem.wb(sdg, 0x24 + i, i % 8)
        if neighbor_dir is not None:
            hx, hy = 20 >> 1, 20 >> 1
            dx = _DX8[neighbor_dir]
            dy = _DY8[neighbor_dir]
            dx = dx - 0x100 if dx & 0x80 else dx
            dy = dy - 0x100 if dy & 0x80 else dy
            nx, ny = (hx + dx) & 0x3F, (hy + dy) & 0x1F
            m.mem.wb(sdg, 0x52D2 + (nx << 5) + ny, neighbor_scent)
    return seed


def _run_getalarmdir(x, y, caste_low3, seed_val, seeder):
    from simant.recovered.gameplay import get_alarm_dir

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)
        seeder(m)

    ax, m = _run_and_get_ax(7, 0xE54, (x, y, caste_low3), seed_fn=seed)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg = m.seg_bases[_SDG]
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    rec_ax = get_alarm_dir(dgroup_view, sdg_view, x, y, caste_low3)

    assert ax == (rec_ax & 0xFFFF), f"seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, f"seed={seed_val:#x}: seed mismatch"


def test_getalarmdir_edge_matches_asm():
    _run_getalarmdir(0x7F, 0x00, 5, 0x1234, lambda m: None)   # TR corner


def test_getalarmdir_gradient_matches_asm():
    _run_getalarmdir(20, 20, 5, 0x1234, _getalarmdir_seed(6, 40))


def test_getalarmdir_no_scent_falls_back_matches_asm():
    _run_getalarmdir(20, 20, 5, 0x1234, _getalarmdir_seed(None, 0))


# ---- _GetRandDir (seg7:0F72) — purely random direction ---------------------
# Simplest of the seg7 _Get*Dir family: yard-edge `_Bounce` handling, or
# (interior) a fresh _SRand8()-random mode-table pick -- no gradient at all.
def _getranddir_seed(m):
    sdg = m.seg_bases[_SDG]
    for i in range(64):
        m.mem.wb(sdg, 0x24 + i, i % 8)


@pytest.mark.parametrize("x,y,caste_low3,seed_val", [
    (0x00, 0x3F, 5, 0x1234),   # BL corner
    (20, 20, 5, 0x1234),       # interior -> random pick
    (20, 20, 3, 0xABCD),       # interior, different caste row/seed
])
def test_getranddir_matches_asm(x, y, caste_low3, seed_val):
    from simant.recovered.gameplay import get_rand_dir

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)
        _getranddir_seed(m)

    ax, m = _run_and_get_ax(7, 0xF72, (x, y, caste_low3), seed_fn=seed)
    asm_seed_after = m.mem.rw(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2)

    buf = bytearray(0x10000)
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg = m.seg_bases[_SDG]
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    rec_ax = get_rand_dir(dgroup_view, sdg_view, x, y, caste_low3)

    assert ax == (rec_ax & 0xFFFF), f"x={x:#x} y={y:#x} seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, (
        f"x={x:#x} y={y:#x} seed={seed_val:#x}: seed mismatch")


# ---- _GetDefendDir (seg7:1026) — game-mode-switched defend direction ------
# Modes 2/3 delegate wholesale to `_GetNestDir` (a near-call in the ASM);
# mode 1 steers toward a fixed attack marker; any other mode is a no-op.
def _getdefenddir_seed(mode, *, flag=0, ac7c=0, ac7e=0, target_x=0,
                       target_y=0, threshold=0, nest_own_scent=0,
                       nest_neighbor_dir=None, nest_neighbor_scent=0,
                       colony_flag=0x00):
    def seed(m):
        dg, pack, sdg = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK],
                         m.seg_bases[_SDG])
        m.mem.ww(dg, 0xCE80, mode)
        m.mem.ww(pack, 0x72EC, flag)
        m.mem.ww(dg, 0xAC7C, ac7c & 0xFFFF)
        m.mem.ww(dg, 0xAC7E, ac7e & 0xFFFF)
        m.mem.ww(pack, 0x9FE4, target_x & 0xFFFF)
        m.mem.ww(pack, 0x9FEA, target_y & 0xFFFF)
        m.mem.ww(pack, 0x9E7A, threshold & 0xFFFF)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        for i in range(64):
            m.mem.wb(sdg, 0x24 + i, i % 8)
        _getnestdir_seed(nest_own_scent, nest_neighbor_dir,
                         nest_neighbor_scent, colony_flag)(m)
    return seed


def _run_getdefenddir(x, y, caste_low3, seed_val, seeder):
    from simant.recovered.gameplay import get_defend_dir

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)
        seeder(m)

    ax, m = _run_and_get_ax(7, 0x1026, (x, y, caste_low3), seed_fn=seed)
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    asm_seed_after = m.mem.rw(dg, 0xCBF2)

    buf = bytearray(m.mem.block(dg, 0, 0x10000))
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg = m.seg_bases[_SDG]
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    pack = m.seg_bases[_PACK]
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    rec_ax = get_defend_dir(dgroup_view, sdg_view, pack_view, x, y, caste_low3)

    assert ax == (rec_ax & 0xFFFF), f"x={x:#x} y={y:#x} seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, (
        f"x={x:#x} y={y:#x} seed={seed_val:#x}: seed mismatch")


def test_getdefenddir_edge_matches_asm():
    _run_getdefenddir(0x00, 0x00, 5, 0x1234, _getdefenddir_seed(1))   # TL corner


def test_getdefenddir_mode2_delegates_to_getnestdir_matches_asm():
    _run_getdefenddir(20, 20, 5, 0x1234,
                      _getdefenddir_seed(2, target_x=20, target_y=20,
                                         colony_flag=0x00))


def test_getdefenddir_mode3_delegates_to_getnestdir_matches_asm():
    _run_getdefenddir(20, 20, 5, 0x1234,
                      _getdefenddir_seed(3, target_x=20, target_y=20,
                                         colony_flag=0x80))


@pytest.mark.parametrize("mode", [0, 4])
def test_getdefenddir_othermode_echoes_caste_matches_asm(mode):
    _run_getdefenddir(20, 20, 5, 0x1234, _getdefenddir_seed(mode))


def test_getdefenddir_mode1_flag_matches_asm():
    _run_getdefenddir(20, 20, 5, 0x1234,
                      _getdefenddir_seed(1, flag=1, ac7c=25 << 4, ac7e=20 << 4))


def test_getdefenddir_mode1_close_uses_random_matches_asm():
    _run_getdefenddir(20, 20, 5, 0x1234,
                      _getdefenddir_seed(1, flag=0, target_x=20, target_y=20,
                                         threshold=1000))


def test_getdefenddir_mode1_far_uses_getdir_matches_asm():
    _run_getdefenddir(20, 20, 5, 0x1234,
                      _getdefenddir_seed(1, flag=0, target_x=25, target_y=20,
                                         threshold=0))


# ---- _GetRedDefendDir (seg7:1194) — red-colony sibling of _GetDefendDir ----
# Same shape (edge, mode 2/3 -> get_nest_dir, mode 1 -> distance-gated
# geometric branch, other -> echo caste_low3), but the mode selector and
# mode-1 target/threshold are different PACK fields, and mode 1 has no
# attack-marker alternative -- always the geometric branch.
def _getreddefenddir_seed(mode, *, target_x=0, target_y=0, threshold=0,
                          nest_own_scent=0, nest_neighbor_dir=None,
                          nest_neighbor_scent=0, colony_flag=0x00):
    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, 0x7606, mode)
        m.mem.ww(pack, 0x80A6, target_x & 0xFFFF)
        m.mem.ww(pack, 0x80AC, target_y & 0xFFFF)
        m.mem.ww(pack, 0xA08E, threshold & 0xFFFF)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        for i in range(64):
            m.mem.wb(sdg, 0x24 + i, i % 8)
        _getnestdir_seed(nest_own_scent, nest_neighbor_dir,
                         nest_neighbor_scent, colony_flag)(m)
    return seed


def _run_getreddefenddir(x, y, caste_low3, seed_val, seeder):
    from simant.recovered.gameplay import get_red_defend_dir

    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, seed_val)
        seeder(m)

    ax, m = _run_and_get_ax(7, 0x1194, (x, y, caste_low3), seed_fn=seed)
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    asm_seed_after = m.mem.rw(dg, 0xCBF2)

    buf = bytearray(m.mem.block(dg, 0, 0x10000))
    buf[0xCBF2] = seed_val & 0xFF
    buf[0xCBF3] = (seed_val >> 8) & 0xFF
    dgroup_view = ByteBackend(buf, 0)
    sdg = m.seg_bases[_SDG]
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    pack = m.seg_bases[_PACK]
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    rec_ax = get_red_defend_dir(dgroup_view, sdg_view, pack_view, x, y, caste_low3)

    assert ax == (rec_ax & 0xFFFF), f"x={x:#x} y={y:#x} seed={seed_val:#x}"
    assert dgroup_view.rw(0xCBF2) == asm_seed_after, (
        f"x={x:#x} y={y:#x} seed={seed_val:#x}: seed mismatch")


def test_getreddefenddir_edge_matches_asm():
    _run_getreddefenddir(0x7F, 0x3F, 5, 0x1234, _getreddefenddir_seed(1))   # BR corner


def test_getreddefenddir_mode2_delegates_to_getnestdir_matches_asm():
    _run_getreddefenddir(20, 20, 5, 0x1234,
                         _getreddefenddir_seed(2, target_x=20, target_y=20,
                                               colony_flag=0x00))


def test_getreddefenddir_mode3_delegates_to_getnestdir_matches_asm():
    _run_getreddefenddir(20, 20, 5, 0x1234,
                         _getreddefenddir_seed(3, target_x=20, target_y=20,
                                               colony_flag=0x80))


@pytest.mark.parametrize("mode", [0, 4])
def test_getreddefenddir_othermode_echoes_caste_matches_asm(mode):
    _run_getreddefenddir(20, 20, 5, 0x1234, _getreddefenddir_seed(mode))


def test_getreddefenddir_mode1_close_uses_random_matches_asm():
    _run_getreddefenddir(20, 20, 5, 0x1234,
                         _getreddefenddir_seed(1, target_x=20, target_y=20,
                                               threshold=1000))


def test_getreddefenddir_mode1_far_uses_getdir_matches_asm():
    _run_getreddefenddir(20, 20, 5, 0x1234,
                         _getreddefenddir_seed(1, target_x=25, target_y=20,
                                               threshold=0))


# ---- _DoDigOutAntA (seg6:1480) — second top-level `_Do*Ant*` routine -------
# NEAR call/return, composes `_Bounce`, `_GetNewMode`, and (on a successful
# move with a nonzero carried-dirt counter) `_JamScentBN`/`_JamScentRN`.
_DODIGOUTANTA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),   # yard tile map + yard life grid + SRand seed
    (_SDG, 0x0000, 0x8B00),                 # dx/dy + mode tables, A-list fields, NEST scent grids, GetNewMode tables
    (_PACK, 0x7600, 0xA000),                # dig threshold [0x7604] + GetNewMode's pack fields
]
_DX8 = [0, 1, 1, 1, 0, 0xFF, 0xFF, 0xFF]
_DY8 = [0xFF, 0xFF, 0, 1, 1, 1, 0, 0xFF]


def _dodigoutanta_seed(slot, a_x, a_y, caste, seed_val, *, threshold=5,
                       dest_tile=0, dest_life=0, field_e=0):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.data[dg + 0x28E8:dg + 0x38E8] = bytes(0x1000)   # yard tile map -> 0
        m.mem.data[dg + 0x68E8:dg + 0x78E8] = bytes(0x1000)   # yard life grid -> 0
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(sdg, 0x23A4 + slot, a_x)
        m.mem.wb(sdg, 0x278E + slot, a_y)
        m.mem.wb(sdg, 0x2F62 + slot, caste)
        m.mem.wb(sdg, 0x334C + slot, field_e)
        m.mem.wb(sdg, 0x2B78 + slot, 0x11)   # sentinel -- must change only when GetNewMode fires
        m.mem.ww(pack, 0x7604, threshold)
        m.mem.ww(pack, 0x9FCE, 0)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])
        for i in range(64):
            m.mem.wb(sdg, 0x24 + i, i % 8)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, 0x40)
        m.mem.ww(sdg, 0x8A58, 0x1122)
        for i in range(8):
            m.mem.wb(sdg, 0x89E6 + i, 0x25)
            m.mem.wb(sdg, 0x8A16 + i, 0x30)
        m.mem.wb(dg, 0x28E8 + ((a_x & 0xFF) << 6) + (a_y & 0xFF), 0)
        # The candidate direction (bounce-free, interior case) picked by the
        # seeded tables is stamped as the actual dest cell too, so a single
        # `dest_tile`/`dest_life` override reaches the branch under test
        # regardless of exactly which of the 8 mode-table rolls landed:
        for i in range(8):
            dx, dy = _DX8[i], _DY8[i]
            dx = dx - 0x100 if dx & 0x80 else dx
            dy = dy - 0x100 if dy & 0x80 else dy
            nx, ny = (a_x + dx) & 0xFF, (a_y + dy) & 0xFF
            m.mem.wb(dg, 0x28E8 + (nx << 6) + ny, dest_tile)
            m.mem.wb(dg, 0x68E8 + (nx << 6) + ny, dest_life)
    return seed


@pytest.mark.parametrize(
    "slot,a_x,a_y,caste,seed_val,threshold,dest_tile,dest_life,field_e", [
    (0x10, 10, 12, 0x08, 0x1234, 5, 0, 0, 0),        # sub=1 not in (5,9) -> mode transition, no move
    (0x10, 10, 12, 0x29, 0x0000, 5, 0, 0, 0),         # sub=5, roll_a==0 -> natural-decay kill
    (0x10, 10, 12, 0x29, 0xABCD, 5, 10, 0, 0),        # sub=5, roll_a!=0, tile>threshold -> terrain-blocked
    (0x10, 10, 12, 0x29, 0xABCD, 5, 3, 0x40, 0),      # sub=5, tile ok, dest occupied -> occupant-blocked
    (0x10, 10, 12, 0x29, 0xABCD, 5, 3, 0, 0),         # sub=5, move succeeds, field_e==0 -> plain move
    (0x10, 10, 12, 0x29, 0xABCD, 5, 3, 0, 5),         # move succeeds, field_e!=0, colony B -> jam_scent_bn
    (0x10, 10, 12, 0xA9, 0xABCD, 5, 3, 0, 5),         # move succeeds, field_e!=0, colony R -> jam_scent_rn
    (0x10, 0x4A, 0x2A, 0x4A, 0x1234, 5, 3, 0, 0),     # sub=9, interior -> mode-table direction (no bounce)
    (0x10, 0, 0, 0x29, 0x1234, 100, 0, 0, 0),         # sub=5, at the (0,0) corner -> `_Bounce` overrides dir_idx
])
def test_dodigoutanta_state_diff_matches_asm(slot, a_x, a_y, caste, seed_val,
                                             threshold, dest_tile, dest_life,
                                             field_e):
    from simant.recovered.gameplay import do_dig_out_ant_a
    results = _run_and_diff_segs(
        6, 0x1480, (slot,),
        lambda d, s, p: do_dig_out_ant_a(d, s, p, slot),
        _DODIGOUTANTA_REGIONS, near=True,
        seed_fn=_dodigoutanta_seed(slot, a_x, a_y, caste, seed_val,
                                   threshold=threshold, dest_tile=dest_tile,
                                   dest_life=dest_life, field_e=field_e),
        )
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DODIGOUTANTA_REGIONS):
        assert asm_after == rec_after, (
            f"slot={slot:#x} caste={caste:#x} seed={seed_val:#x} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _DecEatB / _DecEatR (seg6:48F8 / 6C6A) — colony hunger-decay clocks ----
# Both take NO ARGS (pure global-state tick).  _DecEatB spans DGROUP +
# SIMANT_DATA_GROUP (the no-starve cheat flag) + PACK; _DecEatR spans only
# DGROUP + PACK (it has no cheat-flag gate).
_DECEATB_REGIONS = [
    (hooks.DG_SEG_INDEX, 0xAC00, 0xAD00),   # covers [0xAC82] (reset rate), [0xAC86] (food)
    (_SDG, 0x8A00, 0x8B00),                 # covers [0x8A60] (no-starve flag)
    (_PACK, 0x7400, 0x7500),                # covers [0x7402] (countdown timer)
]
_DECEATR_REGIONS = [
    (hooks.DG_SEG_INDEX, 0xAC00, 0xAD00),   # covers [0xAC84] (reset rate), [0xAC88] (food)
    (_PACK, 0x7C00, 0x7D00),                # covers [0x7C8E] (countdown timer)
]


@pytest.mark.parametrize("no_starve", [False, True])
@pytest.mark.parametrize("timer,reset_rate,food", [
    (5, 320, 10), (0, 320, 10), (-1, 320, 10), (-1, -64, 10),
    (-1, 320, 0), (-1, 320, -5), (100, 0, 50),
])
def test_deceatb_state_diff_matches_asm(timer, reset_rate, food, no_starve):
    from simant.recovered.gameplay import dec_eat_b

    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], 0x7402, timer & 0xFFFF)
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xAC82, reset_rate & 0xFFFF)
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xAC86, food & 0xFFFF)
        m.mem.wb(m.seg_bases[_SDG], 0x8A60, 1 if no_starve else 0)

    results = _run_and_diff_segs(6, 0x48F8, (), lambda d, s, p: dec_eat_b(d, s, p),
                                 _DECEATB_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DECEATB_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("timer,reset_rate,food", [
    (5, 320, 10), (0, 320, 10), (-1, 320, 10), (-1, -64, 10),
    (-1, 320, 0), (-1, 320, -5), (100, 0, 50),
])
def test_deceatr_state_diff_matches_asm(timer, reset_rate, food):
    from simant.recovered.gameplay import dec_eat_r

    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], 0x7C8E, timer & 0xFFFF)
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xAC84, reset_rate & 0xFFFF)
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xAC88, food & 0xFFFF)

    results = _run_and_diff_segs(6, 0x6C6A, (), lambda d, p: dec_eat_r(d, p),
                                 _DECEATR_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DECEATR_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _KillTailB / _KillTailR (seg6:42B0 / 6762) — remove an ant's tail -----
# Each spans DGROUP (the life-grid cell write, no ES override -> default DS)
# and SIMANT_DATA_GROUP (the per-ant has-tail flag + recorded x/y, all indexed
# by ant_idx as a raw byte offset -- not scaled).
_LIFE_NEST2, _LIFE_NEST3 = 0x88E8, 0x98E8
_NEST_SPAN = 0x1000
_KILLTAILB_REGIONS = [
    (hooks.DG_SEG_INDEX, _LIFE_NEST2, _LIFE_NEST2 + _NEST_SPAN),  # life plane 2
    (_SDG, 0x3700, 0x3F00),           # covers Y[0x3736+idx], X[0x392C+idx],
]                                     # flag[0x3D18+idx] for idx up to ~0x1C8
_KILLTAILR_REGIONS = [
    (hooks.DG_SEG_INDEX, _LIFE_NEST3, _LIFE_NEST3 + _NEST_SPAN),  # life plane 3
    (_SDG, 0x4100, 0x4800),           # covers Y[0x4104+idx], X[0x42FA+idx],
]                                     # flag[0x46E6+idx] for idx up to ~0x1C8


@pytest.mark.parametrize("ant_idx,x,y,flag", [
    (0, 0, 0, 1), (1, 63, 63, 1), (50, 32, 16, 0), (99, 5, 60, 1),
    (150, 40, 40, 1),
])
def test_killtailb_state_diff_matches_asm(ant_idx, x, y, flag):
    from simant.recovered.gameplay import kill_tail_b

    def seed(m):
        sdg = m.seg_bases[_SDG]
        m.mem.wb(sdg, 0x3D18 + ant_idx, flag)
        m.mem.wb(sdg, 0x392C + ant_idx, x)
        m.mem.ww(sdg, 0x3736 + ant_idx, y)      # low byte is what matters

    results = _run_and_diff_segs(6, 0x42B0, (ant_idx,),
                                 lambda d, s: kill_tail_b(d, s, ant_idx),
                                 _KILLTAILB_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _KILLTAILB_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("ant_idx,x,y,flag", [
    (0, 0, 0, 1), (1, 63, 63, 1), (50, 32, 16, 0), (99, 5, 60, 1),
    (150, 40, 40, 1),
])
def test_killtailr_state_diff_matches_asm(ant_idx, x, y, flag):
    from simant.recovered.gameplay import kill_tail_r

    def seed(m):
        sdg = m.seg_bases[_SDG]
        m.mem.wb(sdg, 0x46E6 + ant_idx, flag)
        m.mem.wb(sdg, 0x42FA + ant_idx, x)
        m.mem.ww(sdg, 0x4104 + ant_idx, y)

    results = _run_and_diff_segs(6, 0x6762, (ant_idx,),
                                 lambda d, s: kill_tail_r(d, s, ant_idx),
                                 _KILLTAILR_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _KILLTAILR_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- Colony scent decay (seg6:92AA/92D8/9306/9344) — no-arg grid tick ------
# Each decays a 64x32 (0x800-byte) half-res scent grid in SIMANT_DATA_GROUP.
_SCENT_SPAN = 0x800


@pytest.mark.parametrize("routine,off,base,fn_name", [
    ("_ColonySmellBN", 0x92AA, 0x62D2, "colony_smell_decay_bn"),
    ("_ColonySmellRN", 0x92D8, 0x72D2, "colony_smell_decay_rn"),
    ("_ColonySmellBT", 0x9306, 0x6AD2, "colony_smell_decay_bt"),
    ("_ColonySmellRT", 0x9344, 0x7AD2, "colony_smell_decay_rt"),
])
def test_colonysmell_decay_state_diff_matches_asm(routine, off, base, fn_name):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)

    def seed(m):
        sdg = m.seg_bases[_SDG]
        # a mixed pattern: zeros, small values (<8), and larger values, covering
        # every branch of both the linear and exponential decay curves.
        for i in range(_SCENT_SPAN):
            m.mem.wb(sdg, base + i, [0, 1, 7, 8, 9, 100, 255, 3][i % 8])

    results = _run_and_diff_segs(6, off, (), lambda s: fn(s),
                                 [(_SDG, base, base + _SCENT_SPAN)], near=True,
                                 seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, f"{routine}: {_first_diff(asm_after, rec_after, base)}"


# ---- JamScent family (seg6:94B6/94F6/9536/9576) — set-if-greater a cell ----
# _JamScentBT is the one FAR (`retf`) outlier of this family; the rest are NEAR.
_JAM_REGION_SPAN = 0x900   # a 64x32 grid is 0x800 bytes; pad for the idx formula


@pytest.mark.parametrize("routine,off,base,fn_name,near", [
    ("_JamScentBN", 0x94B6, 0x62D2, "jam_scent_bn", True),
    ("_JamScentRN", 0x94F6, 0x72D2, "jam_scent_rn", True),
    ("_JamScentBT", 0x9536, 0x6AD2, "jam_scent_bt", False),
    ("_JamScentRT", 0x9576, 0x7AD2, "jam_scent_rt", True),
])
@pytest.mark.parametrize("x,y,value,existing", [
    (0, 0, 50, 10), (1, 1, 50, 10), (126, 62, 200, 199), (0, 63, 0, 5),
    (64, 32, 255, 0), (32, 16, 5, 5), (32, 16, 4, 5),
])
def test_jamscent_state_diff_matches_asm(routine, off, base, fn_name, near,
                                         x, y, value, existing):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)
    idx = ((x & 0xFFFE) << 4) + (y >> 1)

    def seed(m):
        m.mem.wb(m.seg_bases[_SDG], base + idx, existing)

    results = _run_and_diff_segs(6, off, (x, y, value),
                                 lambda s: fn(s, x, y, value),
                                 [(_SDG, base, base + _JAM_REGION_SPAN)], near=near,
                                 seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, f"{routine}: {_first_diff(asm_after, rec_after, base)}"


# ---- _AlarmHere / _AlarmHere2 (seg6:943C / 947E) — alarm grid update -------
_ALARM_BASE = 0x52D2


@pytest.mark.parametrize("x,y,delta,existing", [
    (0, 0, 10, 5), (10, 10, -5, 20), (126, 62, 1000, 0), (0, 0, -1000, 5),
    (32, 16, 0, 100), (64, 32, 200, 0),
])
def test_alarmhere_state_diff_matches_asm(x, y, delta, existing):
    from simant.recovered.gameplay import alarm_here
    idx = ((x >> 1) << 5) + (y >> 1)

    def seed(m):
        m.mem.wb(m.seg_bases[_SDG], _ALARM_BASE + idx, existing)

    results = _run_and_diff_segs(6, 0x943C, (x, y, delta),
                                 lambda s: alarm_here(s, x, y, delta),
                                 [(_SDG, _ALARM_BASE, _ALARM_BASE + _JAM_REGION_SPAN)],
                                 near=True, seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, _first_diff(asm_after, rec_after, _ALARM_BASE)


@pytest.mark.parametrize("x,y,value,existing", [
    (0, 0, 50, 10), (10, 10, 5, 20), (126, 62, 200, 199), (0, 0, 5, 5),
    (32, 16, 0, 100), (64, 32, 255, 0),
])
def test_alarmhere2_state_diff_matches_asm(x, y, value, existing):
    from simant.recovered.gameplay import alarm_here2
    idx = ((x >> 1) << 5) + (y >> 1)

    def seed(m):
        m.mem.wb(m.seg_bases[_SDG], _ALARM_BASE + idx, existing)

    results = _run_and_diff_segs(6, 0x947E, (x, y, value),
                                 lambda s: alarm_here2(s, x, y, value),
                                 [(_SDG, _ALARM_BASE, _ALARM_BASE + _JAM_REGION_SPAN)],
                                 near=True, seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, _first_diff(asm_after, rec_after, _ALARM_BASE)


# ---- _FindInAList/BList/RList (seg5:2C42/2C86/2CCE) — pure list search -----
# Read-only predicates (no state mutation): seed via `_run_and_get_ax`'s own
# internal machine, capture AX, then feed that SAME machine's (still-seeded,
# untouched since nothing was written) memory to the recovered function.
@pytest.mark.parametrize("count,slots,target0,target1", [
    (5, [(0, 10, 20, 1), (1, 10, 20, 1)], 10, 20),        # 2 matches -> last wins
    (5, [(2, 7, 8, 1)], 7, 8),                             # single match mid-list
    (3, [(0, 1, 1, 0)], 1, 1),                             # 3rd field 0 -> no match
    (0, [], 5, 5),                                          # empty list -> 0xFFFF
    (4, [(3, 9, 9, 1)], 1, 1),                              # no match at all
])
def test_findinalist_matches_asm(count, slots, target0, target1):
    from simant.recovered.gameplay import find_in_a_list

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, 0x80F0, count)
        for slot, f0, f1, f2 in slots:
            m.mem.wb(sdg, 0x23A4 + slot, f0)
            m.mem.wb(sdg, 0x278E + slot, f1)
            m.mem.wb(sdg, 0x2F62 + slot, f2)

    ax, m = _run_and_get_ax(5, 0x2C42, (target0, target1), seed_fn=seed)
    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    expect = find_in_a_list(pack_view, sdg_view, target0, target1)
    assert ax == (expect & 0xFFFF)


@pytest.mark.parametrize("routine,off,count_off,y_off,x_off,c_off,fn_name", [
    ("_FindInBList", 0x2C86, 0x99D4, 0x3736, 0x392C, 0x3D18, "find_in_b_list"),
    ("_FindInRList", 0x2CCE, 0x72CC, 0x4104, 0x42FA, 0x46E6, "find_in_r_list"),
])
@pytest.mark.parametrize("count,slots,ty,tx,tc", [
    (5, [(0, 5, 6, 2), (1, 5, 6, 2)], 5, 6, 2),   # 2 matches -> last wins
    (5, [(2, 7, 8, 3)], 7, 8, 3),                  # single match mid-list
    (3, [(0, 1, 1, 1)], 1, 1, 2),                  # caste mismatch -> no match
    (0, [], 5, 5, 1),                              # empty list -> 0xFFFF
])
def test_findinlist_matches_asm(routine, off, count_off, y_off, x_off, c_off,
                                fn_name, count, slots, ty, tx, tc):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, count_off, count)
        for slot, y, x, caste in slots:
            m.mem.wb(sdg, y_off + slot, y)
            m.mem.wb(sdg, x_off + slot, x)
            m.mem.wb(sdg, c_off + slot, caste)

    ax, m = _run_and_get_ax(5, off, (ty, tx, tc), seed_fn=seed)
    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    expect = fn(pack_view, sdg_view, ty, tx, tc)
    assert ax == (expect & 0xFFFF), f"{routine}: asm={ax:#06x} rec={expect:#06x}"


# ---- _FindAntIndex (seg5:59FC) — colony-dispatching 3-field list search ---
@pytest.mark.parametrize("colony,count_off,f0_off,f1_off,c_off", [
    (0, 0x80F0, 0x23A4, 0x278E, 0x2F62),   # colony<=1 -> A-list
    (1, 0x80F0, 0x23A4, 0x278E, 0x2F62),
    (2, 0x99D4, 0x3736, 0x392C, 0x3D18),   # colony==2 -> B-list
    (3, 0x72CC, 0x4104, 0x42FA, 0x46E6),   # colony==other -> R-list
    (9, 0x72CC, 0x4104, 0x42FA, 0x46E6),
])
@pytest.mark.parametrize("count,slots,f0,f1,c", [
    (5, [(0, 5, 6, 2), (1, 5, 6, 2)], 5, 6, 2),   # 2 matches -> last wins
    (5, [(2, 7, 8, 3)], 7, 8, 3),                  # single match mid-list
    (3, [(0, 1, 1, 1)], 1, 1, 2),                  # caste mismatch -> no match
    (0, [], 5, 5, 1),                              # empty list -> 0xFFFF
])
def test_findantindex_matches_asm(colony, count_off, f0_off, f1_off, c_off,
                                  count, slots, f0, f1, c):
    from simant.recovered.gameplay import find_ant_index

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, count_off, count)
        for slot, sf0, sf1, sc in slots:
            m.mem.wb(sdg, f0_off + slot, sf0)
            m.mem.wb(sdg, f1_off + slot, sf1)
            m.mem.wb(sdg, c_off + slot, sc)

    ax, m = _run_and_get_ax(5, 0x59FC, (colony, f0, f1, c), seed_fn=seed)
    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    expect = find_ant_index(pack_view, sdg_view, colony, f0, f1, c)
    assert ax == (expect & 0xFFFF), f"colony={colony}: asm={ax:#06x} rec={expect:#06x}"


# ---- _SFoundAnt (seg5:53F6) — locate an ant near the attack-marker target -
_SFOUNDANT_TARGET = (32, 20)   # (target_x, target_y), post >>4


def _sfoundant7_seed(alist_slots, pack9fe8, ce80, marker_x, marker_y):
    def seed(m):
        dg, pack, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK], m.seg_bases[_SDG]
        tx, ty = _SFOUNDANT_TARGET
        m.mem.ww(dg, 0xAC7C, (tx << 4) & 0xFFFF)
        m.mem.ww(dg, 0xAC7E, (ty << 4) & 0xFFFF)
        m.mem.ww(pack, 0x7D60, 7)
        m.mem.ww(pack, 0x80F0, len(alist_slots))
        for slot, (sx, sy, caste) in enumerate(alist_slots):
            m.mem.wb(sdg, 0x23A4 + slot, sx)
            m.mem.wb(sdg, 0x278E + slot, sy)
            m.mem.wb(sdg, 0x2F62 + slot, caste)
        m.mem.ww(pack, 0x9FE8, pack9fe8)
        m.mem.ww(dg, 0xCE80, ce80)
        m.mem.ww(dg, 0xCD88, marker_x)
        m.mem.ww(dg, 0xCE7E, marker_y)
    return seed


@pytest.mark.parametrize("alist_slots,pack9fe8,ce80,marker_x,marker_y,label", [
    ([(33, 21, 0x50)], 0, 1, 200, 200, "alist-match"),   # A-list ant within range -> its slot
    ([], 0, 1, 32, 20, "marker-found"),                   # empty list, marker matches exactly
    ([], 1, 1, 32, 20, "9fe8-blocks"),                    # pack[9FE8]!=0 -> skip marker check
    ([], 0, 2, 32, 20, "ce80-blocks"),                    # dgroup[CE80]!=1 -> skip marker check
    ([], 0, 1, 200, 200, "marker-too-far"),                # marker out of range
])
def test_sfoundant_mode7_matches_asm(alist_slots, pack9fe8, ce80, marker_x, marker_y, label):
    from simant.recovered.gameplay import s_found_ant
    ax, m = _run_and_get_ax(
        5, 0x53F6, (),
        seed_fn=_sfoundant7_seed(alist_slots, pack9fe8, ce80, marker_x, marker_y))
    dg, pack, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK], m.seg_bases[_SDG]
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    expect = s_found_ant(dgroup_view, sdg_view, pack_view)
    assert ax == (expect & 0xFFFF), f"{label}: asm={ax:#06x} rec={expect:#06x}"


def _sfoundant_walk_seed(target_x, target_y, dir_idx, life_cells, alist_slots):
    def seed(m):
        dg, pack, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(dg, 0xAC7C, (target_x << 4) & 0xFFFF)
        m.mem.ww(dg, 0xAC7E, (target_y << 4) & 0xFFFF)
        m.mem.ww(pack, 0x7D60, 0)
        m.mem.ww(dg, 0xAC80, dir_idx)
        # clear a wide strip along both walk directions so the default 20-step
        # exhaustion path is deterministic, then apply the specific overrides
        for dx in range(-25, 26):
            cx = target_x + dx
            if 0 <= cx <= 0x7F:
                m.mem.wb(dg, 0x68E8 + (cx << 6) + target_y, 0)
        for (cx, cy), tile in life_cells.items():
            m.mem.wb(dg, 0x68E8 + (cx << 6) + cy, tile)
        m.mem.ww(pack, 0x80F0, len(alist_slots))
        for slot, (sx, sy, caste) in enumerate(alist_slots):
            m.mem.wb(sdg, 0x23A4 + slot, sx)
            m.mem.wb(sdg, 0x278E + slot, sy)
            m.mem.wb(sdg, 0x2F62 + slot, caste)
    return seed


@pytest.mark.parametrize("target_x,target_y,dir_idx,life_cells,alist_slots,label", [
    (10, 20, 2, {}, [], "exhausted-all-empty"),
    (10, 20, 2, {(11, 20): 0xFE}, [], "yellow-ant-aborts"),
    (10, 20, 2, {(11, 20): 0x50}, [(11, 20, 0x50)], "alist-match-mid-walk"),
    (10, 20, 2, {(11, 20): 0x50}, [], "occupied-unmatched-continues"),
    (125, 20, 2, {}, [], "walks-off-grid-edge"),
])
def test_sfoundant_walk_matches_asm(target_x, target_y, dir_idx, life_cells,
                                    alist_slots, label):
    from simant.recovered.gameplay import s_found_ant
    ax, m = _run_and_get_ax(
        5, 0x53F6, (),
        seed_fn=_sfoundant_walk_seed(target_x, target_y, dir_idx, life_cells, alist_slots))
    dg, pack, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK], m.seg_bases[_SDG]
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    expect = s_found_ant(dgroup_view, sdg_view, pack_view)
    assert ax == (expect & 0xFFFF), f"{label}: asm={ax:#06x} rec={expect:#06x}"


# ---- _GetAntIndex (seg5:573C) — read counterpart of _SetAntIndex, 5 far ---
# pointer OUT params.  Point every OUT pointer into DGROUP scratch (0xF000+)
# well away from any real field, and read the words back afterward.
_GETANTINDEX_OUT_OFFSETS = [0xF000, 0xF002, 0xF004, 0xF006, 0xF008]
_DG_SELECTOR = runtime.create_machine().seg_bases[hooks.DG_SEG_INDEX]


def _getantindex_seed(list_type, slot, count, fields):
    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        dg = m.seg_bases[hooks.DG_SEG_INDEX]
        count_off = 0x80F0 if list_type <= 1 else (0x99D4 if list_type == 2 else 0x72CC)
        f0, f1, c, fc, fe = (
            (0x23A4, 0x278E, 0x2F62, 0x2B78, 0x334C) if list_type <= 1 else
            (0x3736, 0x392C, 0x3D18, 0x3B22, 0x3F0E) if list_type == 2 else
            (0x4104, 0x42FA, 0x46E6, 0x44F0, 0x48DC))
        m.mem.ww(pack, count_off, count)
        if fields is not None:
            t0, t1, caste, field_c, field_e = fields
            m.mem.wb(sdg, f0 + slot, t0)
            m.mem.wb(sdg, f1 + slot, t1)
            m.mem.wb(sdg, c + slot, caste)
            m.mem.wb(sdg, fc + slot, field_c)
            m.mem.wb(sdg, fe + slot, field_e)
        for off in _GETANTINDEX_OUT_OFFSETS:
            m.mem.ww(dg, off, 0xDEAD)
    return seed


@pytest.mark.parametrize("list_type,slot,count,fields", [
    (0, 2, 5, (10, 20, 0x50, 3, 7)),    # A-list valid slot
    (2, 1, 3, (15, 25, 0x88, 1, 2)),    # B-list valid slot
    (5, 0, 2, (30, 40, 0x99, 4, 5)),    # R-list (list_type>2) valid slot
    (0, 5, 5, None),                     # slot == count -> out of range
    (0, -1, 5, None),                    # negative slot -> out of range
])
def test_getantindex_matches_asm(list_type, slot, count, fields):
    from simant.recovered.gameplay import get_ant_index
    args = (list_type, slot & 0xFFFF)
    for off in _GETANTINDEX_OUT_OFFSETS:
        args += (off, _DG_SELECTOR)
    ax, m = _run_and_get_ax(5, 0x573C, args,
                            seed_fn=_getantindex_seed(list_type, slot, count, fields))
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    asm_out = [m.mem.rw(dg, off) for off in _GETANTINDEX_OUT_OFFSETS]

    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    expect = get_ant_index(pack_view, sdg_view, list_type, slot)

    if expect is None:
        assert ax == 0, f"list_type={list_type} slot={slot}: expected failure, asm ax={ax:#06x}"
        assert asm_out == [0xDEAD] * 5, "OUT pointers must be untouched on failure"
    else:
        assert ax == 1, f"list_type={list_type} slot={slot}: expected success, asm ax={ax:#06x}"
        assert asm_out == list(expect), (
            f"list_type={list_type} slot={slot}: asm={asm_out} rec={list(expect)}")


# ---- _FindLifeAt/_FindEggAt (seg5:8A96/88A2) — locate an ant at (x,y) ------
# Pure predicates with ONE far-pointer OUT param (the found slot).
_FINDLIFEAT_OUT_OFFSET = 0xF010
_LIFE_BASE_BY_TYPE = {0: 0x68E8, 1: 0x68E8, 2: 0x88E8, 3: 0x98E8}
_ALIST_FIELDS_BY_TYPE = {
    0: (0x80F0, 0x23A4, 0x278E, 0x2F62),
    1: (0x80F0, 0x23A4, 0x278E, 0x2F62),
    2: (0x99D4, 0x3736, 0x392C, 0x3D18),
}
_RLIST_FIELDS = (0x72CC, 0x4104, 0x42FA, 0x46E6)


def _findat_seed(list_type, x, y, tile, alist_slots):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        life_base = _LIFE_BASE_BY_TYPE.get(list_type, 0x68E8)
        if 0 <= list_type <= 3:
            m.mem.wb(dg, life_base + (x << 6) + y, tile)
        count_off, f0, f1, c = _ALIST_FIELDS_BY_TYPE.get(list_type, _RLIST_FIELDS)
        m.mem.ww(pack, count_off, len(alist_slots))
        for slot, (sf0, sf1, sc) in enumerate(alist_slots):
            m.mem.wb(sdg, f0 + slot, sf0)
            m.mem.wb(sdg, f1 + slot, sf1)
            m.mem.wb(sdg, c + slot, sc)
        m.mem.ww(dg, _FINDLIFEAT_OUT_OFFSET, 0xDEAD)
    return seed


@pytest.mark.parametrize("which,off,fn_name", [
    ("_FindLifeAt", 0x8A96, "find_life_at"),
    ("_FindEggAt", 0x88A2, "find_egg_at"),
])
@pytest.mark.parametrize("list_type,x,y,tile,alist_slots,label", [
    (0, 10, 20, 0x03, [(10, 20, 0x03)], "direct-tile-found-in-list"),
    (0, 10, 20, 0x03, [], "direct-tile-not-in-list"),
    (0, 10, 20, 0xFE, [(10, 20, 0x81)], "yellow-sentinel-falls-back"),
    (0, 10, 20, 0x00, [(10, 20, 0x81)], "empty-cell-falls-back"),
    (0, 10, 20, 0x00, [], "empty-cell-and-empty-list-notfound"),
    (2, 10, 20, 0x03, [(10, 20, 0x03)], "blist-direct-found"),
    (5, 10, 20, 0x03, [(10, 20, 0x03)], "rlist-direct-found"),
])
def test_findat_matches_asm(which, off, fn_name, list_type, x, y, tile,
                            alist_slots, label):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)
    args = (_FINDLIFEAT_OUT_OFFSET, _DG_SELECTOR, list_type, x, y)
    ax, m = _run_and_get_ax(5, off, args,
                            seed_fn=_findat_seed(list_type, x, y, tile, alist_slots))
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    asm_slot = m.mem.rw(dg, _FINDLIFEAT_OUT_OFFSET)

    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    dgroup_view = ByteBackend(m.mem.block(dg, 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    rec_slot, rec_caste = fn(pack_view, sdg_view, dgroup_view, list_type, x, y)

    assert ax == (rec_caste & 0xFFFF), (
        f"{which} {label}: caste asm={ax:#06x} rec={rec_caste & 0xFFFF:#06x}")
    assert asm_slot == (rec_slot & 0xFFFF), (
        f"{which} {label}: slot asm={asm_slot:#06x} rec={rec_slot & 0xFFFF:#06x}")


# ---- _FindLifeIndex (seg5:5922) — find_ant_index variant, masked caste ----
# range instead of an exact match.
@pytest.mark.parametrize("list_type,count_off,f0_off,f1_off,c_off", [
    (0, 0x80F0, 0x23A4, 0x278E, 0x2F62),
    (2, 0x99D4, 0x3736, 0x392C, 0x3D18),
    (5, 0x72CC, 0x4104, 0x42FA, 0x46E6),
])
@pytest.mark.parametrize("count,slots,f0,f1,lo,hi,mask", [
    # caste 0x35 & 0x0F = 5, in [2,6] -> match
    (3, [(0, 5, 6, 0x35)], 5, 6, 2, 6, 0x0F),
    # 2 slots both match field0/field1 -> last (highest) slot wins
    (5, [(0, 5, 6, 0x32), (1, 5, 6, 0x35)], 5, 6, 2, 6, 0x0F),
    # masked value out of [lo,hi] -> no match
    (3, [(0, 5, 6, 0x38)], 5, 6, 2, 6, 0x0F),
    # field0/field1 mismatch -> no match despite masked caste in range
    (3, [(0, 1, 1, 0x35)], 5, 6, 2, 6, 0x0F),
    # empty list -> 0xFFFF
    (0, [], 5, 5, 0, 0xFF, 0xFF),
])
def test_findlifeindex_matches_asm(list_type, count_off, f0_off, f1_off, c_off,
                                   count, slots, f0, f1, lo, hi, mask):
    from simant.recovered.gameplay import find_life_index

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, count_off, count)
        for slot, sf0, sf1, sc in slots:
            m.mem.wb(sdg, f0_off + slot, sf0)
            m.mem.wb(sdg, f1_off + slot, sf1)
            m.mem.wb(sdg, c_off + slot, sc)

    ax, m = _run_and_get_ax(5, 0x5922, (list_type, f0, f1, lo, hi, mask), seed_fn=seed)
    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    pack_view = ByteBackend(m.mem.block(pack, 0, 0x10000), 0)
    sdg_view = ByteBackend(m.mem.block(sdg, 0, 0x10000), 0)
    expect = find_life_index(pack_view, sdg_view, list_type, f0, f1, lo, hi, mask)
    assert ax == (expect & 0xFFFF), f"list_type={list_type}: asm={ax:#06x} rec={expect:#06x}"


# ---- _AddAntToAList/BList/RList (seg5:2EF0/2F4A/2FA4) — list insert --------
_YARD_SPAN = 0x2000
_ADDANTA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x68E8, 0x68E8 + _YARD_SPAN),   # life plane 0
    (_SDG, 0x2300, 0x3800),           # covers 0x23A4/278E/2B78/2F62/334C+slot
    (_PACK, 0x80E0, 0x8100),          # covers [0x80F0] (count)
]
_ADDANTB_REGIONS = [
    (hooks.DG_SEG_INDEX, _LIFE_NEST2, _LIFE_NEST2 + _NEST_SPAN),
    (_SDG, 0x3700, 0x4200),           # covers 0x3736/392C/3B22/3D18/3F0E+slot
    (_PACK, 0x9900, 0x9A00),          # covers [0x99D4] (count)
]
_ADDANTR_REGIONS = [
    (hooks.DG_SEG_INDEX, _LIFE_NEST3, _LIFE_NEST3 + _NEST_SPAN),
    (_SDG, 0x4100, 0x4C00),           # covers 0x4104/42FA/44F0/46E6/48DC+slot
    (_PACK, 0x7280, 0x7300),          # covers [0x72CC] (count)
]


@pytest.mark.parametrize("count", [0, 1, 5, 0x3E7, 0x3E8, 0x3E9])
def test_addanttoalist_state_diff_matches_asm(count):
    from simant.recovered.gameplay import add_ant_to_a_list
    t0, t1, caste, fc, fe = 5, 9, 3, 7, 11

    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], 0x80F0, count)

    results = _run_and_diff_segs(
        5, 0x2EF0, (t0, t1, caste, fc, fe),
        lambda d, s, p: add_ant_to_a_list(p, s, d, t0, t1, caste, fc, fe),
        _ADDANTA_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _ADDANTA_REGIONS):
        assert asm_after == rec_after, f"count={count} {label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _ExitHole (seg5:2DB6) — find a clear yard cell, append to A-list -----
_EXITHOLE_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0x28E8 + _YARD_SPAN),   # yard map plane
    (_SDG, 0, 0x3800),          # delta tables [0:16) + 0x23A4/278E/2B78/2F62/334C+slot
    (_PACK, 0x80E0, 0x8100),    # covers [0x80F0] (count)
]


def _exithole_seed(x, y, tiles, count, holes=()):
    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        for si in range(8):
            nx, ny = x + GET_BEST_DIR_DX[si], y + GET_BEST_DIR_DY[si]
            if 0 <= nx <= 0x7F and 0 <= ny <= 0x3F:
                m.mem.wb(dg, 0x28E8 + (nx << 6) + ny, tiles.get(si, 0x50))
        m.mem.ww(m.seg_bases[_PACK], 0x80F0, count)
        for slot in range(count):
            m.mem.wb(sdg, 0x2F62 + slot, 0 if slot in holes else 1)
            m.mem.wb(sdg, 0x23A4 + slot, slot & 0xFF)
            m.mem.wb(sdg, 0x278E + slot, (slot * 3) & 0xFF)
            m.mem.wb(sdg, 0x2B78 + slot, (slot * 5) & 0xFF)
            m.mem.wb(sdg, 0x334C + slot, (slot * 7) & 0xFF)
    return seed


@pytest.mark.parametrize("x,y,tiles,caste,field_c,field_e_hint,count,holes", [
    # nothing clear at all -> no-op, returns 0
    (20, 20, {}, 0x03, 1, 0, 5, ()),
    # one clear direction (si=3), field_c not special -> caste-bit/x-position rule
    (20, 20, {3: 0x10}, 0x00, 1, 0, 5, ()),      # caste bit clear, x<0x40 -> field_e=0x78
    (0x50, 20, {3: 0x10}, 0x00, 1, 0, 5, ()),    # caste bit clear, x>=0x40 -> field_e=0
    (20, 20, {3: 0x10}, 0x80, 1, 0, 5, ()),      # caste bit set, x<=0x40 -> field_e=0
    (0x50, 20, {3: 0x10}, 0x80, 1, 0, 5, ()),    # caste bit set, x>0x40 -> field_e=0x78
    # field_c special cases
    (20, 20, {3: 0x10}, 0x03, 6, 0x99, 5, ()),   # field_c==6 -> field_e=hint
    (20, 20, {3: 0x10}, 0x03, 3, 0x99, 5, ()),   # field_c==3 -> field_e=0
    (20, 20, {3: 0x10}, 0x03, 7, 0x99, 5, ()),   # field_c==7 -> field_e=0
    # multiple clear directions -> takes the FIRST in scan order
    (20, 20, {2: 0x10, 5: 0x10}, 0x00, 1, 0, 5, ()),
    # list at cap with holes -> compaction path
    (20, 20, {3: 0x10}, 0x03, 1, 0, 0x3E8, (2, 500, 999)),
    # list at cap with NO holes -> new entry ends up uncounted (ported as-is)
    (20, 20, {3: 0x10}, 0x03, 1, 0, 0x3E8, ()),
])
def test_exithole_state_diff_matches_asm(x, y, tiles, caste, field_c, field_e_hint,
                                         count, holes):
    from simant.recovered.gameplay import exit_hole
    results = _run_and_diff_segs(
        5, 0x2DB6, (x, y, caste, field_c, field_e_hint),
        lambda d, s, p: exit_hole(d, s, p, x, y, caste, field_c, field_e_hint),
        _EXITHOLE_REGIONS, seed_fn=_exithole_seed(x, y, tiles, count, holes))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _EXITHOLE_REGIONS):
        assert asm_after == rec_after, (
            f"x={x} y={y} tiles={tiles} caste={caste:#x} fc={field_c} "
            f"count={count:#x} holes={holes} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


@pytest.mark.parametrize("count", [0, 1, 5, 0x1F3, 0x1F4, 0x1F5])
def test_addanttoblist_state_diff_matches_asm(count):
    from simant.recovered.gameplay import add_ant_to_b_list
    y, x, caste, fc, fe = 40, 20, 4, 8, 12

    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], 0x99D4, count)

    results = _run_and_diff_segs(
        5, 0x2F4A, (y, x, caste, fc, fe),
        lambda d, s, p: add_ant_to_b_list(p, s, d, y, x, caste, fc, fe),
        _ADDANTB_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _ADDANTB_REGIONS):
        assert asm_after == rec_after, f"count={count} {label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("count", [0, 1, 5, 0x1F3, 0x1F4, 0x1F5])
def test_addanttorlist_state_diff_matches_asm(count):
    from simant.recovered.gameplay import add_ant_to_r_list
    y, x, caste, fc, fe = 50, 30, 6, 10, 14

    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], 0x72CC, count)

    results = _run_and_diff_segs(
        5, 0x2FA4, (y, x, caste, fc, fe),
        lambda d, s, p: add_ant_to_r_list(p, s, d, y, x, caste, fc, fe),
        _ADDANTR_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _ADDANTR_REGIONS):
        assert asm_after == rec_after, f"count={count} {label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _MakeNewTailB / _MakeNewTailR (seg6:424A / 66FC) — append a trailing -
# tail segment behind an ant. Composes add_ant_to_b/r_list; reuses their own
# established regions, widened to also cover the compass delta tables.
_MAKENEWTAILB_REGIONS = [
    (hooks.DG_SEG_INDEX, _LIFE_NEST2, _LIFE_NEST2 + _NEST_SPAN),
    (_SDG, 0, 0x4200),
    (_PACK, 0x9900, 0x9A00),
]
_MAKENEWTAILR_REGIONS = [
    (hooks.DG_SEG_INDEX, _LIFE_NEST3, _LIFE_NEST3 + _NEST_SPAN),
    (_SDG, 0, 0x4C00),
    (_PACK, 0x7280, 0x7300),
]


@pytest.mark.parametrize("colony,seg,off,regions,caste_off,x_off,y_off,"
                         "count_off", [
    ("B", 6, 0x424A, _MAKENEWTAILB_REGIONS, 0x3D18, 0x392C, 0x3736, 0x99D4),
    ("R", 6, 0x66FC, _MAKENEWTAILR_REGIONS, 0x46E6, 0x42FA, 0x4104, 0x72CC),
])
@pytest.mark.parametrize("caste,x_field,y_field,count", [
    (0x83, 20, 30, 5),
    (0x08, 5, 5, 0),
])
def test_makenewtail_state_diff_matches_asm(colony, seg, off, regions,
                                            caste_off, x_off, y_off,
                                            count_off, caste, x_field,
                                            y_field, count):
    from simant.recovered.gameplay import make_new_tail_b, make_new_tail_r
    fn = make_new_tail_b if colony == "B" else make_new_tail_r
    slot = 3

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, count_off, count)
        m.mem.wb(sdg, caste_off + slot, caste)
        m.mem.wb(sdg, x_off + slot, x_field)
        m.mem.wb(sdg, y_off + slot, y_field)
        for i in range(8):
            m.mem.wb(sdg, i, _DX8[i])
            m.mem.wb(sdg, 8 + i, _DY8[i])

    results = _run_and_diff_segs(
        seg, off, (slot,),
        lambda d, s, p: fn(d, s, p, slot),
        regions, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, regions):
        assert asm_after == rec_after, (
            f"{colony} caste={caste:#x} count={count} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _DropFoodB / _DropFoodR (seg6:3C3C / 6242) — grow a food pile --------
# Each spans DGROUP (the map cell), PACK (a "total dropped" counter + the
# shared "acting ant index" context slot [0x9B6A]), and SIMANT_DATA_GROUP (the
# acting ant's caste byte, bit 0x08 cleared).
_DROPFOODB_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x48E8, 0x48E8 + _NEST_SPAN),   # map plane 2
    (_PACK, 0x9B00, 0x9F00),      # covers [0x9B6A] (ant idx) and [0x9EA4] (counter)
    (_SDG, 0x3D00, 0x3E00),       # covers [0x3D18+idx] (caste)
]
_DROPFOODR_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x58E8, 0x58E8 + _NEST_SPAN),   # map plane 3
    (_PACK, 0x7280, 0x9F00),      # covers [0x72DE] (counter) and [0x9B6A] (ant idx)
    (_SDG, 0x46E0, 0x4800),       # covers [0x46E6+idx] (caste)
]


@pytest.mark.parametrize("x,y,tile,ant_idx,caste", [
    (10, 20, 0x05, 0, 0x0B), (10, 20, 0x10, 1, 0x03), (10, 20, 0x12, 2, 0x08),
    (10, 20, 0x13, 3, 0x00), (0, 0, 0x00, 50, 0xFF), (63, 63, 0x13, 5, 0x08),
])
def test_dropfoodb_state_diff_matches_asm(x, y, tile, ant_idx, caste):
    from simant.recovered.gameplay import drop_food_b

    def seed(m):
        dg, pack, sdg = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK],
                         m.seg_bases[_SDG])
        m.mem.wb(dg, 0x48E8 + (x << 6) + y, tile)
        m.mem.ww(pack, 0x9B6A, ant_idx)
        m.mem.wb(sdg, 0x3D18 + ant_idx, caste)

    results = _run_and_diff_segs(6, 0x3C3C, (x, y),
                                 lambda d, p, s: drop_food_b(d, p, s, x, y),
                                 _DROPFOODB_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DROPFOODB_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("x,y,tile,ant_idx,caste", [
    (10, 20, 0x05, 0, 0x0B), (10, 20, 0x10, 1, 0x03), (10, 20, 0x12, 2, 0x08),
    (10, 20, 0x13, 3, 0x00), (0, 0, 0x00, 50, 0xFF), (63, 63, 0x13, 5, 0x08),
])
def test_dropfoodr_state_diff_matches_asm(x, y, tile, ant_idx, caste):
    from simant.recovered.gameplay import drop_food_r

    def seed(m):
        dg, pack, sdg = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK],
                         m.seg_bases[_SDG])
        m.mem.wb(dg, 0x58E8 + (x << 6) + y, tile)
        m.mem.ww(pack, 0x9B6A, ant_idx)
        m.mem.wb(sdg, 0x46E6 + ant_idx, caste)

    results = _run_and_diff_segs(6, 0x6242, (x, y),
                                 lambda d, p, s: drop_food_r(d, p, s, x, y),
                                 _DROPFOODR_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DROPFOODR_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _RemoveFromAList (seg5:2B42) — remove + shift the tail down ----------
# Calls a shared far-memcpy helper (seg7:783E) 5x; NOT stubbed, since it does
# genuine sim-state mutation (the array shift), not a rendering/audio side
# effect — it runs for real.
_REMOVEFROMA_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x68E8, 0x68E8 + _YARD_SPAN),   # life plane 0
    (_SDG, 0x2300, 0x3800),           # covers 0x23A4/278E/2B78/2F62/334C+slot
    (_PACK, 0x80E0, 0x8100),          # covers [0x80F0] (count)
]


@pytest.mark.parametrize("slot,count", [(0, 6), (2, 6), (5, 6), (0, 1)])
def test_removefromalist_state_diff_matches_asm(slot, count):
    from simant.recovered.gameplay import remove_from_a_list

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, 0x80F0, count)
        for i in range(count):
            m.mem.wb(sdg, 0x23A4 + i, (i * 3 + 1) & 0x3F)     # keep target0 in 0..63
            m.mem.wb(sdg, 0x278E + i, (i * 5 + 2) & 0x1F)     # keep target1 small
            m.mem.wb(sdg, 0x2B78 + i, (i * 7 + 3) & 0xFF)
            m.mem.wb(sdg, 0x2F62 + i, (i * 11 + 4) & 0xFF)
            m.mem.wb(sdg, 0x334C + i, (i * 13 + 5) & 0xFF)

    results = _run_and_diff_segs(
        5, 0x2B42, (slot,),
        lambda d, s, p: remove_from_a_list(p, s, d, slot),
        _REMOVEFROMA_REGIONS, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _REMOVEFROMA_REGIONS):
        assert asm_after == rec_after, (
            f"slot={slot} count={count} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _GetFromAlist (seg5:2FFE) — find + remove the last matching-colony ---
# yard ant; a genuine quirk (ported literally) means slot 0 can never match.
@pytest.mark.parametrize("castes,colony_bit,label", [
    ([0x10, 0x20, 0x30, 0x40, 0x50, 0x90], 1, "match at the top slot -> removed"),
    ([0x90, 0x20, 0x30, 0x40, 0x50, 0x60], 1, "only slot 0 matches -> quirk returns 0, no removal"),
    ([0x10, 0x20, 0x30, 0x40, 0x50, 0x60], 1, "no match at all -> 0"),
    ([0x10, 0x00, 0x30, 0x00, 0x90, 0x60], 1, "dead/empty slots skipped -> match at slot 4"),
])
def test_getfromalist_state_diff_matches_asm(castes, colony_bit, label):
    from simant.recovered.gameplay import get_from_a_list
    count = len(castes)

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, 0x80F0, count)
        for i, caste in enumerate(castes):
            m.mem.wb(sdg, 0x23A4 + i, (i * 3 + 1) & 0x3F)
            m.mem.wb(sdg, 0x278E + i, (i * 5 + 2) & 0x1F)
            m.mem.wb(sdg, 0x2B78 + i, (i * 7 + 3) & 0xFF)
            m.mem.wb(sdg, 0x2F62 + i, caste)
            m.mem.wb(sdg, 0x334C + i, (i * 13 + 5) & 0xFF)

    results = _run_and_diff_segs(
        5, 0x2FFE, (colony_bit,),
        lambda d, s, p: get_from_a_list(d, s, p, colony_bit),
        _REMOVEFROMA_REGIONS, seed_fn=seed)
    for (label2, asm_after, rec_after), (_si, lo, _hi) in zip(results, _REMOVEFROMA_REGIONS):
        assert asm_after == rec_after, f"{label} {label2}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _MakeRedInitiator (seg6:967C) — convert an eligible yard ant ---------
_MAKERED_REGIONS = [
    (_PACK, 0x8000, 0x9E00),      # covers [0x80F0] (count), [0x9D74] (pending)
    (_SDG, 0x2B00, 0x8B00),       # covers 0x2F62/2B78/334C (A-fields), 0x8A64 (flag)
]


@pytest.mark.parametrize("rate,slots", [
    (0x1E, [0x10, 0x81]),         # gate passes; last (slot1) is eligible
    (0x1D, [0x81]),                # gate fails (rate<0x1E) -> no-op regardless
    (0x1E, [0x10, 0x20]),          # gate passes; no eligible candidate
    (0x1E, []),                     # gate passes; empty list
    (0x1E, [0x81, 0x82, 0x10]),     # multiple eligible; LAST-found (highest slot with bit7) wins
])
def test_makeredinitiator_state_diff_matches_asm(rate, slots):
    from simant.recovered.gameplay import make_red_initiator

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.wb(sdg, 0x8A64, 0xEE)   # poison the success flag
        m.mem.ww(pack, 0x9D74, 0xCAFE & 0xFFFF)
        m.mem.ww(pack, 0x80F0, len(slots))
        for i, caste in enumerate(slots):
            m.mem.wb(sdg, 0x2F62 + i, caste)
            m.mem.wb(sdg, 0x2B78 + i, 0x55)
            m.mem.wb(sdg, 0x334C + i, 0x66)

    # make_red_initiator reads DGROUP[0xAC82] but never writes DGROUP, so it
    # is not one of _MAKERED_REGIONS's diffed windows; the lambda needs SOME
    # dgroup-like object for its `dgroup` arg.  A tiny read-only stand-in
    # returning the seeded `rate` is sufficient and avoids adding a fourth
    # (read-only, unverified) region just to construct a real view.
    dg_ro = _ConstWordView(rate & 0xFFFF)
    results = _run_and_diff_segs(
        6, 0x967C, (), lambda p, s: make_red_initiator(dg_ro, p, s),
        _MAKERED_REGIONS, seed={0xAC82: rate}, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _MAKERED_REGIONS):
        assert asm_after == rec_after, (
            f"rate={rate} slots={slots} {label}: {_first_diff(asm_after, rec_after, lo)}")


class _ConstWordView:
    """A read-only stand-in that always returns the same word — used where a
    test needs a `dgroup`-like object but the recovered function only ever
    READS one fixed value from it (see `test_makeredinitiator...` above)."""
    def __init__(self, value):
        self._value = value

    def rw(self, _off):
        return self._value


# ---- _TallyModePop (seg6:038E) — roll up tallies, maybe spawn an initiator -
_TALLY_PACK_REGION = (_PACK, 0x7800, 0xA100)     # covers all 12 tally fields,
# plus [0x80F0]/[0x9D74] (make_red_initiator's PACK fields, when the gate fires)
_TALLY_SDG_REGION = (_SDG, 0x2B00, 0x8B00)       # make_red_initiator's SDG range


@pytest.mark.parametrize("gate,rate,slots", [
    (5, 0x1E, []),                  # gate skips (>=1) -> no call, tally only
    (1, 0x1E, []),                  # gate boundary (==1) -> still skips
    (0, 0x1E, [0x81]),              # gate fires (<1); candidate present
    (-3, 0x1E, []),                 # negative gate -> fires; empty A-list
])
def test_tallymodepop_state_diff_matches_asm(gate, rate, slots):
    from simant.recovered.gameplay import tally_mode_pop

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        for off, val in [(0x786E, 3), (0x7870, 4), (0x7872, 5), (0x7874, 6),
                        (0x786C, 7), (0x7878, 8), (0x7882, 9), (0x7876, 10),
                        (0x7BE8, 11), (0x7BEA, 12), (0x7BEC, 13), (0x7BEE, 14),
                        (0x7BE6, 15), (0x7BF2, 16), (0x7BFC, 17), (0x7BF0, 18)]:
            m.mem.ww(pack, off, val)
        m.mem.ww(pack, 0x7C0A, gate & 0xFFFF)
        # make_red_initiator's own inputs, only relevant when the gate fires:
        m.mem.wb(sdg, 0x8A64, 0xEE)
        m.mem.ww(pack, 0x9D74, 0xCAFE & 0xFFFF)
        m.mem.ww(pack, 0x80F0, len(slots))
        for i, caste in enumerate(slots):
            m.mem.wb(sdg, 0x2F62 + i, caste)
            m.mem.wb(sdg, 0x2B78 + i, 0x55)
            m.mem.wb(sdg, 0x334C + i, 0x66)

    regions = [_TALLY_PACK_REGION, _TALLY_SDG_REGION]
    results = _run_and_diff_segs(
        6, 0x038E, (),
        lambda p, s: tally_mode_pop(p, _ConstWordView(rate & 0xFFFF), s),
        regions, seed={0xAC82: rate}, seed_fn=seed, near=True)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, regions):
        assert asm_after == rec_after, (
            f"gate={gate} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _ClrModePop (seg6:034A) — reset mode-population tally arrays ---------
@pytest.mark.parametrize("c1,c2", [(0, 0), (1, 1), (5, 0), (0, 9), (100, 200)])
def test_clrmodepop_state_diff_matches_asm(c1, c2):
    from simant.recovered.gameplay import clr_mode_pop

    def seed(m):
        pack = m.seg_bases[_PACK]
        for i in range(0x14):
            m.mem.ww(pack, 0x7BE4 + 2 * i, 0xBEEF)
            m.mem.ww(pack, 0x786A + 2 * i, 0xDEAD)
        m.mem.ww(pack, 0x7C44, c1)
        m.mem.ww(pack, 0x8078, c2)

    results = _run_and_diff_segs(6, 0x034A, (), lambda p: clr_mode_pop(p),
                                 [(_PACK, 0x7800, 0x8100)], near=True, seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, f"c1={c1} c2={c2}: {_first_diff(asm_after, rec_after, 0x7800)}"


# ---- _FillHolesBN / _FillHolesRN (seg6:91DE / 9244) — hole-scent refresh --
@pytest.mark.parametrize("routine,off,hole_x_off,scent_base,fn_name", [
    ("_FillHolesBN", 0x91DE, 0x82D2, 0x62D2, "fill_holes_bn"),
    ("_FillHolesRN", 0x9244, 0x8312, 0x72D2, "fill_holes_rn"),
])
def test_fillholes_state_diff_matches_asm(routine, off, hole_x_off, scent_base,
                                          fn_name):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)

    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        # row 0: no tracked hole (0) -> skipped; row 1: hole tracked, map cell IS
        # a hole (0x51) -> jam to 0xFF; row 2: hole tracked, map cell filled in
        # (0x20) -> clear to 0; rows 3.. left at 0 (no-op).
        for si in range(0x40):
            m.mem.wb(sdg, hole_x_off + si, 0)
        m.mem.wb(sdg, hole_x_off + 1, 10)
        m.mem.wb(dg, 0x28E8 + (10 << 6) + 1, 0x51)
        m.mem.wb(sdg, hole_x_off + 2, 20)
        m.mem.wb(dg, 0x28E8 + (20 << 6) + 2, 0x20)
        for i in range(0x800):
            m.mem.wb(sdg, scent_base + i, 0x77)   # poison the whole scent grid

    sdg_lo = min(hole_x_off, scent_base)
    sdg_hi = max(hole_x_off + 0x40, scent_base + 0x800)
    regions = [(hooks.DG_SEG_INDEX, 0x28E8, 0x28E8 + _YARD_SPAN),
              (_SDG, sdg_lo, sdg_hi)]
    results = _run_and_diff_segs(6, off, (), lambda d, s: fn(s, d), regions,
                                 near=True, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, rlo, _hi) in zip(results, regions):
        assert asm_after == rec_after, f"{routine} {label}: {_first_diff(asm_after, rec_after, rlo)}"


# ---- _DrownBList / _DrownRList (seg5:2D16 / 2D66) — mark drowning ants ----
@pytest.mark.parametrize("routine,off,count_off,x_off,caste_off,mark_off,fn_name", [
    ("_DrownBList", 0x2D16, 0x99D4, 0x392C, 0x3D18, 0x3B22, "drown_b_list"),
    ("_DrownRList", 0x2D66, 0x72CC, 0x42FA, 0x46E6, 0x44F0, "drown_r_list"),
])
@pytest.mark.parametrize("x,slots", [
    # (slot_x, caste) pairs; x is the flood column being tested
    (5, [(5, 0x08), (5, 0x00), (6, 0x08)]),      # match+markable, dead, no-x-match
    (5, [(5, 0x60)]),                              # sub=0xC -> excluded (>=0xC)
    (5, [(5, 0x58)]),                              # sub=0xB -> included (boundary)
    (5, [(5, 0x80)]),                              # sub=0 -> excluded (<=0)
    (5, []),                                        # empty list
])
def test_drownlist_state_diff_matches_asm(routine, off, count_off, x_off,
                                          caste_off, mark_off, fn_name, x, slots):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, count_off, len(slots))
        for i, (sx, caste) in enumerate(slots):
            m.mem.wb(sdg, x_off + i, sx)
            m.mem.wb(sdg, caste_off + i, caste)
            m.mem.wb(sdg, mark_off + i, 0xAA)     # poison the mark field

    lo, hi = min(x_off, caste_off, mark_off), max(x_off, caste_off, mark_off) + 0x10
    pack_lo, pack_hi = count_off & ~0xFF, (count_off & ~0xFF) + 0x100
    regions = [(_SDG, lo, hi), (_PACK, pack_lo, pack_hi)]

    results = _run_and_diff_segs(5, off, (x,), lambda s, p: fn(p, s, x), regions,
                                 seed_fn=seed)
    for (label, asm_after, rec_after), (_si, rlo, _hi) in zip(results, regions):
        assert asm_after == rec_after, (
            f"{routine} x={x} slots={slots} {label}: {_first_diff(asm_after, rec_after, rlo)}")


# ---- _ClearListB/R (seg5:30E8/30F4), _KillSpider (5:53D4) — trivial resets -
@pytest.mark.parametrize("routine,off,count_off,fn_name", [
    ("_ClearListB", 0x30E8, 0x99D4, "clear_list_b"),
    ("_ClearListR", 0x30F4, 0x72CC, "clear_list_r"),
])
@pytest.mark.parametrize("initial", [0, 1, 500])
def test_clearlist_state_diff_matches_asm(routine, off, count_off, fn_name, initial):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)

    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], count_off, initial)

    lo, hi = count_off & ~0xFF, (count_off & ~0xFF) + 0x100
    results = _run_and_diff_segs(5, off, (), lambda p: fn(p), [(_PACK, lo, hi)],
                                 seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, f"{routine}: {_first_diff(asm_after, rec_after, lo)}"


def test_killspider_state_diff_matches_asm():
    from simant.recovered.gameplay import kill_spider

    def seed(m):
        pack = m.seg_bases[_PACK]
        for off in (0x729E, 0x72E0, 0x7290):
            m.mem.ww(pack, off, 0xBEEF & 0xFFFF)

    results = _run_and_diff_segs(5, 0x53D4, (), lambda p: kill_spider(p),
                                 [(_PACK, 0x7200, 0x7300)], seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, _first_diff(asm_after, rec_after, 0x7200)


# ---- _CompactListA/B/R (seg5:2A16/2A7A/2ADE) — bulk dead-entry sweep ------
@pytest.mark.parametrize("routine,off,count_off,caste_off,f0,f1,fc,fe,fn_name", [
    ("_CompactListA", 0x2A16, 0x80F0, 0x2F62, 0x23A4, 0x278E, 0x2B78, 0x334C,
     "compact_list_a"),
    ("_CompactListB", 0x2A7A, 0x99D4, 0x3D18, 0x3736, 0x392C, 0x3B22, 0x3F0E,
     "compact_list_b"),
    ("_CompactListR", 0x2ADE, 0x72CC, 0x46E6, 0x4104, 0x42FA, 0x44F0, 0x48DC,
     "compact_list_r"),
])
@pytest.mark.parametrize("castes", [
    [1, 0, 2, 0, 0, 3],       # holes scattered through the middle/end
    [0, 0, 0],                # all dead
    [1, 2, 3],                # none dead -> no-op
    [],                       # empty list
    [0, 1],                   # dead first, alive second
])
def test_compactlist_state_diff_matches_asm(routine, off, count_off, caste_off,
                                            f0, f1, fc, fe, fn_name, castes):
    import simant.recovered.gameplay as G
    fn = getattr(G, fn_name)
    count = len(castes)

    def seed(m):
        pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
        m.mem.ww(pack, count_off, count)
        for i, caste in enumerate(castes):
            m.mem.wb(sdg, caste_off + i, caste)
            m.mem.wb(sdg, f0 + i, (i * 3 + 1) & 0xFF)
            m.mem.wb(sdg, f1 + i, (i * 5 + 2) & 0xFF)
            m.mem.wb(sdg, fc + i, (i * 7 + 3) & 0xFF)
            m.mem.wb(sdg, fe + i, (i * 11 + 4) & 0xFF)

    regions = [(_SDG, min(caste_off, f0, f1, fc, fe), max(caste_off, f0, f1, fc, fe) + 0x10),
              (_PACK, count_off & ~0xFF, (count_off & ~0xFF) + 0x100)]
    results = _run_and_diff_segs(5, off, (), lambda s, p: fn(p, s), regions,
                                 seed_fn=seed)
    for (label, asm_after, rec_after), (_si, rlo, _hi) in zip(results, regions):
        assert asm_after == rec_after, (
            f"{routine} castes={castes} {label}: {_first_diff(asm_after, rec_after, rlo)}")


# ---- _SetAntIndex (seg5:584A) — overwrite an existing ant record's fields --
@pytest.mark.parametrize("list_type,which", [(0, "a"), (1, "a"), (2, "b"),
                                             (3, "r"), (99, "r")])
@pytest.mark.parametrize("slot,count", [(0, 5), (4, 5), (5, 5), (6, 5), (0, 0)])
def test_setantindex_state_diff_matches_asm(list_type, which, slot, count):
    from simant.recovered.gameplay import set_ant_index
    t0, t1, caste, fc, fe = 9, 19, 3, 7, 11
    count_off = {"a": 0x80F0, "b": 0x99D4, "r": 0x72CC}[which]
    sdg_region = {"a": _ADDANTA_REGIONS[1], "b": _ADDANTB_REGIONS[1],
                 "r": _ADDANTR_REGIONS[1]}[which]
    pack_region = {"a": _ADDANTA_REGIONS[2], "b": _ADDANTB_REGIONS[2],
                  "r": _ADDANTR_REGIONS[2]}[which]
    regions = [sdg_region, pack_region]

    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], count_off, count)

    results = _run_and_diff_segs(
        5, 0x584A, (list_type, slot, t0, t1, caste, fc, fe),
        lambda s, p: set_ant_index(p, s, list_type, slot, t0, t1, caste, fc, fe),
        regions, seed_fn=seed)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, regions):
        assert asm_after == rec_after, (
            f"list_type={list_type} slot={slot} count={count} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _GetSmellT (seg6:9612) — read the trail-scent grid via a direction ----
# Pure read (no mutation): seed via `_run_and_get_ax`'s own internal machine,
# capture AX, then feed that SAME machine's data to the recovered function.
@pytest.mark.parametrize("p,q,direction,is_red", [
    (10, 10, 0, 0), (10, 10, 1, 0), (10, 10, 3, 1), (0, 0, 2, 0),
    (63, 31, 5, 1), (5, 5, 8, 0), (0, 0, 0, 1),
])
def test_getsmellt_matches_asm(p, q, direction, is_red):
    from simant.recovered.gameplay import get_smell_t

    def seed(m):
        sdg_base = m.seg_bases[_SDG]
        for i in range(0x800):
            m.mem.wb(sdg_base, 0x6AD2 + i, (i * 3 + 7) & 0xFF)
            m.mem.wb(sdg_base, 0x7AD2 + i, (i * 5 + 11) & 0xFF)

    ax, m = _run_and_get_ax(6, 0x9612, (p, q, direction, is_red), seed_fn=seed,
                            near=True)
    sdg_view = ByteBackend(m.mem.block(m.seg_bases[_SDG], 0, 0x10000), 0)
    expect = get_smell_t(sdg_view, p, q, direction, is_red)
    assert ax == (expect & 0xFFFF), f"asm={ax:#06x} rec={expect:#06x}"


# ---- _DecTSmell (seg6:95B6) — single-cell trail-scent decrement -----------
@pytest.mark.parametrize("x,y,is_red,existing", [
    (0, 0, 0, 5), (0, 0, 1, 5), (126, 62, 0, 1), (126, 62, 1, 1),
    (64, 32, 0, 0), (64, 32, 1, 0),   # already-0 cell -> no underflow
])
def test_dectsmell_state_diff_matches_asm(x, y, is_red, existing):
    from simant.recovered.gameplay import dec_t_smell
    idx = ((x >> 1) << 5) + (y >> 1)
    base = 0x7AD2 if is_red else 0x6AD2

    def seed(m):
        m.mem.wb(m.seg_bases[_SDG], base + idx, existing)

    results = _run_and_diff_segs(6, 0x95B6, (x, y, is_red),
                                 lambda s: dec_t_smell(s, x, y, is_red),
                                 [(_SDG, base, base + _JAM_REGION_SPAN)], near=True,
                                 seed_fn=seed)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, _first_diff(asm_after, rec_after, base)


# ---- _TileCanBeMovedOn (seg5:9342) — movement/self-exclusion predicate ----
# Pure read (no mutation): the plane<=1 threshold flag lives behind the same
# DGROUP pointer-global (0xC4AC) `_IsNotBarrier`/`_IsNotObstacle` read; seed it
# alongside the target map tile (and, for the extended-band y==0 case, the
# neighbour tile at (x, y+1) on the same plane).
def _tilecanbemovedon_seed(plane, x, y, tile, inside, neighbor):
    def seed(m):
        dg = m.seg_bases[hooks.DG_SEG_INDEX]
        world = m.mem.rw(dg, 0xC4AC)
        m.mem.wb(world, 0x9B6E, 1 if inside else 0)
        if plane <= 1:
            in_range = 0 <= x <= 0x7F and 0 <= y <= 0x3F
            base = 0x28E8
        else:
            in_range = 0 <= x <= 0x3F and 0 <= y <= 0x3F
            base = 0x48E8 if plane == 2 else 0x58E8
        if in_range:
            m.mem.wb(dg, base + (x << 6) + y, tile)
            if neighbor is not None:
                m.mem.wb(dg, base + (x << 6) + y + 1, neighbor)
    return seed


@pytest.mark.parametrize(
    "plane,x,y,tile,inside,cand_plane,cand_x,cand_y,check_adjacent,neighbor", [
    # plane<=1: threshold gate (0x53 / 0x90), trailing args unused
    (0, 0x10, 0x10, 0x53, False, 0, 0, 0, False, None),
    (0, 0x10, 0x10, 0x54, False, 0, 0, 0, False, None),
    (1, 0x10, 0x10, 0x90, True, 0, 0, 0, False, None),
    (1, 0x10, 0x10, 0x91, True, 0, 0, 0, False, None),
    (0, 0x80, 0x10, 0x00, False, 0, 0, 0, False, None),   # x out of range
    (0, 0x10, 0x40, 0x00, False, 0, 0, 0, False, None),   # y out of range
    # plane>1 out of range
    (2, 0x40, 0x10, 0x00, False, 0, 0, 0, False, None),
    (3, 0x10, 0x40, 0x00, False, 0, 0, 0, False, None),
    # not-clear tile -> 0 regardless of check_adjacent
    (2, 0x10, 0x10, 0x19, False, 2, 0x10, 0x10, False, None),
    (3, 0x10, 0x10, 0x2F, True, 3, 0x10, 0x10, False, None),
    # hard-clear (<=0x18 or pebble), y>1 -> 1 unconditionally
    (2, 0x10, 0x05, 0x00, False, 9, 0, 0, False, None),
    (3, 0x10, 0x05, 0x30, False, 9, 0, 0, False, None),
    (2, 0x10, 0x05, 0x31, False, 9, 0, 0, False, None),
    # hard-clear, y<=1, cand_plane != plane -> 1
    (2, 0x10, 0x00, 0x18, False, 3, 0, 0, False, None),
    # hard-clear, y==0, check_adjacent=False
    (2, 0x10, 0x00, 0x18, False, 2, 0x10, 0x00, False, None),  # match -> 1
    (2, 0x10, 0x00, 0x18, False, 2, 0x11, 0x00, False, None),  # x mismatch -> 0
    (2, 0x10, 0x00, 0x18, False, 2, 0x10, 0x05, False, None),  # cand_y!=0 -> 0
    # hard-clear, y==1, check_adjacent=False
    (2, 0x10, 0x01, 0x18, False, 2, 0x10, 0x00, False, None),  # x match -> 1
    (2, 0x10, 0x01, 0x18, False, 2, 0x11, 0x05, False, None),  # cand_y!=0 -> 1
    (2, 0x10, 0x01, 0x18, False, 2, 0x11, 0x00, False, None),  # neither -> 0
    # hard-clear, check_adjacent=True, y!=0 -> 1
    (2, 0x10, 0x01, 0x18, False, 2, 0x00, 0x00, True, None),
    # hard-clear, check_adjacent=True, y==0
    (2, 0x10, 0x00, 0x18, False, 2, 0x10, 0x00, True, None),   # match -> 1
    (2, 0x10, 0x00, 0x18, False, 2, 0x11, 0x00, True, None),   # x mismatch -> 0
    (2, 0x10, 0x00, 0x18, False, 2, 0x10, 0x05, True, None),   # cand_y!=0 -> 0
    # extended (dirt band, check_adjacent=True)
    (2, 0x10, 0x01, 0x20, True, 2, 0x11, 0x00, True, None),    # x mismatch -> 0
    (2, 0x10, 0x01, 0x2E, True, 2, 0x10, 0x00, True, None),    # x match, y!=0 -> 1
    (2, 0x10, 0x00, 0x1C, True, 2, 0x10, 0x00, True, 0x1F),    # y==0, neighbor in-band -> 0
    (2, 0x10, 0x00, 0x1F, True, 2, 0x10, 0x00, True, 0x2E),    # y==0, neighbor in-band(edge) -> 0
    (2, 0x10, 0x00, 0x20, True, 2, 0x10, 0x00, True, 0x18),    # y==0, neighbor out-of-band -> 1
    (2, 0x10, 0x00, 0x2E, True, 2, 0x10, 0x00, True, 0x2F),    # y==0, neighbor just above band -> 1
    (3, 0x05, 0x01, 0x1F, True, 3, 0x05, 0x00, True, None),    # red-colony plane, x match y!=0 -> 1
])
def test_tilecanbemovedon_matches_asm(plane, x, y, tile, inside, cand_plane,
                                      cand_x, cand_y, check_adjacent, neighbor):
    from simant.recovered.gameplay import tile_can_be_moved_on
    ax, m = _run_and_get_ax(
        5, 0x9342,
        (plane, x, y, cand_plane, cand_x, cand_y, 1 if check_adjacent else 0),
        seed_fn=_tilecanbemovedon_seed(plane, x, y, tile, inside, neighbor))
    dg_view = ByteBackend(m.mem.block(m.seg_bases[hooks.DG_SEG_INDEX], 0, 0x10000), 0)
    expect = tile_can_be_moved_on(dg_view, inside, plane, x, y, cand_plane, cand_x,
                                  cand_y, check_adjacent)
    assert ax == (expect & 0xFFFF), (
        f"(p={plane},{x:#x},{y:#x},t={tile:#x},in={inside},"
        f"cand=({cand_plane},{cand_x:#x},{cand_y:#x}),adj={check_adjacent}): "
        f"asm={ax:#06x} rec={expect:#06x}")


# ---- _GetMyBestDirs (seg6:8828) — my-ant pathfinding (composes 3 recovered --
# routines: _GetDis, _TileCanBeMovedOn, _IsClearTile/_GetLife via raw reads) --
# Pure read: seeds the 8-neighbour map/life cells around (cur_x, cur_y), the
# PACK "candidate site" fields _GetMyBestDirs reads internally, and the same
# [0xC4AC] world flag `_TileCanBeMovedOn` needs for its plane<=1 branch.
GET_BEST_DIR_DX = (0, 1, 1, 1, 0, -1, -1, -1)
GET_BEST_DIR_DY = (-1, -1, 0, 1, 1, 1, 0, -1)


def _getmybestdirs_seed(plane, cur_x, cur_y, tiles, lifes, inside, check_adjacent,
                        cand_plane, cand_x, cand_y):
    def seed(m):
        from simant.recovered.gameplay import map_cell_offset, life_cell_offset
        dg = m.seg_bases[hooks.DG_SEG_INDEX]
        pack = m.seg_bases[_PACK]
        world = m.mem.rw(dg, 0xC4AC)
        m.mem.wb(world, 0x9B6E, 1 if inside else 0)
        m.mem.ww(pack, 0x9BC4, 2 if check_adjacent else 0)
        m.mem.ww(pack, 0x9BE0, cand_plane & 0xFFFF)
        m.mem.ww(pack, 0x80C6, cand_x & 0xFFFF)
        m.mem.ww(pack, 0x80D2, cand_y & 0xFFFF)
        for si in range(8):
            nx, ny = cur_x + GET_BEST_DIR_DX[si], cur_y + GET_BEST_DIR_DY[si]
            moff = map_cell_offset(plane, nx, ny)
            if moff is not None:
                m.mem.wb(dg, moff & 0xFFFF, tiles.get(si, 0x40))
            loff = life_cell_offset(plane, nx, ny)
            if loff is not None:
                m.mem.wb(dg, loff & 0xFFFF, lifes.get(si, 0))
    return seed


@pytest.mark.parametrize(
    "plane,cur_x,cur_y,tgt_x,tgt_y,tiles,lifes,inside,check_adjacent,"
    "cand_plane,cand_x,cand_y", [
    # already at target -> -1, no scan needed (tiles/lifes irrelevant)
    (2, 20, 20, 20, 20, {}, {}, False, False, 0, 0, 0),
    # everything blocked (default tile 0x40) -> best_any=-2 sentinel, both -1/-2
    (2, 20, 20, 25, 25, {}, {}, False, False, 0, 0, 0),
    # one clear unoccupied direction (si=3, tile<0x18) among blocked others
    (2, 20, 20, 25, 25, {3: 0x05}, {}, False, False, 0, 0, 0),
    # two clear directions at different distances -> picks the closer one
    (2, 20, 20, 25, 25, {3: 0x05, 2: 0x05}, {}, False, False, 0, 0, 0),
    # clear but OCCUPIED (life>0) -> falls back to best_any, not best_clear
    (2, 20, 20, 25, 25, {3: 0x05}, {3: 7}, False, False, 0, 0, 0),
    # clear+occupied AND a separate clear+unoccupied -> clear wins over any
    (2, 20, 20, 25, 25, {3: 0x05, 5: 0x06}, {3: 7}, False, False, 0, 0, 0),
    # pebble tile (0x30-0x31) also counts as hard-clear
    (2, 20, 20, 25, 25, {6: 0x30}, {}, False, False, 0, 0, 0),
    # near a boundary so some directions are out-of-range (skipped, not crash)
    (2, 0, 0, 5, 5, {3: 0x05}, {}, False, False, 0, 0, 0),
    # candidate-site self-exclusion suppresses the one clear direction
    (2, 20, 20, 25, 25, {3: 0x05}, {}, False, False, 2, 21, 19),
    # check_adjacent + extended dirt-band tile, neighbour clear -> counts
    (2, 20, 20, 25, 25, {3: 0x22}, {}, False, True, 2, 21, 19),
    # yard plane (plane<=1), inside=False threshold gate
    (0, 20, 20, 25, 25, {3: 0x50}, {}, False, False, 0, 0, 0),
    (0, 20, 20, 25, 25, {3: 0x54}, {}, False, False, 0, 0, 0),
    # yard plane, inside=True widens the threshold
    (1, 20, 20, 25, 25, {3: 0x80}, {}, True, False, 0, 0, 0),
])
def test_getmybestdirs_matches_asm(plane, cur_x, cur_y, tgt_x, tgt_y, tiles,
                                   lifes, inside, check_adjacent, cand_plane,
                                   cand_x, cand_y):
    from simant.recovered.gameplay import get_my_best_dirs
    ax, m = _run_and_get_ax(
        6, 0x8828, (plane, cur_x, cur_y, tgt_x, tgt_y),
        seed_fn=_getmybestdirs_seed(plane, cur_x, cur_y, tiles, lifes, inside,
                                    check_adjacent, cand_plane, cand_x, cand_y))
    dg_view = ByteBackend(m.mem.block(m.seg_bases[hooks.DG_SEG_INDEX], 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(m.seg_bases[_PACK], 0, 0x10000), 0)
    expect = get_my_best_dirs(dg_view, pack_view, inside, plane, cur_x, cur_y,
                              tgt_x, tgt_y)
    assert ax == (expect & 0xFFFF), (
        f"(p={plane},cur=({cur_x},{cur_y}),tgt=({tgt_x},{tgt_y}),tiles={tiles},"
        f"lifes={lifes},in={inside},adj={check_adjacent},"
        f"cand=({cand_plane},{cand_x},{cand_y})): asm={ax:#06x} rec={expect:#06x}")


# ---- _GetMyNextRandDirs (seg6:8BEA) — probe ahead, dispatch on the outcome
# Composes get_my_best_dirs + get_my_rand_dirs (via the SAME PACK-resident
# out1/out2 cells get_my_initial_rand_dir uses).
_GETMYNEXTRANDDIRS_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_SDG, 0, 0x9000),
    (_PACK, 0x7200, 0xA100),
]


def _getmynextranddirs_seed(plane, x, y, tgt_x, tgt_y, tiles, inside,
                            check_adjacent, cand_plane, cand_x, cand_y,
                            avoid_x, avoid_y, out1_init, out2_init):
    from simant.recovered.gameplay import map_cell_offset

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        m.mem.ww(pack, 0x9BC4, 2 if check_adjacent else 0)
        m.mem.ww(pack, 0x9BE0, cand_plane & 0xFFFF)
        m.mem.ww(pack, 0x80C6, cand_x & 0xFFFF)
        m.mem.ww(pack, 0x80D2, cand_y & 0xFFFF)
        m.mem.ww(pack, 0xA0D6, avoid_x & 0xFFFF)
        m.mem.ww(pack, 0xA0DA, avoid_y & 0xFFFF)
        m.mem.ww(pack, 0x78A4, out1_init & 0xFFFF)
        m.mem.ww(pack, 0xA0D8, out2_init & 0xFFFF)
        m.mem.ww(pack, 0x72E4, 0x9999)   # garbage sentinel, only touched on one path
        for si in range(8):
            nx, ny = x + GET_BEST_DIR_DX[si], y + GET_BEST_DIR_DY[si]
            moff = map_cell_offset(plane, nx, ny)
            if moff is not None:
                m.mem.wb(dg, moff & 0xFFFF, tiles.get(si, 0x40))
    return seed


@pytest.mark.parametrize(
    "plane,x,y,tgt_x,tgt_y,tiles,inside,check_adjacent,cand_plane,cand_x,"
    "cand_y,avoid_x,avoid_y,out1_init,out2_init,label", [
    (2, 20, 20, 20, 20, {}, False, False, 0, 0, 0, -100, -100, 0, 3,
     "already-at-target-immediate-neg1"),
    (2, 20, 20, 25, 25, {}, False, False, 0, 0, 0, -100, -100, 0, 3,
     "all-blocked-immediate-neg2-falls-to-randdirs"),
    (2, 20, 20, 25, 25, {3: 0x05}, False, False, 0, 0, 0, -100, -100, 0, 3,
     "one-clear-step-then-default-terrain"),
])
def test_getmynextranddirs_state_diff_matches_asm(
        plane, x, y, tgt_x, tgt_y, tiles, inside, check_adjacent, cand_plane,
        cand_x, cand_y, avoid_x, avoid_y, out1_init, out2_init, label):
    from simant.recovered.gameplay import get_my_next_rand_dirs
    results = _run_and_diff_segs(
        6, 0x8BEA, (plane, x, y, tgt_x, tgt_y),
        lambda d, s, p: get_my_next_rand_dirs(d, p, plane, x, y, tgt_x, tgt_y),
        _GETMYNEXTRANDDIRS_REGIONS,
        seed_fn=_getmynextranddirs_seed(
            plane, x, y, tgt_x, tgt_y, tiles, inside, check_adjacent,
            cand_plane, cand_x, cand_y, avoid_x, avoid_y, out1_init, out2_init))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _GETMYNEXTRANDDIRS_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _GetMyBestDir (seg6:8D3A) — stuck-sentinel gate + the SAME probe walk
# as get_my_next_rand_dirs. Composes get_my_best_dirs, get_dir, get_my_rand_dirs.
def _getmybestdir_seed(plane, x, y, tgt_x, tgt_y, tiles, sentinel_72e4,
                       out1_init=0, out2_init=3):
    base = _getmynextranddirs_seed(
        plane, x, y, tgt_x, tgt_y, tiles, False, False, 0, 0, 0, -100, -100,
        out1_init, out2_init)

    def seed(m):
        base(m)
        m.mem.ww(m.seg_bases[_PACK], 0x72E4, sentinel_72e4 & 0xFFFF)
    return seed


@pytest.mark.parametrize(
    "plane,x,y,tgt_x,tgt_y,tiles,sentinel_72e4,label", [
    (2, 20, 20, 20, 20, {}, 0, "normal-path-already-at-target"),
    (2, 20, 20, 20, 20, {}, 0xFFFF, "stuck-retry-succeeds-neg1-returned-asis"),
    (2, 20, 20, 25, 25, {}, 0xFFFF, "stuck-retry-neg2-but-sentinel-mismatch"),
    (2, 20, 20, 25, 25, {}, 0xFFFE, "stuck-double-confirmed-commits-randdirs"),
])
def test_getmybestdir_state_diff_matches_asm(plane, x, y, tgt_x, tgt_y, tiles,
                                             sentinel_72e4, label):
    from simant.recovered.gameplay import get_my_best_dir
    results = _run_and_diff_segs(
        6, 0x8D3A, (plane, x, y, tgt_x, tgt_y),
        lambda d, s, p: get_my_best_dir(d, p, plane, x, y, tgt_x, tgt_y),
        _GETMYNEXTRANDDIRS_REGIONS,
        seed_fn=_getmybestdir_seed(plane, x, y, tgt_x, tgt_y, tiles, sentinel_72e4))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _GETMYNEXTRANDDIRS_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _GetMyRandDirs (seg6:8928) — sticky-direction search, 2 far-ptr I/O ---
# Two output words are passed as a genuine far pointer pair (offset, segment
# words on the stack); seeded/read back at fixed PACK offsets since any
# writable segment works for a caller-supplied pointer.
_RANDDIRS_OUT1_OFF, _RANDDIRS_OUT2_OFF = 0x9F00, 0x9F02


def _run_getmyranddirs_asm(plane, cur_x, cur_y, tgt_x, tgt_y, out1_init, out2_init,
                           tiles, lifes, inside, check_adjacent, cand_plane, cand_x,
                           cand_y, avoid_x, avoid_y):
    from simant.recovered.gameplay import map_cell_offset, life_cell_offset

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        world = m.mem.rw(dg, 0xC4AC)
        m.mem.wb(world, 0x9B6E, 1 if inside else 0)
        m.mem.ww(pack, 0x9BC4, 2 if check_adjacent else 0)
        m.mem.ww(pack, 0x9BE0, cand_plane & 0xFFFF)
        m.mem.ww(pack, 0x80C6, cand_x & 0xFFFF)
        m.mem.ww(pack, 0x80D2, cand_y & 0xFFFF)
        m.mem.ww(pack, 0xA0D6, avoid_x & 0xFFFF)
        m.mem.ww(pack, 0xA0DA, avoid_y & 0xFFFF)
        m.mem.ww(pack, _RANDDIRS_OUT1_OFF, out1_init & 0xFFFF)
        m.mem.ww(pack, _RANDDIRS_OUT2_OFF, out2_init & 0xFFFF)
        for si in range(8):
            nx, ny = cur_x + GET_BEST_DIR_DX[si], cur_y + GET_BEST_DIR_DY[si]
            moff = map_cell_offset(plane, nx, ny)
            if moff is not None:
                m.mem.wb(dg, moff & 0xFFFF, tiles.get(si, 0x40))
            loff = life_cell_offset(plane, nx, ny)
            if loff is not None:
                m.mem.wb(dg, loff & 0xFFFF, lifes.get(si, 0))

    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    pack_seg = m.seg_bases[_PACK]
    seed(m)
    s = m.cpu.s
    s.ds = m.seg_bases[hooks.DG_SEG_INDEX]
    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[6], 0x8928
    sp = s.sp
    args = (tgt_y, tgt_x, cur_y, cur_x, plane,
           pack_seg, _RANDDIRS_OUT2_OFF, pack_seg, _RANDDIRS_OUT1_OFF)
    for v in (*args, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    for _ in range(200_000):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
            break
    else:
        raise AssertionError("ASM _GetMyRandDirs did not return")
    out1 = m.mem.rw(pack_seg, _RANDDIRS_OUT1_OFF)
    out2 = m.mem.rw(pack_seg, _RANDDIRS_OUT2_OFF)
    return s.ax & 0xFFFF, out1, out2, m


@pytest.mark.parametrize(
    "plane,cur_x,cur_y,tgt_x,tgt_y,out1_init,out2_init,tiles,lifes,inside,"
    "check_adjacent,cand_plane,cand_x,cand_y,avoid_x,avoid_y", [
    # already at target -> -1, no field writes
    (2, 20, 20, 20, 20, 0, 3, {}, {}, False, False, 0, 0, 0, -100, -100),
    # nothing clear -> -2, no field writes
    (2, 20, 20, 25, 25, 0, 3, {}, {}, False, False, 0, 0, 0, -100, -100),
    # fresh search (out1=0): seed direction itself is clear -> forward hit
    (2, 20, 20, 25, 25, 0, 3, {3: 0x05}, {}, False, False, 0, 0, 0, -100, -100),
    # fresh search: seed blocked, but the symmetric backward neighbour is clear
    (2, 20, 20, 25, 25, 0, 3, {2: 0x05}, {}, False, False, 0, 0, 0, -100, -100),
    # fresh search: the only clear direction coincides with the "avoid" cell
    # -> forced blocked, falls back to a farther clear one instead
    (2, 20, 20, 25, 25, 0, 3, {3: 0x05, 6: 0x05}, {}, False, False, 0, 0, 0,
     23, 19),  # avoid = cur+DX[3],cur+DY[3] = (23,19)
    # re-entrant, out1>0 (forward-found last time): chosen1 still clear ->
    # recompute in place, distance improves -> writes out1=0 + new dir
    (2, 20, 20, 25, 25, 1, 3, {3: 0x05}, {}, False, False, 0, 0, 0, -100, -100),
    # re-entrant, out1<0 (backward-found last time): chosen2 still clear
    (2, 20, 20, 25, 25, 0xFFFF, 3, {3: 0x05}, {}, False, False, 0, 0, 0, -100, -100),
    # re-entrant, out1>0, chosen1 became blocked -> advances to find chosen1+1
    (2, 20, 20, 25, 25, 1, 3, {4: 0x05}, {}, False, False, 0, 0, 0, -100, -100),
    # re-entrant, out1<0, chosen2 became blocked -> advances to chosen2-1
    (2, 20, 20, 25, 25, 0xFFFF, 3, {2: 0x05}, {}, False, False, 0, 0, 0, -100, -100),
    # re-entrant, target already very close so the recomputed distance can't
    # improve -> returns the index with NO field writes (out1/out2 unchanged)
    (2, 20, 20, 21, 20, 1, 2, {2: 0x05}, {}, False, False, 0, 0, 0, -100, -100),
    # occupied life cell still counts as "clear" for movement purposes here
    # (unlike get_my_best_dirs, this routine has no occupied/clear split)
    (2, 20, 20, 25, 25, 0, 3, {3: 0x05}, {3: 9}, False, False, 0, 0, 0, -100, -100),
    # yard plane (plane<=1) threshold gate, fresh search
    (0, 20, 20, 25, 25, 0, 3, {3: 0x50}, {}, False, False, 0, 0, 0, -100, -100),
])
def test_getmyranddirs_matches_asm(plane, cur_x, cur_y, tgt_x, tgt_y, out1_init,
                                   out2_init, tiles, lifes, inside, check_adjacent,
                                   cand_plane, cand_x, cand_y, avoid_x, avoid_y):
    from simant.recovered.gameplay import get_my_rand_dirs
    asm_ax, asm_out1, asm_out2, m = _run_getmyranddirs_asm(
        plane, cur_x, cur_y, tgt_x, tgt_y, out1_init, out2_init, tiles, lifes,
        inside, check_adjacent, cand_plane, cand_x, cand_y, avoid_x, avoid_y)
    dg_view = ByteBackend(m.mem.block(m.seg_bases[hooks.DG_SEG_INDEX], 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(m.seg_bases[_PACK], 0, 0x10000), 0)
    out1, out2 = [out1_init], [out2_init]
    rec_ax = get_my_rand_dirs(dg_view, pack_view, out1, out2, inside, plane,
                              cur_x, cur_y, tgt_x, tgt_y) & 0xFFFF
    assert (rec_ax, out1[0] & 0xFFFF, out2[0] & 0xFFFF) == (asm_ax, asm_out1, asm_out2), (
        f"(p={plane},cur=({cur_x},{cur_y}),tgt=({tgt_x},{tgt_y}),o1i={out1_init:#x},"
        f"o2i={out2_init},tiles={tiles},lifes={lifes},adj={check_adjacent},"
        f"avoid=({avoid_x},{avoid_y})): asm=(ax={asm_ax:#06x},o1={asm_out1:#06x},"
        f"o2={asm_out2:#06x}) rec=(ax={rec_ax:#06x},o1={out1[0]&0xFFFF:#06x},"
        f"o2={out2[0]&0xFFFF:#06x})")


# ---- _GetMyInitialRandDir (seg6:8CDE) — commit a fresh get_my_rand_dirs ----
# search.  A plain FAR call (no far-pointer args of its own); its own stack
# has 4 leading unused words before plane/cur_x/cur_y/tgt_x/tgt_y.
_GETMYINITIALRANDDIR_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCBF4),
    (_PACK, 0x7000, 0xA100),
]


def _getmyinitialranddir_seed(plane, cur_x, cur_y, tgt_x, tgt_y, tiles, inside,
                              check_adjacent, cand_plane, cand_x, cand_y,
                              avoid_x, avoid_y):
    from simant.recovered.gameplay import map_cell_offset, life_cell_offset

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.wb(pack, 0x9B6E, 1 if inside else 0)
        m.mem.ww(pack, 0x9BC4, 2 if check_adjacent else 0)
        m.mem.ww(pack, 0x9BE0, cand_plane & 0xFFFF)
        m.mem.ww(pack, 0x80C6, cand_x & 0xFFFF)
        m.mem.ww(pack, 0x80D2, cand_y & 0xFFFF)
        m.mem.ww(pack, 0xA0D6, avoid_x & 0xFFFF)
        m.mem.ww(pack, 0xA0DA, avoid_y & 0xFFFF)
        m.mem.ww(pack, 0x78A4, 0x1234)   # garbage pre-state, must be overwritten
        m.mem.ww(pack, 0xA0D8, 0x5678)   # garbage pre-state, must be overwritten
        m.mem.ww(pack, 0x72E4, 0x9999)   # garbage, must become 0x10
        for si in range(8):
            nx, ny = cur_x + GET_BEST_DIR_DX[si], cur_y + GET_BEST_DIR_DY[si]
            moff = map_cell_offset(plane, nx, ny)
            if moff is not None:
                m.mem.wb(dg, moff & 0xFFFF, tiles.get(si, 0x40))
            loff = life_cell_offset(plane, nx, ny)
            if loff is not None:
                m.mem.wb(dg, loff & 0xFFFF, 0)
    return seed


@pytest.mark.parametrize(
    "plane,cur_x,cur_y,tgt_x,tgt_y,tiles,inside,check_adjacent,cand_plane,"
    "cand_x,cand_y,avoid_x,avoid_y", [
    (2, 20, 20, 25, 25, {3: 0x05}, False, False, 0, 0, 0, -100, -100),
    (2, 20, 20, 25, 25, {2: 0x05}, False, False, 0, 0, 0, -100, -100),
    (2, 20, 20, 20, 20, {}, False, False, 0, 0, 0, -100, -100),   # already at target
    (2, 20, 20, 25, 25, {}, False, False, 0, 0, 0, -100, -100),   # nothing clear
    (0, 20, 20, 25, 25, {3: 0x50}, False, False, 0, 0, 0, -100, -100),  # yard plane
    (2, 20, 20, 25, 25, {3: 0x05}, True, True, 2, 20, 20, -100, -100),  # inside + adjacent
])
def test_getmyinitialranddir_matches_asm(plane, cur_x, cur_y, tgt_x, tgt_y,
                                         tiles, inside, check_adjacent,
                                         cand_plane, cand_x, cand_y,
                                         avoid_x, avoid_y):
    from simant.recovered.gameplay import get_my_initial_rand_dir
    results = _run_and_diff_segs(
        6, 0x8CDE, (0, 0, 0, 0, plane, cur_x, cur_y, tgt_x, tgt_y),
        lambda d, p: get_my_initial_rand_dir(d, p, plane, cur_x, cur_y, tgt_x, tgt_y),
        _GETMYINITIALRANDDIR_REGIONS,
        seed_fn=_getmyinitialranddir_seed(
            plane, cur_x, cur_y, tgt_x, tgt_y, tiles, inside, check_adjacent,
            cand_plane, cand_x, cand_y, avoid_x, avoid_y))
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _GETMYINITIALRANDDIR_REGIONS):
        assert asm_after == rec_after, (
            f"plane={plane} cur=({cur_x},{cur_y}) tgt=({tgt_x},{tgt_y}) "
            f"tiles={tiles} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _CheckMyBestDirs (seg6:8B40) — walk get_my_best_dirs up to 64 steps ---
# Genuine caller of `_GetMyBestDirs` via the near-call/far-retf ABI bridge;
# each call can itself run for tens of thousands of CPU steps, so a full
# 64-step walk needs real headroom.
_CHECKDIRS_OUT_OFF = 0x9F04


def _run_checkmybestdirs_asm(plane, cur_x, cur_y, tgt_x, tgt_y, fill_tile, holes,
                             inside):
    from simant.recovered.gameplay import map_cell_offset

    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        world = m.mem.rw(dg, 0xC4AC)
        m.mem.wb(world, 0x9B6E, 1 if inside else 0)
        m.mem.ww(pack, 0x9BC4, 0)      # check_adjacent off
        m.mem.ww(pack, 0x9BE0, 0)      # cand_plane 0 -> never matches a real plane>0
        m.mem.ww(pack, _CHECKDIRS_OUT_OFF, 0xBEEF)   # poison
        lo, hi = (0, 0x40) if plane > 1 else (0, 0x80)
        for x in range(lo, hi):
            for y in range(0, 0x40):
                off = map_cell_offset(plane, x, y)
                if off is not None:
                    m.mem.wb(dg, off & 0xFFFF, fill_tile)
        for (hx, hy) in holes:
            off = map_cell_offset(plane, hx, hy)
            if off is not None:
                m.mem.wb(dg, off & 0xFFFF, 0x40)   # blocked

    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    pack_seg = m.seg_bases[_PACK]
    seed(m)
    s = m.cpu.s
    s.ds = m.seg_bases[hooks.DG_SEG_INDEX]
    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[6], 0x8B40
    sp = s.sp
    args = (tgt_y, tgt_x, cur_y, cur_x, plane, pack_seg, _CHECKDIRS_OUT_OFF)
    for v in (*args, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    for _ in range(1_500_000):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
            break
    else:
        raise AssertionError("ASM _CheckMyBestDirs did not return")
    out = m.mem.rw(pack_seg, _CHECKDIRS_OUT_OFF)
    return s.ax & 0xFFFF, out, m


@pytest.mark.parametrize("plane,cur_x,cur_y,tgt_x,tgt_y,fill_tile,holes,inside", [
    # already at target -> immediate failure, 0 steps, ax=-1
    (2, 20, 20, 20, 20, 0x05, (), False),
    # wide open field, short hop -> completes in a few steps (< 64)
    (2, 3, 3, 8, 3, 0x05, (), False),
    # wide open field, far target -> may hit the 64-step cap or finish close
    (2, 1, 1, 62, 62, 0x05, (), False),
    # walled in immediately -> fails on the very first get_my_best_dirs call
    (2, 20, 20, 40, 40, 0x40, (), False),
    # open a couple steps, then a wall -> fails partway through the loop
    (2, 20, 20, 40, 20, 0x05, tuple((x, 20) for x in range(23, 40)), False),
    # yard plane (plane<=1)
    (0, 3, 3, 10, 3, 0x50, (), False),
])
def test_checkmybestdirs_matches_asm(plane, cur_x, cur_y, tgt_x, tgt_y, fill_tile,
                                     holes, inside):
    from simant.recovered.gameplay import check_my_best_dirs, map_cell_offset
    asm_ax, asm_out, m = _run_checkmybestdirs_asm(
        plane, cur_x, cur_y, tgt_x, tgt_y, fill_tile, holes, inside)
    dg_view = ByteBackend(m.mem.block(m.seg_bases[hooks.DG_SEG_INDEX], 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(m.seg_bases[_PACK], 0, 0x10000), 0)
    out = [0xBEEF]
    rec_ax = check_my_best_dirs(dg_view, pack_view, out, inside, plane, cur_x,
                                cur_y, tgt_x, tgt_y) & 0xFFFF
    rec_out = out[0] & 0xFFFF
    assert (rec_ax, rec_out) == (asm_ax, asm_out), (
        f"(p={plane},cur=({cur_x},{cur_y}),tgt=({tgt_x},{tgt_y}),fill={fill_tile:#x},"
        f"holes={holes}): asm=(ax={asm_ax:#06x},out={asm_out:#06x}) "
        f"rec=(ax={rec_ax:#06x},out={rec_out:#06x})")


# ---- _GetRedBestDirs (seg6:9A18) — red-colony pathfinding, no PACK state ---
def _getredbestdirs_seed(plane, cur_x, cur_y, tiles, lifes, inside):
    def seed(m):
        from simant.recovered.gameplay import map_cell_offset, life_cell_offset
        dg = m.seg_bases[hooks.DG_SEG_INDEX]
        world = m.mem.rw(dg, 0xC4AC)
        m.mem.wb(world, 0x9B6E, 1 if inside else 0)
        for si in range(8):
            nx, ny = cur_x + GET_BEST_DIR_DX[si], cur_y + GET_BEST_DIR_DY[si]
            moff = map_cell_offset(plane, nx, ny)
            if moff is not None:
                m.mem.wb(dg, moff & 0xFFFF, tiles.get(si, 0x40))
            loff = life_cell_offset(plane, nx, ny)
            if loff is not None:
                m.mem.wb(dg, loff & 0xFFFF, lifes.get(si, 0))
    return seed


@pytest.mark.parametrize("plane,cur_x,cur_y,tgt_x,tgt_y,tiles,lifes,inside", [
    (2, 20, 20, 20, 20, {}, {}, False),                      # at target -> -1
    (2, 20, 20, 25, 25, {}, {}, False),                       # nothing clear
    (2, 20, 20, 25, 25, {3: 0x05}, {}, False),                 # one clear dir
    (2, 20, 20, 25, 25, {3: 0x05, 2: 0x05}, {}, False),        # picks closer
    (2, 20, 20, 25, 25, {3: 0x05}, {3: 7}, False),             # occupied fallback
    (2, 20, 20, 25, 25, {6: 0x30}, {}, False),                 # pebble hard-clear
    (2, 0, 0, 5, 5, {3: 0x05}, {}, False),                     # boundary-adjacent
    (0, 20, 20, 25, 25, {3: 0x50}, {}, False),                 # yard, inside=False
    (1, 20, 20, 25, 25, {3: 0x80}, {}, True),                  # yard, inside=True
])
def test_getredbestdirs_matches_asm(plane, cur_x, cur_y, tgt_x, tgt_y, tiles,
                                    lifes, inside):
    from simant.recovered.gameplay import get_red_best_dirs
    ax, m = _run_and_get_ax(
        6, 0x9A18, (plane, cur_x, cur_y, tgt_x, tgt_y),
        seed_fn=_getredbestdirs_seed(plane, cur_x, cur_y, tiles, lifes, inside))
    dg_view = ByteBackend(m.mem.block(m.seg_bases[hooks.DG_SEG_INDEX], 0, 0x10000), 0)
    expect = get_red_best_dirs(dg_view, inside, plane, cur_x, cur_y, tgt_x, tgt_y)
    assert ax == (expect & 0xFFFF), (
        f"(p={plane},cur=({cur_x},{cur_y}),tgt=({tgt_x},{tgt_y}),tiles={tiles},"
        f"lifes={lifes},in={inside}): asm={ax:#06x} rec={expect:#06x}")


# ---- _SmoothAlarm (seg6:9380) — 4-neighbour box blur of the alarm grid -----
# Snapshots the live grid [0x52D2..) into a scratch buffer [0x4AD2..) first,
# then blurs read-old/write-new; both bands are covered by one region.
_SMOOTHALARM_REGION = (_SDG, 0x4AD2, 0x5AD2)


def _smooth_alarm_seed(pattern):
    def seed(m):
        sdg = m.seg_bases[_SDG]
        for i in range(0x800):
            m.mem.wb(sdg, 0x52D2 + i, pattern(i) & 0xFF)
            m.mem.wb(sdg, 0x4AD2 + i, 0xEE)   # poison the scratch band up front
    return seed


@pytest.mark.parametrize("name,pattern", [
    ("zero", lambda i: 0),
    ("max", lambda i: 0xFF),
    ("ramp", lambda i: i * 37 + 13),
    ("sparse", lambda i: 0xFF if i % 97 == 0 else 0),
    ("checker", lambda i: 0xFF if ((i // 0x20) + (i % 0x20)) % 2 == 0 else 3),
    ("low", lambda i: (i * 7) % 9),   # exercises the <=8 -> 0 snap threshold
])
def test_smoothalarm_state_diff_matches_asm(name, pattern):
    from simant.recovered.gameplay import smooth_alarm

    results = _run_and_diff_segs(6, 0x9380, (), lambda s: smooth_alarm(s),
                                 [_SMOOTHALARM_REGION], near=True,
                                 seed_fn=_smooth_alarm_seed(pattern))
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, (
        f"{name}: {_first_diff(asm_after, rec_after, _SMOOTHALARM_REGION[1])}")


# ---- _FloodNestB (seg5:29DA) — flood the black colony's nest map plane ----
# Pure DGROUP transform on map plane 2 (0x48E8..); no SIMANT_DATA_GROUP/PACK
# involvement, so the plain single-segment `_run_and_diff` harness applies.
def _flood_nest_seed(pattern):
    def seed(mem, dg):
        base = 0x48E8
        for row in range(0x40):
            for col in range(0x40):
                mem.wb(dg, base + (row << 6) + col, pattern(row, col) & 0xFF)
    return seed


@pytest.mark.parametrize("name,pattern", [
    ("dirt_band", lambda row, col: 0x20 + ((row + col) % 0x0E)),   # 0x20..0x2D
    ("nfood_band", lambda row, col: (row + col) % 0x14),           # 0x00..0x13
    ("untouched_band", lambda row, col: 0x14 + ((row + col) % 0x1A)),  # 0x14..0x2D..>0x2D mix
    ("high_band", lambda row, col: 0x2E + ((row * 3 + col) % 0x50)),   # > 0x2D
    ("skip_cols", lambda row, col: 0x22 if col < 3 else 0x00),     # cols 0..2 never touched
    ("boundary", lambda row, col: 0x2D if (row + col) % 2 else 0x13),  # both edges of bands
])
def test_floodnestb_state_diff_matches_asm(name, pattern):
    from simant.recovered.gameplay import flood_nest_b
    asm_after, rec_after = _run_and_diff(
        5, 0x29DA, (), lambda v: flood_nest_b(v),
        seed_fn=_flood_nest_seed(pattern))
    assert asm_after == rec_after, f"{name}: {_first_diff(asm_after, rec_after)}"


def _sx(v):
    return v - 0x10000 if v & 0x8000 else v


def _first_diff(a, b, base=0):
    d = [i for i in range(len(a)) if a[i] != b[i]]
    return (f"{len(d)} differing bytes; first at "
            + ", ".join(f"{i + base:#06x}(asm={a[i]:#04x} rec={b[i]:#04x})"
                        for i in d[:6]))


# ---- _DoNestingB/_DoNestingR (seg6:44A8/690A) — nest dig/tend tick --------
# The largest orchestrators recovered this session. Composes get_enter_dir_b/r,
# get_exit_dir_b, place_egg_b/r, find_in_b/r_list, get_new_mode_b/r, and
# try_move_dir_b/r — all already recovered. Reuses _DONESTFIGHT_REGIONS'
# windows (wide enough for all of these) plus the mode-table seeding
# `_donestfight_seed` established and `_place_egg`'s own PACK dependencies
# (for the branch that spawns a fresh egg).
def _donesting_seed(x, y, slot, field_e_off, tile, tile_base, map_tile,
                    map_base, ac_food, ac_rate, ac_rate2, seed_val,
                    mode_base_hi=2, mode_base_lo=3, gate_flag=0,
                    tbl2=0x25, tbl6=0x30, tbl_direct=0x40, tbl_word=0x1122):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        life_idx = (x << 6) + y
        m.mem.ww(dg, 0xCBF2, seed_val)
        m.mem.wb(dg, tile_base + life_idx, tile)
        m.mem.wb(dg, map_base + life_idx, map_tile)
        m.mem.ww(dg, ac_food, 0)
        m.mem.ww(dg, ac_rate, 0)
        m.mem.ww(dg, ac_rate2, 0)
        m.mem.ww(pack, 0x9B6A, slot)
        m.mem.wb(sdg, field_e_off + slot, 0)   # overwritten by the caller below
        m.mem.ww(pack, 0x7690, mode_base_hi)
        m.mem.ww(pack, 0x9B8A, mode_base_lo)
        m.mem.ww(pack, 0x9FCE, gate_flag)
        for i in range(8):
            m.mem.wb(sdg, 0x89E6 + ((mode_base_hi << 3) + i), tbl2)
            m.mem.wb(sdg, 0x89E6 + ((mode_base_lo << 3) + i), tbl2)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_hi << 3) + i), tbl6)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_lo << 3) + i), tbl6)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, tbl_direct)
        m.mem.ww(sdg, 0x8A58, tbl_word)
        # _PlaceEggB/R's own dependencies (egg-spawn branch only, harmless
        # elsewhere): dig_tile_b/r + add_ant_to_b/r_list inputs.
        m.mem.ww(pack, 0x72C8, 3)
        m.mem.ww(pack, 0x7A56, 2)
        m.mem.ww(pack, 0x8104, 100)
        m.mem.ww(pack, 0x8106, 0)
        m.mem.ww(pack, 0x811A, 200)
        m.mem.ww(pack, 0x811C, 0)
        m.mem.ww(pack, 0x9DDC, 50)
        m.mem.ww(pack, 0x9DDE, 0)
        m.mem.ww(pack, 0x9DE2, 75)
        m.mem.ww(pack, 0x9DE4, 0)
        m.mem.ww(pack, 0x99D4, 5)   # B-list count
        m.mem.ww(pack, 0x72CC, 5)   # R-list count
        m.mem.wb(sdg, field_e_off + slot, 0)
    return seed


@pytest.mark.parametrize("x,y,slot,sub,mode,field_e,tile,map_tile,ac_val,"
                         "seed_val,label", [
    (20, 25, 0, 0, 3, 0x00, 0x00, 0x00, 99, 0x1234, "sub0-erosion-skipped"),
    (20, 25, 0, 0, 3, 0x00, 0x00, 0x10, 0, 0x1234, "sub0-erosion-refill"),
    (20, 25, 1, 1, 3, 0x00, 0x00, 0x00, 0, 0x1234, "sub1-subfield0-enterdir"),
    (20, 25, 1, 1, 3, 0x08, 0x00, 0x00, 0, 0x1234, "sub1-subfield1-tile0-eggspawn"),
    (20, 25, 1, 1, 3, 0x08, 0x03, 0x00, 0, 0x1234, "sub1-subfield1-tile3-noSpawn"),
    (20, 25, 2, 2, 3, 0x08, 0x00, 0x00, 0, 0x1234, "sub2-subfield1"),
    (20, 25, 2, 2, 3, 0x00, 0x03, 0x00, 0, 0x1234, "sub2-subfield0-tile3-notfound"),
    (20, 25, 2, 2, 3, 0x00, 0x00, 0x00, 0, 0x1234, "sub2-subfield0-tile0-erosionOrRefresh"),
    (20, 25, 0, 5, 3, 0x00, 0x00, 0x00, 0, 0xBEEF, "sub-other-default"),
])
def test_donestingb_state_diff_matches_asm(x, y, slot, sub, mode, field_e,
                                           tile, map_tile, ac_val, seed_val,
                                           label):
    import simant.recovered.gameplay as G
    life_base, map_base = 0x88E8, 0x48E8
    results = _run_and_diff_segs(
        6, 0x44A8, (x, y, mode, sub),
        lambda d, s, p: G.do_nesting_b(d, s, p, x, y, mode, sub),
        _DONESTFIGHT_REGIONS,
        seed_fn=_donesting_seed(x, y, slot, 0x3F0E, tile, life_base, map_tile,
                                map_base, 0xAC86, 0xAC82, 0xAC98, seed_val))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _DONESTFIGHT_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("x,y,slot,sub,mode,field_e,tile,map_tile,ac_val,"
                         "seed_val,label", [
    (20, 25, 0, 1, 3, 0x00, 0x00, 0x00, 0, 0x1234, "sub1-digroll"),
    (20, 25, 0, 1, 3, 0x00, 0x00, 0x00, 0, 0x0001, "sub1-altseed"),
    (20, 25, 1, 2, 3, 0x00, 0x03, 0x00, 0, 0x1234, "sub2-tile-search"),
    (20, 25, 1, 2, 3, 0x00, 0x00, 0x00, 0, 0x1234, "sub2-erosion-or-refresh"),
    (20, 25, 0, 5, 3, 0x00, 0x00, 0x00, 0, 0xBEEF, "sub-other-default"),
])
def test_donestingr_state_diff_matches_asm(x, y, slot, sub, mode, field_e,
                                           tile, map_tile, ac_val, seed_val,
                                           label):
    import simant.recovered.gameplay as G
    life_base, map_base = 0x98E8, 0x58E8
    results = _run_and_diff_segs(
        6, 0x690A, (x, y, mode, sub),
        lambda d, s, p: G.do_nesting_r(d, s, p, x, y, mode, sub),
        _DONESTFIGHT_REGIONS,
        seed_fn=_donesting_seed(x, y, slot, 0x48DC, tile, life_base, map_tile,
                                map_base, 0xAC88, 0xAC84, 0xACA4, seed_val))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _DONESTFIGHT_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _GetMyDir (seg6:8ECA) — target-select + the get_my_best_dir probe ----
# Composes check_my_best_dirs, get_my_best_dirs, get_my_rand_dirs, get_dir --
# all already recovered. Reuses _getmybestdir_seed's base (candidate-site
# PACK fields, out1/out2, sentinel) plus the four alternate-destination
# SDG table slots this routine additionally reads.
def _getmydir_seed(plane, cur_x, cur_y, sub, tgt_x, tgt_y, tiles, sentinel_72e4,
                   out1_init=0, out2_init=3):
    base = _getmybestdir_seed(plane, cur_x, cur_y, tgt_x, tgt_y, tiles,
                              sentinel_72e4, out1_init=out1_init,
                              out2_init=out2_init)

    def seed(m):
        base(m)
        sdg = m.seg_bases[_SDG]
        m.mem.ww(sdg, 0x835A, 15); m.mem.ww(sdg, 0x835C, 20)
        m.mem.ww(sdg, 0x835E, 16); m.mem.ww(sdg, 0x8360, 21)
        m.mem.ww(sdg, 0x8352, 17); m.mem.ww(sdg, 0x8354, 22)
        m.mem.ww(sdg, 0x8356, 18); m.mem.ww(sdg, 0x8358, 23)
    return seed


@pytest.mark.parametrize(
    "plane,cur_x,cur_y,sub,tgt_x,tgt_y,tiles,sentinel_72e4,label", [
    (0, 20, 20, 0, 25, 25, {}, 0, "yard-sub0-owntarget-normal-fail"),
    (0, 20, 20, 1, 25, 25, {3: 0x05}, 0xFFFF, "yard-sub1-owntarget-stuck-retry"),
    (1, 20, 20, 2, 25, 25, {}, 0, "yard-sub2-alttable-835A"),
    (1, 20, 20, 3, 25, 25, {}, 0xFFFE, "yard-sub3-alttable-835E-double-stuck"),
    (2, 20, 20, 2, 25, 25, {}, 0, "nest-plane2-subEqPlane-owntarget"),
    (2, 20, 20, 0, 25, 25, {}, 0xFFFF, "nest-plane2-subNe-alttable-8352"),
    (3, 20, 20, 0, 25, 25, {}, 0xFFFE, "nest-plane3-subNe-alttable-8356"),
])
def test_getmydir_state_diff_matches_asm(plane, cur_x, cur_y, sub, tgt_x,
                                         tgt_y, tiles, sentinel_72e4, label):
    from simant.recovered.gameplay import get_my_dir
    results = _run_and_diff_segs(
        6, 0x8ECA, (plane, cur_x, cur_y, sub, tgt_x, tgt_y),
        lambda d, s, p: get_my_dir(d, s, p, plane, cur_x, cur_y, sub, tgt_x, tgt_y),
        _GETMYNEXTRANDDIRS_REGIONS,
        seed_fn=_getmydir_seed(plane, cur_x, cur_y, sub, tgt_x, tgt_y, tiles,
                               sentinel_72e4))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _GETMYNEXTRANDDIRS_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _GetMyDis (seg6:8682) — cross-plane distance via SDG connector table -
# Pure: composes only get_dis, over the SAME 4-slot SDG coordinate table
# get_my_dir reads. No PACK/mutation at all -- a plain predicate oracle.
def _getmydis_seed(cur_x, cur_y, tgt_x, tgt_y):
    def seed(m):
        sdg = m.seg_bases[_SDG]
        m.mem.ww(sdg, 0x835A, 11); m.mem.ww(sdg, 0x835C, 12)   # table A
        m.mem.ww(sdg, 0x835E, 13); m.mem.ww(sdg, 0x8360, 14)   # table B
        m.mem.ww(sdg, 0x8352, 15); m.mem.ww(sdg, 0x8354, 16)   # table C
        m.mem.ww(sdg, 0x8356, 17); m.mem.ww(sdg, 0x8358, 18)   # table D
    return seed


@pytest.mark.parametrize("plane,cur_x,cur_y,tgt_plane,tgt_x,tgt_y,label", [
    (2, 20, 25, 2, 40, 45, "same-plane-direct"),
    (1, 20, 25, 2, 40, 45, "yard-to-nest2"),
    (1, 20, 25, 3, 40, 45, "yard-to-nest3"),
    (2, 20, 25, 1, 40, 45, "nest2-to-yard"),
    (3, 20, 25, 1, 40, 45, "nest3-to-yard"),
    (0, 20, 25, 1, 40, 45, "plane0-to-yard"),
    (2, 20, 25, 3, 40, 45, "nest2-to-nest3-threeleg"),
    (3, 20, 25, 2, 40, 45, "nest3-to-nest2-threeleg"),
    (1, 20, 25, 0, 40, 45, "yard-to-plane0-fallsto-catchall"),
])
def test_getmydis_matches_asm(plane, cur_x, cur_y, tgt_plane, tgt_x, tgt_y, label):
    from simant.recovered.gameplay import get_my_dis
    ax, m = _run_and_get_ax(
        6, 0x8682, (plane, cur_x, cur_y, tgt_plane, tgt_x, tgt_y),
        seed_fn=_getmydis_seed(cur_x, cur_y, tgt_x, tgt_y))
    sdg_view = ByteBackend(m.mem.block(m.seg_bases[_SDG], 0, 0x10000), 0)
    expect = get_my_dis(sdg_view, plane, cur_x, cur_y, tgt_plane, tgt_x, tgt_y)
    assert ax == (expect & 0xFFFF), (
        f"{label}: asm={ax:#06x} rec={expect & 0xFFFF:#06x}")


# ---- _FoodFall/_DropFoodA (seg5:0EAA/0D86) — yard falling-food physics ----
# food_fall's own tile writes + pack[0x9E84] counter; drop_food_a composes it
# for its own tile-4..7/0x27..0x3F branch.
_FOODFALL_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x22BE, 0x48E8),
    (_PACK, 0x9B00, 0x9F00),
]


def _foodfall_seed(x, y, tiles, delta_base, dx, dy, inside=None):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(pack, 0x9C66, delta_base & 0xFFFF)
        m.mem.wb(dg, (delta_base + 0x22BE) & 0xFFFF, dx & 0xFF)
        m.mem.wb(dg, (delta_base + 0x22C2) & 0xFFFF, dy & 0xFF)
        if inside is not None:
            m.mem.ww(pack, 0x9B6E, 1 if inside else 0)
        for (ox, oy), tile in tiles.items():
            m.mem.wb(dg, 0x28E8 + ((x + ox) << 6) + (y + oy), tile)
    return seed


@pytest.mark.parametrize("x,y,tiles,delta_base,dx,dy,label", [
    (20, 20, {}, 0, 1, 0, "default-tile0-immediate-harden"),
    (20, 20, {(0, 0): 10, (1, 0): 10, (2, 0): 10}, 0, 1, 0, "multi-step-then-harden"),
    (5, 5, {(0, 0): 10}, 0, 0x7F, 0, "out-of-bounds-terminates"),
    (20, 20, {(0, 0): 40}, 0, 1, 0, "no-op-tile-stays-nonhardenable-oob-eventually"),
])
def test_foodfall_state_diff_matches_asm(x, y, tiles, delta_base, dx, dy, label):
    from simant.recovered.gameplay import food_fall
    results = _run_and_diff_segs(
        5, 0xEAA, (x, y), lambda d, p: food_fall(d, p, x, y),
        _FOODFALL_REGIONS,
        seed_fn=_foodfall_seed(x, y, tiles, delta_base, dx, dy))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _FOODFALL_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("x,y,tile,inside,label", [
    (20, 20, 2, True, "inside-tile0-3-harden"),
    (20, 20, 12, True, "inside-tile8-0x17-reduce-then-harden"),
    (20, 20, 0x20, True, "inside-tile18-26-bump"),
    (20, 20, 5, True, "inside-tile4-7-scatter"),
    (20, 20, 0x30, True, "inside-tile27-3f-scatter"),
    (20, 20, 0x50, True, "inside-tile-ge-40-noop"),
    (20, 20, 0x10, False, "outside-tile-lt-48-force"),
    (20, 20, 0x49, False, "outside-tile-48-4a-bump"),
    (20, 20, 0x4B, False, "outside-tile-ge-4b-noop"),
])
def test_dropfooda_state_diff_matches_asm(x, y, tile, inside, label):
    from simant.recovered.gameplay import drop_food_a
    results = _run_and_diff_segs(
        5, 0xD86, (x, y), lambda d, p: drop_food_a(d, p, x, y),
        _FOODFALL_REGIONS,
        seed_fn=_foodfall_seed(x, y, {(0, 0): tile}, 0, 1, 0, inside=inside))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _FOODFALL_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _IsValidYard (seg7:2072) — bounds check for the 12x16 boy's-yard grid -
@pytest.mark.parametrize("x,y", [
    (0, 0), (0xB, 0xF), (6, 8), (-1, 8), (0xC, 8),
    (6, -1), (6, 0x10), (0xB, 0x10), (0xFFFF, 8),
])
def test_isvalidyard_matches_asm(x, y):
    from simant.recovered.gameplay import is_valid_yard
    ax, _m = _run_and_get_ax(7, 0x2072, (x, y))
    expect = is_valid_yard(x, y)
    assert ax == (expect & 0xFFFF), f"({x},{y}): asm={ax:#06x} rec={expect:#06x}"


# ---- _FindInLionList (seg7:4B12) — antlion list reverse search -----------
def _findinlionlist_seed(count, slots):
    def seed(m):
        sdg, pack = m.seg_bases[_SDG], m.seg_bases[_PACK]
        m.mem.ww(sdg, 0x8A88, count)
        for slot, (v0, v1) in enumerate(slots):
            m.mem.wb(pack, 0x809C + slot, v0)
            m.mem.wb(pack, 0x80BC + slot, v1)
    return seed


@pytest.mark.parametrize("count,slots,val0,val1,label", [
    (0, [], 5, 9, "empty-list-notfound"),
    (3, [(1, 1), (2, 2), (3, 3)], 2, 2, "found-middle-slot"),
    (3, [(1, 1), (2, 2), (3, 3)], 9, 9, "not-found"),
    (3, [(5, 9), (5, 9), (1, 1)], 5, 9, "duplicate-matches-picks-last-added"),
])
def test_findinlionlist_matches_asm(count, slots, val0, val1, label):
    from simant.recovered.gameplay import find_in_lion_list
    ax, m = _run_and_get_ax(7, 0x4B12, (val0, val1),
                            seed_fn=_findinlionlist_seed(count, slots))
    sdg_view = ByteBackend(m.mem.block(m.seg_bases[_SDG], 0, 0x10000), 0)
    pack_view = ByteBackend(m.mem.block(m.seg_bases[_PACK], 0, 0x10000), 0)
    expect = find_in_lion_list(pack_view, sdg_view, val0, val1)
    assert ax == (expect & 0xFFFF), f"{label}: asm={ax:#06x} rec={expect & 0xFFFF:#06x}"


# ---- _CheckNestFightB/R (seg6:3BA2/61A2) — nest combat trigger gate ------
# Composes is_yellow_ant, find_in_b/r_list, get_winner -- all already
# recovered. The unrecovered _YellowFight branch is gated off (dgroup[0xCE98]
# left 0, matching each routine's own "don't trigger YellowFight" polarity).
_CHECKNESTFIGHT_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x88E8, 0xCE9A),
    (_SDG, 0x3700, 0x8B00),
    (_PACK, 0x7200, 0xA100),
]


def _checknestfight_seed(x, y, life_base, tile, count_off, y_off, x_off,
                         caste_off, slots, cheat_flag=0, rand_state=0,
                         strength_tbl=None, outcome_tbl=None):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.wb(dg, life_base + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCE98, 0)
        m.mem.wb(sdg, 0x8A5C, cheat_flag)
        m.mem.ww(dg, RAND_STATE_OFF, rand_state & 0xFFFF)
        m.mem.ww(dg, (RAND_STATE_OFF + 2) & 0xFFFF, (rand_state >> 16) & 0xFFFF)
        for sub, v in (strength_tbl or {}).items():
            m.mem.wb(dg, 0x8902 + sub, v)
        for idx, v in (outcome_tbl or {}).items():
            m.mem.wb(dg, 0x8918 + idx, v)
        m.mem.ww(pack, count_off, len(slots))
        for slot, (yv, xv, cv) in enumerate(slots):
            m.mem.wb(sdg, y_off + slot, yv)
            m.mem.wb(sdg, x_off + slot, xv)
            m.mem.wb(sdg, caste_off + slot, cv)
    return seed


@pytest.mark.parametrize("which,off,life_base,count_off,y_off,x_off,"
                         "caste_off,tile,found_slot,label", [
    ("b", 0x3BA2, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x50, False,
     "b-below-range-noop"),
    ("b", 0x3BA2, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x90, False,
     "b-in-range-not-in-list"),
    ("b", 0x3BA2, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x90, True,
     "b-in-range-found-cheat-a-wins"),
    ("r", 0x61A2, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x05, False,
     "r-below-range-notyellow-noop"),
    ("r", 0x61A2, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x30, False,
     "r-in-range-not-in-list"),
    ("r", 0x61A2, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x30, True,
     "r-in-range-found-cheat-a-wins"),
])
def test_checknestfight_state_diff_matches_asm(which, off, life_base,
                                               count_off, y_off, x_off,
                                               caste_off, tile, found_slot,
                                               label):
    import simant.recovered.gameplay as G
    fn = G.check_nest_fight_b if which == "b" else G.check_nest_fight_r
    x, y, attacker = 20, 25, 0x08
    slots = [(x, y, tile)] if found_slot else []
    results = _run_and_diff_segs(
        6, off, (x, y, attacker),
        lambda d, s, p: fn(d, s, p, x, y, attacker),
        _CHECKNESTFIGHT_REGIONS,
        seed_fn=_checknestfight_seed(x, y, life_base, tile, count_off, y_off,
                                     x_off, caste_off, slots, cheat_flag=1))
    for (rlabel, asm_after, rec_after), (_si, lo2, _hi) in zip(
            results, _CHECKNESTFIGHT_REGIONS):
        assert asm_after == rec_after, f"{which} {label} {rlabel}: {_first_diff(asm_after, rec_after, lo2)}"


def test_checknestfightb_yellowfight_gate_raises():
    from simant.recovered.gameplay import check_nest_fight_b
    dg = bytearray(0x10000)
    dg[0x88E8] = 0xFE   # a yellow-ant marker tile
    dgv = ByteBackend(dg, 0)
    dgv.ww(0xCE98, 1)
    with pytest.raises(NotImplementedError):
        check_nest_fight_b(dgv, ByteBackend(bytearray(0x10000), 0),
                           ByteBackend(bytearray(0x10000), 0), 0, 0, 0x08)


def test_checknestfightr_yellowfight_gate_raises():
    from simant.recovered.gameplay import check_nest_fight_r
    dg = bytearray(0x10000)
    dg[0x98E8] = 0xFE   # a yellow-ant marker tile, out of the 8..0x67 range
    dgv = ByteBackend(dg, 0)
    dgv.ww(0xCE98, 0)
    with pytest.raises(NotImplementedError):
        check_nest_fight_r(dgv, ByteBackend(bytearray(0x10000), 0),
                           ByteBackend(bytearray(0x10000), 0), 0, 0, 0x08)


# ---- _SetAntLion (seg7:4AD8) — re-stamp an antlion's pit tile ------------
_SETANTLION_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0x48E8),
    (_PACK, 0x7D00, 0x8100),
]


def _setantlion_seed(slot, x, y, type_byte):
    def seed(m):
        pack = m.seg_bases[_PACK]
        m.mem.wb(pack, 0x7D4E + slot, type_byte)
        m.mem.wb(pack, 0x809C + slot, x)
        m.mem.wb(pack, 0x80BC + slot, y)
    return seed


@pytest.mark.parametrize("slot,x,y,type_byte,label", [
    (0, 5, 6, 0x00, "slot0-basic"),
    (3, 10, 12, 0x07, "nonzero-slot-and-type"),
])
def test_setantlion_state_diff_matches_asm(slot, x, y, type_byte, label):
    from simant.recovered.gameplay import set_ant_lion
    results = _run_and_diff_segs(
        7, 0x4AD8, (slot,), lambda d, p: set_ant_lion(d, p, slot),
        _SETANTLION_REGIONS, seed_fn=_setantlion_seed(slot, x, y, type_byte))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _SETANTLION_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _NotMowed (seg7:203E) — test-and-clear a per-cell grass bit ---------
def _notmowed_seed(index, word_val):
    def seed(m):
        m.mem.ww(m.seg_bases[_PACK], 0xA0B6 + (index << 1), word_val)
    return seed


@pytest.mark.parametrize("index,bit,word_val,label", [
    (0, 0, 0x0001, "bit0-set-clears"),
    (0, 0, 0x0000, "bit0-clear-noop"),
    (5, 7, 0x00A0, "midbit-set-clears"),
    (5, 7, 0x005F, "midbit-clear-noop"),
    (3, 15, 0x8000, "highbit-set-clears"),
])
def test_notmowed_state_diff_matches_asm(index, bit, word_val, label):
    from simant.recovered.gameplay import not_mowed
    regions = [(_PACK, 0xA000, 0xA100)]
    results = _run_and_diff_segs(
        7, 0x203E, (index, bit), lambda p: not_mowed(p, index, bit),
        regions, seed_fn=_notmowed_seed(index, word_val))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(results, regions):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"

    ax, _m = _run_and_get_ax(7, 0x203E, (index, bit),
                             seed_fn=_notmowed_seed(index, word_val))
    fresh_pack = bytearray(0x10000)
    ByteBackend(fresh_pack, 0).ww(0xA0B6 + (index << 1), word_val)
    expect = not_mowed(ByteBackend(fresh_pack, 0), index, bit)
    assert ax == (expect & 0xFFFF), f"{label} return-value: asm={ax:#06x} rec={expect:#06x}"


# ---- _DoRestB/R (seg6:367E/5D7E) — nest combat resolution + retreat ------
# Composes is_yellow_ant, find_in_b/r_list, get_winner, get_new_mode -- all
# already recovered. Reuses _donestfight_seed-style mode-table population
# plus _checknestfight_seed-style list seeding.
_DOREST_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x22E0, 0xCBF4),
    (_SDG, 0x3700, 0x8B00),
    (_PACK, 0x7200, 0xA100),
]


def _dorest_seed(x, y, life_base, tile, count_off, y_off, x_off, caste_off,
                 slots, acting_slot=0, acting_caste=0x03, cheat_flag=1,
                 mode_base_hi=2, mode_base_lo=3, gate_flag=0,
                 tbl2=0x25, tbl6=0x30, tbl_direct=0x40, tbl_word=0x1122):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.wb(dg, life_base + (x << 6) + y, tile)
        m.mem.ww(dg, 0xCE98, 0)
        m.mem.wb(sdg, 0x8A5C, cheat_flag)
        m.mem.ww(pack, count_off, len(slots))
        for slot, (yv, xv, cv) in enumerate(slots):
            m.mem.wb(sdg, y_off + slot, yv)
            m.mem.wb(sdg, x_off + slot, xv)
            m.mem.wb(sdg, caste_off + slot, cv)
        m.mem.ww(pack, 0x9B6A, acting_slot)
        m.mem.wb(sdg, caste_off + acting_slot, acting_caste)
        m.mem.ww(dg, RAND_STATE_OFF, 0)
        m.mem.ww(dg, (RAND_STATE_OFF + 2) & 0xFFFF, 0)
        m.mem.ww(pack, 0x7690, mode_base_hi)
        m.mem.ww(pack, 0x9B8A, mode_base_lo)
        m.mem.ww(pack, 0x9FCE, gate_flag)
        for i in range(8):
            m.mem.wb(sdg, 0x89E6 + ((mode_base_hi << 3) + i), tbl2)
            m.mem.wb(sdg, 0x89E6 + ((mode_base_lo << 3) + i), tbl2)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_hi << 3) + i), tbl6)
            m.mem.wb(sdg, 0x8A16 + ((mode_base_lo << 3) + i), tbl6)
        for s in range(8):
            m.mem.wb(sdg, 0x8A46 + s, tbl_direct)
        m.mem.ww(sdg, 0x8A58, tbl_word)
    return seed


@pytest.mark.parametrize("which,off,life_base,count_off,y_off,x_off,"
                         "caste_off,tile,found_slot,seed_val,label", [
    ("b", 0x367E, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x50, False, 1234,
     "b-out-of-range-retreat"),
    ("b", 0x367E, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x90, False, 1234,
     "b-in-range-not-in-list-retreat"),
    ("b", 0x367E, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x90, True, 1234,
     "b-in-range-found-fight"),
    ("r", 0x5D7E, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x05, False, 1234,
     "r-out-of-range-notyellow-retreat"),
    ("r", 0x5D7E, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x30, False, 1234,
     "r-in-range-not-in-list-retreat"),
    ("r", 0x5D7E, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x30, True, 1234,
     "r-in-range-found-fight"),
])
def test_dorest_state_diff_matches_asm(which, off, life_base, count_off,
                                       y_off, x_off, caste_off, tile,
                                       found_slot, seed_val, label):
    import simant.recovered.gameplay as G
    fn = G.do_rest_b if which == "b" else G.do_rest_r
    x, y, attacker = 20, 25, 0x08
    slots = [(x, y, tile)] if found_slot else []
    results = _run_and_diff_segs(
        6, off, (x, y, attacker), lambda d, s, p: fn(d, s, p, x, y, attacker),
        _DOREST_REGIONS,
        seed_fn=_dorest_seed(x, y, life_base, tile, count_off, y_off, x_off,
                             caste_off, slots, acting_slot=5, cheat_flag=1))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _DOREST_REGIONS):
        assert asm_after == rec_after, f"{which} {label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


def test_dorestb_yellowfight_gate_raises():
    from simant.recovered.gameplay import do_rest_b
    dg = bytearray(0x10000)
    dg[0x88E8] = 0xFE
    dgv = ByteBackend(dg, 0)
    dgv.ww(0xCE98, 1)
    with pytest.raises(NotImplementedError):
        do_rest_b(dgv, ByteBackend(bytearray(0x10000), 0),
                  ByteBackend(bytearray(0x10000), 0), 0, 0, 0x08)


def test_dorestr_yellowfight_gate_raises():
    from simant.recovered.gameplay import do_rest_r
    dg = bytearray(0x10000)
    dg[0x98E8] = 0xFE   # out of the 8..0x67 range
    dgv = ByteBackend(dg, 0)
    dgv.ww(0xCE98, 0)
    with pytest.raises(NotImplementedError):
        do_rest_r(dgv, ByteBackend(bytearray(0x10000), 0),
                  ByteBackend(bytearray(0x10000), 0), 0, 0, 0x08)


# ---- _DoRandB/R (seg6:3876/5F7A) — random wander tick ---------------------
# Composes get_new_mode_b/r, is_yellow_ant, find_in_b/r_list, get_winner,
# try_move_dir_b/r -- all already recovered. Reuses _dorest_seed's tables.
@pytest.mark.parametrize("which,off,life_base,count_off,y_off,x_off,"
                         "caste_off,tile,found_slot,sub,seed_val,label", [
    ("b", 0x3876, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x50, False, 1,
     4660, "b-out-of-range-wander"),
    ("b", 0x3876, 0x88E8, 0x99D4, 0x3736, 0x392C, 0x3D18, 0x90, True, 1,
     4660, "b-in-range-found-fight"),
    ("r", 0x5F7A, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x05, False, 1,
     4660, "r-out-of-range-notyellow-wander"),
    ("r", 0x5F7A, 0x98E8, 0x72CC, 0x4104, 0x42FA, 0x46E6, 0x30, True, 1,
     4660, "r-in-range-found-fight"),
])
def test_dorand_state_diff_matches_asm(which, off, life_base, count_off,
                                       y_off, x_off, caste_off, tile,
                                       found_slot, sub, seed_val, label):
    import simant.recovered.gameplay as G
    fn = G.do_rand_b if which == "b" else G.do_rand_r
    x, y, attacker = 20, 20, 0x08
    slots = [(x, y, tile)] if found_slot else []
    results = _run_and_diff_segs(
        6, off, (x, y, attacker, sub),
        lambda d, s, p: fn(d, s, p, x, y, attacker, sub),
        _DOREST_REGIONS,
        seed_fn=_dorest_seed(x, y, life_base, tile, count_off, y_off, x_off,
                             caste_off, slots, acting_slot=5, cheat_flag=1))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _DOREST_REGIONS):
        assert asm_after == rec_after, f"{which} {label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


def test_dorandb_yellowfight_gate_raises():
    from simant.recovered.gameplay import do_rand_b
    dg = bytearray(0x10000)
    dg[0x88E8] = 0xFE
    dgv = ByteBackend(dg, 0)
    dgv.ww(0xCE98, 1)
    dgv.ww(0xCBF2, 1)   # nonzero SRand seed -> skip the roll32==0 refresh path
    with pytest.raises(NotImplementedError):
        do_rand_b(dgv, ByteBackend(bytearray(0x10000), 0),
                 ByteBackend(bytearray(0x10000), 0), 0, 0, 0x08, 1)


def test_dorandr_yellowfight_gate_raises():
    from simant.recovered.gameplay import do_rand_r
    dg = bytearray(0x10000)
    dg[0x98E8] = 0xFE   # out of the 8..0x67 range
    dgv = ByteBackend(dg, 0)
    dgv.ww(0xCE98, 0)
    dgv.ww(0xCBF2, 1)
    with pytest.raises(NotImplementedError):
        do_rand_r(dgv, ByteBackend(bytearray(0x10000), 0),
                 ByteBackend(bytearray(0x10000), 0), 0, 0, 0x08, 1)


# ---- _ForceModeA/B (seg7:0550/0622) — force a mode-transition state ------
_FORCEMODE_REGIONS = [
    (hooks.DG_SEG_INDEX, 0x28E8, 0xCE82),
    (_SDG, 0x2300, 0x4000),
]


def _forcemode_seed(slot, x, y, tile, ce7e, ce80, cd88, counter_off,
                    field_c_off, field_e_off, x_off=None, y_off=None):
    def seed(m):
        dg, sdg = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG]
        if x_off is not None:
            m.mem.wb(sdg, x_off + slot, x)
            m.mem.wb(sdg, y_off + slot, y)
            m.mem.wb(dg, 0x28E8 + (x << 6) + y, tile)
        m.mem.wb(dg, 0xCE7E, ce7e)
        m.mem.ww(dg, 0xCE80, ce80)
        m.mem.ww(dg, 0xCD88, cd88 & 0xFFFF)
        m.mem.wb(sdg, counter_off + slot, 0x10)
        m.mem.wb(sdg, field_c_off + slot, 0x99)
        m.mem.wb(sdg, field_e_off + slot, 0x99)
    return seed


@pytest.mark.parametrize("mode,label", [
    (1, "mode1-bump-plus8"),
    (2, "mode2-noop-bump"),
    (3, "mode3-bump-minus8-and-maptile"),
    (4, "mode4-full-noop"),
    (5, "mode5-bump-minus18"),
    (7, "mode7-alias-of-3"),
    (9, "mode9-alias-of-5"),
    (10, "mode10-out-of-range-default"),
])
def test_forcemodea_state_diff_matches_asm(mode, label):
    from simant.recovered.gameplay import force_mode_a
    slot, x, y, arg3 = 3, 20, 20, 6
    results = _run_and_diff_segs(
        7, 0x0550, (slot, mode, arg3),
        lambda d, s: force_mode_a(d, s, slot, mode, arg3),
        _FORCEMODE_REGIONS,
        seed_fn=_forcemode_seed(slot, x, y, 0x10, 0x0F, 1, 0x28,
                                0x2F62, 0x2B78, 0x334C,
                                x_off=0x23A4, y_off=0x278E))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _FORCEMODE_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("mode,label", [
    (1, "mode1-caste-plus8"),
    (2, "mode2-noop-bump"),
    (3, "mode3-caste-minus8"),
    (4, "mode4-full-noop"),
    (5, "mode5-caste-minus18"),
    (10, "mode10-out-of-range-default"),
])
def test_forcemodeb_state_diff_matches_asm(mode, label):
    from simant.recovered.gameplay import force_mode_b
    slot, arg3 = 3, 6
    results = _run_and_diff_segs(
        7, 0x0622, (slot, mode, arg3),
        lambda d, s: force_mode_b(d, s, slot, mode, arg3),
        _FORCEMODE_REGIONS,
        seed_fn=_forcemode_seed(slot, 0, 0, 0, 0x0F, 1, 0x28,
                                0x3D18, 0x3B22, 0x3F0E))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _FORCEMODE_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _MaintainSwarm (seg7:3580) — decay two swarm-size counters ----------
_MAINTAINSWARM_REGIONS = [
    (hooks.DG_SEG_INDEX, 0xAC8C, 0xAC90),
    (_PACK, 0x8000, 0x9D00),
]


def _maintainswarm_seed(b_val, r_val, b_floor, r_floor):
    def seed(m):
        dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
        m.mem.ww(pack, 0x807A, b_val & 0xFFFF)
        m.mem.ww(pack, 0x9C26, r_val & 0xFFFF)
        m.mem.ww(dg, 0xAC8C, b_floor & 0xFFFF)
        m.mem.ww(dg, 0xAC8E, r_floor & 0xFFFF)
    return seed


@pytest.mark.parametrize("b_val,r_val,b_floor,r_floor,label", [
    (0, 0, 0, 0, "both-zero-noop"),
    (2, 3, 0, 0, "both-small-decrement"),
    (40, 60, 0, 0, "both-decay-quarter"),
    (5, 5, 10, 10, "floor-clamps-up"),
    (100, 200, 0, 0, "cap-clamps-down-to-50"),
])
def test_maintainswarm_state_diff_matches_asm(b_val, r_val, b_floor, r_floor,
                                              label):
    from simant.recovered.gameplay import maintain_swarm
    results = _run_and_diff_segs(
        7, 0x3580, (), lambda d, p: maintain_swarm(d, p),
        _MAINTAINSWARM_REGIONS,
        seed_fn=_maintainswarm_seed(b_val, r_val, b_floor, r_floor))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _MAINTAINSWARM_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


# ---- _FeedAnts (seg6:0474) — hunger-decay food-supply tick ---------------
_FEEDANTS_REGIONS = [
    (hooks.DG_SEG_INDEX, 0xAC86, 0xAC8A),
    (_SDG, 0x8A00, 0x8A70),
    (_PACK, 0x80B0, 0x9E90),
]


def _feedants_seed(no_starve, ac86, ac88, pack_80b4, food_count, threshold):
    def seed(m):
        dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                        m.seg_bases[_PACK])
        m.mem.ww(sdg, 0x8A60, no_starve)
        m.mem.ww(dg, 0xAC86, ac86 & 0xFFFF)
        m.mem.ww(dg, 0xAC88, ac88 & 0xFFFF)
        m.mem.ww(pack, 0x80B4, pack_80b4)
        m.mem.ww(pack, 0x9E84, food_count & 0xFFFF)
        m.mem.ww(sdg, 0x8A62, threshold & 0xFFFF)
    return seed


@pytest.mark.parametrize("no_starve,ac86,ac88,pack_80b4,food_count,"
                         "threshold,label", [
    (0, 5, 5, 0, 100, 5, "both-decrement-no-food-drop"),
    (1, 5, 5, 0, 100, 5, "no-starve-skips-black-decrement"),
    (0, 0, 0, 0, 100, 5, "already-zero-clamps"),
    (0, 5, 5, 3, 0, 100, "pack80b4-3-skips-food-check"),
])
def test_feedants_state_diff_matches_asm(no_starve, ac86, ac88, pack_80b4,
                                         food_count, threshold, label):
    from simant.recovered.gameplay import feed_ants
    results = _run_and_diff_segs(
        6, 0x474, (), lambda d, s, p: feed_ants(d, s, p), _FEEDANTS_REGIONS,
        near=True,
        seed_fn=_feedants_seed(no_starve, ac86, ac88, pack_80b4, food_count,
                               threshold))
    for (rlabel, asm_after, rec_after), (_si, lo, _hi) in zip(
            results, _FEEDANTS_REGIONS):
        assert asm_after == rec_after, f"{label} {rlabel}: {_first_diff(asm_after, rec_after, lo)}"


def test_feedants_addfood_gate_raises():
    from simant.recovered.gameplay import feed_ants
    dg = bytearray(0x10000)
    sdg = bytearray(0x10000)
    pack = bytearray(0x10000)
    dgv, sdgv, packv = ByteBackend(dg, 0), ByteBackend(sdg, 0), ByteBackend(pack, 0)
    sdgv.ww(0x8A60, 1)
    packv.ww(0x80B4, 0)
    packv.ww(0x9E84, 0)
    sdgv.ww(0x8A62, 100)
    with pytest.raises(NotImplementedError):
        feed_ants(dgv, sdgv, packv)
