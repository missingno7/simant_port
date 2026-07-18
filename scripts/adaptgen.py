"""adaptgen — route the VMless graph through the recovered corpus (M2b).

The adapter-routing stage of the DOS_RE 2.0 adoption (owner directive,
docs/recovery_inventory.md): where a verified manual CPU-less implementation
exists in ``simant/recovered/``, it is the AUTHORITATIVE implementation —

    original Win16 entry
    -> generated CPU/ABI adapter          (this script's output)
    -> existing verified CPUless recovered implementation

For every matched entry of ``simant/facts/recovered_map.json`` whose ABI
contract is mechanically closed, this script REPLACES the entry's literal-lift
module in the emitted graph directory with a GENERATED adapter module (same
manifest stem, so ``graph_manifest.json`` and every linked caller re-route
without touching the machinery — the ``dos_re.lift.naming`` seam).  One graph,
one implementation per entry: the literal lift for a routed entry no longer
exists on disk.

The adapter is the CPU-carrier marshalling layer the hand-written islands in
``simant/hooks.py`` pioneered: read the scalar args off the emulated stack per
the ``[bp+N]`` map (sign-extended, the state-diff-oracle convention), bind the
three fixed NE data segments as bridge views (DGROUP = DS, SIMANT_DATA_GROUP
and PACK through their load-time-constant DGROUP pointer globals), call the
recovered function, write the result back to AX (or DX:AX), pop the near/far
return frame, and jump to the caller.

Routing policy (every excluded entry lands in the report with its reason):

* ``status == proven`` only.  The 18 ``proven-gated`` entries stay on the
  literal lift until their gates (``_YellowFight``/``_DoTroph``/
  ``_GetRedBestDirs``) dissolve — the lift carries the gate branches natively,
  the recovered impl would raise ``NotImplementedError`` mid-graph (policy (a)
  of the routing design; see docs/run_status.md cont.223).
* complete scalar contract: every arg carries a ``[bp+N]`` offset forming a
  contiguous word frame (near: 4,6,..; far: 6,8,..).  Anything else (dword
  args, register conventions, docstring-only arg names) stays literal.
* views limited to the three fixed data segments; callback-injected impls
  (the render tier) stay literal — their islands are the hand adapters.
* return convention: ``-> int`` routes as AX, ``-> None`` as no-result;
  anything else (tuples, ``int | None``) needs an explicit ``result`` fact in
  ``simant/facts/adapter_facts.json`` else stays literal.
* ``ret`` (near/far) from recovered_map, else closed mechanically from the IR
  record's exits (retf-only -> far, ret-only -> near).  A map/IR conflict is
  a fatal contract error, never silently resolved.
* NO presentation/API effects in the replaced subtree: the recovered corpus
  deliberately omits presentation side calls (balloons/sound/redraw — the
  state-diff oracle stubs them; docs/recovery_inventory.md section 6), so an
  entry whose ASM call subtree reaches one of the ``presentation_sinks``
  facts (or any api: call site) stays on the literal lift — routing it would
  silently drop the effect, violating fail-loud-never-fake.  The dissolution
  is a future presentation-effect sink seam.

VIRTUAL TIME: adapters follow the proven island convention — they do NOT
preserve the instruction-count timeline (a replaced call costs one dispatch
step, +1, like every island since the first).  The literal graph is emitted
``count_instructions=True`` and passes the whole-demo byte-identical
differential against the interpreted oracle; a ROUTED graph is a different
timing config (like a hooks config), verified per-call against the ASM oracle
(scripts/adaptverify.py) and self-consistent under checkpoints --save/--check.
See docs/run_status.md cont.223 for the full analysis and the dissolution
paths (per-entry time contracts / semantic-boundary clock).

Pipeline (routing runs LAST — liftlink re-emits linked callers, which would
clobber adapter files):

    python scripts/liftemit.py                    # literal corpus + manifest
    python scripts/liftlink.py                    # structural link pass
    python scripts/adaptgen.py                    # M2b: route the matched corpus

    python scripts/adaptgen.py --dry-run          # report only, no files
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401  (puts the nested dos_re on sys.path)

from dos_re.lift.naming import GraphNaming  # noqa: E402

DEFAULT_IR = REPO_ROOT / "artifacts" / "recovery_ir.json"
DEFAULT_MAP = REPO_ROOT / "simant" / "facts" / "recovered_map.json"
DEFAULT_FACTS = REPO_ROOT / "simant" / "facts" / "adapter_facts.json"
DEFAULT_FROM_DIR = REPO_ROOT / "simant" / "lifted" / "graph"
DEFAULT_EMIT_DIR = REPO_ROOT / "simant" / "lifted" / "graph_routed"
DEFAULT_REPORT = REPO_ROOT / "artifacts" / "routing_report.json"

#: DGROUP pointer globals holding the two other fixed NE data segments'
#: selectors (load-time relocation constants — verified by exhaustive
#: write-scan, see simant/hooks.py; live-checked against seg_bases[8]/[9]).
SDG_PTR_GLOBAL = 0xC49A     # -> SIMANT_DATA_GROUP (NE seg 8)
PACK_PTR_GLOBAL = 0xC49C    # -> PACK (NE seg 9)

#: view name -> the expression the adapter binds it from (cpu-local names).
VIEW_EXPR = {
    "dgroup": "SelectorBackend(m, s.ds)",
    "simant_data_group": f"SelectorBackend(m, m.rw(s.ds, {SDG_PTR_GLOBAL:#06x}))",
    "pack": f"SelectorBackend(m, m.rw(s.ds, {PACK_PTR_GLOBAL:#06x}))",
}

#: Return conventions an adapter can marshal.  "none"/"ax"/"dxax" are inferred
#: from the implementation's return annotation; the two below are NEVER
#: inferred — they must be declared by an evidence-carrying ``result`` fact in
#: adapter_facts.json, because they encode how a Python-level value maps onto
#: the ASM's register result:
#:   tuple_ax_dx — a ``(AX, DX)`` pair returned in declaration order
RESULTS = ("none", "ax", "dxax", "tuple_ax_dx")


class ContractError(RuntimeError):
    """A stated contract contradicts the IR — a fact bug, never auto-resolved."""


def load_adapter_facts(path: Path) -> dict:
    """The adapter-routing facts document: per-entry overrides/closures under
    ``entries`` ({"KEY": {"args": [{name,bp}...], "ret": "far", "result":
    "dxax", "route": false, "evidence": "..."}}) plus the
    ``presentation_sinks`` symbol list."""
    if not path.is_file():
        return {"entries": {}, "presentation_sinks": {"symbols": []}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("version") != 1:
        raise ValueError(f"{path}: unsupported adapter_facts version")
    doc.setdefault("entries", {})
    doc.setdefault("presentation_sinks", {"symbols": []})
    return doc


def sink_para_keys(sink_symbols, bases: dict[int, str]) -> set[str]:
    """Resolve the presentation-sink symbol names to paragraph-base keys via
    SIMANTW.SYM.  Loud on an unknown name — a stale fact must not silently
    stop protecting."""
    if not sink_symbols:
        return set()
    from simant.probes.symbols import _segments
    name2addr: dict[str, tuple[int, int]] = {}
    for seg_i, (_mod, syms) in enumerate(_segments(), start=1):
        for off, sym in syms:
            name2addr[sym] = (seg_i, off)
    keys = set()
    for name in sink_symbols:
        addr = name2addr.get(name)
        if addr is None:
            raise SystemExit(f"adaptgen: presentation sink {name!r} not in "
                             f"SIMANTW.SYM — stale fact?")
        keys.add(f"{bases[addr[0]]}:{addr[1]:04X}")
    return keys


def build_effect_index(ir_functions: dict):
    """(call-graph edges over paragraph keys, api-call-site counts per key)."""
    edges: dict[str, set[str]] = {}
    api_sites: dict[str, int] = {}
    for key, rec in ir_functions.items():
        cs = key.split(":")[0]
        out = {f"{cs}:{t}" for t in rec.get("calls_near", ())}
        out |= {f"{seg}:{off}" for seg, off in rec.get("calls_far", ())}
        edges[key] = out
        n = 0
        for blk in rec.get("blocks", ()):
            for inst in blk["instructions"]:
                effect = inst.get("platform_effect")
                if effect and effect.startswith("api:"):
                    n += 1
        api_sites[key] = n
    return edges, api_sites


def omitted_effects(start: str, edges, api_sites, sinks: set[str],
                    ir_functions: dict) -> tuple[list[str], int]:
    """Presentation sinks + api call sites reachable from ``start`` through
    the static IR call graph — the effects a routed adapter would DROP,
    because the recovered implementation replaces that whole subtree."""
    seen: set[str] = set()
    stack = [start]
    hits: set[str] = set()
    apis = 0
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        apis += api_sites.get(key, 0)
        for tgt in edges.get(key, ()):
            if tgt in sinks:
                hits.add(tgt)
            if tgt in ir_functions and tgt not in seen:
                stack.append(tgt)
    return sorted(hits), apis


def seg_para_bases(ir_functions: dict) -> dict[int, str]:
    bases: dict[int, str] = {}
    for key, rec in ir_functions.items():
        bases.setdefault(rec["ne_seg"], key.split(":")[0])
    return bases


def close_ret(entry: dict, rec: dict | None) -> str | None:
    """near/far from the map, else mechanically from the IR exits; a conflict
    raises (the b7fb07b class of bug must fail loud)."""
    stated = entry.get("ret")
    ir_ret = None
    if rec is not None:
        exits = set(rec.get("exits", ()))
        if exits == {"retf"}:
            ir_ret = "far"
        elif exits == {"ret"}:
            ir_ret = "near"
    if stated in ("near", "far"):
        if ir_ret is not None and ir_ret != stated:
            raise ContractError(
                f"{entry['key']} {entry['symbol']}: recovered_map says ret="
                f"{stated} but IR exits say {ir_ret} — fix the contract")
        return stated
    return ir_ret


def impl_result_kind(fn) -> str | None:
    """'ax' / 'none' from the impl's return annotation; None = undecidable."""
    ann = getattr(fn, "__annotations__", {}).get("return")
    if isinstance(ann, str):
        ann = ann.strip("'\" ")
    if ann in ("None", type(None)):
        return "none"
    if ann in ("int",):
        return "ax"
    return None


