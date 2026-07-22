"""render_map — the SimAnt rendering subsystem, mapped from the Execution Atlas.

Evidence-driven, never guessed from visible output: every entry here is an
edge the Atlas holds from cited evidence (static Recovery IR api:* effect tags
+ replay-observed call/callback transfers).  The map answers "which original
procedure produces which kind of graphics, and how is it reached."

    python scripts/render_map.py [--atlas artifacts/atlas] [--json] [--observed]

Output: per rendering CATEGORY (DC lifecycle, invalidation, blit, GDI objects,
primitives, text, scroll, palette, window geometry, clip), the SIMANTW.SYM-
labelled functions that reach that category's Windows API boundary, and the
callback ENTRY POINTS (WndProcs / dialog / timer procs, from replay evidence)
that drive them — the roots of message-driven presentation.

``--observed`` restricts to functions the replay evidence actually executed
(the confirmed live rendering path) vs the static-reachable superset.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

#: Rendering API taxonomy — each Windows API name (as it appears in an
#: api:MODULE.ORD:Name boundary label) mapped to a rendering category.  Only
#: the drawing-relevant surface; anything not here is not a rendering boundary.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "dc-lifecycle": ("GetDC", "BeginPaint", "EndPaint", "ReleaseDC",
                     "CreateCompatibleDC", "DeleteDC", "SaveDC", "RestoreDC"),
    "invalidation": ("InvalidateRect", "InvalidateRgn", "UpdateWindow",
                     "ValidateRgn", "GetUpdateRgn"),
    "blit": ("BitBlt", "StretchBlt", "PatBlt", "SetDIBitsToDevice",
             "SetStretchBltMode", "CreateCompatibleBitmap"),
    "gdi-object": ("CreatePen", "CreateSolidBrush", "CreateFont",
                   "CreateRectRgn", "SelectObject", "DeleteObject",
                   "GetStockObject", "UnrealizeObject", "AddFontResource",
                   "RemoveFontResource"),
    "primitive": ("TextOut", "Polygon", "LineTo", "MoveTo",
                  "FillRect", "InvertRect", "Escape"),
    "text": ("SetTextColor", "SetBkColor", "SetBkMode", "SetTextAlign",
             "GetTextAlign", "GetTextExtent", "GetTextMetrics"),
    "scroll": ("ScrollWindow", "GetScrollPos", "SetScrollPos",
               "GetScrollRange", "SetScrollRange"),
    "palette": ("CreatePalette", "SelectPalette", "RealizePalette",
                "GetPaletteEntries", "GetSystemPaletteUse",
                "SetSystemPaletteUse", "GetSystemPaletteEntries",
                "GetNearestPaletteIndex"),
    "window-geom": ("GetClientRect", "GetWindowRect", "MoveWindow",
                    "SetWindowPos", "ShowWindow", "ClientToScreen",
                    "ScreenToClient", "InvertRect"),
    "clip": ("IntersectClipRect", "SelectClipRgn", "GetRgnBox",
             "RectInRegion", "SetMapMode"),
}

_API_CATEGORY = {api: cat for cat, apis in CATEGORIES.items() for api in apis}


def _api_name(boundary_label: str) -> str | None:
    """'api:GDI.1:SetBkColor' -> 'SetBkColor' (None if not an api boundary)."""
    if not boundary_label or not boundary_label.startswith("api:"):
        return None
    return boundary_label.rsplit(":", 1)[-1]


def _addr(identity: str) -> str:
    """The CS:IP tail of a function/point identity, unescaped."""
    return unquote(identity.rsplit(":", 1)[-1]).upper()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--atlas", default=str(REPO_ROOT / "artifacts" / "atlas"))
    ap.add_argument("--observed", action="store_true",
                    help="restrict to replay-executed functions")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    graph = json.loads(
        (Path(args.atlas) / "indexes" / "graph.json").read_text("utf-8"))
    label = {n["id"]: n.get("label") for n in graph["nodes"]}
    kind = {n["id"]: n["kind"] for n in graph["nodes"]}

    observed_fns: set[str] = set()
    if args.observed:
        cov = json.loads((Path(args.atlas) / "indexes" /
                          "replay_coverage.json").read_text("utf-8"))
        observed_fns = {row["function_id"] for row in cov.get("coverage", ())}

    # func identity -> {category -> set(api names)}
    producers: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    # api boundary edges
    for edge in graph["edges"]:
        tgt = label.get(edge["target"])
        api = _api_name(tgt) if tgt else None
        if api is None:
            continue
        cat = _API_CATEGORY.get(api)
        if cat is None:
            continue
        src = edge["source"]
        if kind.get(src) not in ("function", "execution-point"):
            continue
        if args.observed and src not in observed_fns:
            continue
        producers[src][cat].add(api)

    # callback entry points (WndProc / dialog / timer procs) from replay evidence
    callback_roots: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["kind"] == "callback" and edge["status"] == "observed":
            callback_roots[edge["target"]].append(label.get(edge["source"], "?"))

    # which producers ARE (or are reached from) a callback entry?
    result = {
        "categories": {
            cat: sorted(
                {label.get(fid) or _addr(fid): _addr(fid)
                 for fid, cats in producers.items() if cat in cats}.items())
            for cat in CATEGORIES
        },
        "producer_count": len(producers),
        "producers": sorted(
            (label.get(fid) or _addr(fid), _addr(fid),
             sorted(cats.keys()),
             sorted({a for s in cats.values() for a in s}))
            for fid, cats in producers.items()),
        "callback_entry_points": sorted(
            (label.get(t) or _addr(t), _addr(t), sorted(set(srcs)))
            for t, srcs in callback_roots.items()),
    }

    if args.json:
        print(json.dumps(result, indent=1))
        return 0

    scope = "OBSERVED (replay-executed)" if args.observed else "static-reachable"
    print(f"=== SimAnt rendering map ({scope}) ===")
    print(f"rendering-producer functions: {result['producer_count']}\n")
    for cat in CATEGORIES:
        rows = result["categories"][cat]
        if not rows:
            continue
        print(f"[{cat}] {len(rows)} function(s):")
        for name, addr in rows:
            print(f"    {addr}  {name}")
        print()
    print(f"=== callback entry points (message-driven presentation roots) ===")
    for name, addr, apis in result["callback_entry_points"]:
        print(f"    {addr}  {name}  <- dispatched by: {', '.join(apis)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
