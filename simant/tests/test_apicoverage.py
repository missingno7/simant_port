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
    unnamed = [t for t in report["targets"].values() if t["unnamed"]]
    assert unnamed, "SIMANTW imports ordinals nothing names yet"
    assert all(t["name"] is None and t["name_source"] == "unnamed"
               for t in unnamed)
    # The known equates ride along as data imports, never dispatched.
    assert report["targets"]["KERNEL.114"]["classification"] == "equate"


def test_raw_int_static_surface_is_reported(report):
    ints = report["ints"]["static_sites"]
    assert ints.get("int21_dos", 0) > 0     # the MSC CRT's raw DOS file I/O
    assert ints.get("int2f", 0) > 0         # the TSR/driver probe
    assert report["unresolved_api_tags"] == {}
