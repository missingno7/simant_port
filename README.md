# simant_port

A byte-exact reverse-engineering port of **Maxis SimAnt for Windows (1991)** —
recovered from the original 16-bit binary into host code that runs the game with
**no emulator and no original EXE**, verified every step against the original as
an oracle.

**[▶ v0.1.0 — the playable standalone release](../../releases/latest)**: drop
`simant_vmless.exe` into a SimAnt install and play. No Python, no DOSBox, and
`SIMANTW.EXE` is never opened — the recovered code *is* the game.

Built on [`win16_re`](win16_re) (the game-agnostic Win16 framework, a git
submodule) which vendors [`dos_re`](win16_re/dos_re) (the 8086/80186 VM +
recovery toolchain) as a nested submodule.

---

## How the port works: the staged pipeline

This project follows the **DOS_RE 2.0** method
([`win16_re/dos_re/docs/dos_re_2.0.md`](win16_re/dos_re/docs/dos_re_2.0.md)):
*don't port the game — build the machine that ports the game.* Deterministic
tooling mechanically transforms the binary; a human (or agent) only removes
blockers; the **original binary stays the oracle** — generated code is accepted
only when its behaviour matches the original **byte-for-byte**.

Recovery is a ladder of *detachments*, each one removing a dependency on the
historical machine while remaining oracle-testable. Each rung has a **hard,
tooling-enforced wall** — the milestone isn't "the code works", it's "the code
*cannot* fall back to the layer below":

| Stage | Milestone | What is removed | Wall | Status |
|-------|-----------|-----------------|------|:------:|
| 0 | **Interpreted oracle** | — | the original runs deterministically in the VM | ✅ |
| 1 | **VMless** | instruction interpretation | no interpreter fallback in the corpus; runtime never reads the EXE | ✅ **shipped (v0.1.0)** |
| 2 | **CPUless** | the CPU-shaped carrier (registers, flags, machine stack, CALL/RET) | promoted code cannot touch `cpu.s`/flags/stack | 🔨 **in progress** |
| 3 | **Memoryless** *(DOS-layout-less)* | the historical DGROUP/selector byte-image memory model | gameplay holds no raw offsets — native structures instead | ⬜ next |
| 4 | **Semantic clean port** | anonymity | named functions/fields, domain models, subsystem APIs | 🌱 grown ahead (see below) |

> First remove the interpreter. Then remove the CPU model. Then remove the
> memory layout. What remains is the game itself.

The emulator is demoted one layer at a time — from *engine*, to *oracle*, to
*optional development bridge*. `win16_re` provides this ladder generically; SimAnt
is the Win16 pilot that drives it (as Lemmings is the DOS pilot upstream).

---

## Where SimAnt is on the ladder

### ✅ Stage 1 — VMless (complete, released)

The full reachable program is lifted to host code and runs with **both hard
walls enforced physically**:

- **VMless execution wall** — `cpu.interp_forbidden` is armed from instruction
  zero. 1903 of 1904 census functions are lifted to symbolically-named Python
  modules (the sole exclusion is `_DoInt3`, a dead debug stub); every address the
  step dispatch can reach carries a lifted hook, so interpretation is *impossible*,
  not merely unused. An uncovered address raises.
- **EXE-independence wall** — the runtime boots from a generated **data-only boot
  image** (no NE parse, no `SIMANTW.EXE` read; every recovered code byte zeroed,
  data tables preserved as declared facts) and refuses to open the EXE by name
  *or* content hash.

**Proof:** the linked graph replays the 199.6M-instruction `cold_nohooks` demo
**byte-identical** to the interpreted oracle — all 39 aligned checkpoints and the
final state (masked digest `417cac5c…` at instruction 199,619,366). Shipped as
**v0.1.0** (`scripts/deploy_vmless.py` → a self-contained PyInstaller build,
smoke-tested with the EXE physically absent).

### 🔨 Stage 2 — CPUless (in progress)

Removing the CPU carrier: promoted functions communicate through arguments,
returns, and explicit state instead of emulated registers, flags, and the machine
stack. The M4 promotion census over the corpus:

