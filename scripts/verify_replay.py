"""Verify a replay interval: the interpreted ORACLE vs the detached CANDIDATE.

    python scripts/verify_replay.py ARTIFACT_DIR

dos_re 3.0 `verify_interval` over the full timeline of a Win16
ReplayArtifact: restore both profiles at the base, replay both to the end,
compare their CanonicalState projections through the shared declared
contract, and persist the result on the artifact as a scoped
ReplayValidation.  A pass makes a candidate-relevant claim for exactly this
event stream — never universal correctness; a fail persists the divergence.

The oracle profile is the artifact's capture profile (its base is embedded);
the candidate is the plan-bound DETACHED composition (EXE-free boot image +
generated graph, interpreter wall armed), registered here with its OWN
profile-local base — the event timeline is portable across compositions,
continuation state is not.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import assets_present, create_machine
import simant.vmless_boot as vb
from simant.execution import boot_detached
from dos_re.replay import (ReplayArtifact, ReplayExecutionIdentity,
                           ReplayPoint, verify_interval)
from win16.bootimage import load_boot_manifest, mask_ranges_from_manifest
from win16.continuation import CONTINUATION_SCHEMA, capture_continuation
from win16.replay_driver import PROJECTION_SCHEMA, Win16ReplayDriver


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artifact", help="ReplayArtifact directory")
    args = ap.parse_args()
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found (oracle boot)")

    artifact = ReplayArtifact.open(Path(args.artifact))
    oracle_profile = artifact.capture_profile()
    if oracle_profile.role != "oracle":
        raise SystemExit("the artifact's capture profile is not an oracle")

    manifest = load_boot_manifest(vb.BOOT_DIR)
    mask = mask_ranges_from_manifest(manifest)

    oracle = Win16ReplayDriver(
        profile=oracle_profile, machine_factory=create_machine,
        mask_ranges=mask)

    def candidate_factory():
        machine, _manifest, _plan = boot_detached(vb.BOOT_DIR)
        return machine

    # The candidate's identity: same image/timeline, its own composition.
    seed, _m, plan = boot_detached(vb.BOOT_DIR)
    candidate_profile = ReplayExecutionIdentity(
        profile_id="win16-detached-vmless",
        role="candidate",
        implementation=f"win16-generated-vmless:{plan.plan_digest[:16]}",
        image=oracle_profile.image,
        runtime="win16-re",
        devices="win16-api-surface",
        continuation_schema=CONTINUATION_SCHEMA,
        projection_schema=PROJECTION_SCHEMA,
    )
    base_point = ReplayPoint(0, artifact.timeline_id)
    if candidate_profile not in [p for p, _ in artifact.profiles()]:
        artifact.register_profile(
            candidate_profile, base_point=base_point,
            base_state=capture_continuation(seed, event_cursor=0))
        print(f"[verify] candidate profile registered with its own "
              f"boot-image base")
    candidate = Win16ReplayDriver(
        profile=candidate_profile, machine_factory=candidate_factory,
        mask_ranges=mask)

    start = base_point
    end = artifact.end_point
    print(f"[verify] interval {start.ordinal} -> {end.ordinal} "
          f"({len(artifact.events)} events) — oracle vs "
          f"{candidate_profile.profile_id}")
    result = verify_interval(artifact, oracle, candidate, start, end)
    cmp = result.comparison
    print(f"[verify] equivalent: {result.equivalent}")
    print(f"[verify] oracle digest:    {cmp.oracle_digest[:16]}")
    print(f"[verify] candidate digest: {cmp.candidate_digest[:16]}")
    if not result.equivalent:
        for diff in cmp.differences[:10]:
            print(f"[verify]   {diff}")
        return 2
    print(f"[verify] validation persisted; artifact validations: "
          f"{len(artifact.validations())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
