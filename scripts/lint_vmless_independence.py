"""lint_vmless_independence — static proof the strict runner is loader-free.

Thin wrapper over dos_re's generic tools/lint_independence.py: walks the
MODULE-LEVEL import graph rooted at scripts/play_vmless.py +
simant/vmless_boot.py + win16/bootimage.py and fails if any module on it
imports an EXE-loading symbol (``parse_ne``/``load_ne``/``create_machine``/
``load_snapshot`` + the dos_re defaults) or names an executable path literal.
The runtime ``exe_access_guard`` is the dynamic backstop; this is the static
wall (dos_re_2.0 §1a').

    python scripts/lint_vmless_independence.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401
import dos_re  # noqa: E402

ROOTS = [
    "scripts/play_vmless.py",
    "simant/vmless_boot.py",
]
FORBIDDEN = ["parse_ne", "load_ne", "create_machine"]
PACKAGE_DIRS = [
    f"win16={Path('win16_re') / 'win16'}",
    f"dos_re={Path('win16_re') / 'dos_re' / 'dos_re'}",
    "simant=simant",
]


def main(argv=None) -> int:
    tools = Path(dos_re.__file__).resolve().parents[1] / "tools"
    spec = importlib.util.spec_from_file_location(
        "dosre_lint_independence", tools / "lint_independence.py")
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)

    args = ["--repo-root", str(REPO_ROOT)]
    for r in ROOTS:
        args += ["--root", r]
    for f in FORBIDDEN:
        args += ["--forbidden", f]
    for prefix in ("win16", "simant"):
        args += ["--local-prefix", prefix]
    for pd in PACKAGE_DIRS:
        args += ["--package-dir", pd]
    return lint.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
