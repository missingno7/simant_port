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
