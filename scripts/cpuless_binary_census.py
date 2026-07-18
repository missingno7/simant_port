"""cpuless_binary_census -- the BINARY-WIDE CPUless census (View B).

Classifies EVERY discovered SIMANTW function (not just the runtime-reachable
closure) into exactly one evidence-backed bucket, and emits machine-readable ABI
metadata per function -- the bridge to ABI-recovered CPUless and later
memoryless.  Distinct from scripts/cpuless_wall_gap.py, which measures the
OBSERVED-closure wall (View A) over a single demo.

The nine buckets (a partition; precedence resolves overlap):
  auto-cpuless-composable          a pure auto-promoted body composes today, OR
                                   composes-in-principle once its callees do
                                   (the composability fixpoint, all capabilities)
  manual-cpuless-override          a hand-recovered pure body is authoritative
  native-platform-replacement      a pure platform trampoline the port replaces
                                   in Python (win16 API / FP runtime boundary)
  fail-loud-unsupported-shape      the emitter refuses the SHAPE (x87, frame/
                                   control-flow/sp/ret-n/boundary) -- exact reason
  blocked-indirect-dispatch        a far/indirect transfer the emitter cannot
                                   compose (no evidence, or reg-3 far-indirect)
  proven-runtime-dead              reachable as code but a demo proves the path
                                   is never taken (positive evidence required)
  proven-unreachable               no static path from any known entry/callback
  likely-data-or-false-entry       not a real function (ir-not-liftable, padding)
  unclassified                     residual (should be empty)

"Not observed in these demos" is NOT dead and NOT unreachable -- it is recorded
as a separate `observed` attribute; only affirmative evidence assigns 6/7.

    python scripts/cpuless_binary_census.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401  (puts nested dos_re on sys.path)
import dos_re  # noqa: E402

sys.path.insert(0, str(Path(dos_re.__file__).resolve().parent.parent / "tools"))
from cpuless_closure import composable_closure  # noqa: E402

A = REPO_ROOT / "artifacts"
X87_OPS = {f"unanalyzed-opcode-{h}" for h in ("9B", "D8", "D9", "DA", "DB",
                                              "DC", "DD", "DE", "DF")}
SHAPE_REFUSALS = {
    "leave-without-enter", "frame-pointer-pop-without-save",
    "frame-restore-without-establish", "frame-pointer-clobbered",
    "tail-dispatch-at-nonzero-depth", "tail-dispatch-with-unbalanced-stack",
    "mixed-return-kinds", "ret-n-stack-args (retf N needs far variant)",
    "sp-as-data", "boundary-or-dispatch-address",
    "platform-farcall-contract-unknown",
}
PLATFORM_SEG = 0x0060


def para_of_seg(ir: dict) -> dict[int, str]:
    out: dict[int, str] = {}
    for entry, rec in ir["functions"].items():
        out.setdefault(rec["ne_seg"], entry.split(":")[0])
    return out


def manual_keys(rmap: dict, seg_base: dict[int, str]) -> set[str]:
    out: set[str] = set()
    for f in rmap["functions"]:
        k = f.get("key")
        if not k:
            continue
        seg_s, off = k.split(":")
        base = seg_base.get(int(seg_s))
        if base:
            out.add(f"{base}:{off}".upper())
    return out


def fn_span(rec: dict) -> tuple[int, int] | None:
    start = end = None
    for blk in rec["blocks"]:
        for i in blk["instructions"]:
            off = int(i["ip"], 16)
            start = off if start is None else min(start, off)
            e = off + len(bytes.fromhex(i["bytes"]))
            end = e if end is None else max(end, e)
    return (start, end) if start is not None else None


def near_far_callees(rec: dict, cs: int, funcs: dict) -> set[str]:
    """Direct GAME callees (near + non-platform far) that are IR functions."""
    out: set[str] = set()
    for t in (rec.get("calls_near") or []):
        nk = f"{cs:04X}:{int(t, 16):04X}"
        if nk in funcs:
            out.add(nk)
    for seg, off in (rec.get("calls_far") or []):
        if int(seg, 16) == PLATFORM_SEG:
            continue
        fk = f"{int(seg, 16):04X}:{int(off, 16):04X}"
        if fk in funcs:
            out.add(fk)
    return out


def is_platform_trampoline(rec: dict, cs: int, funcs: dict) -> bool:
    """A pure boundary shim: at least one platform far-call, NO game callees,
    and no game-meaningful control (only the thunk + return)."""
    far = rec.get("calls_far") or []
    if not any(int(seg, 16) == PLATFORM_SEG for seg, _ in far):
        return False
    if near_far_callees(rec, cs, funcs):
        return False
    ninsts = sum(len(b["instructions"]) for b in rec["blocks"])
    return ninsts <= 6          # thunk + far-call + retf, nothing else


def main() -> int:
    ir = json.loads((A / "recovery_ir.json").read_text(encoding="utf-8"))
    census = json.loads((A / "cpuless_census.json").read_text(encoding="utf-8"))
    pcensus = json.loads((A / "cpuless_promote_census.json").read_text(encoding="utf-8"))
    rmap = json.loads((REPO_ROOT / "simant" / "facts" / "recovered_map.json"
                       ).read_text(encoding="utf-8"))
    plat = json.loads((A / "plat_farcalls.json").read_text(encoding="utf-8"))
    observed_doc = json.loads((A / "observed.json").read_text(encoding="utf-8")) \
        if (A / "observed.json").is_file() else {"executed": []}
    dyn_doc = json.loads((A / "indirect_sites.json").read_text(encoding="utf-8")) \
        if (A / "indirect_sites.json").is_file() else {"sites": []}

    funcs = ir["functions"]
    keys = [k.upper() for k in funcs]
    seg_base = para_of_seg(ir)
    manual = manual_keys(rmap, seg_base) & set(keys)
    promoted_auto = {k.upper() for k in pcensus["promotable"]} - manual
    refused_reason: dict[str, str] = {}
    for reason, ks in pcensus["refused"].items():
        for k in ks:
            refused_reason[k.upper()] = reason
    blocked_tier = {k.upper() for k in census["tiers"]["blocked"]}
    cfns = {k.upper(): v for k, v in census["functions"].items()}
    symbols = {k.upper(): (funcs[k].get("symbol") or "") for k in funcs}
    observed = {a.upper() for a in observed_doc.get("executed", ())}

    # per-site observed dispatch evidence -> dyn edges for the fixpoint + closure
    dyn_evidence: dict[str, list[str]] = {}
    for s in dyn_doc.get("sites", []):
        dyn_evidence[s["site"].upper()] = sorted(
            t.upper() for t in s.get("targets", {}))

    # ---- alternate-entry containment (shared code) ------------------------
    # Many IR "functions" are alternate entries into another function's byte
    # span; if the container is reachable, so is the alt entry.
    spans: dict[int, list[tuple[int, int, str]]] = {}
    for k in keys:
        sp = fn_span(funcs[k])
        if sp:
            seg = int(k.split(":")[0], 16)
            spans.setdefault(seg, []).append((sp[0], sp[1], k))

    def container_of(k: str) -> str | None:
        seg = int(k.split(":")[0], 16)
        off = int(k.split(":")[1], 16)
        for s, e, fk in spans.get(seg, ()):
            if fk != k and s <= off < e:
                return fk
        return None

    # ---- reachability from the MAXIMAL root set ---------------------------
    # "proven unreachable (no static path from ANY entry)" must be conservative:
    # under-counting entries would slander live callbacks as dead (the owner's
    # explicit caution -- "not observed" != unreachable). The maximal root set is
    #   * every call-graph SOURCE (a function with no incoming direct edge -- it
    #     cannot be reached by a call, so it is itself an entry: the module
    #     bootstrap, an ISR, an address-taken callback with no static caller),
    #   * the recovery boundary heads (registered callbacks / dispatch arrivals),
    #   * every OBSERVED entry (ground-truth reachable).
    # A function still unreached from THIS set -- and not an alternate entry of a
    # reached function -- has incoming edges only from other unreached code: a
    # genuine dead island, the only defensible "proven unreachable".
    incoming: set[str] = set()
    for k in keys:
        cs = int(k.split(":")[0], 16)
        for nk in near_far_callees(funcs[k], cs, funcs):
            incoming.add(nk)
        for blk in funcs[k]["blocks"]:
            for i in blk["instructions"]:
                if i.get("kind") in ("call_ind", "jmp_ind"):
                    site = f"{cs:04X}:{int(i['ip'], 16):04X}"
                    for tgt in dyn_evidence.get(site, ()):
                        if tgt in funcs:
                            incoming.add(tgt)
    sources = set(keys) - incoming
    roots: set[str] = set(observed) | sources
    bh = A / "boundary_heads_para.txt"
    if bh.is_file():
        for line in bh.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                roots.add(line.upper())
    reachable = set()
    work = list(roots)
    while work:
        k = work.pop().upper()
        if k in reachable:
            continue
        reachable.add(k)
        rec = funcs.get(k)
        if not rec:
            continue
        cs = int(k.split(":")[0], 16)
        for nk in near_far_callees(rec, cs, funcs):
            if nk not in reachable:
                work.append(nk)
        for blk in rec["blocks"]:
            for i in blk["instructions"]:
                if i.get("kind") in ("call_ind", "jmp_ind"):
                    site = f"{cs:04X}:{int(i['ip'], 16):04X}"
                    for tgt in dyn_evidence.get(site, ()):
                        if tgt in funcs and tgt not in reachable:
                            work.append(tgt)
    # alternate entries of a reached container are reachable too
    for k in keys:
        if k not in reachable:
            c = container_of(k)
            if c and c in reachable:
                reachable.add(k)

    # ---- the own-body blocker (needed for the strict fixpoint) ------------
    def own_body_blocker(k: str) -> str | None:
        """A HARD refusal in the function's OWN body (not callee composition)."""
        refs = set(cfns.get(k, {}).get("refusals", {}))
        if refs & X87_OPS:
            return "x87"
        if "indirect-or-far-transfer" in refs:
            return "indirect-or-far-transfer"
        if "ir-not-liftable" in refs:
            return "ir-not-liftable"
        for r in refs:
            if r.startswith("unanalyzed-opcode-"):
                return r
        # a promote-census SHAPE refusal is an own-body shape gap
        pr = refused_reason.get(k)
        if pr in SHAPE_REFUSALS:
            return pr
        return None

    # ---- the composability fixpoint (dos_re SCC-atomic) -------------------
    # roots = every IR entry (View B is binary-wide, not gated on reachability);
    # resolved = the manual overrides (CPUless bodies) + every platform far-call
    # slot that HAS a contract. TWO body_clean regimes:
    #   composable_today       -- own body passes the emitter TODAY (no x87, no
    #                             far-indirect, no unbuilt frame/control-flow
    #                             SHAPE): the honest current-capability reach.
    #   composable_in_principle-- also treats the PLANNED shape emitters
    #                             (frame/control-flow/sp/boundary) as built, so
    #                             only x87 + far-indirect stay hard (the rung
    #                             plan's deferred/last levers).
    plat_slots = {k.upper() for k in plat.get("contracts", {})
                  if not k.startswith("_")}
    refusals_map = {k: (list(v.get("refusals", {}).keys())[0]
                        if v.get("refusals") else "")
                    for k, v in cfns.items()}
    liftable = {k for k in keys if funcs[k].get("liftable", True)}
    HARD = {"x87", "indirect-or-far-transfer", "ir-not-liftable"}
    own_blk = {k: own_body_blocker(k) for k in liftable}
    clean_today = {k for k in liftable if own_blk[k] is None}
    clean_principle = {k for k in liftable if own_blk[k] not in HARD}
    comp = composable_closure(
        ir, keys, promoted=promoted_auto | manual, body_clean=clean_today,
        resolved=manual | plat_slots, dyn_evidence=dyn_evidence,
        refusals=refusals_map)
    composable = set(comp["composable_keys"])
    comp_pr = composable_closure(
        ir, keys, promoted=promoted_auto | manual, body_clean=clean_principle,
        resolved=manual | plat_slots, dyn_evidence=dyn_evidence,
        refusals=refusals_map)
    composable_principle = set(comp_pr["composable_keys"])

    buckets: dict[str, list[str]] = {b: [] for b in (
        "auto-cpuless-composable", "manual-cpuless-override",
        "native-platform-replacement", "fail-loud-unsupported-shape",
        "blocked-indirect-dispatch", "proven-runtime-dead",
        "proven-unreachable", "likely-data-or-false-entry", "unclassified")}
    reasons: dict[str, str] = {}

    def root_blocker(k: str, seen=None) -> str:
        """Resolve a transitively-blocked (contains-call) function to the ROOT
        capability gap: DFS its game callees to the terminal own-body blocker(s)
        that are themselves not composable, priority x87 > indirect > shape."""
        seen = seen if seen is not None else set()
        if k in seen:
            return ""
        seen.add(k)
        own = own_body_blocker(k)
        if own:
            return own
        best = ""
        rank = {"x87": 3, "indirect-or-far-transfer": 2}
        cs = int(k.split(":")[0], 16)
        for c in near_far_callees(funcs[k], cs, funcs):
            if c in composable or c in promoted_auto or c in manual:
                continue
            r = root_blocker(c, seen)
            if r and rank.get(r, 1) > rank.get(best, 0):
                best = r
        return best

    for k in keys:
        rec = funcs[k]
        cs = int(k.split(":")[0], 16)
        # 8 -- data / false entry (not a real function)
        if not rec.get("liftable", True) or "ir-not-liftable" in \
                cfns.get(k, {}).get("refusals", {}):
            buckets["likely-data-or-false-entry"].append(k)
            reasons[k] = "ir-not-liftable"
            continue
        # 2 -- manual override (authoritative CPUless body; always live)
        if k in manual:
            buckets["manual-cpuless-override"].append(k)
            reasons[k] = "authoritative-hand-recovered"
            continue
        # 7 -- proven unreachable (dead island: no path from the maximal roots)
        if k not in reachable and k not in observed:
            buckets["proven-unreachable"].append(k)
            reasons[k] = "no-static-path-from-any-entry"
            continue
        # 3 -- native platform trampoline (replaced by the Python surface)
        if is_platform_trampoline(rec, cs, funcs):
            buckets["native-platform-replacement"].append(k)
            reasons[k] = "pure-platform-thunk"
            continue
        blocker = own_body_blocker(k)
        # 5 -- blocked by an indirect / unresolved far transfer in the OWN body
        if blocker == "indirect-or-far-transfer":
            buckets["blocked-indirect-dispatch"].append(k)
            reasons[k] = "indirect-or-far-transfer"
            continue
        # 4 -- fail-loud unsupported SHAPE in the own body (x87 / frame / ...)
        if blocker is not None:
            buckets["fail-loud-unsupported-shape"].append(k)
            reasons[k] = blocker
            continue
        # 1 -- auto CPUless composable (byte-exact today, or composes-in-principle
        #      once its callees do -- the SCC-atomic composability fixpoint)
        if k in promoted_auto or k in composable:
            buckets["auto-cpuless-composable"].append(k)
            reasons[k] = ("byte-exact-today" if k in promoted_auto
                          else "composes-in-principle")
            continue
        # residual: body-clean but a callee never composes -> resolve to the ROOT
        # capability gap and file under that gap's bucket (indirect vs shape).
        rb = root_blocker(k)
        has_dispatch = any(i.get("kind") in ("call_ind", "jmp_ind")
                           for blk in rec["blocks"] for i in blk["instructions"])
        if rb == "indirect-or-far-transfer":
            buckets["blocked-indirect-dispatch"].append(k)
            reasons[k] = "via-callee:indirect-or-far-transfer"
        elif rb:
            buckets["fail-loud-unsupported-shape"].append(k)
            reasons[k] = f"via-callee:{rb}"
        elif has_dispatch or not rb:
            # A dispatch-cluster member: either this body dispatches (a jump-table
            # arm), OR root_blocker resolved to NOTHING (rb == "") -- every callee
            # is composable or lies on a mutual-recursion cycle back into this
            # set, with no hard own-body gap anywhere.  Such a body is blocked
            # ONLY by an unresolved composition SCC (the message-pump / ant-sim
            # cluster), which the runtime dispatch-cluster capability promotes
            # atomically -- not a shape/indirect gap.  Frame/retf composition
            # widened these clusters (more callees compose, exposing the residual
            # cycle), so attribute them to the dispatch-cluster, never leave the
            # partition with a residual "unclassified" bucket.
            buckets["blocked-indirect-dispatch"].append(k)
            reasons[k] = "dispatch-cluster"
        else:
            buckets["unclassified"].append(k)
            reasons[k] = refused_reason.get(k, "?")

    # ---- ABI metadata (the memoryless bridge) -----------------------------
    per_function: dict[str, dict] = {}
    manual_by_key = {}
    for f in rmap["functions"]:
        kk = f.get("key")
        if kk:
            seg_s, off = kk.split(":")
            base = seg_base.get(int(seg_s))
            if base:
                manual_by_key[f"{base}:{off}".upper()] = f
    bucket_of = {k: b for b, ks in buckets.items() for k in ks}
    for k in keys:
        rec = funcs[k]
        cs = int(k.split(":")[0], 16)
        abi = cfns.get(k, {})
        indirect_targets = sorted({
            t for blk in rec["blocks"] for i in blk["instructions"]
            if i.get("kind") in ("call_ind", "jmp_ind")
            for t in dyn_evidence.get(f"{cs:04X}:{int(i['ip'], 16):04X}", ())})
        m = manual_by_key.get(k)
        per_function[k] = {
            "symbol": symbols.get(k, ""),
            "bucket": bucket_of.get(k, "unclassified"),
            "reason": reasons.get(k, ""),
            "observed": k in observed,
            "reachable_from_roots": k in reachable,
            "composable_in_principle": k in composable or k in promoted_auto,
            "tier": abi.get("tier", ""),
            "abi": {
                # register contract (candidate params / return values)
                "reg_inputs": abi.get("inputs", []),
                "reg_outputs": abi.get("outputs", []),
                # side effects (memory read/write; range granularity is TODO --
                # the analyzer currently records booleans, not byte ranges)
                "reads_mem": abi.get("reads_mem"),
                "writes_mem": abi.get("writes_mem"),
                "max_stack_use": abi.get("max_stack_use"),
                "refusals": list(abi.get("refusals", {}).keys()),
            },
            "callees_direct": sorted(near_far_callees(rec, cs, funcs)),
            "callees_platform": sorted(
                f"{int(s, 16):04X}:{int(o, 16):04X}"
                for s, o in (rec.get("calls_far") or [])
                if int(s, 16) == PLATFORM_SEG),
            "callees_indirect_observed": indirect_targets,
            "ints": rec.get("ints") or [],
        }
        if m:                       # richer manual-recovery ABI facts
            per_function[k]["manual_abi"] = {
                "impl": m.get("impl"), "ret": m.get("ret"),
                "ret_cleanup": m.get("ret_cleanup"),
                "args": m.get("args", []), "views": m.get("views", []),
                "callbacks": m.get("callbacks", []),
            }

    # ---- report -----------------------------------------------------------
    counts = {b: len(ks) for b, ks in buckets.items()}
    total = sum(counts.values())
    reachable_ir = len(reachable & set(keys))
    print("=" * 72)
    print("BINARY-WIDE CPUless CENSUS (View B) -- all discovered functions")
    print("=" * 72)
    print(f"total IR functions: {total}   (observed this demo: {len(observed)}; "
          f"reachable-from-any-entry: {reachable_ir})")
    print()
    order = ["auto-cpuless-composable", "manual-cpuless-override",
             "native-platform-replacement", "fail-loud-unsupported-shape",
             "blocked-indirect-dispatch", "proven-runtime-dead",
             "proven-unreachable", "likely-data-or-false-entry", "unclassified"]
    for b in order:
        print(f"  {b:32} {counts[b]:5d}")
    print()
    print("fail-loud-unsupported-shape, by exact refusal:")
    sc = Counter(reasons[k] for k in buckets["fail-loud-unsupported-shape"])
    for r, n in sc.most_common():
        print(f"    {r:<42} {n:4d}")
    print()
    keyset = set(keys)
    comp_today_ir = len(composable & keyset)
    comp_pr_ir = len(composable_principle & keyset)
    print(f"composable TODAY (auto + bottom-up, respecting emitter gates): "
          f"{comp_today_ir} game functions")
    print(f"composable IN PRINCIPLE (+ planned frame/control-flow shape emitters,"
          f" x87 & far-indirect still hard): {comp_pr_ir} game functions")
    print(f"  message-pump dispatch cluster: max SCC size {comp['max_scc_size']}")

    out = {
        "_notice": "GENERATED by scripts/cpuless_binary_census.py. Disposable.",
        "total_functions": total,
        "observed_this_demo": len(observed),
        "reachable_from_any_entry": reachable_ir,
        "demo": observed_doc.get("demo"),
        "bucket_counts": counts,
        "fail_loud_shape_by_reason": dict(sc.most_common()),
        "composable_today": len(composable & set(keys)),
        "composable_in_principle": len(composable_principle & set(keys)),
        "max_dispatch_scc": comp["max_scc_size"],
        "buckets": {b: sorted(ks) for b, ks in buckets.items()},
        "per_function": per_function,
    }
    (A / "cpuless_binary_census.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {A / 'cpuless_binary_census.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
