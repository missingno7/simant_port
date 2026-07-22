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

## 4. What actually draws each window (resolved) vs the event frontier

The DRAW path resolves statically through `call-far` edges — query it with
`render_map.py --tree <function>`.  The map window's drawing tree:

```
_win_DrawMapWindow (18C0:CE46)
    _DoFastBitmap        (0E99:39B2)   colour sprite/tile blit
    _DoFastMonoBitmap    (0E99:3B48)   1-bpp mask blit
    _GBoxFill            (0E99:19E6)   solid fill
    _GRectInvOutline     (0E99:0E0A) -> _GInvBox   selection outline
    _win_IsWinOpen       (430E:C256)
```

So the map is drawn by the **fast bitmap blitters** (`_DoFastBitmap` /
`_DoFastMonoBitmap`) plus box-fill and invert-outline — the hot path and the
natural seam for hi-res / smooth presentation.  Similarly
`_win_DrawWindow → _win_DrawObjectI → _GRectInv/_GSetAttrib`,
`_win_DrawEditWindow → _DrawEditGraphs`, and `_UpdateEdit → _ScrollEditArrays,
_DrawEditGraphs` (the last two resolved by the menu session, §6).

What is NOT yet resolved is the object-window **event/scroll dispatch**, not
the draw dispatch: `_win_GetEvent` and `_ScrollEditWindow` tail through an
indirect jump (`jmp_ind` at e.g. `430E:B7B6`) into a per-window event routine
(`window_records` is `FarPtr[256]` @ `0xCE9A` — each object carries its
draw/event routine).  **240 indirect dispatch edges remain unresolved**
across the binary; resolving the presentation ones is the far-call-evidence
widening (task #60) applied here, needing sessions that drive each window
type's event path.  The DRAW side, though, is already legible.

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

## 6. Evidence base + next steps

Two independent sessions are ingested: `cold_nohooks` (oracle, quick-play,
199M instr, 597 fns) and `session_165418` (a candidate menu session recorded
with hooks on — the owner interacting with windows, 91M instr, 472 fns, ingested
as CITED manual facts via `replay_artifact.py --hooks --evidence-out` +
`atlas_build.py --render-evidence`, because a candidate capture is not
oracle-trusted but its observed transfers are real evidence).  Together:
development coverage 1036 reachable; the menu session added the edit-window
draw edges and CONFIRMED — across two independent sessions — that only two
guest procedures are ever dispatched (`MAINWNDPROC`, `MYTIMERFUNC`): SimAnt's
single-WndProc architecture is now evidence-backed, not assumed.

Next:

1. **Resolve the event/scroll dispatch (§4)**: record sessions that drive each
   window type's event path (scroll the edit/map view, open the nest/lab
   views, use the caste/mode windows) so the `jmp_ind` object-window event
   routines resolve — the presentation slice of task #60.
2. **Hook at the stable DRAW boundaries the map already identifies** — the
   fast bitmap blitters (`_DoFastBitmap`/`_DoFastMonoBitmap`) are the map's
   hot path and the seam for hi-res/smooth presentation; `_GBoxFill`/
   `_GPutStr`/`_TrapFill` are the primitive seam; the scroll path
   (`_ScrollEditWindow`/`_DoEditScroll`) is the smooth-scroll seam.  Each hook
   is a plan-selected authored implementation verified against the oracle (the
   3.0 catalog), never an isolated one-off.
3. **Query any window's draw tree** with `render_map.py --tree <function>`.

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
