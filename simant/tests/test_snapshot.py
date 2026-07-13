"""Snapshot fidelity: LoadLibrary handles survive save/load (win16/vmsnap.py).

A resumed crash snapshot must resolve GetProcAddress the same way the live
session did — else a FARPROC the game already stored (e.g. waveOutOpen, looked
up after LoadLibrary("MMSYSTEM")) far-calls NULL on resume.  The `libraries`
map lives on the API registry, which load_snapshot rebuilds fresh, so vmsnap
must persist and restore it explicitly.
"""
import pytest

from simant import runtime
from win16.vmsnap import load_snapshot, save_snapshot

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="simant assets not present")


def test_loadlibrary_handles_survive_snapshot_round_trip(tmp_path):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    libs = {"MMSYSTEM.DLL": 0x0100, "SHELL.DLL": 0x0101}
    m.api.services["libraries"] = dict(libs)

    save_snapshot(m, tmp_path / "snap")
    m2 = load_snapshot(tmp_path / "snap", runtime.create_machine)

    # Without the fix the fresh registry starts with no libraries and
    # GetProcAddress(hinst, ...) can no longer map the handle to its module.
    assert m2.api.services.get("libraries") == libs
