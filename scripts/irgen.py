"""irgen — SIMANTW's recovery IR: every SIMANTW.SYM entry, one JSON document.

The DOS_RE 2.0 serialization step (win16_re/dos_re/docs/recovery_ir.md) for
SimAnt: resolves ALL named routines in SIMANTW.SYM's code segments (the same
1319-entry corpus scripts/census.py sweeps), runs the generic win16 irgen
front-end over a loaded machine, and writes the deterministic, regeneratable
``artifacts/recovery_ir.json`` (gitignored — a generated artifact, never
committed; delete it and this script reproduces it byte-identically).

Game facts live in simant/facts/ as committed text (NE_SEG:HEX_OFFSET per
line + comments): keep_interpreted.txt is the census scan frontier (x87 +
_DoInt3), tagged env_wait in the IR.  Symbol identity is first-class: every
record carries symbol/module/ne_seg from the .SYM (and alias names where two
symbols share an address) alongside its canonical paragraph-base CS:IP key,
and the .SYM sha1 lands in provenance next to the EXE's.

    python scripts/irgen.py [--snapshot DIR] [--seg N ...] [--no-probe]
                            [--out artifacts/recovery_ir.json]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401

from win16.irgen import build_ir, write_document  # noqa: E402
from simant.probes.symbols import _SYM_PATH, _segments  # noqa: E402
from simant.runtime import create_machine  # noqa: E402

#: NE segments holding game CODE (4=_TEXT is the C runtime, also code) —
#: the same corpus as scripts/census.py.
CODE_SEGS = (1, 2, 3, 4, 5, 6, 7)

FACTS_DIR = REPO_ROOT / "simant" / "facts"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "recovery_ir.json"


def read_fact_pairs(path: Path) -> list[tuple[int, int]]:
    """NE pairs from a facts file: 'NE_SEG:HEX_OFFSET  # comment' per line
    (segment index decimal, offset hex)."""
    pairs: list[tuple[int, int]] = []
    if not path.exists():
        return pairs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        seg, off = line.split(":", 1)
        pairs.append((int(seg, 10), int(off, 16) & 0xFFFF))
    return pairs


def sym_corpus(segs=CODE_SEGS):
    """(entries, names): every SIMANTW.SYM entry of the code segments, sorted
    by (seg, offset), plus its identity metadata.  Two symbols may alias one
    address (e.g. __aFftol/__ftol) — the first (sorted) name is `symbol`, the
    rest become `aliases`; the address is scanned once."""
    entries: list[tuple[int, int]] = []
    names: dict[tuple[int, int], dict] = {}
    n_syms = 0
    for seg_i, (modname, syms) in enumerate(_segments(), start=1):
        if seg_i not in segs or not syms:
            continue
        for off, name in syms:              # sorted by (offset, name)
            n_syms += 1
            key = (seg_i, off)
            if key in names:
                names[key].setdefault("aliases", []).append(name)
                continue
            entries.append(key)
            names[key] = {"symbol": name, "module": modname}
    return entries, names, n_syms


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default=None,
                    help="build the IR over this vmsnap snapshot instead of "
                         "a fresh create_machine() boot image")
    ap.add_argument("--seg", type=int, action="append", default=[],
                    help="restrict to these NE segments (repeatable)")
    ap.add_argument("--no-probe", action="store_true",
                    help="static-only scan (skip the per-entry interpreter "
                         "step-length probe; faster, decoder unchecked)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    segs = tuple(args.seg) or CODE_SEGS

    if args.snapshot:
        from win16.vmsnap import load_snapshot
        machine = load_snapshot(args.snapshot, create_machine)
        snapshot_desc = str(args.snapshot)
    else:
        machine = create_machine()
        snapshot_desc = "(fresh create_machine boot image)"
    machine.cpu.trace_enabled = False

    entries, names, n_syms = sym_corpus(segs)
    keep = [p for p in read_fact_pairs(FACTS_DIR / "keep_interpreted.txt")
            if p[0] in segs]
    heads = [p for p in read_fact_pairs(FACTS_DIR / "boundary_heads.txt")
             if p[0] in segs]
    dispatch = [p for p in read_fact_pairs(FACTS_DIR / "dispatch_entries.txt")
                if p[0] in segs]

    t0 = time.perf_counter()
    doc = build_ir(
        machine, entries,
        machine_factory=None if args.no_probe else create_machine,
        keep_interpreted=keep,
        boundary_heads=heads,
        dispatch_entries=dispatch,
        names=names,
        snapshot=snapshot_desc,
        symbols=f"{_SYM_PATH.name} sha1="
                f"{hashlib.sha1(_SYM_PATH.read_bytes()).hexdigest()}",
    )
    dt = time.perf_counter() - t0
    out = write_document(doc, args.out)

    functions, unsupported = doc["functions"], doc["unsupported"]
    n_liftable = sum(1 for f in functions.values() if f["liftable"])
    n_unsupported_fns = len({u["entry"] for u in unsupported})
    print(f"recovery IR v{doc['ir_version']}: {n_syms} SYM entries -> "
          f"{len(functions)} functions ({n_liftable} liftable, "
          f"{n_unsupported_fns} unsupported over {len(unsupported)} refusal "
          f"records) in {dt:.1f}s -> {out}")
    if unsupported:
        print("unsupported ledger (the fail-loud frontier):")
        seen = set()
        for u in unsupported:
            if u["entry"] in seen:
                continue
            seen.add(u["entry"])
            fn = functions[u["entry"]]
            label = "/".join([fn.get("symbol", "?")] + fn.get("aliases", []))
            print(f"  {u['entry']} seg{fn['ne_seg']} "
                  f"{fn.get('module', '?')}!{label}: {u['reason']} "
                  f"({u['detail']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
