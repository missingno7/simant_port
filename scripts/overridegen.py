"""overridegen -- emit the CARRIER-FREE CPUless override bodies + contracts.

The unified-override-graph seam (owner's directive; docs/run_status.md cont.247):
every SimAnt function has a GENERATED CPUless identity, and where a verified
hand-recovered body exists in ``simant/recovered/`` it OVERRIDES at the SAME
address in ONE unified CPUless graph --

    implementation = manual_overrides.get(addr, generated[addr])

This is the CPUless-idiom analogue of ``scripts/adaptgen.py``: it shares
adaptgen's EXACT contract classifier (:func:`adaptgen.classify` over
``simant/facts/recovered_map.json`` + the recovery IR + ``adapter_facts.json``),
so the SAME 166 mechanically-closed entries route -- but instead of a CPU-carrier
adapter (``def adapter(cpu):`` reading ``cpu.s``/``cpu.mem``), it emits a
CARRIER-FREE marshalling shim that obeys the dos_re CPUless BODY ABI

    func_CCCC_IIII(mem, *, ds=0, ss=0, sp=0) -> (outputs, compat)

operating on the CPUless caller's EXPLICIT state (the mem image + the register
bundle the CPUless convention passes), NEVER a ``cpu`` object.  The shim:

  (a) reads each ``[bp+N]`` scalar arg off the CPUless caller's stack
      (``mem.rw(ss, sp + N - 2)`` -- at CPUless callee entry ``ss:sp`` points at
      the return frame, exactly as at the historical hook entry),
  (b) binds the dgroup / simant_data_group / pack views over that SAME mem image
      (``SelectorBackend(mem, ...)`` -- duck-typed, VM-free),
  (c) calls the authoritative hand-recovered body,
  (d) returns its AX / DX:AX result in the CPUless output dict,
  (e) leaves the ret/stack effect to the composing caller (the contract's
      ret_kind/ret_pop, applied by dos_re's override-call composition).

Because the shim reaches no ``dos_re.cpu`` and no ``simant.lifted``, it PASSES
``lint_cpuless`` -- it lives in the recovered corpus (``simant/native/cpuless/``)
as the body dos_re's ``cpuless_promote --overrides`` seeds a contract for and
every caller composes.

VIRTUAL TIME (cont.248): an override that runs at the ISLAND cost (one dispatch
step) is exact in STATE but not in TIME -- it does not execute the original's
control flow, so it under-counts, shifting every downstream platform effect and
desyncing the instruction-count-keyed demo (cont.247 measured -16 at cp0 ->
-2.2M by cp31).  Each override therefore derives a dos_re VIRTUAL-TIME CONTRACT
(``virtual_time``) from the recovery IR (:func:`static_cost`):

  * ``{"kind": "static", "cost": N}`` -- every entry->ret path through the
    original executes exactly N instructions (a single-path body, or one whose
    branch arms have equal length, or one whose calls are themselves static).
    The emitted body returns N, so composition is virtual-time-EXACT and the
    address is admissible to the byte-exact gate.
  * ``{"kind": "island"}`` -- the cost genuinely depends on the executed path
    (a loop, or unequal branch arms) and semantic recovery does not hand us the
    path.  NOT gate-admissible; ``cpuless_promote --overrides-time-exact-only``
    leaves the address on its instruction-exact GENERATED body.  We never GUESS
    a cost.

Emits, for each routed entry:
  * ``<bodies-dir>/func_<para>_<ip>.py``  -- the carrier-free override body
  * an entry in ``<out>`` (overrides.json)  -- the dos_re override contract

Every entry adaptgen keeps on the literal lift (args-incomplete, presentation-
effects, gated, sig-mismatch, ...) stays on the GENERATED body -- reported here
with the same reason, unchanged.  Run AFTER the dos_re promoter's --apply (which
regenerates simant/native/cpuless); this drops the override bodies in beside the
generated corpus, so they are not wiped.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401

import adaptgen  # noqa: E402  (the shared contract classifier)

DEFAULT_IR = REPO_ROOT / "artifacts" / "recovery_ir.json"
DEFAULT_MAP = REPO_ROOT / "simant" / "facts" / "recovered_map.json"
DEFAULT_FACTS = REPO_ROOT / "simant" / "facts" / "adapter_facts.json"
DEFAULT_BODIES = REPO_ROOT / "simant" / "native" / "cpuless"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "overrides.json"
DEFAULT_REPORT = REPO_ROOT / "artifacts" / "override_report.json"

#: DGROUP pointer globals holding the other two fixed NE data segments'
#: selectors (load-time relocation constants; see simant/hooks.py + adaptgen).
SDG_PTR_GLOBAL = 0xC49A     # -> SIMANT_DATA_GROUP (NE seg 8)
PACK_PTR_GLOBAL = 0xC49C    # -> PACK (NE seg 9)

#: view name -> the (mem-local) SelectorBackend binding expression.
VIEW_BIND = {
    "dgroup": "SelectorBackend(mem, ds)",
    "simant_data_group": f"SelectorBackend(mem, mem.rw(ds, {SDG_PTR_GLOBAL:#06x}))",
    "pack": f"SelectorBackend(mem, mem.rw(ds, {PACK_PTR_GLOBAL:#06x}))",
}
_VIEW_VAR = {"dgroup": "dgroup", "simant_data_group": "sdg", "pack": "pack"}


class NotStatic(Exception):
    """The original's per-invocation instruction count is not a constant."""


