"""Deploy the strict-VMless SimAnt runner as a standalone folder (and, with --exe, a PyInstaller build).

Rips the strict runner (scripts/play_vmless.py + its interactive host play.py) out of the RE
workbench: computes the import closure of the runner surface (the same package-dir mapping the
M3 independence lint uses), refuses/skips the deny-listed modules (the EXE-boot edge and the
recovery toolchain must not ship), copies the closure + the generated data (boot image, lifted
graph, one reference demo) + a launcher into ``dist/simant_vmless/``, and SMOKE-TESTS the result
in an isolated subprocess (sys.path = the dist tree only, cwd = a temp dir, SIMANTW.EXE
physically ABSENT): boot from the bundled boot image, replay the pinned 45M-instruction
cold_nohooks prefix, and require BOTH wall banners + the pinned digest + zero denied imports.

Unlike pre2's CPU-less native deploy, Stage-1 VMless still runs ON the dos_re CPU state and the
win16 Python OS layer — those ARE the runtime and ship whole; only the NE-loader *entry points*
(simant.runtime / win16.app) and the recovery workbench stay out.  win16/ne.py + win16/loader.py
ship as CLASS CARRIERS (program.pickle holds an NEExecutable, the machine is a Win16Machine) —
the strict import graph proven by scripts/lint_vmless_independence.py never calls parse_ne/
load_ne/create_machine, and the runtime exe-access guard stays armed.

The deployed folder needs only (1) Python + the requirements, (2) the game DATA files from an
original "SimAnt for Windows" install (fonts, sounds, .DAT databases — SIMANTW.EXE itself is
explicitly NOT needed and never read) — point --game-root at them, or drop the folder contents
next to the game files.

    python scripts/deploy_vmless.py                # build + smoke-test dist/simant_vmless/
    python scripts/deploy_vmless.py --exe          # then run PyInstaller + smoke-test the EXE
    python scripts/deploy_vmless.py --out DIR      # custom output folder
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401  (puts win16_re on sys.path)
import win16  # noqa: E402,F401

# The runner surface: the strict runner, its loader-free boot module, and the
# interactive host (all three are scripts/lint_vmless_independence.py ROOTS —
# the lint proves this surface's module-level graph is EXE/loader-free).
ENTRY_ROOTS = (
    "scripts/play_vmless.py",
    "scripts/play.py",
    "simant/vmless_boot.py",
)

# Package roots — the same mapping the M3 lint passes to dos_re's
# tools/lint_independence.py (--package-dir): the nested-submodule layout.
PKG_DIRS = {
    "simant": ROOT / "simant",
    "win16": ROOT / "win16_re" / "win16",
    "dos_re": ROOT / "win16_re" / "dos_re" / "dos_re",
}

# Modules that must NOT ship.  The first block is the EXE-boot edge: the strict
# boot path never touches it (lint-proven); play.py's --resume / EXE-boot
# branches import these function-locally and FAIL LOUD (ImportError) in the
# deployed tree if ever used.  The rest is the RE workbench, belt-and-braces —
# nothing on the runner surface reaches it, and the closure assert keeps it so.
DENY = (
    "simant.runtime", "win16.app",                  # the EXE-boot entry points
    "simant.hooks",                                 # interpreter-era islands (hooks=False here)
    "simant.probes", "simant.tests", "simant.bridge", "simant.native",
    "simant.recovered",
    "win16.irgen", "win16.callgraph", "win16.apicoverage", "win16.tick_demo",
    "dos_re.lift.analyze", "dos_re.lift.emit", "dos_re.lift.emit32",
    "dos_re.lift.ir", "dos_re.lift.irgen_core", "dos_re.lift.manifest",
    "dos_re.verification", "dos_re.frame_verify", "dos_re.checkpoints",
    "dos_re.coverage", "dos_re.frontier",
)

# Generated data that ships beside the code (all regenerable, gitignored):
GRAPH_DIR = ROOT / "simant" / "lifted" / "graph"          # the lifted graph (data, loaded BY PATH)
BOOT_DIR = ROOT / "artifacts" / "vmless_boot"             # the data-only boot image
DEMO = ROOT / "artifacts" / "demos" / "cold_nohooks.jsonl"  # reference demo (smoke + tester verify)

REQUIREMENTS = "pygame\nnumpy\nPillow\n"

LAUNCHER_NAME = "simant_vmless.py"

LAUNCHER = '''\
"""SimAnt VMless standalone — launcher (release {release}).

