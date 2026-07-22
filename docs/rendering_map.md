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

The **switch dispatch is now STATICALLY RESOLVED**: the bounded cs-relative
jump tables (the MSC `cmp/jbe/shl/xchg/jmp cs:[bx+T]` idiom) are read
directly from the code segments by `dos_re.lift.dispatch
.static_switch_targets` (irgen annotates each site with its arm set; the
Atlas imports them as resolved edges).  106 of 130 `jmp_ind` sites resolve
this way — 632 resolved arm edges, every one of the 99 replay-observed arms
confirmed inside its static table (the atlas_build cross-check).  That
includes `_win_DrawObjectI`'s object switch, `MAINWNDPROC`'s message switch,
and `_ProcCasteEvent`/`_ProcModeEvent` (whose arms the caste/behaviour
sessions had just confirmed dynamically).  The only unresolved GAME `jmp_ind`
sites left are the three pre-scaled **ROP dispatchers**
(`_CreateMonoSolidBrush`/`_GBoxFill`/`_GPatBox`: `and ax,70h; shr ax,3;
cmp; ja; jmp cs:[bx+T]` — the bound is a byte offset, refused by design);
the rest are C-runtime dispatchers in seg `275F`.

The object-window **draw and event dispatch is now FULLY MAPPED**
(`scripts/winmap.py` — exact static patterns, cross-checked against the
observed dispatch; typed facts in the Atlas, source `window-object-map`):

* **Draw**: per-slot far pointers in a runtime table
  (`seg[DGROUP:0xC6CC] : 0x77B2 + slot*4`, slot = handle >> 8, parallel to
  `window_records` FarPtr[256] @ DGROUP:0xCE9A).  ALL 11 registrations
  happen in ONE function — `_InitApplicationWindows` (0100:5464) via
  `_win_SetWinDrawHook(handle, farproc)` — and the stored pointer is
  invoked at exactly TWO sites, `430E:BBE2` (pass 1) and `430E:BC83`
  (pass 2) inside `_win_DrawWindow`.  Every replay-observed draw-callback
  invocation is one of the 11 registered hooks (cross-check enforced).
* **Events**: NOT stored pointers.  `_DoEvent` (0100:0BC2) compare-chains
  on the event-code CLASS (`code & 0xFF00`; the class byte mirrors the
  window slot) and STATICALLY calls the per-window `_Proc*Event` — nine
  bindings: Edit/Map/Info/Mode/Caste/History/Yard + the map/yard RIBBON
  pseudo-windows (classes 0x22/0x23).  A first chain, active only in help
  mode (DGROUP:0x0010), routes the same classes to WinHelp context help.
  (An earlier note here claimed a "seg-0060 gateway" — wrong: 0060 is the
  API import-thunk segment; `_win_GetEvent`'s far calls into it are plain
  PeekMessage/API calls.)
* **Lifecycle**: `_win_LoadAllWindows` builds the records at startup;
  `_win_Open(handle,…)` does the Win16 side (CreateWindow, `SetProp`
  hwnd→object, menu edits, ShowWindow, InvalidateRect, UpdateWindow);
  `_win_Close` tears down; `MYTIMERFUNC` drives `_DoAntSim` (simulation)
  + `_UpdateWindows` (repaint) + `_myServiceSong` (music) per tick.

The remaining dynamic `call_ind` frontier is the sound-driver function
pointers (0E99), the FileSelect dialog cases, and CRT — none of it is the
window system.

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
`_DoEditScroll` path — the smooth-scroll target; `_ScrollEditWindow` has no
static caller and never executed in any session); the
big gaps are `gdi-object` (31 unobserved) and `invalidation`/`window-geom`
(paths that only run once child windows/maps are opened and repainted).

## 6. Evidence base + next steps

Four independent sessions are ingested: `cold_nohooks` (oracle, quick-play,
199M instr, 597 fns) plus three candidate sessions recorded with hooks on
(`session_165418` menus/windows 91M instr 472 fns, `session_170643` caste
control 33M instr 213 fns, `session_170653` behaviour control 56M instr
220 fns — ingested as CITED manual facts via `replay_artifact.py --hooks
--evidence-out` + `atlas_build.py --render-evidence`, because a candidate
capture is not oracle-trusted but its observed transfers are real evidence).
The caste/behaviour sessions fired `_ProcCasteEvent`'s and `_ProcModeEvent`'s
switch arms; ALL FOUR sessions dispatch only `MAINWNDPROC` + `MYTIMERFUNC` —
the single-WndProc architecture is thoroughly evidence-backed.  With the
static switch tables (§4) the development coverage is **1525 reachable**
(was 1036 on observation alone: +481 functions proven reachable through the
statically-resolved dispatch).

Next: the Quick Game / Black Nest View vertical slice (§8), verified
against the oracle before generalizing.  Query any window's draw tree with
`render_map.py --tree <function>`; the full window map with
`winmap.py [--json]`.

## 8. The Quick Game / Black Nest View pipeline — and the island boundary

The quick-game presentation is FOUR of the mapped windows plus the shared
machinery (every edge below is an Atlas fact):

| Window (slot) | Draw callback | Event routine | Scroll |
|---|---|---|---|
| **Edit = the black nest view** (00) | `_win_DrawEditWindow` | `_ProcEditEvent` | `_DoEditScroll` (WM_*SCROLL arms of `MAINWNDPROC`) → `_UpdateEdit` |
| Map (01) | `_win_DrawMapWindow` | `_ProcMapEvent` | — |
| MiniMap (14) | (no hook — drawn by the map/edit path) | — | — |
| Yard (19) | `_win_DrawYardWindow` | `_ProcYardEvent` | — |
| ribbons (22/23) | (part of parent) | `_ProcMapRibbonEvent`/`_ProcYardRibbonEvent` | — |

The frame loop: `MYTIMERFUNC` → `_DoAntSim` (simulation, NOT presentation)
+ `_UpdateWindows` → `_UpdateEdit` (the nest-view repaint entry, 39 callers:
the `_goStep*` scroll steppers, `_CenterAnt`/`_Goto*` navigation,
`_MapAreaEvent`) → `_ScrollEditArrays` + `_DrawEditGraphs` → the `_G*`/
`_Do*Bitmap` primitives → GDI.

**The narrowest stable boundary for native presentation is the draw-hook
table itself.**  The original architecture already treats a window's
renderer as a REPLACEABLE far pointer, registered per slot and invoked at
two known sites with a known contract (`(pass)` on the stack, window
object via `window_records[slot]`).  A native presentation island for the
quick game can therefore own slots 00/01/14/19 draw + the `_UpdateEdit`
scroll cluster as ONE island — registered exactly where the game registers
its own renderers, leaving `_DoAntSim` and the event routines (game logic)
untouched.  No invented structure: the seam is the game's own.

Island composition (per the progressive-detachment goal): rather than
hooking `_DoFastBitmap`/`_GBoxFill` individually (hundreds of crossings per
frame), the island boundary is `_win_Draw*Window` + `_UpdateEdit` — one
crossing per paint pass, state read through the bridge's named DGROUP/far
views.  The blitters/primitives then become internal details of the island,
recovered as its readable source.

## 7. Enhancement seams this map exposes

- **Smooth scrolling**: `_DoEditScroll` (+ `_UpdateEdit`) + the
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
