"""tickdemo — hook-config-INVARIANT demos for SimAnt (win16.tick_demo).

A tick demo keys input to the GAME TICK (SimAnt's ~59fps sim WM_TIMER), not the
instruction count, so ONE recording replays identically under ANY hook config —
the deterministic comparison v4 demos cannot give (their instruction anchor is
config-specific; see docs/run_status.md cont.27).

Three-pass workflow:

  convert   — replay an existing v4 demo (pure ASM, its native config) with the
              tick recorder attached; write NAME.tick (buckets + boundaries, no
              digests yet).
     python scripts/tickdemo.py convert cold_nohooks cold.tick

  canonize  — tick-replay NAME.tick (no hooks) recording the per-tick gameplay
              digest under the tick clock model; write the canonical demo.
     python scripts/tickdemo.py canonize cold.tick cold.ctick

  verify    — tick-replay the canonical demo, checking every tick's digest;
              --hooks runs it with the islands installed: a pass PROVES the
              hook config computes identical gameplay tick-for-tick; a failure
              names the first divergent tick.
     python scripts/tickdemo.py verify cold.ctick [--hooks] [--budget N]

Digest = win16.tick_demo.default_digest (full memory, transient stack window
zeroed) + the SimAnt don't-care scratch offsets in _SIMANT_ZERO below (adapter
knowledge, grown case-by-case with evidence in the journal).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from simant.runtime import create_machine, install_hooks  # noqa: E402
from win16.api.system import Win16System  # noqa: E402
from win16.demo import DemoDivergence, DemoDriver, DemoEnded  # noqa: E402
from simant.runtime import resolve_demo  # noqa: E402
from win16.tick_demo import (TickDemoDriver, TickDemoRecorder,  # noqa: E402
                             default_digest)

#: SimAnt don't-care scratch, as DGROUP offsets (converted to linear at machine
#: creation).  Grown only with evidence: each entry names its justification.
_SIMANT_ZERO_DGROUP: dict[int, str] = {
    0xB7D2: "_Unpack match_rem — mid-match scratch, dead unless resume==5 "
            "(island oracle + test_native exclude it; run_status cont.28)",
}


def _digest_fn(machine):
    dg = machine.seg_bases[10]
    lin = machine.mem._xlat(dg, 0)
    zero = [lin + off for off in _SIMANT_ZERO_DGROUP]
    return lambda m: default_digest(m, zero=zero)


def _run(machine, budget: int) -> str:
    cpu = machine.cpu
    cpu.trace_enabled = False
    try:
        while cpu.instruction_count < budget:
            cpu.run(500_000)
        return "budget reached"
    except DemoEnded as exc:
        return f"demo ended: {exc}"
    except DemoDivergence as exc:
        return f"DIVERGENCE: {exc}"


def cmd_convert(args) -> int:
    machine = create_machine()
    sysobj = Win16System(machine)
    v4 = DemoDriver(resolve_demo(args.v4demo))
    if v4.snapshot:
        raise SystemExit("convert needs a COLD-START v4 demo (snapshot-anchored "
                         "demos don't carry the boot state)")
    v4.install(sysobj)
    rec = TickDemoRecorder(args.out, machine.exe.path.name, ms0=0)
    sysobj.tick_recorder = rec
    t0 = time.perf_counter()
    status = _run(machine, args.budget)
    rec.close()
    print(f"convert: {status} after {machine.cpu.instruction_count:,} instrs "
          f"({time.perf_counter() - t0:.0f}s)")
    print(f"  {args.out}: {rec.bucket} ticks, {rec.records} records")
    return 0


def _tick_replay(path, *, hooks: bool, mode: str, budget: int):
    machine = create_machine()
    sysobj = Win16System(machine)
    n = install_hooks(machine) if hooks else 0
    driver = TickDemoDriver(path, digest_fn=_digest_fn(machine), mode=mode)
    driver.install(sysobj)
    t0 = time.perf_counter()
    status = _run(machine, budget)
    wall = time.perf_counter() - t0
    print(f"{mode}: hooks={n} -> {status} after "
          f"{machine.cpu.instruction_count:,} instrs ({wall:.0f}s); "
          f"ticks done {driver.bucket}/{driver.n_ticks}, "
          f"digests {'recorded' if mode == 'record' else 'checked'}: "
          f"{driver.ticks_checked}")
    return driver, status


def cmd_canonize(args) -> int:
    driver, status = _tick_replay(args.tickdemo, hooks=False, mode="record",
                                  budget=args.budget)
    if driver.bucket < driver.n_ticks:
        print(f"WARNING: only {driver.bucket}/{driver.n_ticks} ticks reached — "
              f"the canonical demo is truncated to what replayed")
        driver.boundaries = driver.boundaries[:driver.bucket]
    driver.save(args.out)
    print(f"  canonical demo -> {args.out}")
    return 0


def cmd_verify(args) -> int:
    driver, status = _tick_replay(args.tickdemo, hooks=args.hooks, mode="check",
                                  budget=args.budget)
    ok = driver.ticks_checked == driver.n_ticks and "DIVERGENCE" not in status
    print("VERIFIED: every tick's gameplay digest matches — this config "
          "computes identical gameplay" if ok else
          f"NOT verified ({driver.ticks_checked}/{driver.n_ticks} ticks): {status}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="v4 demo -> tick demo (no digests)")
    c.add_argument("v4demo"); c.add_argument("out")
    c.add_argument("--budget", type=int, default=400_000_000)
    c.set_defaults(fn=cmd_convert)
    k = sub.add_parser("canonize", help="record per-tick digests (no hooks)")
    k.add_argument("tickdemo"); k.add_argument("out")
    k.add_argument("--budget", type=int, default=400_000_000)
    k.set_defaults(fn=cmd_canonize)
    v = sub.add_parser("verify", help="check per-tick digests")
    v.add_argument("tickdemo")
    v.add_argument("--hooks", action="store_true",
                   help="install the islands (the cross-config proof)")
    v.add_argument("--budget", type=int, default=400_000_000)
    v.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