def classify(map_doc: dict, ir_functions: dict, facts_doc: dict):
    """Split the matched corpus into (routed, kept) — every kept entry with an
    explicit reason.  Returns (routed: list[contract-dict], kept: list[dict])."""
    routed: list[dict] = []
    kept: list[dict] = []
    bases = seg_para_bases(ir_functions)
    facts = facts_doc["entries"]
    sinks = sink_para_keys(
        facts_doc["presentation_sinks"].get("symbols", ()), bases)
    edges, api_sites = build_effect_index(ir_functions)

    for entry in map_doc["functions"]:
        key = entry["key"]
        if key is None:
            continue                      # helper-split / utility: no SYM entry
        fact = facts.get(key, {})
        why: list[str] = []

        seg_s, off_s = key.split(":")
        para = bases.get(int(seg_s))
        para_key = f"{para}:{off_s}" if para else None
        rec = ir_functions.get(para_key) if para_key else None
        if rec is None:
            why.append("no-ir-record")
        else:
            if not rec.get("liftable"):
                why.append("not-liftable(keep-interpreted frontier)")
            sink_hits, apis = omitted_effects(para_key, edges, api_sites,
                                              sinks, ir_functions)
            if sink_hits or apis:
                why.append(
                    f"presentation-effects(recovered impl omits the "
                    f"presentation/API calls in the replaced subtree: "
                    f"sinks={sink_hits} api_sites={apis}; needs the "
                    f"presentation-effect sink seam)")

        if fact.get("route") is False:
            why.append(f"fact-excluded({fact.get('evidence', 'no evidence')})")

        if entry["status"] != "proven":
            why.append(f"status:{entry['status']} (gated entries stay on the "
                       f"literal lift until the gate dissolves)")
        if entry["authority"] != "authoritative":
            why.append(f"authority:{entry['authority']}")
        if entry.get("callbacks"):
            why.append("callback-injected(render tier; island is the hand adapter)")
        if entry.get("ret_cleanup"):
            why.append("callee-cleans(dword-arg CRT helper)")

        ret = None
        try:
            ret = fact.get("ret") or close_ret(entry, rec)
        except ContractError as exc:
            raise SystemExit(f"adaptgen: {exc}")
        if ret is None and not why:
            why.append("ret-unclosable(mixed exits)")

        args = fact.get("args") or entry.get("args") or []
        views = entry.get("views") or []
        bad_views = [v for v in views if v not in VIEW_EXPR]
        if bad_views:
            why.append(f"views:{','.join(bad_views)}")
        if any(a.get("bp") is None for a in args):
            why.append("args-incomplete(no [bp+N] map; close from the island "
                       "body or disassembly)")
        elif args and ret in ("near", "far"):
            base = 4 if ret == "near" else 6
            bps = [a["bp"] for a in args]
            if bps != list(range(base, base + 2 * len(args), 2)):
                why.append(f"arg-frame-shape(bps={bps}, expected contiguous "
                           f"words from [bp+{base}])")

        impl_path = entry["impl"]
        modname, fnname = impl_path.rsplit(".", 1)
        fn = None
        try:
            fn = getattr(importlib.import_module(modname), fnname)
        except Exception as exc:  # noqa: BLE001 — a fact error, reported
            why.append(f"impl-unresolvable({exc})")
        result = fact.get("result")
        if result is not None and result not in RESULTS:
            raise SystemExit(f"adaptgen: {key}: bad result fact {result!r}")
        if fn is not None:
            nparams = len(inspect.signature(fn).parameters)
            if nparams != len(views) + len(args):
                why.append(f"sig-mismatch(python takes {nparams} params, "
                           f"contract has {len(views)} views + {len(args)} args)")
            if result is None:
                result = impl_result_kind(fn)
                if result is None:
                    why.append("result-convention(return annotation is neither "
                               "int nor None; add a result fact with evidence)")

        if why:
            kept.append({"key": key, "symbol": entry["symbol"],
                         "impl": impl_path, "reasons": sorted(set(why))})
        else:
            routed.append({
                "key": key, "para_key": f"{para}:{off_s}",
                "cs": int(para, 16), "ip": int(off_s, 16),
                "symbol": entry["symbol"], "impl": impl_path,
                "ret": ret, "views": list(views),
                "args": [(a["name"], a["bp"]) for a in args],
                "result": result,
                "signature": rec.get("signature", ""),
                "facts_used": sorted(fact) if fact else [],
            })
    return routed, kept