Boots the byte-exact VMless SimAnt from the bundled data-only boot image; the
original SIMANTW.EXE is neither needed nor read (runtime-guarded).  The game
DATA files from an original "SimAnt for Windows" install are required — pass
--game-root DIR, or drop them next to this launcher.
"""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)                     # PyInstaller build
if FROZEN:
    BASE = Path(sys._MEIPASS)                              # bundled data tree
    HERE = Path(sys.executable).resolve().parent           # the drop-in location
    # The vendored framework trees ship as data files under _internal; the
    # documented *_PATH escape hatches point the in-repo path bootstrap
    # (simant/_env.py -> win16/_env.py) at them.  Module RESOLUTION still
    # prefers the frozen modules (PyInstaller's importer runs first) — the
    # data tree only satisfies the bootstrap's existence checks, with
    # byte-identical files either way.
    os.environ["WIN16_RE_PATH"] = str(BASE / "win16_re")
    os.environ["DOS_RE_PATH"] = str(BASE / "win16_re" / "dos_re")
else:
    BASE = HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(BASE))
    sys.path.insert(1, str(BASE / "scripts"))

DATA_MARKERS = ("SHARED.DAT", "SIMANT.CFG")                # any = a game-data dir


def _with_game_root(argv):
    if any(a == "--game-root" or a.startswith("--game-root=") for a in argv):
        return argv
    for cand in (HERE, HERE / "antwin", HERE / "assets" / "ANTWIN"):
        if any((cand / m).exists() for m in DATA_MARKERS):
            return ["--game-root", str(cand)] + argv
    print("[simant_vmless] no game data found next to the launcher "
          "(looked for SHARED.DAT / SIMANT.CFG) — pass --game-root DIR "
          "pointing at your SimAnt for Windows data files",
          file=sys.stderr)
    return argv


if __name__ == "__main__":
    from play_vmless import main
    raise SystemExit(main(_with_game_root(sys.argv[1:])))
'''

README = """\
SimAnt for Windows — VMless port (standalone pre-release {release})
===================================================================

A byte-exact source reconstruction of Maxis SimAnt for Windows (1991), running
as recovered code on a Python Win16 OS layer — no emulator interpretation, no
original executable.  Both hard walls are enforced AT RUNTIME and printed in
the startup banner:

  * VMless execution wall — every original instruction runs as lifted,
    recovered code; the interpreter is physically forbidden ("VMless wall:
    HOLDS" after a demo replay).
  * EXE independence wall — the machine boots from a generated data-only boot
    image.  SIMANTW.EXE is neither needed nor read: it works with the file
    physically absent, and a guard blocks it by name AND content hash if
    present ("EXE-independence wall: HOLDS").

The original game DATA files are NOT included and ARE required.

Requirements
------------
* Python 3.11+ with:  pip install -r requirements.txt
  (the PyInstaller build needs no Python at all)
* The game DATA files from an original "SimAnt for Windows" install:
  FONT1..FONT4, FONTRES.FON, SHARED.DAT/.NDX, MWINNT.DAT/.NDX,
  WINGANT.DAT/.NDX, SOUND.DAT/.NDX, SIMANT.CFG, SIMANT.HLP and the SOUND\\
  folder (*.mid music, *.snd effects).  SIMANTW.EXE is explicitly NOT needed.

Run
---
    python {launcher} --game-root "C:/path/to/SIMANT"      (source tree)
    {exe_name} --game-root "C:/path/to/SIMANT"             (PyInstaller build)

or copy this folder's contents INTO the game data folder (or the data files
next to the launcher) and run it with no arguments.

Options: --scale N (integer pixel scale), --speed N (time multiplier),
--demo NAME|PATH (headless deterministic replay), --game-root DIR.

Controls (the game's own): the mouse plays the game (select, dig, build,
order); arrow keys scroll the colony view; F1 help, F2 new game, F4 pause,
F8 radar.  Close the SimAnt window to quit.

Verify the walls (optional)
---------------------------
    python {launcher} --game-root DIR --demo artifacts/demos/cold_nohooks.jsonl

replays a recorded session headlessly and must end with both
"EXE-independence wall: HOLDS" and "VMless wall: HOLDS".

