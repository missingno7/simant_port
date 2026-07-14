# simant_port

A byte-exact reverse-engineering port of **Maxis SimAnt**, built on
[`win16_re`](win16_re) (the game-agnostic Win16 reverse-engineering framework, vendored
here as a git submodule), which itself is built on [`dos_re`](win16_re/dos_re) (the
8086/80186 VM, vendored inside `win16_re`).

A Win16 game runs inside a software 8086/80186 VM where the operating system is
a *Python hook layer*: every Windows API SimAnt imports (KERNEL / USER / GDI /
SOUND / MMSYSTEM / …) resolves to a hooked thunk serviced in Python, and
individual hot ASM routines are replaced with verified Python
reimplementations. The original binary stays the source of truth — a hooked run
is only accepted when it reproduces the original's behaviour **byte-for-byte**.

## The layers

| Layer | What it is |
|-------|-----------|
| `win16_re/` | Git submodule: the **game-agnostic** Win16 framework — NE loader, the selector-based memory model, the full Win16 API surface, windowing, dialogs, menus, palette/DIB rendering, audio, demos, snapshots. Knows nothing about SimAnt. Itself vendors `dos_re/` as a nested submodule. |
| `simant/` | This project's game package: the adapter (`runtime`, `_env`), recovered logic (`recovered/`), lifted islands (`hooks.py`), profiler + symbol lookup (`probes/`), and `tests/`. |
| `scripts/` | `play.py` (play interactively — real window, keyboard, mouse, audio, `--resume`; the dos_re hotkeys: F10 screenshot, F11 demo-record toggle, F12 snapshot), `boot.py` (bring-up frontier probe), `replay.py` (headless demo replay, `--from-snapshot` for anchored demos). |

All SimAnt-specific knowledge lives in `simant/`; `win16_re/` never imports from
it.

## Recovery map