# --- emission ----------------------------------------------------------------

def emit_adapter(c: dict, stem: str) -> str:
    """Source of one generated adapter module (function name == stem)."""
    modname, fnname = c["impl"].rsplit(".", 1)
    far = c["ret"] == "far"
    frame = 4 if far else 2               # ret slot bytes popped at exit
    L: list[str] = []
    A = L.append
    A('"""AUTOGENERATED by scripts/adaptgen.py -- CPU/ABI adapter (M2b routing).')
    A("DO NOT hand-edit: regenerate from recovered_map.json + recovery_ir.json")
    A("+ adapter_facts.json.  The recovered implementation is the AUTHORITATIVE")
    A("source (docs/recovery_inventory.md); this module only marshals the CPU")
    A("carrier into its arguments.")
    A("")
    A(f"{c['key']} {c['symbol']} -> {c['impl']}")
    A(f"ABI: {c['ret']} return, args {[(n, f'[bp+{bp}]') for n, bp in c['args']]},")
    A(f"     views {c['views']}, result -> {c['result']}")
    A('"""')
    A("from __future__ import annotations")
    A("")
    A("import sys as _sys")
    A("from pathlib import Path as _Path")
    A("")
    A("_root = str(_Path(__file__).resolve().parents[3])")
    A("if _root not in _sys.path:")
    A("    _sys.path.insert(0, _root)")
    A("")
    A("from dos_re.hooks import self_disable_if_patched")
    if c["views"]:
        A("from simant.bridge.dgroup_view import SelectorBackend")
    A(f"from {modname} import {fnname} as _impl")
    A("")
    A(f"ENTRY = (0x{c['cs']:04X}, 0x{c['ip']:04X})")
    A(f"SIGNATURE = bytes.fromhex({c['signature']!r})")
    A("")
    if any(bp for _n, bp in c["args"]):
        A("")
        A("def _sx(v):")
        A("    return v - 0x10000 if v & 0x8000 else v")
        A("")
    A("")
    A(f"def {stem}(cpu):")
    A(f'    """Generated CPU/ABI adapter for {c["symbol"]} ({c["key"]})."""')
    A(f"    self_disable_if_patched(cpu, 0x{c['ip']:04X}, SIGNATURE, {stem!r})")
    A("    s, m = cpu.s, cpu.mem")
    A("    ss, sp = s.ss, s.sp")
    A("    ret_ip = m.rw(ss, sp)")
    if far:
        A("    ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)")
    params: list[str] = []
    for view in c["views"]:
        var = {"dgroup": "dgroup", "simant_data_group": "sdg",
               "pack": "pack"}[view]
        A(f"    {var} = {VIEW_EXPR[view]}")
        params.append(var)
    for i, (name, bp) in enumerate(c["args"]):
        delta = bp - 2                    # [bp+N] at hook entry = [sp+N-2]
        A(f"    a{i} = _sx(m.rw(ss, (sp + {delta}) & 0xFFFF))   # {name}=[bp+{bp}]")
        params.append(f"a{i}")
    call = f"_impl({', '.join(params)})"
    if c["result"] == "none":
        A(f"    {call}")
    elif c["result"] == "ax":
        A(f"    s.ax = {call} & 0xFFFF")
    elif c["result"] == "dxax":
        A(f"    _r = {call} & 0xFFFFFFFF")
        A("    s.ax = _r & 0xFFFF")
        A("    s.dx = (_r >> 16) & 0xFFFF")
    elif c["result"] == "tuple_ax_dx":
        A(f"    _a, _d = {call}")
        A("    s.ax = _a & 0xFFFF")
        A("    s.dx = _d & 0xFFFF")
    A(f"    s.sp = (sp + {frame}) & 0xFFFF")
    if far:
        A("    s.cs = ret_cs")
    A("    s.ip = ret_ip")
    A("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    ap.add_argument("--facts", default=str(DEFAULT_FACTS))
    ap.add_argument("--from-dir", default=str(DEFAULT_FROM_DIR),
                    help="the emitted+linked literal graph (liftemit/liftlink "
                         "output) the routed flavor is built FROM")
    ap.add_argument("--emit-dir", default=str(DEFAULT_EMIT_DIR),
                    help="where the ROUTED graph is materialized (literal "
                         "modules copied, routed entries replaced by "
                         "generated adapters).  Pass the same directory as "
                         "--from-dir to route in place.")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--dry-run", action="store_true",
                    help="classify + report only; write no adapter modules")
    args = ap.parse_args(argv)

    ir_doc = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    map_doc = json.loads(Path(args.map).read_text(encoding="utf-8"))
    facts_doc = load_adapter_facts(Path(args.facts))

    routed, kept = classify(map_doc, ir_doc["functions"], facts_doc)

    from_dir = Path(args.from_dir)
    emit_dir = Path(args.emit_dir)
    written = 0
    if not args.dry_run:
        if emit_dir.resolve() != from_dir.resolve():
            # Materialize the routed FLAVOR beside the literal graph: copy the
            # emitted+linked corpus, then replace routed stems below.  The
            # literal graph stays intact as the byte-identical-gate artifact.
            if not (from_dir / "graph_manifest.json").is_file():
                raise SystemExit(f"adaptgen: no graph manifest in {from_dir} "
                                 f"— run scripts/liftemit.py (and "
                                 f"liftlink.py) first")
            emit_dir.mkdir(parents=True, exist_ok=True)
            for old in emit_dir.glob("*.py"):
                old.unlink()
            for src in sorted(from_dir.glob("*.py")):
                (emit_dir / src.name).write_bytes(src.read_bytes())
            (emit_dir / "graph_manifest.json").write_bytes(
                (from_dir / "graph_manifest.json").read_bytes())
        naming = GraphNaming.load(emit_dir)
        if not naming.mapping:
            raise SystemExit(f"adaptgen: no graph manifest in {emit_dir} — "
                             f"run scripts/liftemit.py (and liftlink.py) first")
        for c in routed:
            stem = naming.stem_of(c["para_key"])
            path = emit_dir / f"{stem}.py"
            if not path.is_file():
                raise SystemExit(
                    f"adaptgen: {c['para_key']} ({c['symbol']}): literal module "
                    f"{path} is missing — the routing pass replaces emitted "
                    f"modules, it never invents graph entries")
            path.write_text(emit_adapter(c, stem), encoding="utf-8")
            c["module"] = path.name
            written += 1

    reason_counts = Counter()
    for k in kept:
        for r in k["reasons"]:
            reason_counts[r.split("(")[0]] += 1
    report = {
        "version": 1,
        "policy": "M2b adapter routing (docs/run_status.md cont.223); gated "
                  "entries stay literal until their gates dissolve; adapters "
                  "follow the island virtual-time convention (not "
                  "count_instructions)",
        "routed": sorted(routed, key=lambda c: c["key"]),
        "kept_literal": sorted(kept, key=lambda k: k["key"]),
        "totals": {
            "matched": len(routed) + len(kept),
            "routed": len(routed),
            "kept_literal": len(kept),
            "kept_by_reason": dict(sorted(reason_counts.items())),
        },
    }
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")

    shapes = Counter((c["ret"], bool(c["args"]), c["result"]) for c in routed)
    print(f"adaptgen: {len(routed)}/{len(routed) + len(kept)} matched entries "
          f"routed to the recovered corpus"
          + ("" if args.dry_run else f" ({written} adapter modules written to "
                                     f"{emit_dir})"))
    print("routed ABI shapes (ret, has-args, result):")
    for shape, n in shapes.most_common():
        print(f"  {n:4d}  {shape}")
    print(f"kept on the literal lift: {len(kept)} — by reason:")
    for reason, n in reason_counts.most_common():
        print(f"  {n:4d}  {reason}")
    print(f"routing report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