Known limitations (pre-release {release})
-----------------------------------------
* Pre-release: expect rough edges.  Report issues WITH the console output —
  every failure is designed to be loud and specific (a console window stays
  attached for exactly that reason).
* Only the literal lifted-graph flavor ships; the routed-adapter flavor
  (readable recovered source routed through named state) is a separate build
  and is not included yet.
* Demo recording/replay of NEW demos is a workbench feature; snapshot-anchored
  demos are refused by design (cold demos only).
* Saving/loading games, the help viewer and some dialogs are lightly exercised
  paths — failures there are loud, not silent.
"""

SMOKE = '''\
"""Deployed-tree smoke test: prove the standalone runs the strict-VMless game.

Run by deploy_vmless.py with cwd = a TEMP dir (SIMANTW.EXE physically absent
anywhere under it) and a scrubbed environment, so every simant/win16/dos_re
import resolves INSIDE the deployed tree.  Boots from the bundled boot image,
replays the pinned 45M-instruction cold_nohooks prefix, and requires BOTH wall
banners, the pinned digest, and zero deny-listed imports."""
import contextlib
import io
import json
import sys
from pathlib import Path

DIST = Path(sys.argv[1]).resolve()
DATA = Path(sys.argv[2]).resolve()
PIN = json.loads(sys.argv[3])           # {{"limit":..,"end_instr":..,"digest":..,"release":..}}

sys.path.insert(0, str(DIST))
sys.path.insert(1, str(DIST / "scripts"))

cwd = Path.cwd()
assert not [p for p in cwd.rglob("*") if p.suffix.upper() == ".EXE"], \\
    "the smoke cwd must not contain any .EXE"
assert not [p for p in DATA.rglob("*") if p.suffix.upper() == ".EXE"], \\
    "the smoke game-data dir must not contain any .EXE"

# The pinned demo prefix (same recipe as simant/tests/test_vmless_walls.py).
src = (DIST / "artifacts" / "demos" / "cold_nohooks.jsonl").read_text().splitlines()
kept = [src[0]] + [ln for ln in src[1:]
                   if json.loads(ln).get("i", 0) <= PIN["limit"]]
prefix = cwd / "prefix.jsonl"
prefix.write_text("\\n".join(kept) + "\\n")

import play_vmless                                                   # noqa: E402

out = io.StringIO()
with contextlib.redirect_stdout(out):
    rc = play_vmless.main(["--demo", str(prefix), "--game-root", str(DATA)])
text = out.getvalue()
sys.stdout.write(text)

assert rc == 0, f"play_vmless returned {{rc}}"
assert f"release {{PIN['release']}}" in text, "release banner missing"
assert "EXE-independence wall: HOLDS" in text, "EXE-independence wall banner missing"
assert "VMless wall: HOLDS" in text, "VMless wall banner missing"
assert f"final digest: {{PIN['digest']}}" in text, "pinned digest mismatch"
assert f"instructions: {{PIN['end_instr']:,}}" in text, "pinned instruction count mismatch"

DENY = {deny}
bad = sorted(m for m in sys.modules if any(m == d or m.startswith(d + ".") for d in DENY))
assert not bad, f"deny-listed modules imported at runtime: {{bad}}"
for name in ("simant", "win16", "dos_re"):
    f = Path(sys.modules[name].__file__).resolve()
    assert str(f).startswith(str(DIST)), f"{{name}} resolved OUTSIDE the dist tree: {{f}}"

print(f"SMOKE OK: strict replay on the deployed tree — {{PIN['end_instr']:,}} "
      f"instructions, digest {{PIN['digest'][:16]}}..., walls HOLD, deny-free")
'''


# --- import-closure computation ---------------------------------------------

def path_to_module(p: Path) -> str | None:
    """Dotted module name for a file inside the mapped package roots / scripts."""
    for prefix, d in PKG_DIRS.items():
        try:
            rel = p.relative_to(d)
        except ValueError:
            continue
        parts = [prefix] + list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)
    if p.parent == ROOT / "scripts":
        return p.stem
    return None


def module_to_path(mod: str) -> Path | None:
    parts = mod.split(".")
    base = PKG_DIRS.get(parts[0])
    if base is not None:
        for cand in (base.joinpath(*parts[1:]).with_suffix(".py") if parts[1:]
                     else base / "__init__.py",
                     base.joinpath(*parts[1:]) / "__init__.py"):
            if cand.is_file():
                return cand
    p = ROOT / "scripts" / (mod + ".py")            # scripts-local bare imports (play)
    if p.is_file():
        return p
    return None


def denied(mod: str) -> bool:
    return any(mod == d or mod.startswith(d + ".") for d in DENY)


def imports_of(path: Path):
    """Every import target of ``path`` — module-level AND function-local (a
    lazy import is a runtime capability of the shipped file, so its target
    must ship too unless deny-listed, in which case it fails loud)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mod = path_to_module(path)
    if mod is None:
        pkg_parts: list[str] = []
    elif path.name == "__init__.py":
        pkg_parts = mod.split(".")
    else:
        pkg_parts = mod.split(".")[:-1]
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield a.name
        elif isinstance(n, ast.ImportFrom):
            if n.level == 0 and n.module:
                yield n.module
                for a in n.names:                   # `from pkg import sub` — sub may be a module
                    yield f"{n.module}.{a.name}"    # (harmless if it's a symbol: no file match)
            elif n.level > 0 and pkg_parts:         # relative: from .x import Y / from .. import Z
                base = pkg_parts[:len(pkg_parts) - (n.level - 1)]
                if base:
                    yield ".".join(base + ([n.module] if n.module else []))
                    if not n.module:                # from . import a, b -> the siblings
                        for a in n.names:
                            yield ".".join(base + [a.name])


