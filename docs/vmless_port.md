# Toward a VM-less SimAnt port

The north star, adapted from `pre2_port`'s enhanced-layer endgame: **the win16
VM becomes an oracle/test harness, not the engine.** Recovered source runs
directly on the game's own state; the emulator is kept only to prove that source
byte-exact.

Where pre2 reached this for a whole DOS game (`play_native.py` cold-boots with no
emulator), SimAnt is early — 28 leaf islands (math helpers, LZSS, the tile-table
builders, the PRNG). This doc records the **direction and the seam** so every
future recovery lands VM-less-ready, not the claim that the native game runs yet.

## Execution modes (the target)

The original ASM runs only in oracle/verify modes — never as a silent fallback.

| Mode | What runs | Use |
|------|-----------|-----|
| **native (product)** | recovered source only, NO VM | the standalone game (not yet reachable) |
| **oracle / original** | pure original ASM (`--no-hooks`) | reference, capturing oracles |
| **hybrid (workbench)** | recovered islands over the VM | preparing/proving a new island vs the live ASM — `scripts/play.py` today |
| **verify** | ASM oracle vs recovered logic, diffed | the `tests/` A/B byte-exact gate |

No silent fallbacks: an unrecovered frontier fails loud (the VM stop / gap).

## The layering rule (never violated)

```
recovered logic  (pure — the WHAT)          simant/recovered/   e.g. srand_step(seed)
        │  human-named state, no offsets
        ▼
state-view seam  (the WHERE — layout)        simant/bridge/dgroup_view.py
        │  field -> backend.rb/rw/wb/ww(offset)
        ▼
backend  (the HOW)                           SelectorBackend (VM) | ByteBackend (native) | OverlayBackend (contract)
```

- `simant/recovered/` — pure recovered logic; **never** imports the VM.
- `simant/bridge/` — the layout bridge; pure Python, the ONLY place a migrated
  island's DGROUP offsets live; imports no VM and no runtime.
- `simant/hooks.py` — the VM island adapters (the workbench scaffolding).
- Lower (cleaner) layers must never depend back up on the VM / CPU / selector
  world.

## The state-view seam (realized)

`simant/bridge/dgroup_view.py` binds human-named fields to their DGROUP offsets:
recovered logic reads `s.rng_seed`, `s.map_cols` and never sees a byte. The view
holds a **backend**, and the same view + same logic runs over any of them:

- **`SelectorBackend(mem, seg)`** — the faithful hybrid backend: goes through the
  win16 VM's `mem.rb(seg, off)` so selector translation (RPL masking, >64 KB
  huge blocks) matches the VM exactly. This is what makes win16 different from
  pre2's flat `DS<<4` DOS — the DGROUP base is selector-relative, not constant.
- **`ByteBackend(image, dgroup_base)`** — flat indexing into an owned image
  (`NativeGameState.data`) at DGROUP's linear base. The native runtime + the
  memcmp verification path.
- **`OverlayBackend`** — a read-through overlay accumulating a `{offset: value}`
  write contract, for future whole-routine transforms that return a write set.

`simant/native/state.py`'s `NativeGameState` *is* the address-space image
(`.data`, exactly what the VM's `mem` exposes), so the same bridges run over it
unchanged; `.from_machine()` is the bootstrap (VM image → owned image).

### Proven in-use

The `_SRand*` / `Set`/`GetSRandSeed` islands read and write the LFSR seed through
`SimAntState.rng_seed` (not a raw `0xCBF2`), and stay byte-exact against the ASM
oracle. `tests/test_state_view.py` pins that the SAME view + recovered
`srand_step` produce identical results over a live VM and an owned
`NativeGameState` — one implementation, two backends, no second copy that can
drift.

## Growing it

Each migrated island: (1) name its DGROUP fields once in `SimAntState` (or a
shared `StructView`), cross-checked against `SIMANTW.SYM` and the routine; (2)
have the island read/write through the view; (3) keep the A/B oracle green. The
offset map is the *optional* half — a future field-backed backend could drop
offsets entirely behind the same view API.

The far endgame (native cold boot) needs the core loop recovered — the message
pump, the simulation tick, window/paint. That is a long road; this seam is the
groove it runs in.
