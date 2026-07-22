# SimAnt rendering subsystem — the map (evidence-driven)

Every claim here is an edge the Execution Atlas holds from cited evidence
(static Recovery IR `api:*` effect tags + replay-observed call/callback
transfers), never inferred from visible output.  Regenerate the live query
with `python scripts/render_map.py` (`--observed` for the replay-confirmed
subset, `--json` for machine form).  Coverage figures below are from the
`cold_nohooks` oracle replay; widening the evidence (a gameplay recording that
scrolls the map, opens child windows, repaints) extends them — see §6.

## 1. The Windows API boundary is NARROW

SimAnt does **not** register a window procedure per window.  The replay
evidence shows only **two** guest procedures dispatched by Windows:

| Callback entry | Address | Dispatched by |
|---|---|---|
| `MAINWNDPROC` | `0100:2930` | CreateWindow, DispatchMessage, SetWindowPos, ShowWindow, UpdateWindow |
| `MYTIMERFUNC` | `0100:2440` | DispatchMessage (WM_TIMER → the frame/animation driver) |

Everything visible is produced by SimAnt's **own** window/rendering code
(below), reached from these two entries.  The Win16 API is a thin service
boundary: 45 GDI + the drawing-relevant USER calls (`render_map.py`'s
taxonomy), consumed by 140 statically-reachable SimAnt functions (62
confirmed executed by `cold_nohooks`).

## 2. The rendering layers (by NE segment)

The SIMANTW.SYM names cluster cleanly by segment:

| Seg | Module | Role |
|---|---|---|
| `0100` | SIMANT | App shell: `MAINWNDPROC`, `WINMAIN`, `_InitApplication`, `_InitInstance`, `_NewGame`, `_RedrawWindows`, `_SetDefaultWindows`, the palette setup (`_InitPalette`/`_SetUpPalette`/`_SetDevicePalette`). |
| `430E` | GR (graphics/window object system) | SimAnt's **own windowing layer**: `_win_Open`/`_win_Close`/`_win_Swap`/`_win_Zoom`, `_win_DrawWindow`/`_win_DrawObjectI`, `_win_GetEvent`/`_win_DoProxMenu`, `_win_InvalidateObject`, `_win_SetPalette`, `_win_PrintfAtObj`/`_win_CenterStrAtObj`. |
| `0E99` | GR_MODULE (graphics primitives) | The device layer: `_InitGraphics`, `_DetectDisplay`, the "G" primitives `_GLine`/`_GBoxFill`/`_GPatBox`/`_GPutStr`/`_GInvBox`/`_TrapFill`, the bitmap blitters `_DoBitmap`/`_DoFastBitmap`/`_DoMonoBitmap`/`_DoFastMonoBitmap`, fonts `_GSetBigFont`/`_GSetSmallFont`, clip `_MSClipStart`/`_MSClipEnd`, and `_PaintStuff` (the WM_PAINT body). |
| `18C0` | ANTEDIT / map+edit windows | The scene drawers: `_DrawMap`/`_DrawMapData`/`_DrawYard`/`_Draw_SimYard`/`_DrawSpider`/`_DrawBalloons`/`_DrawMower`/`_DrawSimKid`/`_DrawSwarm`, the edit/scroll path `_ScrollEditWindow`/`_DoEditScroll`/`_ResetEditScrollRange`/`_UpdateEdit`, the map windows `_win_DrawMapWindow`/`_win_DrawEditWindow`/`_OpenMiniMapWin`/`_MapAreaEvent`. |

## 3. The pipeline (state → pixels)

```
Windows message  ->  MAINWNDPROC (0100:2930)   [the single WndProc]
                        |  dispatch on message + SimAnt window object
                        v
                     _win_* object-window system (430E)
                        |  _win_DrawWindow -> _win_DrawObjectI
                        |  -> per-object DRAW POINTER  (INDIRECT, see §4)
                        v
                     scene drawers (18C0)         _DrawMap, _DrawYard,
                        |                          _Draw_SimYard, _DrawSpider ...
                        v
                     graphics primitives (0E99)   _GLine, _GBoxFill, _GPutStr,
                        |                          _DoBitmap, _TrapFill ...
                        v
                     GDI / USER  (win16/api)       BitBlt, TextOut, Polygon,
                                                   SelectObject, palette, DCs
WM_TIMER  ->  MYTIMERFUNC (0100:2440)  -> animation/frame updates -> invalidation
```

Confirmed call edges (Atlas, resolved/observed): `_win_DrawWindow →
_win_DrawObjectI`; `_win_Open → _win_Recalc`, `_win_LockWinHigh`,
`_win_UnlockWin`; `_DrawMap → _DrawMapData`; `_Draw_SimYard → _DrawMower`,
`_DrawSimKid`, `_DrawSwarm`; `_PaintStuff → _GSetBigFont`.

