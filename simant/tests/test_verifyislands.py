"""scripts/verifyislands.py: the deterministic island-vs-ASM demo verifier.

The end-to-end proof (replay a demo, clone + re-run the ASM oracle at each island
call) needs the real binary + a snapshot + a demo, so it lives as a script you
run, not a unit test.  What is asserted here without assets: the harness imports,
exposes `main`, and `--list` enumerates exactly the installed island set — so a
rename in hooks._ISLANDS can't silently desync the tool.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verifyislands.py"


def _load():
    spec = importlib.util.spec_from_file_location("verifyislands", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_imports_and_exposes_main():
    mod = _load()
    assert callable(mod.main)


def test_list_matches_installed_islands():
    from simant import hooks
    out = subprocess.run([sys.executable, str(SCRIPT), "--list"],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    listed = set(out.stdout.split())
    expected = {name for *_rest, name in hooks._ISLANDS}
    assert listed == expected
