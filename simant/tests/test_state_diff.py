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
"""
from __future__ import annotations

import pytest

import simant.hooks as hooks
from simant import runtime
from simant.bridge.dgroup_view import ByteBackend

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="SimAnt assets not present")

SENT_CS, SENT_IP = 0xDEAD, 0xBEEF
DGROUP_SIZE = 0x10000
# For a small-model app ss == ds == DGROUP, so the call's stack frame lives at
# the TOP of DGROUP.  The sim state these routines mutate is all well below this
# (the highest field seen is ~0xC4A0), so diff only [0, SIM_HI) and leave the
# stack band — execution scaffolding, not sim state — out of the comparison.
SIM_HI = 0xF000


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
    for _ in range(50_000):                             # loop mutators need headroom
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
                       near=False):
    """Like `_run_and_diff`, but for a routine that mutates through MULTIPLE
    fixed NE data segments (e.g. via a DGROUP pointer-global into
    SIMANT_DATA_GROUP/PACK — see `hooks.SIMANT_DATA_GROUP_SEG_INDEX`/
    `PACK_SEG_INDEX`).  `regions` is [(seg_index, lo, hi), ...]: the byte window
    of each touched segment to snapshot/diff (real load-time data, not seeded —
    these are fixed segments, not per-test scratch).  `apply_recovered` receives
    one `ByteBackend` per region, positional, in `regions` order, each addressed
    by the SAME real offsets the ASM uses (so `view.rw(0x8A5E)` reads the byte at
    that real offset within its segment, not window-relative index 0).

    `near=True` for a routine that returns via a NEAR `ret` (pops only IP, CS
    unchanged) rather than a far `retf` — common for calls between routines in
    the SAME NE segment.  A near-return routine only needs SENT_IP pushed (no
    return CS slot), and the completion check is IP-only (CS never changes).

    Returns a list of (label, asm_window, recovered_window) for each region.
    """
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    s = m.cpu.s
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = dg
    for o, v in (seed or {}).items():
        m.mem.ww(dg, o, v & 0xFFFF)

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
    for _ in range(50_000):
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
_SDG = hooks.SIMANT_DATA_GROUP_SEG_INDEX
_PACK = hooks.PACK_SEG_INDEX
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
    m = runtime.create_machine()
    god_base, current_base = m.seg_bases[_SDG], m.seg_bases[_PACK]
    m.mem.wb(god_base, 0x8A5E, 1 if god else 0)
    m.mem.ww(current_base, 0x9BEC, current & 0xFFFF)

    results = _run_and_diff_segs(
        5, 0x8C70, (new_health,),
        lambda dg, sdg, pack: set_my_health(dg, sdg, pack, _sx(new_health)),
        _HEALTH_REGIONS)
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
    m = runtime.create_machine()
    dg, sdg, pack = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_SDG],
                     m.seg_bases[_PACK])
    m.mem.ww(pack, 0x7402, timer & 0xFFFF)
    m.mem.ww(dg, 0xAC82, reset_rate & 0xFFFF)
    m.mem.ww(dg, 0xAC86, food & 0xFFFF)
    m.mem.wb(sdg, 0x8A60, 1 if no_starve else 0)

    results = _run_and_diff_segs(6, 0x48F8, (), lambda d, s, p: dec_eat_b(d, s, p),
                                 _DECEATB_REGIONS)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DECEATB_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("timer,reset_rate,food", [
    (5, 320, 10), (0, 320, 10), (-1, 320, 10), (-1, -64, 10),
    (-1, 320, 0), (-1, 320, -5), (100, 0, 50),
])
def test_deceatr_state_diff_matches_asm(timer, reset_rate, food):
    from simant.recovered.gameplay import dec_eat_r
    m = runtime.create_machine()
    dg, pack = m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK]
    m.mem.ww(pack, 0x7C8E, timer & 0xFFFF)
    m.mem.ww(dg, 0xAC84, reset_rate & 0xFFFF)
    m.mem.ww(dg, 0xAC88, food & 0xFFFF)

    results = _run_and_diff_segs(6, 0x6C6A, (), lambda d, p: dec_eat_r(d, p),
                                 _DECEATR_REGIONS)
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
    m = runtime.create_machine()
    sdg = m.seg_bases[_SDG]
    m.mem.wb(sdg, 0x3D18 + ant_idx, flag)
    m.mem.wb(sdg, 0x392C + ant_idx, x)
    m.mem.ww(sdg, 0x3736 + ant_idx, y)      # low byte is what matters

    results = _run_and_diff_segs(6, 0x42B0, (ant_idx,),
                                 lambda d, s: kill_tail_b(d, s, ant_idx),
                                 _KILLTAILB_REGIONS)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _KILLTAILB_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("ant_idx,x,y,flag", [
    (0, 0, 0, 1), (1, 63, 63, 1), (50, 32, 16, 0), (99, 5, 60, 1),
    (150, 40, 40, 1),
])
def test_killtailr_state_diff_matches_asm(ant_idx, x, y, flag):
    from simant.recovered.gameplay import kill_tail_r
    m = runtime.create_machine()
    sdg = m.seg_bases[_SDG]
    m.mem.wb(sdg, 0x46E6 + ant_idx, flag)
    m.mem.wb(sdg, 0x42FA + ant_idx, x)
    m.mem.ww(sdg, 0x4104 + ant_idx, y)

    results = _run_and_diff_segs(6, 0x6762, (ant_idx,),
                                 lambda d, s: kill_tail_r(d, s, ant_idx),
                                 _KILLTAILR_REGIONS)
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
    m = runtime.create_machine()
    sdg = m.seg_bases[_SDG]
    # a mixed pattern: zeros, small values (<8), and larger values, covering
    # every branch of both the linear and exponential decay curves.
    for i in range(_SCENT_SPAN):
        m.mem.wb(sdg, base + i, [0, 1, 7, 8, 9, 100, 255, 3][i % 8])

    results = _run_and_diff_segs(6, off, (), lambda s: fn(s),
                                 [(_SDG, base, base + _SCENT_SPAN)], near=True)
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
    m = runtime.create_machine()
    sdg = m.seg_bases[_SDG]
    idx = ((x & 0xFFFE) << 4) + (y >> 1)
    m.mem.wb(sdg, base + idx, existing)

    results = _run_and_diff_segs(6, off, (x, y, value),
                                 lambda s: fn(s, x, y, value),
                                 [(_SDG, base, base + _JAM_REGION_SPAN)], near=near)
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
    m = runtime.create_machine()
    sdg = m.seg_bases[_SDG]
    idx = ((x >> 1) << 5) + (y >> 1)
    m.mem.wb(sdg, _ALARM_BASE + idx, existing)

    results = _run_and_diff_segs(6, 0x943C, (x, y, delta),
                                 lambda s: alarm_here(s, x, y, delta),
                                 [(_SDG, _ALARM_BASE, _ALARM_BASE + _JAM_REGION_SPAN)],
                                 near=True)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, _first_diff(asm_after, rec_after, _ALARM_BASE)


@pytest.mark.parametrize("x,y,value,existing", [
    (0, 0, 50, 10), (10, 10, 5, 20), (126, 62, 200, 199), (0, 0, 5, 5),
    (32, 16, 0, 100), (64, 32, 255, 0),
])
def test_alarmhere2_state_diff_matches_asm(x, y, value, existing):
    from simant.recovered.gameplay import alarm_here2
    m = runtime.create_machine()
    sdg = m.seg_bases[_SDG]
    idx = ((x >> 1) << 5) + (y >> 1)
    m.mem.wb(sdg, _ALARM_BASE + idx, existing)

    results = _run_and_diff_segs(6, 0x947E, (x, y, value),
                                 lambda s: alarm_here2(s, x, y, value),
                                 [(_SDG, _ALARM_BASE, _ALARM_BASE + _JAM_REGION_SPAN)],
                                 near=True)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, _first_diff(asm_after, rec_after, _ALARM_BASE)


# ---- _FindInAList/BList/RList (seg5:2C42/2C86/2CCE) — pure list search -----
# Read-only predicates (no state mutation): seed ONE machine, run the ASM to
# return and capture AX, then feed the SAME machine's (still-seeded, untouched
# since nothing was written) memory to the recovered function via ByteBackend
# and compare.  reuses this file's PACK/SIMANT_DATA_GROUP segment indices.
def _run_and_get_ax(m, seg, off, args, near=False):
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
    for _ in range(50_000):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == target:
            break
    else:
        raise AssertionError(f"ASM {seg}:{off:#06x} did not return")
    return s.ax


@pytest.mark.parametrize("count,slots,target0,target1", [
    (5, [(0, 10, 20, 1), (1, 10, 20, 1)], 10, 20),        # 2 matches -> last wins
    (5, [(2, 7, 8, 1)], 7, 8),                             # single match mid-list
    (3, [(0, 1, 1, 0)], 1, 1),                             # 3rd field 0 -> no match
    (0, [], 5, 5),                                          # empty list -> 0xFFFF
    (4, [(3, 9, 9, 1)], 1, 1),                              # no match at all
])
def test_findinalist_matches_asm(count, slots, target0, target1):
    from simant.recovered.gameplay import find_in_a_list
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    m.mem.ww(pack, 0x80F0, count)
    for slot, f0, f1, f2 in slots:
        m.mem.wb(sdg, 0x23A4 + slot, f0)
        m.mem.wb(sdg, 0x278E + slot, f1)
        m.mem.wb(sdg, 0x2F62 + slot, f2)

    ax = _run_and_get_ax(m, 5, 0x2C42, (target0, target1))
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
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    m.mem.ww(pack, count_off, count)
    for slot, y, x, caste in slots:
        m.mem.wb(sdg, y_off + slot, y)
        m.mem.wb(sdg, x_off + slot, x)
        m.mem.wb(sdg, c_off + slot, caste)

    ax = _run_and_get_ax(m, 5, off, (ty, tx, tc))
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
    m = runtime.create_machine()
    m.mem.ww(m.seg_bases[_PACK], 0x80F0, count)

    results = _run_and_diff_segs(
        5, 0x2EF0, (t0, t1, caste, fc, fe),
        lambda d, s, p: add_ant_to_a_list(p, s, d, t0, t1, caste, fc, fe),
        _ADDANTA_REGIONS)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _ADDANTA_REGIONS):
        assert asm_after == rec_after, f"count={count} {label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("count", [0, 1, 5, 0x1F3, 0x1F4, 0x1F5])
def test_addanttoblist_state_diff_matches_asm(count):
    from simant.recovered.gameplay import add_ant_to_b_list
    y, x, caste, fc, fe = 40, 20, 4, 8, 12
    m = runtime.create_machine()
    m.mem.ww(m.seg_bases[_PACK], 0x99D4, count)

    results = _run_and_diff_segs(
        5, 0x2F4A, (y, x, caste, fc, fe),
        lambda d, s, p: add_ant_to_b_list(p, s, d, y, x, caste, fc, fe),
        _ADDANTB_REGIONS)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _ADDANTB_REGIONS):
        assert asm_after == rec_after, f"count={count} {label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("count", [0, 1, 5, 0x1F3, 0x1F4, 0x1F5])
def test_addanttorlist_state_diff_matches_asm(count):
    from simant.recovered.gameplay import add_ant_to_r_list
    y, x, caste, fc, fe = 50, 30, 6, 10, 14
    m = runtime.create_machine()
    m.mem.ww(m.seg_bases[_PACK], 0x72CC, count)

    results = _run_and_diff_segs(
        5, 0x2FA4, (y, x, caste, fc, fe),
        lambda d, s, p: add_ant_to_r_list(p, s, d, y, x, caste, fc, fe),
        _ADDANTR_REGIONS)
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
    m = runtime.create_machine()
    dg, pack, sdg = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK],
                     m.seg_bases[_SDG])
    m.mem.wb(dg, 0x48E8 + (x << 6) + y, tile)
    m.mem.ww(pack, 0x9B6A, ant_idx)
    m.mem.wb(sdg, 0x3D18 + ant_idx, caste)

    results = _run_and_diff_segs(6, 0x3C3C, (x, y),
                                 lambda d, p, s: drop_food_b(d, p, s, x, y),
                                 _DROPFOODB_REGIONS)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _DROPFOODB_REGIONS):
        assert asm_after == rec_after, f"{label}: {_first_diff(asm_after, rec_after, lo)}"


@pytest.mark.parametrize("x,y,tile,ant_idx,caste", [
    (10, 20, 0x05, 0, 0x0B), (10, 20, 0x10, 1, 0x03), (10, 20, 0x12, 2, 0x08),
    (10, 20, 0x13, 3, 0x00), (0, 0, 0x00, 50, 0xFF), (63, 63, 0x13, 5, 0x08),
])
def test_dropfoodr_state_diff_matches_asm(x, y, tile, ant_idx, caste):
    from simant.recovered.gameplay import drop_food_r
    m = runtime.create_machine()
    dg, pack, sdg = (m.seg_bases[hooks.DG_SEG_INDEX], m.seg_bases[_PACK],
                     m.seg_bases[_SDG])
    m.mem.wb(dg, 0x58E8 + (x << 6) + y, tile)
    m.mem.ww(pack, 0x9B6A, ant_idx)
    m.mem.wb(sdg, 0x46E6 + ant_idx, caste)

    results = _run_and_diff_segs(6, 0x6242, (x, y),
                                 lambda d, p, s: drop_food_r(d, p, s, x, y),
                                 _DROPFOODR_REGIONS)
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
    m = runtime.create_machine()
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
        _REMOVEFROMA_REGIONS)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, _REMOVEFROMA_REGIONS):
        assert asm_after == rec_after, (
            f"slot={slot} count={count} {label}: {_first_diff(asm_after, rec_after, lo)}")


# ---- _ClrModePop (seg6:034A) — reset mode-population tally arrays ---------
@pytest.mark.parametrize("c1,c2", [(0, 0), (1, 1), (5, 0), (0, 9), (100, 200)])
def test_clrmodepop_state_diff_matches_asm(c1, c2):
    from simant.recovered.gameplay import clr_mode_pop
    m = runtime.create_machine()
    pack = m.seg_bases[_PACK]
    for i in range(0x14):
        m.mem.ww(pack, 0x7BE4 + 2 * i, 0xBEEF)
        m.mem.ww(pack, 0x786A + 2 * i, 0xDEAD)
    m.mem.ww(pack, 0x7C44, c1)
    m.mem.ww(pack, 0x8078, c2)

    results = _run_and_diff_segs(6, 0x034A, (), lambda p: clr_mode_pop(p),
                                 [(_PACK, 0x7800, 0x8100)], near=True)
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
    m = runtime.create_machine()
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
                                 near=True)
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
    m = runtime.create_machine()
    pack, sdg = m.seg_bases[_PACK], m.seg_bases[_SDG]
    m.mem.ww(pack, count_off, len(slots))
    for i, (sx, caste) in enumerate(slots):
        m.mem.wb(sdg, x_off + i, sx)
        m.mem.wb(sdg, caste_off + i, caste)
        m.mem.wb(sdg, mark_off + i, 0xAA)     # poison the mark field

    lo, hi = min(x_off, caste_off, mark_off), max(x_off, caste_off, mark_off) + 0x10
    pack_lo, pack_hi = count_off & ~0xFF, (count_off & ~0xFF) + 0x100
    regions = [(_SDG, lo, hi), (_PACK, pack_lo, pack_hi)]

    results = _run_and_diff_segs(5, off, (x,), lambda s, p: fn(p, s, x), regions)
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
    m = runtime.create_machine()
    m.mem.ww(m.seg_bases[_PACK], count_off, initial)
    lo, hi = count_off & ~0xFF, (count_off & ~0xFF) + 0x100
    results = _run_and_diff_segs(5, off, (), lambda p: fn(p), [(_PACK, lo, hi)])
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, f"{routine}: {_first_diff(asm_after, rec_after, lo)}"


def test_killspider_state_diff_matches_asm():
    from simant.recovered.gameplay import kill_spider
    m = runtime.create_machine()
    for off in (0x729E, 0x72E0, 0x7290):
        m.mem.ww(m.seg_bases[_PACK], off, 0xBEEF & 0xFFFF)

    results = _run_and_diff_segs(5, 0x53D4, (), lambda p: kill_spider(p),
                                 [(_PACK, 0x7200, 0x7300)])
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
    m = runtime.create_machine()
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
    results = _run_and_diff_segs(5, off, (), lambda s, p: fn(p, s), regions)
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
    m = runtime.create_machine()
    m.mem.ww(m.seg_bases[_PACK], count_off, count)

    results = _run_and_diff_segs(
        5, 0x584A, (list_type, slot, t0, t1, caste, fc, fe),
        lambda s, p: set_ant_index(p, s, list_type, slot, t0, t1, caste, fc, fe),
        regions)
    for (label, asm_after, rec_after), (_si, lo, _hi) in zip(results, regions):
        assert asm_after == rec_after, (
            f"list_type={list_type} slot={slot} count={count} {label}: "
            f"{_first_diff(asm_after, rec_after, lo)}")


# ---- _GetSmellT (seg6:9612) — read the trail-scent grid via a direction ----
# Pure read (no mutation): seed the whole trail grid with a distinguishable
# pattern, run to return, compare AX against the recovered fn on the same
# (still-seeded) machine.  The direction-delta tables are real game data, read
# live — not overridden here (only the destination grid is seeded).
@pytest.mark.parametrize("p,q,direction,is_red", [
    (10, 10, 0, 0), (10, 10, 1, 0), (10, 10, 3, 1), (0, 0, 2, 0),
    (63, 31, 5, 1), (5, 5, 8, 0), (0, 0, 0, 1),
])
def test_getsmellt_matches_asm(p, q, direction, is_red):
    from simant.recovered.gameplay import get_smell_t
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    sdg_base = m.seg_bases[_SDG]
    for i in range(0x800):
        m.mem.wb(sdg_base, 0x6AD2 + i, (i * 3 + 7) & 0xFF)
        m.mem.wb(sdg_base, 0x7AD2 + i, (i * 5 + 11) & 0xFF)

    ax = _run_and_get_ax(m, 6, 0x9612, (p, q, direction, is_red), near=True)
    sdg_view = ByteBackend(m.mem.block(sdg_base, 0, 0x10000), 0)
    expect = get_smell_t(sdg_view, p, q, direction, is_red)
    assert ax == (expect & 0xFFFF), f"asm={ax:#06x} rec={expect:#06x}"


# ---- _DecTSmell (seg6:95B6) — single-cell trail-scent decrement -----------
@pytest.mark.parametrize("x,y,is_red,existing", [
    (0, 0, 0, 5), (0, 0, 1, 5), (126, 62, 0, 1), (126, 62, 1, 1),
    (64, 32, 0, 0), (64, 32, 1, 0),   # already-0 cell -> no underflow
])
def test_dectsmell_state_diff_matches_asm(x, y, is_red, existing):
    from simant.recovered.gameplay import dec_t_smell
    m = runtime.create_machine()
    sdg = m.seg_bases[_SDG]
    idx = ((x >> 1) << 5) + (y >> 1)
    base = 0x7AD2 if is_red else 0x6AD2
    m.mem.wb(sdg, base + idx, existing)

    results = _run_and_diff_segs(6, 0x95B6, (x, y, is_red),
                                 lambda s: dec_t_smell(s, x, y, is_red),
                                 [(_SDG, base, base + _JAM_REGION_SPAN)], near=True)
    (label, asm_after, rec_after), = results
    assert asm_after == rec_after, _first_diff(asm_after, rec_after, base)


def _sx(v):
    return v - 0x10000 if v & 0x8000 else v


def _first_diff(a, b, base=0):
    d = [i for i in range(len(a)) if a[i] != b[i]]
    return (f"{len(d)} differing bytes; first at "
            + ", ".join(f"{i + base:#06x}(asm={a[i]:#04x} rec={b[i]:#04x})"
                        for i in d[:6]))
