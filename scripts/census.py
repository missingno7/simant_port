"""census — the DOS_RE 2.0 frontier probe for SIMANTW: scan EVERY .SYM entry.

First step of the staged recovery pipeline (win16_re/dos_re/docs/dos_re_2.0.md):
run the mechanical lifter over the complete declared corpus — every named
routine in SIMANTW.SYM's code segments — and report, per segment and in total:

    liftable        scan_function accepted the CFG and emit_function produced
                    a literal VMless hook (nothing is installed; this is a
                    static census, not a verification run)
    refused         scan_function refused (histogram of structured reasons)
    emit-blocked    scanned OK but emit_function raised EmitUnsupported

The histograms ARE the deliverable: each distinct reason is either a generic
dos_re/win16_re capability gap or a game recovery fact to record — the 2.0
loop is "observe the frontier, improve the tooling, regenerate", never
hand-porting around it.

    python scripts/census.py [--emit-dir DIR] [--json artifacts/census.json]

Uses a fresh `create_machine()` (segments mapped + relocated at load; the
same image every state-diff oracle in tests/ scans against).  No demo and no
snapshot needed: liftability is a property of the code bytes.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401

from dos_re.lift.cfg import scan_function  # noqa: E402
from dos_re.lift.emit import EmitUnsupported, emit_function  # noqa: E402
from simant.probes.symbols import _segments, module_name  # noqa: E402
from simant.runtime import create_machine  # noqa: E402

#: NE segments holding game CODE (4=_TEXT is the C runtime, also code).
CODE_SEGS = (1, 2, 3, 4, 5, 6, 7)


def _probe_for(machine, cs: int):
    """A FRESH scratch interpreter per entry.  Probing executes instructions,
    and execution mutates the scratch (stack pushes, memory writes) — a probe
    shared across entries steps later instructions against corrupted state and
    reports false decoder-mismatches.  Cloning per entry costs ~20 ms; a wrong
    frontier costs a wrong roadmap."""
    from win16.verify import clone_machine
    scratch = clone_machine(machine, create_machine)
    cpu = scratch.cpu
    cpu.replacement_hooks.clear()
    cpu.hook_names.clear()
    cpu.hook_verifier = None
    cpu.trace_enabled = False

    def probe(ip: int):
        ip &= 0xFFFF
        cpu.s.cs, cpu.s.ip = cs & 0xFFFF, ip
        try:
            cpu.step()
        except Exception:  # noqa: BLE001 — an unprobeable address is a datum
            return None
        return ((cpu.s.ip - ip) & 0xFFFF) or None

    return probe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=None, help="write the full ledger here")
    ap.add_argument("--seg", type=int, action="append", default=[],
                    help="restrict to these NE segments (repeatable)")
    args = ap.parse_args(argv)
    segs = tuple(args.seg) or CODE_SEGS

    machine = create_machine()
    machine.cpu.trace_enabled = False

    ledger: dict[str, dict] = {}
    refuse_hist: Counter[str] = Counter()
    emit_hist: Counter[str] = Counter()
    per_seg: dict[int, Counter] = {}

    for seg_i, (modname, syms) in enumerate(_segments(), start=1):
        if seg_i not in segs or not syms:
            continue
        cs = machine.seg_bases[seg_i]
        lin = machine.mem.sel_base.get(cs & 0xFFFC, cs * 16)
        fetch = lambda ip, lin=lin: machine.mem.data[lin + (ip & 0xFFFF)]
        tally = per_seg.setdefault(seg_i, Counter())

        for off, name in syms:
            label = f"{seg_i}:{off:04X} {modname}!{name}"
            try:
                scan = scan_function(fetch, off, probe=_probe_for(machine, cs))
            except Exception as exc:  # noqa: BLE001 — a scanner crash is frontier data
                tally["scan_crash"] += 1
                refuse_hist[f"scan-crash: {type(exc).__name__}"] += 1
                ledger[label] = {"state": "scan_crash", "reason": str(exc)[:200]}
                continue
            if not scan.liftable:
                reasons = sorted({f"{r.reason} ({r.detail})" if r.detail else r.reason
                                  for r in scan.refusals})
                tally["refused"] += 1
                for r in reasons:
                    refuse_hist[r] += 1
                ledger[label] = {"state": "refused", "reasons": reasons}
                continue
            sig = bytes(machine.mem.data[lin + off:lin + off + 12])
            try:
                emit_function(scan, cs, f"lifted_{seg_i}_{off:04x}",
                              signature=sig, coverage=False)
            except EmitUnsupported as exc:
                tally["emit_blocked"] += 1
                emit_hist[str(exc)[:120]] += 1
                ledger[label] = {"state": "emit_blocked", "reason": str(exc)[:200]}
                continue
            except Exception as exc:  # noqa: BLE001
                tally["emit_crash"] += 1
                emit_hist[f"emit-crash: {type(exc).__name__}: {exc}"[:120]] += 1
                ledger[label] = {"state": "emit_crash", "reason": str(exc)[:200]}
                continue
            tally["liftable"] += 1
            ledger[label] = {"state": "liftable",
                             "insts": len(scan.insts),
                             "blocks": len(scan.block_leaders())}

    total = Counter()
    print(f"{'seg':>4} {'module':<16} {'entries':>8} {'liftable':>9} "
          f"{'refused':>8} {'emit-blk':>9} {'crash':>6}")
    for seg_i, tally in sorted(per_seg.items()):
        n = sum(tally.values())
        crash = tally["scan_crash"] + tally["emit_crash"]
        print(f"{seg_i:>4} {module_name(seg_i):<16} {n:>8} {tally['liftable']:>9} "
              f"{tally['refused']:>8} {tally['emit_blocked']:>9} {crash:>6}")
        total.update(tally)
    n = sum(total.values())
    crash = total["scan_crash"] + total["emit_crash"]
    pct = 100.0 * total["liftable"] / n if n else 0.0
    print(f"{'all':>4} {'':<16} {n:>8} {total['liftable']:>9} "
          f"{total['refused']:>8} {total['emit_blocked']:>9} {crash:>6}"
          f"   ({pct:.1f}% liftable)")

    if refuse_hist:
        print("\nrefusal reasons (the scan frontier):")
        for reason, cnt in refuse_hist.most_common():
            print(f"  {cnt:>5}  {reason}")
    if emit_hist:
        print("\nemit-unsupported reasons (the emit frontier):")
        for reason, cnt in emit_hist.most_common():
            print(f"  {cnt:>5}  {reason}")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
        print(f"\nledger -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
