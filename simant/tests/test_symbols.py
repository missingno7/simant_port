"""The segment-aware SIMANTW.SYM resolver, gated by recovered ground truth.

The anchors below are byte-proven, not name-trusted: the MakeTable addresses
are where the install-time code signatures matched (simant/hooks.py), so if
the SYM parse or the SYM-order == NE-order assumption ever breaks, these fail.
"""
from __future__ import annotations

import pytest

from simant.runtime import assets_present
from simant.probes.symbols import (
    _segments, module_name, nearest_symbol, symbols_in_range,
    symbols_in_segment,
)

pytestmark = pytest.mark.skipif(not assets_present(),
                                reason="SimAnt assets not present")


def test_parses_the_ten_ne_segments_with_module_names():
    names = [name for name, _ in _segments()]
    assert names == [
        "SIMANT_MODULE", "GR_MODULE", "ANTEDIT_MODULE", "_TEXT",
        "SIMONE_MODULE", "SIMANT1_MODULE", "SIMTWO_MODULE",
        "SIMANT_DATA_GROUP", "PACK", "DGROUP",
    ]
    for name, syms in _segments():
        assert syms, name
        assert syms == sorted(syms)


def test_recovered_anchors_resolve_exactly():
    # Proven by install-time signature match at these addresses (hooks.py).
    assert nearest_symbol(4, 0x4674) == "_TEXT!_Windows_MakeTable4x4"
    assert nearest_symbol(4, 0x46BB) == "_TEXT!_Windows_MakeTable1x1"


def test_gboxfill_resolves_in_its_own_segment():
    # The old offset-only resolver mis-named addresses inside _GBoxFill with
    # symbols from other segments (docs/run_status.md, 2026-07-09).
    assert nearest_symbol(2, 0x19E6).startswith("GR!_GBoxFill")
    assert nearest_symbol(2, 0x19F0).startswith("GR!_GBoxFill+0x")


def test_no_cross_segment_bleed():
    # The same offset must resolve per-segment, never globally.
    seen = {nearest_symbol(seg, 0x0000) for seg in (1, 2, 3, 4)}
    assert len(seen) == 4


def test_interior_offsets_get_a_delta():
    base = symbols_in_segment(6)[0]
    assert base == (0x0000, "_DoAntSim")
    assert nearest_symbol(6, 0x0004) == "SIMANT1!_DoAntSim+0x4"


def test_symbols_in_range_is_segment_scoped():
    hits = symbols_in_range(4, 0x4674, 0x46BC)
    assert (0x4674, "_Windows_MakeTable4x4") in hits
    assert (0x46BB, "_Windows_MakeTable1x1") in hits
    assert all(0x4674 <= o < 0x46BC for o, _ in hits)


def test_out_of_range_segment_is_loud_not_wrong():
    assert nearest_symbol(11, 0x1234) == "(seg 11 not in SYM)"
    assert module_name(11) == ""
    assert symbols_in_segment(11) == []