def graph_runtime_imports() -> set[str]:
    """The modules the EMITTED graph imports at load time — the graph ships as
    plain data files loaded by path, so the AST walk cannot see these edges;
    derive them from the generated modules themselves, never assume."""
    mods: set[str] = set()
    for p in GRAPH_DIR.glob("*.py"):
        for line in p.read_text(encoding="utf-8").splitlines()[:60]:
            if line.startswith(("ENTRY", "SIGNATURE", "def ")):
                break
            if line.startswith(("import ", "from ")):
                target = line.split()[1]
                if target.split(".")[0] in PKG_DIRS:
                    mods.add(target)
    return mods


def compute_closure(extra_roots: set[str] = frozenset()) -> list[Path]:
    todo = [ROOT / r for r in ENTRY_ROOTS]
    for mod in sorted(extra_roots):
        p = module_to_path(mod)
        if p is None:
            raise SystemExit(f"graph runtime dependency {mod} not resolvable to a file")
        todo.append(p)
    closure: set[Path] = set()
    while todo:
        p = todo.pop()
        if p in closure:
            continue
        closure.add(p)
        # importing a submodule EXECUTES its ancestor packages' __init__.py,
        # so those are part of the walk (scanned, not just copied): a package
        # init that imports siblings (dos_re/lift/__init__.py) pulls them in.
        d = p.parent
        while True:
            ini = d / "__init__.py"
            if ini.exists() and ini not in closure:
                todo.append(ini)
            if d in (ROOT, ROOT / "scripts") or d.parent == d:
                break
            d = d.parent
        for m in imports_of(p):
            if denied(m):
                continue                            # not shipped; lazy uses fail loud
            mp = module_to_path(m)
            if mp is not None and mp not in closure:
                todo.append(mp)
    return sorted(closure)


# --- deploy ------------------------------------------------------------------

