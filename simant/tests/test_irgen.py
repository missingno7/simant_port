"""scripts/irgen.py — SIMANTW's recovery-IR generation, pinned on real code.

A miniature of the full corpus run (a handful of entries, static-only scan —
the probe is census-verified separately): pins the conventions everything
downstream (liftemit/liftlink/install, the adapter-matching stage) relies on:
paragraph-base CS:IP record keys, the first-class .SYM identity
(symbol/module/ne_seg/aliases) in records AND in the unsupported ledger, the
api:* platform-effect tag on a known API-calling routine, a far-call record
between game segments, and the env_wait keep-interpreted fact from
simant/facts/.  The full-document determinism gate is the runner's job
(two byte-identical runs — see docs/run_status.md cont.220); here we pin the
document's shape without a 13-second sweep per test run.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from simant.runtime import assets_present

pytestmark = pytest.mark.skipif(not assets_present(),
                                reason="SimAnt assets not present")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "irgen.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("simant_irgen", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def doc():
    """The IR over a pinned entry sample: _SetHelpCursor (seg 2 — calls the
    USER cursor API and far-calls into seg 4) + the aliased keep-interpreted
    x87 entry __aFftol/__ftol (seg 4)."""
    irgen = _load_script()
    from simant.runtime import create_machine
    from win16.irgen import build_ir

    machine = create_machine()
    machine.cpu.trace_enabled = False
    entries, names, _ = irgen.sym_corpus()
    sample = [(2, 0x0000), (4, 0x0ACC)]
    keep = irgen.read_fact_pairs(irgen.FACTS_DIR / "keep_interpreted.txt")
    return build_ir(machine, sample, machine_factory=None,
                    names={k: names[k] for k in sample},
                    keep_interpreted=[p for p in keep if p in sample],
                    symbols="SIMANTW.SYM sha1=test")


def test_committed_facts_file_parses_and_covers_the_census_frontier():
    irgen = _load_script()
    pairs = irgen.read_fact_pairs(irgen.FACTS_DIR / "keep_interpreted.txt")
    assert len(pairs) == 32                       # 33 SYM names, 32 addresses
    assert all(seg in irgen.CODE_SEGS for seg, _ in pairs)
    assert (4, 0x0ACC) in pairs and (7, 0xF85B) in pairs


def test_records_are_keyed_by_paragraph_base_with_sym_identity(doc):
    # NE seg 2 loads at paragraph 0x0E99 (deterministic image layout).
    rec = doc["functions"]["0E99:0000"]
    assert rec["entry"] == "0E99:0000"
    assert rec["ne_seg"] == 2
    assert rec["symbol"] == "_SetHelpCursor"
    assert rec["module"] == "GR_MODULE"
    assert rec["liftable"] and rec["signature"]
    assert rec["exits"] == ["retf"]
    assert doc["provenance"]["symbols"] == "SIMANTW.SYM sha1=test"


def test_api_effect_tag_and_cross_segment_far_call(doc):
    rec = doc["functions"]["0E99:0000"]
    insts = [i for b in rec["blocks"] for i in b["instructions"]]
    tags = {i["ip"]: i["platform_effect"] for i in insts
            if "platform_effect" in i}
    assert tags["000E"] == "api:USER.173:LoadCursor"
    # A far-call record between game segments: seg 2 -> seg 4 (_TEXT),
    # paragraph-based like every address in the IR.
    assert ["275F", "0762"] in rec["calls_far"]
    # The API thunk far call is recorded too (segment 0060 = the thunk seg).
    assert any(s == "0060" for s, _ in rec["calls_far"])


def test_keep_interpreted_fact_tags_env_wait_and_ledger_names_symbols(doc):
    rec = doc["functions"]["275F:0ACC"]
    assert rec["ne_seg"] == 4
    assert rec["symbol"] == "__aFftol"
    assert rec["aliases"] == ["__ftol"]          # two .SYM names, one address
    assert rec["module"] == "_TEXT"
    assert rec["platform_effect"] == "env_wait"  # the keep-interpreted fact
    assert not rec["liftable"]
    # Fail-loud ledger, with first-class symbol identity (refusals name the
    # symbol, not just the address).
    assert doc["unsupported"]
    for u in doc["unsupported"]:
        assert u["entry"] == "275F:0ACC"
        assert u["symbol"] == "__aFftol"
        assert u["reason"] == "unsupported-opcode"
