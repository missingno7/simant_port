"""waitscan — the mechanical env-wait enumeration (scripts/waitscan.py).

Pins: (1) the committed derived facts file is FRESH against the IR (--check
contract, same as dispatchgen); (2) the crash-evidenced wait sites are in the
park set (derived or hand-promoted); (3) the big work loops (MAINWNDPROC,
WINMAIN, the sim frame paths) are NOT mechanically parked — the conservative
direction the owner directive demands.
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IR = REPO / "artifacts" / "recovery_ir.json"


def _waitscan():
    spec = importlib.util.spec_from_file_location(
        "waitscan", REPO / "scripts" / "waitscan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pytestmark = pytest.mark.skipif(not IR.exists(),
                                reason="recovery_ir.json not generated")


def _cands():
    ws = _waitscan()
    return ws, ws.scan(json.loads(IR.read_text(encoding="utf-8")))


def test_derived_facts_fresh():
    ws = _waitscan()
    assert ws.main(["--check"]) == 0, \
        "boundary_heads_derived.txt is stale -- rerun scripts/waitscan.py"


def test_evidenced_waits_are_parked():
    ws, cands = _cands()
    parked = {c["head"] for c in cands if c["class"] == "wait"}
    # The live-crash / sweep-evidenced pure waits (cont.228).
    for head in ("0E99:07E6",     # _IBMInitStuff splash timeout (Maxis logo)
                 "0E99:4A08",     # _WaitHundredths pacing delay
                 "0100:6A88",     # _processEdit tick spin
                 "0100:59C3"):    # _DoScenario idle pump
        assert head in parked, f"{head} must be a derived wait head"
    # The evidence-promoted MIXED waits live in the hand-curated file.
    hand = (REPO / "simant" / "facts" / "boundary_heads.txt").read_text()
    facts = {ln.split("#")[0].strip() for ln in hand.splitlines()}
    assert "1:D4BE" in facts      # _ShowIntro intro wait
    assert "1:6418" in facts      # _PictureDialog wait


def test_work_loops_not_mechanically_parked():
    ws, cands = _cands()
    parked_syms = {c["symbol"] for c in cands if c["class"] == "wait"}
    for sym in ("MAINWNDPROC", "WINMAIN", "_ExpDig", "_SimColonies",
                "_DoAntLions", "_DoNestAntB", "_DoNestAntR"):
        assert sym not in parked_syms, \
            f"{sym} classified as a pure wait -- the predicate regressed"


def test_ir_carries_all_heads():
    """Every committed head (hand + derived) must be applied in the IR —
    catches a regen that forgot one of the two facts files."""
    doc = json.loads(IR.read_text(encoding="utf-8"))
    applied = set(doc["facts_applied"]["boundary_heads"])
    ws = _waitscan()
    derived = {c["head"] for c in ws.scan(doc) if c["class"] == "wait"}
    assert derived <= applied, \
        f"derived heads missing from the IR: {sorted(derived - applied)}"
    assert {"0100:D4BE", "0100:6418"} <= applied   # the hand promotions