def _insts(fn: dict) -> dict[int, tuple[dict, int]]:
    """{ip: (instruction, next_ip)} over the whole function region."""
    out: dict[int, tuple[dict, int]] = {}
    for blk in fn["blocks"]:
        for i in blk["instructions"]:
            ip = int(i["ip"], 16)
            out[ip] = (i, ip + len(i["bytes"]) // 2)
    return out


def static_cost(key: str, funcs: dict, _chain: tuple = (),
                _memo: dict | None = None) -> int:
    """The ORIGINAL's per-invocation instruction count, entry through its return
    inclusive -- or raise :class:`NotStatic`.

    This is the ground truth a ``static`` virtual-time contract declares, and it
    is exactly what the generated twin accumulates (dos_re's emit_cpuless counts
    every instruction including the terminating ``ret``/``retf``, and a composing
    caller adds the callee's ``cost`` verbatim).  Derived MECHANICALLY from the
    recovery IR, never hand-written:

      seq/jmp     1 + cost of the successor
      jcc         both arms must cost the SAME, else path-dependent
      ret/retf    1 (the return instruction itself)
      call        1 + the callee's own static cost + the successor's
      call_far    same, unless the target is the 0060 platform thunk (the
                  plat.farcall dispatch cost is dynamic)

    Any loop (a back edge), unequal branch arms, recursion, or a platform call
    makes the cost path-dependent -- reported, never approximated.
    """
    _memo = {} if _memo is None else _memo
    if key in _memo:
        v = _memo[key]
        if isinstance(v, NotStatic):
            raise v
        return v
    if key in _chain:
        raise NotStatic("recursion")
    fn = funcs.get(key)
    if fn is None:
        raise NotStatic("no-ir")
    chain = _chain + (key,)
    insts = _insts(fn)
    cs = int(key.split(":")[0], 16)
    seen: dict[int, int] = {}
    on_path: set[int] = set()

    def at(ip: int) -> int:
        if ip in seen:
            return seen[ip]
        if ip in on_path:
            raise NotStatic("loop")
        on_path.add(ip)
        rec = insts.get(ip)
        if rec is None:
            raise NotStatic("off-region")
        i, nxt = rec
        kind = i["kind"]
        if kind in ("ret", "retf", "iret"):
            cost = 1
        elif kind == "seq":
            cost = 1 + at(nxt)
        elif kind == "jmp":
            cost = 1 + at(int(i["target"], 16))
        elif kind == "jcc":
            taken, fall = at(int(i["target"], 16)), at(nxt)
            if taken != fall:
                raise NotStatic("path-dependent")
            cost = 1 + taken
        elif kind == "call":
            tgt = f"{cs:04X}:{int(i['target'], 16):04X}"
            cost = 1 + static_cost(tgt, funcs, chain, _memo) + at(nxt)
        elif kind == "call_far":
            seg, off = i["far_target"]
            if int(seg, 16) == 0x0060:
                raise NotStatic("platform-farcall")
            tgt = f"{int(seg, 16):04X}:{int(off, 16):04X}"
            cost = 1 + static_cost(tgt, funcs, chain, _memo) + at(nxt)
        else:
            raise NotStatic(kind)
        on_path.discard(ip)
        seen[ip] = cost
        return cost

    try:
        total = at(int(key.split(":")[1], 16))
    except NotStatic as exc:
        _memo[key] = exc
        raise
    _memo[key] = total
    return total


def virtual_time_of(c: dict, funcs: dict, memo: dict) -> dict:
    """The dos_re virtual-time contract for one routed override."""
    try:
        return {"kind": "static", "cost": static_cost(c["para_key"], funcs,
                                                      _memo=memo),
                "evidence": f"single-path original ({c['para_key']} "
                            f"{c['symbol']}); instruction count derived from "
                            f"the recovery IR CFG (scripts/overridegen.py "
                            f"static_cost)"}
    except NotStatic as exc:
        return {"kind": "island", "reason": str(exc),
                "evidence": f"per-invocation cost is path-dependent ({exc}); "
                            f"semantic recovery does not yield the executed "
                            f"path -- NOT gate-admissible, stays on the "
                            f"generated body under "
                            f"--overrides-time-exact-only"}


def emit_body(c: dict) -> str:
    """Source of one carrier-free CPUless override body (function name matches
    the dos_re contract: ``func_<para>_<ip>``)."""
    modname, fnname = c["impl"].rsplit(".", 1)
    stem = f"func_{c['para_key'].replace(':', '_').lower()}"
    views = c["views"]
    args = c["args"]
    L: list[str] = []
    A = L.append
    A('"""AUTOGENERATED by scripts/overridegen.py -- carrier-free CPUless OVERRIDE')
    A("body (the unified override-graph seam).  DO NOT hand-edit: regenerate from")
    A("recovered_map.json + recovery_ir.json + adapter_facts.json.")
    A("")
    A(f"{c['key']} {c['symbol']} -> {c['impl']}")
    A(f"ABI: {c['ret']} return, views {views},")
    A(f"     virtual time: {c['virtual_time']['kind']} "
      f"(cost {c['virtual_time'].get('cost', 1)})")
    A(f"     args {[(n, f'[bp+{bp}]') for n, bp in args]}, result -> {c['result']}")
    A("")
    A("Obeys the dos_re CPUless body ABI (mem + register bundle -> outputs,")
    A("compat); reaches no dos_re.cpu / cpu.s -- it is composed as a DIRECT")
    A('override, not a CPU-carrier adapter."""')
    A("from __future__ import annotations")
    A("")
    if views:
        A("from simant.bridge.dgroup_view import SelectorBackend")
    A(f"from {modname} import {fnname} as _impl")
    A("")
    if args:
        A("")
        A("def _sx(v):")
        A("    return v - 0x10000 if v & 0x8000 else v")
        A("")
    A("")
    # inputs: ds when a view is bound; ss/sp when a stack arg is read.
    params = []
    if views:
        params.append("ds=0")
    if args:
        params += ["ss=0", "sp=0"]
    A(f"def {stem}(mem, *, {', '.join(params)}):")
    A(f'    """Carrier-free CPUless override for {c["symbol"]} ({c["key"]})."""')
    call_params: list[str] = []
    for v in views:
        A(f"    {_VIEW_VAR[v]} = {VIEW_BIND[v]}")
        call_params.append(_VIEW_VAR[v])
    for i, (name, bp) in enumerate(args):
        delta = bp - 2       # [bp+N] at hook/callee entry == [sp+N-2]
        A(f"    a{i} = _sx(mem.rw(ss, (sp + {delta}) & 0xFFFF))   # {name}=[bp+{bp}]")
        call_params.append(f"a{i}")
    call = f"_impl({', '.join(call_params)})"
    if c["result"] == "ax":
        A(f"    ax = {call} & 0xFFFF")
        out = "{'ax': ax}"
    elif c["result"] == "dxax":
        A(f"    _r = {call} & 0xFFFFFFFF")
        out = "{'ax': _r & 0xFFFF, 'dx': (_r >> 16) & 0xFFFF}"
    else:                                        # none
        A(f"    {call}")
        out = "{}"
    vt = c["virtual_time"]
    cost = vt.get("cost", 1)
    if vt["kind"] == "static":
        A(f"    # virtual time: the ORIGINAL's per-invocation instruction count "
          f"is the")
        A(f"    # constant {cost} (every entry->ret path), so composing this "
          f"override is")
        A(f"    # virtual-time-EXACT -- the caller's _cost accumulates exactly "
          f"as it")
        A(f"    # would over the ASM.")
    else:
        A(f"    # virtual time: ISLAND (one dispatch step) -- the original's "
          f"cost is")
        A(f"    # path-dependent ({vt['reason']}), so this override is NOT "
          f"admissible")
        A(f"    # to an instruction-count-keyed gate.")
    A(f"    return {out}, {{'flags': 0, 'fmask': 0, 'cost': {cost}}}")
    A("")
    return "\n".join(L) + "\n"


def contract_of(c: dict) -> dict:
    """The dos_re override contract for a routed entry (fed to
    cpuless_promote --overrides)."""
    views, args = c["views"], c["args"]
    inputs = []
    if views:
        inputs.append("ds")
    if args:
        inputs += ["ss", "sp"]
    result = c["result"]
    outputs = {"ax": ["ax"], "dxax": ["ax", "dx"], "none": []}[result]
    stem = f"func_{c['para_key'].replace(':', '_').lower()}"
    return {
        "name": stem,
        "inputs": sorted(inputs),
        "outputs": outputs,
        "ret_kind": c["ret"],
        "ret_pop": 0,               # adaptgen routes only cdecl (caller-pops);
                                    # ret_cleanup/pascal entries stay literal
        "sp_delta": 0,
        "sp_deltas": [0],
        "needs_plat": False,        # presentation/API subtrees stay literal
        "df_livein": False,
        "flags_livein": False,
        "exit_flags": [],           # a semantic body guarantees no exit flags;
                                    # a caller that reads them stays refused
        "virtual_time": c["virtual_time"],
        "evidence": f"authoritative hand-recovered ({c['impl']}); "
                    f"routed by adaptgen classify (recovered_map.json)",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    ap.add_argument("--facts", default=str(DEFAULT_FACTS))
    ap.add_argument("--bodies-dir", default=str(DEFAULT_BODIES),
                    help="where the carrier-free override bodies are written "
                         "(the recovered corpus; must exist -- run AFTER the "
                         "dos_re promoter --apply so they are not wiped)")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="the override-contract JSON fed to cpuless_promote "
                         "--overrides")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--only-time-exact", action="store_true",
                    help="emit body modules ONLY for the overrides carrying an "
                         "EXACT virtual-time contract (the gate-admissible "
                         "set).  Required whenever the dos_re promoter runs "
                         "with --overrides-time-exact-only: a non-seeded "
                         "address keeps its GENERATED body, which lives under "
                         "the SAME func_<para>_<ip> module name, so writing an "
                         "island override there would clobber it.")
    ap.add_argument("--dry-run", action="store_true",
                    help="classify + write overrides.json + report; emit no "
                         "body modules")
    args = ap.parse_args(argv)

    ir_doc = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    map_doc = json.loads(Path(args.map).read_text(encoding="utf-8"))
    facts_doc = adaptgen.load_adapter_facts(Path(args.facts))

    routed, kept = adaptgen.classify(map_doc, ir_doc["functions"], facts_doc)

    # the VIRTUAL-TIME contract (cont.248), derived mechanically per override.
    _memo: dict = {}
    for c in routed:
        c["virtual_time"] = virtual_time_of(c, ir_doc["functions"], _memo)

    overrides = {c["para_key"]: contract_of(c) for c in routed}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "_notice": "GENERATED by scripts/overridegen.py from recovered_map.json "
                   "+ recovery_ir.json + adapter_facts.json (adaptgen classify). "
                   "Disposable; regenerate, do not hand-edit.",
        "version": 1,
        "overrides": {k: overrides[k] for k in sorted(overrides)},
    }, indent=1), encoding="utf-8")

    written = 0
    if not args.dry_run:
        bodies = Path(args.bodies_dir)
        if not bodies.is_dir():
            raise SystemExit(f"overridegen: bodies dir {bodies} missing -- run "
                             f"the dos_re promoter --apply first")
        for c in routed:
            if (args.only_time_exact
                    and c["virtual_time"]["kind"] == "island"):
                continue
            stem = f"func_{c['para_key'].replace(':', '_').lower()}"
            (bodies / f"{stem}.py").write_text(emit_body(c), encoding="utf-8",
                                               newline="\n")
            written += 1

    exact = sorted(k for k, v in overrides.items()
                   if v["virtual_time"]["kind"] != "island")
    inexact = Counter(v["virtual_time"].get("reason", "?")
                      for v in overrides.values()
                      if v["virtual_time"]["kind"] == "island")

    reason_counts = Counter()
    for k in kept:
        for r in k["reasons"]:
            reason_counts[r.split("(")[0]] += 1
    report = {
        "version": 1,
        "policy": "unified override-graph (cont.247): carrier-free CPUless "
                  "override bodies for the mechanically-closed contracts "
                  "(adaptgen classify); the rest stay on the GENERATED body.",
        "overrides": sorted(overrides),
        "kept_generated": sorted({k["key"]: k for k in kept}.values(),
                                 key=lambda k: k["key"]),
        "virtual_time": {
            "policy": "an override declares what its compat-channel cost MEANS; "
                      "only a STATIC (or model) contract is admissible to the "
                      "instruction-count-keyed byte-exact gate "
                      "(cpuless_promote --overrides-time-exact-only).",
            "gate_admissible": exact,
            "not_admissible_by_reason": dict(sorted(inexact.items())),
        },
        "totals": {
            "matched": len(routed) + len(kept),
            "overrides": len(routed),
            "overrides_time_exact": len(exact),
            "overrides_time_island": len(routed) - len(exact),
            "kept_generated": len(kept),
            "kept_by_reason": dict(sorted(reason_counts.items())),
        },
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=1, sort_keys=True)
                                 + "\n", encoding="utf-8")

    print(f"overridegen: {len(routed)}/{len(routed) + len(kept)} matched "
          f"entries composed as CARRIER-FREE CPUless overrides"
          + ("" if args.dry_run else f" ({written} body modules -> "
                                     f"{args.bodies_dir})"))
    print(f"VIRTUAL TIME: {len(exact)} override(s) carry an EXACT contract "
          f"(gate-admissible); {len(routed) - len(exact)} stay ISLAND-cost:")
    for reason, n in inexact.most_common():
        print(f"  {n:4d}  {reason}")
    for k in exact:
        vt = overrides[k]["virtual_time"]
        print(f"        {k}  cost={vt.get('cost')}")
    print(f"kept on the GENERATED body: {len(kept)} -- by reason:")
    for reason, n in reason_counts.most_common():
        print(f"  {n:4d}  {reason}")
    print(f"overrides contract: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