## 4. The object-window draw dispatch is INDIRECT — the key map gap

`_win_DrawMapWindow`, `_ScrollEditWindow`, `_DrawMap` show few/no static
callees because the object-window layer dispatches drawing through a **per-
window draw function pointer**, not a static call.  This matches the recovered
state layout: `SimAntState.window_records` is `FarPtr[256]` @ `0xCE9A` — each
window object carries its own draw/event routine.  `_win_DrawWindow` calls it
indirectly (`call far [obj->draw]`).

Consequence: **the map→drawer edges are indirect and only resolve from runtime
evidence** (the Atlas's `call_ind` sites are `unresolved` until a replay
observes the target).  This is the single most important reason the rendering
map is only as complete as the replay coverage, and why §6 (wider recording)
is the next step — it is also a concrete instance of far-call evidence
widening (task #60) applied to the presentation layer.

## 5. The drawing categories (static-reachable / observed)

| Category | Windows APIs | static | observed |
|---|---|---:|---:|
| dc-lifecycle | GetDC/BeginPaint/EndPaint/ReleaseDC/Create/DeleteDC/Save/RestoreDC | 16 | 10 |
| invalidation | InvalidateRect/Rgn, UpdateWindow, Validate*, GetUpdateRgn | 56 | 30 |
| blit | BitBlt/StretchBlt/PatBlt/SetDIBitsToDevice | 4 | 3 |
| gdi-object | Create Pen/Brush/Font/Rgn, SelectObject, DeleteObject, GetStockObject | 42 | 11 |
| primitive | TextOut/Polygon/LineTo/MoveTo/FillRect/InvertRect | 24 | 7 |
| text | SetTextColor/BkColor/BkMode/Align, GetTextExtent/Metrics | 8 | 6 |
| scroll | ScrollWindow/ScrollDC, Get/SetScrollPos/Range | 12 | 8 |
| palette | Create/Select/RealizePalette, Get/SetPaletteEntries | 6 | 6 |
| window-geom | GetClientRect, MoveWindow, SetWindowPos, ShowWindow, Client/ScreenTo | 29 | 12 |
| clip | IntersectClipRect, SelectClipRgn, GetRgnBox, SetMapMode | 8 | 5 |

`palette` is fully exercised at boot; `scroll` is mostly exercised (the
`_ScrollEditWindow`/`_DoEditScroll` path — the smooth-scroll target); the
big gaps are `gdi-object` (31 unobserved) and `invalidation`/`window-geom`
(paths that only run once child windows/maps are opened and repainted).

## 6. Evidence gap + next steps

1. **Record a rendering session** (owner) that opens the map/nest/lab views,
   scrolls, zooms the mini-map, and forces repaints; convert to a
   ReplayArtifact; run `replay_artifact.py --evidence`; rebuild the Atlas
   (`atlas_build.py`).  This resolves the indirect object-window draw
   dispatches (§4) and fills the `gdi-object`/`invalidation`/`window-geom`
   gaps (§5).
2. **Re-run `render_map.py --observed`** — the observed set converges on the
   full rendering closure; the callback-entry list confirms whether any
   guest procedure beyond `MAINWNDPROC`/`MYTIMERFUNC` exists.
3. **Then hook at the stable boundaries the map identifies** — the graphics
   primitives (`_DoBitmap`/`_GPutStr`/`_TrapFill`) and the object-window draw
   dispatch (`_win_DrawWindow`) are the meaningful seams for a native
   renderer; the scroll path (`_ScrollEditWindow`) is the seam for smooth
   scrolling.  Each hook is a plan-selected authored implementation verified
   against the oracle (per the 3.0 catalog), never an isolated one-off.

## 7. Enhancement seams this map exposes

- **Smooth scrolling**: `_ScrollEditWindow`/`_DoEditScroll` + the
  `Get/SetScrollPos/Range` boundary — recover this path, then a native
  scroller can sub-pixel-interpolate between the game's integer scroll steps.
- **Reduced flicker / efficient repaint**: the `invalidation` set (56 fns) +
  the object-window `_win_InvalidateObject`/`_win_DrawWindow` seam — a native
  compositor can coalesce the game's per-object invalidations into one
  double-buffered present.
- **High-res / scaling**: the `_Do*Bitmap` blitters + `SetMapMode`/`StretchBlt`
  boundary — the native blit path can render the 16-colour DIBs at integer or
  smooth scale.

All of these require the recovered path FIRST (this map) so the enhancement is
a clean native structure, not a hack over opaque behaviour.
