# SimAnt — run status (newest on top)

## 2026-07-13 (cont.43) — tick-demo clock sideband; structural blocker isolated
- Continued #17: added the CLOCK SIDEBAND (win16_re 12164f8) — record every
  GetTickCount the game consumes (bucketed per tick, deltas off the boundary ms),
  replay by index (gameplay reads come first each tick -> align despite the
  shorter replay pacing spin).  Tap at USER.13 only; v4 replay byte-identical
  (74bf3228...).  Fallback past the recorded reads: a hybrid ramp-to-next-boundary
  + escape (handles BOTH the within-tick render-clock wait AND the long pre-tick
  init, which a pure ramp starved).  7 unit tests green (win16_re 122).
- MEASURED: the sideband makes replay markedly more faithful (reaches the tick-91
  divergence in 43.7M instrs vs 35.6M with the synthetic clock — closer to the
  real ~40M).  But it STILL diverges at ~tick 91 (the same _font_PrintStr ->
  seg7:ADE0 runaway on a WM_MOUSEMOVE to window 328).
- ROOT of the residual, now isolated: the clock-read SEQUENCE misaligns, not the
  values.  During recording the sim paces by SPINNING (100-250 GetTickCount reads
  through GR!_TickCount per tick), and WM_PAINTs (font blink) interleave into
  that long spin reading the clock; on replay the boundary is delivered on the
  FIRST ask so the spin is short and the paints interleave at different points,
  so the by-index clock stream drifts once a paint's reads land mid-bucket.  The
  gameplay (MYTIMERFUNC) reads DO align (they're first); the misalignment is the
  render/paint-side reads bleeding into the index.
- NEXT (design options, not yet chosen): (a) tag clock reads by CALLER site so
  render-side reads (masked from the gameplay digest anyway) use a separate
  channel/fallback and never consume the gameplay index; (b) advance the tick
  boundary to AFTER MYTIMERFUNC returns (so a bucket = exactly one sim tick's
  reads, no trailing pacing spin); (c) record the pacing-spin read COUNT and
  reproduce it.  (b) looks cleanest — the seam is MYTIMERFUNC's retf.
- Framework is landed + tested; SimAnt end-to-end faithfulness is genuine
  multi-pass RE.  Suites: win16_re 122, simant 356.

## 2026-07-13 (cont.42) — tick-demo record/replay pair (hook-config-invariant); 91 ticks
- Built the win16 hook-config-INVARIANT demo model (win16_re cae3550): keys input
  to the GAME TICK (the sim WM_TIMER consumption), not the instruction count, so
  ONE recording verifies identical gameplay under any hook config — the
  deterministic comparison v4 cannot give ([[demos-are-hook-config-specific]]).
  * `TickDemoRecorder` — taps every CONSUMED input msg (`is_input_message`:
    keyboard/mouse + WM_SIZE/COMMAND/HSCROLL/VSCROLL, minus machine-derived
    WM_CHAR) into per-tick buckets; each WM_TIMER closes a bucket (the boundary).
  * `TickDemoDriver` — replays ON DEMAND in consumption order (no pre-injection,
    no fetch-API coupling — both historical traps avoided); delivers the boundary
    WM_TIMER when the game asks, INCLUDING the (0,0) any-scan peek the cold-start
    modal spins on; serves GetTickCount from the recording; check-mode names the
    first divergent tick.
  * System taps behind `sysobj.tick_recorder`/`tick_driver`, guarded so v4 +
    interactive are untouched (v4 replay digest byte-identical 74bf3228...).
  * `scripts/tickdemo.py` — convert (v4->tick) / canonize (record per-tick
    digests, no hooks) / verify (--hooks = the cross-config proof).
  * 6 game-free unit tests; win16_re 121, simant 356 green.
- PROVEN: convert captured 1584 ticks from cold_nohooks; the tick DRIVER now
  traverses the real session (0 -> 91 ticks before diverging).
- BLOCKER (task #17): at ~tick 91 the no-hooks canonize replay DIVERGES from the
  recording (a runaway in _font_PrintStr -> seg7:ADE0 on drifted state).  Cause:
  GetTickCount faithfulness — the synthetic within-tick clock (base_ms + a
  32-calls/ms stall escape) does NOT reproduce the exact per-call GetTickCount
  values the recording saw, so clock-dependent state (animation/blink timers,
  tick-seeded RNG) drifts.  This is exactly dos_re.tick_demo's SIDEBAND case:
  record the clock the game consumed and inject it.  NEXT: add GetTickCount as a
  recorded per-tick sideband channel (capture at the read site; likely a small
  fixed set of reads/tick), then re-run canonize to convergence.
- Also: dos_re has a generic `step_probe` (trapped per-instance step observers) —
  our scratch probes hand-roll this; adopt when next touching them.

## 2026-07-13 (cont.41) — numpy-for-win16 assessed (measured); EndPaint 1.7x
- Owner: dos_re added a fast numpy renderer (7a4f7d8, 21x; policy 2169ebb "numpy
  first-class where it MEASURABLY wins") — is numpy usable for win16_re speedup?
- MEASURED the render share over the full cold_nohooks replay under PyPy (the
  live-play host, 199.6M instrs, 50.9s): SetDIBitsToDevice 4.2% (already numpy),
  EndPaint clip-restore 3.3%, FillRect 1.2%, everything else ~0 (BitBlt/
  StretchBlt/Polygon/TextOut are COLD on SimAnt: 0-105 calls).  Render is ~10%
  of wall; the other ~90% is the interpreter — dos_re's documented numpy
  ANTI-WIN (the real win16 speedup path stays island/native recovery).
- win16 had already adopted numpy in the big blits (SetDIBitsToDevice, Scroll-
  Window, compositor) before the dos_re policy.  The one measured improvement:
  `_apply_paint_clip` (win16_re bc5c4aa).  Three shapes measured on the same
  workload: original numpy with bytes()/tobytes() round-trips = 4 full-frame
  copies (2.0 ms/call); pure-bytearray row loop (1.46 ms — ~2us/row-slice on
  PyPy); writable np.frombuffer VIEWS + one 2D copy per rect + 2 memcpys =
  1.19 ms/call — the residue is cpyext crossing cost (~50 np calls/paint), the
  PyPy floor.  Replay digest byte-identical (74bf3228...), suites green.
- PyPy LESSON (recorded for future perf work): numpy-on-PyPy pays ~20us per
  call boundary (cpyext) — whole-frame ops win, per-rect/per-row numpy is
  bounded by crossings; always measure per-interpreter.
- Also bumped dos_re 32c7f71 -> 121f9e0 (+3, PM-only; NOTE: new generic
  `dos_re.step_probe` — trapped per-instance step observers, exactly what our
  scratch probes hand-roll by monkey-patching CPU8086.step; future cleanup can
  adopt it).  dos_re 375 / win16_re 115 / simant 356 green.

## 2026-07-13 (cont.40) — synergy: win16.tick_demo seam over dos_re.tick_demo
- Owner: "kick the can down the hill simant_port -> win16_re -> dos_re".  Bumped
  dos_re e19bb1a -> 32c7f71 (+3, PM-only: sblaster dma_tc snapshot + pm demo
  determinism; shared 16-bit modules byte-untouched); win16_re 6a54c23.
- Built the REUSABLE win16-layer half of the tick-demo adoption (cont.38 synergy
  #1): `win16/tick_demo.py` (win16_re ce43bd3) — re-exports dos_re.tick_demo's
  engine (a win16 machine's `.cpu` IS the dos_re CPU8086, so `record_ticks(machine,
  ...)` drives it with no shim) + `input_demo_drive`: the win16 recording drive
  that replays a v4 input demo and turns DemoEnded into "drive done".  Game-free
  unit tests (tests/test_tick_demo.py).  win16_re 115 green.
- PROVEN end-to-end on SimAnt (scratch smoke, not committed — needs the EXE +
  cold_nohooks): `record_ticks` captured **537 sim ticks** over the cold_nohooks
  drive (seed = full 4MB image, 537 unique digests), save/load round-trips.
- SEAM FOUND: the SimAnt sim tick = **`SIMANT!MYTIMERFUNC` (seg1:2440)** — the
  WM_TIMER TimerProc body (dispatched via the 0100:2440 callback thunk, ~538×
  over cold_nohooks).  This is the tick-demo adapter's seed_ip/commit_ip seam.
- NEXT (task #17, the game-specific half — bigger): observe callbacks at input-
  consumption sites, a GetTickCount sideband channel, and THE gameplay-owned
  DGROUP region + exclusion mask (the real RE piece), plus a native tick() once
  NativeGameState can advance one sim tick.  Then one tick-demo recording
  verifies across every hook config AND the VM-less native core — the fix for the
  v4 cross-config desync ([[demos-are-hook-config-specific]]) and the engine-flip
  exit condition.

## 2026-07-13 (cont.39) — dos_re bumped +28 commits; no shared-module changes
- Bumped dos_re af0db41 -> e19bb1a (28 commits).  ALL of it is the 32-bit PM
  stack (pm_player audio/demo bring-up, sblaster DMA, cpu386 REP fast paths,
  dos4gw fixes, cfg32/emit32) — verified the shared 16-bit/win16-relevant
  modules (cpu.py, memory.py, verification.py, lift/cfg+emit, tick_demo,
  coverage, input_demo, snapshot) are byte-untouched between the pins.  Zero
  risk; suites green (dos_re 366, win16_re 112, simant 356).  win16_re ec9106f.
- Synergy findings (patterns, not code we can import directly):
  1. **`pm_composition` — observable-state verifier for NON-LEAF routines**:
     diff every byte EXCEPT the transient stack window [min_sp, entry_sp) and
     skip register diffs (install only where result regs are unused).  This is
     the contract our win16 verifier will need when recovering SimAnt's composed
     sim routines (_DoAntSim/_DoForageAnt call helpers; clean recovered source
     cannot reproduce callee stack spills byte-for-byte).  PM-bound impl (clones
     a PM runtime); a win16 analog would sit on win16.verify.clone_machine.
  2. cfg32/emit32 indirect-jump lifting (switch dispatchers lift as tail jumps)
     is 32-bit-only; the 16-bit lifter liftverify.py uses did not gain it.
     Porting that refusal-killer to lift/cfg.py+emit.py is upstream work to
     request if SimAnt auto-lifting starts hitting jmp-table refusals.
  3. The input_demo determinism fix (wall-clock-fired IRQ during record vs
     replay) is pm_input_demo-side; win16's v4 GetTickCount reproduction already
     handles our analog of that lesson.

## 2026-07-12 (cont.38) — dos_re bumped +30 commits; synergy assessment
- Owner asked to update dos_re and look for win16_re synergies.  Bumped the
  nested submodule 58a1a51 -> af0db41 (30 commits); all three suites green
  (dos_re 341, win16_re 112, simant 356).  win16_re 75796f2 pins it.
- SYNERGIES, in value order:
  1. **`dos_re.tick_demo`** — the promoted pre2_port endgame-equivalence engine:
     demos keyed to the GAME TICK (not instruction count), storing per-tick
     consumed input + sidebands + a masked digest of gameplay-owned state.  This
     is the designed fix for our v4 desync (cont.27: instruction-keyed input is
     mode-dependent; hooks change instruction timing).  SimAnt's tick = the
     ~59fps WM_TIMER sim tick.  Adopting it needs a win16 adapter: consumption-
     point key observers, GetTickCount sidebands, and the gameplay ownership
     mask (digest boundary).  THE roadmap item for the native-port endgame.
  2. **`dos_re.coverage`** — the measured "native %" collector, fed by
     `cpu.coverage_telemetry` on the SHARED CPU8086, so win16 gets it for free.
     Proven live on a cold_nohooks slice (71.9M instrs, per-island buckets,
     2.86M hook dispatches).  Honest % needs ASM-equivalents: run the verifier
     (verifyislands.py) with telemetry attached once -> save_cache() -> later
     replays estimate from the cache.  Cheap follow-up, gives the headline
     recovery metric.
  3. **`dos_re.frontend_timeline`** — per-present-frame screen timeline compare
     (the front-end analogue of tick_demo).  win16 analog would sample at the
     compositor/present boundary.  Relevant once a native front end exists.
  4. numpy is now first-class in dos_re policy (win16 always used it); dos_re
     also gained its own `checkpoints.py` (VM-until-checkpoint stepping off a
     HookTaxonomy phase map) — DIFFERENT from our scripts/checkpoints.py
     (digest-trace tool); name collision worth remembering.
  5. The 32-bit PM stack (LE loader / CPU386 / DOS4GW / pmlift) is DOS-only —
     no win16 relevance.
- NOT adopted this turn (scoped follow-ups): tick_demo adapter, coverage-with-
  verifier native-% run, frontend_timeline adapter.

## 2026-07-12 (cont.37) — live hooks crash: reachable render islands ruled out
- Owner report: LIVE play WITH hooks (46) crashes at 76.8M — _Punt (panic
  MessageBox, garbage caption + MB_ICONHAND) then runaway into the C-runtime
  _exit/B0FA garbage.  A REAL bug (live = native config, no demo/desync), unlike
  the cont.27 demo-replay _Punt (which was a no-hooks-demo-with-hooks desync).
- Made `capture_call_ab` FAST (replacement-hook detection instead of a per-step
  wrapper — ~7-12s to a ~39M render call).  A/B'd every render island reachable in
  cold_nohooks against the ASM over its first REAL call: _DoCalcTile,
  _XferTileColor, _XferLifeTileColor, _GenNestMap, _DrawChar all BYTE-EXACT (only
  don't-care stack/flag scratch, zero real memory/register diffs).  Ruled out.
- But `_os_ClipLine`, `_XferTileMono`, `_XferLifeTileMono`, `_GenOverMap` are
  NOT_REACHED in cold_nohooks (that session uses only colour tiles, no lines /
  mono / overlay-map) — the crashing session took different paths, so its culprit
  island isn't exercised by cold_nohooks and can't be A/B'd from it.
- Also confirmed: _win_GetObjRect's _win_LockWin/_win_UnlockWin (7:E3A8/E3A4) are
  genuine `retf` no-ops, so that island's skip is correct.
- BLOCKER: cold_nohooks runs clean to 199.6M (no crash), so I can't reproduce the
  live crash from it.  NEED: a HOOKS-recorded demo of a crashing session
  (`play.py --record <name>` — default installs hooks; play until it crashes).
  Replayed WITH hooks it reproduces deterministically (same config = no desync),
  and then bisecting islands / capture_call_ab at the real crash-path calls
  pinpoints the culprit.

## 2026-07-11 (cont.36) — captured-state A/B harness (unblocks sim-core recovery)
- Direction (owner): recover the simulation core toward the native backend.  The
  blocker was verification — ant-AI routines read live ant-array/map state through
  runtime selectors AND call PRNG subroutines, so they can't be exercised with
  synthetic inputs, and installing islands to run verifyislands over a no-hooks
  demo desyncs (~22M).
- Built `simant/tests/capture_ab.py: capture_call_ab(seg,off,island,demo|None)`:
  replays a demo with NO hooks (a faithful drive; None = free-run from boot),
  catches the nth real call of the target, snapshots the pre-state, runs the
  ORIGINAL ASM to the routine's own return (subroutine calls and all), then runs
  the island from the same pre-state and diffs the full 4 MB image + registers.
  Returns (mem_diffs, reg_diffs, stack_low).  This is test_native.py's _Unpack
  capture generalised to ANY routine — the pattern every stateful-sim recovery
  will verify with.
- Validated on `_Unpack` (reached during boot, no demo, 0.26s): the harness
  reproduces the manual finding exactly — only don't-care scratch differs
  (match_rem 0xB7D2 + the routine's own stack frame: freed locals below entry sp
  + the budget ARG the ASM decrements in place above sp, which the caller pops) +
  flags; ZERO decode-output bytes differ.  test_capture_ab.py classifies via a
  stack-window + known-scratch allowlist.  Suite 356 green.
- NEXT: recover a first stateful sim routine (its PRNG subcalls resolve to the
  already-recovered SRand/RRand family) and verify with this harness.  Late
  gameplay calls need a demo drive under pypy (the per-step catch is slow over
  tens of M instrs on CPython) — boot-reachable routines run in-suite.

## 2026-07-11 (cont.35) — _Unpack "DIVERGED" is benign scratch, decode is exact
- Investigated the one REAL (non-flag) divergence verifyislands flagged on
  cold_nohooks: `_Unpack`.  Captured the first real _Unpack call (fresh boot +
  cold_nohooks drive), snapshotted the pre-state, ran the ASM to its return vs the
  island, and diffed the whole 4 MB image.
- Result: exactly 4 bytes differ, ALL don't-care scratch — 2 = `match_rem`
  (DGROUP:0xB7D2, the documented don't-care the native test already excludes) and
  2 = the [sp-6] FREED-FRAME local (the island writes win_seg=0x02D6 there to
  reproduce the ASM's residue, but the ASM leaves 0x0000 on this exit path — the
  island's frame-residue guess is approximate for some resume codes).  Crucially,
  ZERO output-buffer bytes differ (classified: none fall in out_seg:out_off) —
  the LZSS decode is byte-identical.
- Conclusion: no functional _Unpack bug; the true exit-state fields (resume/flags/
  r/src_off/dx/cx/in_rem) are exact, only don't-care scratch differs.  So across
  all 46 islands the ONLY verifyislands divergences are don't-care flag/scratch
  residue — the recovered logic is functionally sound.  Left the island as-is
  (the freed-frame value is path-dependent and dead after retf; the game replays
  to 199.6M fine); no code change.

## 2026-07-11 (cont.34) — scripts/checkpoints.py: deterministic checkpoint traces
- Owner ask: "a way of deterministically checking/verification, like checkpoints."
  Built `scripts/checkpoints.py`: replays a demo and fingerprints the game-
  observable state (win16.vmsnap.digest: memory + CPU + every window surface +
  clock) at fixed instruction-count intervals -> a checkpoint TRACE.  Two replays
  of the same demo under the same config MUST produce an identical trace, so a
  saved baseline is a regression ORACLE: `--check` reports the FIRST diverging
  checkpoint (instr / digest / length) instead of only a far-downstream crash.
      checkpoints.py cold_nohooks --interval 25000000 --save cold.trace
      checkpoints.py cold_nohooks --interval 25000000 --check cold.trace  -> MATCH
- KEY detail: the session runs mostly inside ONE long WndProc/TimerProc callback
  that cpu.run() can't return from to sample — so checkpoints are captured via
  `sysobj.yield_check` (win16 call_far invokes it every ~8192 instrs DURING a
  callback), chained after DemoDriver's own yield hook.  This gives full coverage:
  8 checkpoints spanning the whole 199.6M-instruction cold_nohooks session, and a
  re-run MATCHes exactly (determinism proven).  `--hooks` mismatches a no-hooks
  baseline as expected (config-specific, per the instruction-keyed v4 model).
- Comparison logic extracted to `compare_traces()` + unit tests
  (test_checkpoints.py).  Suite 355 green.  This is the deterministic-verification
  substrate the earlier verifyislands/HookVerifier work was reaching toward — the
  digest-trace half (regression), complementing the per-call island-vs-ASM half.

## 2026-07-11 (cont.33) — verifyislands: cold-start demos + FLAGS_ONLY classification
- Surveyed the next-tier gameplay routines: _CountAnts (5:04DE) is large + calls
  _myBeginSong (plays population-milestone jingles) + reads 8 segments;
  _GetExitDirB/_IncFoodHere/_DeadAntHere all call subroutines / read runtime
  state.  The trivial predicates are exhausted; the real AI logic is complex and
  interconnected — needs the captured-state harness, not synthetic oracles.
- Improved `scripts/verifyislands.py` (51161de): (1) `--snapshot` optional — a
  cold-start demo (recorded from boot, e.g. `cold_nohooks`) replays from a fresh
  create_machine; (2) classify a divergence in ONLY the arithmetic flags
  (registers/segments/memory all match) as **FLAGS_ONLY** (undefined-after-routine
  residue no caller reads) instead of a scary "DIVERGED".  Over cold_nohooks the
  four divergences resolve to 3 flags-only (__aFuldiv, _DrawChar, _win_GetObjRect)
  + 1 REAL (`_Unpack`: 1 scratch byte at ~0x77E38 — worth a look; the native
  unpack test already excludes match_rem as don't-care).
- KNOWN LIMIT: verifyislands over a NO-HOOKS demo still terminates early with an
  OrphanReturnError (~22.7M on cold_nohooks) — installing islands shifts the
  instruction timeline so the callback nesting desyncs.  Full-session island
  verification needs a demo recorded WITH the islands (same config), OR a fix to
  the callback-frame tracking under the verifier's clone re-runs.

## 2026-07-11 (cont.32) — recovered _IsItDirt (tile-type predicate)
- `_IsItDirt` (seg5:1182): diggable dirt tiles are 0x20..0x2E (signed); AX=1/0,
  clobbers dx(=arg).  Companion of `is_it_food`; both now in recovered/gameplay.py.
  A/B oracle over in/out/edge/negative values; byte-exact.  46 islands, 350 green.
- Next tier (`_CountAnts` 5:04DE, `_DeadAntHere` 6:28C0) is multi-segment (reads
  ant arrays via runtime selectors 0x5294/0x5ef3 + count segs [0xC2D2/4/6]) — not
  cleanly A/B-testable from a fresh machine without seeding that state, so it
  needs a captured-state harness rather than the synthetic-input oracle.

## 2026-07-11 (cont.31) — recovered _IsYellowAnt + _InNestBounds (gameplay predicates)
- `_IsYellowAnt` (seg5:5720): returns 1 when the caste/marker value is 0xFE or
  0xFF (the yellow-ant sentinels marking the player-controlled ant), 0 otherwise
  — a full-16-bit equality compare (0x1FE/0x1FF are NOT yellow).
- `_InNestBounds` (seg5:115C): valid nest cell = x in 0..0x3F and y in 1..0x3F
  (the 64x64 grid, row 0 excluded), signed compares.  Island reproduces the dx
  clobber residue (= x if the x-check failed, else y — the ASM reloads dx before
  the y-check).  These map the nest coordinate space + player-ant markers the
  bigger AI routines share.
- Both in `recovered/gameplay.py` (is_yellow_ant / in_nest_bounds); AX=1/0
  islands, clobbered residue reproduced, rest preserved.  A/B oracles over
  in/out/edge/negative-as-16bit values; byte-exact.  45 islands, suite 342 green.

## 2026-07-11 (cont.30) — GDI.36 Polygon (frontier from a cold no-hooks demo)
- Owner recorded a COLD-START no-hooks demo (`cold_nohooks`, 11845 records) and
  confirmed the ghosting is GONE (validates cont.29 — the ghosting was the play.py
  display race, not the VM).  New frontier: `GDI.36` not implemented, called from
  `GR!_TrapFill+0xB3` (seg2:533F) during a WM_PAINT @199.4M instructions.
- GDI.36 = **Polygon(hdc, lpPoints, nCount)** (the call site pushes hdc, a far
  POINT* and count=4 — a filled trapezoid; _TrapFill draws nest cross-sections /
  terrain shading).  Implemented in win16_re (d44c2c7): even-odd pixel-centre
  scanline fill with the DC brush + 1px Bresenham pen outline, surface-clipped;
  `_read_points`/`_fill_polygon`/`_draw_line`/`_pen_rgb` helpers + GDI.36 handler +
  ordinal name.  Unit tests (tests/test_polygon.py).
- END-TO-END: `replay.py cold_nohooks` (cold start, pure ASM) now runs to demo
  exhaustion — 199,612,542 instructions, clock 62365ms, digest 74bf3228... — past
  the old gap.  win16_re 112 green, simant 324 green.
- NOTE: `cold_nohooks` is a clean cold-start no-hooks demo (no snapshot anchor),
  so it replays faithfully via replay.py — a much better regression baseline than
  the snapshot-anchored ones (which desynced).  Owner keeps the file; demos are
  recreatable so it isn't committed.

## 2026-07-11 (cont.29) — ghosting ROOT CAUSE: a play.py display race, NOT the VM
- Owner: ghosting appears with NO hooks too -> suspected a serious VM bug.
- Method: replayed the no-hooks demo_185520 FAITHFULLY (pure ASM via replay.py's
  path — no island desync), dumped the map child surface (423x346) at EVERY
  EndPaint, and diffed pre/post-scroll frames against the clean-shift hypothesis
  (post == pre shifted by (dx,dy), only the newly-exposed strip differing).
- RESULT: all 14 scrolls — 8 horizontal (dx=16), 5 vertical (dy=-16/80), and the
  big diagonal (dx=192,dy=144) — are a PIXEL-PERFECT clean shift: **0 overlap
  mismatch**.  Plus: the hugeheap maps the map DIB's >64K huge selectors
  contiguously (sel_base[base+8k] = lin+64k*k), so the tile-write and DIB-read
  paths agree; `_XferTileColor` is byte-exact; the compositor is stateless
  (rebuilt each frame); the version gate redraws on any surface touch.
- CONCLUSION: the VM/win16 RENDERING CORE IS CORRECT — the ghosting is NOT a VM
  bug.  The only headless-vs-live difference is threading: play.py runs the CPU on
  a worker thread (surface numpy-blits) and the display on the tkinter thread
  (composite copy).  The `_composited` version fence catches writes that COMPLETE
  mid-copy but NOT a blit IN FLIGHT -> a torn/ghosted frame.  This exactly
  explains headless-perfect + live-ghost.
- Tried a coarse render_lock (worker holds it around each cpu.run burst; display
  around the composite) — REVERTED: deadlocks, because cpu.run BLOCKS in a
  GetMessage wait (idle/modal) while holding the lock, starving the GUI.  A safe
  fix needs fine-grained locking around the surface mutations themselves (win16_re
  gdi/user blit sites) or double-buffered surface swaps — a careful follow-up, not
  a quick change.  No code shipped; suite 324 green.
- Caveat: this explains TRANSIENT tearing during active redraw.  If the owner's
  ghosting is PERSISTENT (stays after scrolling stops), that is a different bug —
  an F10 screenshot of it would settle which.

## 2026-07-11 (cont.28) — deterministic demo comparison: scripts/verifyislands.py
- Owner asked for a way to make replays "deterministically comparable" (like
  pre2_port's verify_native_tick_demo.py).  pre2_port isn't on this machine and
  dos_re doesn't vendor a native-tick verifier, BUT the engine underneath is
  dos_re's `HookVerifier` (already wired for auto-lifted hooks via liftverify.py).
- Built `scripts/verifyislands.py`: installs the PRODUCTION islands (hooks.py),
  wraps each (or `--only NAME`) in the HookVerifier, and replays a demo — every
  island CALL clones the machine, re-runs the ORIGINAL ASM from the same
  pre-state to the island's continuation, and diffs full CPU state + memory.
  This is config-invariant and deterministic: it checks each call at its REAL
  pre-state, so the verdict holds even when the overall replay timeline drifts
  (unlike comparing two whole-run replays, which desync — see cont.27).  Sweeps
  all islands, continuing past a divergence (retire-from-verify, keep running).
- First sweep over demo_185520 (samples=2): **10 islands fully byte-exact** (SRand
  family, _win_IsWinOpen, bytecopy, _SetSRandSeed).  4 DIVERGED — `__aFuldiv`,
  `_DrawChar`, `_win_GetObjRect`, `_Unpack` — but ALL are FLAGS-ONLY (arithmetic
  ZF/SF/PF residue the islands leave at entry instead of the ASM's computed
  value); `_Unpack` also differs by 1 scratch byte.  Registers + memory otherwise
  match -> NO island corrupts game state (reconfirms the ghosting isn't island
  pixels).  Also verified `_XferTileColor` byte-exact (3 calls) directly.
- Root of the flag gap: the unit A/B oracle (test_hooks.py) compares registers,
  NOT flags; the HookVerifier compares flags too.  FOLLOW-UP: make those 4
  islands flag-exact (or establish the post-routine flags are don't-care), and
  investigate the 1-byte _Unpack scratch diff.
- Limitation: snap_185520 predates callback-frame recording, so the sweep hits
  an OrphanReturnError at ~0.8M and 28 islands stay NOT_REACHED — re-take the
  snapshot (and record a hooks demo) for a full-session sweep.  Smoke test added
  (test_verifyislands.py); suite 324 green.

## 2026-07-11 (cont.27) — "ghosting worse with hooks" -> a demo/hook desync, NOT an island bug
- Owner report: quickgame scroll ghosting, "worse with our hooks"; demo
  `demo_185520.jsonl` (anchored snap_185520 @ 30.8M).
- Replay WITH 43 hooks HANGS/crashes at ~41.7M via a runaway: chain is
  `_DrawMapData`->`_win_DrawHBar`->`_win_SetColorFromObjNum`->`_win_ObjAddr(objnum)`
  with a garbage objnum (0xE147) -> `_Punt` (panic) -> `_exit` -> the C runtime
  `__ctermsub` atexit walk hits a garbage far ptr and runs away in a data
  selector (B0FA) until CallbackOverrun.  Replay WITHOUT hooks reaches ~97M
  (near the demo's 103.8M end).
- Bisect fingered `_XferTileColor`, BUT a definitive A/B (logged every mem write
  on the 64K-crossing calls) proves the island is BYTE-EXACT vs the ASM — same
  128 writes (seg:off:val) AND same exit registers.  The earlier 21-byte "diff"
  was just the ASM's pushaw/popaw stack residue below SP.
- ROOT CAUSE: **this demo was recorded --no-hooks** (no-hooks replay ~97M vs
  hooks ~41.7M).  v4 demos inject input keyed by INSTRUCTION COUNT; islands
  execute a routine in 1 VM step instead of hundreds, so replaying a no-hooks
  demo WITH hooks desyncs the injection timeline -> input lands at the wrong game
  state -> garbage objnum -> _Punt.  Removing an island merely shifts the
  timeline enough to dodge the divergence (why the bisect misled).  So the crash
  is a replay/methodology artifact, and hooks do NOT change rendered pixels (the
  tile islands are byte-exact) -> the ghosting is a win16_re update-region/clip
  issue, independent of hooks; "worse with hooks" is a scroll-cadence/timing
  perception, not pixel corruption.
- TAKEAWAYS: (1) a v4 demo can only be replayed under the SAME hook config it was
  recorded with; cross-config replay silently desyncs (worth a guard/warning).
  (2) Don't chase `_XferTileColor` — verified correct.  (3) To fix the ghosting I
  need a hooks-recorded scroll demo or F10 screenshots (this no-hooks demo can't
  be run with hooks).  No code changed.

## 2026-07-11 (cont.26) — audio: music re-enable doesn't resume (investigation)
- Owner report: in-game, the music button disables music (stops it) but
  re-enabling doesn't resume.  Provided a no-hooks demo `demo_184143.jsonl`
  (anchored to snapshot snap_184142 @ instr 41.7M) toggling music off/on.
- Replayed it headless with logging backends: during the entire re-enable the
  game makes only 2 GetProcAddress(0x100,...) calls and ZERO mciSendCommand /
  BeginSong / backend play — i.e. NO music playback command is issued on
  re-enable.  So the failure is UPSTREAM of the host audio backend (our backend
  isn't being asked to play), not a MidiBackend bug.
- Also found: the snapshot does NOT preserve audio state (`mci_state` is None
  post-restore; api.services aren't pickled, only sysobj+memory), and the game's
  MMSYSTEM module handle (`[0x8d08]=0x100`) + song-active flags (`[0xaf0]`/`[0xaf2]`
  = 0) leave `_MciMessage` bailing at its `cmp [0xaf2],0; je` gate.  So this
  snapshot-anchored demo cannot faithfully replay the live MCI path — repro needs
  a COLD-BOOT (non-snapshot) toggle recording.
- Game music map: toggle writes songs_on (0xAF6) via MAINWNDPROC (seg1:2EB6);
  the seg2 GR music engine (`_myBeginSong`/`_MciMessage`/`_StopSong`/
  `_myServiceSong`/`_MultiMediaSong`) drives MCI through GetProcAddress'd
  mciSendCommand, gated by 0xAF0/0xAF2 (song active) + songs_on.
- Action (win16_re 414a2d6): added `[mci]` console logging of every
  state-changing MCI command (open/play/stop/pause/resume/close) with dev+song,
  pairing with the existing `[audio] MIDI play #N` log.  A live toggle will now
  show whether re-enable issues a play at all — the observation that localizes
  the bug (game-logic/state vs backend).  Asked owner for a cold-boot repro +
  the console lines.  win16_re 107 green, simant 322 green.

## 2026-07-11 (cont.25) — audio: MCI song restart-storm (sound looping)
- Owner report: at startup a sound plays over music "over and over"; same with
  --no-hooks (so it's the win16 audio layer, not islands).
- Investigation (headless can't reproduce — the game issues exactly ONE MCI
  open+play per song in every deterministic run: free-run to 60M instrs, the
  `newcold` gameplay demo replay, both show one GAMETHME.MID play).  Mapped the
  game's sound path: `_myBeginSong`/`_MciMessage` (seg2) drive MCI via
  GetProcAddress'd mciSendCommand; `_TryAntTheme` (5:1414) rotates ANTTHME1-3
  every ~7200 `_TickCount` units (GetTickCount-based); `_myServiceSong` just
  pumps PeekMessage to flush MCI notifications.  SOUND dir: GAMETHME is a full
  ~30s song; ATTACK/HAPPY/JOLLY/etc. are short stinger .mids.
- Root cause (host side): `MidiBackend.play()` reloaded+restarted the song on
  EVERY MCI_PLAY.  A game re-issuing MCI_PLAY while polling status restarts it
  each poll -> the same clip stutters "over and over".  Fix (win16_re eca4b4c):
  guard play() on `music.get_busy()` — real MCI continues an already-playing
  device; only (re)start a song that isn't currently sounding (fresh play or a
  loop after it ended).  Plus a per-(re)start console log ("[audio] MIDI play
  #N: NAME") so the exact looping .mid is visible live.  Unit tests added.
- Could not reproduce headless, so this is the correct-semantics fix + a live
  diagnostic; asked owner to re-run and report which NAME repeats if it persists.
- win16_re 107 green, simant 322 green.

## 2026-07-11 (cont.24) — fix in-game EndPaint crash + crash snapshots
- In-game VM STOP (owner report, ~55.8M instrs): `EndPaint` reshape ValueError
  "cannot reshape array of size 439074 into shape (288,423,3)".  Root cause: a
  wndproc RESIZED the client surface (346->288 tall) between BeginPaint and
  EndPaint, so the pre-paint pixel snapshot (346*423*3=439074) no longer matched
  the surface (288*423*3).  Fix (win16_re 7718175): extracted the clip-restore to
  `user.py: _apply_paint_clip`, which no-ops when the snapshot size no longer
  matches the surface (the wndproc already repainted the resized surface — never
  crash on a mid-paint resize).  Regression test `tests/test_paint_clip.py`.
- Owner ask "crashes should produce snapshots": play.py now saves a best-effort
  post-mortem snapshot to artifacts/snapshots/crash_<time> on any VM stop — no
  quiescence pause (the CPU already halted at the fault); save_snapshot works at
  any instruction boundary.  (simant 5d3becc; bumps the win16_re submodule.)
- win16_re suite 104 green, simant suite 322 green.

## 2026-07-11 (cont.23) — PIVOT to the simulation core: recovered _IsItFood
- DIRECTION (owner): steer recovery toward the "pure gameplay part" so a modern
  native backend can be built on it later.  Rendering primitives (blitters, the
  _PlotLine/_PlotMonoLine rasterizers) would be *replaced* by a native backend —
  low value to grind further; the *simulation* logic must stay byte-exact.
- Surveyed the binary: the gameplay core lives in seg6 (ant AI — _DoAntSim,
  _DoForageAnt, _DoFightA, _SimQueenA, _DoDigOutAntA, ...), seg5 (colony/world —
  _CountAnts, _PickupFoodA, _DigMyTile, _MoveSpider, _ScanForAnts, _MakeMap), and
  seg7 (world sim — _SimBird/_SimCat/_SimDog, _DoAntLions, _RandWorld, _InitSimVars).
- First gameplay island: `_IsItFood` (seg6:2D1A) -> new `recovered/gameplay.py`.
  A world-state flag ([0xC320]:[0x9B6E]) picks the food tile range (inside nest
  0x18..0x27, outside yard 0x48..0x4B).  Island returns AX=1/0, clobbers dx(=arg)
  and es(=world selector), preserves the rest.  A/B oracle over 12 tiles x 2
  modes: byte-exact.  43 islands, suite 322 green.
- NOTE: _PlotLine (6FBE) analysis done but intentionally NOT recovered (rendering,
  not gameplay).  Next gameplay targets: _CountAnts (5:04DE), the food carry pair
  _PickupFoodA/_DropFoodA (5:0D18/0D86), _InNestBounds (5:115C) — these start
  mapping the ant-list + map data structures the bigger AI routines share.

## 2026-07-11 (cont.22) — recovered _os_ClipLine (midpoint line clipper)
- `_os_ClipLine` (seg4:6E24): the line-drawing family's clipper — a NEAR-call,
  register-in/out routine (endpoints si/di + dx/bx; clip bounds in DGROUP words
  0x1D7A/0x1D78; persistent swap-parity in 0x1D82; returns CF=reject/accept).
  Cohen-Sutherland outcodes for trivial accept/reject, then integer *midpoint
  subdivision* (`add`/`sar 1` bisection with an `inc` nudge) to walk whichever
  endpoint is out onto the violated edge, looping until both codes clear.  New
  `recovered/geometry.py: clip_line` (+ `_outcode`, `_sar1_sum`, `_bisect`),
  signed-16-bit exact, fail-loud convergence guards.  Island preserves ax
  (push/pop), reproduces the clobbered `cx` residue (last b-midpoint) and the CF
  output.  A/B oracle: 12 hand cases (every trivial/crossing case) + a 150-line
  deterministic fuzz over random endpoints & bounds — si/di/dx/bx/cx + CF +
  0x1D82 all byte-exact.  42 islands, suite 298 green.  (_PlotLine 6FBE /
  _PlotMonoLine 71A7 — the consumers — are the next of the family.)

## 2026-07-11 (cont.21) — recovered _MoveTextToBalloon (inverting bitmap blit)
- `_MoveTextToBalloon` (seg4:6CF8): copies a `{u16 width, u16 height, far* pixels}`
  source bitmap into a destination DIB, XOR-ing every byte (invert) and landing
  source rows on every other dst scanline (dst step = dst_stride*2 - src_stride).
  Shares the _CopyChar family's DGROUP stride scratch (0x1D70) + row-0 offset
  `4+x+((y*2)&0xFF)*(dst_stride&0xFF)` + all-registers-preserved profile.
  `recovered/render.py: move_text_to_balloon` returns {dst_off: byte^0xFF}; island
  reads the struct's nested pixel far ptr.  A/B oracle over x/y/dst-width/src-size
  grids incl. 640-wide: DIB bytes + stride global + regs byte-exact.  41 islands,
  suite 285 green.  (_CopyChar / _CopyCharRep / _MoveTextToBalloon = the seg4:6C62
  DIB-glyph blit family, now complete.)

## 2026-07-11 (cont.20) — recovered _CopyChar / _CopyCharRep (DIB glyph blits)
- `_CopyChar` (seg4:6C62) + `_CopyCharRep` (seg4:6CAA): blit a 16-row glyph into
  a DIB.  Byte stride = header width word >> 3 (cached to DGROUP scratch 0x1D70,
  segment 0x6902 which == the DGROUP selector).  Row 0 lands at
  `4 + x + ((y*2)&0xFF)*(stride&0xFF)` past the DIB offset (the +4 skips the two-
  word inline header; the y term is an 8-bit `mul dl`), each row steps by the
  full 16-bit stride.  `_CopyChar` copies one source byte per row (`movsb`);
  `_CopyCharRep` `rep stosb`s each source byte `rep` times horizontally.
  `recovered/render.py: copy_char_rep` returns {dst_off: byte}; `copy_char` is the
  rep==1 specialisation.  The two share an identical 0x33-byte prologue and only
  diverge at the row-advance (`stride-1` vs `stride-rep`); sigs extended past the
  split.  Both preserve every register (pushaw/popaw + ds,es).  A/B oracle over
  x/y/width (and rep) grids incl. 640-wide: pixels + stride global + regs
  byte-exact.  40 islands, suite 281 green.
- FOLLOW-UP (done, commit 2a3bd41): `_GenNestMap` had been recovered twice — a
  dead sparse-dict `gen_nest_map` island shadowed by the live `gen_nest_map_cells`
  generator one.  Dropped the dead island + orphaned render fn; one clean impl.

## 2026-07-11 (cont.19) — recovered _exchange (two-buffer byte swap)
- `_exchange` (seg4:6E05): swaps `count` bytes between two far buffers, byte by
  byte in order — reads both current bytes before writing both, so overlapping
  buffers behave exactly as the ASM's in-order `lodsb`/`stosb` loop.
  `recovered/byteops.py: exchange` takes injected read/write closures (stays
  VM-free, addresses with the routine's 16-bit offset wrap); island wires them to
  VM memory.  Every register preserved (pushaw/popaw + push/pop ds,es), so the
  oracle only needs the two buffers + regs.  A/B byte-exact over counts 1/4/8/16.
  38 islands, suite 273 green.

## 2026-07-11 (cont.18) — recovered _GenNestMap (nest-map terrain classifier)
- `_GenNestMap` (seg4:4754): classifies a 64x64 terrain layer into fill bytes —
  0xFE/0xFF -> val_feff, bit7-set -> val_high, else -> val_else; empty (0) cells
  take `table[src2[dx]>>2]` (mode==0) or are skipped.  Sibling of _GenOverMap
  (same column-major cursor, all-registers-preserved via pushaw/popaw).
  `recovered/render.py: gen_nest_map` returns the sparse write set; island echoes
  the table base + 3 fill bytes to DGROUP scratch (0x1B78/0x1B7A/0x1B7B/0x1B7C).
  A/B oracle over both modes with a src grid hitting every branch: 4 KB grid +
  globals + registers byte-exact.  37 islands, suite green.  The GenOver/GenNest
  terrain-map generator pair is now recovered.

## 2026-07-11 (cont.17) — recovered _GenOverMap (terrain overlay compositor)
- `_GenOverMap` (seg4:46E9): composites two column-major source layers through
  LUTs into a 64x128 row-major overlay.  Per cell: primary `src[cx]` (cursor
  steps 0x40/col, +1/row); nonzero -> `table1[a>>3]`; else if mode==0 ->
  `table2[src2[dx]]`; else SKIP (dst byte unchanged).  `recovered/render.py:
  gen_over_map` returns the sparse write set; island applies it, echoes the two
  table bases to DGROUP scratch (0x1B76/0x1B78), all registers preserved
  (pushaw/popaw).  A/B oracle over both modes: full 8 KB grid + globals + regs
  byte-exact.  36 islands, suite 269 green.  (Sibling _GenNestMap 4754 next.)

## 2026-07-11 (cont.16) — recovered _CopyName (NetBIOS 16-byte name-field copy)
- `_CopyName` (seg4:7438): space-fill 16 bytes, copy min(strlen(src),16), force
  byte 15 to NUL — the fixed-width NUL-anchored name records NetBIOS uses.
  `recovered/netbios.py: copy_name` + `cstrlen`; island reproduces the clobbered-
  register residue (ax=0, bx=dst_off, cx=0, dx=src_seg, es=dst_seg; si/di/bp/ds
  preserved).  A/B oracle over empty/short/exactly-16/over-16/embedded-space
  names; byte-exact.  35 islands, suite 267 green.

## 2026-07-11 (cont.15) — recovered _WindowsMono_MakeTable2x2a/b: the mono MakeTable family is COMPLETE
- The half-resolution mono packer: 2 scanlines, FOUR tiles per byte at 2 bits each
  (slots 0xC0/0x30/0x0C/0x03), count 0x20 (a) / 0x10 (b), stride == count; same
  SS pattern table as the 4x4 packer (rows 0..1).  `recovered/render.py:
  windows_mono_make_table_2x2`; one island factory drives both.  A/B oracle over
  both halves x 3 phases; byte-exact.  34 islands, suite 262 green.
- Mono MakeTable family now fully recovered: 4x4a/b (2 tiles/byte x4 rows) +
  2x2a/b (4 tiles/byte x2 rows), alongside the colour _Windows_MakeTable4x4/1x1.

## 2026-07-11 (cont.14) — recovered _WindowsMono_MakeTable4x4b (completes the 4x4 mono pair)
- The "b" half (44B9) is identical to "a" (442C) except `cx=0x20` pairs and the
  output scanline stride == the pair count.  Generalized the recovered
  `windows_mono_make_table_4x4(tiles, table, pairs)` and made ONE island factory
  drive both (pairs 0x40 / 0x20).  A/B oracle parametrized over both halves x 3
  table phases; byte-exact output + register preservation.  32 islands, 256 green.
  Remaining mono siblings: _WindowsMono_MakeTable2x2a/b (4542/45DB).

## 2026-07-11 (cont.13) — recovered the endian/word-order helper family (seg4)
- Lifted three clean leaf helpers toward exhausting the islands for the native
  port: `_FlipWord` (7356, byte-swap a word), `_FlipLong` (7360, byte-swap each
  half of a long -> AX=flip(hi) DX=flip(lo)), `_XFlipLong` (52D8, in-place swap
  of a dword's two words through a far pointer).  Pure logic in
  `recovered/byteops.py: flip_word/flip_long`; A/B oracles over many values incl.
  register residue (XFlipLong leaves es/bx/cx/ax) and the in-place memory swap.
  31 islands; suite 253 green.  (`_FlipWords`/`_XFlipLong`'s buffer-reversal
  sibling _FlipWords(52EE) is a loop — left for a later pass.)

## 2026-07-11 (cont.12) — NATIVE EXECUTION MODE proven: _Unpack runs with no VM
- Built the first real native (product) routine: `simant/native/unpack.py:
  native_unpack(state, out_seg, out_off, budget)` runs the recovered
  `lzss.decode_chunk` over a `NativeGameState` — args passed as a plain Python
  call, resumable state in the owned image, selectors resolved by the state's own
  `_xlat` — with NO cpu, NO stack, NO emulator.
- `NativeGameState` is now a drop-in `mem`: it mirrors the VM's selector table
  (`sel_base`, RPL-masked) and exposes `rb/rw/wb/ww`/`_xlat` exactly like dos_re
  `Memory`, so recovered adapters run over it unchanged.  `.from_machine` copies
  the image + selector table (the bootstrap seam).
- Proof (`tests/test_native.py`): capture a real _Unpack call from a pure-ASM VM,
  snapshot to a NativeGameState, let the ASM finish, then run native_unpack over
  the snapshot — the decompressed output AND the exit decoder state are
  byte-identical to the ASM (same fields the island's own oracle checks;
  match_rem excluded as documented don't-care scratch).  The VM is the oracle,
  the recovered source is the engine — the endgame direction working end-to-end
  for one routine.  Suite 239 green.

## 2026-07-11 (cont.11) — window islands migrated; every descriptor kind exercised
- Migrated `_win_IsWinOpen` and `_win_GetObjRect` onto the seam: the slot->HWND
  word table (0xBCA6) is `SimAntState.window_hwnd` (`_U16Array`), the slot->winrec
  far-ptr table (0xCE9A) is `window_records` (`StructArray` of a reusable `FarPtr`
  StructView), and the inclusive-rects flag (0xBD0A) is `obj_rect_inclusive`.  The
  record-internal object-rect array (in the record's own segment, not DGROUP)
  stays a raw far read.  Both byte-exact vs the ASM oracle; tests seed the tables
  through the same view.  Dead offset constants removed.
- The state-view seam now carries FIVE diverse islands (PRNG, DrawChar, Unpack,
  IsWinOpen, GetObjRect) and exercises every descriptor kind — `_U16/_S16/
  _U16Array/StructView/StructArray/FarPtr` — over both backends.  Suite 238 green.

## 2026-07-11 (cont.10) — state-view seam adopted by 3 diverse islands (PRNG/render/codec)
- Grew the seam past the proof: migrated the `_DrawChar` blit's cached DGROUP
  scratch (src/dst selectors + strides + word count, 0xB90E..0xB918) onto a
  `DrawCharGlobals` StructView, and the `_Unpack` LZSS decoder's resumable state
  (0xB7C0..0xB7D4 — win_seg/thresh/src/in_rem/r/flags/dx/cx/match_rem/resume)
  onto an `UnpackState` StructView (`_S16 in_rem` drops the manual sign-extend).
- Both islands read/write through the named view instead of raw offsets and stay
  byte-exact vs the ASM oracle; the DrawChar test seeds its strides through the
  same view.  So the seam now carries a PRNG, a renderer, and a codec — three
  different routine kinds — proving it is general, not PRNG-specific.  Dead
  DRAWCHAR_G_* offset constants removed.  Suite 238 green.

## 2026-07-11 (cont.9) — VM-less port groundwork: the state-view seam (pre2-style)
- Started the enhanced-layer endgame direction (VM becomes an oracle, recovered
  source runs on the game's own state).  Established the layering + the concrete
  seam, adapted from pre2_port for win16's selector model:
  * `simant/bridge/dgroup_view.py` — the layout bridge (pure, VM-free): named
    fields via `_U8/_S8/_U16/_S16/_U16Array/StructView/StructArray/DgroupView`;
    backends `SelectorBackend` (VM-faithful mem.rb/rw — matches selector xlat/RPL),
    `ByteBackend` (flat native image), `OverlayBackend` (write contract).  A
    `SimAntState` names the first verified globals: rng_seed 0xCBF2, music_device
    0xB91C, map_cols 0xCC80, map_rows 0xCD7A, songs_on 0x0AF6.
  * `simant/native/state.py` — `NativeGameState` (the owned .data image +
    dgroup_base; `.from_machine()` bootstrap).
  * Proven IN-USE: the `_SRand*`/Set/GetSRandSeed islands now read/write the seed
    through `SimAntState.rng_seed` (not raw 0xCBF2) and stay byte-exact vs the ASM.
  * `tests/test_state_view.py` (7) pins that the same view + recovered srand_step
    give identical results over a live VM and a NativeGameState.  Suite 238 green.
- Docs: `docs/vmless_port.md` (execution modes native/oracle/hybrid/verify + the
  layering rule + the seam).  Far endgame (native cold boot) still needs the core
  loop recovered; this seam is the groove future recovery runs in.

## 2026-07-11 (cont.8) — recovered _WindowsMono_MakeTable4x4a; render routines unexercised by newcold
- Lifted `_WindowsMono_MakeTable4x4a` (seg4:442C) — the zoomed monochrome tile
  packer, sibling of the recovered _Windows_MakeTable4x4/1x1.  Packs a fixed 0x40
  tile PAIRS into four 0x40-strided output scanlines: `out[r][j] =
  (table[t0][r] & 0xF0) | (table[t1][r] & 0x0F)`, where table[tile] is an 8-byte
  per-scanline pattern row selected by (mode & 7) at SS:0x26A0 (the "a" half uses
  scanlines 0..3).  `recovered/render.py: windows_mono_make_table_4x4a` +
  `MONO_MAKETABLE_PAIRS`; island reads the SS table; A/B oracle over 3 table
  phases (byte-exact + full register preservation).  28 islands; suite 231 green.
- **Reachability finding:** NONE of the seg4 render routines — the four Xfer* tile
  blits (incl. the two just recovered), the MakeTable/EditScroll/Plot families —
  are called during the newcold demo.  newcold is intro/menu/registration (its
  profile is busy-waits), and never enters the in-game MAP VIEW where tile
  rendering runs.  So render recoveries can only be validated by the synthetic A/B
  oracle (which is the standard proof).  To profile/validate render lifts against
  real gameplay, a future demo must reach the map view (ants simulating, scrolling).

## 2026-07-11 (cont.7) — recovered _XferLifeTileMono (the masked 1bpp overlay)
- Lifted `_XferLifeTileMono` (seg4:49B7) — the transparent sibling of _XferTileMono
  and mono counterpart of _XferLifeTileColor.  Same bottom-up mono geometry, but a
  SECOND source plane (the mask) at a fixed `+mask_delta = 0x3000 - (src_tile &
  0xFF80)*32` from the data byte selects per bit: mask 1 keeps the dest, 0 draws
  the source (`new = (dst & mask) | (data & ~mask)`, via the ASM's
  `dst ^= (data ^ dst) & ~mask`).  `recovered/render.py: xfer_life_tile_mono`;
  island reads+writes the dest; A/B oracle with a varied mask plane (4 cases,
  byte-exact + full register preservation).  27 islands; suite 228 green.

## 2026-07-11 (cont.6) — recovered _XferTileMono (the 1bpp sibling of _XferTileColor)
- Back to source recovery.  Lifted `_XferTileMono` (seg4:486C, _TEXT) — the
  monochrome sibling of the already-recovered `_XferTileColor`.  Same 22-byte far
  ABI + huge-pointer DIB walk, but 1bpp (eight pixels/byte): stride packs bits not
  nibbles (no `<<2`), byte offsets are pixel>>3, tiles are 32 bytes, and the band
  is walked BOTTOM-UP (`di -= stride`) from `((y_extent-top)*height-1)` so source
  row j lands in band row height-1-j.  A pure copy; pusha/popa preserves all regs.
- `recovered/render.py`: `xfer_tile_mono` + `_tile_blit_geometry_mono`; island in
  `hooks.py` (26 now); A/B oracle in test_hooks.py (4 cases, byte-exact dest +
  full register preservation).  Suite 224 green.
- Profiling note: newcold is mostly intro/menu, so its top buckets are busy-waits
  (_TickCount/_WaitedEnough/_StillDown) + the deliberate _Unpack resume-passthrough,
  not new pure-compute loops — picked the clean mono-sibling recovery instead.

## 2026-07-11 (cont.5) — MIDI music WORKS end-to-end (real .mid soundtrack plays)
- Phases 1-3 landed.  The game now loads mmsystem, resolves the MCI procs, opens
  `C:\sound\gamethme.mid` (→ assets/ANTWIN/SOUND/GAMETHME.MID) and plays it through
  the host synth.  newcold runs clean to the end with MIDI on; both suites green
  (simant_port 220, win16_re 101).
- What made it work (the ONLY blocker was the missing DLL file):
  * `api.provided_dlls` (MMSYSTEM.DLL) — single source of truth: a provided DLL
    LoadLibrary's AND reports as an existing file (system.file_open), so the game's
    `_access(mmsystem.dll)` existence probe passes and its loader runs.
  * `midiOutGetNumDevs`→1; `waveOutGetNumDevs`→0 + waveOut* safe stubs (correct arg
    specs so callee-clean far-return pops right) so the game uses MIDI music and
    skips the digitized-WAV effect path (which we don't provide) without crashing.
  * `mciSendCommand` engine: OPEN(element=.mid) assigns a device id, resolves the
    DOS path to the real asset (new `Win16System.resolve_host_path`, follows subdirs
    case-insensitively), SET/STATUS/PLAY/STOP/CLOSE; deterministic `mci_log` +
    optional `music_backend` (presentation only, so replay stays exact).
  * `win16.audio.MidiBackend` (pygame SDL_mixer music stream) plays the real .mid;
    wired in play.py alongside the SFX SquareWaveBackend.
  * new KERNEL.8/9 LocalLock/LocalUnlock (fixed-block identity) — a small frontier
    the deeper MIDI path reached.
- Fixed a Phase-1 regression along the way: GetProcAddress keyed libs by full
  filename but procs register under the module name — strip ".DLL" before minting.
- CORRECTION recorded: my earlier "MIDI is dead code / unrecoverable" (cont.2/3) was
  a static-analysis error; owner was right.  songid 10001 = GAMETHME.MID.

## 2026-07-11 (cont.4) — CORRECTION: MIDI is NOT dead — it works once mmsystem.dll "exists"
- Owner was right (pointed at how otvdm plays the .mid).  My "dead code" /
  "unrecoverable" conclusions (cont.2/cont.3) were WRONG — a static-analysis error
  (`_CheckMMWave` was a red herring; the real mmsystem loader is a different,
  reachable function that IS called).  The ONLY blocker was the missing
  `mmsystem.dll` file: `_access`(INT21/43h) fails → the loader skips.
- **Proof (empirical):** provide a virtual `MMSYSTEM.DLL` so the existence check
  passes, and over the newcold run the game DOES: LoadLibrary(MMSYSTEM.DLL)→0x100,
  GetProcAddress(midiOutGetNumDevs)→call→1, GetProcAddress(mciSendCommand), then the
  MCI play sequence for songid 10001:
    mci OPEN flags=0x200(MCI_OPEN_ELEMENT) element='C:\sound\gamethme.mid'  (+12 in the parms)
    mci STATUS flags=0x4003 ; mci SET 0x20000 ; mci SET 0x400 ; mci PLAY 0x4
  So songid 10001 → GAMETHME.MID.  The handle DID get set (0x100).  MIDI works.
- **Phase 2 is achievable & clear:** (1) report provided DLLs (MMSYSTEM) as existing
  in the file layer so the loader runs; (2) implement `midiOutGetNumDevs`(≥1) +
  `mciSendCommand` (OPEN element=.mid → resolve C:\sound\NAME.mid to assets/ANTWIN/
  SOUND/NAME.MID; SET/STATUS/PLAY/STOP/CLOSE), deterministic event-log; (3) pygame
  host backend plays the real .mid.  In progress.

## 2026-07-11 (cont.3) — the songid→.mid mapping is NOT recoverable from the running game
- Chased the "intercept _myBeginSong → play .mid" plan and hit an information wall:
  * The .mid base names (ANTTHME1/GAMETHME/ATTACK/...) appear NOWHERE as strings —
    not in SIMANTW.EXE (raw file), not in any loaded segment, not in the .DAT/.NDX
    resources.  They exist only as on-disk FILENAMES.
  * `SOUND.NDX`/`SOUND.DAT` are a NUMBERED archive (181 entries, id→offset, no names);
    SOUND.DAT holds digitized PCM (sound FX), not named song records.
  * So the game plays music as NUMBERED songs (SOUND.DRV note sequences); the
    songid→.mid-name table lived only in the dead MIDI code and its name strings
    aren't in the binary at all (built at runtime / from a resource the dead path
    would have read).  There is no clean, verifiable songid→.mid mapping to recover.
- **Net:** playing the real .mid soundtrack faithfully is blocked — not by
  implementation effort but by missing information in this build.  Only a
  best-effort EMPIRICAL mapping is possible (hook _myBeginSong, log songids at
  known game moments, hand-match to the descriptively-named .mid files — imprecise,
  unverifiable).  The faithful alternative is to render the ACTUAL game music (the
  SOUND.DRV note sequences we already emulate) with a nicer synth than a bare
  square wave.  Back to owner for the call.

## 2026-07-11 (cont.2) — DECISIVE: SimAnt's MMSYSTEM/MIDI path is DEAD CODE in this build
- The ONLY writer of the mmsystem handle `es:[0x8d08]` is `_CheckMMWave+0x6C`
  (seg2:77D3).  `_CheckMMWave`(0x7766) has **no direct caller and no pointer
  reference anywhere** (call-operand + data-word scans across all segments = 0
  hits) — it is unreferenced dead code.  Empirical confirmation: over a full
  newcold gameplay run (51.7M instr) `_CheckMMWave` runs **0 times** and the
  handle is **never** nonzero.  So `_MusicInit`/`_myBeginSong`/`_myBeginSound`
  always see handle==0 and bail out of the MMSYSTEM branch → music ALWAYS plays
  via SOUND.DRV (the PC-speaker note sequencer we already emulate).
- **Conclusion:** implementing MMSYSTEM/MCI is MOOT — SIMANTW.EXE never attempts
  it.  Phase-1 dynamic-loading (LoadLibrary/GetProcAddress/thunk minting) is still
  a valid general win16 capability (kept), but it won't make SimAnt play MIDI.
- **The soundtrack is still reachable another way:** the game DOES call
  `_myBeginSong(songid)` naturally (newcold hits it at 17M).  Debug strings show
  it resolves `songid -> "sound\NAME.mid"` ("myBeginSong(shift=%d)(id=%d)(file=%s)",
  "%ssound\%s.mid").  So the real soundtrack can be played by intercepting
  `_myBeginSong`/`_StopSong` (recover songid->name, log deterministically, play
  the real .mid via a pygame backend) — a music-presentation backend, NOT MMSYSTEM
  emulation and NOT byte-exact recovery.  Awaiting owner decision on direction.

## 2026-07-11 (cont.) — MIDI detection chain FULLY traced (the trigger + the loader)
- **The device is config-driven:** `assets/ANTWIN/SIMANT.CFG` is text — "Display Mode: ?\n
  Sound Mode: 6\n".  `_ReadConfig`(seg2:0x8F4, called by `_IBMInitStuff`) does
  `musicDevice(DGROUP:0xB91C) = 'Sound Mode' digit - '0'` → 6 currently.  So the sound
  device is just that CFG digit.  (Which digit == MIDI Mapper is still TBD — needs the
  mode→driver map, or empirically sweeping the digit with mmsystem provided.)
- **The mmsystem loader is `_CheckMMWave`(seg2:0x7766):** builds "<GetWindowsDirectory>\
  system\mmsystem.dll", checks the FILE EXISTS (call seg4:0x73A ~= _access), and only
  then `LoadLibrary`(KERNEL.95, thunk 0x22c) it and stores the HANDLE at mmsystem-block
  `es:[0x8d08]` (es=[DGROUP:0xBF78]=seg 0x5294).  Then GetProcAddress(handle, name).
- **Why we get handle=0 → SOUND fallback:** our file layer has no `mmsystem.dll`, so the
  existence check fails and `_CheckMMWave` never LoadLibrary's it.  Phase 1's LoadLibrary
  WOULD now succeed if reached.
- mmsystem state block (seg 0x5294): +0x8d06 sound-enabled, +0x8d08 mmsystem handle,
  +0x8d0a avail flags, +0x8d0c, +0x8d0e mciProc, +0x8d28 second DLL handle (SB).
  `_myBeginSound`(0x98B0)/`_myBeginSong`(0x858E) bail if +0x8d06==0, else GetProcAddress
  via the stored handle.
- **Concrete Phase 2 start next session:** (1) provide `mmsystem.dll` to the file layer
  so `_CheckMMWave`'s existence check passes (a stub file is enough — LoadLibrary is
  already faked); (2) find `_CheckMMWave`'s caller / the sound-init trigger; (3) sweep
  the CFG Sound-Mode digit to find the MIDI value; (4) implement `mciSendCommand` +
  `midiOutGetNumDevs`>0; (5) host backend.  All chain pieces are now mapped.

## 2026-07-11 — MIDI music Phase 1 DONE (dynamic loading); Phase 2 fully scoped
- **Phase 1 shipped** (win16_re `ff912c6`, simant_port `b4f9792`): KERNEL
  LoadLibrary/FreeLibrary/GetProcAddress(ord 50!)/GetModuleHandle + ApiRegistry
  runtime thunk minting (GetProcAddress hands back a callable INT3 thunk that
  dispatches to a by-name handler).  midiOutGetNumDevs registered but reports 0
  (non-breaking — SOUND.DRV fallback intact).  2 integration tests, suites green.
- **The soundtrack is real MIDI**: 29 `.MID` files in assets/ANTWIN/SOUND/
  (ANTTHME/GAMETHME/ATTACK/VICTORY/...).  Host CAN play them (pygame-ce SDL_mixer
  loads .mid; OS "Microsoft GS Wavetable Synth" present).
- **SimAnt's MMSYSTEM engine is EXTENSIVE** — it GetProcAddress-resolves 28 procs
  across three DLLs (extracted statically):
  * MMSYSTEM MIDI: `mciSendCommand`, `midiOutGetNumDevs`  ← the music path we CAN do
  * MMSYSTEM WAV (sound FX, _myBeginSound/_MciOutWave): PlaySound, sndPlaySound,
    waveOutOpen/Close/Write/Reset/GetNumDevs/GetPosition/Prepare/UnprepareHeader
  * SoundBlaster driver (DSOUND.DLL/SNDBLST.DLL — proprietary, we CANNOT provide):
    GetDSoundVersion, musOpenDevice/CloseDevice/PlayMemMidi/StopMusic/TransposeNote,
    vocOpenDevice/CloseDevice/PlayMemUnFormat/StopVoice, sbc*.
  * COMMDLG: GetOpenFileName/GetSaveFileName (the editor's file dialogs).
- **Key runtime facts (probed):** the mmsystem state block is at seg[DGROUP:0xBF78]
  (=seg 0x5294): handle[+0x8d08], midiAvail[+0x8d0a], [+0x8d0c], mciProc[+0x8d0e].
  `_musicDevice`(DGROUP:0xB91C) has a DEFAULT of 6 baked into the EXE data — it is
  NOT the result of a completed init.  `_MusicInit`(seg2:8294) alone doesn't load
  mmsystem (its handle==0 path just CloseSound()s).  The real music-device init
  (LoadLibrary mmsystem + set handle + select MIDI Mapper) runs on a trigger we
  haven't hit (music-toggle seg1:07B2 / game-start seg3:A232); newcold reaches
  `_myBeginSong` but never inits MIDI, so it plays via SOUND.DRV.
- **Recommended Phase 2 path:** the MMSYSTEM **MCI MIDI** route only — implement
  `midiOutGetNumDevs`(report a device) + `mciSendCommand` (MCI_OPEN a sound\X.mid /
  SET time / PLAY / STOP / STATUS / CLOSE), deterministic event-log + pygame
  presentation backend.  Leave DSOUND/SNDBLST unprovided (LoadLibrary fails → the
  game skips SoundBlaster and takes the MIDI Mapper path).  OPEN QUESTION to crack
  first next session: find the music-device-init function + its trigger, and what
  `_musicDevice` value selects the MIDI Mapper, so a headless harness can drive
  _MusicInit→_myBeginSong→mciSendCommand and reveal the exact MCI command sequence
  to implement.  (Probes: scratchpad mci2.py / procnames.py / findll.py.)

## 2026-07-10 — FIXED _DrawChar island rendering bug (garbled "registered to" screen)
- Repro: demo_195527 — the "This copy of SimAnt is registered to: eXo" screen
  rendered GARBLED with islands on.  Bisected via island-A/B over the real demo
  (replay with hooks vs pure ASM, per-window surface sha): 4 UI panels diverged;
  the glyph-rendering island `_DrawChar` was the culprit.
- Root cause: the partial-mask lookup is `xlatb` with a **CS: override (2E D7)**,
  so the top-n-bits mask table is read from the CODE segment (seg7:B02A), NOT the
  glyph source segment.  The island read it from `src_seg`; the A/B test masked
  the bug by planting the table in DGROUP where `src_seg` happened to point.
  In-game `src_seg != code seg` → garbage mask → garbled text.
- Fix (hooks.py): read the mask from `seg_bases[DRAWCHAR_SEG_INDEX]`.  Test
  (test_hooks.py): drop the DGROUP planting so the source seg's 0xB02A is
  unrelated data — the old bug now diverges.  Proof: demo_195527 replay with
  islands is now byte-identical to pure ASM across ALL window surfaces (was 4
  diverging).  Also made hooks.py's module-level `dos_re.cpu` import robust
  (bootstrap win16 first) so the suite collects.  214 passed.
- Method note: the strict HookVerifier also flags a benign `_win_IsWinOpen` AF-flag
  divergence (my A/B masked AF as "undefined for logic ops") — cosmetic, C ignores
  flags across calls; rendering is byte-exact regardless.  Follow-up: make islands
  flag-exact so they pass the verifier clean.

## 2026-07-10 — demo v4 PROVEN end-to-end: cold-start replay is clean + deterministic
- Owner re-recorded a fresh cold-start demo (`newcold`, v4: 2474 input arrivals +
  ~1758 clock samples) clicking through the SAME registration "click to continue"
  screen that deadlocked colddemo under the old model.
- Replay result: **clean run to the end** — 2474/2474 events consumed, 51,728,941
  instructions, ended via DemoEnded (deterministic exhaustion), reaching the full
  in-game window set (RibbonWindow + the GenericWindow panels = PAST the splash).
  No CallbackOverrun, no modal-loop deadlock — the v4 model fixes it.
- **Deterministic + bit-exact:** replayed twice, identical instruction count AND
  identical digest (a185d43d…) both times.  The instruction-keyed injection + clock
  reproduction reproduces the recorded run exactly.
- All old (v1-v3) demos ditched per owner (colddemo/ghost*/artifacts demos removed).

## 2026-07-10 — BUILT demo v4: instruction-keyed input injection + reproduced GetTickCount
- Redesigned the win16 demo record/replay model (owner OK'd breaking old demos).
  v4 records the raw INPUT TIMELINE instead of per-API consumption:
  * "i" input arrival (host event) stamped with the instruction_count it landed at;
  * "c" periodic (instr -> tick) clock sample (rate-limited);
  * "d" dialog event; "quit".
  On replay a `DemoDriver` injects each "i" into msg_queue at its instruction count
  and reproduces GetTickCount by interpolating the (instr, tick) samples; the game's
  OWN pump (GetMessage/PeekMessage/GetAsyncKeyState) then fetches exactly as live —
  no fetch-API matching, no stream-position coupling.  Fixes the class of deadlock
  the colddemo hit (a busy-poll couldn't reach input recorded behind a message).
- Files: win16/demo.py (rewrite: DemoRecorder v4 + DemoDriver), win16/api/system.py
  (tick_count/get_message/peek_message consult demo_driver; dropped m/p record taps
  + the player path), win16/api/dialogs.py (driver.next_dialog_event; recorder stamps
  instr), win16/interactive.py (record "i"/"c"/quit with instr), scripts/replay.py
  (install DemoDriver).  Pre-v4 demos are rejected with a clear "re-record" error.
- Verified: 9 new v4 unit tests (win16/tests/test_demo.py — injection, clock interp,
  pump_get force-deliver, pump_peek busy-miss, DemoEnded, dialogs, version reject);
  win16_re suite 98 green; simant_port suite 214 green; and a DETERMINISTIC real-
  machine integration (hand-built demo) confirms system.py tick_count == driver
  timeline EXACTLY at every probe and both events inject/consume.  Determinism note:
  replay must use the SAME hooks config the demo was recorded with (instr counts are
  config-specific).
- **Needs owner re-record to close:** record a fresh cold-start repro with the new
  format (pypy scripts/play.py --record <name>, F11 or --record) and replay it — that
  is the end-to-end proof the colddemo modal-loop deadlock is gone (can't record
  interactively headless).

## 2026-07-10 — colddemo root cause NAILED: demo replay model is fragile for poll-loops (needs instruction-count keying)
- Traced the exact stuck iteration (260 steps from the (0,0,0) peek-miss at pos 5391):
  the WndProc spins in `_win_IsWinInFront(0x013A)` — window handle 314 = the modal
  registration "click to continue" window.  The loop is pure WINDOW-STATE polling
  (GetTopWindow → GetWindow(GW_HWNDNEXT/GW_OWNER) → IsWindowVisible/GetProp); it
  exits only when window 314 is DISMISSED.  It is NOT clock-driven (re-anchoring the
  GetTickCount floor did nothing) — confirmed by trace.
- The dismiss input (the click, async 'a' records at pos 5392+) is recorded BEHIND a
  WM_TIMER that pos 5391 recorded under GetMessage ('m').  The poll-loop fetches via
  PeekMessage(0,0,0) + GetAsyncKeyState and never calls GetMessage, so it can't reach
  the 'a' records → deadlock.  Root cause: **the demo replay model matches records to
  the specific fetch API + strict stream position.**  A message recorded under
  GetMessage is invisible to a PeekMessage that wants it; async recorded behind a
  message can't reach a poll-loop.
- Prototypes tried (all in scratchpad, none landed): GetTickCount clamp-to-next-ts
  (froze the clock), floor re-anchor per record (no effect on this decision),
  unify m/p fetch (diverged EARLIER at 4515), deliver-async-on-peek-miss (too eager,
  broke at 4179).  Lesson: heuristics each break elsewhere — the strict model is
  load-bearing for the rest of the timeline.  The fix must be a principled MODEL
  change, not a patch.
- **Design conclusion:** key the demo to a DETERMINISTIC anchor — the instruction
  count — like dos_re does, instead of fetch-API+position matching.  Record each input
  event (queue message / async note) with the instruction_count at which it occurred;
  on replay inject it at that instruction, letting the game's real PeekMessage/
  GetMessage/GetAsyncKeyState pull it naturally.  To keep record==replay control flow,
  the demo must also carry enough of the GetTickCount timeline (tick + instr per event)
  to reproduce the clock the game saw.  Owner OK'd breaking the demo format (few demos,
  re-recordable) — so v4 can redesign freely.  Needs a fresh re-recorded demo to verify
  (can't record interactively headless).

## 2026-07-10 — colddemo: cold-start replay gap = GetTickCount drift on a wall-clock modal loop (NOT a VM bug)
- `colddemo` (5845 records, cold `--no-hooks --record`, snapshot:null) — owner
  clicked around from boot; replay stalls at record 5391 (of 5845) in a
  CallbackOverrun: WndProc 0100:2440 spins forever (>400M steps).
- Diagnosis (definitive): the spin is a wall-clock-paced MODAL loop on the
  cold-start/registration "click to continue" screen.  It polls PeekMessage(0,0,0)
  (522,746 misses), GetAsyncKeyState(SPACE/ESC), GetTopWindow, GetTickCount.  The
  next demo record (5391) is a `GetMessage` for WM_TIMER, but the loop never falls
  through to GetMessage.  No timer is armed (KillTimer'd earlier) so none is
  synthesized.  **tick_count=102813 vs recorded clock_ms=28875** — the headless
  GetTickCount instruction-floor (INSTR_PER_MS=1000) has run 3.5x past the recorded
  wall clock, so the modal loop's timing decisions diverge from record time.
- This is NOT a CPU/VM accuracy issue (interpreter byte-exact); it's replay-clock
  fidelity for wall-clock-timed modal code — the classic determinism trap.
  Prototype "clamp the replay clock to the next record's timestamp" did NOT cleanly
  fix it (froze the clock without making the loop call GetMessage) → the divergence
  is subtler than pure overshoot; needs owner steer before touching win16's clock
  model.  Pragmatic workflow answer: the demo format already supports snapshot
  ANCHORING to skip such non-deterministic splash regions — anchored demos are the
  deterministic regression baseline; cold demos are a gap-finder, not a baseline.
- colddemo kept as a useful gap-finding artifact.

> Scope: **SimAnt is the sole target** (owner, 2026-07-09).  Other games are
> leaving the repo; wherever a doc names Paulie Python "the RE target," it's
> stale — SimAnt is the focus.  Primary goal: clean, readable, byte-exact source
> reconstruction (recovered routines in `simant/recovered/`, hot-loop islands in
> `simant/hooks.py`, each gated byte-exact by the A/B oracle).

## 2026-07-10 — recovered _DoCalcTile (the deferred 184-insn boss) — the emitter really paid off
- Recovered `_DoCalcTile` (seg4:4A6B, the demo's #3 hot routine) -> `recovered/render.py:
  do_calc_tile`.  Resolves a map cell (tile_x, tile_y) to its graphic index (CE96 byte)
  and attribute (CE7A word) for the current VIEW MODE (DGROUP:0xCC76): 0/1 the main map,
  2 and 3 two alternate map pairs, >=4 draws nothing.  Modes 0/1 first consult 5 half-res
  overlay layers (far-ptr table 0xACAE, selector 0xAC58) — a texel >0x10 overrides the
  graphic.  The attribute is shared logic (0/0xFF/0xFE/other) assembled from season/phase
  globals (CF54/CC84/CF50/CE92).  184 insts + 4 modes collapsed to a 7-field table
  (`_TILE_MODES`) + a shared `_tile_attr` — the 3 paths differ only in map offsets + 3 bases.
- **This is where the automatic lifter earned its keep, exactly as predicted.**  Working
  from the emitted artifact's labelled 48-block CFG made the mode dispatch + the repeated
  attr ladder legible; hand-tracing 184 instructions of jumps would have been error-prone.
  Still probe-validated every branch first (mode dispatch, layer hit/miss, all attr cases,
  the CC84>=8 split) — 10 ground-truth cases before writing a line.
- Island is clean: pusha/popa preserves every register, output is just the 2 globals.
- Proven three ways: A/B oracle (10 cases across all 4 modes + every attr branch; CE96/CE7A
  + all 10 regs), VM-free unit exercise, liftverify ORACLE_PASSING.  25 islands, 214 green.
- The whole hot list is now recovered readable source: _GenNestMap, _DoCalcTile,
  _XferLifeTileColor, _XferTileColor, _DrawChar, _win_IsWinOpen, _win_GetObjRect.
  Remaining hot: _ResetEditScrollRange (#2, 6 side-effectful scroll-API calls), _CenterEdit.

## 2026-07-10 — recovered _DrawChar (the sub-byte-shifted glyph blit — the intricate one)
- Recovered `_DrawChar` (seg7:B033) -> `recovered/render.py: draw_char` + `shift_glyph_word`.
  A 1bpp glyph OR-composited into a bitmap with SUB-BYTE bit alignment: per row it walks
  `width//8` OVERLAPPING words (position advances one byte/step, so a shifted source
  word smears across the byte boundary) plus a partial edge column masked to `width & 7`
  top bits.  Sub-byte shift = byteswap, shl(x&7), keep-high-byte, shr(y&7), byteswap back.
- **Probe-first paid off (again).**  Two subtleties a rushed read would miss, both nailed
  by ground-truth probes: (1) the partial-mask table is at src-seg:0xB02A =
  `0080c0e0f0f8fcfeff` (top-n-bits), read via xlatb with ds=source segment (font data is
  in seg7); (2) it does NOT pusha, so ax/bx/cx/dx are clobbered with data-dependent exit
  values — reproduced exactly: bx=width, cx=(y&7)<<8|(x&7), and for a partial column
  dx=mask<<8, ax=the last row's partial shifted word (else `mov ax,bx;and ax,7` leaves 0).
- Writes three scratch globals (0xB90E src seg, 0xB910 dst seg, 0xB918 width>>3); reads
  per-row strides from 0xB912/0xB914; hardcodes ds=DGROUP (0x6902 = seg_bases[10]).
- Proven three ways: A/B oracle (5 cases: byte-aligned, multi-row, sub-byte x, odd width
  + x&y sub-bits, single-byte-shifted; composited dest + 3 globals + all 10 regs incl the
  clobbered ax/bx/cx/dx), VM-free unit exercise, liftverify ORACLE_PASSING.  24 islands, 204 green.
- Deferred boss: _DoCalcTile (184, 4 modes).  Remaining call-coupled hot: _ResetEditScrollRange
  (#2 hot, 6 side-effectful scroll-API calls), _CenterEdit (65, 1 near call).

## 2026-07-10 — recovered _XferLifeTileColor (the transparent blit sibling)
- Recovered `_XferLifeTileColor` (seg4:48FA) -> `recovered/render.py: xfer_life_tile_color`.
  BYTE-IDENTICAL setup to _XferTileColor (same stride/start/huge-ptr walk — the compiler
  didn't dedup the prologue), so I factored the shared geometry into `_tile_blit_geometry`
  and both blits call it.  The difference is the inner op: a per-pixel transparent BLEND
  instead of a copy.
- Transparency semantics (recovered from the nibble-mask ASM): each source byte is two
  4bpp pixels; sentinel 0xDD leaves the dest byte; a pixel whose index is 0xD is
  transparent (kept from the dest).  `dst = (dst & keep) | draw`, keep marking the
  transparent nibbles.  So the island READS the dest too (blend), unlike the plain copy.
- Proven three ways: A/B oracle (3 geometries x a source spread of opaque/low-transp/
  high-transp/0xDD over non-trivial existing dest bytes + all 10 regs), VM-free unit
  exercise, liftverify ORACLE_PASSING.  23 islands, 199 green.
- Session recovery tally: _win_IsWinOpen (API-coupled), _win_GetObjRect (near-call),
  _GenNestMap (hot pure), _XferTileColor (huge-ptr copy), _XferLifeTileColor (huge-ptr
  blend) — five shapes.  _DoCalcTile (184, 4 modes) still the deferred boss (lift ORACLE_PASSING).

## 2026-07-10 — recovered _XferTileColor (the huge-pointer tile blit)
- Recovered `_XferTileColor` (seg4:47DD) -> `recovered/render.py: xfer_tile_color`: a 4bpp
  tile-colour blit that copies `height` rows of `tile_w//2` bytes into a DIB whose
  scanline is padded to a 32-bit boundary (`stride = ceil(map_w*tile_w*4/32)` bytes),
  starting at the `(y_extent-top-1)`-th band, source tile at 128 bytes each.
- **The signature island-method case: a huge pointer.**  The destination is >64K; the
  ASM walks it with `es += 8` per 64K (`__AHINCR`).  Our selector heap maps consecutive
  selectors to contiguous memory, so the island resolves a linear dest offset back to
  (selector, off) by the same +8/64K rule and writes the identical bytes the ASM's
  `rep movsb` loop does — es is preserved (popa) so its intermediate value is irrelevant.
  pusha/popa restores every register: the only observable state is the destination bytes.
- **Emitter caveat, again:** the emit hid the `mul`/`les`/`lds` operands behind
  interp_one (85% native), so I disassembled for the geometry, then a ground-truth probe
  validated the whole blit (stride, start band, row advance) before writing source.  All
  16-bit products are masked (& 0xFFFF) to match the registers.
- Proven three ways: A/B oracle (4 geometries incl. dst_x offset, non-zero tile, non-zero
  start band; dest bytes + all 10 regs), VM-free unit exercise, liftverify ORACLE_PASSING.
  22 islands, 196 green.
- Deferred (owner): _DoCalcTile (184 insts, 4 modes) needs a dedicated careful pass;
  its lift is already ORACLE_PASSING so a byte-exact executable form exists.  Other hot
  pure-ish targets: _XferLifeTileColor (88, likely a blit sibling), _DrawChar (89).

## 2026-07-10 — recovered _GenNestMap (the demo's #1 hot routine) — and: the lifter pays off
- Recovered the hottest routine in the demo (~26% of PC samples): `_GenNestMap` (seg4:4754)
  -> `recovered/render.py: gen_nest_map_cells`.  Builds the 64x64 nest colour map: per
  cell, classify the terrain byte (border 0xFE/FF -> A; high bit -> B; low nonzero -> C;
  empty 0x00 -> leave-it (mode!=0) or `table[alt>>2]`).  65 insts, 15 blocks, pure.
- **Was the automatic lifter worth it?  For this one, clearly yes.**  I reversed the
  routine FROM the emitted artifact (`simant/lifted/lifted_4_4754.py`), not raw disasm:
  the emitter had already decoded all 65 instructions, split the control flow into
  labelled basic blocks with explicit transitions, and named every memory op — so the
  algorithm read straight off the page.  The honest counter-evidence: I still MUST
  validate against the ASM.  A probe caught that `c1e802` is `shr ax,2` (÷4), not the
  `shl` (×4) my first reading assumed — the empty-cell lookup is `table[alt>>2]`.
  Verdict: for the 22-41 insn window helpers the emitter added little over disasm; at
  65+ insns (and _DoCalcTile is 184) the block-structured lift genuinely de-risks the
  hand-translation.  liftverify (the verify half) has been paying off since day one.
- Island is unusually clean: `pusha`/`popa` restores EVERY register, so the only
  observable state is the 4096-byte output + four DGROUP globals (0x1B78 table base,
  0x1B7A/7B/7C palette).  A/B test compares the full output map + globals + all 10 regs
  in both modes; also VM-free unit-exercised; liftverify ORACLE_PASSING.  21 islands, 192 green.
- Next hot targets (all liftable, pure): _DoCalcTile (184 insts, the big one),
  _XferTileColor (66), _XferLifeTileColor (88), _ResetEditScrollRange (74, 6 far calls).

## 2026-07-10 — recovered _win_GetObjRect (the near-call analogue)
- Second call-coupled routine through the full loop (seg7:C2D2 -> recovered/window.py:
  `win_get_obj_rect`).  It copies an object's stored RECT into *lpRect via a two-level
  far-pointer walk (window-table at DGROUP:0xCE9A -> window record; record+0x2C obj-rect
  array -> RECT) and bumps right/bottom when the DGROUP:0xBD0A inclusive-rects flag is
  set.  Recovered source stays VM-free (the far-ptr walk is injected as `resolve_rect`).
- **Key find:** the bracketing `push cs; call` pairs go to `_win_LockWin` (seg7:E3A8) and
  `_win_UnlockWin` (seg7:E3A4) — both bare `retf` NO-OP stubs (the fixed Win16 memory
  model needs no locking).  So the "near calls" carry no behaviour; the island need not
  re-issue them, and the recovered source documents them as no-op brackets.
- Island reproduces the compiled residue the register oracle checks: DX:AX = the source
  RECT far pointer (the es:[bx+si+0x2c/2e] loads), ES = lpRect segment, BX = &lpRect
  (adjust) or (obj&0xFF)*4 (no adjust); CX/SI/DI/BP/DS preserved.  Flags at retf come
  from the final `add sp,2` arg-cleanup (a calling-convention artifact) — compared by
  the machine lift's full-state check, not the register island (house style, like
  MakeTable).
- Proven two ways: A/B oracle (`test_getobjrect_island_matches_asm`, 4 cases incl. the
  inclusive bump and a 0xFFFF->0 wrap) + liftverify ORACLE_PASSING.  20 islands, 190
  green.  Both call-coupled frontier routines (API-call + near-call analogues) are now
  recovered readable source.

## 2026-07-10 — first RECOVERED call-coupled routine: _win_IsWinOpen (readable source)
- The lift pipeline exists to FEED recovery, not replace it.  Took _win_IsWinOpen
  (seg7:C256) the whole way: lift -> verify -> **refactor into readable
  `simant/recovered/window.py`** -> prove the same oracle green.  The recovered source
  reads like the original C:
      HWND hwnd = g_window_hwnd[objHandle >> 8];   // word table at DGROUP:0xBCA6
      return hwnd && IsWindowVisible(hwnd);
  VM-free (imports nothing; window-table read + IsWindowVisible injected as callables),
  so it's the actual reconstructed source, not a lifted liability.
- Wired as a hand island (`hooks.py: _make_iswinopen_island`, 19 islands now) that
  re-issues the far USER.IsWindowVisible call via `call_far` and reproduces the
  compiled residue the register oracle checks: BX = &g_window_hwnd[slot] (the shl+add
  pointer, never restored), flags = `set_logic_flags(result)` (the final or/xor; USER
  returns canonical 0/1 so it's exact).  This is the FIRST island that calls a Windows
  API from inside itself — the call-coupled pattern, now demonstrated end to end.
- Proven TWO independent ways: A/B oracle (`test_iswinopen_island_matches_asm`, 4 cases
  incl. visible/hidden/empty-slot, comparing 10 regs + CF|PF|ZF|SF|OF flags) AND the
  machine lift (liftverify ORACLE_PASSING).  Full suite 186 green.
- Next recovery candidate: _win_GetObjRect (seg7:C2D2, near-calls) — the near-call
  analogue.  Then widen down the SIMTWO/GR module hot list.

## 2026-07-10 — scripts/liftverify.py: the lift→prove loop, in situ over a demo
- New win16 lift-verify driver (`scripts/liftverify.py`): snapshot + demo + entries
  (`--entry 7:C256` or `--symbol _win_IsWinOpen`) → emit a literal hook each, install,
  replay the demo, and on every call re-interpret the ORIGINAL ASM to the hook's
  continuation, diffing full CPU state + memory.  Reports ORACLE_PASSING/DIVERGED/
  NOT_REACHED + block coverage; regenerated lifts are gitignored scratch (`simant/lifted/`).
- **Proven:** `_win_IsWinOpen` (far API call, 4/4 blocks) and `_win_GetObjRect`
  (near-calls, 2/3 — this demo doesn't hit the 3rd) both ORACLE_PASSING, 5 calls each
  byte-exact, in 29s.  The exact call-coupled frontier that blocked recovery last round.
- **This needed THREE upstream dos_re fixes + one win16_re layer**, all found by SimAnt
  being the first non-DOS consumer of the verifier (each pushed with a failing-on-old
  regression test):
  * dos_re `3c403a6`: `HookVerifierConfig.clone_runtime` — the verifier's cloner was
    DOS-only; a win16 machine's OS is a Python object graph that must be re-bound to
    the clone.
  * dos_re `baff7f5`: `asm_keeps_passthrough_hooks` — strict mode cleared ALL hooks
    from the ASM oracle, but the win16 API hooks ARE the environment (INT3 tripwires,
    no ASM behind them) → oracle died on the tripwire.
  * dos_re `58a1a51`: the divergence REPORTER assumed `rt.dos`, so a genuine win16
    divergence surfaced as AttributeError instead of the actual diff.
  * win16_re `c6f995c`: `win16/verify.py` — the Win16 runtime shim + cloner (pickle
    the OS graph, copy memory, re-bind API services to the clone; game-code hooks port
    over, thunk hooks stay the clone's own), wiring all three seams together.
- **Perf, measured & fixed:** first runs took ~28s and it looked like the verifier
  was expensive.  It is not — the 2 functions x 5 samples = 10 ASM-oracle re-runs cost
  ~0.3s total.  The 28s was the DRIVER over-running: after the last sample the outer
  loop (200k-instruction step) kept replaying the demo into a GetTickCount busy-wait
  region (~3k instr/s) before it could check "done".  Stepping the replay in 20k
  chunks and breaking the instant every function is sampled -> **0.7s** (40x), same
  byte-exact result.  Not too broad, not thread-bound; parallelism would not have
  helped.  (Interpreter note: run liftverify under CPython — verification is short
  clone+diff bursts the PyPy JIT can't amortise: clone 10.2 vs 49.8ms, verify 42.5 vs
  70.5ms.  PyPy stays right for replay.py / play.py.)
- Next: refactor a passing lift (start with `_win_IsWinOpen`, 22 insts) into readable
  `simant/recovered/`, keeping this same oracle green — the actual recovery goal.

## 2026-07-10 — dos_re's automatic lifting pipeline works on Win16 (and SimAnt found 2 upstream bugs)
- dos_re landed automatic literal lifting (M0 decode/CFG census, M1 emitter, M2
  liftverify, M3 refactor-loop proof; `dos_re/docs/lifting_design.md`).  Applied it
  here the same day — the OS-free layers run on Win16 code UNCHANGED:
  * **Census: 1238/1319 named SIMANTW.SYM functions v1-liftable (94%)** — better than
    any DOS port at scale.  Refusals: 48 jump-table dispatchers (`_GBoxFill` is one),
    33 unsupported (inline x87 etc.).  Liftable median 48 insts, max 1233.
  * **M1 proof**: emitted lift of `_Windows_MakeTable4x4` passes our own hand-island
    A/B harness byte-exact (all counts, 3/3 blocks) with zero hand edits.
  * **Call-coupled frontier UNBLOCKED**: `_win_GetObjRect` (near-calls) +
    `_win_IsWinOpen` (far API call, IsWindowVisible) lifted and verified in situ over
    the ghost demo replay — digest + instruction count identical to pure ASM
    (77,519,358 instr), hooks fired (4/4, 2/3 blocks).  `emulate_call` composes
    through the win16 API layer exactly as designed.
- **Two real lifter bugs found by SimAnt, fixed + regression-tested upstream**
  (dos_re `11917f2`, `fbb2ad3`): (1) entry-instruction interpreter-fallback recursed
  into the lifted hook itself (SimAnt prologues are `enter`, a fallback op — every
  such function hit it); (2) emulated calls only recognized C-convention returns —
  pascal `ret n`/`retf n` (every Win16 API!) never matched, so the emulation ran away
  through the rest of the program.  DOS ports never saw either.
- Verify caveat learned: free-running this snapshot is NOT a terminating drive (modal
  wndproc wait-loop → CallbackOverrun in pure ASM too) — in-situ lift verification on
  win16 uses DEMO REPLAYS as the drive, which is our evidence baseline anyway.
- Next: a `scripts/liftverify.py` win16-flavoured driver (snapshot+demo -> emit,
  install, verify, ledger), then start the lift -> refactor -> `simant/recovered/`
  loop on the hot list; jump tables + x87 functions stay hand territory.

## 2026-07-10 — VM-accuracy audit of the scroll path: CLEAN (and a probe-methodology lesson)
- Owner asked whether the residual "occasional ghosting" (demo_134538, v3, replays
  2157/2157) indicates a VM accuracy problem.  Audit of one controlled scroll per
  direction, from the anchored snapshot, all under pure ASM (replay installs no islands):
  * **Pixel shifts: exact** — every scroll's surface equals the pre-surface shifted by
    exactly (dx,dy), all four directions (per-scroll verifier, 0 violations).
  * **Edit arrays: exact** — the game's screen-tile arrays ([143E]/[1442] far ptrs,
    31x23 at this window size) shift perfectly on every scroll (682/682 interior match;
    up-scroll fills the exposed row with fresh grass tiles correctly).
  * Scrollbar scrolls take _UpdateEdit's [1456]!=0 early branch (no _ScrollEditArrays —
    by design; the arrays are synced elsewhere and verified above).  The [1456]==0
    guarded branch (auto-center / _CenterEdit) calls _ScrollEditArrays(dx,dy) =
    2x _memmove (seg4:062C) + 0xFF-sentinel fill of vacated rows.
- **Methodology lesson (important):** the earlier "residual vs ground truth" numbers
  were PROBE ARTIFACTS — injecting `_invalidate(whole client)+WM_PAINT` out of band
  does not reproduce the game's real full redraw (game-side state my injection skips),
  so surface-vs-forced-repaint diffs measured the probe, not ghosts.  Injected paints
  are NOT a valid oracle for this engine; only the game's own paints are.
- Verdict: no VM/CPU-semantics inaccuracy found on the scroll path; islands stay
  byte-exact-gated; suites green.  The remaining user-visible "occasional ghosting"
  is not yet reproduced in any replayed surface — next capture: F10 screenshot + F12
  snapshot at a visibly-ghosted moment (surface + arrays + origin in one artifact,
  diffable offline with no injected paints).  Candidates: viewer-thread tearing
  (play.py composite fence), or the auto-center jump reading as a flash.

## 2026-07-10 — ghost2 demo: scrolls verified clean on the fixed build; demo v3 (arrival notes)
- Owner reported residual up-scroll ghosting + a "refresh jump" and recorded
  `ghost2.jsonl` (1435 records).  A full per-scroll pixel scan of the replay shows all
  23 scrolls (down/right/up/left) **pixel-perfect on the fixed win16** — 0 stale pixels
  each.  Most likely the live session still ran the pre-fix build (play.py loads win16
  at launch; restart required).  Watch for a re-report on a restarted session.
- The demo itself exposed two REAL replay gaps (win16_re `6416df4`):
  (a) **polled input is arrival-derived**: SimAnt's tick spins on
  GetAsyncKeyState(VK_LBUTTON) without pumping (MYTIMERFUNC+0x3BA), so a
  consumption-only replay deadlocked at record 970.  Demo v3 records the drainer's
  arrival notes as "a" records; the player applies them at pump touchpoints and via
  the replay input_drainer (refresh_polled_input for non-pumping polls).
  (b) **snapshot resume left sysobj.interactive=True** (pickled from the live
  session) — tick_count() returned the frozen clock and MAINWNDPROC's GetTickCount
  drag loop spun forever; load_snapshot resets it (host wiring, like message_source).
- Proof: a v3 demo emulating the ghost2 scenario (down/right/up/left scrolls + mouse
  moves + a click; `artifacts/demos/ghost3.jsonl`, 728 records) replays 728/728,
  digest-identical twice (`d071340d…`), clean nest.  v2 demos (ghost2) cannot replay
  past a polled-input wait — re-record under v3 for full-session repro.

## 2026-07-10 — tile-ghosting FIXED: the update region is real (win16_re `b55a6f7`)
- The owner's F11 repro demo (`ghost.jsonl`, 635 records, anchored to snap_125747)
  replayed deterministically and became the microscope.  Probe chain: our ValidateRgn
  couldn't subtract (single-bbox "clear only if fully covered") → fixed with a true
  rect-list region — **necessary but not sufficient** (digest unchanged).  The decisive
  probe: one WM_PAINT with a 59-rect region painted 4764 px of PRE-scroll content at
  bbox (158,0,258,162) — almost entirely OUTSIDE its own region.  SimAnt's painter
  redraws its whole changed-objects list (ants at old positions included) and RELIES
  on real USER clipping the writes to the update region it carefully built.
- Fix (win16_re): `Window.update_rects` rect list + ValidateRgn rect-splitting
  subtraction + **BeginPaint clips the paint session to the region** (surface snapshot
  at BeginPaint, restore-outside-region at EndPaint — byte-equivalent to per-op GDI
  clipping for the post-paint surface).  Evidence: first scroll = pixel-perfect 16px
  shift (0 violations, was 4764 stale); full demo replays to a clean nest
  (digest `30b93a02…`, was `81fdcae7…` ghosted).  Gates green (win16_re 89, here 182).
- Method note: three armchair root-cause theories were wrong in a row (tile-periodic
  dirt makes a one-tile scroll look like a no-op — deceptive evidence); the repro
  demo + instrumented replay settled it in three probes.  Demos earn their keep.

## 2026-07-10 — play.py adopts the dos_re hotkeys: F10 screenshot, F11 demo toggle, F12 snapshot
- Same muscle memory as every dos_re port (template_dos_port/docs/cookbook.md).  F12
  replaces F9 for snapshots (F9 stays as a legacy alias); F10 screenshots every game
  window's composited frame to `artifacts/screenshots/` (this shadows SimAnt's own
  F10=exit accelerator — File > Exit still works); **F11 starts/stops demo recording
  mid-session, auto-saving an anchor snapshot first** so every F11 demo replays out of
  the box — stopping prints the exact `replay.py --from-snapshot` command.  Smoke-tested
  scripted end-to-end: F10 (5 PNGs), F11 start→stop (17 records), the printed replay
  command consumed 17/17 records cleanly, F12 snapshot saved.

## 2026-07-10 — in-game demo record/replay works; the scrollbar tile-ghost has a repro demo
- **Demo v2** (win16_re `f1261f0`): a demo now records the PeekMessage timeline too
  ("p" records with the consuming filter) — in-game SimAnt never calls GetMessage, so
  v1 demos were blind to everything in-game.  Headers carry a snapshot anchor;
  `play.py --resume X --record d.jsonl` anchors, `replay.py d.jsonl --from-snapshot X`
  demands it (and checks the instruction count).
- **Snapshot resume inside a callback** (win16_re `ae050cb`): F9 parks inside the
  sim-tick TimerProc; the dispatching call_far frame wasn't in the snapshot, so resume
  fell off the sentinel when the tick returned (this had made every in-game resume
  time-bomb).  call_far frames are now serializable (api+argbytes+sp), snapshots carry
  the pending chain, the sentinel hook completes orphaned returns (DispatchMessage /
  SendMessage only — post-work APIs fail loudly by name).  Legacy snapshots: re-anchor
  by injecting the reconstructed frame + re-saving (see snap_ghost_base).  Also
  anchored the headless GetTickCount instruction floor to the saved clock — un-anchored
  it froze the clock ~28M instructions after resume, stalling every busy-wait.
- **End of stream for peek-driven games** (win16_re `eb09c6d`): first peek past the
  last record raises DemoEnded — the deterministic stop; GetMessage path already had it.
- **The ghost repro** (`artifacts/demos/ghost_scroll.jsonl`, anchored to
  `artifacts/snapshots/snap_ghost_base` = snap_114308 + reconstructed frame): one
  WM_VSCROLL SB_LINEDOWN to the Quick Game window, 359 records.  Replays are
  digest-identical across runs (`7d2e0a6c…`) and end with the tile ghost on the
  Quick Game surface (the 64×16 object rect at (192,128) repainted with one-tile-lower
  content).  **Root cause already diagnosed**: `Window.update_rect` is a single
  bounding rect, so ScrollWindow's exposed strip (0,330,423,346) unioned with the
  ant's rect (192,128,256,144) becomes a 218px slab whose repaint overwrites the
  just-scrolled pixels.  Fix = true multi-rect update region + paint clip; this demo
  is its before/after evidence.
- Recording harness note: one `cpu.run(4096)` swallows a whole sim tick (~14M instr,
  nested call_far), so wall-clock-timed input scripting is misleading — post input
  up front or pace by consumed records.

## 2026-07-10 — USER.421 wvsprintf: SimAnt's panic handler can finally speak
- Frontier hit in play (`USER.421 from 0E99:18E3`, 209M instructions in).
  Identified from the CALL SITE, not the ordinal table: the caller is
  `GR!_Punt` (seg2:18BE) — `enter 0x200,0` (a 512-byte local), `KillTimer`,
  then `wvsprintf(lpOutput=the local, lpFmt=its own far char* arg,
  lpArglist=&its varargs)`, then `MessageBox(hwnd, buf, "Fatal Error",
  MB_ICONHAND)`, then `exit(1)`.  **_Punt is SimAnt's panic handler — 46 call
  sites** (db_/cache/index family in seg7, win_LoadWindow, GR init, ...).
- Implemented in win16_re `1839bfd`: `win16/wsprintf.py`, a pure VM-free
  engine (word-sized ints, `l` -> dword, far LPSTR for `%s`, `%hs` near via DS,
  `%p` as SSSS:OOOO, C flag/width padding).  Covers exactly the specifiers
  present in SimAnt's 105 format strings; precision/`+`/floats raise
  `FormatGap` rather than guess.  Verified by rendering real DGROUP format
  strings through the actual `_Punt` in the VM — e.g. "Purge handle - handle
  not found!! handle=1234:0010", "Packing database - 100000 bytes currently
  wasted".  Gates: win16_re 79, simant_port 182.
- **Open: WHY did it punt.**  The trace before it is a `KERNEL.22` GlobalFlags
  burst — that's `GR!_mem_Freed`/`_mem_Type`/`_mem_LockLevel` (decoded: freed =
  discardable&&discarded, type = discardable?3:1, locklevel = handle?
  (discardable? lockcount : 1) : 0).  Our GlobalFlags returns 0 (fixed,
  non-discardable, unlocked), which is self-consistent for the selector heap,
  so those aren't obviously wrong.  The `_Punt` message itself now names the
  failing subsystem — **reproduce the crash and read the Fatal Error box.**

## 2026-07-10 — recovered SIMONE's PRNG: the simulation LFSR + its 13 entry points
- The queue's highest-fan-in leaves: SimAnt's simulation randomness is ONE
  16-bit LFSR (`seed <<= 1; if carry: seed ^= 0x1BF5`, seed word DGROUP:CBF2),
  stepped by every `_SRand*` call — `_SRand1(n)` returns `seed % n`, the nine
  compiled copies `_SRand2.._SRand256` return `seed & (2^k-1)` (masks proven
  byte-exact by install-time signatures built from the names).  Plus the seed
  accessors: `_Set/GetSRandSeed`, `_GetRRandSeed` (reads the BIOS tick dword
  0040:006C — the "real random" game seed), `_SetRRandSeed` (empty stub).
  Readable source: `recovered/simone.py`; 13 new islands in `hooks.py` (18
  total).
- Oracle upgrade: these A/B tests compare **FLAGS, the seed word, and the
  freed-frame residue** ([sp-2] saved BP, [sp-4] result scratch) on top of the
  registers — islands reproduce flags via the CPU's own set_logic_flags/shift
  helpers, so they match the interpreter by construction.  Gate 182 green
  (134 new A/B cases).  getting the PRNG byte-exact is the precondition for
  everything above it: every `_DoAntSim*` behaviour routine consumes it.

## 2026-07-10 — static call-graph probe: the bottom-up recovery queue exists
- New `win16/insn.py` (decode-only 8086/186 length walker; handles the
  FP-emulator `INT 34h-3Dh` +modrm forms this no-x87 build is full of) and
  `win16/callgraph.py` (call extraction over the loaded image: near, far via
  seg_bases, API via thunk `hook_names`, indirect flagged) — game-agnostic,
  in win16_re `5b384be`.  `simant/probes/callgraph.py` joins them with the
  SYM table and classifies every routine: **leaf** (no calls — fits the A/B
  oracle as-is), **api** (OS calls only — island can service them through the
  Python API layer), **coupled** (recover after its callees), **indirect**.
- **1313 routines, 377 leaves, 95 api-only.**  Per module (leaf/api/coupled/
  ind): SIMANT 25/13/127/0, GR 30/48/88/15, ANTEDIT 31/4/108/2, _TEXT
  128/19/93/8, SIMONE 76/0/93/0, SIMANT1 24/0/99/0, SIMTWO 63/11/204/4.
  Sanity gates in `test_callgraph.py`: MakeTable4x4/1x1 classify as leaves
  (proven pure), _GBoxFill shows its API calls, >60% of near-call targets
  land exactly on named entries (decoder is in sync, not reading noise).
  Gate 48 green.  `python -m simant.probes.callgraph` prints the queue;
  `--seg N` a per-module ledger.  Many sub-4-byte "leaves" are ret-stubs —
  fold them into their module's recovery rather than one-by-one islands.

## 2026-07-10 — SYM resolver rewritten segment-aware; SimAnt's source-module map recovered
- `probes/symbols.py` now parses the real MAPSYM structure (MAPDEF → SEGDEF
  chain → per-segment SYMDEF tables) instead of the flat offset-only scan whose
  cross-segment mis-namings the 07-09 frontier note warned about.  SYM segment
  order == NE segment order, proven by byte-anchors (`test_symbols.py`): the
  signature-verified MakeTable4x4/1x1 addresses resolve exactly, seg2:19E6 is
  `_GBoxFill`, and the NE entry seg4:0061 is `__astart`.  Lookups are symdeb
  style (`MODULE!_name+0xNN`); `symbols_in_segment/range` are segment-scoped.
- **The SYM names SimAnt's original source files** — the ten SEGDEFs are the
  compile modules: seg1 SIMANT (main, 165 syms), seg2 GR (graphics, 181),
  seg3 ANTEDIT (editor, 145), seg4 _TEXT (C runtime + Windows glue, 254),
  seg5 SIMONE (169), seg6 SIMANT1 (`_DoAntSim` at 0 — the ant sim, 123),
  seg7 SIMTWO (282, incl. the 103-routine `_win_*` toolkit + `_GetStrategy`/
  castes), seg8-10 data (SIMANT_DATA_GROUP, PACK, DGROUP).  `simant/recovered/`
  should eventually mirror this module layout.  Gate 43 green (7 new).
- Next per the bottom-up plan: static call-graph extractor over the
  disassembly → leaf queue → batch-recover pure leaves, then compose upward
  (`_win_IsWinOpen` first of the call-coupled three).

## 2026-07-10 — dos_re/win16_re bumped; PyPy measured at 8x for headless SimAnt runs
- Chain bump: dos_re `3be9439` (modrm/displacement inlining round — byte-exact,
  proven here by the island A/B oracles staying green) → win16_re `037bd69` → this repo.
- dos_re established PyPy + pytest-xdist as its standard fast paths
  (`dos_re/docs/performance.md`); measured what carries over to us
  (`win16_re/docs/performance.md`): **PyPy 8x on headless interpretation**
  (0.46M → 3.69M instr/s, 20M-instr boot, identical end CS:IP) — use it for
  replay, A/B oracles, verify sweeps.  Only 1.9x on `boot.py` (trace-on string
  formatting doesn't JIT — keep the probe on CPython, it doesn't matter).
  Suite: PyPy 4.6s vs CPython 6.5s; **xdist is a loss** (9.6s) on 36 tests —
  don't use `-n auto` until the suite is much bigger.  PyPy path:
  `winget install PyPy.PyPy.3.11` → `pypy -m pip install pytest numpy`; the
  repo's `sys.path` shims resolve the whole chain, no pip install of the repos.
- **Update (same day): the interactive viewer runs under PyPy too** — pygame-ce
  ships PyPy wheels (upstream pygame doesn't; import-compatible), tkinter is
  bundled, Pillow installs.  `pypy scripts/play.py` is now the fast way to play;
  owner confirmed interactively.  Doc updated (win16_re `377d5fd`).

## 2026-07-09 — recovered _Windows_MakeTable1x1 (the 1:1 tile packer); MakeTable family done
- The no-zoom sibling of MakeTable4x4 (seg4:46BB): packs pairs of source tiles into one
  4bpp byte via an XLAT table at SS:0x1B56 (`al=ss:[0x1B56+t0]; al|=ss:[0x1B66+t1]`),
  `count>>1` iterations.  `recovered/render.py: windows_make_table_1x1`; A/B-gated
  byte-exact (counts 2/5/16/127/128, odd exercises the dropped tile).  Islands: 5.
- **Frontier note for the next recovery:** an in-game named profile (scratchpad,
  snap_204728) shows the remaining hot routines are call-COUPLED, not pure loops:
  `_GBoxFill` (0e99:19E6, a GDI box-fill wrapper calling 0060:020C/013C/011C),
  `_win_GetObjRect` (430e:C2D2, object-table lookup + nested near-calls +
  movsw RECT copy), `_win_IsWinOpen` (430e:C256, handle-table lookup + an API
  validity call).  A pure "skip-the-routine" island doesn't fit these — they delegate.
  Recovering them means either an island that re-issues the sub-calls, or recovering
  them as readable source verified by a different oracle.  Caveat: seg-2 symbol names
  are OFFSET-ONLY approximations (e.g. _AdjustWndMinMax/_DoToAlarm resolve INSIDE
  _GBoxFill) — always cross-check the disassembly, not just the name.

## 2026-07-09 — recovered _Windows_MakeTable4x4 (first source recovery this session)
- The game's terrain tile-to-pixel expander (SIMANTW.SYM seg4:4674): per column,
  one `lodsb` (tile colour index) + four `stosw`, each scanline's 16-bit fill word
  (four packed 4bpp pixels) from a 4x32-word table at `SS:0x1A56` (0x40 row stride);
  four rows `2*count` words apart (the DIB scanline).  Preserves all regs (pusha/popa
  + push bp + push ds/es), `retf` caller-cleans.
- Clean logic in `simant/recovered/render.py`; signature-verified island in
  `simant/hooks.py`; A/B oracle in `test_hooks.py` proves byte-identical band + exit
  state vs the ASM (counts 1/4/16/128).  Install count 3→4.
- **Method reminder:** PC-sample (`simant/probes/profile.py`) → live-trace the loop →
  recover VM-free logic → hook as a signature-verified island → A/B byte-exact gate.
  Existing islands: `__aFuldiv`, `_Unpack` (LZSS load bottleneck), a seg2:3460 bytecopy.
  Named routines come from `assets/ANTWIN/SIMANTW.SYM` via `simant/probes/symbols.py`.



## 2026-07-09 — Quick Game "sized for a smaller window" on first show: missing WM_SIZE
- Owner: on first show the QG content expects a smaller window than actual; a manual
  resize fixes it.  Confirmed: DIB blit = 400×304 into a 423×346 window → a 24,758-px
  white L-strip (right 23px + bottom 42px = the window's white background).  The game
  sizes its view to the client only on WM_SIZE (post-resize DIB = 464×352 fills 463×
  348); on first show it never gets one, so it keeps a default 400×304 frame.
- **Fix (play.py):** real Windows sends WM_SIZE when a window is first sized, so post
  one with the actual client size when a resizable WindowView is created (scoped to
  `_can_resize`; fixed panels already match their creation size).  Verified on
  snap_204728: after the nudge the game re-renders 432×352 and fills (0 white px).
- Owner also confirmed the earlier version-fence fixed the ghosting.

## 2026-07-09 — Quick Game "ghosting" is a presentation-thread race, not a render bug
- Owner: redrawing/ghosting on the Quick Game view (snaps 204728 / 204832 / 204849).
- **The game's rendering is correct.**  Diffing the "clean" (204832) and "ghosting"
  (204849) frames — same 463×348 size — shows only 2.6% changed, confined to the
  normally-animated chamber rectangle (no smear/band); forcing a full re-render
  (resize) reproduces the same clean image.  The QG frame buffer is a ~80K (>64K,
  multi-selector) DIB, blitted whole via one SetDIBits; no BitBlt scroll touches the
  surface.  So the artifact is NOT baked into the game's graphics.
- **Root cause (presentation):** the CPU runs on a daemon worker thread (`_run_cpu`)
  and rewrites the surfaces (a full-frame SetDIBits takes ms) while `_tick` reads them
  on the tkinter thread to draw — a concurrent read catches a half-updated buffer =
  torn/ghosted frame, only while actively redrawing (idle → no blits → no tearing),
  which is exactly why the static snapshot looks clean.
- **Fix (best-effort, play.py):** version-fence the composite — if a surface write
  completed during the copy, redo it once.  If residual tearing remains, the robust
  fix is to pause the CPU worker at a boundary around the frame read.  UNVERIFIED
  live (a GUI timing artifact; not reproducible from a snapshot) — pending owner test.


## 2026-07-09 — Surface View half-black: selector RPL aliases in the huge heap
- **Symptom:** SimAnt's "Surface View" (a 512×256 4bpp = 64K DIB) rendered with the
  top half black; OTVDM fills it fully at the same window size.
- **Deep trace (snap_191636):** decode is faithful — the DIB's upper 32K is genuinely
  unwritten.  The terrain rasterizer at `275f:4674` (fills 4 rows/call) is fed dest
  pointers by `18c0:dafc`, which SIGN-EXTENDS the 16-bit byte offset before adding it
  to base `864f:0000`.  At offset ≥ 0x8000 the sign bit borrows into the segment word:
  `864f:0000 + 0xFFFF8000 = 864e:8000`.  `864e`/`864f` share LDT index `0x10C9`
  (differ only in RPL, which hardware ignores) → same block on real HW.  Our `sel_base`
  keyed by exact selector → `864e` missed → real-mode fallback → top-half writes lost.
- **Fix (moved into dos_re — the faithful home):** the RPL-agnostic resolution now
  lives in dos_re Memory: `sel_base` is keyed by descriptor and every lookup masks the
  RPL (`sel & 0xFFFC`) in `_xlat`/`rb`/`rw`/`wb`/`ww` — real hardware ignores RPL for
  descriptor lookup, so this is faithful, not a workaround, and benefits any
  protected-mode port.  win16's `hugeheap` just registers descriptor keys (one per
  selector); `load_snapshot` re-keys restored maps to descriptors for old snapshots.
  Verified: `_xlat(864e,0x8000)` lands in the buffer; 864f/864e/864c all resolve
  identically.  (Owner asked to review win16↔dos_re placement — this was the one thing
  that belonged in the backend; the x87 work was already correctly in dos_re, and the
  message-pump / GDI / allocator changes are correctly win16.)
- **Method note:** the winning technique here was catching the write loop with a
  `write_watcher` + a replacement-hook on the filler's entry to read the caller's
  return address and the dest far-pointer per call — that exposed the `864e` selector
  and the sign-extension.  Reusable for any "half/partial buffer" rendering bug.


## 2026-07-09 — PALETTEINDEX COLORREF bug: SimAnt's meter bars rendered black
- **Symptom (owner):** the caste/behavior/colony meter bars + the ribbon's central
  status strip drew as solid black blocks.
- **Root cause:** SimAnt fills them with `CreateSolidBrush(PALETTEINDEX(8))` =
  `0x01000008`.  `CreateSolidBrush` did `color & 0xFFFFFF`, stripping the COLORREF
  *type* byte, so the brush became literal RGB `(8,0,0)` ≈ black instead of *palette
  entry 8* (light grey).  Confirmed by replaying `snap_164229` and logging the brush
  arg (471×/200k instr, all `0x01000008`); palette entry 8 = `(192,192,192)`.
- **Fix:** `gdi.colorref_rgb(colorref, palette)` — `PALETTEINDEX(i)` → DC's realized
  logical palette entry i (else the app system palette); `RGB`/`PALETTERGB` → low 24
  bits.  `CreateSolidBrush`/`SetTextColor` keep the full 32-bit COLORREF;
  `FillRect`/`PatBlt`/`TextOut`/class backgrounds resolve at draw time against the DC
  palette (`dc_palette_entries`).  Verified: the ribbon status strip repaints grey.
  The colony H/P + caste meter bars use the same path but only repaint on value
  change, so they self-correct on next redraw.  Tests: `tests/test_colorref.py`.
- **Method note:** replaying a saved snapshot + wrapping `machine.api.entries[(mod,ord)]
  .handler` to log/inspect a specific window's draw ops (compare `_dc_surface(hdc)` to
  the target window's surface) is a fast, repeatable way to find a rendering bug
  without re-booting to in-game (~150s/boot).  Scripts under scratchpad.

## 2026-07-09 — ribbon buttons dead: clicks must reach the composited child's wndproc
- **Owner:** caste slider works now, but ribbon-panel buttons don't respond.
- **Cause:** the ribbon is a WS_CHILD toolbar (0x116) composited into the main frame,
  with its OWN wndproc that hit-tests its buttons.  play.py posted every main-canvas
  click to the frame (0x114), so 0x116 never saw them.  The promoted panels worked
  because their clicks go straight to their own view/window (like a real child click).
- **Fix:** play.py `_route_click` walks the composited child tree (skipping
  standalone/promoted children) to the deepest visible child under the cursor and
  posts THERE with child-relative coords — real-Windows child-click delivery.  Verified
  against snap_174855: ribbon-area clicks → 0x116 (local coords intact), body clicks →
  0x118.  cursor_pos unchanged (origin+local = same screen point), so the WAP poll and
  the raise/z-order hit-test stay consistent.  **Not yet verified end-to-end** that the
  ribbon reacts (headless click injection is too fragile — wrapping the pump leaked the
  callback-return sentinel); reasoned-correct, pending owner test.

## 2026-07-09 — in-game freeze after a caste click: live polled input for non-pumping loops
- **Owner:** clicked Caste Control, it updated, then the game froze (snap_174855).
- **Root cause:** `snap_174855` had `async_keys=[1]` — LBUTTON stuck DOWN.  The caste
  slider enters a DRAG loop that spins on `GetAsyncKeyState(VK_LBUTTON)` waiting for
  release, WITHOUT calling Peek/GetMessage (freeze footprint: USER.17/29/249/186 only,
  no 108/109, at 0100:bf08).  Polled state was fed only on message CONSUMPTION, so the
  WM_LBUTTONUP the release generated sat unconsumed → button read down forever.
- **Fix:** polled state (async_keys/cursor_pos) is now fed at input ARRIVAL, not
  consumption.  The interactive driver's `_drain_input` calls `_note_input` per drained
  message; `get_message`/`peek_message` skip their own note while a drainer is attached
  (no double-note); `GetAsyncKeyState`/`GetKeyState`/`GetCursorPos` call
  `refresh_polled_input()` (drains host input) before reading.  Headless/replay (no
  drainer) unchanged — deterministic consumption-time derivation.
- **Design note:** this is the general rule for WAP/polling games — a live poll
  (GetAsyncKeyState) must reflect real-time host input independent of whether the game
  pumps its queue.  Verified on snap_174855: delivering WM_LBUTTONUP clears async_keys
  and the drag loop at 0100:bf08 exits to 0e99:47a4.

## 2026-07-09 — verified panel clicks end-to-end; exposed + fixed x87 DA/DE integer arithmetic
- Replayed `snap_171018` and INJECTED a click on the Caste Control triangle (raise +
  cursor_pos + WM_MOUSEMOVE/LBUTTONDOWN + async LBUTTON).  Result: the click was
  dispatched to **0x13e's wndproc** (WM_LBUTTONDOWN) and the game's own
  `WindowFromPoint` returned **0x13e** — i.e. the raise fix routes the click to the
  right panel (before, it went to 0x14a Quick Game).  284k GetCursorPos polls confirm
  the WAP engine is live and reacting.
- The caste handler then hit an **unimplemented x87 op — `DA /0` (FIADD m32int)**.
  dos_re's execute_fpu had D8/DC real memory arithmetic but not the DA (m32int) / DE
  (m16int) integer-arithmetic escapes.  Added them (dos_re 692103c, bumped here);
  test `test_x87_integer_memory_arithmetic`.
- After that fix the injected run reached a further `IndexError` at 0060:0000 (IP=0) —
  almost certainly an INJECTION artifact (bad control-flow from faking the click into a
  loaded snapshot; the follow-on CallbackOverrun corroborates state drift), not a clean
  opcode gap.  Real remaining gaps will surface in live play.

## 2026-07-09 — panel buttons dead: overlapping panels + WindowFromPoint z-order (fixed) + USER.129
- **Owner:** Caste/Behavior/ribbon buttons don't respond; right-click Quick Game →
  `USER.129` gap.
- **USER.129/130 GetClassWord/SetClassWord:** negative idx → a WNDCLASS field (GCW_*),
  non-negative → a WORD in the class's cbClsExtra bytes (`WndClass.class_extra`, sized
  at RegisterClass).  Hit by the WAP right-click hit-test.
- **Dead buttons — root cause:** the in-game panels are WS_CHILD windows that OVERLAP
  each other in the game's virtual screen space (stacked MDI children).  The WAP
  wndproc re-resolves the polled cursor with `WindowFromPoint`, whose tie-break among
  same-depth overlapping windows is LAST-in-z-order = Quick Game (0x14a).  Verified on
  `snap_171018`: a click ANYWHERE on Caste/Behavior/Nest returned 0x14a, so their
  buttons were dead.  (This overlap predates the promotion-to-OS-windows; promotion
  just made it hittable/visible.)  **Fix:** play.py `_raise_z` — on any mouse event
  over a promoted WS_CHILD panel, move it to the top of the VM window list, like
  activating an MDI child.  After: each panel resolves to its own hwnd.  The ribbon
  (0x116, composited, un-overlapped) already resolves correctly, so if its buttons
  still misbehave the cause is elsewhere (message-target vs poll) — pending owner test.
- Owner also confirmed the scroll ghosting STOPS after resizing the Quick Game window
  — consistent with the overlapping-self-BitBlt theory (resize forces a clean full
  redraw of the frame buffer, discarding the smeared shift).

## 2026-07-09 — ghosting when scrolling "Quick Game": overlapping self-BitBlt (candidate fix)
- Owner supplied a mid-ghost snapshot (`snap_171018`): a faint dotted vertical trail
  below the tunnel + a sharp-edged horizontal band in the map view.
- **Localised:** the view (hwnd 0x14a) is painted by ONE full 400×304 4bpp
  `SetDIBitsToDevice` (start=0, lines=304); the terrain decodes cleanly (right colours,
  clean dithering), so it is NOT a DIB-decode bug.  The ghost is stale content in
  SimAnt's *persistent* frame buffer (idle → the buffer is re-blitted, not recomputed,
  so the trail survives) — i.e. a scroll SHIFT corrupted the buffer.
- **Fix (candidate):** `blit()` (win16/api/objects.py) copied rows top-to-bottom
  reading live from `dst.pixels`, so an overlapping self-BitBlt (scroll a surface by
  BitBlt-ing itself shifted down) read already-overwritten rows → vertical smear.  Now
  reads from a pristine source snapshot when `src is dst`.  `tests/test_blit.py`.
- **Caveat:** could NOT reproduce the scroll headlessly — arrow keys and mouse-at-edge
  (with LBUTTON) did not trigger a scroll / any self-BitBlt from `snap_171018` (the
  runs even halted early with an empty exception under injected input).  So this is the
  strongest-candidate fix by mechanism, NOT a verified one.  If it still ghosts after
  owner re-test, next suspects: the scroll is the game's own VM memcpy of the buffer
  (would be faithful — look elsewhere), or a partial `SetDIBitsToDevice` band; get the
  owner's exact pan method (drag / edge / follow-ant) to reproduce.

## 2026-07-09 — in-game panels are now REAL OS windows (owner's call over painted chrome)
- Owner preferred native windows to the painted caption bars.  Captioned WS_CHILD
  panels ("Caste Control", "Behavior Control", "Black Nest View") are now each their
  own tkinter Toplevel with native chrome (OS title bar + close box), floating above /
  transient to the main frame.
- **Why the mouse path is safe:** the game lives in the VM's *virtual* screen space
  (all coords come from `_window_origin`, which walks the parent chain) and play.py
  always posts CLIENT-relative mouse coords — so a promoted panel's real host-window
  position is irrelevant to hit-testing.  No per-window screen-position sync needed;
  this is why breaking them out did NOT reopen the in-game aiming problem.
- **Mechanism:** `compositor.own_windows()` = parent==0 frames + captioned children
  (`presents_standalone()`); `composite(..., standalone=SET)` skips promoted children
  at any depth (SimAnt nests panels under a plain body window).  Default `()` keeps
  headless/screenshots compositing them in with the painted caption (still there as a
  fallback).  `WindowView._place()` positions at the absolute VM origin.
- Painted caption bars from the prior entry are retained for the headless path only.

## 2026-07-09 — SimAnt is in-game and the sim runs; panels get title bars; more USER gaps
- **The simulation is ALIVE.** With the x87 completion (dos_re) + the GetTickCount
  wall-clock/instruction-floor fix landed, Quick Game reaches in-game and the ants
  *move* (owner-confirmed, snapshot `snap_160504`) — just very slowly (each sim tick
  runs inside a TimerProc callback through call_far; perf is the next island, not a
  correctness bug).
- **Closed three more in-game USER gaps:**
  - `USER.104 MessageBeep` — a UI cue on a click; returns TRUE (host beep not modelled).
  - `USER.272 IsZoomed` — hit on the in-game window-resize WM_SIZE path; returns the
    window's tracked `maximized` flag.
  - (both added to `ordinals.py`.)
- **Panels are real windows now (chrome).** The in-game control panels ("Caste
  Control", "Behavior Control", "Black Nest View") are `WS_CHILD | WS_CAPTION |
  WS_SYSMENU` composited INTO the main frame — so the host WM gives them no chrome and
  they read as flat rectangles ("no handles").  `compositor.py` now paints a Win3.1
  title bar (blue bar, white title, grey system box, min/max boxes per style bits) on
  any composited child with the full WS_CAPTION bits.  **Caveat:** overlay only — we
  still don't inset the client (`Window.client_size == window rect`), so the caption
  covers the child's top strip.  A real non-client model (GetClientRect inset +
  compositor reclaims the non-client + coordinate-mapping offset for captioned
  children) is the clean fix if that coverage matters; deferred because it touches the
  in-game mouse hit-testing that currently works.
- **Diagnostic lesson:** an earlier `log_createwin` probe mislabelled the CreateWindow
  MENU arg (`args[8]`) as the parent (`args[7]`), which is why the panels looked like
  NULL-parent top-level windows.  They are real children; `compositor.top_level_windows`
  docstring's "created with a NULL parent" claim is stale — the parent==0 selection is
  still correct for genuine top-level windows, but the SimAnt panels do NOT hit it.

## 2026-07-09 — dos_re is now a real git submodule (was an undeclared sibling checkout)
- **Owner caught it:** win16_re silently required `D:\Games\DOS\dos_re` to exist on
  disk (hardcoded default in each package's `_env.py`) — an unversioned, invisible-
  to-git dependency.  Checking it, the local sibling checkout had actually drifted 3
  commits behind `origin/main` with nobody noticing (no data loss though: dos_re's
  PUSHA/POPA + interpreter-speedup work are confirmed ancestors of origin/main).
- **Fixed:** `git submodule add https://github.com/missingno7/dos_re.git dos_re`,
  pinned at `9c9247b`.  `ppython/_env.py` / `simant/_env.py` / `microman/_env.py` now
  default to the vendored path (`Path(__file__).parent.parent / "dos_re"`);
  `DOS_RE_PATH` is kept as an explicit opt-in escape hatch for co-developing dos_re
  against a separate working checkout (not the default).  `git clone
  --recurse-submodules` is now sufficient for a fresh checkout — verified end-to-end
  (fresh clone, gate green, only the expected assets-not-present skips, zero
  reference to the old sibling path).
- **Noticed but out of scope:** three doc cross-references (`docs/methodology.md`,
  `docs/pitfalls.md`, `docs/bringing_up_a_game.md`) point at `dos_re/docs/
  methodology.md` / `ai_porting_charter.md` / `pitfalls.md`, none of which exist in
  the current dos_re docs layout (it was reorganized into `architecture.md`,
  `hooks_and_verification.md`, `demos_and_snapshots.md`, `state_mirrors.md`,
  `glossary.md` — pre-existing drift, not introduced by the submodule move). Fixed
  the relative path prefix (`../../DOS/dos_re` → `../dos_re`) but left the target
  filenames as-is; a follow-up should re-map them to dos_re's current doc set.

## Standing mechanisms (check here before building new tooling)
- **Memory model: selector translation (4MB).** win16 lifts the 1MB real-mode ceiling
  via `dos_re` Memory's optional `sel_base` (selector→linear-base dict). The loaded
  program stays real-mode in low memory; GlobalAlloc blocks are selectors mapping into
  [0x140000, 4MB) managed by `win16/hugeheap.py` (small=1 selector; >64K=consecutive
  selectors 8 apart → contiguous 64K so `__AHINCR=8` huge-pointer walking is correct;
  linear+selector reclamation). `mem._xlat(seg,off)` resolves any far pointer (used by
  SetDIBitsToDevice/_lread for huge buffers). DOS path (sel_base None) is byte-identical
  — dos_re suite stays 116 green. To grow past 4MB, bump WIN16_MEM_SIZE in loader.py.
  Verified: microman now boots THROUGH startup (no more `LoadPage Error = 9` memory-
  exhaustion box) into its WAP title animation and paints a real frame; ppython
  (the RE target) unaffected. Interpreter overhead of the selector branch is ~4%.
  `test_microman_boots_and_renders` now gates on the first non-blank paint (it used to
  assume a startup crash-frontier, which the selector fix removed). Full suite 38 green.
- **win16 is now game-agnostic; multi-game testing.** `win16/app.py create_machine(exe,
  winflags)` boots ANY NE. `scripts/games.py` = the test-game registry (ppython is the
  RE target; microman/bangbang/kye/skifree are fixtures to harden win16).
  `scripts/boot.py <game> [steps]` = generic frontier probe. `ppython/runtime.py` is now
  a thin adapter. Ordinal names for KERNEL/USER/GDI/MMSYSTEM extended so ANY import
  fails loud WITH its name. **MICROMAN status:** boots ~1.7M instructions (full startup
  + file loads + game init) to the frontier GDI.360:CreatePalette (the 256-colour
  palette subsystem — CreatePalette/SelectPalette/RealizePalette/GetPaletteEntries/
  GetNearestPaletteIndex/SetDIBitsToDevice + MMSYSTEM.2 sndPlaySound are its open APIs).
- **Snapshot at an event:** `play.py --snapshot-on-box Collision` saves an INSPECTION
  snapshot whenever a MessageBox whose caption/text matches appears (the crash box is
  "Ughhh!"/"Collision!"). CPU is parked in the modal handler so memory+CPU+pixels are
  consistent (the crash frame is captured); NOT resumable (native modal stack not
  saved) — inspect with `win16.vmsnap.load_snapshot`, use demos for reproducible
  replay. F9 still takes a resumable boundary snapshot during normal play. Before any
  modal blocks the GUI, `_flush_windows` force-renders the latest frame (else the
  version-gated renderer races and drops the final pre-modal frame, e.g. the crash).
- **Demos (record/replay):** `win16/demo.py` — the frame boundary is GetMessage, so a
  demo is the exact stream of returned messages + consumed dialog events (virtual-clock
  stamped). Record: `play.py --record FILE`, or set `services["demo_recorder"]`. Replay:
  `python scripts/replay.py FILE [--png DIR] [--snapshot DIR]`, or set
  `system.message_source = player.next_message` + `services["demo_player"]`. Replay is
  bit-exact (proven) and fails loud (`DemoDivergence`/`DemoEnded`) the instant the
  machine asks for input the demo doesn't have next. THIS IS THE VERIFICATION BASELINE
  every future hook/native replacement must reproduce.
- **Snapshots:** `win16/vmsnap.py` — `save_snapshot(machine, dir)` / `load_snapshot(dir,
  create_machine)`; three files (memory.bin, state.json, system.pickle). Must be taken at
  a message boundary (refuses if a modal dialog is open). `digest(machine)` = the
  game-observable fingerprint (memory + CPU + window surfaces + clock + timer intervals;
  the pump's internal timer_due schedule is deliberately excluded). In `play.py` press
  **F9** to snapshot (pauses the CPU at its next boundary first).
- **Console-first errors:** `play.py` prints every VM stop to stderr with CS:IP,
  instruction count, traceback, last trace lines and API call log — the window only shows
  a red "see console" banner. MessageBoxes are echoed to stdout. Built for AI operation.
- **Dialog engine:** `win16/dialog.py` (DLGTEMPLATE parser) + `win16/api/dialogs.py`
  (DialogBox/EndDialog/Get-SetDlgItem*/SendDlgItemMessage/DlgDir* + Dialog/
  DialogControlState). DialogBox runs the game's real dialog proc in the VM in a
  modal loop (WM_INITDIALOG → control events → EndDialog); other windows' timers
  keep firing under it. Presentation via `services["dialog_ui"]` (the player builds
  real tkinter widgets from the template, du_to_px layout); headless leaves it None
  and auto-answers OK/Cancel. Control state (text/checked/items/sel) lives in the
  DialogControlState objects — the single source of truth the widgets mirror.
  Window-like handles (dialogs, controls) resolve through `geom_px()` so geometry
  APIs treat them uniformly.
- **Interactive player:** `python scripts/play.py [--speed N] [--scale N]` — **each
  Win16 window is its own real tkinter window** (WindowView per handle; created/
  destroyed as the game creates/destroys windows). The Paulie Python window carries
  the game's real menu bar (from the MENU resource via `win16/menu.py`), with
  **live grayed/checked sync** from the game's EnableMenuItem/CheckMenuItem state —
  disabled items are unclickable, exactly like real USER (delivering WM_COMMAND for
  a grayed item crashes the game: Pause = idiv-by-zero). Game MessageBoxes appear
  as real modal boxes (`services["messagebox_ui"]`, CPU thread blocks until
  dismissed). DialogBox/WinHelp are logged-and-skipped stopgaps
  (`services["skipped_ui"]`, shown in the status bar) until the dialog engine
  lands. VM death shows a red banner, never silence. Pacing:
  `win16/interactive.py` installed as `Win16System.message_source`; headless
  replay leaves it None (auto-OK MessageBoxes, deterministic clock).
- **Interpreter speed:** ~300k instr/s standalone; a gameplay frame is heavy, so play
  is choppy (a few fps) — cProfile confirms it's ALL VM stepping (execute_opcode/
  fetch8/rb), NOT the Python GDI. Real fix = the dos_re method (hook hot routines →
  native). Boot to windows is ~6400 instr (instant); the main window is legitimately
  blank until New Game.
- **Gameplay gate:** `tests/test_gameplay.py` — boot→idle→WM_COMMAND(1050 New Game)→
  level intro msgbox + painted playfield + SOUND notes. The msgbox/sound logs are
  `api.services["messagebox_log"|"sound_log"]` (virtual-clock-stamped evidence).
- **Menu commands** (from the MENU resource): 1050 New(F2), 1100 Sound(F3),
  1150 Pause(F4), 1175 HighScores(F5), 1200 Exit(F10); attitudes 2151-2155
  (default 2153 Diamondback); control 2201 kbd / 2202 mouse; screen-set 2051-2053.

## 2026-07-09 — Fixes: snapshot pickle crash + sim-tick overrun (GetTickCount overshoot) (8dc8b50)
- **F9 snapshot crashed** ("cannot pickle _thread.RLock"): the driver sets
  input_drainer + yield_check (bound methods holding a Condition) on sysobj, and
  save_snapshot only detached machine + message_source.  Now detaches all four.
- **"Quick Game starts but never progresses" → CallbackOverrun (20M steps).** The
  sim tick paces each frame by spinning on GetTickCount; GetTickCount used
  max(clock_ms, instr_floor), and the interpreter runs ~2× faster than
  INSTR_PER_MS, so the floor overshot real time (~41s at 21s wall).  Inside the
  nested tick the game then thought thousands of frames were due → processed them
  all → overrun.  New Win16System.tick_count(): interactive hosts track the wall
  clock (kept current inside a callback by check_pause on 8192-step chunks),
  headless keeps the instruction floor (busy-waits still elapse; demo replay
  deterministic).  USER.13 + the WM_TIMER clock + TimerProc dwtime all use it.
- **NOT verified in-game by me** (headless reach is too slow); owner must confirm
  the ants now move.  Owner's "windows still flat / not resizable" report is a
  VERSION lag — the panel/resize/scrollbar work (4c7fdec/ad4da75) is newer than
  the yield_check commit their RLock came from; needs `git pull` + `git submodule
  update --init` (the FPU fix lives in the dos_re submodule).

## 2026-07-09 — FPU: SimAnt uses native inline x87; completed the emulator (was the ant-stall)
- **Do we have FPU? Yes, but it was incomplete.** SimAnt's FP is NOT INT 34-3D —
  disassembling the 111 OSFIXUP sites shows `fwait; es: <x87>` (native inline x87,
  D8-DF), which dos_re's execute_fpu runs (segment overrides included).  0 FP hits
  during boot/title; all FP is in the sim (routines __ftol/__fassign/__STRINGTOD
  + _BuildAntListA).  Statically enumerated the ~20 distinct x87 instructions from
  the fixup sites — execute_fpu implemented only ~1/3, so the ant physics hit
  UnsupportedInstruction and stalled (almost certainly why the ants don't move).
- **Fix (dos_re 47418f1; win16_re submodule bump 0e8708f):** added the missing
  register forms (D8/DC arithmetic, FXCH, FCHS/FABS/FTST/FXAM, FLD1/FLDZ/FLDPI...,
  FSQRT/FRNDINT/FSCALE/FSIN/FCOS, FNSTSW AX) and memory forms (single-precision
  m32 FLD/FST/FSTP, integer FIST/FISTP m32 + FILD/FIST/FISTP m16, D8/DC m32/m64
  arithmetic) + _fxam class/sign bits.  Verified sqrt(2)/FCHS/FXCH/FTST/FXAM/m32
  round-trip; dos_re suite + new test_core x87 case green; win16 gate 47 green.
- **Progression: NOT yet visually confirmed by me** — reaching in-game headlessly
  keeps timing out (~150s to reach + heavy nested sim frames).  The FPU gap was
  the strong suspect for "ants not moving"; with x87 complete the sim should
  advance.  Owner to confirm in play.py (new game → do the ants move?).  If a
  further x87 opcode is hit it fails loud ("x87 opcode XX /Y at CS:IP") — easy add.
- **Note:** dos_re edits must target the SUBMODULE path (win16_re/dos_re), commit
  there + push to the dos_re remote, then bump the pointer in win16_re.

## 2026-07-09 — In-game windows are real: title/close/resize/maximize/scrollbars (play.py)
- **Captured the in-game window styles** (log via a CreateWindow hook): the panels
  are WS_CHILD|WS_CAPTION created with a NULL parent (top-level framed windows):
  "Caste Control"/"Behavior Control"/"Black Nest View" = CAPTION|SYSMENU
  (titled/closable/fixed); "SimAnt - Quick Game" = CAPTION|SYSMENU|THICKFRAME|
  MAXBOX|VSCROLL|HSCROLL (titled/closable/resizable/maximizable/scrollable) —
  exactly the owner's "some closable-only, some resizable" description.
- **compositor.top_level_windows now selects parent==0** (desktop-parented), so
  these WS_CHILD panels are presented as their OWN tkinter Toplevels instead of
  being hidden by the old `not is_child` filter.  (ad4da75 chain: 4c7fdec+ad4da75.)
- **WindowView maps Win16 styles → native tkinter chrome:** WS_THICKFRAME →
  resizable (+ WM_SIZE on drag, debounced, so the game re-lays-out; the main
  AntRoot frame = WS_OVERLAPPEDWINDOW ⇒ now resizable); WS_SYSMENU → close box
  (posts WM_CLOSE to that window; main still quits); WS_H/VSCROLL → scroll bars
  (post WM_H/VSCROLL, thumb driven from the tracked scroll range).
- **NOT visually verified by me** — headless in-game render times out (reaching
  it is ~150s and each frame does heavy nested work), so this is built from the
  captured styles + standard tkinter and boots clean (gate 47), but the owner
  must confirm the in-game look/behaviour.  Risks: WM_SIZE relayout fidelity,
  panel positioning, scroll page-size (approximated).

## 2026-07-09 — In-game unfreeze: USER.236 + callback runs via cpu.run (faster, pausable)
- **USER.236 GetCapture** (c1efb66) — the mouse-capture poll; an unimplemented gap
  stopped the VM once in-game.
- **call_far rewrite** (c1efb66).  SimAnt's entire in-game runs inside the ~59fps
  TimerProc callback; call_far drove it one instruction at a time in Python, so
  in-game was slow AND un-pausable (the worker's check_pause never ran → window
  "frozen", F9 timed out).  Now a permanent replacement-hook at the sentinel
  CS:IP raises to signal return, so the callback body runs via cpu.run()'s tight
  loop; a yield_check between 64K-step chunks (driver → check_pause) keeps it
  pausable.  Verified byte-exact (demo replay + snapshot roundtrip green).
- **Note on headless timing:** an outer cpu.run(N) step that dispatches a
  WM_TIMER runs a WHOLE nested sim frame that doesn't count toward N, so a
  head­less "chunk" post-Quick-Game does enormous work — in-game is impractical
  to measure/drive headlessly (reaching it is ~43M + huge frames).  Interactive
  (paced) play is the loop; the owner drives it.
- **Still open (owner's asks):** real resizable/closable/maximizable CHILD windows
  and main-frame resize.  Needs the in-game window styles (couldn't capture
  headlessly — too slow) + likely a separate tkinter Toplevel per captioned child
  (native chrome) OR full non-client modelling.  Get the styles from the owner /
  a winevdm run before building it.

## 2026-07-09 — GUI chrome: native dropdown menu + framed child windows (play.py)
- **Menu bar is a real native dropdown now** (a55a559).  It was a painted, dead
  in-client strip because play.py only built a tkinter menubar from a MENU
  *resource* (gated on wndclass.menu_name); SimAnt builds its menu at runtime
  (CreateMenu/AppendMenu→SetMenu into win.menu_obj).  WindowView now builds the
  native bar from menu_obj (cascades + WM_COMMAND), (re)building in sync() when
  SetMenu lands.  compositor.composite gained `menu_bar=False` so the host drops
  the painted strip; the strip stays for headless screenshots.
- **Framed child windows get a Win3.1 window frame** (4b88b18).  composite()
  paints a raised 3D frame around any child with WS_BORDER/WS_DLGFRAME/WS_CAPTION
  (verified: SELECT-A-GAME dialog now framed, borderless ribbon not).
- **ROOT GAP for the rest — no non-client area modelling.**  The owner's other
  asks (caption TITLE BARS on WS_CAPTION windows, SCROLLBARS on WS_HSCROLL/
  VSCROLL, true frame RESIZE on WS_THICKFRAME) all need client-rect ≠ window-rect
  with insets (caption/border/scrollbar/menu).  Today client==window everywhere
  (CreateWindow/GetClientRect/GetWindowRect/ClientToScreen/compositor all assume
  it), which is also why the frame above overlays the outer ~2px of client.
  Modelling non-client is the right next step but foundational + touches the
  click-coordinate path we just got working — do it WITH interactive testing,
  not blind.  SimAnt child styles seen: ribbon/root = plain WS_CHILD (borderless,
  correct); dialogs = WS_CHILD|WS_DLGFRAME (now framed).

## 2026-07-09 — SimAnt INTERACTIVE: clicks work, sim-tick timer runs, Quick Game plays in play.py
- **Clicks now register in play.py.** The WAP steers by POLLING GetCursorPos +
  GetKeyState(VK_LBUTTON), which our pump never fed from mouse messages (only
  keyboard).  `Win16System._note_input` now derives cursor_pos (client→screen via
  the parent chain) + button VKs from WM_LBUTTON*/mouse messages, on BOTH
  GetMessage and PeekMessage(PM_REMOVE).  The interactive driver exposes an
  `input_drainer` so a PeekMessage busy-poll (the menus never call GetMessage)
  sees freshly-posted input.  play.py `_on_mouse` subtracts the presentation
  menu-bar strip so coords match the game's client space.
- **SetTimer TimerProc = the ~59fps sim tick (SetTimer(0x118, 0, 17, 0100:2440
  MYTIMERFUNC)).** Was NotImplementedError ("crash on cold start" once in-game).
  Now the proc is stored; its WM_TIMER carries the proc in lParam; DispatchMessage
  calls TimerProc(hwnd,WM_TIMER,id,dwTime), NOT the wndproc (whose WM_TIMER hangs).
- **WM_TIMER discoverable by PeekMessage** — the tick paces frames by spinning on
  `PeekMessage(..,WM_TIMER,WM_TIMER,PM_REMOVE)`; we only synthesized timers in
  GetMessage, so that spin was infinite (→ callback watchdog).  peek_message now
  returns a due WM_TIMER (clock = max(message clock, instruction floor)).
  INSTR_PER_MS moved to system.py (shared by GetTickCount + the timer clock).
- **USER.30 WindowFromPoint** — deepest visible window containing a screen point;
  the click hit-test the main wndproc calls after ClientToScreen.
- **PERF FOLLOW-UP:** in-game frames run inside TimerProc → call_far's per-step
  loop (checks the return sentinel each step, ~1.5-2× slower than cpu.run), so
  gameplay is slower than boot.  Worth speeding call_far (compare a packed int
  instead of building a (cs,ip) tuple each step; or a sentinel-hook + cpu.run).
- Commit 43695f9.  Default gate 47 green; demo-replay determinism + snapshot
  roundtrip green (message-derived input state stays deterministic).

## 2026-07-08 — MILESTONE: SimAnt reaches IN-GAME (Quick Game) + snapshot-anywhere
- **In-game reached.** Start -> "Select a Game" -> Quick Game now renders live
  gameplay: the nest view with a dug ant tunnel, the surface panel, the caste
  slider ("Soldiers 40%"), live palette readouts.  ~44.8M instructions in.
- **The click recipe** (important — WAP polls the mouse, it does NOT use the
  WM_LBUTTONDOWN lParam): set `services['cursor_pos']` to the object's SCREEN
  coords and hold VK_LBUTTON (`services['async_keys']={0x01}`) across a few
  frames, then release + post WM_LBUTTONUP.  Quick Game = screen (337,186)
  (dialog 0x13c at frame (138,116) + object center (199,70)).  Open Select-a-Game
  first by clicking body window 0x118 at (250,150).  Driver: scratchpad
  `to_ingame.py`.
- **API frontier closed** (each ID confirmed against winevdm .spec): USER.156 was
  mis-registered GetSubMenu -> it is **GetSystemMenu** (GetSubMenu=159); added
  RemoveMenu(412)/DeleteMenu(413)/GetMenuItemCount(263), SetWindowText(37),
  GetScrollPos(63), ScrollWindow(61), EqualRect(244), InvalidateRgn(126)/
  ValidateRgn(128); GDI SaveDC(30)/RestoreDC(39)/IntersectClipRect(22); INT 2Fh
  serviced as unhandled-multiplex.  dos_re gained **PUSHA/POPA** (0x60/0x61).
- **Next frontier: SetTimer-with-TimerProc** (the sim-tick callback) — needed for
  the ant simulation to animate.  Requires calling a VM callback on each timer.
- **Snapshot anywhere** (owner ask): F9 used to time out on the menus/in-game
  because it only parked at a GetMessage boundary, but those loops busy-poll
  PeekMessage.  The vmsnap machinery already round-trips from ANY instruction
  boundary (proven bit-exact in-game: digest+instr+CS:IP match after restore);
  only the interactive PAUSE was the limit.  Fix: `InteractiveDriver.check_pause()`
  called between 4096-instr chunks parks the CPU thread at an instruction
  boundary too.  Modal DialogBox/MessageBox stay inspection-only (nested Python
  loop).  Commits: dos_re `c8f5cf8`, win16_re `79e0655` + `4a7f882`.

## 2026-07-08 — Performance: SimAnt is spin-bound, and a byte-exact +27% interpreter win
- **Window sizing** (owner ask): SimAnt is resolution-adaptive — it creates the
  AntRoot frame, calls `ShowWindow(SW_SHOWMAXIMIZED)`, then sizes RibbonWindow +
  the root panel from the MAXIMIZED client rect.  We ignored the maximize (only
  flipped visibility), so children were laid out to the 627-wide create rect, not
  the full 640-wide screen — why it looked smaller than otvdm (which mirrors the
  host desktop, hence its huge maximize).  Fixed: ShowWindow now grows a real
  top-level frame to SM_CXSCREEN×SM_CYSCREEN and re-fires WM_SIZE.  Host-window
  drag-resize (tkinter → WM_SIZE feedback) is a separate follow-on if wanted.
- **Where the time goes** (profiled, islands ON): NOT computation.  Over 5.8M
  steady-state instructions the game calls GetTickCount **64,890×** while the
  message clock advances **0 ms** — it is SPIN-WAITING on the 18.2 Hz frame timer
  (`_TickCount = GetTickCount()/55`).  The profiler's "hot routines" (`_win_Events`,
  `_win_IsWinOpen`, the 47xx cluster) are the pump/pacing spin.  `__aFuldiv` (the
  one pure math leaf) is already an island; there is **no clean pure-compute island
  left** to lift.  The one big game-code lever is the frame-pacing spin itself —
  which the bytecopy-island comment already flagged as deliberately left alone
  (accelerating it shifts the RNG-seeded worldgen).  Owner chose the safe lever:
- **Speed up the interpreter** (dos_re `fa7b97d`): cProfile showed ~all time in the
  CPU core (`execute_opcode`/`step`/`fetch8`/memory), only ~1.7% in our hooks.
  Four trace-off-hot-path changes, **byte-exact** (SimAnt DGROUP+regs at 9.84M
  instrs SHA-256-identical before/after; dos_re 118 + SimAnt 45 green): gate the
  debug disassembly f-strings on `trace_enabled` (never built in gameplay, ~+20%),
  inline fetch8's selector fast-path, hoist the hottest opcodes (Jcc/XCHG/MOV/INC-
  DEC) to the front of the if-ladder, and skip the hook-key tuple alloc when no
  hooks.  **249K → 317K instr/s (+27%)**, helping the spin AND all real work with
  zero worldgen risk.  Method note: a state-digest gate (hash DGROUP+regs at a
  fixed instr count) made the ladder reorder safe to verify — any slip => mismatch.

## 2026-07-08 — SOLVED: logo, ribbon buttons AND the SELECT-A-GAME dialog — two VM bugs, winevdm +relay as differential oracle
- **The winevdm oracle went from source-reading to EXECUTION.**  otvdm v0.9.0 runs on this
  Win11 box, so `WINEDEBUG=+relay otvdm SIMANTW.EXE` produced a 2.7M-line ground-truth API
  trace of the REAL game.  The trace is now a standing instrument: when an ordinal's
  identity or behaviour is in doubt, diff our call site against winevdm's relay log +
  its `.spec` files.  (Downloaded to scratchpad; the .spec ordinal maps confirmed every
  prior RE guess.)
- **BUG #1 — ordinal misidentification (GDI.181).**  We had 181 as GetRgnBox (which WRITES
  the region bbox into the caller's lpRect).  winevdm's `gdi.exe16.spec` + relay trace:
  **181 = RectInRegionOld → RectInRegion16**, a READ-ONLY hit-test; real GetRgnBox is
  GDI.134.  The bogus WRITE stamped the update-region box (0,0,W,H) over each WAP object's
  position rect every paint — SimAnt's ribbon buttons piled at (0,0); the logo's bottom
  half lost its +176 offset.  Fixed: 181 = real RectInRegion (reads the rect, returns
  intersect 0/1, never writes); added 134 = GetRgnBox alongside.  Title logo (full ant +
  complete SIMANT wordmark + all ribbon buttons) now pixel-correct.
- **BUG #2 — unsigned dest origin in SetDIBitsToDevice (GDI.443).**  Fixing 181 turned the
  SELECT-A-GAME dialog WHITE.  Not a region-cull (RectInRegion returned 1 for every dialog
  object; all 33 band blits fired) — the blits' DEST X was 0xFFFF.  The dialog paints at a
  client origin of (-1,-1); the 181-write had been silently clobbering that -1 to 0.  With
  the correct read-only 181, the real -1 reached GDI.443, whose handler sign-extended
  cx/cy/xs/ys but NOT the destination origin xd/yd — so -1 read as +65535 and every band
  landed fully off-surface.  Fixed: xd/yd are sign-extended too.  Dialog now renders fully.
- **Method note:** both bugs were latent, masked by a compensating bug.  The A/B that
  cracked it: hold everything else fixed, flip ONLY ordinal 181 between write-box and
  read-only, and diff the resulting blit DEST coords (0 vs 0xFFFF) — the discriminator was
  the coordinate, not the return value or the draw count.  Full SimAnt gate (45) + framework/
  microman (56) green; no other game regressed.

## 2026-07-08 — Real USER update-region semantics (winevdm as the API oracle)
- **Owner suggested mining winevdm (github.com/otya128/winevdm)** — the right call: it
  bundles Wine's 16-bit USER, giving authoritative semantics for exactly the APIs under
  suspicion.  Verified from its `user/window.c`: InvalidateRect16 = RedrawWindow(RDW_
  INVALIDATE [+RDW_ERASE]) — rects ACCUMULATE into an update region; GetUpdateRgn16
  copies that region out; BeginPaint validates (clears) it, erases ONLY when an erase is
  pending, and reports rcPaint = the update box.  **winevdm is now the standing API-
  semantics oracle: when a USER/GDI behaviour is in doubt, read its source, don't guess.**
- **Implemented faithfully** (win16/api/): `Window.update_rect` (accumulated union, client
  coords) + `update_erase` replace the info-destroying bool; InvalidateRect honours its
  lpRect + erase args; GetUpdateRgn copies the real region; BeginPaint erases only when
  asked, writes rcPaint = update box, and validates; every internal full-invalidate goes
  through the new `_invalidate()`.  Full suite green (incl. ppython + microman pixel A/Bs).
- **SimAnt logo/buttons: not yet healed by this** — [RESOLVED in the entry ABOVE this one].
  The "GetRgnBox dozens of times per band" observation was the tell: those calls were
  ordinal 181, which is NOT GetRgnBox — it is read-only RectInRegion.  The whole damage-
  stamp corruption was a single-ordinal misID, plus a masked signed-origin bug in GDI.443.

## 2026-07-08 — ROOT MECHANISM FOUND: WAP damage-stamp destroys object rects (logo + ribbon buttons)
- **One mechanism explains BOTH owner bugs** (logo halves overlapping at y=0 AND the ribbon
  buttons crawled into the top-left corner), exactly as the owner predicted.  Full chain,
  every step traced (not guessed):
  1. WAP object nodes (44 bytes each; rect at +0, visible flags at +0x24) are loaded RAW
     from WINGANT.DAT (one 1923-byte `_lread`) with CORRECT rects — the ribbon buttons are
     x=13/61/97/133, y=21 (a button row); the logo halves y=0..176/176..352.
  2. `_win_Recalc` (seg7:E6E2; WAP window id 0x2200 = the ribbon, per SetProp(278,·,0x2200))
     stamps 0x8000 into every rect (pass 1), then pass 2 RESTORES them correctly.  Fine.
  3. The paint path (fn returning to seg7:BC2B) creates a region, `GetUpdateRgn(hwnd,hrgn)`,
     stores hrgn in DGROUP `[CD84]`, and then — gated on `[CD84] != 0` — for EACH object
     node does `GetRgnBox(hrgn, &node.rect)`: the DAMAGE BOX (0,0,627,73 = full client)
     is written OVER the object's rect, object drawn, next node (~25K instrs apart).
  4. **The restore that must follow never happens** — watched to 7.5M instructions: the
     rects stay = damage box forever.  Objects then draw at rect.x1,y1 = (0,0): buttons
     pile at top-left; the logo bottom half loses its +176.
  5. `[CD84]` is set per paint cycle (seg2:3EE7) and cleared at cycle end (seg2:3F91) —
     the damage path is ACTIVE by design in SimAnt.  **microman (same WAP engine) renders
     correctly because it NEVER enters this path** — GetUpdateRgn/CreateRectRgn/GetRgnBox
     were first implemented today, for SimAnt; the engine gates on their availability.
- **Open question (the actual fix)**: what restores/repositions node rects after the
  damage-stamp on real Windows.  Candidates: seg7 fn 0E99:0F98 (runs only when a draw
  returns 0 — an update-queue helper?), the per-object draw fn (near call ~seg7:B6E0)
  possibly recomputing the rect from sprite strips, or a region API we answer differently
  (our GetUpdateRgn returns the FULL client rect whenever `win.dirty` — one bool — where
  real USER tracks an accumulating region that BeginPaint empties).  Next: statically
  reverse the stamping loop fn + the per-object draw path, and compare the node struct
  use against microman's working flow.
- **Real VM bug found + fixed along the way (dos_re)**: `Memory._notify_write` masked
  every watcher address with `& 0xFFFFF` (real-mode legacy), so write-watch traces of
  selector-space (>1MB) memory silently missed ALL hits — this hid the stamping writes
  for half the investigation.  Mask now applies only when `sel_base is None`; dos_re
  suite 118 green.

## 2026-07-08 — SimAnt logo: two halves overlap — deep WAP investigation (superseded above)
- **Symptom (owner):** the SIMANT title logo shows only its top half; the bottom half
  (legs + "© 1991 MAXIS") is drawn first then covered.  Confirmed via a blit trace of
  window 312: 43 SetDIBitsToDevice bands, `B166 B158 … B0` (bottom, source selector 8557)
  drawn FIRST, then `A160 … A0` (top, 854f) over it — both land in y=0..166.  Rendering
  each source buffer separately confirms 854f=top, 8557=bottom.  The bottom half's Y is
  short by exactly **176** (the top half's height): it should be at y=176..342.
- **Ruled out (each checked, not guessed):**
  - *Decompression* — the _Unpack LZSS island is byte-exact vs the ASM (136/136), so the
    asset bytes are correct.
  - *Huge-pointer / hugeheap mapping* — 854f and 8557 are SEPARATE GlobalAlloc blocks
    (8557 is a standalone 8939-byte block; they map 0x804 apart, not 64K), each rendering
    correctly on its own.  Not one >64K buffer wrapping wrong.
  - *Transparency* — the two halves occupy the SAME y band with different content, so a
    colour-key overlay would still mash them, not stack them.  They MUST be placed at
    different Y.
- **Localised to the WAP sprite-layout.** The logo is a WAP composite (`_win_DrawBitMap`
  seg7:BD5A, from `_ShowIntro`) that recursively draws ~32 child sprites, each passed
  position (0,0) (x=[bp+6], y=[bp+8] from a display-list node's `es:[si+2]`), so a
  sprite's on-screen Y is intrinsic to its own strip data; leaf blit is `0E99:10a2` ->
  the seg2 band-draw (SetDIBitsToDevice).  The bottom sprite's strips carry Y=0..166 where
  they should carry 176..342 — the +176 origin is lost in the WAP page-layout math (it did
  NOT surface as a fresh 0xA6 inside the leaf draw, so it is set further up, when the
  display list / strip Y is built).  Next lead: the WAP display-list construction.  Nothing
  committed — investigation only, tree clean.

## 2026-07-08 — Byte-copy island — load now ~35% faster; tile "blit" was a timing wait
- **Owner asked to island the "tile color blit" + tiles.**  Tracing corrected the profiler's
  (offset-based, cross-segment) symbol labels: the hot 24% at seg2:47xx labelled
  `_XferTileColor`/`_WaitedEnough` is NOT a blit — it is a **GetTickCount frame-pacing
  busy-wait** (`while (!WaitedEnough()) ;`, dividing ticks by 55 via __aFuldiv).  Left
  alone: accelerating it would shift the RNG stream (worldgen is seeded from GetTickCount),
  so it is not a clean lift.
- **The tiles ARE liftable**: seg2:3460 (`_FloorTiles` region) is a compiler-emitted far
  byte-memcpy — SI bytes, huge source ptr (@bp-8/-6) -> huge dest (@bp-12/-10), selector-
  wrapping — copying 960-byte tile rows (~9.5% of load).  New `bytecopy` island does the
  whole run as one linear block move (with an overlapping-forward smear fallback to stay
  exact).  Byte-exact unit gate (`test_bytecopy_island_matches_asm`) covers the real
  960-byte case, a 1-byte edge, dst-before-src, and an overlapping smear vs the ASM.
- **Payoff: ~35% faster to the title** (18.3s -> 11.8s) with all three islands
  (__aFuldiv + _Unpack + bytecopy); 19% from _Unpack alone.  Note this is a pure
  interpreter speedup (skipping slow *interpreted* loops) — a memcpy has nothing to
  "recover" for a native port, so it stays in hooks.py, not recovered/.
- **File I/O is NOT a bottleneck** (owner's other question): measured 0.0% of load —
  `_lread`/DOS-read are already native Python block-copies (`mem.data[lin:lin+n]=chunk`),
  not interpreted ASM.  Only interpreted CPU loops are worth islanding.

## 2026-07-08 — The LZSS decoder is now clean VM-free recovered code
- **The decompressor is lifted out of the hook into pure, VM-less recovered code**
  (`simant/recovered/lzss.py`) — the shape the source port targets: a plain
  `decompress(compressed, out_len) -> bytes` (and a resumable `decode_chunk`) with NO
  cpu / mem / hooks / offsets, behaving exactly like the original C `Unpack`.  It is
  the Okumura LZSS with its fingerprint constants named (N=4096, F=18, WINDOW_START=
  0x0FEE, THRESHOLD=2, space-filled window).
- **The island is now a thin ADAPTER** (`simant/hooks.py`): it reads the routine's
  DGROUP/stack state and drives `lzss.decode_chunk` over **memoryviews straight into VM
  memory** (source, the 4KB window, output) — zero copies — then writes back the ABI exit
  state.  Same byte-exact A/B gate (`test_unpack_island_is_byte_exact_vs_asm`, 136/136),
  same ~20% faster-to-title.  This is the standing "shadow -> verified hook -> pure system"
  progression: the interpreted game and a native port now share ONE decoder.
- **Pure unit tests** (`simant/tests/test_lzss.py`) exercise the recovered function with a
  round-trip encoder and the Okumura invariants — no VM needed, the form a native build
  uses.  Full suite green.

## 2026-07-08 — The _Unpack LZSS island lands — byte-exact, ~18% faster load
- **The asset-decompression bottleneck is now lifted.**  `simant/hooks.py` installs an
  island at seg7:A668 (`_Unpack`) that reimplements the Okumura LZSS decode in Python — a
  faithful 1:1 transliteration of the ASM (setup / literal / match / exit) so it produces
  the identical output, window, and exit state.  A mid-operation resume (entry [B7D4] != 0)
  passes through to the real routine (keeps the delicate two-sided-streaming resume path
  authoritative); every fresh call is fast-pathed.
- **Byte-exact, proven.**  The A/B gate (`test_unpack_island_is_byte_exact_vs_asm`) boots
  SimAnt with and without the island and requires the decompressed output + exit globals
  to match **call for call** — 136/136 identical in dev.  Getting there pinned three exact
  ABI details: the literal path leaves `dl` = the byte (so exit DX = last output byte); the
  `retf` does NO arg cleanup (caller does `add sp,6`); and the routine writes its stack
  frame (locals + pushed di/si/ds), which the island must replicate because SimAnt reads
  the freed scratch.  A full-memory A/B is deliberately NOT the gate: the game seeds
  `rand()` (seg4 `_rand`) from GetTickCount, which is instruction-count-based, so a faster
  load legitimately changes the RNG stream — that downstream divergence is the game's own
  timing sensitivity, not the island.
- **Payoff: ~18% faster to the title screen** (18.0s → 14.8s wall-clock to first title
  paint).  The instruction-count drop is only ~3% but wall-clock gains far exceed it: the
  island swaps thousands of *interpreted* ASM instructions per call for one native Python
  decode.  Further speedup is available by transliterating the resume path too (the ~40%
  of calls that stream mid-match still run the ASM) — logged as the next lift.

## 2026-07-08 — Load bottleneck located: the _Unpack LZSS asset decompressor
- **Owner: "loading is very slow — RE + hook the asset-loading island."**  PC-sampling
  the boot/load phase (`simant.probes.profile` with warmup=0) is unambiguous: **~90% of
  load time is one loop at seg7:A668 `_Unpack`** (the resolver mislabels it `_CenterAnt`
  — the offset-based symbol lookup collides across segments; the real routine has a
  `_Unpack` symbol at its head).  It is the **classic Okumura LZSS decompressor**:
  - 4KB sliding window (`and bx,0FFFh`), window **initialised with spaces (0x20)**, decode
    pointer **r0 = 0x0FEE = N−F = 4096−18** (the LZSS fingerprint), THRESHOLD=2, F=18.
  - Per step: `shr ax,1; test ah,1` pulls the next flag bit; bit=1 → literal (copy a
    source byte to output AND to `window[r+4]`); bit=0 → match (12-bit offset + 4-bit
    length back-reference from the window).  Flag byte reloaded as `c | 0xFF00`.
  - **Resumable/streaming**: state lives in DGROUP globals (`[B7C0]` window seg, `[B7C4:6]`
    src far ptr, `[B7C8]` input len, `[B7CA]` r, `[B7CC]` flag buffer, `[B7CE/D0]` match
    carry, `[B7D4]` mid-match flag); output far ptr + output len come on the stack
    (`[bp+6]`, `[bp+10]`).  Entry seg7:A668 (`push bp;mov bp,sp;sub sp,4;push di;push si`),
    exit `mov [B7C8],ax; pop di; pop bp; retf`.
- **A first-pass textbook Okumura decoder reproduces ~72% of the captured output** — close,
  but NOT byte-exact yet: the exact match offset/length bit-packing and the streaming
  call boundaries still need pinning (a decompressor that is 72% right silently corrupts
  every asset, so it is NOT shipped — the byte-exact bar holds).  **Next: build the island
  with an A/B gate** — run the ORIGINAL `_Unpack` and the Python island over the same
  compressed input and diff the full decompressed output + the DGROUP exit state, byte for
  byte, before trusting it (the microman/`__aFuldiv` island pattern).  Expected payoff:
  the whole load is dominated by this loop, so lifting it should cut load time sharply.

## 2026-07-08 — SimAnt hooking infrastructure + first island (__aFuldiv)
- **SimAnt is now the sole test target.**  `pytest.ini` scopes the default run to
  `simant/tests` + the game-AGNOSTIC framework tests SimAnt relies on (compositor,
  audio, hugeheap, localheap, msgbox).  ppython/microman tests are intentionally not
  collected (run `pytest microman/tests tests` for them); they may break without
  blocking SimAnt.  Default suite **35 green in <1s**.
- **Hooking infrastructure stood up, mirroring microman's** (the standing lifted-island
  method):
  - `simant/probes/profile.py` — PC-sampling profiler.  Buckets the CPU by
    (NE-segment, offset) across SimAnt's SIX code segments and names each hot bucket
    from the symbol file.  `python -m simant.probes.profile`.
  - `simant/probes/symbols.py` — reads the shipped **SIMANTW.SYM** (MAPSYM) to turn any
    `seg:offset` into the nearest routine name (flat nearest-preceding; approximate but
    dense).  This is what named `_StillDown`/`_DialogWaitInit` during USER.186 bring-up.
  - `simant/hooks.py` — signature-verified island registry + `install(machine)`; refuses
    to install on a prologue-byte mismatch.  `simant/runtime.install_hooks` wires it to
    the generic `scripts/games.install_game_hooks('simant', m)` and play.py `--hooks`.
- **First island: `__aFuldiv`** — the profiler's runaway #1 (~14% of steady-state
  samples): the Microsoft C far 32-bit UNSIGNED long-divide runtime helper, called
  constantly for the map/coordinate math.  Lifted to one exact Python `//`.  ABI nailed
  from a live trace (far, callee-cleans: `retf 8`; dividend/divisor on the stack;
  quotient in DX:AX; BX/SI/DI/BP preserved; CX clobbered).  Engages hard — **91,628
  fires over 6M steady-state steps**, game still paints, no crash.
- **The A/B oracle gate** (`simant/tests/test_hooks.py`): runs the ORIGINAL ASM routine
  and the island over 14 input pairs (both code paths) and requires an identical
  register RESULT.  Scoped to the ABI contract (result + preserved regs + `retf` unwind),
  NOT the caller-clobbered CX scratch — on the full-32-bit path the ASM leaves an
  algorithm-internal intermediate in CX that no caller observes and that only the loop
  the island skips could reproduce.  Next islands: `_CenterAnt`, the `__ftol`/`__aFldiv`
  siblings, and the `_XferTileColor`/`_FloorTiles` render loops the profiler ranks next.

## 2026-07-08 — SimAnt reaches its SELECT-A-GAME menu (title dismiss + window enum + 1bpp)
- **SimAnt now boots -> title -> (click) -> the "SELECT A GAME" menu**, fully rendered
  (Tutorial/Quick/Full/Experimental/Load Game icons + CANCEL), ribbon correct throughout,
  ~41M instructions, no gap.  Owner playtest drove past the title and hit new frontiers;
  each resolved from its call site + `SIMANTW.SYM`:
  - **USER window enumeration**: GetTopWindow(229, wrapped by the app's `_MyGetTopWindow`),
    GetNextWindow(230), GetWindow(262) — SimAnt walks a parent's children (close/redraw)
    with GetTopWindow + GW_HWNDNEXT.  Shared `_get_window`/`_z_children` helpers: our
    window list is draw order (last = topmost), so top-to-bottom Z-order is the reverse.
    Pinned by `tests/test_window_enum.py`.
  - **1bpp (monochrome) DIBs** in SetDIBitsToDevice (8 px/byte, MSB = leftmost) — the
    SELECT-A-GAME dialog's mono glyphs/masks.  Joins the existing 4/8bpp paths.
- The owner also reported the ribbon buttons "in the top-left" and the title logo drawing
  half — but the composited render (exactly what play.py shows via `compositor.composite`)
  is correct at every stage checked (ribbon buttons in place, full logo), so this looks
  already-resolved by the window/compositor work or was a transient first-frame/real-time
  artifact; flagged to re-verify in live play.  Next: wire a game-mode pick (Quick Game)
  into the actual simulation screen, then the x87 `fpu.py` the sim needs.

## 2026-07-08 — SimAnt runs its full multi-window UI (title + ribbon), no gaps
- **SimAnt now boots clean through startup into its running main loop and paints
  its "windows within a window" UI** — no API gap, no crash, for 20M+ instructions.
  Driven past the splash by the fail-loud frontier loop; each API identified from its
  call site (args sniffed off the stack, strings read from DGROUP, callers named via
  `SIMANTW.SYM`), not guessed.  Rendered: the **SIMANT title logo** (GenericWindow
  522x352 child) and the **game ribbon** (RibbonWindow 627x73: Yard/Nest/Surface tabs,
  tool buttons, bookmarks 1-7, YELLOW/BLACK/RED colony health bars) composited over the
  AntRoot frame.  APIs added (all game-agnostic, in `win16/`):
  - **USER**: SetWindowPos(232, honours SWP_NOMOVE/NOSIZE/SHOW/HIDE — sizes the child
    panels), IsWindowVisible(49), BringWindowToTop(45), PeekMessage(109, non-blocking
    filtered queue scan via new `Win16System.peek_message` — SimAnt's main loop peeks
    mouse 0x200-0x209 PM_REMOVE), GetUpdateRgn(237, fills a region with the window's
    update area).  USER.186 is an *unconfirmed* 1-word input gate at the head of the
    `_StillDown` helper (over-popping it as 2 words corrupted the return address and
    jumped into zeroed memory — the arg count matters); returns TRUE so the real
    still-down decision is delegated to GetAsyncKeyState (USER.249, already native).
  - **GDI**: CreateRectRgn(64) + GetRgnBox(181) on a new bounding-box `Region` object
    (DeleteObject frees it; non-rect combines would degrade to the bbox).
  - **KERNEL**: GetSystemDirectory(135), GetProfileInt(57)/GetProfileString(58) over
    WIN.INI (absent -> default; SimAnt reads `[SimAnt] autotrack=` at startup).
- The ordinal-neighbourhood self-checks held (confirmed USER.49=IsWindowVisible +
  USER.50=FindWindow anchor 45=BringWindowToTop; USER.249=GetAsyncKeyState anchors the
  key polling).  Full suite still green.  Next: confirm USER.186's true name; drive the
  title/ribbon into the actual game screen (menu picks, the ant map in AntRoot), and the
  x87 `fpu.py` frontier the simulation will need.

## 2026-07-08 — SimAnt boots + paints (the big stress target) + project renamed win16_re
- The repo is now **win16_re** (generic Win16 RE framework, `README.md` added); paths are
  all relative so the rename was transparent.  New `simant/` package (runtime + boot test),
  registered `simant` in `scripts/games.py`.
- **SIMANTW.EXE (Maxis SimAnt) boots through startup and paints its MAXIS splash** — a full
  commercial Win16 app (6 code segs, KEYBOARD+WIN87EM, raw INT 21h I/O, programmatic menus,
  16-colour DIBs).  Brought up by the fail-loud frontier loop; ~1k → 3.36M → running once the
  4bpp blit landed.  APIs/services added (each identified from its call site, not guessed):
  - **loader**: INT 21h now routes to the KERNEL DOS service table (apps call DOS raw).
  - **USER**: FindWindow(50, single-instance guard), Get/Set/RemoveProp(24/25/26, window
    property store on `Window.props`), UpdateWindow(124), FillRect(81), and the **programmatic
    menu builder** — CreateMenu/DestroyMenu/AppendMenu/InsertMenu/GetSubMenu/SetMenu on a new
    `Menu.items`/`MenuItem` model (SimAnt builds menus in code, not from a resource).
  - **GDI**: Escape(38, QUERYESCSUPPORT→0), CreateFont(56, new `Font` object → fixed-cell
    metrics), GetTextExtent(91), AddFontResource(119), UnrealizeObject(150), SetMapperFlags(349),
    and **4bpp (16-colour) DIBs** in SetDIBitsToDevice (nibble-unpack in the vectorized path).
  - **KERNEL**: lstrcat(89)/lstrlen(90), GlobalReAlloc(16, alloc+copy+free), GlobalFlags(22),
    GlobalCompact(25), GlobalLRUNewest/Oldest(163/164), GetFreeSpace(169) — plus huge-heap
    `free_bytes`/`largest_free_block`.
  - **DOS (INT 21h)**: get-drive(19h), create(3Ch), open(3Dh), get/set-attr(43h), IOCTL(44h,
    isatty), get-cwd(47h).
  - System-metrics table filled out (icon/cursor/scroll/dbl-click sizes).
- SimAnt more than doubled the win16 surface; every change is game-agnostic (lives in `win16/`)
  and the fixtures still pass — full suite **50 green**.  Next: drive past the splash into the
  menu/first screen (KEYBOARD imports + x87 `fpu.py` are the likely upcoming frontiers).

## 2026-07-07 — microman package + MessageBox Yes/No + snapshot game-name + 2 more islands
- **MessageBox button sets** (owner: Restart gave only OK, treated as No).  `win16/
  msgbox.py` maps `mtype & 0x0F` to the real button set + IDs (MB_OK/OKCANCEL/
  ABORTRETRYIGNORE/YESNOCANCEL/YESNO/RETRYCANCEL → IDOK..IDNO).  The API returns the
  DEFAULT (affirmative) headless (was always IDOK=1, which the game read as "not Yes");
  play.py's modal renders the actual buttons and reports the chosen ID.  microman's
  Restart is MB_YESNO|ICONQUESTION (0x24) → Yes/No returning 6/7.  Pinned:
  `tests/test_msgbox.py`.
- **microman is now its own package** (mirrors ppython/): `microman/` = `_env`,
  `runtime` (EXE path, winflags, create_machine, install_hooks, GAME_NAME), `hooks`
  (moved from gamehooks/), `recovered/`, `probes/`, `tests/`.  gamehooks/ retired; the
  generic loader is `scripts/games.install_game_hooks(name, machine)` → imports
  `<name>.runtime.install_hooks`.  Every game-specific test moved under
  `microman/tests/`.
- **Snapshots carry the game name** (format v3: `game` field).  `play.py --resume DIR`
  now works WITHOUT `--game` — it reads the game from the snapshot (falls back to
  matching the recorded EXE name for pre-v3 snapshots).  `win16.vmsnap.snapshot_game`.
- **Two more lifted islands** (owner: profile the snapshot, hook the costliest).  Fine
  PC-sampling of gameplay from snap_220905 found two unhooked huge-pointer byte loops
  the earlier fill/copy signatures missed (different frame layout, matched
  STRUCTURALLY now, reading the frame offsets from the code):
  - `wap_byte_fill` (huge-ptr memset, value/dst walk 1 byte/iter) — the hottest idle
    loop; fires ~24k times in the title alone.  **7.1 → 8.6 fps (+21%)** idle.
  - `wap_byte_copy` (huge-ptr memcpy) — the opaque sprite-row draw; ~13k fires under
    input, −26% instructions during action.
  Both verified byte-exact by the A/B pixel gate (now asserts EACH of the 5 island
  families fires).  19 islands total.  Remaining gameplay hot spot: the `6E` sprite
  decoder (per-pixel clip + transparency branches) — not a single slice, the harder
  next target.

## 2026-07-07 — snapshot resume from play.py + SND_MEMORY SFX + islands scan-all
- **Resume a session from a snapshot** (owner asked, to profile gameplay itself):
  `play.py --resume <snap_dir>` boots straight from an F9 snapshot instead of cold.
  Two selector-era fixes were needed: (1) `load_snapshot` re-wires the VM Memory's
  `sel_base`/`sel_min` to the RESTORED huge heap (the pickle copied the dict, so the
  fresh boot's empty map would leave every global selector unmapped → instant
  divergence); (2) the InteractiveDriver seeds its wall-clock epoch from the restored
  `clock_ms`, else every armed timer sits `clock_ms` ms in the future and the game
  looks frozen for ~45 s.  Snapshot format v2 also carries the polled key state
  (`async_keys`).  Gate: `tests/test_microman_snapshot.py` (bit-exact resume, plain
  AND hooked).
- **SFX now audible**: microman plays fire/hit sounds via `sndPlaySound(ptr,
  SND_MEMORY)` — a RIFF/WAV image it builds in a global buffer (NOT a disk file; only
  the looping title music is MICROMAN.WAV).  The SND_MEMORY branch was log-only; now
  `_read_wav_image` copies the blob out by its RIFF size and hands it to the backend.
  SquareWaveBackend separates looping MUSIC (replace-on-new) from one-shot SFX (mix on
  any of 16 channels, decoded-Sound cache so a rapid-fire SFX decodes once, live-ref
  ring so pygame doesn't GC a still-playing one-shot).  Pinned by
  `tests/test_sndplaysound.py`.  Owner sound bug fixed.
- **Islands scan-all**: `gamehooks/microman.py` now signature-scans the code segment
  for every clone of the WAP loop bodies (ascending fill, descending fill, dword copy)
  instead of two hand-picked addresses — 17 clones hooked.  Gameplay from the level-1
  snapshot: 4.1→7.1 fps (the fill loops appear at 8 more sites used by sprite draw).
  Remaining gameplay hot spots (post-hook resample): seg2:6Axx 18%, 6Exx 11%, 72xx
  10% — the WAP sprite compositor's per-pixel plotting; next islands.

## 2026-07-07 — GAME-SIDE HOOKS PROVEN: the WAP lifted islands (per-game, oracle-gated)
- **The dos_re method now works on win16 games.**  New `gamehooks/` package: per-game
  hook modules (`gamehooks/<name>.py`, `install(machine)`), kept OUT of the
  game-agnostic win16 layer; play.py installs them by game name (`--no-hooks` runs
  pure ASM).  Each module verifies code-byte signatures at its hook addresses and
  refuses to install on mismatch.
- `gamehooks/microman.py` lifts the two sampled WAP inner loops as ISLANDS (hook at
  the loop head, do all iterations in one Python slice op, write back the exact final
  register/flag/locals state, jump to the loop exit):
  - `wap_rle_fill` (seg2:8D70→8DB2): RLE run fill, one byte + full selector recompute
    per iteration in ASM → one descending-span slice fill.
  - `wap_huge_copy` (seg2:926C→9299): huge-pointer dword copy (selector+=8 on wrap) →
    one linear slice copy (with forward-overlap propagation preserved).
  Semantics derived from live traces (artifacts/loop_tr.txt); both fire ONLY in the
  WAP page-transition animations (boot LoadPage uses sibling loop copies — the other
  two fill-loop clones at seg2:8CC0/8D2C are future islands if they ever sample hot).
- **The gate** (`tests/test_microman_hooks.py`): a hooked and an unhooked machine run
  the same 20-batch deterministic boot; window pixels must be sha256-IDENTICAL at
  every checkpoint, and the hooked run must use materially fewer instructions.
  Result: pixel-exact, 30.1M→22.1M instructions (-26%), wall 77.5s→58.8s for the
  window covering the first transition.  42 tests green.
- **SimAnt rehearsal note**: the pipeline is now end-to-end — PC-sample (wrap
  CPU.step) → trace the hot loop live (cpu.trace at the loop head) → lift as an
  island → A/B pixel oracle.  Same steps apply to any future game's hot engine.

## 2026-07-07 — perf split VM-side/game-side; WAV out; keyboard fixed; hook targets named
- **Owner asked where the bottleneck is.**  Measured: the game requests a 40ms timer
  (25fps) but received 3.9 ticks/s — 6x slow, and the driver drops missed ticks, so
  game TIME dilates (the "386 feel").  cProfile split the cost:
  - **VM side (fixed, 2.6x)**: SetDIBitsToDevice was 63% — LUT rebuilt with 256 mem.rw
    per blit + per-pixel Python.  Now: LUT cached on (table bytes, palette identity),
    blit fully numpy-vectorized (analytic clip both axes; ~4 array ops per blit).
    1500-step window 1.457s→0.554s.
  - **Game side (the next lever, ~52% of what remains)**: PC-sampling (wrap CPU.step,
    sample CS:IP every 64 instr) found WAP's two inner loops in seg 2 (CS 0852):
    `0852:8D70-8DAF` = 37% — a huge-buffer FILL storing ONE byte per iteration with
    full selector recompute (shl dx,3 / add / mov es / stosb-like) ≈ 25 interpreted
    instr per byte; `0852:9260-929F` = 15% — the classic huge-pointer MEMCPY
    (4 bytes/iter, offset+=4 / jnc / selector+=8).  Both are single memoryview/numpy
    slice ops in our linear memory model → hook the enclosing functions (find entries,
    replace, oracle-verify frame pixels over a demo) — the dos_re method, and the
    rehearsal for the SimAnt endgame.
- **WAV audio**: sndPlaySound now plays through the host (SquareWaveBackend.play_wav
  via pygame.mixer; SND_LOOP honoured; NULL=stop; sound_log stays authoritative;
  SND_MEMORY log-only until proven).  microman's title WAV (32KB) confirmed delivered.
- **Keyboard fixed**: GetAsyncKeyState read services["async_keys"] which NOTHING fed —
  microman steers by POLLING (not WM_KEYDOWN), so arrows were dead.  Key state is now
  derived from the message stream in get_message (demo-replay identical), with the
  real API's bit-0 went-down-since-last-poll latch for taps.

## 2026-07-07 — MICROMAN pixel-correct: the palette chain root-caused (3 fixes)
- The owner's playtest still showed `LoadPage Error = 9` + wrong colours.  A full-API
  ring-buffer trace dumped at the moment the game called MessageBox found the real
  chain (three defects hiding behind one symptom):
  1. **SelectPalette returned 0 on a fresh DC** (`dc.palette is None` → "prev = 0").
     Real GDI has the stock DEFAULT_PALETTE selected, so success never returns 0 —
     WAP treats 0 as failure and aborts LoadPage with error 9, so its page BMPs
     (MICROMAN.PG1/PG2 — plain 8bpp BMP files) never loaded and every page rendered
     from an uninitialised buffer.  Fix: report/accept the stock handle.
  2. **DIB_PAL_COLORS decode**: with pages actually loading, SetDIBitsToDevice gets a
     16-bit WORD-index table into the DC's logical palette (identity 0..255), NOT an
     RGBQUAD table.  The old "RGBQUAD despite fuColorUse=1" pin was an artifact of
     observing blits only while LoadPage was failing.  Both modes now implemented +
     pinned (`test_dib_render.py`: 3 tests incl. fail-loud PAL_COLORS-without-palette).
  3. **GetSystemPaletteEntries returned a grayscale ramp** (stub).  WAP builds its blit
     table by nearest-matching the SYSTEM palette into its logical palette, so the ramp
     collapsed every page to grays.  Now RealizePalette copies the realized logical
     palette into `Win16System.system_palette` (static single-app display model — no
     other app competes for slots) and GetSystemPaletteEntries reports it (R,G,B order).
- Verified against the owner's real-Windows screenshot: the info page (gray bg, magenta
  contact text, yellow "Press SPACE-BAR to Play!", colour photo) and the DEMO playfield
  (green circuit bg, red sprite) match.  `messagebox_log` empty over 19M instr.
- **Instrument lesson**: headless MessageBox only appends to `services["messagebox_log"]`
  — `messagebox_ui` is a WinHelp-only service, so a probe lambda there never fires.
  Every earlier "boxes=0" claim came from that wrong channel; read messagebox_log.

## 2026-07-07 — MICROMAN runs: reaches its message loop + renders (palette/DIB path)
- Pushed the microman fixture from the CreatePalette frontier all the way into its
  running game: implemented the **palette subsystem** (CreatePalette/GetPaletteEntries/
  GetNearestPaletteIndex/GetSystemPaletteEntries/GetSystemPaletteUse + USER
  SelectPalette/RealizePalette; DC.palette field), **SetDIBitsToDevice** (8bpp
  BI_RGB/PAL_COLORS DIB → dest surface via a palette-resolved LUT — microman's core
  renderer), **MMSYSTEM.2 sndPlaySound** (event-logged like SOUND.DRV), and the
  **resource family** (FindResource/LoadResource/LockResource/FreeResource over the
  NE resources into global memory). Result: microman boots → GetMessage loop →
  creates its window (MicroManClass 544x390) → renders the MicroMan title via
  SetDIBitsToDevice (confirmed non-blank screenshot). dos_re unchanged this slice.
- **play.py is now game-agnostic**: `python scripts/play.py --game microman`
  (default ppython). Uses win16.app.create_machine + scripts/games; is_main =
  window-with-a-menu (already generic).
- **Colour fix (owner: it's a 16-colour game, was rendering grayscale):**
  microman's SetDIBitsToDevice passes fuColorUse=DIB_PAL_COLORS but ships an
  **RGBQUAD colour table** (the standard 16-colour VGA palette). Trusting the flag,
  we read WORD indices (garbage like 49152 ≥ 256) and fell back to gray. Fix:
  trust the DATA — treat the table as PAL_COLORS only when the words are valid
  palette indices, else RGBQUAD. Now renders in colour (blue "Micro Man", etc.).
- **Caveats (documented, not hidden):** (1) pure-Python interp is slow — microman
  runs ~10M instructions (~90s) before its first paint; (2) a later frontier is CPU
  opcode **FF /7** (undefined on 8086) reached after the headless pump spins the
  attract loop with no real input — likely a state divergence, not a missing opcode;
  may differ under interactive input. Next things to chase if we push microman
  further; ppython recovery remains the focus.
- Suite: 33 (microman test re-pinned to boots-and-renders: asserts it exercises
  _lopen/GetDeviceCaps/CreatePalette/SetDIBitsToDevice and runs >1.5M instr).

## 2026-07-07 — win16_re: game-agnostic launcher + MICROMAN as a hardening fixture
- Owner reorganized assets into per-game subfolders (assets/PPYTHON, MICROMAN,
  BANGBANG, KYE, SKIFREE) and reframed the project as win16_re: win16/ is the
  framework, ppython is the RE target, other games are test fixtures. Refactored:
  `win16/app.py` (generic create_machine for any NE), `scripts/games.py` (registry),
  `scripts/boot.py` (frontier probe); ppython/runtime.py → thin adapter (path fixed to
  assets/PPYTHON/PYTHON.EXE). CLAUDE.md reframed.
- **MICROMAN bring-up** (fixture): resolved all its new ordinal names (KERNEL/USER/GDI/
  MMSYSTEM, incl. __AHSHIFT/__AHINCR equates); added dos_re CPU **ENTER (0xC8)** frame
  op (committed there w/ test); implemented the KERNEL string/global-mem/_l* file batch
  (lstrcpy, GlobalAlloc/Lock/Unlock/Free/Size, GetWinFlags, GetWindowsDirectory,
  _lopen/_lcreat/_lclose/_lread/_lwrite/_llseek), USER batch (GetDesktopWindow +
  GetDC(NULL)=screen, GetTickCount, GetCursorPos, SetRect, SendMessage,
  GetAsyncKeyState), GDI GetDeviceCaps (VGA-256 profile) + SetMapMode + GetTextMetrics
  generalized to all stock fonts. Result: microman 433 instr → 1.7M instr, deep in its
  own code. These all live in the shared layer → they benefit ppython too.
- ppython unaffected (still boots both windows). Suite: 33 (+microman boot test).

## 2026-07-07 — bitmap menu items (ScreenSculptor ▸ Shape shows real icons)
- Owner: the Shape menu should show shape ICONS, not text names. Confirmed the game
  converts all 16 shape items (ids 3101-3116: PPMOUSE, PPWALL1-10, PPHEAD R/D/L/U,
  PPBALL) to bitmap menu items at boot via `ModifyMenu(MF_BITMAP)`, each pointing at
  the shape's loaded bitmap handle. ModifyMenu now records the handle in
  `Menu.item_bitmaps`; play.py renders those items as `add_checkbutton` with the
  decoded 16x16 bitmap image (so the selected-shape checkmark still works) instead of
  text. Verified all 16 render as image checkbuttons matching the game screenshot
  (PPWALL1 checked). Menu-state sync handles both text (✓ label) and bitmap (var).
- Suite: 32.

## 2026-07-07 — the REAL crash-frame fix: MessageBox must pump WM_PAINT
- The earlier _flush_windows fix was necessary but not sufficient. Root cause found
  by tracing blits: the game draws the crash head **PPHEADX to the OFFSCREEN
  playfield** (last blit before the box, at the head cell advanced into the wall),
  calls InvalidateRect (window goes dirty), then MessageBox — it never blits the
  viewport to the window itself. Real Windows' MessageBox runs a message loop that
  dispatches WM_PAINT to other windows, so the game repaints the crash head from
  its offscreen buffer WHILE the box is up. Ours just blocked. Proven: at the box
  the window is dirty and dispatching one WM_PAINT turns 0→73 center-red pixels (the
  crash head appears, advanced into the wall — matches the owner's screenshot).
- Fix: `Win16System.pump_modal(paint, timers)` dispatches a pending WM_PAINT/timer
  to a window's WndProc. MessageBox (user.py) now runs a real modal loop:
  present a NON-blocking box (play.py `ModalBox`+`MessageBoxView`, custom Win3.1
  box, not tk_messagebox — a native blocking box would freeze the GUI tick and
  hide the repaint) and pump WM_PAINT until the user answers. Dialog engine routed
  through the same pump_modal (paint+timers) for consistency. Paint-only for boxes
  keeps the crash frame frozen behind the box (no re-entrant snake movement).
- snapshot-on-box + F9 preserved; on_close releases parked box loops.
- Suite: 32.

## 2026-07-07 — crash-frame regression fixed + snapshot-on-event
- Owner: the crashed-snake frame stopped showing after the flicker fix. Root cause
  confirmed by instrumenting: the game DOES draw the crash frame before the
  "Collision!" box (surface version 58/114/170, non-blank pixels), but the
  version-gated renderer races — a tick can render the pre-crash frame, then the
  modal blocks before the next tick renders the crash frame. Fix: `_flush_windows`
  force-renders every window right before a MessageBox/dialog blocks (verified it
  flushes exactly versions 58/114). No re-introduction of flicker (only fires at
  modals, not per tick).
- **`--snapshot-on-box TEXT`**: answer to "snapshot right before the crash" — saves
  an inspection snapshot at the matching box (crash frame + memory), digest-verified
  on load. Mid-modal so not resumable; demos give reproducible replay.
- Suite: 32.

## 2026-07-07 — audio stereo fix + dialog fidelity (font base units, icons)
- **Audio crash fixed** (owner traceback, console-first paid off): SDL opened a
  STEREO mixer despite channels=1; a mono 1-D buffer → "Array must be
  2-dimensional". Now read `mixer.get_init()` and column-stack mono→stereo when
  the device is 2ch. Verified both mono and forced-stereo paths.
- **Dialog fidelity**: dialog-unit→pixel scaling now derives base units from the
  actual dialog FONT (avg char width, line height) exactly like Windows
  (x=du*baseX/4, y=du*baseY/8) instead of hardcoded (8,13) — About went 360→270px
  wide (base_x 8→6), matching the Helv-8/MS-Sans-Serif metrics. Every control uses
  that one font; dialog face is Win 3.1 gray (#c0c0c0); SS_CENTER honoured. "Helv"
  maps to MS Sans Serif (its modern descendant).
- **Icons**: `win16/icon.py` decodes GROUP_ICON directories + ICON DIBs (XOR image
  + AND transparency mask) → RGBA. The About/ScreenSculptor SS_ICON statics now
  show the real 32x32 Paulie head (was blank). LoadIcon path can reuse this later.
- Suite: 32 (added 3 icon tests; audio tests from prior slice).

## 2026-07-07 — DIALOG VISIBILITY FIX + PC-speaker-style audio
- **Dialogs were invisible** (owner: High Scores/About/Help "do nothing"): the
  Toplevel was transient to the WITHDRAWN root, so it never mapped — 1x1,
  unmapped, but it grab_set() input = an invisible modal freezing the game. Fixed
  in play.py DialogView: parent/centre over the visible game window, size+position,
  deiconify+lift+focus, grab only once visible. High Scores 658x172, About 360x238,
  verified mapped + closing on OK.
- **Audio**: the game's sound is SOUND.DRV notes (protected-mode Win16 can't touch
  the speaker ports, so no direct PC-speaker I/O — dos_re's port-based speaker model
  doesn't apply; reused only the square-wave idea). `win16/api/sound.py` now decodes
  note value→freq (note 1 = C3, semitone steps) and length+tempo→ms, feeds an
  optional backend. `win16/audio.py` SquareWaveBackend synthesizes square waves via
  pygame+numpy (no device → logged no-op, events still captured — no silent fake).
  Wired into play.py (`--mute` to disable). Captured the real jingle (51 notes,
  tempo 220, 9s) and rendered it to WAV — a proper melody, octave-exact.
- Suite: 29 (added 4 audio tests, device-free).

## 2026-07-07 — RE MACHINERY: demos + snapshots + console-first + clean Exit
- Built the dos_re-style evidence layer for Win16. **Demos** (`win16/demo.py`):
  record/replay the GetMessage stream + dialog events; replay proven bit-exact
  (record interactive session w/ dialog → replay headless → identical digest +
  playfield PNG) and fail-loud on divergence. **Snapshots** (`win16/vmsnap.py`):
  memory+CPU+OS-object-graph, digest-verified roundtrip, taken only at a message
  boundary; F9 in the player (pauses CPU at boundary via `driver.pause_at_boundary`).
  `scripts/replay.py` is the headless replay/evidence tool. Determinism gates added
  (3 tests): demo replay bit-exact, snapshot roundtrip bit-exact, divergence raises.
- **Console-first per the owner + dos_re doctrine**: VM stops print to stderr with
  CS:IP + instr count + traceback + trace tail + API log; window shows only a red
  "see console" banner; MessageBoxes echo to stdout; `--record` announced. This is an
  AI-operated harness — evidence goes to the console, not trapped in a GUI.
- **Exit crash fixed** (owner report "handle 0000 is NoneType, wanted DC"): GDI ops on
  a NULL hdc now return the API's documented failure (not a handle-table KeyError);
  the true fail-loud path (non-zero garbage handle = OUR bug) is preserved. The Exit
  path then needed GetClassInfo/UnregisterClass and DOS INT 21h AH=4Ch (terminate) →
  the app now exits cleanly (HaltExecution → "app exited cleanly", window closes).
- Digest excludes the pump's internal timer_due (unobservable scheduling detail) —
  found via a component-by-component record/replay diff.
- Suite: 25 (added the 3 determinism gates: demo bit-exact, snapshot roundtrip,
  divergence-fails-loud).

## 2026-07-07 — DIALOG ENGINE: the real thing, no stubs (About/High Scores/Options/…)
- Owner: menu items (About, High Scores, Options ▸ Mouse/Screen-set, Help) "did
  nothing" — they were the DialogBox skip-stub. Replaced with a real Win16 dialog
  engine: `win16/dialog.py` parses all 6 DLGTEMPLATEs (Static/Button/Edit/ComboBox/
  GroupBox, dialog-unit→px), `win16/api/dialogs.py` runs the game's own dialog proc
  in a modal loop and implements the dialog API family (Get/SetDlgItemText/Int,
  SendDlgItemMessage for Button BM_/ComboBox CB_/Edit EM_, DlgDirListComboBox for
  the screen-set picker). The interactive player renders real tkinter widgets
  (`DialogView`) laid out from the template; MessageBoxes are real modal boxes;
  WinHelp says "help unavailable" honestly. All 3 complex dialogs verified running
  their procs headless (About→IDOK, High Scores→IDOK, Screen Chooser). The game now
  runs THROUGH game-over + the high-score entry dialog with zero gaps (was the old
  frontier). Dialogs/controls are windows → uniform `geom_px()` resolver for
  GetWindowRect/GetClientRect/MoveWindow/etc.
- Suite: 22 (added dialog parse + engine tests; gameplay gates no longer pin the
  DialogBox frontier since it's implemented).
- Next frontier is now past the whole death/high-score loop — re-probe to find it.

## 2026-07-07 — PLAYER: flicker fixed by change-detection, not a new backend
- Owner reported menu flicker while the game runs. Cause was churn, not tkinter
  itself: the canvas image was rebuilt every 33 ms tick and all 44 menu entries
  were entryconfig'd every tick (reconfiguring an OPEN Windows menu redraws it
  and fights selection). Fix: `Surface.version` (bumped by every mutating GDI
  op) gates in-place canvas updates; menu states are cached and reconfigured
  only when the game changes them. Measured: 0 redraws + 0 menu reconfigs at
  idle; ~9 repaints/s per window in game (the game's own paint rate). A pygame
  presentation backend stays the fallback if tkinter still misbehaves on real
  hardware. Suite: 18 passed.

## 2026-07-07 — PLAYER v2: one real OS window per Win16 window; menu-state faithfulness
- Owner feedback: the menu belonged on the game's own window, and menu clicks died.
  Root causes found: (1) clicking DialogBox-backed items (About/High Scores) killed
  the worker silently; (2) **delivering WM_COMMAND for a GRAYED item is a real
  crash** — Pause while no game runs = idiv-by-zero at seg1:1F72; real USER blocks
  grayed items, so the UI must too. Both fixed: WindowView-per-handle architecture,
  menu on the PYTHON window with live grayed/checked sync (the game actively
  manages it: enables Pause during play, grays Options, checks Sound/attitude/
  shape), modal MessageBox bridge (player sees "Next Screen:"/"Collision!"/"GAME
  OVER!" boxes for real), DialogBox/WinHelp logged-skip stopgaps, red stop banner.
- Verified headless: Pause disabled→enabled by the game, menu-click New starts the
  game, collision box shows, High Scores skips without killing the VM, Pause during
  a game works. Suite: 18 passed (slower now — gameplay tests run their full budget
  since DialogBox no longer raises).
- The DialogBox engine (real dialogs: high scores, about, screen-set picker) is the
  next faithfulness slice; the skip-stub is temporary and loudly logged.

## 2026-07-07 — INTERACTIVE: scripts/play.py — a real controllable window
- Real-time play harness: worker thread runs the CPU; a tkinter/PIL GUI renders
  the windows and forwards live input. `GetMessage` now delegates to an optional
  `message_source` (`Win16System.get_message()`); `win16/interactive.py` paces
  timers to wall-clock time (drops missed ticks, blocks the CPU thread on a
  condition until input/next-timer). `--speed` scales time; `--scale` zooms.
- Faithful input path landed: **TranslateAccelerator** (matches WM_KEYDOWN/WM_CHAR
  against the accel table → WM_COMMAND; F2→New, F3→Sound, F4→Pause, F5→Scores,
  F1→Help, F8→Radar, F10→Exit) and **TranslateMessage** (WM_KEYDOWN→WM_CHAR for
  ASCII VKs). Mouse move/click → WM_MOUSEMOVE/L/RBUTTON in client coords to the
  window under the pointer. Verified: a synthesized VK_F2 WM_KEYDOWN starts a new
  game through the accelerator (deterministic test) and the threaded harness paints
  the playfield + responds to arrow steering.
- Suite: 15 passed (added the F2-accelerator gate).
- **Next unchanged:** DialogBox (high-score/about) is still the frontier — the
  player stops there gracefully; implementing it unlocks full game-over/menus.

## 2026-07-07 — GAMEPLAY: New Game plays itself blind — level, music, collisions, game over
- x87 landed in dos_re (ESC D8-DF subset per static census: 59 FWAIT+ESC sites;
  FILD/FLD/FSTP m32/m64/m80, FADDP/FMULP/FDIV(R)P/FSUB(R)P, FCOM(P)+FNSTSW,
  FLDCW/FSTCW+RC-honouring FISTP, FINIT; doubles-for-80-bit caveat documented).
  KEY CORRECTION: the NE file carries REAL x87 opcodes; OSFIXUPs would convert
  them to emulator INTs on FPU-less machines — we run them natively like Wine
  (which ignores OSFIXUPs) and __WINFLAGS could now honestly advertise a FPU.
- Full observed lifecycle with no input: WM_COMMAND(1050) → OpenSound +
  queue(512) → level loaded (FP layout math) → "Next Screen: Portrait of a
  Python" → playfield blitted (walls/mice/Paulie visible in game0 PNG; the
  radar shows the level IS a python face) → jingle (69 SOUND events) →
  Paulie crashes unsteered: "Collision!" ×3 → "GAME OVER!" → **frontier:
  USER.87:DialogBox (high-score dialog) — the next slice** (dialog resources,
  MakeProcInstance done as identity, dialog proc callbacks).
- StretchBlt = nearest-neighbour (COLORONCOLOR); GDI default BLACKONWHITE
  caveat noted in code — check against owner playtest evidence later.
- MessageBox auto-returns IDOK and logs. Suite: 14 passed (~22 s).
- **Next:** DialogBox + dialog procs → keyboard input (WM_KEYDOWN steering,
  accel F-keys) → an interactive/live viewer → then demos + the lockstep
  verifier per the dos_re method (GetMessage is the boundary).
- **Boot probe:** `python -m ppython.probes.boot [max_steps]` — runs from the NE entry
  point, prints the stop reason (the frontier), last trace lines, and the API call log.
- **NE inspection:** `win16/ne.py` parses everything (segments, relocs, entry table,
  resources); `NEExecutable.find_resources("BITMAP")` etc.
- **API surface:** `win16/api/core.py` `ApiRegistry` — register handlers with
  `@api.register(mod, ordinal, args="word str long", ret="word|long|void")`;
  unregistered imports fail loud (`Win16ApiGap`) naming MODULE.ord:Name + call site.

## 2026-07-07 — THE GAME RUNS: full boot → intro → idle loop, Paulie-O-Meter renders
- **PYTHON.EXE now runs indefinitely in the VM with zero gaps** (5M+ steps): crt0 →
  WinMain → WM_CREATE (level file read via OpenFile+DOS handle calls, 26 LoadBitmaps,
  1344×960 playfield + 168×120 radar offscreen buffers, timers 140/250/4000 ms) →
  intro window (4 s timer) → DestroyWindow → the idle message loop with WM_TIMER +
  WM_PAINT flowing. `python -m ppython.probes.screenshot` dumps window PNGs:
  the Paulie-O-Meter shows SCORE/LIVES/BONUS/LEVEL/MICE TO GO/SCREEN SET in colour.
  Main window black = correct (no game started; needs menu WM_COMMAND input).
- **The frame boundary is `GetMessage`** (the Win16 analogue of overkill's 1010:9B2E):
  `Win16System.next_message()` is the deterministic pump — posted msgs > WM_PAINT
  (dirty windows) > WM_TIMER (virtual clock jumps to earliest due timer). Timers:
  id2 @140ms = the gameplay tick, id1 @250ms, id3 @4000ms (intro).
- USER/GDI object model landed (`win16/api/objects.py`): HandleTable (recycling —
  DC churn exhausted 16 bits once), WndClass/Window/DC/Bitmap/Surface (RGB,
  3B/px)/Menu/AccelTable; `win16/callback.py` `call_far` = nested-interpreter
  callbacks INTO VM code (WndProc); WM_CREATE/SIZE/MOVE/DESTROY/PAINT/TIMER live.
- GDI: BitBlt (SRCCOPY/AND/PAINT/INVERT + BLACK/WHITENESS), PatBlt, text pipeline
  (SetBkMode/SetTextColor/GetTextMetrics 8×13 fixed + TextOut over the embedded
  public-domain font8x8 — presentation-only approximation), CreateCompatibleDC/
  Bitmap with real GDI default-object semantics (first SelectObject returns the
  default 1×1 bitmap handle, DCs pre-seed stock brush/pen/font — the game VERIFIES
  SelectObject returns, an error path caught this).
- **NAMETABLE (resource type 15) decoded** — the game loads bitmaps by NAME
  (PPINTRO, PPWALL1..10, PPBODY, PPHEAD*, PPICON1-4, KBCURSOR); the map lives in
  `NEExecutable.resource_name_map`, consumed by `lookup_resource`. All 26
  LoadBitmaps resolve (a spy probe caught them all returning 0 before this).
- wsprintf = CDECL varargs (raw handler + Win16 %-format engine). Two real bugs
  fixed: GDI draws must NOT dirty windows (WM_PAINT storm), handle recycling.
- dos_re framework grew (separate commits there): LEAVE (0xC9), CWD (0x99),
  three-operand IMUL (0x69/0x6B) — each with focused tests, 111 passed.
- Suite here: 13 passed (boot-to-idle gate: both windows alive, timers armed,
  meter has rendered pixels, 26 bitmaps resolved).
- **Next:** input driver (post WM_COMMAND "new game" + WM_KEYDOWN steering) →
  playfield renders → then the demo/lockstep machinery per the dos_re method.

## 2026-07-07 — the MSC C startup chain is complete; frontier is inside WinMain
- Implemented, one observed call at a time (each verified in the boot trace):
  `InitTask` (full register contract: AX=1 BX=81 CX=stack DX=nCmdShow SI=hPrev
  DI=hInst ES=PSP; instance-data stack words in DGROUP), `WaitEvent`,
  `GetVersion` (0x05000A03), `DOS3Call` AH=30h/35h/25h (version + Python-side
  interrupt-vector table), `InitApp`, `__fpMath` BX=0/2/3 (install/deinstall/
  set-error-handler — handler seg1:8310 recorded), `LockSegment`/`UnlockSegment`
  (identity in the flat mapping), `LocalAlloc`/`LocalFree`/`LocalSize` over a real
  first-fit DGROUP heap allocator (`win16/api/localheap.py`),
  `GetModuleFileName` (virtual DOS path C:\PYTHON.EXE), `GetDOSEnvironment`
  (PATH= block + WORD 1 + exe path).
- **545 instructions of crt0 run clean; WinMain = seg1:5EB0** (near-called from
  the seg1:0033 thunk). Frontier: USER.173:LoadCursor from seg1:5EF9 — the app's
  window-class setup. Next: the USER windowing model (class/window objects,
  message queue, WndProc far-callbacks into VM code), then CreateWindow →
  message loop → first paintable frame.
- Suite: 13 passed.

## 2026-07-07 — bring-up: NE loader boots PYTHON.EXE to the first API frontier
- Target identified: **Paulie Python 1.0** (Way Out West-ware), Win 3.x NE app.
  2 segments (CODE 0x8C91 @seg1, DATA/DGROUP 0x5940 @seg2, stack 0x1400 heap 0x1000),
  entry seg1:61EA, 105 unique imports by ordinal from KERNEL/USER/GDI/SOUND/win87em,
  25 DIB bitmap resources, 1 menu, 6 dialogs, 1 accel table. Level data in
  WAYOUT0..7.PPS (10080 bytes each), settings/scores in WAYOUT.SET.
- **Architecture decided:** dos_re VM (8086 core, hooks, snapshots) + new game-agnostic
  `win16/` layer: NE parser + loader (real-mode-style flat segment mapping; selector ==
  paragraph base), import thunk segment 0x0060 with one hooked slot per (module,
  ordinal) — **the Windows OS itself is the first Python hook layer**. The game's own
  code runs 100% interpreted.
- **FP model:** OSFIXUP relocations (82 sites) deliberately unapplied → the CD 34..3D
  (INT 34h–3Dh) win87em emulator forms stay live; `__WINFLAGS` equate = 0x0013
  (PMODE|CPU286|STANDARD, **no WF_80x87**). INT 34h–3Dh will be serviced in Python.
- **Boot evidence:** entry runs `xor bp,bp; push bp; call far KERNEL.91:InitTask` —
  the classic MSC Win16 C startup — and fails loud at the InitTask thunk. Relocations
  verified: all 100+ far-call import sites point into the thunk segment; internal
  SEGMENT16/OFFSET16 fixups + the equate applied; chained fixups handled.
- Suite: 10 passed. Next: implement the startup API chain (InitTask → __fpMath init →
  InitApp → WinMain) one observed call at a time.
