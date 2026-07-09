# AGENTS.md — simant_port

These instructions apply to the whole repository. They are written for AI
agents and humans working on the SimAnt reverse-engineering port. Start with
[`CLAUDE.md`](CLAUDE.md) (the operational brief) and
[`win16_re/docs/README.md`](win16_re/docs/README.md) (the method).

## What this repository is

An **oracle-driven reverse-engineering port of Maxis SimAnt** — the sole game this
project is about. It vendors [`win16_re`](win16_re) (the game-agnostic Win16 framework:
NE loader, the Win16 API surface, the memory model, rendering, compositing) as a **git
submodule**, which in turn vendors [`dos_re`](win16_re/dos_re) (the 8086/80186 VM) as its
own nested submodule. `git submodule update --init --recursive` after cloning;
`WIN16_RE_PATH` overrides to a separate `win16_re` checkout when actively co-developing
that framework itself.

## Working principles

Correctness beats speed. Traceability beats cleverness. Small verified progress
beats large intuitive rewrites.

- **This repo is the only place SimAnt-specific knowledge lives.** `win16_re/` (and,
  nested inside it, `dos_re/`) must never learn anything about SimAnt — no addresses,
  filenames, formats, or per-title behaviour. If you find yourself wanting to add
  SimAnt knowledge to `win16_re/`, that is a sign the change belongs here instead, or
  that `win16_re/` is missing a genuinely general mechanism.
- **Do not make the OS layer more general than SimAnt requires.** A new API / DOS
  service / opcode in `win16_re/` is added only when SimAnt actually calls it,
  identified from its *actual call site* (not guessed), with the observed
  argument/return contract documented. Datasheet completeness is scope creep.
- **Fail loud, never fake.** An unimplemented API / opcode / DOS service raises
  with precise context (`Win16ApiGap` / `NotImplementedError` with CS:IP). It
  does not return a plausible stub to "keep things moving". The honest frontier
  is the value.
- **Behaviour changes need tests, and never commit red.** `python -m pytest -q`
  is green before every commit; one verified slice = one focused commit.
- **Never weaken an oracle/test to make a slice pass.** A lifted hook is
  accepted only when an A/B run (original ASM vs. the Python replacement) is
  pixel- and state-identical. Blocked ⇒ revert, and record the repro in
  `docs/run_status.md`.
- **Determinism is a feature.** The deterministic paths (headless replay, no
  wall clock) stay deterministic; time-driven behaviour (the interactive
  driver, `GetTickCount`'s instruction-derived clock) is deterministic or
  clearly opt-in.

## Where things live

```text
simant/           the adapter + recovered logic (see win16_re/docs/lifted_islands.md):
  _env.py           locates the win16_re submodule (WIN16_RE_PATH escape hatch)
  runtime.py        EXE_PATH, GAME_NAME, assets_present, create_machine, install_hooks
  recovered/        pure recovered SimAnt logic — never imports the VM or win16_re
  hooks.py          lifted islands (hot ASM routines reimplemented, byte-exact)
  probes/           profile.py (PC-sampler) + symbols.py (SIMANTW.SYM name lookup)
  tests/            island A/B oracles + boot/splash gate
scripts/          play.py (interactive), boot.py (frontier probe), replay.py (headless)
docs/             docs/run_status.md — the journal and standing-mechanisms registry
assets/           SIMANTW.EXE + data files (gitignored, never committed)
win16_re/         the game-agnostic Win16 framework (git submodule; dos_re nested inside)
```

## Standard commands

```bash
python -m pytest -q                       # the suite — green before every commit
python scripts/boot.py [max_steps]        # bring-up frontier probe (honest report)
python scripts/play.py                    # play interactively (real window, input, audio)
python scripts/play.py --resume <snapdir> # resume from an F9 snapshot
```

## Things not to do

- Do not let `win16_re/` learn anything about SimAnt.
- Do not return guessed stub values to get past a fail-loud frontier — identify
  the call from its site first, then implement the observed contract.
- Do not "clean up" original-behaviour quirks (flag shapes, wrap semantics,
  return codes) without oracle evidence — they are load-bearing.
- Do not trust a probe's negative result until you've checked the code path
  actually consults the probe (see [`win16_re/docs/pitfalls.md`](win16_re/docs/pitfalls.md)).
- Do not treat performance, or a window merely being non-blank, as proof of
  correctness.
