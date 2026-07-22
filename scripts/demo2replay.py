"""Convert a retired v4 JSONL demo into a dos_re 3.0 ReplayArtifact — once.

    python scripts/demo2replay.py DEMO.jsonl OUT_DIR [--from-snapshot DIR]
        [--role oracle|candidate] [--profile-id ID] [--implementation DESC]

dos_re 3.0 keeps no legacy replay reader, and neither does win16_re: the v4
format is dead, and recordings either convert once through this script or get
re-recorded.  The conversion is mechanical — each v4 record becomes one
timeline event on its Win16 channel at the next ordinal, carrying its live
instruction count as the ordinal's ``ReplayPointCoordinate`` — plus a base
continuation captured from the machine state the demo starts from (a fresh
boot, or ``--from-snapshot`` for anchored demos).

Capture-profile honesty: ``--role oracle`` is ONLY for demos recorded with the
untouched interpreter (play.py --no-hooks).  A demo recorded with islands
installed is a CANDIDATE capture; its timeline earns trust later through an
oracle validation, exactly as dos_re 3.0 defines trusted replays.  This script
cannot check how the demo was recorded, so the flag is an operator claim —
record it truthfully.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import EXE_PATH, assets_present, create_machine, resolve_demo
from dos_re.replay import ReplayExecutionIdentity, ReplayRecording
from win16.continuation import CONTINUATION_SCHEMA, capture_continuation
from win16.replay import (CLOCK_CHANNEL, DIALOG_CHANNEL,
                          GUEST_INSTRUCTION_COORDINATE, INPUT_CHANNEL,
                          MESSAGEBOX_CHANNEL, QUIT_CHANNEL, clock_payload,
                          dialog_payload, input_payload, messagebox_payload)

#: The Win16 game-observable comparison schema (vmsnap.digest's field set).
PROJECTION_SCHEMA = "win16-re-observable-v1"

_CHANNEL_FOR = {"i": INPUT_CHANNEL, "c": CLOCK_CHANNEL, "d": DIALOG_CHANNEL,
                "m": MESSAGEBOX_CHANNEL, "quit": QUIT_CHANNEL}


def _payload(rec: dict) -> dict:
    t = rec["t"]
    if t == "i":
        return input_payload(rec["v"])
    if t == "c":
        return clock_payload(rec["ms"])
    if t == "d":
        return dialog_payload(rec["dlg"], rec["v"])
    if t == "m":
        return messagebox_payload(rec["cap"], rec["r"])
    return {}                                              # quit


def convert(demo_path: Path, out_dir: Path, *, machine, role: str,
            profile_id: str, implementation: str) -> None:
    lines = demo_path.read_text(encoding="ascii").splitlines()
    header = json.loads(lines[0])
    if header.get("kind") != "win16-demo" or header.get("version", 0) != 4:
        raise SystemExit(f"{demo_path}: not a v4 win16 demo")
    records = [json.loads(ln) for ln in lines[1:] if ln.strip()]
    start_instr = int(header.get("instruction", 0))
    got = machine.cpu.instruction_count
    if got != start_instr:
        raise SystemExit(
            f"base state is at instruction {got:,} but the demo starts at "
            f"{start_instr:,} — wrong snapshot / not a fresh boot?")

    exe_sha = hashlib.sha256(EXE_PATH.read_bytes()).hexdigest()
    profile = ReplayExecutionIdentity(
        profile_id=profile_id, role=role,
        implementation=implementation,
        image=f"{header.get('exe', EXE_PATH.name)}:sha256:{exe_sha}",
        runtime="win16-re",
        devices="win16-api-surface",
        continuation_schema=CONTINUATION_SCHEMA,
        projection_schema=PROJECTION_SCHEMA,
    )
    base = capture_continuation(machine, event_cursor=0,
                                note=f"converted from {demo_path.name}")

    recording = ReplayRecording(
        out_dir, timeline_id=f"win16:{demo_path.stem}",
        profile=profile, base_state=base,
        metadata={"converted_from": demo_path.name,
                  "v4_snapshot_anchor": header.get("snapshot")})
    recording.mark(0, schema_id=GUEST_INSTRUCTION_COORDINATE, value=start_instr)
    ordinal = 0
    last_instr = start_instr
    for rec in records:
        ordinal += 1
        recording.add(ordinal, _CHANNEL_FOR[rec["t"]], _payload(rec))
        last_instr = int(rec["i"])
        recording.mark(ordinal, schema_id=GUEST_INSTRUCTION_COORDINATE,
                       value=last_instr)
    ordinal += 1
    recording.mark(ordinal, schema_id=GUEST_INSTRUCTION_COORDINATE,
                   value=last_instr)
    artifact = recording.finish(ordinal)
    print(f"[demo2replay] {demo_path.name}: {len(records)} records -> "
          f"{out_dir} (timeline {artifact.timeline_id}, "
          f"end ordinal {ordinal}, capture role {role})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo", help="demo NAME (in artifacts/demos/) or a path")
    ap.add_argument("out", help="output ReplayArtifact directory")
    ap.add_argument("--from-snapshot", metavar="DIR", default=None,
                    help="base state for an anchored demo")
    ap.add_argument("--role", choices=("oracle", "candidate"),
                    default="candidate",
                    help="capture-profile role; 'oracle' ONLY for --no-hooks "
                         "recordings (default: candidate)")
    ap.add_argument("--profile-id", default=None,
                    help="profile id (default: win16-<role>-<demo stem>)")
    ap.add_argument("--implementation", default=None,
                    help="capture composition descriptor (default derived "
                         "from --role)")
    args = ap.parse_args()
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    demo_path = Path(resolve_demo(args.demo))
    if args.from_snapshot:
        from win16.vmsnap import load_snapshot
        machine = load_snapshot(args.from_snapshot, create_machine)
    else:
        machine = create_machine()
    role = args.role
    implementation = args.implementation or (
        "win16-interpreted-cpu:no-hooks" if role == "oracle"
        else "win16-interpreted-cpu:recorded-composition")
    profile_id = args.profile_id or f"win16-{role}-{demo_path.stem}"
    convert(demo_path, Path(args.out), machine=machine, role=role,
            profile_id=profile_id, implementation=implementation)


if __name__ == "__main__":
    main()
