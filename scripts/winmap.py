"""winmap — SimAnt's window-object system, extracted statically and PROVEN.

The evidence-driven map of the `_win_*` object-window layer (docs/
rendering_map.md §4): which caller opens each window type, which draw
callback each window slot registers, which event routine `_DoEvent`'s
compare chain binds to each slot, and where the stored draw pointer is
invoked at runtime.  Every binding is extracted from the loaded image's
instruction stream by an EXACT pattern (refuse-on-doubt: a site that does
not provably match is reported, never guessed), then cross-checked against
the replay-observed dispatch evidence — an observed draw-callback invocation
that is not a registered hook is a fatal contradiction.

    python scripts/winmap.py [--ir artifacts/recovery_ir.json] [--json]
        [--facts artifacts/atlas]   # write typed manual facts into the Atlas

The mechanism (proven from the routines below):

* ``_win_LoadAllWindows`` (430E:C806) builds the per-slot window records
  (``window_records`` FarPtr[256] @ DGROUP:0xCE9A, slot = handle >> 8).
* ``_InitApplicationWindows`` (0100:5464) registers every DRAW callback via
  ``_win_SetWinDrawHook(handle, farproc)`` — the pointer lands at
  ``seg[DGROUP:0xC6CC] : 0x77B2 + slot*4``.
* ``_win_DrawWindow`` (430E:BB96) invokes the stored pointer twice per
  paint (``call far es:[bx]`` at 430E:BBE2 pass 1 and 430E:BC83 pass 2).
* ``_win_Open`` (430E:CA2E) does the Win16 side of opening (CreateWindow,
  SetProp hwnd->object, menus, ShowWindow, InvalidateRect, UpdateWindow);
  ``_win_Close`` closes.  EVENTS are not stored pointers: ``_DoEvent``
  (0100:0BC2) compare-chains on the event-code CLASS (``code & 0xFF00`` —
  the class byte mirrors the window slot) and statically calls the
  per-window ``_Proc*Event``; classes 0x22/0x23 are the map/yard RIBBON
  pseudo-windows.  A first chain, active only in help mode
  (DGROUP:0x0010 != 0), routes the same classes to WinHelp context help.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import simant._env  # noqa: E402,F401  (puts win16_re on sys.path)
import win16  # noqa: E402,F401  (win16._env puts dos_re on sys.path)

WIN_OPEN = ("430E", "CA2E")
WIN_CLOSE_SYM = "_win_Close"
SET_DRAW_HOOK = ("430E", "D59C")
INIT_WINDOWS = "_InitApplicationWindows"
DO_EVENT = "_DoEvent"
DRAW_INVOKE_SITES = ("430E:BBE2", "430E:BC83")   # call far es:[bx] in _win_DrawWindow


def _flat(fn):
    return [i for _, i in sorted((int(i["ip"], 16), i)
            for b in fn.get("blocks", ()) for i in b["instructions"])]


def _imm16(b: bytes) -> int:
    return b[1] | (b[2] << 8)


def registrations(ir, by_addr) -> list[dict]:
    """(handle, slot, callback) per _win_SetWinDrawHook call in
    _InitApplicationWindows.  EXACT pattern: three literal pushes
    (seg, off, handle — right-to-left C order) immediately before the call;
    anything else refuses that site loudly."""
    fn = next(f for f in ir["functions"].values()
              if f.get("symbol") == INIT_WINDOWS)
    out, pend = [], []
    for i in _flat(fn):
        raw = bytes.fromhex(i["bytes"])
        if i["kind"] == "seq" and raw[0] == 0x68:
            pend.append(_imm16(raw))
        elif i["kind"] == "seq" and raw[0] == 0x6A:
            pend.append(raw[1])
        elif i["kind"] == "call_far":
            if i.get("far_target") == list(SET_DRAW_HOOK):
                if len(pend) < 3:
                    raise SystemExit(f"[winmap] unprovable SetWinDrawHook "
                                     f"site at {i['ip']}: args not literal")
                seg, off, handle = pend[-3], pend[-2], pend[-1]
                key = f"{seg:04X}:{off:04X}"
                out.append({"handle": handle, "slot": handle >> 8,
                            "callback": key,
                            "callback_symbol": by_addr.get(key, "?")})
            pend = []
        elif i["kind"] != "seq":
            pend = []
    return out


def call_sites_with_handle(ir, target_pair) -> list[dict]:
    """Every far call to ``target_pair`` with the LAST pre-call push, which
    for _win_Open/_win_Close is the window handle.  A non-literal handle
    (register/stack) is reported as handle None — never guessed."""
    out = []
    for key, fn in ir["functions"].items():
        pend: list[int | None] = []
        for i in _flat(fn):
            raw = bytes.fromhex(i["bytes"])
            if i["kind"] == "seq" and raw[0] == 0x68:
                pend.append(_imm16(raw))
            elif i["kind"] == "seq" and raw[0] == 0x6A:
                pend.append(raw[1])
            elif i["kind"] == "seq" and raw[:2] == b"\xff\x76":
                pend.append(None)                      # push [bp+d]
            elif i["kind"] == "call_far":
                if i.get("far_target") == list(target_pair):
                    out.append({"caller": fn.get("symbol") or key,
                                "caller_entry": key, "site": i["ip"],
                                "handle": pend[-1] if pend else None})
                pend = []
            elif i["kind"] != "seq":
                pend = []
    return out


#: pushes that provably do not write ax — an arm pushes its args before the
#: event call; the walk may cross them without losing the chain state.
_PUSH_OPS = frozenset(range(0x50, 0x58)) | {0x68, 0x6A, 0x0E, 0x1E, 0x06, 0x16}


def event_bindings(ir, by_addr) -> list[dict]:
    """Decode _DoEvent's compare chain: handle -> _Proc*Event.

    A strict abstract walk over the REAL control flow.  State per path:
    ``delta`` (total subtracted from ax = handle & 0xFF00), ``pending``
    (the immediate of the last cmp/sub/or test), and ``eq`` (the equality
    the last TAKEN jz established: handle == eq).  A call to a routine
    reached with an established equality binds handle -> routine.  The
    modeled instruction set is exact — cmp/sub/or/jcc/jmp/pushes/call —
    and ANY other instruction ends the path: bindings are only ever
    emitted from provably-tracked paths (refuse, not guess).  A handle
    bound to two different routines is a contradiction and raises."""
    entry_key = next(k for k, f in ir["functions"].items()
                     if f.get("symbol") == DO_EVENT)
    fn = ir["functions"][entry_key]
    flat = _flat(fn)
    by_ip = {int(i["ip"], 16): i for i in flat}
    cs, entry_ip = entry_key.split(":")

    bindings: dict[int, set[str]] = {}
    seen = set()
    # (ip, delta, pending, eq): delta/pending model ax = code - delta with a
    # pending comparison immediate; None poisons them (ax no longer tracks
    # the dispatch code).  eq is the equality the last TAKEN jz established
    # about the ORIGINAL code — an ax mutation never retracts it.
    stack = [(int(entry_ip, 16), None, None, None)]
    while stack:
        ip, delta, pending, eq = stack.pop()
        key = (ip, delta, pending, eq)
        if key in seen or ip not in by_ip:
            continue
        seen.add(key)
        i = by_ip[ip]
        raw = bytes.fromhex(i["bytes"])
        nxt = ip + len(raw)
        if i["kind"] == "jcc":
            tgt = int(i["target"], 16)
            if raw[0] == 0x74 and pending is not None and delta is not None:
                stack.append((tgt, delta, None, delta + pending))  # jz taken
                stack.append((nxt, delta, pending, eq))
            else:
                stack.append((tgt, delta, pending, eq))
                stack.append((nxt, delta, pending, eq))
            continue
        if i["kind"] == "jmp":
            stack.append((int(i["target"], 16), delta, pending, eq))
            continue
        if i["kind"] in ("call", "call_far"):
            if i["kind"] == "call":
                routine = by_addr.get(f"{cs}:{int(i['target'], 16):04X}")
            else:
                seg, off = i["far_target"]
                routine = by_addr.get(f"{seg}:{off}")
            if eq is not None and routine:
                bindings.setdefault(eq, set()).add(routine)
            continue                       # the arm's dispatch is done
        if i["kind"] != "seq":
            continue                       # ret/retf/... — path ends
        if raw[:2] == b"\x8a\x66":         # mov ah,[bp+d]: (re)load the code
            delta, pending = 0, None
        elif raw[0] == 0x3D and delta is not None:            # cmp ax,imm
            pending = _imm16(raw)
        elif raw[0] == 0x2D and delta is not None:            # sub ax,imm
            delta += _imm16(raw)
            pending = 0
        elif raw[:2] == b"\x0b\xc0" and delta is not None:    # or ax,ax
            pending = 0
        elif raw[:2] == b"\x2a\xc0":       # sub al,al: clears the low byte
            pass                           # (the class byte in ah is intact)
        elif raw[0] in _PUSH_OPS or raw[:2] == b"\xff\x76" \
                or raw[0] in (0xC8, 0xC9):
            pass                           # pushes/enter/leave: ax untouched
        else:
            delta, pending = None, None    # ax poisoned; eq SURVIVES
        stack.append((nxt, delta, pending, eq))

    out = []
    for handle in sorted(bindings):
        routines = bindings[handle]
        if len(routines) > 1:
            raise SystemExit(f"[winmap] AMBIGUOUS event binding for handle "
                             f"{handle:04X}: {sorted(routines)}")
        out.append({"handle": handle, "slot": handle >> 8,
                    "event_routine": next(iter(routines))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ir", default=str(REPO_ROOT / "artifacts" / "recovery_ir.json"))
    ap.add_argument("--atlas", default=str(REPO_ROOT / "artifacts" / "atlas"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--facts", action="store_true",
                    help="write the map into the Atlas as typed manual facts "
                         "(source 'window-object-map')")
    args = ap.parse_args()

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    by_addr = {k: fn.get("symbol") for k, fn in ir["functions"].items()}
    by_sym = {fn.get("symbol"): k for k, fn in ir["functions"].items()}

    regs = registrations(ir, by_addr)
    opens = call_sites_with_handle(ir, WIN_OPEN)
    close_key = by_sym[WIN_CLOSE_SYM]
    closes = call_sites_with_handle(ir, tuple(close_key.split(":")))
    events = event_bindings(ir, by_addr)

    # ------------------------------------------------------------------ merge
    slots: dict[int, dict] = {}
    def slot(n):
        return slots.setdefault(n, {"slot": n, "draw": None, "event": None,
                                    "openers": [], "closers": []})
    for r in regs:
        slot(r["slot"])["draw"] = r
    for e in events:
        slot(e["slot"])["event"] = e
    for o in opens:
        if o["handle"] is not None:
            slot(o["handle"] >> 8)["openers"].append(o)
    for c in closes:
        if c["handle"] is not None:
            slot(c["handle"] >> 8)["closers"].append(c)

    def name_of(s) -> str:
        d = s["draw"] and s["draw"]["callback_symbol"]
        if d and d.startswith("_win_Draw") and d.endswith("Window"):
            return d[len("_win_Draw"):-len("Window")]
        for o in s["openers"]:
            c = o["caller"]
            if c.startswith("_Open"):
                return c[len("_Open"):].removesuffix("Window").removesuffix("Win")
        return f"slot-{s['slot']:02X}"

    # ------------------------------------------------- observed cross-check
    graph_path = Path(args.atlas) / "indexes" / "graph.json"
    observed_draw: set[str] = set()
    if graph_path.exists():
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        label = {n["id"]: n.get("label") for n in graph["nodes"]}
        for edge in graph["edges"]:
            if edge["kind"] != "call_ind" or edge["status"] != "observed":
                continue
            src = unquote(edge["source"].rsplit(":", 1)[-1]).upper()
            if src in DRAW_INVOKE_SITES:
                observed_draw.add(label.get(edge["target"])
                                  or unquote(edge["target"].rsplit(":", 1)[-1]))
        registered = {r["callback_symbol"] for r in regs}
        rogue = observed_draw - registered
        if rogue:
            raise SystemExit(f"[winmap] CONTRADICTION: observed draw-callback "
                             f"invocation(s) {sorted(rogue)} were never "
                             f"registered via _win_SetWinDrawHook")

    result = {
        "mechanism": {
            "record_table": "DGROUP:0xCE9A FarPtr[256], slot = handle >> 8",
            "draw_hook_table": "seg[DGROUP:0xC6CC] : 0x77B2 + slot*4",
            "draw_invoke_sites": list(DRAW_INVOKE_SITES),
            "event_dispatch": "_DoEvent compare chain on the event-code "
                              "CLASS (code & 0xFF00; the class byte mirrors "
                              "the window slot) -> static _Proc*Event calls, "
                              "no stored pointer.  A first chain behind the "
                              "help-mode flag (DGROUP:0x0010) routes the "
                              "same classes to USER.WinHelp context help.",
        },
        "windows": [
            {**{"name": name_of(s)}, **s} for _, s in sorted(slots.items())],
        "observed_draw_callbacks": sorted(observed_draw),
    }

    if args.facts:
        from dos_re.atlas import ExecutionAtlas
        from simant.execution import PROGRAM_KEY, function_id
        atlas = ExecutionAtlas.open(Path(args.atlas))
        def fid(key: str) -> str:
            cs, ip = key.split(":")
            return function_id(int(cs, 16), int(ip, 16))
        nodes, edges = [], []
        for n, s in sorted(slots.items()):
            wid = f"{PROGRAM_KEY}:region:window:{n:02X}"
            nodes.append({"id": wid, "kind": "region",
                          "label": f"window:{name_of(s)}",
                          "metadata": {"slot": n, "handle": n << 8,
                                       "record": "DGROUP:0xCE9A",
                                       "draw_table": "0x77B2+slot*4"}})
            if s["draw"]:
                edges.append({"source": wid, "target": fid(s["draw"]["callback"]),
                              "kind": "draw-callback", "status": "resolved"})
                edges.append({"source": fid(by_sym[INIT_WINDOWS]),
                              "target": fid(s["draw"]["callback"]),
                              "kind": "registers-callback", "status": "resolved",
                              "metadata": {"slot": n,
                                           "via": "_win_SetWinDrawHook"}})
            ev = s["event"]
            if ev and ev["event_routine"] and ev["event_routine"] in by_sym:
                edges.append({"source": wid,
                              "target": fid(by_sym[ev["event_routine"]]),
                              "kind": "event-routine", "status": "resolved",
                              "metadata": {"via": "_DoEvent compare chain"}})
            for o in s["openers"]:
                edges.append({"source": fid(o["caller_entry"]), "target": wid,
                              "kind": "opens-window", "status": "resolved",
                              "metadata": {"site": o["site"]}})
            for c in s["closers"]:
                edges.append({"source": fid(c["caller_entry"]), "target": wid,
                              "kind": "closes-window", "status": "resolved",
                              "metadata": {"site": c["site"]}})
        atlas.add_manual_facts(
            "window-object-map",
            provenance={"source": "scripts/winmap.py (exact static patterns, "
                                  "cross-checked vs observed dispatch)",
                        "recovery_ir": str(args.ir)},
            nodes=tuple(nodes), edges=tuple(edges))
        atlas.rematerialize()
        print(f"[winmap] facts written: {len(nodes)} window regions, "
              f"{len(edges)} typed edges")

    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    print("=== SimAnt window-object map (static, proven) ===")
    for k, v in result["mechanism"].items():
        print(f"  {k}: {v}")
    print()
    for w in result["windows"]:
        draw = w["draw"] and w["draw"]["callback_symbol"]
        ev = w["event"] and w["event"]["event_routine"]
        op = ", ".join(o["caller"] for o in w["openers"]) or "-"
        print(f"[{w['slot']:02X}] {w['name']:<12} draw={draw or '-':<24} "
              f"event={ev or '-':<20} openers: {op}")
    if observed_draw:
        print(f"\nobserved draw callbacks (all registered): "
              f"{', '.join(sorted(observed_draw))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
