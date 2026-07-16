"""Boot wiring for SimAnt (assets/ANTWIN/SIMANTW.EXE).

A thin adapter over the generic win16 launcher: pins the EXE path and boot
flags.  SimAnt links WIN87EM but its NE carries real x87 opcodes (no OSFIXUPs
applied), so it runs FPU-less-emulator-form free — WINFLAGS_NO_FPU.
"""
from __future__ import annotations

from pathlib import Path

from . import _env  # noqa: F401  (puts the dos_re framework on sys.path)

from win16.app import WINFLAGS_NO_FPU, create_machine as _create_machine
from win16.loader import Win16Machine
from win16.ne import NEExecutable, parse_ne

# GAME_NAME / demo_out_path live in the loader-free half (simant.vmless_boot)
# so the interactive host never needs this module; re-exported for the
# workbench scripts that already boot through here.
from .vmless_boot import GAME_NAME, demo_out_path  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "assets"
EXE_PATH = ASSETS / "ANTWIN" / "SIMANTW.EXE"

# Recorded demos live under artifacts/demos/ (git-ignored scratch — dos_re
# convention); a repro promoted to a test baseline goes to artifacts/test_oracles/.
DEMOS_DIR = REPO_ROOT / "artifacts" / "demos"


def resolve_demo(name: str) -> Path:
    """Locate a demo by NAME so bare names recorded with --record-demo replay
    without a path: tries the path as given, then artifacts/demos/NAME(.jsonl).
    Returns the first that exists, else the canonical artifacts/demos/NAME.jsonl
    (so a 'not found' error names the expected location)."""
    for cand in (Path(name), DEMOS_DIR / name, DEMOS_DIR / f"{name}.jsonl"):
        if cand.exists():
            return cand
    return DEMOS_DIR / f"{name}.jsonl"


def assets_present() -> bool:
    return EXE_PATH.exists()


def load_exe() -> NEExecutable:
    return parse_ne(EXE_PATH)


def create_machine() -> Win16Machine:
    return _create_machine(EXE_PATH, winflags=WINFLAGS_NO_FPU)


def install_hooks(machine) -> int:
    """Install SimAnt's lifted-island hooks; returns the number installed.
    (Called by scripts/games.install_game_hooks and play.py --hooks.)"""
    from . import hooks
    return hooks.install(machine)
