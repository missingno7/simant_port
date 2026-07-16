"""SimAnt's strict-VMless boot wiring (dos_re_2.0 §1a/§1a').

The game-specific, LOADER-FREE half of the EXE-independence wall: everything
``scripts/play_vmless.py`` needs that must not drag the NE loader or the EXE
path constants onto the strict runner's import graph.  ``simant.runtime`` is
deliberately NOT imported here (it pins the executable's path); the lint
(scripts/lint_vmless_independence.py) walks the import graph from the strict
runner and this module and proves no loader edge is reachable.

The boot image itself is built by ``scripts/build_vmless_boot_image.py``
(which DOES consume the EXE — at build time only).
"""
from __future__ import annotations

from pathlib import Path

from . import _env  # noqa: F401  (puts the win16_re framework on sys.path)

from win16.api.surface import WINFLAGS_NO_FPU, build_registry

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_DIR = REPO_ROOT / "artifacts" / "vmless_boot"
LIFT_DIR = REPO_ROOT / "simant" / "lifted" / "graph"
DEMOS_DIR = REPO_ROOT / "artifacts" / "demos"

GAME_NAME = "simant"
#: The game DATA files (fonts, sound, .DAT databases — read via INT 21h at
#: run time).  Data stays readable under the EXE-independence wall; only the
#: executable is walled off (by name AND content hash).
DATA_ROOT = REPO_ROOT / "assets" / "ANTWIN"

#: Entries configured for INTERPRETED execution under the strict runner:
#: none.  (simant/facts/keep_interpreted.txt lists _DoInt3, but that entry is
#: OUTSIDE the corpus — dead code, scan-refused, zero static callers — not an
#: interpreted configuration: reaching it under the armed poison fails loud,
#: which is exactly the wall's contract for out-of-corpus addresses.)
STRICT_SKIP: frozenset[str] = frozenset()

#: Corpus exclusions, for the banner: entries with no lifted module and WHY.
CORPUS_EXCLUSIONS = {
    "430E:F85B": "_DoInt3 — dead debug stub (decodes into 0xFFFF padding; "
                 "zero static callers); fail-loud if ever reached",
}


#: The generated park policy that ships beside the lifted graph
#: (scripts/liftemit.py writes it from simant/facts/boundary_heads*.txt).
POLICY_NAME = "boundary_policy.json"


def boundary_park_kinds(lift_dir: Path | str = LIFT_DIR) -> dict:
    """``{(cs, ip): kind}`` — the INTERACTIVE park cost per boundary head,
    for ``win16.interactive.InteractiveDriver.arm_boundary_parks``.

    Read from the graph's own generated policy file (paragraph-keyed, so no
    NE mapping and no facts files are needed at run time — it ships wherever
    the graph ships).  Absent file = no policy: every head takes the host's
    default cost.  The kinds are NOT an emission input; the same graph runs
    identically under any policy (and under none, headless).
    """
    import json
    path = Path(lift_dir) / POLICY_NAME
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {(int(cs, 16), int(ip, 16)): kind
            for cs, ip, kind in ((k.split(":")[0], k.split(":")[1], v)
                                 for k, v in doc.get("kinds", {}).items())}


def registry_factory():
    """SimAnt's API surface, loader-free (WINFLAGS: no x87 emulator forms —
    the NE carries real x87 opcodes)."""
    return build_registry(winflags=WINFLAGS_NO_FPU)


def resolve_demo(name: str) -> Path:
    """Demo-name resolution (mirrors simant.runtime.resolve_demo, which the
    strict runner must not import): NAME, artifacts/demos/NAME(.jsonl)."""
    for cand in (Path(name), DEMOS_DIR / name, DEMOS_DIR / f"{name}.jsonl"):
        if cand.exists():
            return cand
    return DEMOS_DIR / f"{name}.jsonl"


def demo_out_path(name: str) -> Path:
    """Where `--record-demo NAME` writes: artifacts/demos/NAME.jsonl.  A NAME
    that is already a path (has a suffix or a directory part) is used verbatim.
    Lives here (not simant.runtime) so the interactive host stays on the
    loader-free import graph — ``simant.runtime`` re-exports it."""
    p = Path(name)
    if p.suffix or len(p.parts) > 1:
        return p
    DEMOS_DIR.mkdir(parents=True, exist_ok=True)
    return DEMOS_DIR / f"{name}.jsonl"


def boot_strict(boot_dir: Path | str = BOOT_DIR, *,
                lift_dir: Path | str = LIFT_DIR,
                game_root: Path | str | None = None,
                arm_wall: bool = True):
    """Boot the strict-VMless SimAnt machine from the data-only boot image:
    EXE-free load, full graph install, poison armed.  Returns
    ``(machine, manifest, installed)``."""
    from win16.bootimage import boot_vmless_machine
    return boot_vmless_machine(boot_dir, registry_factory,
                               lift_dir=lift_dir, skip=STRICT_SKIP,
                               game_root=game_root or DATA_ROOT,
                               arm_wall=arm_wall)
