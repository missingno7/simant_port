# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the strict-VMless SimAnt standalone — run from INSIDE
the deployed folder (scripts/deploy_vmless.py --exe drives this with
cwd = dist/simant_vmless, and copies this spec there first).

Produces a onedir app: dist/exe/simant_vmless/simant_vmless.exe + _internal/.
Game DATA files are NOT bundled (the user provides them; --game-root).  The
original SIMANTW.EXE is neither bundled nor needed — the deploy asserts it is
absent from the tree, and the runtime guard blocks it by name AND hash.

Layout notes (why this spec looks the way it does):
* The lifted graph (~1,900 generated modules) and the boot image ship as DATA
  files, not frozen modules — dos_re.lift.install loads the graph BY PATH via
  spec_from_file_location, and simant.vmless_boot resolves both from its own
  __file__ (= sys._MEIPASS under a frozen build), so bundling them at the same
  repo-relative destinations makes every default path work unchanged.
* The win16_re source tree (already deny-filtered by the deploy) also ships as
  data: the launcher points the documented WIN16_RE_PATH / DOS_RE_PATH escape
  hatches at it so the in-repo path bootstrap (simant/_env.py, win16/_env.py)
  finds real files; module resolution still uses the byte-identical frozen
  modules (PyInstaller's importer runs first).
* hiddenimports are DERIVED from the emitted graph's own import lines (the
  analyzer cannot see imports inside data files) — never assumed.
"""
from pathlib import Path

HERE = Path(SPECPATH)  # noqa: F821  (the deployed tree; PyInstaller injects SPECPATH)

# The emitted graph's load-time imports (dos_re.cpu / dos_re.hooks /
# dos_re.lift.runtime ...) — derived, since the graph ships as data.
graph_hidden: set[str] = set()
for p in (HERE / "simant" / "lifted" / "graph").glob("*.py"):
    for line in p.read_text(encoding="utf-8").splitlines()[:60]:
        if line.startswith(("ENTRY", "SIGNATURE", "def ")):
            break
        if line.startswith(("import ", "from ")):
            target = line.split()[1]
            if target.split(".")[0] in ("dos_re", "win16", "simant"):
                graph_hidden.add(target)

a = Analysis(
    ["simant_vmless.py"],
    pathex=[".", "scripts", "win16_re", "win16_re/dos_re"],
    binaries=[],
    datas=[
        ("artifacts/vmless_boot", "artifacts/vmless_boot"),
        ("artifacts/demos", "artifacts/demos"),
        ("simant/lifted/graph", "simant/lifted/graph"),
        ("win16_re", "win16_re"),          # the *_PATH bootstrap targets (see above)
    ],
    hiddenimports=sorted(graph_hidden) + ["dos_re.lift.install"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "pytest", "IPython", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="simant_vmless",
    debug=False,
    strip=False,
    upx=False,
    console=True,                          # fail-loud console output is the UX
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="simant_vmless",
)
