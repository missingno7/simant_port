"""cpuless_wall_gap -- the CPUless wall-gap map over the RUNTIME-reachable
closure (task #44/#46 measurement).

Given the runtime function-entry trace (scripts/entry_probe.py -> observed.json)
and the promotion censuses, classify EVERY function the game actually runs as
one of the four wall buckets, so the gap between the 234-function byte-exact
graph and a CLOSED play_cpuless wall is exact, not estimated:

  cpuless-promoted   a pure auto-promoted body already exists
                     (simant/native/cpuless, the byte-exact graph)
  manual-adapter     a hand-recovered pure body (simant/recovered) reached via
                     a generated CPU-ABI adapter -- the BODY is CPUless, but the
                     adapter marshals cpu-state (touches the carrier), so for the
                     WALL it must be composed DIRECTLY as a CPUless callee
  literal-lift       not CPUless: the real work-list, counted per strict-gate
                     REFUSAL reason
  blocked            x87 / indirect / platform capability gap (census blocked
                     tier), sub-split so the x87-in-closure question is definitive

The reachable closure is the OBSERVED set (every function whose entry executed);
static near-call reachability is reported alongside as a cross-check.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

A = REPO_ROOT / "artifacts"
X87_OPS = {f"unanalyzed-opcode-{h}" for h in ("9B", "D8", "D9", "DA", "DB",
                                              "DC", "DD", "DE", "DF")}


def para_of_seg(ir: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for entry, rec in ir["functions"].items():
        out.setdefault(rec["ne_seg"], entry.split(":")[0])
    return out


def manual_para_keys(map_doc: dict, seg_base: dict[int, str]) -> set[str]:
    out: set[str] = set()
    for f in map_doc["functions"]:
        k = f.get("key")
        if not k:
            continue
        seg_s, off = k.split(":")
        base = seg_base.get(int(seg_s))
        if base:
            out.add(f"{base}:{off}".upper())
    return out


def blocked_reason(refusals: dict) -> str:
    """The dominant capability gap for a blocked function."""
    keys = set(refusals)
    if keys & X87_OPS:
        return "x87"
    if "indirect-or-far-transfer" in keys:
        return "indirect-or-far-transfer"
    if "ir-not-liftable" in keys:
        return "ir-not-liftable"
    for k in keys:
        if k.startswith("unanalyzed-opcode-"):
            return k
    return "other"


def main() -> int:
    ir = json.loads((A / "recovery_ir.json").read_text(encoding="utf-8"))
    obs = json.loads((A / "observed.json").read_text(encoding="utf-8"))
    census = json.loads((A / "cpuless_census.json").read_text(encoding="utf-8"))
    pcensus = json.loads((A / "cpuless_promote_census.json").read_text(encoding="utf-8"))
    rmap = json.loads((REPO_ROOT / "simant" / "facts" / "recovered_map.json"
                       ).read_text(encoding="utf-8"))

    funcs = ir["functions"]
    observed = {a.upper() for a in obs["executed"]}
    seg_base = para_of_seg(ir)
    manual = manual_para_keys(rmap, seg_base) & set(k.upper() for k in funcs)
    #: the manual entries that ACTUALLY compose carrier-free today: adaptgen's
    #: mechanically-closed contracts (scripts/overridegen.py --dry-run output).
    #: Absent -> the model falls back to the optimistic "all manual" ceiling and
    #: says so, rather than silently reporting an unreachable number.
    ovp = A / "overrides.json"
    manual_routed: set[str] = set()
    if ovp.is_file():
        manual_routed = {k.upper() for k in
                         json.loads(ovp.read_text(encoding="utf-8"))["overrides"]}

    promoted = {k.upper() for k in pcensus["promotable"]}
    refused_reason: dict[str, str] = {}
    for reason, keys in pcensus["refused"].items():
        for k in keys:
            refused_reason[k.upper()] = reason
    blocked = {k.upper() for k in census["tiers"]["blocked"]}
    cfns = {k.upper(): v for k, v in census["functions"].items()}
    symbols = {k.upper(): (v.get("symbol") or "") for k, v in funcs.items()}

    def classify(key: str) -> tuple[str, str]:
        """(bucket, reason)."""
        key = key.upper()
        if key in promoted:
            return "cpuless-promoted", ""
        if key in manual:
            return "manual-adapter", ""
        if key in blocked:
            return "blocked", blocked_reason(cfns.get(key, {}).get("refusals", {}))
        if key in refused_reason:
            return "literal-lift", refused_reason[key]
        return "literal-lift", "unclassified"

    # ---- static near-call closure (cross-check) ---------------------------
    # walk near + static far (9A) edges from the observed set; the tool's model.
    reached = set(observed)
    work = list(observed)
    while work:
        k = work.pop()
        rec = funcs.get(k)
        if not rec:
            continue
        cs = int(k.split(":")[0], 16)
        for t in (rec.get("calls_near") or []):
            nk = f"{cs:04X}:{int(t, 16):04X}"
            if nk in funcs and nk not in reached:
                reached.add(nk)
                work.append(nk)
    static_only = reached - observed

    # ---- classify the runtime-reachable closure ---------------------------
    buckets: dict[str, list[str]] = {}
    reasons: Counter = Counter()
    blocked_sub: Counter = Counter()
    per_func: dict[str, dict] = {}
    for key in sorted(observed):
        bucket, reason = classify(key)
        buckets.setdefault(bucket, []).append(key)
        per_func[key] = {"bucket": bucket, "reason": reason,
                         "symbol": symbols.get(key, "")}
        if bucket == "literal-lift":
            reasons[reason] += 1
        if bucket == "blocked":
            blocked_sub[reason] += 1

    # x87-in-closure: the definitive question.
    x87_reached = sorted(k for k in observed
                         if k in blocked
                         and blocked_reason(cfns.get(k, {}).get("refusals", {})) == "x87")
    indirect_reached = sorted(k for k in observed
                              if k in blocked
                              and "indirect-or-far-transfer"
                              in cfns.get(k, {}).get("refusals", {}))

    # ---- report ------------------------------------------------------------
    def line(s=""):
        print(s)

    line("=" * 72)
    line("CPUless WALL-GAP MAP -- runtime-reachable closure (cold_nohooks)")
    line("=" * 72)
    line(f"total IR functions:              {len(funcs)}")
    line(f"runtime-reachable (executed):    {len(observed)}   <- the closure")
    line(f"static near-call extension:      +{len(static_only)} "
         f"(reachable via an untaken near-call, never ran)")
    line(f"static closure (observed+ext):   {len(reached)}")
    line("")
    line("classification of the RUNTIME-reachable closure:")
    order = ["cpuless-promoted", "manual-adapter", "literal-lift", "blocked"]
    for b in order:
        line(f"  {b:18} {len(buckets.get(b, [])):4d}")
    line("")
    line("literal-lift work-list, by strict-gate REFUSAL reason:")
    for reason, n in reasons.most_common():
        line(f"    {reason:<42} {n:4d}")
    line("")
    line("blocked, by capability gap:")
    for reason, n in blocked_sub.most_common():
        line(f"    {reason:<42} {n:4d}")
    line("")
    line("x87 IN THE REACHABLE CLOSURE?  "
         f"{'YES' if x87_reached else 'NO'}  ({len(x87_reached)} function(s))")
    for k in x87_reached:
        line(f"    {k}  {symbols.get(k, '')}")
    line(f"indirect-transfer blocked, reachable: {len(indirect_reached)}")
    for k in indirect_reached:
        line(f"    {k}  {symbols.get(k, '')}")

    # ---- rung plan: bottom-up cascade under each capability ---------------
    # A calls-only function refuses `contains-call` when a game-callee lacks a
    # CPUless contract; it composes bottom-up once every callee is composable
    # AND its own body is gate-clean.  Model composability as a fixpoint over
    # the reachable closure and measure the MARGINAL functions each capability
    # unblocks (a capability makes a class of BODIES clean; the cascade then
    # promotes every contains-call caller whose callees are now all composable).
    FRAME = {"leave-without-enter", "frame-pointer-pop-without-save",
             "frame-restore-without-establish", "frame-pointer-clobbered"}
    CFLOW = {"tail-dispatch-at-nonzero-depth",
             "tail-dispatch-with-unbalanced-stack", "mixed-return-kinds",
             "ret-n-stack-args (retf N needs far variant)"}
    SPDATA = {"sp-as-data"}
    BOUND = {"boundary-or-dispatch-address"}

    def game_callees(key: str) -> set[str]:
        rec = funcs.get(key)
        if not rec:
            return set()
        cs = key.split(":")[0]
        out = set()
        for t in (rec.get("calls_near") or []):
            nk = f"{int(cs, 16):04X}:{int(t, 16):04X}"
            if nk in funcs:
                out.add(nk)
        for pair in (rec.get("calls_far") or []):
            seg, off = pair
            if int(seg, 16) == 0x0060:          # platform thunk -> plat.farcall
                continue
            fk = f"{int(seg, 16):04X}:{int(off, 16):04X}"
            if fk in funcs:
                out.add(fk)
        return out

    def body_clean(key: str, caps: set[str]) -> bool:
        """The function's OWN body passes (given the enabled capabilities);
        only its callees may still block it (contains-call)."""
        if key in promoted:
            return True
        if key in manual:
            # A hand-recovered body composes carrier-free ONLY as an override,
            # and only the entries adaptgen's classifier mechanically closes are
            # routable (cont.247).  Splitting the capability keeps the rung plan
            # honest: "manual-routed" is what EXISTS today, "manual" is the
            # optimistic ceiling once every remaining contract is closed.
            if key in manual_routed:
                return "manual-routed" in caps or "manual" in caps
            return "manual" in caps
        if key in blocked:
            r = blocked_reason(cfns.get(key, {}).get("refusals", {}))
            if r == "x87":
                return "x87" in caps
            if r == "indirect-or-far-transfer":
                return "indirect" in caps
            return False                        # ir-not-liftable / opcode gap
        reason = refused_reason.get(key)
        if reason == "contains-call":
            return True                         # body clean; callee-gated only
        if reason in FRAME:
            return "frame" in caps
        if reason in CFLOW:
            return "cflow" in caps
        if reason in SPDATA:
            return "sp-as-data" in caps
        if reason in BOUND:
            return "boundary" in caps
        return False

    def composable_fixpoint(caps: set[str]) -> set[str]:
        # The --observed wall model: a call whose target was NEVER executed is
        # a runtime-dead call and becomes a FAIL-LOUD stub (not a blocker), so
        # a caller is gated only by the callees it ACTUALLY reached at runtime.
        comp = {k for k in observed if k in promoted}
        changed = True
        while changed:
            changed = False
            for k in observed:
                if k in comp or not body_clean(k, caps):
                    continue
                if (game_callees(k) & observed) <= comp:
                    comp.add(k); changed = True
        return comp

    scenarios = [
        ("today (auto only)", set()),
        ("+manual direct-compose (routed)", {"manual-routed"}),
        ("+manual direct-compose (all)", {"manual"}),
        ("+frame-shape emitter", {"manual", "frame"}),
        ("+control-flow-shape emitter", {"manual", "frame", "cflow"}),
        ("+sp-as-data", {"manual", "frame", "cflow", "sp-as-data"}),
        ("+boundary/dispatch entry", {"manual", "frame", "cflow", "sp-as-data",
                                      "boundary"}),
        ("+indirect/dispatch composition",
         {"manual", "frame", "cflow", "sp-as-data", "boundary", "indirect"}),
        ("+x87 (deferred; 0 reachable)",
         {"manual", "frame", "cflow", "sp-as-data", "boundary", "indirect",
          "x87"}),
    ]
    line("")
    line("=" * 72)
    line("RUNG PLAN -- bottom-up cascade over the reachable closure")
    line("=" * 72)
    line(f"{'capability added':<34}{'composable':>11}{'marginal':>10}")
    prev = 0
    cascade: list[dict] = []
    for label, caps in scenarios:
        comp = composable_fixpoint(caps)
        n = len(comp)
        line(f"{label:<34}{n:>11}{n - prev:>+10}")
        cascade.append({"capability": label, "composable": n,
                        "marginal": n - prev})
        prev = n
    # The final rung: the message-pump / dispatch recursive CLUSTER.  The linear
    # fixpoint above cannot break call cycles (A->B->A), but the dos_re promoter
    # composes a mutually-recursive dispatch cluster ATOMICALLY (the
    # self-recursion contract injection + dispatch-cluster promotion).  Any
    # residual function that is itself body-clean AND blocked ONLY by other
    # residual functions is part of one such cluster -> it composes with the
    # cluster capability.  (Verified: 0 residual bottoms out in a genuinely
    # unclean callee, so the cluster closes the whole remainder.)
    all_caps = {"manual", "manual-routed", "frame", "cflow", "sp-as-data",
                "boundary", "indirect", "x87"}
    linear = composable_fixpoint(all_caps)
    residual = observed - linear
    cluster_ok = all(body_clean(k, all_caps)
                     and ((game_callees(k) & observed) - linear) <= residual
                     for k in residual)
    closed = len(observed) if cluster_ok else len(linear)
    line(f"{'+dispatch-cluster (message-pump)':<34}{closed:>11}"
         f"{closed - prev:>+10}"
         + ("   (atomic recursive cluster)" if cluster_ok else ""))
    cascade.append({"capability": "+dispatch-cluster (message-pump)",
                    "composable": closed, "marginal": closed - prev})
    line(f"{'closure target (reachable)':<34}{len(observed):>11}")
    remaining = [] if cluster_ok else sorted(residual)
    line(f"residual recursive cluster (atomic): {len(residual)}  "
         f"-> {'CLOSES' if cluster_ok else 'does NOT close'} the wall")
    # WHY it does not close: the cluster is atomic, so a SINGLE unclean body in
    # it blocks all of it.  Naming those bodies is the actionable frontier --
    # without it "does NOT close" is a number with no next step.
    cluster_blockers = {k: (refused_reason.get(k)
                            or blocked_reason(cfns.get(k, {}).get("refusals", {})))
                        for k in sorted(residual) if not body_clean(k, all_caps)}
    if cluster_blockers:
        line(f"  cluster blocked by {len(cluster_blockers)} unclean "
             f"body/bodies (atomic -> all {len(residual)} stay out):")
        for k, why in cluster_blockers.items():
            line(f"    {k}  {symbols.get(k, '')}  [{why}]")
    line(f"NOT composable even with every capability + cluster: {len(remaining)}")
    for k in remaining[:20]:
        b, r = classify(k)
        line(f"    {k}  {symbols.get(k, '')}  [{b}:{r}]")

    out = {
        "_notice": "GENERATED by scripts/cpuless_wall_gap.py. Disposable.",
        "rung_plan": cascade,
        "manual_total": len(manual & observed),
        "manual_routed_today": len(manual_routed & observed),
        "cluster_blockers": cluster_blockers,
        "not_composable_all_caps": remaining,
        "demo": obs.get("demo"),
        "final_instr": obs.get("final_instr"),
        "total_functions": len(funcs),
        "runtime_reachable": len(observed),
        "static_only_extension": len(static_only),
        "static_closure": len(reached),
        "counts": {b: len(buckets.get(b, [])) for b in order},
        "literal_lift_by_reason": dict(reasons.most_common()),
        "blocked_by_capability": dict(blocked_sub.most_common()),
        "x87_reachable": [{"key": k, "symbol": symbols.get(k, "")}
                          for k in x87_reached],
        "indirect_reachable": [{"key": k, "symbol": symbols.get(k, "")}
                               for k in indirect_reached],
        "buckets": {b: sorted(v) for b, v in buckets.items()},
        "static_only": sorted(static_only),
        "per_function": per_func,
    }
    outp = A / "cpuless_wall_gap.json"
    outp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    line("")
    line(f"wrote {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
