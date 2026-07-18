"""The standalone deploy's closure + deny guarantees (scripts/deploy_vmless.py).

Covers the COMPUTATION, not the build: the import closure of the runner
surface must carry the runtime spine (the dos_re CPU + win16 OS layer + the
graph installer) and must NOT carry any deny-listed module (the EXE-boot edge
and the RE workbench); the emitted graph's own load-time imports must be
derivable and deny-free; and the no-executable assert must catch an EXE in
any disguise (suffix, MZ header, or content hash).  The full build + the
pinned-digest smoke run via ``python scripts/deploy_vmless.py [--exe]``.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_MANIFEST = REPO_ROOT / "simant" / "lifted" / "graph" / "graph_manifest.json"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"test_deploy_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def deploy():
    return _load_script("deploy_vmless")


def test_deny_covers_the_exe_boot_edge_and_the_workbench(deploy):
    for must in ("simant.runtime", "win16.app", "simant.hooks",
                 "simant.probes", "simant.tests"):
        assert must in deploy.DENY
    # deny is prefix-based: subpackages are covered too
    assert deploy.denied("simant.probes.profile")
    assert deploy.denied("win16.app")
    assert not deploy.denied("win16.api.surface")
    assert not deploy.denied("dos_re.lift.runtime")


def test_static_closure_ships_the_spine_and_nothing_denied(deploy):
    closure = deploy.compute_closure()
    rels = {p.relative_to(REPO_ROOT).as_posix() for p in closure}
    for must in (
        "scripts/play_vmless.py", "scripts/play.py",
        "simant/vmless_boot.py", "simant/_env.py",
        "win16_re/win16/bootimage.py", "win16_re/win16/vmsnap.py",
        "win16_re/win16/demo.py", "win16_re/win16/api/surface.py",
        # The machine RECORD + memory map, split out of win16/loader.py so the
        # CPUless runner can hold a machine without the interpreter (cont.251).
        # win16/loader.py itself is the NE→VM mapping and is NOT in the closure:
        # the release boots EXE-free from the boot image and never parses an NE.
        "win16_re/win16/machine.py", "win16_re/win16/ne.py",  # class carriers (pickles)
        "win16_re/dos_re/dos_re/cpu.py", "win16_re/dos_re/dos_re/memory.py",
        "win16_re/dos_re/dos_re/independence.py",
        "win16_re/dos_re/dos_re/lift/install.py",
        "win16_re/dos_re/dos_re/lift/decode.py",  # pulled by dos_re/lift/__init__.py
    ):
        assert must in rels, f"runtime spine file missing from the closure: {must}"
    # the EXE-boot edge and the workbench never ship
    for never in ("simant/runtime.py", "win16_re/win16/app.py",
                  "simant/hooks.py", "win16_re/win16/irgen.py"):
        assert never not in rels, f"deny-listed file leaked: {never}"
    for p in closure:
        mod = deploy.path_to_module(p)
        assert mod is None or not deploy.denied(mod), f"denied module in closure: {mod}"


@pytest.mark.skipif(not GRAPH_MANIFEST.exists(),
                    reason="emitted graph not built (scripts/liftemit.py + liftlink.py)")
def test_graph_runtime_imports_are_derived_and_deny_free(deploy):
    deps = deploy.graph_runtime_imports()
    # the emitted modules' documented load-time surface
    assert "dos_re.cpu" in deps
    assert "dos_re.lift.runtime" in deps
    assert any(d.startswith("dos_re.hooks") for d in deps)
    assert not any(deploy.denied(d) for d in deps)
    closure = deploy.compute_closure(deps)
    rels = {p.relative_to(REPO_ROOT).as_posix() for p in closure}
    assert "win16_re/dos_re/dos_re/lift/runtime.py" in rels
    assert "win16_re/dos_re/dos_re/hooks.py" in rels


def test_no_executable_assert_catches_every_disguise(deploy, tmp_path):
    (tmp_path / "ok.txt").write_text("data")
    deploy.assert_no_executable(tmp_path, "0" * 64, 12345)   # clean tree passes

    mz = tmp_path / "innocent.bin"
    mz.write_bytes(b"MZ" + b"\x00" * 32)
    with pytest.raises(SystemExit, match="MZ-headed"):
        deploy.assert_no_executable(tmp_path, "0" * 64, 12345)
    mz.unlink()

    (tmp_path / "tool.exe").write_bytes(b"xx")
    with pytest.raises(SystemExit, match="executable-suffixed"):
        deploy.assert_no_executable(tmp_path, "0" * 64, 12345)
    (tmp_path / "tool.exe").unlink()

    import hashlib
    payload = b"renamed original bytes"
    renamed = tmp_path / "innocent.dat"
    renamed.write_bytes(payload)
    with pytest.raises(SystemExit, match="hash match"):
        deploy.assert_no_executable(
            tmp_path, hashlib.sha256(payload).hexdigest(), len(payload))


def test_release_constant_is_a_version(deploy):
    release = _load_script("play_vmless").VMLESS_RELEASE
    assert re.fullmatch(r"\d+\.\d+\.\d+(-pre)?", release)
    # the deploy's smoke pin single-sources the walls test's constants
    from simant.tests import test_vmless_walls as walls
    pin = deploy.smoke_pin()
    assert pin["digest"] == walls.PREFIX_DIGEST
    assert pin["end_instr"] == walls.PREFIX_END_INSTR
    assert pin["release"] == release
