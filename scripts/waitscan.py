"""waitscan — mechanical enumeration of SIMANTW's env-wait loops (boundary heads).

The owner directive that created this (docs/run_status.md cont.228): stop
discovering env-waits one MAX_ITERATIONS crash at a time — the class is
mechanically enumerable from the recovery IR.  A candidate is

    a loop (backward jcc/jmp inside one function) whose body reaches a
    POLLING-CLASS api call — GetTickCount / GetAsyncKeyState / GetKeyState /
    GetCursorPos / PeekMessage — directly or through a small call closure
    (<= 2 intermediate census functions: the observed shapes are
    _WaitedEnough -> _TickCount -> GetTickCount and the
    _win_GetEvent -> PeekMessage pump).

Each candidate's LOOP HEAD (the first emit-legal instruction of the loop
interval: seq/call/call_far/call_ind/int — never a control transfer) is
derived, and the candidate is CLASSIFIED, conservatively and honestly:

* ``wait``   — every call inside the loop interval targets a WAIT PRIMITIVE
  (below) or is itself a polling api site: the loop makes no progress except
  through the wall clock / host input; PARKED (the derived facts file).
* ``mixed``  — the loop also calls anything else (real per-iteration work
  possible: frame loops, sim loops, render loops — a loop merely READING the
  tick count may be timestamping, not waiting).  REPORTED, never parked
  mechanically — promotion needs evidence and goes to the hand-curated
  simant/facts/boundary_heads.txt.

WAIT PRIMITIVES are the game's wait/pump framework, two tiers:

* mechanically derived — the fixpoint of functions whose whole body's api
  effects are all polling-class and whose every call targets another member
  (the tick/input leaf helpers: _TickCount, _WaitedEnough, ...);
* curated by evidence (CURATED_PRIMITIVES) — the SIMTWO idle pump
  (_win_GetEvent/_win_Events/_win_FlushEvents: they Translate/Dispatch
  pending messages WHILE waiting, which is api surface outside the poll set,
  so the mechanical tier honestly refuses them) and SIMANT's dialog-wait
  family (_Dialog*Wait*/Abort helpers, _myDelay, _myButton) — the live
  crash set (crash_214204/214734/220104 + the headless sweep) all spin
  through exactly these.

Output:
* ``simant/facts/boundary_heads_derived.txt`` — the poll-only heads, one per
  line (NE_SEG:HEX_OFFSET), each with its mechanical evidence comment.
  scripts/irgen.py reads it alongside the hand-curated file.
* ``artifacts/wait_report.json`` — the full census (every candidate, both
  classes, with loop interval, calls, poll paths) — the review artifact.

    python scripts/waitscan.py [--ir artifacts/recovery_ir.json] [--check]

``--check`` re-derives and fails (exit 2) if the committed derived facts file
does not match — the freshness pin (same contract as dispatchgen --check).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

DEFAULT_IR = REPO_ROOT / "artifacts" / "recovery_ir.json"
DERIVED = REPO_ROOT / "simant" / "facts" / "boundary_heads_derived.txt"
REPORT = REPO_ROOT / "artifacts" / "wait_report.json"

#: The polling-class API surface (the env-wait predicate's leaves).  These
#: are the calls a wait loop spins on; each is served in one hooked step and
#: makes no progress the loop could wait for except through the WALL clock /
#: host input — exactly what an interactive host must be allowed to advance
#: between passes (the boundary park).
POLL_APIS = {
    "api:USER.13:GetTickCount",
    "api:USER.249:GetAsyncKeyState",
    "api:USER.106:GetKeyState",
    "api:USER.17:GetCursorPos",
    "api:USER.109:PeekMessage",
}
#: Call-closure depth: loop -> callee [-> callee] -> poll api.  2 covers the
#: observed shapes (_WaitedEnough -> _TickCount -> GetTickCount); anything
#: deeper is out of the mechanical predicate on purpose (honesty over reach).
MAX_DEPTH = 2

#: Evidence-curated wait primitives, by .SYM symbol (see module docstring).
#: Members' bodies pump/poll while waiting; a loop whose every call lands
#: here waits, it does not work.
CURATED_PRIMITIVES = {
    "_win_GetEvent", "_win_Events", "_win_FlushEvents",   # the SIMTWO pump
    "_DialogClearWait", "_DialogAbort", "_DialogAbortOrCont",
    "_DialogWaitInit",                                    # dialog-wait family
    "_myDelay", "_myButton", "_StillDown",                # input/delay waits
}

ALLOWED_HEAD_KINDS = {"seq", "call", "call_far", "call_ind", "int"}


def load_ir(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def poll_reach(functions: dict) -> dict[str, int]:
    """entry -> minimal call depth at which the function reaches a poll api
    (0 = a poll site in its own body).  Bounded by MAX_DEPTH."""
    reach: dict[str, int] = {}
    for entry, rec in functions.items():
        if not rec.get("liftable"):
            continue
        for blk in rec.get("blocks", ()):
            if any(i.get("platform_effect") in POLL_APIS
                   for i in blk["instructions"]):
                reach[entry] = 0
                break
    for depth in (1, 2):
        if depth > MAX_DEPTH:
            break
        for entry, rec in functions.items():
            if entry in reach or not rec.get("liftable"):
                continue
            cs = entry.split(":")[0]
            callees = [f"{cs}:{t}" for t in rec.get("calls_near", ())]
            callees += [f"{s}:{o}" for s, o in rec.get("calls_far", ())]
            if any(reach.get(c, 99) <= depth - 1 for c in callees):
                reach[entry] = depth
    return reach


def _callees(rec: dict, cs: str) -> list[str]:
    return ([f"{cs}:{t}" for t in rec.get("calls_near", ())]
            + [f"{s}:{o}" for s, o in rec.get("calls_far", ())])


def pure_helpers(functions: dict) -> set[str]:
    """Fixpoint of effect-free computation helpers: no platform effects
    anywhere in the body and every census callee itself pure (the CRT math
    tier — __aFuldiv under _TickCount was the found case).  Used ONLY to
    unblock the wait-primitive derivation; a LOOP calling a pure helper
    still classifies mixed (an effect-free function may mutate game state —
    treating it neutral in loop classification would over-park sim loops)."""
    pure: set[str] = set()
    changed = True
    while changed:
        changed = False
        for entry, rec in functions.items():
            if entry in pure or not rec.get("liftable"):
                continue
            if any(i.get("platform_effect")
                   for b in rec.get("blocks", ())
                   for i in b["instructions"]):
                continue
            cs = entry.split(":")[0]
            callees = [c for c in _callees(rec, cs) if c in functions]
            if any(c not in pure for c in callees):
                continue
            pure.add(entry)
            changed = True
    return pure


def wait_primitives(functions: dict) -> dict[str, str]:
    """entry -> tier ('derived'|'curated') for every wait primitive.

    Derived tier: fixpoint of liftable functions whose body's api effects
    are ALL polling-class and whose every call targets another member or a
    pure computation helper (the tick/input leaf helpers).  Curated tier:
    CURATED_PRIMITIVES by symbol.
    """
    pure = pure_helpers(functions)
    prims: dict[str, str] = {}
    for entry, rec in functions.items():
        if rec.get("symbol") in CURATED_PRIMITIVES and rec.get("liftable"):
            prims[entry] = "curated"
    changed = True
    while changed:
        changed = False
        for entry, rec in functions.items():
            if entry in prims or not rec.get("liftable"):
                continue
            effects = [i.get("platform_effect")
                       for b in rec.get("blocks", ())
                       for i in b["instructions"] if i.get("platform_effect")]
            if any(e not in POLL_APIS for e in effects):
                continue
            cs = entry.split(":")[0]
            # api far calls carry their effect on the call instruction, so a
            # callee that is the poll thunk is already covered by `effects`;
            # every census callee must itself be a primitive (or a pure
            # computation helper — neutral).
            census_callees = [c for c in _callees(rec, cs)
                              if c in functions and c not in pure]
            if any(c not in prims for c in census_callees):
                continue
            if not effects and not any(c in prims for c in census_callees):
                continue                         # reaches no poll: not a wait
            prims[entry] = "derived"
            changed = True
    return prims


def scan(doc: dict) -> list[dict]:
    """Every loop candidate, classified.  A loop is the ip interval
    [back-edge target, back-edge source] inside one function (the MSC shapes
    here are single-interval loops); intervals of one function that share a
    head are merged."""
    functions = doc["functions"]
    reach = poll_reach(functions)
    prims = wait_primitives(functions)
    out: list[dict] = []
    seen_heads: set[str] = set()         # global: overlapping case_* records
    for entry in sorted(functions):
        rec = functions[entry]
        if not rec.get("liftable"):
            continue
        cs = entry.split(":")[0]
        insts = {}                       # ip -> record, function-wide
        for blk in rec.get("blocks", ()):
            for i in blk["instructions"]:
                insts[int(i["ip"], 16)] = i
        edges = [(int(i.get("target"), 16), ip)
                 for ip, i in insts.items()
                 if i["kind"] in ("jcc", "jmp") and i.get("target")
                 and int(i["target"], 16) < ip]
        for lo, hi in sorted(edges):
            body = [insts[ip] for ip in sorted(insts) if lo <= ip <= hi]
            if not body:
                continue
            polls, prim_calls, poll_calls, work_calls = [], [], [], []
            for i in body:
                eff = i.get("platform_effect")
                if eff in POLL_APIS:
                    polls.append(f"{i['ip']}:{eff}")
                    continue
                if i["kind"] in ("call", "call_far") and (
                        i.get("target") or i.get("far_target")):
                    key = (f"{cs}:{i['target']}" if i.get("target")
                           else ":".join(i["far_target"]))
                    tgt = functions.get(key)
                    name = tgt.get("symbol", key) if tgt else key
                    if key in prims:
                        prim_calls.append(f"{i['ip']}->{name}"
                                          f"({prims[key]})")
                    elif eff and eff.startswith("api:"):
                        work_calls.append(f"{i['ip']}->{eff[4:]}")
                    elif reach.get(key, 99) <= MAX_DEPTH:
                        poll_calls.append(f"{i['ip']}->{name}"
                                          f"(reach{reach[key]})")
                    else:
                        work_calls.append(f"{i['ip']}->{name}")
            if not polls and not prim_calls and not poll_calls:
                continue                          # not a wait candidate
            # loop head: first emit-legal instruction of the interval
            head = None
            for ip in sorted(ip for ip in insts if lo <= ip <= hi):
                if insts[ip]["kind"] in ALLOWED_HEAD_KINDS:
                    head = ip
                    break
            if head is None:
                continue
            head_key = f"{cs}:{head:04X}"
            if head_key in seen_heads:
                continue
            seen_heads.add(head_key)
            # WAIT: nothing in the loop but polls and wait primitives.  A
            # poll_calls entry (reaches a poll but is NOT a primitive) is
            # treated as work — a sim routine that reads the clock is
            # timestamping, not waiting (the conservative direction).
            is_wait = not work_calls and not poll_calls and (
                polls or prim_calls)
            out.append({
                "entry": entry,
                "symbol": rec.get("symbol", "?"),
                "ne_seg": rec["ne_seg"],
                "loop": f"{lo:04X}..{hi:04X}",
                "head": head_key,
                "head_ne": f"{rec['ne_seg']}:{head:04X}",
                "polls": polls,
                "prim_calls": prim_calls,
                "poll_calls": poll_calls,
                "work_calls": work_calls,
                "class": "wait" if is_wait else "mixed",
            })
    return out


def derived_text(cands: list[dict]) -> str:
    lines = [
        "# boundary_heads_derived.txt -- GENERATED by scripts/waitscan.py; do",
        "# not hand-edit (hand-curated heads live in boundary_heads.txt).",
        "# Every WAIT loop of the recovery IR: a loop whose body contains",
        "# nothing but polling-class api calls (GetTickCount /",
        "# GetAsyncKeyState / GetKeyState / GetCursorPos / PeekMessage) and",
        "# calls into the WAIT-PRIMITIVE set (tick/input leaf helpers derived",
        "# by fixpoint + the evidence-curated pump/dialog-wait family) -- a",
        "# pure env-wait, parked interactively at its loop head.  Mixed loops",
        "# (real work per iteration) are REPORTED in artifacts/",
        "# wait_report.json and promoted by hand with evidence only.",
        "# Freshness pinned by scripts/waitscan.py --check (suite-held).",
        "",
    ]
    for c in cands:
        if c["class"] != "wait":
            continue
        via = "; ".join(c["polls"] + c["prim_calls"])
        lines.append(f"# {c['symbol']} ({c['entry']}) loop {c['loop']} "
                     f"waits via: {via}")
        lines.append(c["head_ne"])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--check", action="store_true",
                    help="verify the committed derived facts match a fresh "
                         "derivation (exit 2 on drift)")
    args = ap.parse_args(argv)

    doc = load_ir(Path(args.ir))
    cands = scan(doc)
    pure = [c for c in cands if c["class"] == "wait"]
    mixed = [c for c in cands if c["class"] == "mixed"]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(
        {"provenance": doc["provenance"], "poll_apis": sorted(POLL_APIS),
         "max_depth": MAX_DEPTH,
         "wait_primitives": {e: t for e, t
                             in sorted(wait_primitives(doc["functions"])
                                       .items())},
         "candidates": cands},
        indent=1, sort_keys=True) + "\n", encoding="utf-8")

    text = derived_text(cands)
    if args.check:
        current = DERIVED.read_text(encoding="utf-8") if DERIVED.exists() else ""
        if current != text:
            print("waitscan --check: DERIVED FACTS STALE -- rerun "
                  "scripts/waitscan.py (and the regen chain)", file=sys.stderr)
            return 2
        print(f"waitscan --check: {len(pure)} derived head(s) fresh")
        return 0
    DERIVED.write_text(text, encoding="utf-8")

    print(f"wait candidates: {len(cands)} loops in "
          f"{len({c['entry'] for c in cands})} functions "
          f"({len(pure)} wait -> {DERIVED.name}, {len(mixed)} mixed -> "
          f"report only)")
    for c in pure:
        print(f"  PARK  {c['head']} {c['symbol']} loop {c['loop']} "
              f"[{'; '.join(c['polls'] + c['prim_calls'])}]")
    for c in mixed:
        work = c["work_calls"] + c["poll_calls"]
        print(f"  mixed {c['head']} {c['symbol']} loop {c['loop']} "
              f"work: {', '.join(work[:6])}"
              f"{' ...' if len(work) > 6 else ''}")
    print(f"report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