| tier | count | meaning |
|------|:-----:|---------|
| **leaf** | 419 | no refusals — the first CPUless emitter targets |
| **calls-only** | 1417 | promote bottom-up as their callees promote |
| **blocked** | 68 | need a named capability (x87 FPU, indirect transfers) |

The blocked frontier is almost entirely **x87** (opcodes `9B`/`D8`–`DF`) — the
same FPU the VMless emitter already handles via shared `execute_fpu` delegation.
The first vertical slice (promote the leaf tier, gate it against the oracle
differential) is underway.

### 🌱 Stage 4 grown ahead — the recovered corpus

`simant/recovered/` holds **~309 functions of clean, byte-exact Python** — the ant
simulation recovered by hand and proven against the ASM with state-diff oracles
(disassemble → run the recovered logic on a copy of the pre-state → diff the
mutation). This started as classic routine-by-routine recovery and is now the
project's **CPUless/semantic head start**: every one of these functions is already
CPU-less-shaped (pure Python on named state views, no CPU object). The
manual-corpus rule ([owner directive](docs/run_status.md)) is **the verified hand
recovery is authoritative** — the pipeline generates a CPU/ABI *adapter* around it,
never a parallel machine-lift of the same function.

What's covered, bottom-up and complete: the leaf predicates + RNG, the map/life
query family, geometry (`_GetDir`/`_GetDis`) and the pathfinding core
(`_GetBestDir`); the mutator tier (ant-list CRUD, scent/pheromone grids,
mode-population); the dig subsystem and movement execution (`_TryMoveDirB/R` ⇄
`_GetOutB/R`); the full black-nest per-ant behaviour tier (`_DoForageAnt`,
`_DoDigInB`, `_SimQueenB`, `_DoFoodInB`, `_DoDigOutB`) and its 18-arm dispatcher
`_DoNestAntB`/`_DoAntSimB`; and all eight of `_DoAntSimA`'s dependencies. Two gates
raise loudly by design rather than guess (`_DoTroph`, `_YellowFight`). The
remaining frontier is the `_DoAntSim`/`_DoAntSimA` top-level orchestration.

| segment | module | recovered | role |
|---------|--------|:---------:|------|
| `seg5` | SIMONE | 123 / 169 | sim primitives — map/life, RNG, geometry, dig subsystem |
| `seg6` | SIMANT1 | 107 / 123 | ant AI — lists, scent, pathfinding, movement, behaviour tier |
| `seg7` | SIMTWO | 66 / 282 | world sim + tile rendering + event loop |
| `seg4` | `_TEXT` | 27 / 248 | C runtime + tile expanders |

---

## The layers

| Layer | What it is |
|-------|-----------|
| `win16_re/` | Git submodule: the **game-agnostic** Win16 framework — NE loader, selector memory model, the full Win16 API surface (KERNEL/USER/GDI/SOUND/MMSYSTEM), windowing, dialogs, palette/DIB rendering, audio, demos, snapshots. Knows nothing about SimAnt. Vendors `dos_re/` (the VM + the lift/CPUless recovery toolchain). |
| `simant/` | This game: `recovered/` (the authoritative hand-recovered corpus, VM-free), `facts/` (the evidence-backed recovery facts the pipeline consumes), `bridge/`+`native/` (the state-view seam toward native memory), `probes/` (symbols + call graph), `tests/`. |
| `scripts/` | The pipeline (below) + `play.py` (interactive hybrid) / `play_vmless.py` (the strict runner) / `replay.py` / `checkpoints.py` (the differential gate). |

The one rule that never breaks: **`win16_re/` never learns SimAnt** (no game
addresses, filenames, or formats). A missing capability becomes a *generic*
framework improvement or a game-side *fact* — never a game-specific patch to the
framework. Fixes to `dos_re`/`win16_re` discovered here are pushed upstream with a
regression test.

---

## Running the pipeline

Every stage is a repeatable command; generated artifacts are disposable and
regenerate from the binary + facts + toolchain. Modules are named from
`SIMANTW.SYM` (`simone_srand1.py`) and carry their address as provenance — never
hand-edited.