def dist_rel(p: Path) -> Path:
    """Where a closure file lands inside the dist tree: the REPO-relative path
    for simant/scripts, and the submodule-relative path (under win16_re/) for
    the framework packages — the dist mirrors the repo layout exactly, so
    simant/_env.py's submodule bootstrap works unchanged."""
    return p.relative_to(ROOT)


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"deploy_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assert_no_executable(tree: Path, exe_sha: str, exe_size: int) -> None:
    """The dist tree must not carry the original executable in ANY form:
    executable suffix, MZ header, or content hash (renaming does not launder)."""
    for f in sorted(tree.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() in (".exe", ".com", ".dll"):
            raise SystemExit(f"executable-suffixed file in the dist tree: {f}")
        with open(f, "rb") as fh:
            if fh.read(2) in (b"MZ", b"ZM"):
                raise SystemExit(f"MZ-headed file in the dist tree: {f}")
        if f.stat().st_size == exe_size and \
                hashlib.sha256(f.read_bytes()).hexdigest() == exe_sha:
            raise SystemExit(f"{f} IS the source EXE (hash match)")


def prepare_smoke_data(tmp: Path) -> Path:
    """Copy the game DATA files (never the executable / the debug symbols)
    into ``tmp`` — the clean-room recipe from simant/tests/test_vmless_walls.py."""
    src = ROOT / "assets" / "ANTWIN"
    data = tmp / "data"
    data.mkdir()
    for f in src.iterdir():
        if f.is_file() and f.suffix.upper() not in (".EXE", ".SYM"):
            shutil.copy(f, data / f.name)
        elif f.is_dir():
            shutil.copytree(f, data / f.name)
    return data


def smoke_pin() -> dict:
    """The pinned clean-room prefix (single source of truth: the walls test)."""
    walls = importlib.util.spec_from_file_location(
        "deploy_walls_pin", ROOT / "simant" / "tests" / "test_vmless_walls.py")
    mod = importlib.util.module_from_spec(walls)
    walls.loader.exec_module(mod)
    release = _load_script("play_vmless").VMLESS_RELEASE
    return {"limit": mod.PREFIX_LIMIT, "end_instr": mod.PREFIX_END_INSTR,
            "digest": mod.PREFIX_DIGEST, "release": release}


def prune_pycache(tree: Path) -> None:
    """Bytecode scratch (from the smoke run / PyInstaller's analysis) never
    ships and never bundles — the dist tree stays sources + data only."""
    for pyc in sorted(tree.rglob("__pycache__")):
        shutil.rmtree(pyc)


def scrubbed_env() -> dict:
    drop = ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
            "WIN16_RE_PATH", "DOS_RE_PATH")
    return {k: v for k, v in os.environ.items() if k not in drop}


def run_smoke(cmd: list[str], tmp: Path, what: str) -> None:
    print(f"smoke test ({what}): pinned 45M-instruction strict replay, "
          f"EXE absent, cwd={tmp} ...")
    r = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=scrubbed_env(),
                       timeout=1800)
    tail = "\n".join(r.stdout.splitlines()[-14:])
    print(tail)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"SMOKE FAILED ({what}) — rc={r.returncode}")


