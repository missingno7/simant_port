"""Replay a recorded demo headlessly — the deterministic evidence tool.

    python scripts/replay.py DEMO.jsonl [--budget N] [--png DIR] [--snapshot DIR]

Feeds the recorded message/dialog-event stream back into a fresh machine and
reports how far it got and the game-observable state digest.  A divergence
(machine asking for something the demo doesn't have next) raises loudly with
the record index.  This is the baseline every future hook/native replacement
must reproduce bit-exact.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import assets_present, create_machine, resolve_demo
from win16.demo import DemoDivergence, DemoDriver, DemoEnded
from win16.vmsnap import digest, save_snapshot


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a win16 demo headlessly.")
    ap.add_argument("demo", help="demo NAME (in artifacts/demos/) or a path, "
                                 "recorded by play.py --record-demo")
    ap.add_argument("--budget", type=int, default=200_000_000,
                    help="max instructions to execute")
    ap.add_argument("--png", metavar="DIR", default=None,
                    help="dump every window surface to DIR when done")
    ap.add_argument("--snapshot", metavar="DIR", default=None,
                    help="save a machine snapshot of the end state")
    ap.add_argument("--from-snapshot", metavar="DIR", default=None,
                    help="resume this snapshot before replaying (required when "
                         "the demo was recorded from one; the header names it)")
    ap.add_argument("--profile", default="development",
                    choices=("development", "detached"),
                    help="execution composition (dos_re 3.0): development = "
                         "the EXE under the interpreter (pure oracle); "
                         "detached = the EXE-free boot image with the "
                         "generated graph, wall armed by the plan")
    args = ap.parse_args()
    if args.profile == "development" and not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    driver = DemoDriver(resolve_demo(args.demo))
    anchor = f", anchored to snapshot {driver.snapshot}" if driver.snapshot else ""
    print(f"[replay] {args.demo}: {len(driver.records)} records "
          f"(exe {driver.exe}{anchor})")

    if driver.snapshot and not args.from_snapshot:
        raise SystemExit(
            f"this demo was recorded from snapshot {driver.snapshot!r} — "
            f"pass --from-snapshot <dir> pointing at it "
            f"(e.g. artifacts/snapshots/{driver.snapshot})")
    if args.profile == "detached":
        if args.from_snapshot:
            raise SystemExit("--profile detached boots only from the boot "
                             "image (no --from-snapshot)")
        import simant.vmless_boot as vb
        from dos_re.independence import exe_access_guard_from_manifest
        from simant.execution import boot_detached
        from win16.bootimage import load_boot_manifest
        manifest = load_boot_manifest(vb.BOOT_DIR)
        _guard = exe_access_guard_from_manifest(manifest)
        _guard.__enter__()                     # held for the whole session
        machine, manifest, plan = boot_detached(vb.BOOT_DIR)
        print(f"[replay] DETACHED plan {plan.plan_digest[:12]}: "
              f"EXE-free boot, interpreter wall armed")
    elif args.from_snapshot:
        from win16.vmsnap import load_snapshot
        machine = load_snapshot(args.from_snapshot, create_machine)
        got = machine.cpu.instruction_count
        if driver.instruction and got != driver.instruction:
            raise SystemExit(
                f"snapshot mismatch: demo was recorded from instruction "
                f"{driver.instruction:,} but {args.from_snapshot} restores to "
                f"{got:,} — wrong snapshot?")
        print(f"[replay] resumed {args.from_snapshot} (instruction {got:,})")
    else:
        machine = create_machine()
    machine.cpu.trace_enabled = False
    sysobj = machine.api.services["system"]
    driver.install(sysobj)          # injects input by instruction count; reproduces GetTickCount

    outcome = "budget exhausted"
    try:
        machine.cpu.run(args.budget)
    except DemoEnded as exc:
        outcome = f"demo ended: {exc}"
    except DemoDivergence as exc:
        print(f"[replay] DIVERGENCE: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001 — report and re-raise, fail loud
        print(f"[replay] VM stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"[replay] at event {driver._ei}/{len(driver._events)}, "
              f"instruction {machine.cpu.instruction_count}", file=sys.stderr)
        raise
    if machine.cpu.halted:
        outcome = "app exited cleanly"

    print(f"[replay] {outcome}")
    print(f"[replay] events consumed: {driver._ei}/{len(driver._events)}")
    print(f"[replay] instructions: {machine.cpu.instruction_count:,}")
    print(f"[replay] clock: {sysobj.clock_ms} ms, windows: "
          f"{[w.wndclass.name for w in sysobj.windows]}")
    print(f"[replay] digest: {digest(machine)}")

    if args.png:
        from win16.png import write_png
        out = Path(args.png)
        out.mkdir(parents=True, exist_ok=True)
        for i, win in enumerate(sysobj.windows):
            s = win.surface
            path = out / f"replay{i}_{win.wndclass.name}.png"
            write_png(path, s.w, s.h, bytes(s.pixels))
            print(f"[replay] wrote {path}")
    if args.snapshot:
        save_snapshot(machine, args.snapshot, note=f"end of demo {args.demo}")
        print(f"[replay] snapshot saved to {args.snapshot}")


if __name__ == "__main__":
    main()