```
# ── recovery IR: the shared representation every stage consumes ──
python scripts/irgen.py                    # 1904 fns (.SYM census + dispatch-fact
                                           # case entries + static-call closure);
                                           # symbol identity is first-class
python scripts/dispatchgen.py              # mechanical jump-table derivation → facts
python scripts/waitscan.py                 # mechanical env-wait enumeration → facts

# ── Stage 1: VMless graph ──
python scripts/liftemit.py --require-vmless-wall   # 1903 symbolic modules + manifest
python scripts/liftlink.py                 # structural near+far link + capability report
python scripts/adaptgen.py                 # route matched entries through the recovered
                                           # corpus via generated CPU/ABI adapters

# ── Stage 2: CPUless promotion ──
python win16_re/dos_re/tools/cpuless_census.py --ir artifacts/recovery_ir.json \
    --out artifacts/cpuless_census.json    # the promotion work-list

# ── the gate: oracle-vs-graph, byte-exact ──
python scripts/checkpoints.py cold_nohooks --api-aligned --save base.trace
python scripts/checkpoints.py cold_nohooks --api-aligned \
    --boot-image artifacts/vmless_boot --mask-poison artifacts/vmless_boot \
    --check base.trace --check-field mdigest        # strict run == oracle
```

**Every SIMANTW entry is served by exactly one of four tiers** — one
implementation per function, never two:

| tier | serves the entry with | provenance |
|------|----------------------|------------|
| authoritative manual | `simant/recovered/` (pure, CPU-less) | hand recovery + state-diff/island oracles |
| generated adapter | `scripts/adaptgen.py` (marshals the CPU carrier → the recovered fn) | `recovered_map.json` + `recovery_ir.json` |
| literal lift | `scripts/liftemit.py` (mechanical) | recovery IR |
| api effects | `win16/api/` Python services | the OS surface |

### The strict runner and the release

```
python scripts/build_vmless_boot_image.py  # EXE consumed HERE (build time only)
python scripts/audit_vmless_boot_image.py  # data-only proof (no bundled EXE)
python scripts/lint_vmless_independence.py  # the runner reaches no loader edge
python scripts/play_vmless.py --demo cold_nohooks   # headless, EXE-free, both walls
python scripts/play_vmless.py                       # interactive

python scripts/deploy_vmless.py --exe      # → dist/exe/… the standalone build
```

A tester needs only the game **DATA** files from an original SimAnt for Windows
install (fonts, `SOUND\*.MID`, `.DAT`/`.NDX`) — `SIMANTW.EXE` is not needed and
never read.

### API coverage

`scripts/apicoverage.py` joins the IR's `api:*` call sites against the
implemented Win16 surface plus an instrumented strict replay — the honest map of
which of the 196 imported ordinals are implemented, exercised, or stubbed.

---

## The hybrid workbench

Before the pipeline lifts a routine, `scripts/play.py` runs SimAnt in the
interpreter with the Win16 API serviced in Python and hand-recovered islands
installed — the bring-up + recovery bench:

```
python scripts/play.py --scale 2                          # play it (real window, audio)
python scripts/play.py --resume artifacts/snapshots/<snap>  # resume a snapshot
python scripts/boot.py [max_steps]                        # bring-up frontier report
```

Every VM stop, API gap, or wall violation goes to the **console** with CS:IP + a
named frontier — never trapped in the GUI.

---

## Setup

```
git clone --recurse-submodules <this repo>
# or, if already cloned:
git submodule update --init --recursive
```

## Working principles

- **Fail loud, never fake.** An unimplemented API / opcode / format stops with a
  named frontier rather than guessing.
- **The oracle decides correctness.** A stage is accepted only when its output
  matches the original binary byte-for-byte over a recorded demo. Never weaken a
  gate to make a slice pass.
- **Suspect the tooling first.** Every observed glitch is triaged as a possible
  lifter/emitter bug (oracle differential at the suspect site) before it's called
  a game fact.
- **Game logic stays framework-free**; the VM/lift/CPUless machinery stays in
  `win16_re/`/`dos_re/`, generic and reusable for the next game.

## Status

Live bring-up notes, the milestone journal, and the standing-mechanisms registry
are in [`docs/run_status.md`](docs/run_status.md) (newest on top). The endgame
design is [`docs/vmless_port.md`](docs/vmless_port.md). The test suite is the gate
— `python -m pytest -q` before any commit; never commit red.