def exe_smoke(exe: Path, dist: Path, pin: dict) -> None:
    """Smoke-test THE BUILT EXE itself: same temp-cwd, EXE-absent, pinned-
    digest recipe, asserted from the outside on its console output."""
    with tempfile.TemporaryDirectory(prefix="simant_vmless_exe_smoke_") as td:
        tmp = Path(td)
        data = prepare_smoke_data(tmp)
        src = (dist / "artifacts" / "demos" / "cold_nohooks.jsonl").read_text().splitlines()
        kept = [src[0]] + [ln for ln in src[1:]
                           if json.loads(ln).get("i", 0) <= pin["limit"]]
        prefix = tmp / "prefix.jsonl"
        prefix.write_text("\n".join(kept) + "\n")
        print(f"smoke test (built exe): pinned 45M-instruction strict replay, "
              f"cwd={tmp} ...")
        r = subprocess.run([str(exe), "--demo", str(prefix),
                            "--game-root", str(data)],
                           cwd=str(tmp), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=scrubbed_env(), timeout=1800)
        print("\n".join(r.stdout.splitlines()[-12:]))
        if r.returncode != 0:
            sys.stderr.write(r.stderr)
            raise SystemExit(f"SMOKE FAILED (built exe) — rc={r.returncode}")
        for needle in (f"release {pin['release']}",
                       "EXE-independence wall: HOLDS",
                       "VMless wall: HOLDS",
                       f"final digest: {pin['digest']}",
                       f"instructions: {pin['end_instr']:,}"):
            if needle not in r.stdout:
                raise SystemExit(f"SMOKE FAILED (built exe) — missing {needle!r}")
        print("SMOKE OK (built exe): walls HOLD, pinned digest reproduced")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "dist" / "simant_vmless"))
    ap.add_argument("--exe", action="store_true",
                    help="also run PyInstaller (pip install pyinstaller) and "
                         "smoke-test the built exe")
    ap.add_argument("--skip-smoke", action="store_true",
                    help="skip the smoke tests (build-only; NOT for release)")
    args = ap.parse_args()
    out = Path(args.out)

    for req, hint in ((GRAPH_DIR / "graph_manifest.json",
                       "scripts/liftemit.py + scripts/liftlink.py"),
                      (BOOT_DIR / "manifest.json",
                       "scripts/build_vmless_boot_image.py"),
                      (DEMO, "the reference demo (artifacts/demos/cold_nohooks.jsonl)")):
        if not req.exists():
            raise SystemExit(f"missing generated input {req} — build it: {hint}")

    # 0. the static wall must hold before anything ships
    if _load_script("lint_vmless_independence").main([]) != 0:
        raise SystemExit("independence lint FAILED — nothing deployed")

    # 1. the code closure (runner surface + the graph's own load-time imports)
    graph_deps = graph_runtime_imports()
    bad_deps = sorted(m for m in graph_deps if denied(m))
    if bad_deps:
        raise SystemExit(f"the emitted graph imports deny-listed modules: {bad_deps}")
    closure = compute_closure(graph_deps)
    leaked = [p for p in closure
              if (m := path_to_module(p)) is not None and denied(m)]
    if leaked:
        raise SystemExit(f"deny-listed files leaked into the closure: "
                         f"{[str(p) for p in leaked]}")

    manifest = json.loads((BOOT_DIR / "manifest.json").read_text(encoding="utf-8"))
    exe_sha = manifest["source_exe"]["sha256"]
    exe_size = manifest["source_exe"]["size"]

    # 2. lay out the dist tree (mirrors the repo layout, so the submodule
    #    path bootstrap works unchanged)
    if out.exists():
        for child in out.iterdir():                # keep the dir inode (Windows)
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    else:
        out.mkdir(parents=True)
    for p in closure:
        dst = out / dist_rel(p)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    n_code = len(closure)

    boot_dst = out / "artifacts" / "vmless_boot"
    shutil.copytree(BOOT_DIR, boot_dst)
    graph_dst = out / "simant" / "lifted" / "graph"
    shutil.copytree(GRAPH_DIR, graph_dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    demo_dst = out / "artifacts" / "demos" / DEMO.name
    demo_dst.parent.mkdir(parents=True)
    shutil.copy2(DEMO, demo_dst)

    pin = smoke_pin()
    (out / LAUNCHER_NAME).write_text(LAUNCHER.format(release=pin["release"]),
                                     encoding="utf-8")
    (out / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
    (out / "README.md").write_text(
        README.format(release=pin["release"], launcher=LAUNCHER_NAME,
                      exe_name="simant_vmless.exe"), encoding="utf-8")

    # 3. the EXE never ships, in any disguise
    assert_no_executable(out, exe_sha, exe_size)

    n_graph = sum(1 for _ in graph_dst.glob("*.py"))
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"deployed {n_code} code files + {n_graph} graph modules + boot image "
          f"-> {out}  ({total / 1e6:.1f} MB)")

    # 4. smoke-test the deployed tree in a clean subprocess
    if not args.skip_smoke:
        smoke_py = out / "_smoke.py"
        smoke_py.write_text(SMOKE.format(deny=repr(set(DENY))), encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="simant_vmless_smoke_") as td:
            tmp = Path(td)
            data = prepare_smoke_data(tmp)
            run_smoke([sys.executable, str(smoke_py), str(out), str(data),
                       json.dumps(pin)], tmp, "dist tree")
        smoke_py.unlink()                          # scaffolding; the proof ran
        prune_pycache(out)

    # 5. --exe: PyInstaller onedir + smoke-test the binary itself
    if args.exe:
        spec = out / "simant_vmless.spec"
        shutil.copy2(ROOT / "scripts" / "simant_vmless.spec", spec)
        print("running PyInstaller ...")
        r = subprocess.run([sys.executable, "-m", "PyInstaller",
                            "--distpath", str(out.parent / "exe"),
                            "--workpath", str(out.parent / "build"),
                            "-y", str(spec)],
                           cwd=str(out), text=True)
        if r.returncode != 0:
            raise SystemExit("PyInstaller failed (pip install pyinstaller?)")
        exe = out.parent / "exe" / "simant_vmless" / "simant_vmless.exe"
        print(f"exe build -> {exe.parent}")
        prune_pycache(out)                         # analysis-time scratch
        if not args.skip_smoke:
            exe_smoke(exe, out, pin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
