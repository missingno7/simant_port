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


def _run_and_diff_segs(seg, off, args, apply_recovered, regions, *, seed=None):
    """Like `_run_and_diff`, but for a routine that mutates through MULTIPLE
    fixed NE data segments (e.g. via a DGROUP pointer-global into
    SIMANT_DATA_GROUP/PACK — see `hooks.SIMANT_DATA_GROUP_SEG_INDEX`/
    `PACK_SEG_INDEX`).  `regions` is [(seg_index, lo, hi), ...]: the byte window
    of each touched segment to snapshot/diff (real load-time data, not seeded —
    these are fixed segments, not per-test scratch).  `apply_recovered` receives
    one `ByteBackend` per region, positional, in `regions` order, each addressed
    by the SAME real offsets the ASM uses (so `view.rw(0x8A5E)` reads the byte at
    that real offset within its segment, not window-relative index 0).

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
    for v in (*reversed(args), SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    for _ in range(50_000):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
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


def _sx(v):
    return v - 0x10000 if v & 0x8000 else v


def _first_diff(a, b, base=0):
    d = [i for i in range(len(a)) if a[i] != b[i]]
    return (f"{len(d)} differing bytes; first at "
            + ", ".join(f"{i + base:#06x}(asm={a[i]:#04x} rec={b[i]:#04x})"
                        for i in d[:6]))