SimAnt's simulation is a call tree: colony **orchestrators** drive per-ant
**behaviors**, which lean on shared **helpers** and, at the bottom, small **leaf**
predicates and RNG. Recovery proceeds bottom-up — the whole foundation is byte-exact:
the leaf predicates and RNG, the map/life-grid query family, **the pathfinding
core** (`_GetBestDir` and the helpers it composes), **and now a full mutator tier**
— ant-list CRUD (find/add/set/remove/compact, all three colonies), the
scent/pheromone system (NEST linear decay, TRAIL exponential decay, jam, single-cell
read/decrement), and the mode-population/red-initiator subsystem
(`_ClrModePop`/`_TallyModePop`/`_MakeRedInitiator`, including a genuine
mutator-calling-mutator chain). Those needed a **state-diff oracle** (snapshot → run
recovered logic on a copy of the pre-state → diff against the ASM's mutation)
instead of the return-value oracle the foundation was built with. The first genuine
top-level `_Do*Ant*` routine is now also byte-exact: `_DoFightA` (yard combat
resolution, composed with the newly-recovered `_GetNewMode` caste mode-transition
lookup). What remains is the rest of the per-ant **behaviors** (`_DoForageAnt`,
`_DoNestAntB`, `_DoDigInB`) and the **orchestrators** above them, which compose the
now-recovered mutator tier. The graph below is a real slice of the `seg5`/`seg6`/`seg7` call
graph (every edge is an actual call); green nodes are proven byte-exact against the
original ASM, an amber ring marks the most-called routines, dashed nodes are the
not-yet-recovered frontier.

```mermaid
flowchart TD
  subgraph L1["orchestrators"]
    das["_DoAntSim"]
    dab["_DoAntSimB"]
    daa["_DoAntSimA"]
  end
  subgraph L2["behaviors — the frontier (mutating; need state-diff)"]
    dnb["_DoNestAntB"]
    dfor["_DoForageAnt"]
    ddig["_DoDigInB"]
  end
  subgraph L2c["top-level behaviors, started"]
    dfa["_DoFightA"]
    ddoa["_DoDigOutAntA"]
    gnm["_GetNewMode"]
    dah["_DeadAntHere"]
    gw["_GetWinner"]
    sfa["_StartFightA"]
    gin["_GoInNest"]
    rt["_RandTurn"]
  end
  subgraph L3m["mutator tier — lists, scent, mode-pop (done)"]
    fial["_FindInAList"]
    aal["_AddAntToAList"]
    rfal["_RemoveFromAList"]
    cla["_CompactList*"]
    csd["_ColonySmellDecay*"]
    jsc["_JamScent*"]
    tmp["_TallyModePop"]
    mri["_MakeRedInitiator"]
  end
  subgraph L3p["pathfinding-selection tier (done)"]
    tcbmo["_TileCanBeMovedOn"]
    gmbd["_GetMyBestDirs"]
    grbd["_GetRedBestDirs"]
    gmrd["_GetMyRandDirs"]
    cmbd["_CheckMyBestDirs"]
  end
  subgraph L3d["dig subsystem (done)"]
    dtb["_DigTileB/R"]
    dttb["_DigTileThemB/R"]
    mnhb["_MakeNewHoleB/R"]
    exh["_ExitHole"]
    seb["_SmoothEdgesB/R"]
    fxm["_FixExitMapB/R"]
    afld["__aFldiv"]
    ged["_GetExitDirB/R"]
    gnd2["_GetEnterDirB/R"]
  end
  subgraph L3e["movement EXECUTION (done)"]
    tmdb["_TryMoveDirB/R"]
    gob["_GetOutB/R"]
  end
  subgraph L3["helpers + pathfinding core"]
    gbd["_GetBestDir"]
    gmap["_GetMap"]
    ihole["_IsItHole"]
    gdis["_GetDis"]
    glife["_GetLife"]
    inobs["_IsNotObstacle"]
    bnc["_Bounce"]
    gfd["_GetForageDir"]
    gnd["_GetNestDir"]
    gad["_GetAlarmDir"]
    grd["_GetRandDir"]
    gdd["_GetDefendDir"]
    grdd["_GetRedDefendDir"]
  end
  subgraph L4["leaves — the foundation"]
    iva["_IsValidA"]
    idirt["_IsItDirt"]
    iyel["_IsYellowAnt"]
    ifood["_IsItFood"]
    ipeb["_IsThisPebble"]
    srand["_SRand*"]
  end
  das --> dab & daa
  dab --> dnb
  daa --> dfor & gbd
  daa -.-> dfa & ddoa
  dfa --> gnm & dah & srand
  ddoa --> gnm & bnc & jsc & srand
  sfa --> gw & fial
  gin --> cla & aal & dtb
  rt --> srand
  gnd --> bnc
  gad --> bnc & srand
  grd --> bnc & srand
  gdd --> bnc & gnd & srand
  grdd --> bnc & gnd & srand
  dnb --> ddig & iyel & srand
  dfor --> iva & iyel & srand
  dfor -.-> fial & aal & csd
  dnb -.-> jsc
  gbd --> gmap & gdis & glife & inobs & ipeb
  ddig --> idirt & iyel
  gmap --> iva
  ihole --> iva & ifood
  tmp --> mri
  mri --> fial
  gmbd --> tcbmo & gdis
  grbd --> tcbmo & gdis
  gmrd --> tcbmo & gdis
  cmbd --> gmbd
  dfor -.-> gmbd
  dttb --> dtb & mnhb & idirt
  mnhb --> dtb & exh
  dtb --> seb & fxm & afld
  ddig -.-> dttb
  tmdb <--> gob
  gob --> mnhb & exh & dttb
  ddig -.-> tmdb

  classDef done fill:#2f7d4f,stroke:#8fce9e,color:#fff;
  classDef load fill:#2f7d4f,stroke:#e8a72c,stroke-width:3px,color:#fff;
  classDef front fill:#5c564b,stroke:#a99e86,color:#f3ece0,stroke-dasharray:5 4;
  class gmap,ihole,ifood,ipeb,gdis,glife,gbd,inobs,bnc,gfd,gnd,gad,grd,gdd,grdd done;
  class iva,idirt,iyel,srand load;
  class fial,aal,rfal,cla,csd,jsc,tmp,mri done;
  class tcbmo,gmbd,grbd,gmrd,cmbd done;
  class dtb,dttb,mnhb,exh,seb,fxm,afld,ged,gnd2 done;
  class tmdb,gob done;
  class dfa,ddoa,gnm,dah,gw,sfa,gin,rt done;
  class das,dab,daa,dnb,dfor,ddig front;
```

Coverage by segment — named routines proven byte-exact (an island + A/B oracle):

| Segment | Module | Role | Recovered | Status |
|---------|--------|------|:---------:|--------|
| `seg5` | SIMONE | sim primitives — map/life query, RNG, predicates, geometry, **dig subsystem done**; `_Get{Exit,Enter}Dir{B,R}` done | 74 / 169 | foundation **done** |
| `seg6` | SIMANT1 | ant AI — lists/scent/mode-pop/pathfinding/**movement done**; `_DoFightA`/`_DoDigOutAntA`/`_GetWinner`/`_StartFightA`/`_GoInNest`/`_RandTurn`/`_StealFoodB/R`/`_SimEggA`/`_Lost{Head,Tail}*` done; forage/nest frontier | 51 / 123 | movement **done** |
| `seg7` | SIMTWO | world sim + tile rendering + event loop; `_GetNewMode*`, `_Bounce`, the full `_Get*Dir` family done | 14 / 282 | mostly rendering |
| `seg4` | `_TEXT` | C runtime (`__aFldiv`/`__aFulmul`, MSC `rand`/`srand`) + tile expanders | 27 / 248 | hot paths lifted |

The recovered routines are deliberately the load-bearing ones — `_SRand1` has 88
callers, `__aFldiv` 40+ (the MSC compiler's own long-division helper, pulled in
by everything from the dig subsystem to unrelated UI/score code), `_SRand8` 71,
`_win_IsWinOpen` 67, `_win_GetObjRect` 50, `_IsYellowAnt` 28, `_IsValidA` 26,
`_GetDir` 17, `_FindInAList` 16, `_IsItDirt` 15, `_GetDis` 15, `_FindInBList` 15.
Regenerate the underlying call-graph data with `python -m simant.probes.callgraph`.

**What's done vs. what's missing.** Everything an ant needs to *decide how to
move and then actually move* is byte-exact, end to end:

- **Foundation**: leaf predicates + RNG, the map/life-grid query family, the
  geometry (`_GetDir`/`_GetDis`), the pathfinding core `_GetBestDir`.
- **Mutator tier**: ant-list CRUD (find/add/remove/compact, all three
  colonies), the scent/pheromone grids (decay/jam/read for both the NEST and
  TRAIL grids), and the mode-population/red-initiator subsystem.
- **Pathfinding-selection tier**: `_TileCanBeMovedOn` (the shared movement/
  self-exclusion predicate), `_GetMyBestDirs`/`_GetRedBestDirs` (per-colony
  neighbour selection), and the two routines that compose them —
  `_GetMyRandDirs` (stateful sticky-direction search across ticks via a
  far-pointer in/out state) and `_CheckMyBestDirs` (walks up to 64 steps
  toward a target).
- **Dig subsystem, complete**: `_FixExitMapB/R` (exit-distance flood-fill),
  `_SmoothEdgesB/R` (post-dig edge auto-tiling), `_ExitHole`, `_DigTileB/R`,
  `_MakeNewHoleB/R`, `_DigTileThemB/R` — everything a movement routine needs
  to dig through the nest as it goes.
- **Movement EXECUTION, complete**: `_TryMoveDirB/R` <-> `_GetOutB/R` — a
  genuine mutual-recursion pair (execute a step, or reach the surface and
  either complete an exit hole or nudge the dig frontier and retry). The
  black side has one deliberate, documented gap: a trophallaxis (food-
  sharing) branch that calls the unrecovered `_DoTroph` — the port computes
  that gate's condition exactly and raises loudly if it would ever fire,
  rather than fake the outcome.
- **Top-level `_Do*Ant*` behaviors, started**: `_DoFightA` and
  `_DoDigOutAntA` — the first two genuinely **top-level** routines recovered
  (each one call-hop below `_DoAntSimA`). `_DoFightA`: per-tick caste jitter
  plus a 1-in-16 kill roll that composes the caste mode-transition lookup
  `_GetNewMode` (`seg7`) and the already-recovered `_DeadAntHere`. Its
  presentation-only branch (`ANTEDIT!_FightBalloons`, a speech-balloon UI
  call) is deliberately omitted, not ported — same core/presentation split
  as the redraw stubs below. `_DoDigOutAntA`: aging/mode-transition, or a
  move (with its own, distinct natural-decay kill chance) toward a
  `_Bounce`-biased or mode-table-random direction, composing `_Bounce`
  (also recovered — a yard-edge "bounce back into the map" compass) and the
  already-recovered `_JamScentBN`/`_JamScentRN`.
- **Also recovered, complete**: the six-routine seg7 `_Get*Dir` family —
  `_GetForageDir` (TRAIL scent gradient, its own non-`_Bounce` yard-edge
  scheme), `_GetNestDir` (NEST scent gradient, or homing toward the
  colony's queen/nest-entrance target via `_GetDir` when the ant's own
  cell has no scent), `_GetAlarmDir` (a single colony-neutral ALARM grid),
  `_GetRandDir` (no gradient at all — pure `_Bounce`-or-random), and
  `_GetDefendDir`/`_GetRedDefendDir` (each gated on a colony-specific
  game-mode selector: modes 2/3 delegate wholesale to `_GetNestDir`, mode
  1 steers toward a fixed attack marker or a distance-gated target, any
  other mode is a no-op). None have a caller in this repo yet
  (`_DoForageAnt`/`_DoNestAntB`/combat orchestration remain unrecovered),
  same as `_Bounce` before `_DoDigOutAntA` landed — a self-contained
  "direction picker" tier, ready for whichever top-level behavior routine
  composes it next. Also in `seg5`: `get_exit_dir_b`/`r` and
  `get_enter_dir_b`/`r` — the nest-tunneling counterparts, heading toward
  the highest or lowest exit-distance respectively (the SAME arrays
  `fix_exit_map_b`/`r` maintain), each a byte-identical B/R twin pair.
  `steal_food_b`/`r` (an ant nibbling stored food on the nest map — reroll
  on the "full pile" tile, else a genuine byte-wrapping decrement with no
  underflow guard). The 5-routine `_Lost*` family (`lost_head_a`/`b`/`r`,
  `lost_tail_b`/`r`) — trail-marker occupancy checks that trust an intact
  encoded tile value first, falling back to an actual ant-list search
  only when the tile has changed. `_DeadAntHere` (a 100-slot corpse-decay
  ring buffer),
  the MSC C-runtime long-arithmetic helpers `__aFldiv`/`__aFulmul` and the
  independent `rand`/`srand`/`_RRand` generator (distinct from the `_SRand*`
  LFSR used for map generation).

**Missing**: the per-ant **behavior tier** in `seg6` (`_DoForageAnt`,
`_DoNestAntB`, `_DoDigInB`, `_DoAntSim*`) that composes all of the above into
an actual decision, `_DoTroph`'s own dependency chain (a real sound-engine
routine plus a dialog/busy-wait UI routine — presentation/audio work, not
core sim logic), and the rest of combat (`_YellowFight`/`_GetWinner`, which
now only needs zero-blocker leftovers now that `_GetNewMode` is done). That's
the next milestone toward the [VM-less native port](docs/vmless_port.md).

### What gets lifted vs. what gets replaced

The endgame is a **VM-less native port** (see [`docs/vmless_port.md`](docs/vmless_port.md)):
the emulator becomes an oracle, and recovered source runs the game directly. That
makes the game/backend boundary the thing that matters, and there are two:

- **The Win16 OS layer** (`win16_re/` — KERNEL/USER/GDI/SOUND/MMSYSTEM, the VM,
  windowing, audio) is the backend. A native port *replaces* it wholesale; nothing
  here is "lifted".
- **Inside the game**, each routine is either **core** (the deterministic
  simulation a native backend must run byte-exact — ant AI, map/life grids, RNG),
  **presentation** (tile expanders, the window system, the editor UI — a native
  backend *reimplements* these), or **runtime** (C-runtime/codec helpers the
  language provides). The litmus test: *simulation decides where the ants are and
  what they choose; presentation decides how that looks.*

So the honest native-port metric is **core routines recovered**, not the flat island
count — presentation islands (recovered early because they were hot) are workbench
scaffolding a native backend discards. `python -m simant.probes.callgraph` reports it:

| Role | Recovered / total | In the native port |
|------|:-----------------:|--------------------|
| **core** | **40 / 583** | runs unchanged — *the denominator* |
| presentation | 18 / 490 | reimplemented natively |
| runtime | 9 / 240 | provided by Python |

## Setup

```
git clone --recurse-submodules <this repo>
# or, if already cloned:
git submodule update --init --recursive
```

## Running the game

```
python scripts/play.py --scale 2                              # play it
python scripts/play.py --resume artifacts/snapshots/<snap>     # resume a snapshot
python scripts/boot.py [max_steps]                             # bring-up frontier report
```

`play.py` mirrors each Win16 window as a real OS window and reports every error
to the console (the game itself only needs the user to provide input).

## Working principles

- **Fail loud, never fake.** An unimplemented API / opcode / DOS service stops
  with a named frontier rather than guessing — the honest bring-up report.
- **Never weaken an oracle to make a slice pass.** The byte-exact proof is the
  value. A lifted hook is only accepted when an A/B run (original ASM vs. Python
  replacement) is pixel- and state-identical.
- **Game logic stays VM-free**; the VM/hook machinery stays in `win16_re/`.

## Status

Live bring-up notes and the standing-mechanisms registry are in
[`docs/run_status.md`](docs/run_status.md). The test suite is the
gate — run `python -m pytest -q` before any commit; never commit red.
