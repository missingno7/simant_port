"""The two hard walls, held by the suite (dos_re_2.0 §1a + §1a' + §6).

* **static independence lint** — the strict runner's import graph reaches no
  loader edge (always runs; pure AST walk);
* **boot-image audit** — the generated image is data-only: no bundled
  executable, every recovered code byte poisoned or declared code_as_data
  (runs when the image + IR artifacts exist);
* **clean-room replay** — boot in a temp dir with the EXE PHYSICALLY ABSENT
  (game data copied, executable not), the file-access guard armed, the
  interpreter poison armed from instruction zero, and replay a 45M-instruction
  prefix of cold_nohooks (918 input events: boot, intro, menus, NewGame,
  worldgen, early gameplay) to a PINNED digest.

Regeneration story (all inputs disposable): scripts/irgen.py →
scripts/dispatchgen.py (fixpoint) → scripts/liftemit.py → scripts/liftlink.py
→ scripts/build_vmless_boot_image.py.  The pinned digest changes only when
the demo or the corpus semantics change — regenerate it with
scripts/play_vmless.py --demo <prefix> and update the constant together with
the change that moved it (never to make a red run green).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOT_DIR = REPO_ROOT / "artifacts" / "vmless_boot"
IR_PATH = REPO_ROOT / "artifacts" / "recovery_ir.json"
GRAPH_DIR = REPO_ROOT / "simant" / "lifted" / "graph"
DEMO = REPO_ROOT / "artifacts" / "demos" / "cold_nohooks.jsonl"
DATA_DIR = REPO_ROOT / "assets" / "ANTWIN"

#: The clean-room pin: cold_nohooks truncated to records due by instruction
#: 45,000,000, replayed on the strict boot-image machine (poison armed).
#: Re-pinned 2026-07-17 — the digitized sound effects went LIVE (win16_re:
#: the MMSYSTEM waveOut device).  waveOutOpen now SUCCEEDS where the old
#: stub reported MMSYSERR_BADDEVICEID, so from ~41M the game takes its
#: _MciOutWave path (decode the 4-bit delta-PCM, prepare/write the buffer,
#: poll for MM_WOM_DONE) instead of backing off: +4,634 instructions of real
#: guest work, 9 effects played over the prefix.  Attribution checked by
#: reverting the win16_re hunks — the run returns EXACTLY to the previous pin
#: (45,102,443 / 50365479…, sound_log empty), so this move is that change and
#: nothing else.  The whole-demo differential still MATCHES (both sides shift
#: identically: 39/39 aligned checkpoints + the final state), which is what
#: proves host audio never reaches guest state.
PREFIX_LIMIT = 45_000_000
PREFIX_END_INSTR = 45_107_077
PREFIX_DIGEST = "f9ad9c8b10413b76da5649fc40fde8777c999b75fd4d91c103104f0adccdda81"

_have_image = (BOOT_DIR / "manifest.json").exists() and IR_PATH.exists()
_have_graph = (GRAPH_DIR / "graph_manifest.json").exists()


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"simant_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_static_independence_lint_holds():
    assert _load_script("lint_vmless_independence").main([]) == 0


@pytest.mark.skipif(not _have_image,
                    reason="boot image / IR not built (scripts/"
                           "build_vmless_boot_image.py)")
def test_boot_image_audit_passes():
    from win16.bootimage import audit_boot_image
    fails, _info = audit_boot_image(BOOT_DIR, IR_PATH)
    assert fails == []


@pytest.mark.skipif(not _have_image,
                    reason="boot image not built")
def test_manifest_reports_full_poison_and_walls():
    from win16.bootimage import independence_report, load_boot_manifest
    manifest = load_boot_manifest(BOOT_DIR)
    assert manifest["poison"]["enabled"]
    assert manifest["poison"]["code_bytes_present_after"] == 0
    report = independence_report(manifest)
    assert report.endswith("EXE-independence wall: HOLDS")


@pytest.mark.skipif(not (_have_image and _have_graph and DEMO.exists()
                         and DATA_DIR.exists()),
                    reason="needs boot image + emitted graph + cold_nohooks "
                           "demo + game data")
def test_cleanroom_strict_replay_exe_absent(tmp_path):
    """Boot EXE-free in a temp dir (executable physically absent), poison
    armed from instruction zero, replay the 45M-instruction demo prefix to
    the pinned digest."""
    import simant.vmless_boot as vb
    from dos_re.independence import exe_access_guard_from_manifest
    from win16.bootimage import load_boot_manifest
    from win16.demo import DemoDriver, DemoEnded
    from win16.vmsnap import digest

    data = tmp_path / "data"
    data.mkdir()
    for f in DATA_DIR.iterdir():
        if f.is_file() and f.suffix.upper() not in (".EXE", ".SYM"):
            shutil.copy(f, data / f.name)
        elif f.is_dir():
            shutil.copytree(f, data / f.name)
    boot = tmp_path / "vmless_boot"
    shutil.copytree(BOOT_DIR, boot)
    assert not [p for p in tmp_path.rglob("*")
                if p.suffix.upper() == ".EXE"]     # the EXE is ABSENT

    src = DEMO.read_text().splitlines()
    kept = [src[0]] + [ln for ln in src[1:]
                       if json.loads(ln).get("i", 0) <= PREFIX_LIMIT]
    demo = tmp_path / "prefix.jsonl"
    demo.write_text("\n".join(kept) + "\n")

    manifest = load_boot_manifest(boot)
    with exe_access_guard_from_manifest(manifest):
        machine, manifest, installed = vb.boot_strict(boot, game_root=data)
        assert machine.cpu.interp_forbidden          # the wall, from instr 0
        assert machine.cpu.code_poisoned
        assert len(installed) > 1800
        sys.setrecursionlimit(200_000)
        driver = DemoDriver(demo)
        driver.install(machine.api.services["system"])
        with pytest.raises(DemoEnded):
            while True:
                machine.cpu.run(2_000)

    assert machine.cpu.instruction_count == PREFIX_END_INSTR
    assert digest(machine) == PREFIX_DIGEST


@pytest.mark.skipif(not (IR_PATH.exists() and DATA_DIR.exists()),
                    reason="IR artifact / assets not present")
def test_dispatch_facts_match_fresh_derivation():
    """The committed dispatch/code_as_data facts are exactly what a fresh
    mechanical derivation over the committed IR produces (the dispatchgen
    fixpoint holds — regeneration would not drift)."""
    assert _load_script("dispatchgen").main(["--check"]) == 0


@pytest.mark.skipif(not (_have_image and IR_PATH.exists()),
                    reason="boot image / IR not built")
def test_exe_access_guard_refuses_the_binary_by_name_and_hash(tmp_path):
    from dos_re.independence import (VMlessViolation,
                                     exe_access_guard_from_manifest)
    from win16.bootimage import load_boot_manifest
    manifest = load_boot_manifest(BOOT_DIR)
    exe = DATA_DIR / manifest["source_exe"]["name"]
    if not exe.exists():
        pytest.skip("source EXE not present to test the guard against")
    renamed = tmp_path / "innocent.dat"
    shutil.copy(exe, renamed)
    with exe_access_guard_from_manifest(manifest):
        with pytest.raises(VMlessViolation):
            open(exe, "rb")                          # by name
        with pytest.raises(VMlessViolation):
            open(renamed, "rb")                      # by content hash
        (tmp_path / "ok.txt").write_text("data")     # data stays readable
        assert open(tmp_path / "ok.txt").read() == "data"
