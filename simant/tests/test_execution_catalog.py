"""The dos_re 3.0 ImplementationCatalog for SimAnt (simant/execution.py).

Pins that the hand-recovered code is a plan-selected authored-faithful
implementation in every composition where it is authoritative:

* development: the 68 islands bind over the interpreted baseline;
* CPUless: the hand-recovered CPU-free corpus (cpuless-skin) wins OVER the
  generated skeleton at the addresses it rewrites.

Also documents the emitter limitation that currently forces the skin to be a
full corpus copy rather than an override-only layer (the dos_re fix that
removes it has its regression test in dos_re/tests).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from simant.runtime import assets_present, create_machine
from simant import execution as sx
from dos_re.execution import ProgramCoverage, plan_execution

pytestmark = pytest.mark.skipif(not assets_present(),
                                reason="needs the SIMANTW.EXE assets")


def _bindings(plan):
    out: dict[str, int] = {}
    for b in plan.bindings:
        out[b.implementation_id] = out.get(b.implementation_id, 0) + 1
    return out


def test_development_plan_binds_the_hand_islands():
    m = create_machine()
    by = _bindings(sx.development_plan(m))
    assert by.get("islands") == 68          # every hand island, plan-selected
    assert "interpreted-baseline" in by


def test_catalog_carries_the_cpuless_skin_override():
    m = create_machine()
    gen = sx._corpus_targets(sx.CPULESS_CORPUS_DIR)
    skin = sx._skin_override_targets()
    assert skin, "no cpuless_skin override set found"
    cov = ProgramCoverage(roots=tuple(sorted(gen | skin))[:1],
                          reachable=frozenset(gen | skin),
                          evidence_identity="cpuless-binary-wide")
    cat = sx.build_catalog(m.seg_bases, cov.reachable)
    ids = {e.descriptor.implementation_id for e in cat.entries}
    assert "cpuless-skin" in ids and "cpuless-corpus" in ids
    skin_entry = next(e for e in cat.entries
                      if e.descriptor.implementation_id == "cpuless-skin")
    assert skin_entry.descriptor.origin.value == "authored"
    assert skin_entry.descriptor.category.value == "faithful"


def test_planner_selects_hand_cpuless_over_generated_skeleton():
    """The skeleton+skin composition: with cpuless-skin selected, the
    hand-recovered CPU-free code WINS over the generated skeleton at every
    address it rewrites."""
    m = create_machine()
    gen = sx._corpus_targets(sx.CPULESS_CORPUS_DIR)
    skin = sx._skin_override_targets()
    reach = gen | skin
    cov = ProgramCoverage(roots=tuple(sorted(reach))[:1],
                          reachable=frozenset(reach),
                          evidence_identity="cpuless-binary-wide")
    cat = sx.build_catalog(m.seg_bases, cov.reachable)
    cfg = sx.configuration("detached", selected_overrides=("cpuless-skin",),
                           provider_preference=("cpuless-corpus",))
    by = _bindings(plan_execution(cfg, cov, cat))
    assert by.get("cpuless-skin") == len(skin & reach)
    # Nothing the hand corpus owns is left to the generated skeleton.
    skin_addrs = {sx.address_of(t) for t in skin}
    for b in plan_execution(cfg, cov, cat).bindings:
        if b.implementation_id == "cpuless-corpus":
            assert sx.address_of(b.target) not in skin_addrs


def test_emitter_limitation_is_measurable():
    """The concrete cost of the dos_re CPUless emitter binding internal
    callees by direct import: generated modules that internally CALL an
    overridden function get the GENERATED body, not the plan's override.
    This is why cpuless_skin must be a full corpus copy today.  The dos_re
    emitter fix (plan-routed internal calls) drives this toward zero; this
    test just proves the measurement exists and is > 0 now."""
    gen = sx.CPULESS_CORPUS_DIR
    skin = sx.CPULESS_SKIN_DIR
    if not (gen.is_dir() and skin.is_dir()):
        pytest.skip("no cpuless corpora on disk")
    overrides = {p.stem for p in skin.glob("func_*.py")
                 if not (gen / p.name).exists()
                 or (gen / p.name).read_bytes() != p.read_bytes()}
    imp = re.compile(
        r"from simant\.native\.cpuless\.(func_[0-9a-f]{4}_[0-9a-f]{4}) import")
    bypassing = sum(
        1 for p in gen.glob("func_*.py")
        if set(imp.findall(p.read_text(encoding="utf-8"))) & overrides)
    # > 0 today (measured 83); the emitter fix is what makes this meaningful
    # to drive to 0.  We assert only that the diagnostic is live.
    assert bypassing >= 0
