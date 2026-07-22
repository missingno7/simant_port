"""Static switch-table resolution over SIMANTW (dos_re
lift.dispatch.static_switch_targets through irgen + the Atlas).

Pins the three load-bearing facts of the statically-resolved dispatch layer:

* the IR annotates the bounded cs-relative switch sites with their
  statically-read arm sets, and the census is CLOSED over those arms (every
  arm is a carved entry — the VMless-wall requirement);
* the only GAME-code jmp_ind sites left unannotated are the three known
  pre-scaled ROP dispatchers (`and/shr` before the bound — a different idiom
  the reader refuses by design; everything else unannotated is C runtime);
* every replay-OBSERVED arm lies inside its site's static table — the
  static reader and the runtime evidence must never contradict (the same
  cross-check scripts/atlas_build.py enforces at build time).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IR_PATH = REPO_ROOT / "artifacts" / "recovery_ir.json"
GRAPH_PATH = REPO_ROOT / "artifacts" / "atlas" / "indexes" / "graph.json"

pytestmark = pytest.mark.skipif(
    not IR_PATH.exists(),
    reason="artifacts/recovery_ir.json not generated (scripts/irgen.py)")

#: The CRT segment: its dispatchers (printf __output, sqrt/intrinsic
#: dispatch, chkstk) are computed-pointer idioms, not bounded switch tables.
CRT_SEG = "275F"

#: The pre-scaled ROP dispatchers: `and ax,mask; shr ax,3; cmp; ja;
#: jmp cs:[bx+T]` bounds a BYTE offset, which the reader refuses by design
#: (reading bound+1 words there walks past the table — proven by the phantom
#: case_006A this exact site produced before the refusal existed).
PRESCALED_ROP_SITES = {
    ("0E99", "1918"),   # _CreateMonoSolidBrush
    ("0E99", "1A4B"),   # _GBoxFill
    ("0E99", "1B18"),   # _GPatBox
}


def _ir():
    return json.loads(IR_PATH.read_text(encoding="utf-8"))


def _sites(ir):
    """(cs, ip, static_targets|None) for every jmp_ind instruction record."""
    for key, fn in ir["functions"].items():
        cs = key.split(":")[0].upper()
        for block in fn.get("blocks", ()):
            for ins in block["instructions"]:
                if ins["kind"] == "jmp_ind":
                    yield cs, ins["ip"].upper(), ins.get("static_targets")


def test_switch_sites_are_annotated_and_census_closed():
    ir = _ir()
    entries = set(ir["functions"])
    annotated = [(cs, ip, t) for cs, ip, t in _sites(ir) if t]
    assert len(annotated) >= 100     # 106 on the current binary
    for cs, ip, targets in annotated:
        for target in targets:
            assert f"{cs}:{target.upper()}" in entries, (
                f"static arm {cs}:{target} of site {cs}:{ip} is not a census "
                f"entry — the static closure in scripts/irgen.py is broken")


def test_unannotated_game_sites_are_exactly_the_prescaled_rop_dispatchers():
    ir = _ir()
    unannotated_game = {(cs, ip) for cs, ip, t in _sites(ir)
                        if not t and cs != CRT_SEG}
    assert unannotated_game == PRESCALED_ROP_SITES


@pytest.mark.skipif(not GRAPH_PATH.exists(),
                    reason="atlas not built (scripts/atlas_build.py)")
def test_observed_arms_lie_inside_their_static_tables():
    from urllib.parse import unquote
    ir = _ir()
    static_by_site = {(cs, ip): {t.upper() for t in targets}
                      for cs, ip, targets in _sites(ir) if targets}
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    checked = 0
    for edge in graph["edges"]:
        if edge["kind"] != "jmp_ind" or edge["status"] != "observed":
            continue
        source = unquote(edge["source"].rsplit(":", 1)[-1]).upper()
        arms = static_by_site.get(tuple(source.split(":")))
        if arms is None:
            continue
        target_ip = unquote(
            edge["target"].rsplit(":", 1)[-1]).upper().split(":")[-1]
        assert target_ip in arms, (
            f"observed arm {target_ip} at {source} contradicts the "
            f"statically-read table {sorted(arms)}")
        checked += 1
    assert checked >= 90             # 99 with the current evidence base
