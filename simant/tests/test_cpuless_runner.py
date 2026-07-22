"""scripts/play_cpuless.py — the standalone CPUless runner's own contract.

The runner is the M4 capstone: it boots SimAnt EXE-free and CPU-free and runs
the promoted corpus over a CPU-free Windows.  What is pinned here is the part
that must not silently regress:

* the WALL's forbidden set really names SimAnt's CPU-ABI adapter packages (if
  the runner could import ``simant.lifted`` the wall would prove nothing), and
  arming it really does reject them;
* the static closure lint (``lint_cpuless --root``) passes from the runner;
* the boot is CPU-free end to end and the CPU-FREE Win16 API path services a
  real SimAnt import thunk — asserted against the generated corpus when it is
  present (it is disposable/gitignored, so the deep checks skip without it).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOT_DIR = REPO_ROOT / "artifacts" / "vmless_boot"
CORPUS_DIR = REPO_ROOT / "simant" / "native" / "cpuless"

needs_boot = pytest.mark.skipif(
    not (BOOT_DIR / "manifest.json").exists(),
    reason="boot image not built (scripts/build_vmless_boot_image.py)")
needs_corpus = pytest.mark.skipif(
    not (CORPUS_DIR / "dispatch.py").exists(),
    reason="CPUless corpus not promoted (scripts/cpuless_promote.py)")


def _runner_source() -> str:
    return (REPO_ROOT / "scripts" / "play_cpuless.py").read_text(encoding="utf-8")


def test_the_wall_names_the_cpu_abi_adapters():
    """The adapters and the lifted graphs are VERIFICATION shims.  A runner
    that can import them has not detached from anything."""
    src = _runner_source()
    for forbidden in ("simant.lifted", "simant.hooks", "simant.runtime",
                      "win16.loader", "win16.bootimage"):
        assert f'"{forbidden}"' in src, f"{forbidden} missing from the wall"
    # and the guard is armed at MODULE level, before the host is imported
    assert src.index("install_import_guard(") < src.index("from win16.cpuless")


def test_the_armed_wall_rejects_the_adapter_packages():
    """Not just declared — enforced, including through a RELATIVE import."""
    import builtins

    import simant._env  # noqa: F401
    import win16  # noqa: F401
    from dos_re.detachment_guard import (DetachedDependencyError,
                                         install_import_guard, resolve_import)

    real_import = builtins.__import__
    try:
        install_import_guard(extra_forbidden=("simant.lifted",))
        with pytest.raises(DetachedDependencyError, match="simant.lifted"):
            __import__("simant.lifted.graph_cpuless")
        with pytest.raises(DetachedDependencyError, match="dos_re.cpu"):
            __import__("dos_re.cpu")
    finally:
        builtins.__import__ = real_import
    # the relative-import blind spot the shared host closes
    assert resolve_import("cpu", {"__package__": "dos_re"}, 1) == "dos_re.cpu"


def test_lint_cpuless_root_passes_from_the_runner():
    """The STATIC half of the wall: no path from the runner reaches a CPU,
    including branches this run never executes."""
    tool = REPO_ROOT / "win16_re" / "dos_re" / "tools" / "lint_cpuless.py"
    proc = subprocess.run(
        [sys.executable, str(tool), "--repo-root", str(REPO_ROOT),
         "--root", "scripts/play_cpuless.py",
         "--forbidden-module", "dos_re.cpu",
         "--forbidden-module", "dos_re.cpu386",
         "--forbidden-module", "dos_re.lift.install",
         "--forbidden-module", "dos_re.lift.runtime",
         "--forbidden-module", "dos_re.runtime",
         "--forbidden-module", "simant.lifted",
         "--forbidden-module", "simant.hooks",
         "--forbidden-module", "win16.loader",
         "--forbidden-module", "win16.bootimage",
         "--local-prefix", "dos_re", "--local-prefix", "simant",
         "--local-prefix", "win16",
         "--package-dir", "win16=win16_re/win16",
         "--package-dir", "dos_re=win16_re/dos_re/dos_re"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


@needs_boot
def test_the_boot_is_cpu_free():
    """``load_cpuless_image`` reconstructs the machine with a carrier that
    executes nothing, and refuses to pretend otherwise."""
    import simant._env  # noqa: F401
    import win16  # noqa: F401
    from win16.cpuless import (CpuFreeCarrier, CpuFreeExecutionAttempt,
                               Win16CpulessPlatform, load_cpuless_image)
    from win16.api.surface import WINFLAGS_NO_FPU, build_registry

    machine, manifest = load_cpuless_image(
        BOOT_DIR, lambda: build_registry(winflags=WINFLAGS_NO_FPU))
    assert isinstance(machine.cpu, CpuFreeCarrier)
    with pytest.raises(CpuFreeExecutionAttempt):
        machine.cpu.step
    plat = Win16CpulessPlatform(machine)
    assert plat.api.slots, "no import thunk slots bound from the manifest"
    assert manifest["thunk_seg"] == 0x0060


@needs_boot
@needs_corpus
def test_a_promoted_body_reaches_windows_with_no_cpu():
    """End to end: a promoted SimAnt body runs on the CPU-free host and its
    ``plat.farcall`` is serviced by the real win16 API — args in, result out."""
    import inspect

    import simant._env  # noqa: F401
    import win16  # noqa: F401
    from win16.api.surface import WINFLAGS_NO_FPU, build_registry
    from win16.cpuless import (Win16CpulessPlatform, load_cpuless_image,
                               load_recovered)

    machine, _ = load_cpuless_image(
        BOOT_DIR, lambda: build_registry(winflags=WINFLAGS_NO_FPU),
        game_root=REPO_ROOT / "assets" / "ANTWIN")
    plat = Win16CpulessPlatform(machine)
    meta = json.loads((BOOT_DIR / "state.json").read_text(encoding="utf-8"))

    key = "0E99:547C"          # a promoted body whose only effect is one API call
    fn = load_recovered("simant.native.cpuless", key)
    params = inspect.signature(fn).parameters
    kwargs = {r: v for r, v in meta["cpu"].items() if r in params}
    if "_flags_in" in params:
        kwargs["_flags_in"] = meta["cpu"]["flags"]
    out, compat = plat.call(fn, **kwargs)

    assert plat.farcalls, "no Win16 service reached through the CPU-free path"
    assert plat.farcalls[0].startswith("KERNEL.")
    assert compat["cost"] > 0                  # the body owns its virtual time
    assert isinstance(out, dict)


@needs_boot
def test_the_runner_stops_loud_at_the_frontier():
    """The program entry (``__astart``) is census-BLOCKED, so the runner's
    honest outcome today is a witness naming it — never a fallback."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "play_cpuless.py")],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "CPUless WITNESS" in proc.stderr
    assert "no recovered module" in proc.stderr
    assert "CPUless wall: ARMED" in proc.stdout
