# CLAUDE.md — simant_port: reverse-engineering Maxis SimAnt

A byte-exact reverse-engineering port of **Maxis SimAnt** (`assets/ANTWIN/SIMANTW.EXE`)
onto the [`win16_re`](win16_re) game-agnostic Win16 framework, itself built on the
[`dos_re`](win16_re/dos_re) method (proven on DOS games: Prehistorik 2, Overkill). This
project is **only about SimAnt** — this is the game-port project, analogous to how
`pre2_port` sits on top of `dos_re` in the DOS ecosystem. Read [`AGENTS.md`](AGENTS.md)
and [`win16_re/docs/README.md`](win16_re/docs/README.md) first.

SimAnt is a full commercial Win16 app (6 code segs, KEYBOARD+WIN87EM, native inline x87,
raw INT 21h I/O, programmatic menus, 16-colour DIBs, **child windows within a window**,
huge-pointer tile renderers). It boots, runs in-game, and its source is being recovered
routine-by-routine — clean, readable, byte-exact Python in
[`simant/recovered/`](simant/recovered) with hot-loop islands in
[`simant/hooks.py`](simant/hooks.py) (see
[`win16_re/docs/lifted_islands.md`](win16_re/docs/lifted_islands.md)), each gated
byte-exact by an A/B oracle.

Boot it with `python scripts/boot.py` to find the next `win16_re` gap.

**win16_re is a git submodule** of this repo, pinned at `win16_re/`
(https://github.com/missingno7/win16_re.git), which itself vendors `dos_re` as its own
nested submodule — `git clone --recurse-submodules` (or
`git submodule update --init --recursive`) is all a fresh checkout needs. `simant/_env.py`
(+ `conftest.py`) puts `win16_re` on `sys.path`; `WIN16_RE_PATH` is a deliberate opt-in
escape hatch for co-developing `win16_re` itself against a separate working checkout (and
`win16_re` in turn honours `DOS_RE_PATH` the same way for `dos_re`).

**Our primary goal is reconstructing SimAnt's source code, cleanly and readably.**
Performance gained from a lifted island is a byproduct of recovery, not the goal itself —
an island *is* the recovered readable source, proven byte-exact against the original ASM.

## Layout

```
simant/           the adapter + recovered logic:
  _env.py           locates the win16_re submodule (WIN16_RE_PATH escape hatch)
  runtime.py        EXE_PATH, GAME_NAME, assets_present, create_machine, install_hooks
  recovered/        pure recovered SimAnt logic — never imports the VM or win16_re
  bridge/           the state-view seam (dgroup_view.py) — named DGROUP fields +
                    swappable backends (SelectorBackend/ByteBackend/OverlayBackend);
                    pure, VM-free.  Toward the VM-less port (docs/vmless_port.md)
  native/           NativeGameState — the owned address-space image the recovered
                    logic runs on with no VM (the native-mode target)
  hooks.py          lifted islands (hot ASM routines reimplemented, byte-exact)
  probes/           profile.py (PC-sampler) + symbols.py (SIMANTW.SYM name lookup)
  tests/            island A/B oracles + state-view seam + boot/splash gate
scripts/          play.py (interactive; --record-demo NAME -> artifacts/demos/,
                  --resume; dos_re hotkeys:
                  F10 screenshot, F11 demo-record toggle, F12 snapshot),
                  boot.py (frontier probe), replay.py (headless demo replay,
                  --from-snapshot for anchored demos),
                  liftverify.py (verify AUTO-LIFTED hooks vs ASM over a demo),
                  verifyislands.py (verify the PRODUCTION islands vs ASM over a
                  demo — the deterministic island-vs-original comparison),
                  checkpoints.py (deterministic checkpoint-digest trace of a demo
                  replay: --save a baseline, --check to catch a regression at the
                  first diverging checkpoint))
docs/             docs/run_status.md — the journal (newest on top)
assets/           SIMANTW.EXE + data files (gitignored, never committed)
win16_re/         the game-agnostic Win16 framework (git submodule)
```

**This is an AI-operated harness.** Only a human is needed to *play* (generate input);
everything else is for the agent. VM stops and gaps go to the **console** (stderr) with
CS:IP + instruction count + traceback + trace tail + API log — never trapped in the GUI.
Evidence tooling mirrors dos_re: demos (`scripts/replay.py`) and snapshots are the
deterministic verification baseline.

## Non-negotiables (inherited from dos_re/win16_re — enforced, not aspirational)

- Never commit red: `python -m pytest -q` green before every commit; one verified
  slice = one focused commit.
- Never weaken an oracle/test to make a slice pass. Blocked ⇒ revert + entry in
  `docs/run_status.md`.
- Fail loud, never fake: an unimplemented API/opcode/format raises; no silent
  plausible fallbacks. Implement observed behaviour, not datasheet generality — and if
  it's a genuinely new *mechanism* (not just SimAnt behaviour), it belongs in `win16_re/`,
  never in this repo.
- `win16_re/` never learns this game (no SIMANTW.EXE addresses/format knowledge stay in
  it); `simant/recovered/` never imports the VM.
- Update `docs/run_status.md` (newest on top) as you go; the next session resumes from
  git + the journal alone.
