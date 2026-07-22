"""Plan a SimAnt execution composition — dos_re 3.0 `plan_execution` CLI.

    python scripts/plan.py --profile development [--override islands]
        [--prefer cpuless-corpus] [--verbose]

Builds ProgramCoverage from the Execution Atlas (artifacts/atlas — rebuild it
with dos_re/tools/atlas.py if absent), declares the implementation catalog
(simant/execution.py), and resolves an immutable ExecutionPlan + its
DetachmentReport.  ``--plan-only`` semantics: this script never constructs a
runtime; a strict profile that cannot be satisfied fails HERE, before any
machine exists.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import assets_present, create_machine
from simant import execution as sx
from dos_re.atlas import ExecutionAtlas
from dos_re.execution import ExecutionPlanError, format_execution_plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default="development",
                    choices=("development", "verification", "detached",
                             "release"))
    ap.add_argument("--atlas", default=str(REPO_ROOT / "artifacts" / "atlas"))
    ap.add_argument("--override", action="append", default=[],
                    help="authored implementation to select (e.g. islands)")
    ap.add_argument("--prefer", action="append", default=[],
                    help="provider preference order, first wins")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    atlas = ExecutionAtlas.open(args.atlas)
    seg_bases = create_machine().seg_bases
    preference = tuple(args.prefer) + ("interpreted-baseline",)

    try:
        plan = sx.plan(args.profile, atlas, seg_bases,
                       selected_overrides=tuple(args.override),
                       provider_preference=preference)
    except ExecutionPlanError as exc:
        print(f"[plan] {args.profile}: UNSATISFIABLE")
        for line in exc.report.failure_lines():
            print(f"[plan]   {line}")
        return 2

    print(format_execution_plan(plan, verbose=args.verbose))
    report = plan.report
    by_impl: dict[str, int] = {}
    for binding in plan.bindings:
        by_impl[binding.implementation_id] = \
            by_impl.get(binding.implementation_id, 0) + 1
    print(f"[plan] profile={args.profile} digest={plan.plan_digest[:16]}")
    print(f"[plan] reachable={len(report.reachable)} bindings="
          f"{len(plan.bindings)} " + " ".join(
          f"{k}={v}" for k, v in sorted(by_impl.items())))
    print(f"[plan] active boundaries={len(report.active_boundaries)} "
          f"collapsed edges={report.collapsed_edge_count}")
    counts = report.closure_counts()
    if counts:
        print("[plan] closure findings: " + " ".join(
            f"{kind}={n}" for kind, n in counts))
    for line in report.closure_warning_lines():
        print(f"[plan] WARN {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
