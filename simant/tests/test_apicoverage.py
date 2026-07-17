"""scripts/apicoverage.py — the SIMANTW API-coverage join, pinned on the
real artifacts (static half only; the instrumented strict replay is the
runner's job — see docs/run_status.md cont.226).

Structural invariants of the join between artifacts/recovery_ir.json's
``api:*`` surface, the boot manifest's import-slot table and win16_re's
registry — not exact counts (they move as recovery proceeds):

* every statically-called target is an import (the IR tags far transfers
  into thunk slots, which only exist through import relocations);
* the implementation-status partition covers all targets exactly;
* identity is honest: a target no source names is ``unnamed`` with no name,
  and a known one (PeekMessage) resolves through the ordinal table;
* the raw-INT static surface (int21_dos + int2f — SimAnt's MSC CRT) is seen.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import simant._env  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[2]
IR_PATH = REPO_ROOT / "artifacts" / "recovery_ir.json"
MANIFEST_PATH = REPO_ROOT / "artifacts" / "vmless_boot" / "manifest.json"

pytestmark = pytest.mark.skipif(
    not (IR_PATH.exists() and MANIFEST_PATH.exists()),
    reason="IR / boot image not generated (scripts/irgen.py + "
           "scripts/build_vmless_boot_image.py)")


@pytest.fixture(scope="module")
def report():
    from win16.api.surface import build_registry
    from win16.apicoverage import build_coverage
    doc = json.loads(IR_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    registry = build_registry()
    for key, off in manifest["api_slots"].items():
        module, ordinal = key.split(".")
        registry.slots[(module, int(ordinal))] = off
    return build_coverage(doc, registry)


def test_every_statically_called_target_is_an_import(report):
    called = {label: t for label, t in report["targets"].items()
              if t["static_sites"]}
    assert called, "the IR carries api:* call sites"
    not_imported = [label for label, t in called.items() if not t["imported"]]
    assert not_imported == []


def test_status_partition_covers_all_targets(report):
    s = report["summary"]
    assert s["implemented"] + s["equates"] + s["tripwires"] == s["targets"]
    # Static-only join: no runtime axes.
    assert s["exercised"] is None and s["never_exercised"] is None


def test_identity_is_honest(report):
    peek = report["targets"]["USER.109"]
    assert peek["name"] == "PeekMessage"
    assert peek["name_source"] == "ordinal-table"
    assert peek["implemented"] == "handler"
    assert peek["static_sites"] > 0
    # An unnamed target must SAY so rather than carry a guess.  This used to
    # assert the set was non-empty (25 of SIMANTW's imports had no name); the
    # tripwire-tier sweep resolved every one, so the invariant now holds
    # vacuously — and the stronger fact it became is pinned below.
    assert all(t["name"] is None and t["name_source"] == "unnamed"
               for t in report["targets"].values() if t["unnamed"])
    # The known equates ride along as data imports, never dispatched.
    assert report["targets"]["KERNEL.114"]["classification"] == "equate"


def test_every_import_is_named_and_named_from_the_ordinal_table(report):
    # What the tripwire-tier sweep bought: no import is a bare number any more,
    # so every Win16ApiGap names the API it stopped on.  `unnamed` is the honest
    # fallback and is now empty; if a future EXE imports an ordinal the Wine
    # spec tables do not cover, this fails and the answer is to extend
    # win16/api/ordinals.py FROM THAT SOURCE — never to guess a name here.
    unnamed = sorted(label for label, t in report["targets"].items()
                     if t["unnamed"])
    assert unnamed == [], f"unnamed imports: {unnamed}"
    # Every name comes from a source that can be checked — never a guess.
    sources = {t["name_source"] for t in report["targets"].values()}
    assert sources <= {"ordinal-table", "handler-name", "registry-entry"}
    # A TRIPWIRE has no handler to borrow a name from, so the ordinal table is
    # its ONLY honest source; a tripwire named any other way would mean someone
    # guessed.  (An implemented target may legitimately be handler-named: a few
    # KERNEL ordinals — GlobalFlags and friends — are absent from the Wine spec
    # tables entirely.)
    tripwires = {label: t for label, t in report["targets"].items()
                 if t["implemented"] == "tripwire"}
    assert tripwires, "the fail-loud tier still exists"
    assert all(t["name_source"] == "ordinal-table" for t in tripwires.values()), \
        {k: v["name_source"] for k, v in tripwires.items()}


def test_raw_int_static_surface_is_reported(report):
    ints = report["ints"]["static_sites"]
    assert ints.get("int21_dos", 0) > 0     # the MSC CRT's raw DOS file I/O
    assert ints.get("int2f", 0) > 0         # the TSR/driver probe
    assert report["unresolved_api_tags"] == {}
