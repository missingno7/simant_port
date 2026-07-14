"""State-diff oracle — the verification tier for *mutating* sim routines.

The predicate/accessor tier is proven by return value (test_hooks.py).  A routine
that mutates world state instead needs a different oracle: seed the sim-state
arrays, run the ORIGINAL ASM (with its screen-redraw side call stubbed out, so
only sim state changes), and diff the resulting arrays against the recovered
Python mutator applied to the same seed.  Byte-identical delta == byte-exact.

This is the harness the seg6 behavior layer (`_DoForageAnt`, ...) will use; it is
bootstrapped here on the simplest mutator, `_SetMap` (one map-cell write).
"""
from __future__ import annotations

import pytest

import simant.hooks as hooks
from simant import runtime
from simant.bridge.dgroup_view import ByteBackend

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="SimAnt assets not present")

SENT_CS, SENT_IP = 0xDEAD, 0xBEEF
# The three map planes in DGROUP (yard 0x2000, nest planes 0x1000 each).
MAP_LO, MAP_HI = 0x28E8, 0x68E8
_SETMAP_SEG, _SETMAP_OFF = 5, 0x617A
# _SetMap's unconditional screen redraw — a rendering side effect, not sim state.
_ZAP_SEG, _ZAP_OFF = 3, 0x0000


def _seed_map(mem, dg):
    """Fill the map region with a deterministic non-trivial pattern."""
    for off in range(MAP_LO, MAP_HI):
        mem.wb(dg, off, (off * 7 + 0x3B) & 0xFF)


def _read_region(mem, dg, lo, hi):
    return bytes(mem.rb(dg, off) for off in range(lo, hi))


def _run_setmap_asm(plane, x, y, value):
    """Run the real _SetMap over a seeded map, with the redraw stubbed; return the
    map region afterwards."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    s = m.cpu.s
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = dg
    _seed_map(m.mem, dg)

    # Stub the screen-redraw far call (_ZapEuMapAt) with a plain far return so the
    # ASM touches only sim state.  _SetMap calls it cdecl (caller cleans).
    zap_cs = m.seg_bases[_ZAP_SEG]

    def _redraw_stub(cpu):
        cs = cpu.s
        ret_ip = cpu.mem.rw(cs.ss, cs.sp)
        ret_cs = cpu.mem.rw(cs.ss, (cs.sp + 2) & 0xFFFF)
        cs.sp = (cs.sp + 4) & 0xFFFF
        cs.cs, cs.ip = ret_cs, ret_ip
    m.cpu.replacement_hooks[(zap_cs, _ZAP_OFF)] = _redraw_stub

    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[_SETMAP_SEG], _SETMAP_OFF
    sp = s.sp
    for v in (value, y, x, plane, SENT_CS, SENT_IP):   # plane@[bp+6] .. value@[bp+0xc]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    for _ in range(300):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
            break
    else:
        raise AssertionError("ASM _SetMap did not return")
    return _read_region(m.mem, dg, MAP_LO, MAP_HI)


def _recovered_setmap(plane, x, y, value):
    """Apply the recovered set_map to a bytearray seeded identically; return the
    map region afterwards."""
    from simant.recovered.gameplay import set_map
    dgroup = bytearray(0x10000)
    for off in range(MAP_LO, MAP_HI):
        dgroup[off] = (off * 7 + 0x3B) & 0xFF
    set_map(ByteBackend(dgroup, 0), plane, x, y, value)
    return bytes(dgroup[MAP_LO:MAP_HI])


@pytest.mark.parametrize("plane,x,y,value", [
    (0, 0x00, 0x00, 0x42), (1, 0x7F, 0x3F, 0x99), (1, 0x40, 0x20, 0x01),  # yard
    (2, 0x00, 0x00, 0x55), (2, 0x3F, 0x3F, 0xAA), (3, 0x10, 0x20, 0x7E),  # nest
    (0, 0x80, 0x00, 0x11),   # x out of range -> no write
    (2, 0x40, 0x00, 0x22),   # x out of range on the nest plane -> no write
    (4, 0x10, 0x10, 0x33),   # plane out of range -> no write
    (0, 0x10, 0x10, 0x180),  # value is written as a byte (low 8 bits)
])
def test_setmap_state_diff_matches_asm(plane, x, y, value):
    asm_after = _run_setmap_asm(plane, x, y, value)
    rec_after = _recovered_setmap(plane, x, y, value)
    assert asm_after == rec_after, (
        f"(p={plane},{x:#x},{y:#x},v={value:#x}): "
        f"map delta at {[i for i in range(len(asm_after)) if asm_after[i] != rec_after[i]]}")
