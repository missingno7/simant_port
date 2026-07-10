"""The static call-graph probe, gated by recovered ground truth.

The recovered MakeTable routines are PROVEN pure loops (their islands do all
the work and the A/B oracle passes), so the extractor must classify them as
leaves; the 2026-07-09 frontier note's disassembly of _GBoxFill shows it
calling import thunks (0060:xxxx), so it must show API calls.  If the length
walker desynchronizes (e.g. on the CD 3x FP-emulator forms), these break.
"""
from __future__ import annotations

import pytest

from simant.runtime import assets_present

pytestmark = pytest.mark.skipif(not assets_present(),
                                reason="SimAnt assets not present")


@pytest.fixture(scope="module")
def routines():
    from simant.probes.callgraph import build
    return {(r.seg, r.off): r for r in build()}


def test_recovered_pure_loops_classify_as_leaves(routines):
    assert routines[(4, 0x4674)].name == "_Windows_MakeTable4x4"
    assert routines[(4, 0x4674)].classification == "leaf"
    assert routines[(4, 0x4674)].anomalies == 0
    assert routines[(4, 0x46BB)].name == "_Windows_MakeTable1x1"
    assert routines[(4, 0x46BB)].classification == "leaf"


def test_gboxfill_shows_its_api_calls(routines):
    r = routines[(2, 0x19E6)]
    assert r.name == "_GBoxFill"
    assert any(c.kind == "api" for c in r.calls)


def test_win_iswinopen_is_call_coupled(routines):
    r = routines[(7, 0xC256)]
    assert r.name == "_win_IsWinOpen"
    assert r.classification != "leaf"


def test_near_calls_resolve_to_symbols_not_noise(routines):
    # Structural sanity for the whole scan: if the walker were reading operand
    # bytes as opcodes, near-call targets would be uniform noise; in real code
    # the great majority land exactly on a named routine entry.
    from simant.probes.symbols import symbols_in_segment
    entries = {(seg, off) for seg in range(1, 8)
               for off, _ in symbols_in_segment(seg)}
    near = [(c.seg, c.off) for r in routines.values() for c in r.calls
            if c.kind == "near"]
    assert len(near) > 300
    on_symbol = sum(1 for t in near if t in entries)
    assert on_symbol / len(near) > 0.6, f"{on_symbol}/{len(near)}"


def test_every_code_segment_yields_routines(routines):
    segs = {seg for seg, _ in routines}
    assert segs == {1, 2, 3, 4, 5, 6, 7}
