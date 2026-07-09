"""Locate the vendored win16_re framework (a git submodule of this repo) and
put it on sys.path.

win16_re is pinned in-repo at `win16_re/` (a real git submodule of
https://github.com/missingno7/win16_re.git) — `git clone --recurse-submodules`
(or `git submodule update --init --recursive`) is all a fresh checkout needs.
Once win16_re is on sys.path, importing `win16` transparently makes `dos_re`
importable too: win16_re/win16/_env.py owns that bootstrap itself, so this
project never needs to know dos_re is nested two levels down.

For active co-development of win16_re itself, set WIN16_RE_PATH to point at a
separate working checkout instead (e.g. one with uncommitted framework changes
being tested against this repo before they land upstream) — this is a
deliberate opt-in escape hatch, not the default.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SUBMODULE = Path(__file__).resolve().parent.parent / "win16_re"


def ensure_win16_re() -> Path:
    root = Path(os.environ["WIN16_RE_PATH"]) if "WIN16_RE_PATH" in os.environ else _SUBMODULE
    if not (root / "win16" / "__init__.py").exists():
        hint = ("WIN16_RE_PATH points at a bad checkout" if "WIN16_RE_PATH" in os.environ
                else "run `git submodule update --init --recursive` in this repo")
        raise ImportError(f"win16_re framework not found at {root} — {hint}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


ensure_win16_re()
