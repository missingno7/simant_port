"""The window-object map (scripts/winmap.py): the statically-proven binding
of SimAnt's window slots to their draw callbacks, event routines and openers.

Pins the load-bearing facts the presentation work stands on:

* the draw-hook registration is COMPLETE — 11 callbacks, all registered in
  `_InitApplicationWindows`, stored at seg[DGROUP:0xC6CC]:0x77B2+slot*4 and
  invoked at exactly 430E:BBE2/BC83;
* `_DoEvent`'s compare chain binds all nine event classes to their
  `_Proc*Event` (incl. the ribbon pseudo-windows 0x22/0x23), with no
  ambiguity;
* the quick-game / Black Nest View windows (Edit 00, Map 01, Yard 19) have
  BOTH draw and event bindings — the vertical-slice targets;
* every replay-OBSERVED draw-callback invocation is a registered hook
  (the winmap cross-check, re-asserted here against the built Atlas).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IR_PATH = REPO_ROOT / "artifacts" / "recovery_ir.json"

pytestmark = pytest.mark.skipif(
    not IR_PATH.exists(),
    reason="artifacts/recovery_ir.json not generated (scripts/irgen.py)")


@pytest.fixture(scope="module")
def winmap():
    spec = importlib.util.spec_from_file_location(
        "winmap", REPO_ROOT / "scripts" / "winmap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ir():
    return json.loads(IR_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_addr(ir):
    return {k: fn.get("symbol") for k, fn in ir["functions"].items()}


def test_draw_hook_registrations_are_complete_and_named(winmap, ir, by_addr):
    regs = winmap.registrations(ir, by_addr)
    assert len(regs) == 11
    bound = {r["slot"]: r["callback_symbol"] for r in regs}
    assert bound[0x00] == "_win_DrawEditWindow"      # the black nest view
    assert bound[0x01] == "_win_DrawMapWindow"
    assert bound[0x19] == "_win_DrawYardWindow"
    assert all(s.startswith("_win_Draw") for s in bound.values())


def test_event_chain_binds_all_nine_classes_unambiguously(winmap, ir, by_addr):
    events = {e["slot"]: e["event_routine"]
              for e in winmap.event_bindings(ir, by_addr)}
    assert events == {
        0x00: "_ProcEditEvent", 0x01: "_ProcMapEvent",
        0x05: "_ProcInfoEvent", 0x12: "_ProcModeEvent",
        0x13: "_ProcCasteEvent", 0x15: "_ProcHistoryEvent",
        0x19: "_ProcYardEvent",
        0x22: "_ProcMapRibbonEvent", 0x23: "_ProcYardRibbonEvent",
    }


def test_quick_game_windows_are_fully_bound(winmap, ir, by_addr):
    """Edit(00)/Map(01)/Yard(19) — the quick-game vertical-slice targets —
    each have a registered draw hook, an event routine, AND a named opener."""
    regs = {r["slot"] for r in winmap.registrations(ir, by_addr)}
    events = {e["slot"] for e in winmap.event_bindings(ir, by_addr)}
    opens = {o["handle"] >> 8
             for o in winmap.call_sites_with_handle(ir, winmap.WIN_OPEN)
             if o["handle"] is not None}
    for slot in (0x00, 0x01, 0x19):
        assert slot in regs and slot in events and slot in opens


def test_observed_draw_invocations_are_registered_hooks(winmap, ir, by_addr):
    graph_path = REPO_ROOT / "artifacts" / "atlas" / "indexes" / "graph.json"
    if not graph_path.exists():
        pytest.skip("atlas not built (scripts/atlas_build.py)")
    from urllib.parse import unquote
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    label = {n["id"]: n.get("label") for n in graph["nodes"]}
    registered = {r["callback_symbol"]
                  for r in winmap.registrations(ir, by_addr)}
    observed = set()
    for edge in graph["edges"]:
        if edge["kind"] != "call_ind" or edge["status"] != "observed":
            continue
        src = unquote(edge["source"].rsplit(":", 1)[-1]).upper()
        if src in winmap.DRAW_INVOKE_SITES:
            observed.add(label.get(edge["target"], "?"))
    assert observed, "no observed draw invocations in the Atlas"
    assert observed <= registered


def test_atlas_carries_the_typed_window_facts():
    graph_path = REPO_ROOT / "artifacts" / "atlas" / "indexes" / "graph.json"
    if not graph_path.exists():
        pytest.skip("atlas not built (scripts/atlas_build.py)")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    kinds = {e["kind"] for e in graph["edges"]}
    assert {"opens-window", "draw-callback", "event-routine",
            "registers-callback"} <= kinds
    windows = [n for n in graph["nodes"]
               if n["kind"] == "region" and ":window:" in n["id"]]
    assert len(windows) >= 20
