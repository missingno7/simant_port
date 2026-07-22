"""Replay a Win16 ReplayArtifact headlessly — the 3.0 deterministic evidence
runner.

    python scripts/replay_artifact.py ARTIFACT_DIR [--budget N] [--png DIR]

Restores the artifact's base continuation (the machine state the recording
started from), installs the Win16 replay input driver (injection by
instruction count, GetTickCount reproduction), and runs the machine.  A
divergence raises loudly.  Reports the end instruction count and the
game-observable digest.

TRANSITIONAL: the single plan-driven player (3.0 migration Phase 3) absorbs
this runner; it exists so converted timelines can be proven equivalent to
their v4 originals before the v4 reader is deleted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import assets_present, create_machine
from dos_re.replay import ReplayArtifact, ReplayPoint
from win16.continuation import apply_continuation
from win16.replay import ReplayExhausted, ReplayDivergence, input_driver_for
from win16.vmsnap import digest


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Replay a Win16 ReplayArtifact headlessly.")
    ap.add_argument("artifact", help="ReplayArtifact directory")
    ap.add_argument("--budget", type=int, default=600_000_000,
                    help="max instructions to execute")
    ap.add_argument("--png", metavar="DIR", default=None,
                    help="dump every window surface to DIR when done")
    args = ap.parse_args()
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    artifact = ReplayArtifact.open(Path(args.artifact))
    capture = artifact.capture_profile()
    print(f"[replay] {args.artifact}: timeline {artifact.timeline_id}, "
          f"{len(artifact.events)} events, capture role {capture.role}")

    machine = create_machine()
    base = artifact.restore(capture, ReplayPoint(0, artifact.timeline_id))
    apply_continuation(machine, base)
    machine.cpu.trace_enabled = False
    sysobj = machine.api.services["system"]

    driver = input_driver_for(artifact)
    driver.install(sysobj)

    outcome = "budget exhausted"
    try:
        machine.cpu.run(args.budget)
    except ReplayExhausted as exc:
        outcome = f"timeline ended: {exc}"
    except ReplayDivergence as exc:
        print(f"[replay] DIVERGENCE: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001 — report and re-raise, fail loud
        print(f"[replay] VM stopped: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        print(f"[replay] at ordinal {driver.current_ordinal}, instruction "
              f"{machine.cpu.instruction_count}", file=sys.stderr)
        raise
    if machine.cpu.halted:
        outcome = "app exited cleanly"

    print(f"[replay] {outcome}")
    arrivals = len(driver._events)
    print(f"[replay] arrivals applied: {driver._ei}/{arrivals} "
          f"(timeline events applied: {driver.current_ordinal})")
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


if __name__ == "__main__":
    main()
