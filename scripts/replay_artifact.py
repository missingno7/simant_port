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
                    help="record execution evidence (visits + observed "
                         "dispatch/callback transfers) — persisted on the "
                         "artifact for an oracle capture, written to "
                         "--evidence-out for any capture")
    ap.add_argument("--evidence-out", metavar="FILE", default=None,
                    help="write the observed evidence as identity-keyed "
                         "manual-fact JSON (atlas_build --render-evidence "
                         "ingests it); valid for a candidate capture, which "
                         "is not oracle-trusted but whose observed edges are "
                         "still cited evidence")
    ap.add_argument("--hooks", action="store_true",
                    help="install the development-plan islands after restore "
                         "— REQUIRED to faithfully replay a candidate session "
                         "recorded with hooks on (play.py's default)")
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

    if args.hooks:
        # The islands are host wiring (replacement_hooks), NOT part of the
        # restored continuation, so they must be re-installed to reproduce a
        # hooks-on candidate recording (else the instruction timeline diverges
        # and input injects at the wrong points).
        from dos_re.execution import bind_plan_implementations
        from simant.execution import INTERPRETED_CARRIER, development_plan
        plan = development_plan(machine)
        bind_plan_implementations(machine, plan, carrier_id=INTERPRETED_CARRIER)
        n = sum(1 for b in plan.bindings if b.implementation_id == "islands")
        print(f"[replay] composition: {n} island hook(s) installed "
              f"(matching a hooks-on candidate recording)")

    driver = input_driver_for(artifact)
    driver.install(sysobj)

    probe = site_kinds = ir_sha = None
    if args.evidence:
        if capture.role != "oracle" and not args.evidence_out:
            raise SystemExit(
                "--evidence on a non-oracle capture must write to "
                "--evidence-out FILE (a candidate's observed edges are cited "
                "manual-fact evidence, not oracle-trusted replay evidence). "
                f"This capture role is {capture.role!r}.")
        ir_raw = Path(args.ir).read_bytes()
        ir_sha = hashlib.sha256(ir_raw).hexdigest()
        ir = json.loads(ir_raw)
        site_kinds = dispatch_sites(ir)
        probe = Win16EvidenceProbe(
            entry_set(ir), frozenset(site_kinds),
            lambda: driver.current_ordinal)
        machine.cpu.coverage_telemetry = probe
        machine.cpu.win16_callback_observer = probe.record_callback
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
        evidence, visits, callback_entries = finish_evidence(
            probe, image=image, address_space=ADDRESS_SPACE,
            timeline_id=artifact.timeline_id, profile=capture,
            site_kinds=site_kinds,
            provenance={"observer": "win16-evidence-probe/v2",
                        "recovery_ir_sha256": ir_sha,
                        "runner": "scripts/replay_artifact.py"})
        fired = len(evidence.transfers)
        print(f"[replay] evidence: {len(visits.records())} visited functions, "
              f"{fired} observed transfer edges, "
              f"{len(callback_entries)} callback entry roots")
        if capture.role == "oracle" and not args.evidence_out:
            changed = artifact.set_execution_evidence(capture, evidence,
                                                      visits=visits)
            print(f"[replay] oracle evidence "
                  f"{'persisted' if changed else 'identical to stored'} "
                  f"on the artifact")
        if args.evidence_out:
            # Identity-keyed observed facts for atlas_build --render-evidence:
            # visits become observed function nodes, transfers become observed
            # edges (the resolved indirect dispatches + callbacks) — cited to
            # this candidate session, not claimed oracle-trusted.
            out = Path(args.evidence_out)
            out.write_text(json.dumps({
                "_notice": "GENERATED by replay_artifact.py --evidence-out. "
                           "Observed under a CANDIDATE composition; cited "
                           "manual-fact evidence, not oracle-trusted.",
                "session": Path(args.artifact).name,
                "capture_role": capture.role,
                "recovery_ir_sha256": ir_sha,
                "visited_functions": [v.function_id for v in visits.records()],
                "transfers": [t.to_json() for t in evidence.transfers],
                "callback_entries": list(callback_entries),
            }, indent=1), encoding="utf-8")
            print(f"[replay] observed evidence written to {out}")


if __name__ == "__main__":
    main()
