"""liftlink — structurally link SIMANTW's VMless graph + the capability report.

The M2 linking pass of the DOS_RE 2.0 adoption: runs dos_re's batch linker
(``tools/liftlink.py --from-ir``) over the emitted corpus
(scripts/liftemit.py), turning interpreter-mediated near/far CALLs between
census entries into direct Python calls (module-level ``LINKS`` tables bound
at install time through the emit dir's ``graph_manifest.json``), then
classifies EVERY edge the structural linker did not resolve into a
fine-grained capability class — the discovery deliverable
(``artifacts/capability_report.json``):

    api:<MODULE>.<ord>:<Name>   far call into the import-thunk segment
                                (serviced by the Python API layer in ONE
                                hooked step; NEVER a link candidate)
    int:<tag>                   raw software interrupt (INT 21h DOS file I/O
                                in the MSC CRT; serviced in Python)
    keep-interpreted-fact:*     callee pinned interpreted by simant/facts/
                                keep_interpreted.txt (the x87/_DoInt3
                                census frontier), subclassed by refusal
    exit-shape:near|far         callee mixes exits (tail exit / retf+ret) —
                                stays emulate_call through the hook dispatch
    not-a-census-entry          static call target that is no .SYM entry
    indirect-near-call /        call through a register/memory pointer —
    indirect-far-call           target unknowable statically, resolved at
                                run time by the hook dispatch
    callback-entry:*            graph roots the OS calls INTO (wndproc /
                                timer proc, classified from a booted
                                snapshot's registries)
    root-unreferenced           statically uncalled entries with no runtime
                                registration found (dialog/enum procs,
                                GetProcAddress targets, dead code)

    python scripts/liftlink.py [--ir artifacts/recovery_ir.json]
                               [--emit-dir simant/lifted/graph]
                               [--snapshot artifacts/snapshots/snap_185520]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401

from win16.loader import THUNK_SEG  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from liftemit import DEFAULT_EMIT_DIR, DEFAULT_IR, load_dosre_tool  # noqa: E402

DEFAULT_REPORT = REPO_ROOT / "artifacts" / "capability_report.json"
DEFAULT_LINK_REPORT = REPO_ROOT / "artifacts" / "link_report.json"


def _label(rec: dict) -> str:
    return f"{rec['entry']} {rec.get('symbol', '?')}"


def classify_unresolved(doc: dict, link_report: dict,
                        callback_procs: dict[str, str]) -> dict:
    """Every unresolved/unlinked edge, in a specific class — never one
    generic bucket.  Returns the ``unresolved`` section of the report."""
    functions = doc["functions"]
    keep = set(doc["facts_applied"].get("keep_interpreted", ()))

    api_calls: dict[str, list[str]] = defaultdict(list)
    int_calls: dict[str, list[str]] = defaultdict(list)
    ind_near: list[str] = []
    ind_far: list[str] = []
    for entry in sorted(functions):
        rec = functions[entry]
        if not rec.get("liftable"):
            continue
        for blk in rec.get("blocks", ()):
            for inst in blk["instructions"]:
                effect = inst.get("platform_effect")
                site = f"{_label(rec)} @{inst['ip']}"
                if effect and effect.startswith("api:"):
                    api_calls[effect[4:]].append(site)
                elif effect:
                    int_calls[effect].append(site)
                elif inst["kind"] == "call_ind":
                    (ind_far if "far" in inst["mnemonic"]
                     else ind_near).append(site)

    # Blocked structural edges, subclassed.  Far edges into the import-thunk
    # segment are the API surface (already counted above per call SITE);
    # they are excluded from not-a-census-entry rather than double-reported.
    blocked_classes: dict[str, list[str]] = defaultdict(list)
    for caller_key, callee_key, reason in link_report.get("blocked", ()):
        callee_cs = int(callee_key.split(":")[0], 16)
        caller = functions.get(caller_key, {})
        desc = (f"{caller_key} {caller.get('symbol', '?')} -> {callee_key} "
                f"{functions.get(callee_key, {}).get('symbol', '')}").rstrip()
        if reason == "not-an-entry":
            if callee_cs == THUNK_SEG:
                continue                      # the api:* class covers it
            blocked_classes["not-a-census-entry"].append(desc)
        elif reason == "callee-not-liftable":
            callee = functions.get(callee_key, {})
            refusals = ",".join(sorted({r["reason"]
                                        for r in callee.get("refusals", ())}))
            sub = ("keep-interpreted-fact" if callee_key in keep
                   else "callee-not-liftable")
            blocked_classes[f"{sub}:{refusals or 'unknown'}"].append(desc)
        elif reason == "exit-shape":
            # Near CALLs into a pure-retf callee never land here any more:
            # dos_re's linker links the `push cs` idiom (MSC's same-segment
            # far call) directly, and reports the rare non-idiom case under
            # its own 'retf-callee-no-push-cs-idiom' reason (the final else).
            callee = functions.get(callee_key, {})
            exits = sorted(set(callee.get("exits", ())))
            caller_cs, _ = caller_key.split(":")
            callee_cs_s, callee_off = callee_key.split(":")
            is_near_edge = (caller_cs == callee_cs_s and
                            callee_off in caller.get("calls_near", ()))
            shape = "near" if is_near_edge else "far"
            blocked_classes[
                f"exit-shape:{shape}:{'+'.join(exits)}"].append(desc)
        else:
            blocked_classes[reason].append(desc)

    # Graph roots: entries no static near/far edge reaches — the OS (or an
    # indirect call) enters them.  Classified against the booted snapshot's
    # callback registries where possible.
    static_callees: set[str] = set()
    for entry, rec in functions.items():
        cs = entry.split(":")[0]
        for tgt in rec.get("calls_near", ()):
            static_callees.add(f"{cs}:{tgt}")
        for seg, off in rec.get("calls_far", ()):
            static_callees.add(f"{seg}:{off}")
    roots: dict[str, list[str]] = defaultdict(list)
    for entry in sorted(functions):
        if entry in static_callees:
            continue
        rec = functions[entry]
        kind = callback_procs.get(entry, "root-unreferenced")
        roots[kind].append(_label(rec))

    def _section(mapping):
        return {k: {"count": len(v), "sites": v}
                for k, v in sorted(mapping.items())}

    return {
        "api": _section(api_calls),
        "int": _section(int_calls),
        "indirect-near-call": {"count": len(ind_near), "sites": ind_near},
        "indirect-far-call": {"count": len(ind_far), "sites": ind_far},
        "blocked-edges": _section(blocked_classes),
        "entry-roots": _section(roots),
    }


def runtime_callback_procs(snapshot: str | None) -> dict[str, str]:
    """entry -> callback class, read from a booted snapshot's registries
    (window classes' wndprocs, SetTimer timer procs).  Best-effort: absent
    snapshot means roots stay 'root-unreferenced'."""
    if not snapshot:
        return {}
    if not Path(snapshot).is_dir():
        print(f"[liftlink] snapshot {snapshot} not present -- entry roots "
              f"stay 'root-unreferenced' (runtime classification skipped)")
        return {}
    from simant.runtime import create_machine
    from win16.vmsnap import load_snapshot
    machine = load_snapshot(snapshot, create_machine)
    sysobj = machine.api.services["system"]
    wndprocs: dict[str, list[str]] = defaultdict(list)
    for name, cls in sorted(sysobj.classes.items()):
        seg, off = cls.wndproc
        wndprocs[f"{seg:04X}:{off:04X}"].append(name)
    procs = {entry: f"callback-entry:wndproc({','.join(names)})"
             for entry, names in wndprocs.items()}
    for (hwnd, tid), ptr in sorted(sysobj.timer_procs.items()):
        if ptr:
            procs[f"{(ptr >> 16) & 0xFFFF:04X}:{ptr & 0xFFFF:04X}"] = \
                f"callback-entry:timerproc(id={tid})"
    return procs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--emit-dir", default=str(DEFAULT_EMIT_DIR))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--link-report", default=str(DEFAULT_LINK_REPORT))
    ap.add_argument("--snapshot", default="artifacts/snapshots/snap_185520",
                    help="booted snapshot whose callback registries classify "
                         "graph roots ('' skips runtime classification)")
    ap.add_argument("--skip-link", action="store_true",
                    help="reuse an existing --link-report (classification "
                         "only; the emitted modules are NOT re-linked)")
    args = ap.parse_args(argv)

    # 1. The structural link (dos_re's batch linker, unforked; naming comes
    #    from the emit dir's graph_manifest.json).
    if not args.skip_link:
        liftlink = load_dosre_tool("liftlink")
        rc = liftlink.main(["--from-ir", args.ir,
                            "--emit-dir", args.emit_dir,
                            "--json", args.link_report])
        if rc != 0:
            return rc

    doc = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    link_report = json.loads(Path(args.link_report).read_text(encoding="utf-8"))

    callback_procs = runtime_callback_procs(args.snapshot)
    unresolved = classify_unresolved(doc, link_report, callback_procs)

    totals = link_report.get("totals", {})
    report = {
        "provenance": doc["provenance"],
        "link": {
            "near_edges": len(link_report.get("edges", ())),
            "far_edges": len(link_report.get("far_edges", ())),
            "callers_reemitted": totals.get("callers_reemitted"),
            "emulate_call_before": totals.get("emulate_call_before"),
            "emulate_call_after": totals.get("emulate_call_after"),
            "blocked_by_reason": totals.get("blocked_by_reason", {}),
        },
        "unresolved": unresolved,
    }
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")

    # 2. The human summary.
    print(f"\n=== capability report ({out}) ===")
    print(f"linked: {report['link']['near_edges']} near + "
          f"{report['link']['far_edges']} far edges into "
          f"{report['link']['callers_reemitted']} re-emitted callers "
          f"(emulate_call {report['link']['emulate_call_before']} -> "
          f"{report['link']['emulate_call_after']})")

    api = unresolved["api"]
    n_api_sites = sum(v["count"] for v in api.values())
    by_module = Counter()
    for label, v in api.items():
        by_module[label.split(".", 1)[0]] += v["count"]
    print(f"api edges: {n_api_sites} call sites over {len(api)} imports "
          f"({', '.join(f'{m}={n}' for m, n in by_module.most_common())})")
    top = sorted(api.items(), key=lambda kv: -kv[1]["count"])[:10]
    for label, v in top:
        print(f"   {v['count']:4d}  api:{label}")
    for section in ("int", "blocked-edges", "entry-roots"):
        print(f"{section}:")
        for label, v in sorted(unresolved[section].items(),
                               key=lambda kv: -kv[1]["count"]):
            print(f"   {v['count']:4d}  {label}")
    print(f"indirect calls: near={unresolved['indirect-near-call']['count']} "
          f"far={unresolved['indirect-far-call']['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
