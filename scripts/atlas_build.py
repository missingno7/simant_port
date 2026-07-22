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
    ap.add_argument("--render-evidence", action="append", default=[],
                    metavar="FILE",
                    help="observed-evidence JSON from a CANDIDATE session "
                         "(replay_artifact.py --evidence-out): its observed "
                         "transfers are ingested as cited manual facts "
                         "(not oracle-trusted, but real evidence)")
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

    # Candidate-session observed evidence as CITED manual facts (a candidate
    # capture is not oracle-trusted, so it cannot go through ingest_replay,
    # but its observed transfers ARE real evidence — the resolved indirect
    # object-window draw dispatches + the confirmed callback entries).
    for ev_path in args.render_evidence:
        ev = json.loads(Path(ev_path).read_text(encoding="utf-8"))
        edges = [{"source": t["source_id"], "target": t["target_id"],
                  "kind": t["kind"], "status": "observed",
                  "observation_count": int(t.get("count", 1))}
                 for t in ev.get("transfers", ())]
        if edges:
            atlas.add_manual_facts(
                f"render-observed-{ev['session']}",
                provenance={"source": "replay_artifact.py --evidence-out "
                                      "(candidate composition, cited)",
                            "session": ev["session"],
                            "capture_role": ev.get("capture_role", "candidate"),
                            "recovery_ir_sha256": ev.get("recovery_ir_sha256")},
                edges=tuple(edges))
        print(f"[atlas] render evidence {Path(ev_path).name}: "
              f"{len(ev.get('visited_functions', ()))} visited, "
              f"{len(edges)} observed edges, "
              f"{len(ev.get('callback_entries', ()))} callback entries")

    # Containment for observed-only execution points: a replay-observed
    # dispatch TARGET that is not an IR function entry (a jump-table arm
    # interior to some function) has no static containment edge, so no
    # implementation would own it at plan time.  The Recovery IR knows the
    # exact instruction sets — attribute each such point to the function
    # whose decoded instructions include its address, as a cited fact.
    atlas.rematerialize()
    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    by_addr: dict[tuple[int, int], str] = {}
    for key, fn in ir["functions"].items():
        cs = int(key.split(":")[0], 16)
        for blk in fn["blocks"]:
            for i in blk["instructions"]:
                by_addr[(cs, int(i["ip"], 16))] = key
    from simant.execution import function_id
    edges = []
    for node in atlas.nodes(kind="execution-point"):
        if not node.metadata.get("observed_only"):
            continue
        address = node.identity.rsplit(":", 1)[-1]
        from urllib.parse import unquote
        cs, ip = unquote(address).split(":")
        owner_key = by_addr.get((int(cs, 16), int(ip, 16)))
        if owner_key is not None:
            ocs, oip = owner_key.split(":")
            edges.append({
                "source": function_id(int(ocs, 16), int(oip, 16)),
                "target": node.identity, "kind": "contains",
                "status": "containment"})
    if edges:
        atlas.add_manual_facts(
            "observed-point-containment",
            provenance={"source": "recovery_ir instruction sets (exact hit)",
                        "ir_sha256": hashlib.sha256(
                            Path(args.ir).read_bytes()).hexdigest()},
            edges=tuple(edges))
        print(f"[atlas] observed-point containment: {len(edges)} point(s) "
              f"attributed to their IR functions")

    # Cross-check: STATIC switch tables vs OBSERVED dispatch.  Every arm a
    # replay ever observed at a statically-annotated jmp_ind site must lie
    # inside that site's statically-read table — a contradiction means either
    # the table reader or the evidence probe is wrong, and the Atlas must not
    # be built on top of it.  (Fail loud, never fake.)
    from urllib.parse import unquote as _unq
    static_by_site: dict[str, set[str]] = {}
    for key, fn in ir["functions"].items():
        cs = key.split(":")[0].upper()
        for blk in fn.get("blocks", ()):
            for ins in blk["instructions"]:
                if ins.get("static_targets"):
                    static_by_site[f"{cs}:{ins['ip'].upper()}"] = {
                        t.upper() for t in ins["static_targets"]}
    checked = 0
    for edge in atlas.edges():
        if edge.kind != "jmp_ind" or edge.status != "observed":
            continue
        s_addr = _unq(edge.source.rsplit(":", 1)[-1]).upper()
        arms = static_by_site.get(s_addr)
        if arms is None:
            continue
        t_ip = _unq(edge.target.rsplit(":", 1)[-1]).upper().split(":")[-1]
        if t_ip not in arms:
            raise SystemExit(
                f"[atlas] static/observed CONTRADICTION at {s_addr}: observed "
                f"arm {t_ip} is not in the statically-read table {sorted(arms)}")
        checked += 1
    print(f"[atlas] static-table cross-check: {checked} observed arm(s) "
          f"confirmed inside their static tables")

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

    # The window-object map (scripts/winmap.py): typed opens-window /
    # draw-callback / event-routine facts, extracted by exact static
    # patterns and cross-checked against the observed dispatch above.
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "winmap.py"),
         "--ir", args.ir, "--atlas", str(atlas_dir), "--facts"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"[atlas] winmap facts FAILED:\n{proc.stdout}"
                         f"{proc.stderr}")
    for line in proc.stdout.splitlines():
        if line.startswith("[winmap]"):
            print(f"[atlas] {line}")
    # the subprocess wrote + rematerialized on disk; re-open for the report
    atlas = ExecutionAtlas.open(atlas_dir)

    coverage = atlas.coverage_for("development")
    print(f"[atlas] development coverage: {len(coverage.reachable)} "
          f"reachable, {len(coverage.unresolved_edges)} unresolved edges")
    print(f"[atlas] identity: {atlas.identity_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
