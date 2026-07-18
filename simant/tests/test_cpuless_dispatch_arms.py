"""SIMANTW's `case_XXXX` fragments are dispatch ARMS, not functions.

The message-pump cluster (MAINWNDPROC / WINMAIN / MYTIMERFUNC / _DoEvent /
_DoMouse / _UpdateWindows and their `case_XXXX` arms) was the last thing between
the CPUless fixpoint and a closed observed-closure wall (docs/run_status.md
cont.248).  The cluster is ATOMIC, so it was held out entirely by NINE
tail-dispatch containers that refused ``dyn-target-unpromoted``: each dispatches
through a jump table whose arms the IR carved as their own "functions", and an
arm in isolation is a shared epilogue with no prologue (``leave-without-enter`` /
``frame-restore-without-establish``).  Refusing was correct -- a live jump to an
unpromoted arm would raise ``UnknownDispatchTarget`` -- and blocking.

An arm is really an ALTERNATE ENTRY into its container's body, sharing the
container's frame.  dos_re's generic seam (``dos_re.lift.dispatch``,
``cpuless_promote --absorb-dispatch-arms``) fuses each arm back into its
container and declares it an owned alternate entry; the SimAnt side supplies only
the facts (the recovery IR + the per-site dynamic-target evidence).

These tests pin the resulting SimAnt facts: the nine containers no longer refuse
on dispatch, and every arm is owned rather than orphaned.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CENSUS = REPO_ROOT / "artifacts" / "cpuless_promote_census.json"

#: the nine `dyn-target-unpromoted` containers cont.248 measured as the whole
#: wall gap -- SIMANTW.SYM names in comments.
BLOCKERS = (
    "0100:023A",    # _UpdateUserButtons  (frameless register-save epilogue)
    "0100:02D6",    # case_02D6
    "0100:035A",    # case_035A
    "18C0:0B7A",    # _ProcEditEvent
    "18C0:A51E",    # _ProcYardEvent
    "18C0:A6CE",    # _ProcYardRibbonEvent
    "18C0:C3D4",    # _ProcMapRibbonEvent
    "430E:B710",    # _win_DrawObjectI
    "430E:E6E2",    # _win_Recalc
)


@pytest.fixture(scope="module")
def census() -> dict:
    if not CENSUS.is_file():
        pytest.skip("no promotion census (run scripts/cpuless_promote.py)")
    return json.loads(CENSUS.read_text(encoding="utf-8"))


def test_no_container_refuses_dyn_target_unpromoted(census):
    """The dispatch blocker class is EMPTY: no function is held out because a
    switch arm of its jump table could not be dispatched to."""
    assert census["refused"].get("dyn-target-unpromoted", []) == []


def test_the_nine_blockers_are_resolved(census):
    """Each of cont.248's nine is now promoted, or recognised as an arm owned by
    a container -- never still blocked on dispatch."""
    promoted = {k.upper() for k in census["promotable"]}
    arms = {k.upper(): v.upper() for k, v in census["absorbed_arms"].items()}
    reason = {k.upper(): r for r, ks in census["refused"].items() for k in ks}
    for key in BLOCKERS:
        if key in promoted or key in arms:
            assert reason.get(key) is None
            continue
        # whatever still refuses must be an ordinary composition dependency
        # (its own callees), not the dispatch seam -- `contains-call` resolves
        # with the rest of the atomic cluster, `dyn-target-unpromoted` never did.
        assert reason.get(key) == "contains-call", f"{key}: {reason.get(key)}"


def test_absorbed_arms_are_owned_not_orphaned(census):
    """Every absorbed arm names a container, and no container is itself an arm
    (an arm cannot own an arm -- ownership is one level, by construction)."""
    arms = {k.upper(): v.upper() for k, v in census["absorbed_arms"].items()}
    assert arms, "no dispatch arms absorbed -- is --absorb-dispatch-arms wired?"
    owners = set(arms.values())
    assert not (owners & set(arms)), "an owner is itself an arm"
    # SIMANTW's arms are the SYM `case_XXXX` fragments: they are never a static
    # call target, which is exactly what makes them arms rather than callees.
    ir = json.loads((REPO_ROOT / "artifacts" / "recovery_ir.json"
                     ).read_text(encoding="utf-8"))["functions"]
    called = set()
    for key, rec in ir.items():
        cs = int(key.split(":")[0], 16)
        for t in (rec.get("calls_near") or []):
            called.add(f"{cs:04X}:{int(t, 16):04X}")
        for seg, off in (rec.get("calls_far") or []):
            called.add(f"{int(seg, 16):04X}:{int(off, 16):04X}")
    assert not (set(arms) & called), "an absorbed arm is statically called"


def test_no_arm_refused_absorption(census):
    """A fusion that cannot be proven byte-identical refuses loudly.  SIMANTW
    has none -- every arm overlaps its container exactly."""
    assert census["absorbed_arm_refusals"] == {}
