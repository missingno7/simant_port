"""Replay a Win16 ReplayArtifact headlessly — the 3.0 deterministic evidence
runner.

    python scripts/replay_artifact.py ARTIFACT_DIR [--budget N] [--png DIR]
        [--evidence [--ir artifacts/recovery_ir.json]]

Restores the artifact's base continuation (the machine state the recording
started from), installs the Win16 replay input driver (injection by
instruction count, GetTickCount reproduction), and runs the machine.  A
divergence raises loudly.  Reports the end instruction count and the
game-observable digest.

``--evidence`` additionally arms the Win16 evidence probe over the replay
(oracle captures only): function-entry visits + observed dynamic-dispatch
transfers, identity-keyed, persisted on the artifact via
``set_execution_evidence`` — the input to ``dos_re/tools/atlas.py
ingest-replay``.

TRANSITIONAL: the single plan-driven player (3.0 migration Phase 3) absorbs
this runner; it exists so converted timelines can be proven equivalent to
their v4 originals before the v4 reader is deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import EXE_PATH, assets_present, create_machine
from dos_re.identity import ImageIdentity, ProgramIdentity
from dos_re.replay import ReplayArtifact, ReplayPoint
from win16.continuation import apply_continuation
from win16.evidence import (Win16EvidenceProbe, dispatch_sites, entry_set,
                            finish as finish_evidence)
from win16.replay import ReplayExhausted, ReplayDivergence, input_driver_for
from win16.vmsnap import digest

#: The SimAnt program/image identity (the game-side constants every Atlas
#: artifact keys by — must match the ingest-ir invocation).
PROGRAM_KEY = "simant:1.0"
ADDRESS_SPACE = "win16-para"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Replay a Win16 ReplayArtifact headlessly.")
    ap.add_argument("artifact", help="ReplayArtifact directory")
    ap.add_argument("--budget", type=int, default=600_000_000,
                    help="max instructions to execute")
    ap.add_argument("--png", metavar="DIR", default=None,
                    help="dump every window surface to DIR when done")
    ap.add_argument("--evidence", action="store_true",
                    help="record oracle execution evidence (visits + observed "
                         "dispatch transfers) onto the artifact")
    ap.add_argument("--ir",
                    default=str(REPO_ROOT / "artifacts" / "recovery_ir.json"),
                    help="Recovery IR supplying the entry/site sets for "
                         "--evidence")
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

    probe = site_kinds = ir_sha = None
    if args.evidence:
        if capture.role != "oracle":
            raise SystemExit(
                "--evidence requires an oracle capture profile: execution "
                "evidence must be recorded by an oracle "
                "(this artifact's capture role is "
                f"{capture.role!r})")
        ir_raw = Path(args.ir).read_bytes()
        ir_sha = hashlib.sha256(ir_raw).hexdigest()
        ir = json.loads(ir_raw)
        site_kinds = dispatch_sites(ir)
        probe = Win16EvidenceProbe(
            entry_set(ir), frozenset(site_kinds),
            lambda: driver.current_ordinal)
        machine.cpu.coverage_telemetry = probe
        print(f"[replay] evidence probe armed: {len(probe.entries)} entries, "
              f"{len(site_kinds)} dispatch sites (IR sha256 {ir_sha[:16]})")

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

    if probe is not None:
        exe_sha = hashlib.sha256(EXE_PATH.read_bytes()).hexdigest()
        image = ImageIdentity(ProgramIdentity(PROGRAM_KEY), EXE_PATH.name,
                              "sha256", exe_sha)
        evidence, visits = finish_evidence(
            probe, image=image, address_space=ADDRESS_SPACE,
            timeline_id=artifact.timeline_id, profile=capture,
            site_kinds=site_kinds,
            provenance={"observer": "win16-evidence-probe/v1",
                        "recovery_ir_sha256": ir_sha,
                        "runner": "scripts/replay_artifact.py"})
        changed = artifact.set_execution_evidence(capture, evidence,
                                                  visits=visits)
        fired = len(evidence.transfers)
        print(f"[replay] evidence: {len(visits.records())} visited functions, "
              f"{fired} observed transfer edges "
              f"({'persisted' if changed else 'identical to stored'})")


if __name__ == "__main__":
    main()
