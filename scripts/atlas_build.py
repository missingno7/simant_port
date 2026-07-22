"""Build the SIMANTW Execution Atlas from scratch — one command, regenerable.

    python scripts/atlas_build.py [--atlas artifacts/atlas]
        [--ir artifacts/recovery_ir.json] [--replay DIR]...

Deletes and recreates the Atlas directory: creates it for ``simant:1.0``,
ingests the Recovery IR under the ``win16-para`` address space, ingests every
given replay artifact's oracle execution evidence, and sets the development
product roots to ``__astart`` PLUS every observed host->guest callback entry
(WndProcs, dialog procs, timer procs) — the Win16 fact that a message-pump
program has many entry points, only one of which is in the NE header.

The Atlas is a disposable projection: delete it and this script reproduces
it from the same sources.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import EXE_PATH, assets_present
from simant.execution import ADDRESS_SPACE, PROGRAM_KEY, image_identity, program
from dos_re.atlas import ExecutionAtlas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--atlas", default=str(REPO_ROOT / "artifacts" / "atlas"))
    ap.add_argument("--ir",
                    default=str(REPO_ROOT / "artifacts" / "recovery_ir.json"))
    ap.add_argument("--replay", action="append", default=[],
                    help="replay artifact dir(s) with oracle evidence "
                         "(default: artifacts/replays/cold_nohooks if present)")
    args = ap.parse_args()
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    replays = [Path(p) for p in args.replay]
    if not replays:
        default = REPO_ROOT / "artifacts" / "replays" / "cold_nohooks"
        if default.is_dir():
            replays = [default]

    atlas_dir = Path(args.atlas)
    if atlas_dir.exists():
        shutil.rmtree(atlas_dir)

    atlas = ExecutionAtlas.create(atlas_dir, program=program())
    atlas.import_recovery_ir(args.ir, image=image_identity(),
                             address_space=ADDRESS_SPACE)
    print(f"[atlas] IR ingested from {args.ir}")

    for replay in replays:
        report = atlas.ingest_replay_with_report(replay)
        value = report.to_json()
        print(f"[atlas] replay {replay.name}: "
              f"{value['visited_function_count']} visited, "
              f"{value['observed_edge_count']} observed edges "
              f"(+{len(report.new_node_ids)} nodes)")

    # Roots: the NE entry plus every observed callback entry.  A Win16
    # program is message-driven — its WndProcs are entry points the OS calls;
    # conservative coverage without them cannot reach anything dispatched
    # through the message pump.
    entry = atlas.resolve("__astart").identity
    observed_callbacks = {
        edge.target for edge in atlas.edges()
        if edge.kind == "callback" and edge.status == "observed"}
    # Only targets that exist as function nodes become roots (a callback into
    # a non-entry address stays an execution point, not a root).
    known = {n.identity for n in atlas.nodes(kind="function")}
    callback_roots = sorted(observed_callbacks & known - {entry})
    atlas.set_product_roots("development", [entry] + callback_roots)
    atlas.rematerialize()
    print(f"[atlas] roots: __astart + {len(callback_roots)} callback entries")

    coverage = atlas.coverage_for("development")
    print(f"[atlas] development coverage: {len(coverage.reachable)} "
          f"reachable, {len(coverage.unresolved_edges)} unresolved edges")
    print(f"[atlas] identity: {atlas.identity_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
