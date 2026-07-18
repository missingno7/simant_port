# SimAnt — run status (newest on top)

## 2026-07-18 (cont.247) — MANUAL DIRECT-COMPOSE: the unified override graph (generic dos_re seam + carrier-free shim) — 166 hand-recovered bodies compose as DIRECT CPUless overrides, composed 402→571; the override graph is BLOCKED on virtual-time, pinned gate untouched and green
- **The architecture landed: one graph, address-keyed, `implementation =
  overrides.get(addr, generated[addr])`.**  Until now the 309 hand-recovered
  bodies were reachable only through `scripts/adaptgen.py`'s **CPU-carrier**
  adapter (`def adapter(cpu):` reading `cpu.s`/the stack), so they lived in a
  separate `graph_routed` world and every caller of a manual function refused
  `contains-call` — the manual boundary blocked composition outright.  An
  override is now a first-class composable CPUless callee at the SAME address as
  its generated twin.
- **(1) The GENERIC half, in dos_re** (branch `cpuless-manual-override` off
  origin/main 6825851, commit **ed02917**).  `cpuless_promote --overrides @FILE`
  takes per-address contracts ({name, inputs, outputs, ret_kind, ret_pop,
  sp_delta, needs_plat, df_livein, flags_livein, exit_flags}) and **SEEDS** them
  into the callee-contract maps before the fixpoint (near, plus far when
  ret_kind is far), so callers compose an override exactly like a generated
  callee; the key is marked done so the promoter never emits a PARALLEL body
  ([[manual-recovery-is-authoritative]]).  A balanced near-return override joins
  the dynamic-dispatch registry under the same rule as a promoted function; a
  far/ret-pop/sp-varying one is reported static-call-reachable-only.  New
  `emit_override_adapter` emits the identity-preserving CPU-ABI adapter for the
  function's lifted slot — the same bridge `emit_adapter` builds, but driven by
  the SUPPLIED contract instead of an abi scan (the override replaces a body the
  strict emitter could not generate).  **dos_re learns nothing about SimAnt**:
  the marshalling (which views, the `[bp+N]` map) stays entirely the consumer's.
  Differential regression `test_cpuless_override` (3 cases): a caller composing a
  seeded override diffed byte-for-byte (whole register file + stack memory)
  against the interpreter over the identical bytes; the emitted adapter run on a
  real CPU carrier pinning the far RET frame pop; the body name pinned as the
  single running implementation.
- **(2) The SimAnt half: the carrier-free marshalling shim
  (`scripts/overridegen.py`).**  The CPUless-idiom analogue of adaptgen, sharing
  its **exact** contract classifier (`adaptgen.classify` over
  `recovered_map.json` + the IR + `adapter_facts.json`) so the two flavors can
  never disagree on WHICH entries are mechanically closed.  Instead of a cpu
  adapter it emits a body obeying the dos_re CPUless body ABI —
  `func_CCCC_IIII(mem, *, ds, ss, sp) -> (outputs, compat)` — that (a) reads each
  `[bp+N]` scalar arg off the CPUless caller's stack (`mem.rw(ss, sp+N-2)`: at
  CPUless callee entry `ss:sp` points at the return frame, EXACTLY as at the
  historical hook entry — the same arithmetic adaptgen proved against the ASM
  oracle), (b) binds the dgroup/simant_data_group/pack views over that SAME mem
  image (`SelectorBackend`, duck-typed, VM-free), (c) calls the authoritative
  body, (d) returns AX / DX:AX in the CPUless output dict, (e) leaves the
  ret/stack effect to the composing caller.  It touches **no `cpu` object at
  all**, so `lint_cpuless` PASSES with the wall widened to the whole recovered
  corpus (`simant/native/cpuless` + `simant/recovered` + `simant/bridge` — the
  latter two verified CPU-free here rather than assumed): **586 modules, PASS**.
- **Coverage: composed 402 → 571 (+169) = 405 generated + 166 direct overrides.**
  Of the 309 manual entries, **166** have a mechanically closed contract and
  compose carrier-free; **143 stay on the GENERATED body**, each with adaptgen's
  own reason: args-incomplete 96, presentation-effects 48, result-convention 36,
  proven-gated 18, sig-mismatch 13, callback-injected 12, views:table_view 4,
  arg-frame-shape 3, callee-cleans 2, views:view 2, fact-excluded 1.  Registering
  the 166 also unblocks **+3** generated callers that previously refused
  `contains-call` (910→902; the reason simply shifts for the rest — those callers
  hit `leave-without-enter` next, 153→158).  The **gated** entries
  (`_YellowFight`/`_DoTroph`/`_GetRedBestDirs`) are among the 18 proven-gated and
  correctly stay on the generated body, which carries the gate branch natively —
  cont.223 policy (a) unchanged.
- **Binary-wide census: composable-today stays 868 — and that number is the
  honest one.**  All 166 overrides were ALREADY inside the census's
  `manual-cpuless-override` bucket (309), so the total does not move; what
  changed is HOW they compose — carrier-free DIRECT overrides in the unified
  graph instead of carrier-touching adapters in a separate one, which is
  precisely what the CPUless wall measures.  `cpuless_binary_census.py` does not
  yet model the override seam (it cannot distinguish "manual via CPU adapter"
  from "manual composed carrier-free"); teaching it that split is a reported
  follow-up, not silently folded into the headline.
- **THE GATE — the precise obstacle, and why the pin is untouched.**  The
  override graph builds cleanly (571 adapters routed, lint PASS) but it **cannot
  be validated against the pinned cold_nohooks oracle**, for a structural reason,
  not a marshalling bug:
  - An override is **ONE dispatch step** (`cost=1`, the island virtual-time
    convention every hand-recovered body has followed since the first), whereas
    the ASM it replaces took N instructions.  The 402 generated adapters ARE
    virtual-time-exact (per-block `_cost`, `owns_time`), which is why the pin
    holds for them; a semantic body cannot be, because it does not execute the
    ASM's control flow.
  - The demo is **instruction-count-keyed** (`win16/demo.py`: input is delivered
    "exactly when the machine reaches that instruction count").  Measured drift:
    **-16 instructions at cp0, accumulating to -2,211,796 by cp31** — so input
    lands at the wrong game moments, the run desyncs, and only 32 of 40
    checkpoints are reached.  This is exactly [[demos-are-hook-config-specific]]:
    a cross-config replay silently desyncs; it is NOT a state divergence to
    bisect.
  - Evidence that the marshalling itself is sound as far as the gate can see:
    **cp0 mdigest MATCHES the oracle byte-for-byte** (`17c6448d6a3d32a7`, with
    only the -16 count differing) — the register-file/memory exactness concern
    the whole-state digest raises did not materialise before the timeline drift
    took over.  Beyond cp0 the comparison is meaningless (different game
    moments), so it is reported as unproven, not as passing.
  - Therefore **manual direct-compose is OPT-IN** (`--with-overrides`, default
    OFF, pair with `--graph-dir .../graph_cpuless_override`).  The production
    `graph_cpuless` stays generated-only and the **pinned gate is GREEN,
    re-verified after the work: all 39 checkpoints + final MATCH, instr
    199,619,366, mdigest 417cac5cd9aadb8c** — the exact cont.238/240/242/244/245/
    246 pin.  The gate was never weakened; the override graph simply is not
    admissible to it yet.
- **What would unblock it (ranked).**  (1) **A virtual-time contract per
  override** — the honest fix.  An override would declare (or compute) the ASM's
  per-invocation instruction count; straight-line entries admit a static
  constant, branchy ones need a path-dependent cost model, which is the part that
  does not fall out of semantic recovery.  (2) **A re-recorded demo under the
  override config** ([[demos-are-recreatable]] — the owner re-records), giving
  the override graph its own self-consistent byte-exact baseline; this validates
  the override graph but not against the generated graph's pin.  (3) **A
  semantic-boundary clock** (cont.223's dissolution path): key demo input to a
  game-observable boundary instead of raw instruction count, which would make
  every island config comparable.  (2) is cheapest; (3) is the real fix and would
  retire the whole class of hook-config-specific desync.
- **Chain (NOT pushed — owner reviews + pushes dos_re→win16_re→simant_port):**
  dos_re **ed02917** → win16_re **4b16664** (Bump dos_re) → simant_port (this),
  each on branch `cpuless-manual-override` off its main.  Suites green per repo:
  dos_re **704** (+3 override differential), win16_re **341**, simant_port
  **2270** (+5 override marshalling tests).  `simant/recovered/`, v0.1.0
  dist/+tag untouched; generated `simant/native/cpuless/` + `graph_cpuless/` +
  `artifacts/overrides.json` disposable/gitignored.  Watch: stray
  `pynuked_opl3/` untracked gitlink in dos_re — added files explicitly, never
  `-A`.
- **Reproduce.**  `python scripts/cpuless_promote.py` (pinned generated graph,
  402) → the `checkpoints.py --check-field mdigest` gate above.  The override
  graph: `python scripts/cpuless_promote.py --with-overrides --graph-dir
  simant/lifted/graph_cpuless_override` (571 routed; `scripts/overridegen.py
  --dry-run` alone prints the 166/143 split and writes the contracts).
- **Remaining frontier toward a fully-CPUless binary.**  Unchanged in rank except
  that manual direct-compose is now BUILT and gated on virtual time rather than
  on the composition seam: (1) **dispatch-cluster arm-as-alt-entry** (the 6
  evidence-gated tail-dispatch containers + `_UpdateUserButtons`); (2) **the
  override virtual-time contract** above (what makes the +166 admissible to the
  byte-exact gate and materially advances `play_cpuless`); (3) residual **frame
  variants** (frame-pointer-pop-without-save 15, frame-pointer-clobbered 9);
  (4) **far-indirect / platform-far-indirect** (the 35 sound-driver `call far
  [bp-N]` path) + x87 (0 reachable) — still last.

## 2026-07-18 (cont.246) — CPUless CONTROL-FLOW-SHAPE emitter tier (generic dos_re): far retf-N callee-cleanup + shared-epilogue tail dispatch + a composed-plat virtual-time fix — promoted 339→402 (+63), binary composable-today 780→868 (+88), byte-exact
- **Two generic CPUless emitter capabilities + one virtual-time fix, landed on
  dos_re branch `cpuless-control-flow-shape` off origin/main aea1813** (commits
  `c72cb05`, `d2a7c0c`, `fd93960`, `4e15a9e`).  These are the control-flow-shape
  rung the cont.244/245 ranking named next.
- **(1) far retf-N callee-cleanup (`c72cb05`).**  The emitter already modelled
  the NEAR `ret N` arg-pop (`ret_pop`; a caller pops `2+N`); the FAR `retf N`
  (0xCA) variant — the pascal/stdcall convention a Win16/Borland callee uses to
  clean the caller's stacked args — was refused `ret-n-stack-args (retf N needs
  far variant)`.  A far frame is 4 bytes (CS:IP), so the ONLY difference is the
  return-frame size (rides `ret_kind`); the arg pop N is modelled identically.
  `_check_stack_depths` now records `ret N`/`retf N` uniformly, and a composing
  caller pops `4+N` for a far/retf callee.  Differential regression
  `test_cpuless_retf_n` (whole reg-file + stack memory + VIRTUAL-TIME cost, diffed
  against the interpreter over a `push args; call far; retf 4` sequence; fails on
  the old gate; negatives pin `mixed-ret-pop` + plain `retf`).  **Alone: 339→366
  (+27).**
- **(2) shared-epilogue tail dispatch at nonzero frame depth (`d2a7c0c`).**  A
  compiler emits a switch as a jump-table tail dispatch (`jmp cs:[bx*2+table]`,
  each arm ending `leave; ret(f)` — the arm shares the dispatching function's
  frame, restores it, returns through the caller's frame).  The depth prover
  composed the DEPTH-0 tail but refused `tail-dispatch-at-nonzero-depth` whenever
  a frame was still standing at the jmp (the saved bp + enter/sub locals).  That
  IS the framed-switch idiom: the arm's `leave` (mov sp,bp; pop bp) discards
  everything above the frame base and restores sp to entry, so the exit is
  balanced EXACTLY as a fused `leave; ret(f)` — allowed now when the function has
  an ESTABLISHED FRAME POINTER (enter, or push bp; mov bp,sp).  Without a frame
  it still refuses; an UNKNOWN depth still refuses.  Differential
  `test_cpuless_tail_dispatch` (a framed jump-table container exec'd against a
  faithful `_dyn` running the resolved arm through its `leave` but not its ret,
  reg-file + stack diffed vs the interpreter; negatives pin frameless + depth-0).
  **22 of 23 SimAnt tail-dispatch containers promote** (the 1 residual,
  `_UpdateUserButtons` 0100:023A, is a REGISTER-SAVE `push di; push si` shared
  epilogue with NO bp frame — the arm's exact pop is unknowable to a `leave`
  unwind, so it stays refused; the honest frontier).
- **(3) a composed needs-plat callee anchors its plat effects at entry+call
  (`fd93960`) — the byte-exact-gate fix.**  Promoting (1)+(2) reached a caller
  (SimAnt's `_mem_Size` API helper behind `430E:7860`) that INLINES an
  API-calling helper.  A composed callee is handed its `_base` — the ABSOLUTE
  instruction_count at ENTRY, from which it anchors each plat effect
  (plat.farcall/intr/boundary).  The callee is entered AFTER the caller's `call`
  executes, so `_base` must include it: `_base + _cost + count`.  The emitter
  passed `count - 1`, anchoring the callee's plat effect ONE instruction early —
  so the first API-aligned checkpoint inside a composed needs-plat callee drifted
  **-1**.  The STANDALONE farcall path (which sets `_base = instruction_count` at
  entry) never exercised a COMPOSED needs-plat callee, so it stayed latent until
  frame/retf composition reached one.  Found by an instrumented literal-vs-cpuless
  hook-entry differential (the drift originated at `_mem_Size`'s plat.farcall to
  KERNEL.20, ~instr 3.35M).  Differential `test_cpuless_composed_plat_base` (the
  ABSOLUTE virtual time the inner plat.farcall fires, composed vs interpreter;
  fails on the old count-1).
- **SimAnt promote (`scripts/cpuless_promote.py`): 339 → 402 (+63), byte-exact.**
  retf-N+tail-dispatch alone reach 410, but promoting a framed tail-dispatch
  container is UNSOUND when its switch arms are unpromoted `frame-restore-without-
  establish` fragments NOT in the CPUless dispatch registry (a live `jmp` to one
  raises `UnknownDispatchTarget`).  The SimAnt promoter never passed the
  evidence-gate; it now does (`--dyn-evidence artifacts/indirect_sites.json`), so
  `_gate_dyn_evidence` refuses a container whose OBSERVED tail target is not
  dispatchable (`dyn-target-unpromoted`, 6 containers) — gating out exactly the
  unsound promotions.  `lint_cpuless` PASS (405 modules; recovered corpus reaches
  no CPU).
- **Byte-exact gate: MATCH (mandatory `--check-field mdigest`).**  Fresh
  interpreted oracle regenerated under pypy (`artifacts/oracle_fresh2.trace`, 40
  cp) — the true baseline (339) matches it exactly, confirming it is a valid
  oracle.  Candidate = boot-image + graph_cpuless (402 adapters) replay of
  cold_nohooks: **all 39 checkpoints + final MATCH, instr 199,619,366, mdigest
  417cac5cd9aadb8c** — the exact cont.238/240/242/244/245 pin.  (A first pass
  DIVERGED by the -1 above; bisected to the count-1 base anchor and fixed — never
  weakened the gate.)
- **Binary-wide census (View B, `cpuless_binary_census.py`): composable TODAY 780
  → 868 (+88).**  Buckets: auto-cpuless-composable 459→**547**,
  fail-loud-unsupported-shape 449→**340**, blocked-indirect-dispatch 666→**687**.
  composable IN PRINCIPLE stays 1130; max dispatch SCC 20.  The census classifier
  now routes a body-clean `contains-call` residual whose `root_blocker` resolves
  to an unbroken composition cycle (no hard own-body gap) to the dispatch-cluster
  bucket — frame/retf composition widened those clusters, exposing 15 that would
  otherwise land in `unclassified`; the 9-bucket partition is a clean 1904 again
  (0 unclassified).
- **Chain (NOT pushed — owner reviews + pushes dos_re→win16_re→simant_port):**
  dos_re **4e15a9e** → win16_re **1ba7f0a** (Bump dos_re) → simant_port (this).
  Suites green per repo: dos_re **701** (+9: 4 retf-N + 4 tail-dispatch + 1
  composed-plat-base), win16_re **341**, simant_port **2265**.  Watch: stray
  `pynuked_opl3/` untracked gitlink in dos_re — added files explicitly, never
  `-A`.  `simant/recovered/`, v0.1.0 dist/+tag untouched; generated
  `simant/native/cpuless/` + `graph_cpuless/` disposable/gitignored.
- **Remaining highest-leverage frontier toward a fully-CPUless binary.**  (1)
  **dispatch-cluster composition** — the framed-switch ARMS
  (`frame-restore-without-establish` / `tail-dispatch-with-unbalanced-stack`
  fragments) must be registered as CPUless dispatch targets (composed as
  alt-entries of their container) so the 6 evidence-gated tail-dispatch
  containers (+their register-save cousin `_UpdateUserButtons`) promote soundly;
  this is the same atomic-SCC machinery `composable_closure` already has, now
  needing the arm-as-alt-entry seam.  (2) **manual direct-compose** (+136
  observed, reaches the ant sim).  (3) residual **frame variants**
  (frame-pointer-pop-without-save 15, frame-pointer-clobbered 9).  (4)
  **far-indirect / platform-far-indirect** (the 35 sound-driver `call far
  [bp-N]` path) + x87 (0 reachable) — still last.

## 2026-07-18 (cont.245) — CPUless FRAME-SHAPE emitter (generic dos_re): a fused `leave` established by a hand-rolled `push bp; mov bp,sp` — promoted 234→339 (+105), binary composable-today 636→780 (+144), byte-exact
- **The capability (generic dos_re), landed on branch `cpuless-frame-hand-rolled-leave`
  off origin/main 747a253, commit `90028a3`.** `leave` is the one-byte encoding of
  `mov sp,bp; pop bp`, and a compiler freely pairs a hand-rolled `push bp; mov bp,sp`
  prologue with a `leave` epilogue instead of the atomic `enter`. The CPUless frame
  gate refused that whole idiom `leave-without-enter`: `_check_frame_pointer`'s SHALLOW
  establish check accepted only `enter` (0xC8) — even though the DEEP prover
  `_prove_bp_framebase_at_teardowns` already treats `mov bp,sp` as a valid establish
  (the two were inconsistent). The shallow check now accepts the hand-rolled establish
  for a fused leave, but ONLY together with its matching `push bp` (leave's own pop
  consumes a saved word, so a bare `mov bp,sp` with no push is not a balanced frame).
  Pure Borland/MSC/Watcom frame idiom — no game specifics. The depth walker needed NO
  change: a fused leave resets to the entry depth (0) regardless of establish form.
- **Differential regression `test_cpuless_hand_rolled_leave` (dos_re):** the composed
  CPUless body is exec'd and diffed byte-for-byte — WHOLE register file + stack memory
  — against stepping the identical bytes through the interpreter (`CPU8086`), over a
  synthetic `push bp; mov bp,sp; sub sp,4; <locals>; leave; ret`. FAILS on the old gate
  (2 of 5 tests raise `leave-without-enter`, verified via stash). Three negative guards
  pin the soundness: a bare `mov bp,sp` (no push), an alt-entry epilogue fragment
  (`leave; ret`, its frame base in the container), and the unchanged `enter` path.
- **SimAnt promoted: 234 → 339 (+105).** The 86 own hand-rolled-leave functions
  unblock, plus their transitive calls-only callers compose bottom-up (the fixpoint
  reaches in 4 rounds). `leave-without-enter` refusals in the 1527-candidate promote
  census: 184-era → 130 remaining (the 98 alt-entry epilogue fragments — `leave; retf`
  jump-table arms whose frame base lives in the container — plus a few via-callee).
  lint_cpuless **PASS** (342 modules; recovered corpus reaches no CPU).
- **Binary-wide census (View B): composable TODAY 636 → 780 (+144).** Buckets:
  auto-cpuless-composable 320→**459**, fail-loud-unsupported-shape 570→**449** (own
  leave-without-enter 184→122), blocked-indirect-dispatch 684→**666** (−18: some
  via-callee:indirect functions composed once their frame-blocked callees promoted).
  composable IN PRINCIPLE stays 1130 — my fix REALISES 144 of the previously-"planned
  frame emitter" functions into the today-reach (in-principle already assumed it built).
- **Byte-exact gate: MATCH (mandatory `--check-field mdigest`).** Fresh interpreted
  oracle regenerated under pypy (`artifacts/oracle_fresh.trace`, 40 cp) — byte-identical
  to `oracle_cold.trace` (0 mdigest diffs), confirming the emitter-only change leaves
  the interpreter and VMless path untouched. Candidate = boot-image + graph_cpuless (339
  adapters) replay of cold_nohooks: **all 39 checkpoints + final MATCH, instr
  199,619,366, mdigest 417cac5cd9aadb8c** — the exact cont.238/240/242/244 pin. A
  divergence would have been a real emitter bug; there was none.
- **Capability #1 (register/dispatch-indirect) TRIAGED, not the emitter-bundle case the
  census framing assumed.** The near/register-indirect dispatch is ALREADY emitted
  (`_is_dyn`: CALL_IND/JMP_IND reg /2,/4, mod=3 register form included, through the
  `_dyn` runtime registry). The 35 own `indirect-or-far-transfer` blockers are ALL
  FF /3 FAR-indirect calls through function pointers in stack locals (`call far [bp-N]`)
  — the sound-driver dispatch tables (`_myBeginSound`, `_MciMessage`, `_snd_Install`,
  `_myBeginSong`, `_PaintStuff`…), whose targets are win16 API/MCI **Python hooks**, not
  game code. They carry ZERO capture evidence (indirect_sites.json deliberately excludes
  /3 far-indirect-to-thunk sites — the target is a hook, never interpreted), so there is
  no resolved target set to compose through. This is the genuinely-hard
  platform-far-indirect case (a `plat.farcall`-through-a-pointer contract + capture
  inclusion), NOT the "analysis-built, bundle-missing" easy win — reported as the
  frontier per scope discipline, not forced.
- **Chain (NOT pushed — owner reviews + pushes dos_re→win16_re→simant_port):** dos_re
  **90028a3** → win16_re **4b40617** (Bump dos_re) → simant_port (this). Suites green
  per repo: dos_re **703** (+5 hand-rolled-leave), win16_re **341**, simant_port
  **2265**. Watch: stray `pynuked_opl3/` untracked gitlink in dos_re working tree — added
  files explicitly, never `-A`. `simant/recovered/`, v0.1.0 dist/+tag untouched;
  generated `simant/native/cpuless/` + `graph_cpuless/` disposable/gitignored.
- **Next highest-leverage rung toward a fully-CPUless binary.** (1) **control-flow-shape
  emitter** (+~100 in-principle): tail-dispatch-at-nonzero-depth (23 own + 79 via) +
  ret-n-stack-args/retf-N (6 own + 39 via). (2) the residual **frame variants** —
  frame-pointer-pop-without-save (15) / frame-pointer-clobbered (9) ride the deep prover
  on bp-scratch joins; frame-restore-without-establish (11) are alt-entry epilogue
  fragments needing container/dispatch-cluster composition, not a standalone gate fix.
  (3) **manual direct-compose** (+136 observed, reaches the ant sim). (4) far-indirect /
  platform-far-indirect (the 35 sound path) + x87 (0 reachable, deferred).

## 2026-07-17 (cont.244) — the capture↔close FIXPOINT (generic dos_re) + the binary-wide CPUless census: two views, the pin bumped, both cold_nohooks gates byte-exact
- **The generic graph-completeness improvement, landed in dos_re** (branch
  `cpuless-composable-fixpoint` off origin/main aa6162b, commit **e67d060**).
  `walk_closure` measured REACHABILITY; it never answered how much of the
  reachable set is CPUless-**composable**, which is a second, COUPLED fixpoint
  over the callee graph: a caller composes only when every callee it reaches —
  near call, static far call, AND every observed dynamic-dispatch target — is
  itself composable, and the dispatch targets are reached ONLY by following the
  evidence captured at the caller's indirect sites.  New **`composable_closure`**
  (`tools/cpuless_closure.py`, `--fixpoint`): condenses the reachable callee
  graph into SCCs (iterative Tarjan) and promotes a component ALL-OR-NOTHING
  once every edge leaving it lands on a composable target — the message-pump /
  ant-sim dispatch clusters (a naive "compose when all callees compose" pass
  DEADLOCKS on them) close atomically.  Resume-address coverage + the observed
  runtime/static-only split carry over.  Regression test
  `test_cpuless_closure_fixpoint.py` (4 cases): atomic SCC promotion, the
  without-evidence differential (dispatch targets fall OUT of the closure),
  blocked-by-callee tagging, resume coverage, static-only split.  **Convergence
  is by construction** — the SCC condensation is a DAG, one reverse-topo pass IS
  the fixed point (`converged: True`).  dos_re **679** green.
- **The CAPTURE side (port), the actual missing half.**  `entry_probe.py`'s
  docstring promised per-site dispatch evidence but only emitted `executed`.  It
  now also captures **`indirect_sites.json`** — per NEAR-indirect site (`call
  [..]`/2, `jmp [..]`/4) + ISR-chain far `jmp`/5, the resolved runtime target is
  the VERY NEXT interpreted instruction, stashed in a single `pending` slot
  (far-indirect CALLs /3 to API thunks excluded — their target is a Python hook,
  not interpreted).  This is the FIRST runtime dispatch evidence ever captured
  for SimAnt (dispatch entries were only ever derived statically).  Result over
  cold_nohooks: **20 of 59 distinct dispatch sites fired, 82 site→target edges**
  — feeds dos_re's `--dyn-evidence` gate and the fixpoint directly.  SimAnt has
  **ZERO game-vectored INTs** (only platform int 21/2F), so vector-evidence is
  empty — reported, not chased.
- **The pin bumped + BOTH cold_nohooks gates byte-exact (mandatory
  `--check-field mdigest`).**  Chain: dos_re e67d060 → win16_re **430ef7f**
  (341 green) → simant_port (this).  cpuless_promote re-ran against the new pin:
  **still 234 promoted, lint PASS** (aa6162b C-startup-bootstrap + 07726e0
  de-SMC-far-chain don't touch SimAnt's reachable set — consistent with
  cont.243).  Fresh interpreted oracle (`artifacts/oracle_cold.trace`, 40 cp,
  demo ended) vs the strict boot image:
  - **VMless graph: MATCH** — all 39 checkpoints + final, instr 199,619,366,
    **mdigest 417cac5cd9aadb8c** (the exact cont.238/240/242 pin).
  - **CPUless graph_cpuless: MATCH** — identical, same mdigest.
  The changed dos_re files touch only the CPUless emitter/ABI/platform, never
  the VMless literal emitter or the interpreter, so the byte-exact MATCH
  confirms the whole pipeline is sound on the new pin without a redundant VMless
  regen.
- **The SECOND-demo question, settled with evidence.**  cold_nohooks (199.6M,
  clean "demo ended") is the SOLE full-playthrough demo that replays cleanly
  under the boot-image differential config.  `cold_nohooks2`/`cold_nohooks3`
  (the longer cold recordings) both hit **`CallbackOverrun` under the pure
  interpreter oracle itself** (callback 0100:2440 spins >20M steps at 430E:D1C7
  / 275F:0A6F) — demo drift, not clean gates (wait-vs-symptom: the callback
  polls for input the demo timeline no longer delivers; not a lift regression —
  it desyncs under the interpreter with no lifting involved).  The
  snapshot-anchored gameplay demos (`demo_004053` etc.) are hook-config-specific
  ([[demos-are-hook-config-specific]]) and desync under the no-hooks boot-image
  config.  So per the owner's allowance: ONE clean gate, used; both graphs pass
  on it.
- **View A — observed runtime closure (reproduced, `cpuless_wall_gap.json`).**
  cold_nohooks closure = **597** functions; **85 cpuless-promoted + 132
  manual-adapter + 366 literal-lift + 14 blocked (ALL indirect, 0 x87)**.  Rung
  cascade reproduces cont.243 to the function: today 110 → +manual **+136** →
  +frame **+100** → +cflow **+53** → +sp/+boundary +5 → +indirect **+81** → +x87
  **+0** → +dispatch-cluster **+112** (atomic) = **597, wall CLOSES, 0
  not-composable**.  x87 unblocks ZERO reachable.
- **View B — binary-wide CPUless census (NEW, `cpuless_binary_census.json`,
  `scripts/cpuless_binary_census.py`).**  All **1904** discovered functions, a
  9-bucket partition (sum 1904, 0 unclassified):
  ```
    auto-cpuless-composable        320   (234 byte-exact today + 86 bottom-up)
    manual-cpuless-override        309   (authoritative recovered/ bodies)
    native-platform-replacement     20   (pure boundary thunks)
    fail-loud-unsupported-shape    570   (own + via-callee shape; exact reason)
    blocked-indirect-dispatch      684   (26 own far-indirect + transitive)
    proven-runtime-dead              0   (no positive dead-path evidence)
    proven-unreachable               0   (no dead island under the maximal roots)
    likely-data-or-false-entry       1   (_DoInt3, ir-not-liftable)
    unclassified                     0
  ```
  "Not observed" is NOT conflated with dead/unreachable — proven-dead/unreachable
  demand affirmative evidence and honestly stay 0 (the maximal root set — call-
  graph sources ∪ boundary heads ∪ observed — reaches every function; a single
  demo cannot prove deadness).  Composable **TODAY 636 game functions** (strict
  emitter gates), **IN PRINCIPLE 1130** (+ planned frame/control-flow shape
  emitters; x87 & far-indirect still hard).  fail-loud top reasons:
  leave-without-enter 184 (+106 via-callee), tail-dispatch 12 (+47), frame-pop 15
  (+43), ret-n 5 (+36), x87 31 (+16).  Max dispatch SCC = **20** (the ant-sim
  `_DoAntSim` command cluster).
- **ABI metadata (the memoryless bridge): 1904/1904 coverage.**  Per function,
  keyed by address: register inputs/outputs (candidate params/returns), mem
  read/write side-effect flags, max stack use, direct + platform + observed-
  indirect callees, ints, and the manual-recovery ABI (impl/ret/args/views/
  callbacks) for the 309 overrides.  Surfaces exactly what
  `dos_re/lift/cpuless.py` computes; the one KNOWN GAP is memory-RANGE
  granularity (the analyzer records read/write booleans, not byte ranges) —
  flagged as the next ABI-recovery item.
- **Capability ranking for the next rung (dual criteria: observed-closure +
  binary-wide/dos_re-reuse).**
  1. **frame-shape + control-flow-shape emitters** (generic dos_re): observed
     **+153** (100+53); binary-wide unblocks ~**500 of 570** fail-loud
     (leave-without-enter 290, tail 59, frame-pop 58, ret-n 41, frame-clobber
     26…).  Biggest dual lever, and pure emitter frame/control-flow-shape work
     that generalises beyond SimAnt.
  2. **manual direct-compose**: observed **+136** (largest single, reaches the
     ant sim); binary-wide dissolves the manual boundary transitively.
     Port-architectural (adaptgen), less dos_re-generic.
  3. **far-indirect / dispatch composition** (generic dos_re): observed +81 plus
     the **+112** atomic message-pump cluster (the SCC algorithm now EXISTS in
     `composable_closure`; the remaining work is the far-indirect reg-3 bundle in
     the emitter); binary-wide the LARGEST single class at **684**.
     Register-indirect capture + resume-address closure generalise.
  4. **x87** — deferred: **0** observed, 47 binary-wide.  Still last.
- **Suites green per repo.**  dos_re 679 (+4 fixpoint), win16_re 341,
  simant_port 2265 (2 script edits + gitlink + journal; generated
  artifacts gitignored).  v0.1.0 dist/ + tag + `simant/recovered/` untouched.
  Reproduce: `pypy scripts/entry_probe.py cold_nohooks` (observed +
  indirect_sites) → `python scripts/cpuless_binary_census.py` (View B) →
  `python scripts/cpuless_wall_gap.py` (View A) → the two `checkpoints.py
  --check-field mdigest` differentials (VMless + `--lift-dir graph_cpuless`).

## 2026-07-17 (cont.243) — CPUless WALL-GAP measured: the runtime-reachable closure is 597 functions, x87 is DEFINITIVELY NOT in it, and the wall closes with NO new blocked-tier work
- **The measurement, not the capstone.**  cont.242 left the play_cpuless wall
  (#46) blocked on an unknown: how much of the *reachable* closure the 234
  byte-exact promotions cover, and exactly what stands between here and a closed
  wall.  cont.241 flagged `cpuless_closure --observed` couldn't run for lack of a
  runtime trace.  This builds that trace and produces the ACCURATE map — no wall
  is closed, no capability built (per scope).  Two new disposable tools; nothing
  in `simant/recovered/`, dos_re, or win16_re touched.
- **The runtime function-entry probe (task #44), `scripts/entry_probe.py`.**  A
  cold_nohooks replay under the plain INTERPRETER (`create_machine` — no islands,
  no graph, so EVERY guest instruction interprets) with a tight
  `cpu.coverage_telemetry` that tests each executed address against the IR
  function-entry set.  A boot-image/graph run would fire promoted functions as
  HOOKS (never touching that telemetry), so the interpreter is exactly the right
  engine for an execution-reachability probe.  Result: the pinned demo runs to
  **instr 199,619,366** (identical to the cont.238/240 oracle) and **597 of 1904
  functions execute** — the runtime-reachable closure, sharper than the static
  near-call estimate (~472, which under-counts by missing far/indirect edges).
  Output `artifacts/observed.json` is exactly `cpuless_closure --observed`'s input.
- **`cpuless_closure --observed` runs (deliverable #2).**  From the 597 observed
  roots the tool's static near+far walk reaches 1015; runtime frontier **512**,
  static-only 380.  The 512 = **366 literal-lift (reasons match the promote census
  exactly) + 146 "not-promoted"** — and those 146 are precisely the 132 manual +
  14 blocked the tool CANNOT see as CPUless (the mis-key cont.242 predicted:
  adapters use symbolic names, the manual bodies aren't `func_CCCC_IIII` in
  `native/cpuless`).  `scripts/cpuless_wall_gap.py` corrects that seam.
- **THE WALL-GAP MAP (`artifacts/cpuless_wall_gap.json`), reachable closure = 597:**
  - **cpuless-promoted (pure auto body already): 85** — of the 234 total, 85 are
    reachable; the other 149 promotions serve unreached code.
  - **manual-adapter: 132** — hand-recovered pure bodies (`simant/recovered/`)
    reached via a CPU-ABI adapter.  The BODY is CPUless; the WALL gap is the
    adapter layer (it marshals cpu-state), so each must be composed DIRECTLY as a
    CPUless callee.  Not a body to write — a composition seam to build.
  - **literal-lift (the real work-list): 366**, by strict-gate refusal:
    contains-call 309, leave-without-enter 31, frame-pointer-pop-without-save 8,
    frame-restore-without-establish 5, tail-dispatch-at-nonzero-depth 4,
    tail-dispatch-with-unbalanced-stack 2, boundary-or-dispatch-address 2,
    ret-n-stack-args 2, frame-pointer-clobbered 2, sp-as-data 1.  The 309
    contains-call are calls-only bodies that are already gate-clean and compose
    bottom-up as their callees promote (NOT 309 units of work).
  - **blocked: 14 — ALL indirect-or-far-transfer, ZERO x87.**
- **x87 IN THE REACHABLE CLOSURE?  DEFINITIVELY NO (0 functions).**  Of the 31
  x87-blocked functions, **0 execute** in cold_nohooks; 1 (`_AdjustWndMinMax`, the
  boot-wndproc x87 the hypothesis named) is one static near-call hop from an
  observed function but the WM_GETMINMAXINFO path never fires; 4 appear only in
  the deeper static-only reach.  The reachable sound functions (`_myBeginSound`,
  `_myBeginSong`, `_MciMessage`, `_MciOutWave`) ARE in the closure but are blocked
  by **indirect dispatch** (MCI/wave through function pointers), not x87 — the
  digitized decode is integer delta-PCM (cont.237), no x87.  **x87-in-CPUless
  (#42) unblocks ZERO reachable functions and stays deferred out of the closure.**
- **THE RUNG PLAN (bottom-up cascade over the 597, ranked by reachable functions
  unblocked).**  Modeled as a composability fixpoint under the `--observed` wall
  rule (a call whose target never ran = a fail-loud stub, not a blocker):
  ```
    capability added                  composable  marginal
    today (auto, --observed on)              110      +110
    +manual direct-compose                   246      +136   <- biggest lever
    +frame-shape emitter                     346      +100
    +control-flow-shape emitter              399       +53
    +sp-as-data                              400        +1
    +boundary/dispatch entry                 404        +4
    +indirect/dispatch composition           485       +81
    +x87 (deferred)                          485        +0   <- unblocks nothing
    +dispatch-cluster (message-pump)         597      +112   <- atomic recursive
    closure target                           597               cluster
  ```
  (Note: "today" is 110 not 85 because the fixpoint assumes `cpuless_promote
  --observed` is enabled — the dead-call-stub already lifts the standalone base
  85→110; that flag is not yet on in the byte-exact graph.)  The final 112 are a
  single mutually-recursive component (MAINWNDPROC / WINMAIN / MYTIMERFUNC /
  _DoEvent / _DoMouse / _UpdateWindows and their case_* jump-table arms):
  VERIFIED that **0 of the 112 bottom out in a genuinely unclean callee** — they
  are all body-clean and blocked ONLY by each other, so the dos_re promoter's
  atomic dispatch-cluster / self-recursion composition closes the whole remainder.
- **The bottom line: the wall CAN close over the reachable closure with NO new
  blocked-tier (x87) capability.**  Ranked capstone work-list: (1) manual-body
  direct composition (+136, the largest and the one that reaches the ant sim),
  (2) the strict-gate frame/control-flow emitter variants (+154 combined),
  (3) indirect/dispatch composition (+81, incl. the reachable sound path),
  (4) the message-pump dispatch-cluster atomic composition (+112, the recursive
  core).  Plus turning on `cpuless_promote --observed` (dead-call stubbing) for
  the standalone corpus.  x87 is out.
- **Suites green** (simant_port 2265; no source touched — two new `scripts/`
  tools + gitignored artifacts only).  No dos_re change was needed: `--observed`
  consumed the trace as-is (its schema already matches `entry_probe`'s output).
  Reproduce: `pypy scripts/entry_probe.py cold_nohooks --out artifacts/
  observed.json` then `python scripts/cpuless_wall_gap.py` (+ optionally the
  `cpuless_closure --observed` cross-check with the observed set as roots).

## 2026-07-17 (cont.242) — CPUless (M4) THIRD SLICE: plat.farcall — the platform far-call gateway; 234 promoted, byte-exact
- **The seg-0060 frontier is DISSOLVED.** cont.241 stalled with 1203
  calls-only refusals bottoming out in far-calls to the API import-thunk
  segment. New generic dos_re capability **`plat.farcall`**
  (`dos_re/lift/emit_cpuless.py` + `platform.py`, commit dos_re 933cd7c on a
  branch off the upstream tip): a `CALL_FAR` into a declared platform-boundary
  segment emits `plat.farcall(seg, off, regs, argbytes, anchor)` through the
  CPU-free platform seam instead of refusing. Contract (per-slot pascal
  argbytes, reg in/out, clobbers, SP-cleanup, DYNAMIC service-reported cost)
  handed in as DATA — dos_re stays boundary-segment-agnostic. Differential
  regression test: composed body == interpreter byte-for-byte.
- **SimAnt wiring** (`scripts/cpuless_promote.py`): `build_plat_farcalls`
  derives the per-slot argbytes straight off the win16 api registry (the same
  number `ret_far` pops — a win16 fact, nothing game-specific); a raw-register
  API or an unimplemented slot gets NO contract → the far-call REFUSES
  `platform-farcall-contract-unknown` (honest frontier, never a guessed count).
- **Result: 234 CPUless-promoted** (was 166; plat.farcall + the 8 upstream
  cpuless-emitter commits it rebased onto — 3-op imul, cs-access fix, INT 13h,
  runtime-dead stub, resume-address, bp-scratch). CPUless wall `lint_cpuless`
  PASSES (recovered corpus reaches no CPU). Byte-exact gate GREEN — the
  234-adapter graph_cpuless replays cold_nohooks identical to the oracle (all
  39 checkpoints + final, mdigest 417cac5c…). VMless graph re-verified on the
  same pin (boot sha 9ec8beb2, MATCH).
- Chain: dos_re 933cd7c → win16_re f543678 → simant_port (this). Process
  notes: the killed plat.farcall agent's work was ~95% done (1 stale
  source-text test assertion re a fixed→dynamic cost, corrected); rebased over
  active upstream (2 additive param conflicts resolved); a stray `pynuked_opl3`
  gitlink caught+removed before the dos_re push.
- Next toward the **CPUless hard wall** (play_cpuless, #46): the reachable-
  closure frontier + x87-if-reached (#42) + indirect/dispatch, then the runner
  + runtime wall + `lint_cpuless --root`.

## 2026-07-17 (cont.241) — CPUless (M4) SECOND SLICE: the calls-only tier — bottom-up call composition promoted byte-exact (166 total, +10 composed) — and the frontier is now the seg-0060 platform far-call wall
- **The composition mechanism, confirmed.**  dos_re's `cpuless_promote`
  ALREADY composes the calls-only tier: a FIXPOINT over the near/far call DAG
  (tier 4) promotes a caller only once every direct callee already carries a
  CPUless contract, and the emitted caller body imports the promoted callee and
  calls it as a PLAIN PYTHON function — e.g. `func_430e_8e58` opens with
  `from simant.native.cpuless.func_275f_042c import func_275f_042c` and inlines
  `_o, _c = func_275f_042c(mem, _df=..., ax=ax, ...)`, threading the callee's
  outputs dict `_o` and its compat `_c` (exit-flags + `owns_time` cost) back
  into the caller.  Carrier-free composition — no CPU, no graph re-entry.  So
  the port wrapper only had to WIDEN the candidate set: `scripts/
  cpuless_promote.py` grew `--tiers` (default `leaf,calls-only`) and
  `promote_entries` now yields `(⋃tiers) ∩ ¬manual`; the promoter does the
  bottom-up ordering itself.
- **The slice: 166 promoted, +10 over the leaf baseline.**  Candidates
  `(leaf 419 ∪ calls-only 1417) ∩ ¬manual(309) = 1527`; fixpoint reaches in 3
  rounds → **166 promotable**.  Of these **156 are leaf∩¬manual** (the leaf
  baseline MOVED 145→156 purely from the dos_re pin bump 74fe1eb→e93f71e — the
  upstream DAA/bp-frame emitter work lets 11 more leaves pass the strict frame
  gate; orthogonal to this slice) and **+10 are calls-only∩¬manual COMPOSED**
  — the new thing: `0E99:60CE, 275F:0DED, 275F:136C, 275F:37C2, 430E:8E58,
  430E:DC5E, 430E:E4E8, 430E:EB96, 430E:EC0C, 430E:EC8A`.  By the (triage-grade)
  domain classifier **6 of the 10 are CORE simulation**, 3 runtime, 1
  presentation — the composition reaches into the ant sim, not just C-runtime.
- **THE GATE HOLDS, byte-exact.**  Fresh interpreted oracle vs the strict
  boot-image run with all 166 CPUless adapters installed (`checkpoints.py
  --api-aligned --mask-poison artifacts/vmless_boot --boot-image
  artifacts/vmless_boot --lift-dir simant/lifted/graph_cpuless cold_nohooks
  --check-field mdigest`): **all 39 checkpoints AND the final state identical —
  final instr 199,619,366, mdigest 417cac5cd9aadb8c** — the exact pin
  (cont.238/240).  So interprocedural composition (caller inlining a promoted
  callee + `owns_time` accounting) shifts the timeline by ZERO instructions and
  the whole-machine state by ZERO bytes.  The CPUless wall (`lint_cpuless`, 169
  modules): **PASS** — the composed bodies import only sibling recovered modules.
- **THE FRONTIER: seg-0060, the platform far-call wall (biggest lever).**  Of
  the 1527 candidates, **1203 refuse `contains-call`** — a callee lacks a
  CPUless contract.  Categorized against the call graph: **ALL 175 distinct
  not-in-IR call targets live in NE segment 0060** (the import / inter-segment
  thunk table), hit at **1226 call sites** — nearly every non-trivial
  calls-only function far-calls seg 0060 (the API/platform boundary), so it
  cannot compose until the emitter models a PLATFORM FAR-CALL contract
  (a `plat.farcall`, analogue of the leaf slice's `plat.intr`).  Cascade
  measurement (a caller unblocks when all callees compose): seg-0060 composed
  → **+237**; the manual-authoritative boundary → +105; both → +361; +blocked
  tier (x87/indirect) → +433.  The 485 pure-`transitive-contains-call`
  refusals all bottom out in one of these.  The other refusals are the SAME
  leaf strict-gate frame-shape variants as cont.240 (leave-without-enter 107,
  frame-pointer-clobbered 21, frame-restore-without-establish 11, tail-dispatch
  9+2, ret-n-stack-args 3, sp-as-data 3, mixed-return-kinds 1, op-6B-grp3 1).
- **Why the manual boundary is a WALL, not a bug.**  The ~172 calls-only∩manual
  (137 leaf∩manual too) stay excluded — adaptgen owns them
  ([[manual-recovery-is-authoritative]]) and they remain LITERAL lifts in
  graph_cpuless.  They CANNOT compose into the byte-exact gate even in
  principle: the manual corpus deliberately stubs presentation effects and the
  adaptgen adapters do not preserve virtual time (+1 per dispatch), so inlining
  one into a CPUless caller would break both the mdigest and the instruction
  count.  Generating a parallel auto-body for them is forbidden.  So the manual
  track (pure-Python CPUless via adaptgen) and the byte-exact auto-promoted
  track are two fidelity regimes that do not merge — this is architectural, and
  it is the ceiling for calls-only until seg-0060 is modeled.
- **Ant-simulation coverage (triage-grade).**  Of **1113 core functions**:
  **64 core auto-promoted** (byte-exact graph_cpuless) + **284 core
  manual-recovered** = **348 core CPUless by either route ≈ 31.3%**; 765 core
  remain, gated on the seg-0060 far-call wall and x87.  The core sim is
  carrier-free about a third of the way, mostly via the hand-recovered corpus,
  now plus 6 freshly-composed calls-only sim functions in the byte-exact graph.
- **No dos_re change this slice.**  There was no SMALL composition capability
  to fix — the seg-0060 platform far-call is a substantial new emitter
  capability (per-thunk platform contracts), so per the scope rule it is
  REPORTED as the frontier, not attempted.  dos_re/win16_re untouched; the only
  edit is `scripts/cpuless_promote.py` (the `--tiers` widening).  Generated
  bodies (`simant/native/cpuless/`, 166) + routed graph
  (`simant/lifted/graph_cpuless/`) are disposable/gitignored; `simant/
  recovered/`, v0.1.0 dist/+tag untouched.
- **Suites green.**  simant_port **2265**; dos_re **650** / win16_re **341**
  unchanged at the pin (no submodule edit).
- **Note: `cpuless_closure --observed` needs a runtime-executed trace the port
  does not yet emit** (only a STATIC callgraph probe exists,
  `simant/probes/callgraph.py`); coverage above was measured statically over
  the recovery IR instead.  A runtime function-entry probe (feeding
  `--observed`) is a small future tooling item to split the core frontier into
  runtime-reachable vs static-only.
- **Next step.**  The clear lever is the **seg-0060 platform far-call
  composition** capability in the CPUless emitter (a `plat.farcall` contract,
  mirroring `plat.intr`) — it unblocks +237 immediately and is the gateway to
  the deep calls-only sim tier; then x87 (deferred, #42); then Stage 3
  (memoryless / DOS-layout dissolution).  Reproduce: `python
  scripts/cpuless_promote.py` (regenerate 166), save the oracle
  (`checkpoints.py --api-aligned --mask-poison artifacts/vmless_boot
  cold_nohooks --save O.trace`), then differential with `--boot-image
  artifacts/vmless_boot --lift-dir simant/lifted/graph_cpuless ... --check
  O.trace --check-field mdigest`.

## 2026-07-17 (cont.240) — CPUless (M4) FIRST VERTICAL SLICE: the leaf tier promoted byte-exact — 145 recovered bodies + adapters, whole-demo differential GREEN — and it found a real dos_re INT-flags bug
- **The slice, end to end.**  New `scripts/cpuless_promote.py` (the M4 analogue
  of the M2b `adaptgen.py`): reads the promotion census (`artifacts/
  cpuless_census.json`) for the leaf tier and `recovered_map.json` for the
  manual corpus, restricts dos_re's `cpuless_promote` to **leaf∩¬manual** (the
  282 no-manual leaves), and materializes the CPUless-routed graph beside the
  literal one at `simant/lifted/graph_cpuless/`.  Of the 282, **145 promote on
  the strict first-subset pass**; the other 137 refuse with named,
  expected-strict-gate reasons: `leave-without-enter` 95, `sp-as-data` 29,
  `tail-dispatch-at-nonzero-depth` 7, `ret-n-stack-args` 3,
  `tail-dispatch-with-unbalanced-stack` 2, `emitter-unsupported-op-6B-grp3` 1.
  The **137 leaf∩manual are NOT promoted here** — they are already the CPUless
  implementation (hand-recovered in `simant/recovered/`) and adaptgen wraps
  them; the wrapper's `--entries` excludes them so the two toolchains produce
  ONE body per function ([[manual-recovery-is-authoritative]]).
- **The CPU-ABI adapter seam is GENERIC dos_re, and it drops in unchanged.**  A
  dos_re-emitted CPUless adapter (`lifted_CCCC_IIII(cpu, bb=0)` reading the
  inferred live inputs off the carrier, calling the pure recovered body,
  writing the outputs + exit-FLAGS + `owns_time` virtual-time cost back, then
  the historical RET) is a byte-for-byte drop-in for the literal lift it
  replaces — same signature, same RET, same `owns_time`.  So it installs
  through the identical `dos_re.lift.install` machinery; the wrapper just swaps
  each promoted leaf's symbolically-named module for its adapter and remaps the
  `graph_manifest.json` stem (the `dos_re.lift.naming` seam every linked caller
  resolves through).  Recovered bodies land in `simant/native/cpuless/` (a real
  package, import base `simant.native.cpuless`); both dirs are DISPOSABLE
  autogenerated output (gitignored).  `scripts/checkpoints.py` grew a
  `--lift-dir` passthrough so the strict boot-image machine installs the
  CPUless graph — the boot image's poison is graph-independent, so ONE image
  (`artifacts/vmless_boot`) serves either graph.
- **The CPUless wall HOLDS.**  `lint_cpuless` recovered-purity over the 148
  generated modules (145 bodies + `__init__` + `dispatch` + `_dyncall`):
  **PASS** — no path from the recovered corpus reaches `dos_re.cpu`/`cpu386`/
  `simant.lifted`.  The promoted bodies compute over `(mem[, plat], *regs)`;
  they cannot touch the carrier.
- **THE GATE: whole-demo masked differential, byte-exact GREEN.**  Fresh
  interpreted oracle vs the strict boot-image run with all 145 CPUless adapters
  installed (`checkpoints.py --api-aligned --mask-poison artifacts/vmless_boot
  --boot-image artifacts/vmless_boot --lift-dir simant/lifted/graph_cpuless
  cold_nohooks --check-field mdigest`): **all 39 checkpoints AND the final
  state identical — final instr 199,619,366, mdigest 417cac5cd9aadb8c** — the
  exact values the literal VMless graph pins (cont.238).  Virtual time is
  preserved to the instruction: the CPUless adapters shift the timeline by zero.
- **A REAL dos_re CPUless-emitter BUG, found by the gate and fixed upstream
  (suspect-the-lifter-first).**  The first routed run DIVERGED at checkpoint 7
  (~40M) — same instruction count, different mdigest, so a genuine state diff,
  not a timing artifact.  Bisected: instrumented the routed run to the 25
  adapters firing in [33M,41M], then binary-searched the boot-image differential
  (reverting subsets to literal) down to a SINGLE culprit — **275F:003C
  `_profstart` = `mov ax,4503h; int 2Fh; retf`.**  Root cause: **a PLATFORM INT
  did not trigger `flags_livein`.**  The interpreter's INT (and the literal
  lift's `emulate_int`) touches FLAGS only if the handler does; win16's INT 2Fh
  multiplex with no TSR is a no-op IRET that RETURNS FLAGS UNCHANGED (= caller
  live-in).  The CPUless emitter models the INT's flag output via `plat.intr`
  reading its `_flags` input back out — and `flags_livein` was forced for a
  game-vectored INT but NOT for a platform INT, so the body seeded that input
  from the ZERO default and CLOBBERED the caller's preserved flags; a downstream
  branch then read a wrong flag → different memory.  **Fix
  (`dos_re/lift/emit_cpuless.py`, one condition): any `i.kind == INT` forces
  `flags_livein`** (subsumes the game subset).  Regression test
  `tests/test_cpuless_abi.py::test_platform_int_forces_flags_livein` (the exact
  `_profstart` shape; fails on old code, passes on new).  Also fixed a tool bug:
  `tools/cpuless_promote.py` never `mkdir`'d its `--adapter-dir`.  Both on a
  dos_re branch off a2ca7aa (owner pushes the chain).
- **Suites green per repo.**  dos_re **640** (+1 regression), win16_re **341**,
  simant_port **2265**.  No generated artifact committed (recovered bodies,
  routed graph, census, traces all gitignored); v0.1.0 dist/ + tag untouched;
  `simant/recovered/` untouched.
- **Ready to widen.**  The leaf seam + the CPUless wall + the byte-exact gate
  are proven on solid ground.  The blocked-leaf frontier is small strict-gate
  refinements (frame-shape variants, retf-N far bundle); the big remaining
  tiers are the calls-only composition (promote bottom-up as callees promote —
  the adapters compose since a promoted callee's contract feeds its callers)
  and x87 (mirror the VMless `execute_fpu` delegation in the CPUless emitter),
  then `play_cpuless` + `lint_cpuless --root` closure.  The INT-flags fix
  unblocks the 8 other seg-4 `plat.intr` leaves already promoted here and every
  future INT-bearing CPUless function.
- **Reproduce the gate** (pypy): save the oracle
  (`checkpoints.py --api-aligned --mask-poison artifacts/vmless_boot
  cold_nohooks --save O.trace`), regenerate
  (`python scripts/cpuless_promote.py`), then differential
  (`checkpoints.py --api-aligned --mask-poison artifacts/vmless_boot
  --boot-image artifacts/vmless_boot --lift-dir simant/lifted/graph_cpuless
  cold_nohooks --check O.trace --check-field mdigest`).

## 2026-07-17 (cont.239) — CPUless (M4) baseline: the promotion census over the SimAnt corpus
- With dos_re at a2ca7aa (the mature CPUless emitter), ran
  `dos_re/tools/cpuless_census.py` over `artifacts/recovery_ir.json`
  (`artifacts/cpuless_census.json`). Over 1904 functions:
  **419 leaf** (no refusals — the first CPUless emitter targets),
  **1417 calls-only** (promote bottom-up as callees promote — the 8913
  "call-abi-composition" sites are that composition, not a blocker),
  **68 blocked**.
- Blocked frontier (ranked): x87 is the whole story — opcodes 9B (FWAIT,
  168 sites) + DD/D9/DB/DE/DF/DC/D8 (~145 sites) = the same FPU the VMless
  emitter already handles via shared `execute_fpu` delegation; plus
  `indirect-or-far-transfer` (103) and a trivial `opcode-82` decoder gap (1);
  `_DoInt3` the lone ir-not-liftable. **Zero manual-corpus functions are
  blocked** — the hand-recovered gameplay never touches x87.
- Key architectural finding (cross-ref vs `recovered_map.json`): all 309
  hand-recovered functions fall in leaf (137) / calls-only (172) — **and
  they are ALREADY the CPUless implementation** (pure Python on the state
  views, no CPU object). For them CPUless "promotion" = the CPU-ABI adapter
  M2b already generates (166 routed). So the manual corpus is a large CPUless
  head start; the mechanical work is the ~282 leaves with no manual impl.
- Plan (M4, [[dos-re-2-adoption]] + [[manual-recovery-is-authoritative]]):
  (1) leaf vertical slice — promote the ~282 no-manual leaves via
  `cpuless_promote`, route the 137 leaf∩manual through existing impls, gate
  with `lint_cpuless` + the oracle differential; (2) x87 in the CPUless
  emitter (dos_re, mirror the VMless `execute_fpu` delegation); (3)
  calls-only composition bottom-up; (4) `play_cpuless`. The CPU carrier
  leaves the graph; the interpreter stays the oracle.

## 2026-07-17 (cont.238) — the dos_re a2ca7aa bump does NOT regress the VMless graph: the "divergence at checkpoint 0" was a CHECK-FIELD artifact, not a state difference; the whole-demo masked differential is byte-exact GREEN
- **The report was: the ce620ab→a2ca7aa dos_re bump (35 commits, the mature
  CPUless emitter) flipped the whole-demo differential — DIVERGED at checkpoint
  0 (~instr 5,000,059), oracle `153e16d0…` vs lifted `17c6448d…`, "same
  instruction count, so a genuine state difference."  Triaged lifter-first;
  bisect deferred until the divergence was actually localized.  It never
  localized — because there is no divergence.**
- **ROOT CAUSE: the repro compared the WRONG digest field.**  The gate is
  `checkpoints.py --api-aligned --mask-poison … --boot-image …`, and the boot
  image ZEROES the poison partition (the ~331 KB of code emitted as lifted
  functions) while the interpreted oracle keeps the real EXE code bytes there.
  The correct differential compares `mdigest` — the digest with the manifest's
  poison ranges masked — which is exactly the field the WALL RUN and v0.1.0
  always used (cont.233/237).  The repro omitted `--check-field mdigest`, so it
  compared the UNMASKED `digest`, which by construction differs at EVERY
  checkpoint (poisoned code vs real code).  The "153e16d0 vs 17c6448d" is
  literally `oracle.digest` vs `lifted.digest`, and `lifted.digest ==
  oracle.mdigest == 17c6448d` because the boot image already carries zeros where
  the mask zeroes.  It reported checkpoint 0 only because that is the FIRST
  checkpoint; the plain-`digest` comparison mismatches at all 40 and CANNOT
  match on any pin, old or new.
- **THE CORRECT GATE, on a2ca7aa: byte-exact MATCH.**  Fresh interpreted oracle
  (`--mask-poison artifacts/vmless_boot cold_nohooks`) vs the strict boot-image
  run (`--boot-image artifacts/vmless_boot`), `--api-aligned --check-field
  mdigest`: **all 39 checkpoints AND the final state identical** — final instr
  **199,619,366**, mdigest **417cac5cd9aadb8c**.  Per-checkpoint tally: 40/40
  mdigest matches, 0/40 plain-digest matches (the expected poison signature).
- **Stronger than an alignment argument.**  mdigest masks ONLY the code
  partition (2410 declared instruction runs + 2362 code-as-data runs); DGROUP,
  heap, stack and every window surface are UNMASKED — so any emitter behaviour
  change reachable in the demo would surface as a data-state mdigest divergence
  somewhere in 199M instructions.  None does.  And the final instruction count
  **199,619,366 is byte-identical to the old-pin ce620ab differential**
  (cont.237): the a2ca7aa emitter/interpreter changes (xlat CS-override, boundary
  heads as re-entry points, INT re-entry, de-SMC, stuck-loop detection, 80186
  frame ops) do not shift this demo's timeline by a single instruction.
- **Classification: none of emitter-bug / stale-facts / interp-inconsistency —
  no defect.**  The lifter-first triage terminates cleanly: the construct under
  suspicion (the 8 prime-suspect commits) never produces an observable
  difference on cold_nohooks.  No dos_re change, no simant facts change, no
  boundary-head regen needed.  `simant/facts/boundary_heads.txt` and the graph
  regenerated on a2ca7aa (irgen→liftemit→liftlink→adaptgen→boot image: VMless
  wall HOLDS, 0 interp_one, 1903 liftable, poison 331358 bytes/2410 runs
  unchanged) are correct as-is.
- **Green footing for CPUless.**  The a2ca7aa graph is verified byte-exact over
  the whole demo, so the pin-bump chain (dos_re ce620ab→a2ca7aa, win16_re pin,
  simant_port pin) can land on a clean differential.  No artifacts committed
  (graph/boot-image/traces are gitignored); v0.1.0's dist/ and tag untouched.
- **Reproduce the correct gate** (fast oracle+boot-image, all under pypy):
  ```
  pypy scripts/checkpoints.py --api-aligned --mask-poison artifacts/vmless_boot \
       cold_nohooks --save ORACLE.trace
  pypy scripts/checkpoints.py --api-aligned --mask-poison artifacts/vmless_boot \
       --boot-image artifacts/vmless_boot cold_nohooks \
       --check-field mdigest --check ORACLE.trace
  ```
  The `--check-field mdigest` is load-bearing: without it the harness compares
  the unmasked digest and reports a poison-region "divergence" that is not one.

## 2026-07-17 (cont.237) — the digitized sound effects are AUDIBLE: the MMSYSTEM waveOut device, completed on the VIRTUAL clock (win16_re)
- **Owner request: "Could you make sounds working?"  They do now.**  The SFX
  path was silent because `waveOutOpen` was a deliberate stub returning
  MMSYSERR_BADDEVICEID (cont.231 recovered the contract and parked the slice
  for its re-pin ceremony).  This lands the real device.
- **cont.231's contract re-verified from the bytes, not inherited.**  All of
  it held: `_CheckMMWave` (2:77D2) stores the LoadLibrary handle at [8D08]
  BEFORE testing `waveOutGetNumDevs` and returns 0 without clearing it, so
  `_myBeginSound`'s [8D08] gate passes with zero devices — that is why the
  stub saw 59 open attempts.  The PCMWAVEFORMAT at 2:9D72..9D99 is literally
  {1, 1, 0x1000, 0x1000, 1, 8} = **4096 Hz mono 8-bit**, rebuilt identically
  by the fallback's RIFF builder (2:9E6B..9E9E).  `waveOutOpen(&hwo,
  WAVE_MAPPER, &fmt, hwnd@CD78, 0, CALLBACK_WINDOW)`; on success
  `_MciOutWave`, on failure GetProcAddress("sndPlaySound").  Two readings in
  that entry were operand-order slips and are corrected here: 2:9FC6 is
  `add ax,[bp-0x20]` and 2:9C0B is `cmp dx,[123a]` (opcodes 03/3B are
  reg,rm) — so the deadline is `GetTickCount + MMTIME.ms`, and the branch is
  **now vs deadline**: `now > deadline` (the effect FINISHED) tears the device
  down and reopens it for the new one; `now <= deadline` DROPS the new effect
  rather than interrupting.  The 0x3BD poll is a **drain** loop, not a wait.
- **The decode is not ours to write.**  The delta-PCM loop at 2:9660 runs in
  GUEST code (16-byte table, high nibble then low, `running = (running +
  table[n]) & 0xFF` seeded 0x80, two samples per input byte); the game hands
  `waveOutWrite` plain 8-bit unsigned PCM.  So win16_re grew a wave DEVICE and
  no codec — the platform plays back what the program declares and writes.
- **The device (win16_re, generic): waveOutGetNumDevs/Open/PrepareHeader/
  Write/Reset/UnprepareHeader/Close/GetPosition** — exactly the set SIMANTW
  resolves by name, nothing speculative.  WAVEFORMATEX and WAVEHDR are read
  faithfully out of guest memory; a non-PCM tag, an unknown callback type, an
  unknown open flag, a wrong sizeof(WAVEHDR) and an unknown MMTIME unit all
  raise.  Position reports what has PLAYED (elapsed since open/reset, clamped
  to what was queued) — the real contract, and the reason the game preempts
  effects rather than dropping them.
- **DETERMINISTIC COMPLETION — the design constraint.**  New generic
  `Win16System.schedule_message(due_ms, ...)`: a deferred post released into
  msg_queue once `tick_count()` reaches its due time — the same lazy ask-time
  rule as an armed timer, wrap-safe over the 32-bit tick.  A buffer's duration
  is arithmetic (bytes / the program's declared nAvgBytesPerSec, rounded up),
  so MM_WOM_DONE lands at a computed GUEST instant — **never a host-audio
  callback**, which would make replay depend on host scheduling.
  `waveOutReset` cancels the pending completion (scoped by wParam, so one
  device cannot silence another); the WAVEHDR flags and the in-flight list
  settle from the SAME clock at every entry point, so the message and the
  header can never disagree.  Host audio is a pure sink: a test drives the
  identical timeline with no backend, a slow backend and a silent one.
- **Which path the game actually takes: waveOut.**  Over cold_nohooks: 22
  waveOutWrites, **zero sndPlaySound** — the fallback runs only when the
  device is refused, and it is implemented and tested because it is real code
  the game runs on a machine with no wave hardware (`sndPlaySound` also had to
  be registered BY NAME — it was ordinal-only, so GetProcAddress handed the
  fallback NULL and it played nothing).
- **The completion ledger balances**: 22 scheduled = 9 cancelled by a reset
  mid-play + 13 delivered on the virtual clock and consumed by the game's own
  pump; 0 stranded, 0 left in the queue.  Honest nuance: the 0x3BD drain loop
  itself consumed 0 of them — it runs at `_myBeginSound` entry and the main
  GetMessage pump gets there first.  The loop terminates either way (it is a
  drain, not a wait), and the ledger is the real proof.
- **Cross-check against artifacts/sounds_wav/ — and its provenance settled.**
  Every effect reaching the device is byte-for-byte an extracted reference:
  the waveOut buffers are a reference + exactly 6 samples (4/4 distinct
  sounds), and the fallback's RIFFs are **7/7 EXACT** matches — so that corpus
  is the game's own sndPlaySound output, and it is tagged 4096 Hz, agreeing
  with the game's own declaration (cont.231's 11025 Hz note was the old zip).
  Both paths apply the same `(len - 0x10) * 2` (identical bytes at 2:95FB,
  2:9A25, 2:9E17), so the 6 samples are two different length accessors;
  1.5 ms, not pinned down further.
- **A real bug found on the way — SimAnt's, not ours.**  Its RIFF builder
  writes `RIFF size = data chunk + header`, omitting the "WAVE" tag and the
  whole "fmt " chunk: **28 bytes short**.  Windows' MMIO reader finds chunks
  by WALKING, so the file plays whole; our reader trusted the field and
  clipped the tail off every fallback effect.  `riff_image_length()` now walks
  the chunks, and `riff_with_consistent_size()` corrects the FIELD (never a
  sample) before handing an image to a strict host decoder, which clamps
  subchunks to the parent.  Both generic, both tested.
- **The ceremony (the pin moved; the gate did not).**
  - **Whole-demo differential: MATCH** — literal graph vs a fresh interpreted
    oracle, both with sound on, `checkpoints.py --api-aligned` over
    cold_nohooks: all 39 aligned checkpoints AND the final state identical
    (instr 199,619,366, digest bcfaad65addf69ad; was 199,612,996).  Both sides
    shifted identically, which is exactly what proves host audio never reaches
    guest state.
  - **Re-pinned** `test_vmless_walls` 45,102,443 / 50365479… →
    **45,107,077 / f9ad9c8b…** (+4,634 instructions, 9 effects over the
    prefix).  **Attribution proven the cont.228/232 way**: `git stash` of the
    win16_re hunks reproduces the OLD pin EXACTLY (45,102,443 / 50365479…,
    sound_log empty) on this same tree, so the move is this change and nothing
    else.  Parks stay transparent (test_boundary_parks reaches the identical
    45,107,077).
  - **A pickle-compat wall closed while re-pinning**: `artifacts/vmless_boot/
    system.pickle` and every saved snapshot predate the new field, and pickle
    restores `__dict__` verbatim — so the first PeekMessage died with
    AttributeError.  `Win16System.__setstate__` now fills defaults for fields
    a snapshot predates (defaults only, never overriding a saved value): a
    field's default IS the correct value for a state saved before the concept
    existed.  Snapshots outlive the class that wrote them; without this every
    future field addition breaks every crash dump.
- **cold_nohooks does NOT need re-recording to serve as a gate** — it replays
  cleanly end to end (all 4212 events, no divergence) and the differential
  above still matches, which is the property the gate asserts.  But its input
  is instruction-count-keyed and control flow now shifts from ~41M, so what
  the recorded clicks *mean* after that point has drifted by up to ~6.4k
  instructions of guest time; if a future slice needs the demo to represent a
  specific play-through again, re-record it (standing directive: demos are
  recreatable).
- **What the owner hears**: `pypy scripts/play_vmless.py` (PlayApp wires the
  backends; `mute` defaults off) — the ant effects at 4096 Hz mono 8-bit,
  resampled by SDL to the open mixer (verified: a 4096 Hz 8-bit mono image
  decodes to the right duration and a real signal, not silence), mixing over
  the Mapper-filtered GAMETHME MIDI from cont.231.  Effects preempt one
  another rather than overlapping — that is the game's own single-device
  design, straight out of its own deadline arithmetic.
- **Gates**: win16_re 341 passed (289 + 52 new: the device, the deferred-post
  contract incl. the tick wrap, the RIFF walk/normalise, the fail-loud paths,
  pickle compat), simant_port 2265 passed (+7: `simant/tests/test_sfx.py` —
  the declared format read out of seg 2, both paths driven on the strict graph
  against the reference corpus, the game's 28-byte RIFF bug, the ledger).
  Commits: win16_re (the deferred-post primitive; the waveOut device) + the
  port's bump/re-pin/tests/journal.

## 2026-07-17 (cont.236) — the API tripwire tier closed: 20 of 28 imports implemented from their call sites, 4 left failing loud ON EVIDENCE — and the file dialogs, opened for the first time, turned out to be broken three ways (win16_re 3be222a, 4db04b6, 084c717)
- **The problem: 28 of SIMANTW's 196 imported ordinals had a thunk slot and NO
  handler, so each raised `Win16ApiGap` the moment the game reached it — and
  the owner was finding them one crash per play session (USER.473 in the Load
  dialog, GDI.44 in the About box).  Closed systematically instead.**
- **NAMES FIRST, as facts (win16_re 3be222a).**  25 of the 28 printed nothing
  but a number — `apicoverage.py` listed them honestly as `unnamed`, because
  the rule is that a name is READ OFF a table, never inferred from a call site
  or from Win3.x folklore.  Re-fetched the Wine 16-bit spec files this table
  already cites (krnl386/user/gdi/**keyboard**.drv16) and resolved all 28.
  This is not cosmetic: `ApiRegistry.register` cross-checks every handler
  against the table and RAISES on a mismatch, so a misidentified ordinal fails
  at import time instead of quietly mis-servicing a call — the check that
  caught USER.186 SwapMouseButton once already.  `KEYBOARD` is a whole new
  section (a Win16 app imports its keyboard LAYOUT from KEYBOARD.DRV).
  Also table-sourced KERNEL.89/90 (lstrcat/lstrlen), previously handler-named.
- **THE TIER (20 implemented, each from its own call site in the IR — the arg
  shape, the value the caller BRANCHES on, and what must be true to proceed):**
  - **Load/Save** — `USER.473 AnsiPrev` (the previous character clamped to
    lpszStart; DBCS in Win3.1, a clamped decrement single-byte; returns a FAR
    pointer the caller reads as ES:BX) and `USER.99 DlgDirSelect` (copies the
    selection AND reports directory-vs-file: "[SUBDIR]" -> "SUBDIR\", "[-c-]"
    -> "c:", a file verbatim — SimAnt lstrcat()s its spec onto the result only
    when that boolean is set, so getting it wrong pastes "*.ANT" onto a file).
  - **About** — `GDI.44 SelectClipRgn` (REPLACES the clip with a COPY, which is
    why both sites DeleteObject the region on the next line; tracked as a rect
    exactly like IntersectClipRect, enforcement in the blitters still not
    modelled and nothing has proven the need).
  - **Input** — `KEYBOARD.131 MapVirtualKey` (**new `win16/api/keyboard.py`**: a
    layout IS a table, so the table is the implementation; a key not on the US
    layout RAISES, because a plausible scan code is indistinguishable from a
    right one to the caller and SimAnt feeds the character straight into the C
    runtime's 256-entry ctype table as an INDEX), `USER.70 SetCursorPos` (the
    guest-visible cursor moves NOW — the half any program can detect; warping
    the host's real pointer is presentation and needs a "cursor_warp" callback
    the host may install), `USER.76 PtInRect`.
  - **Menus** — `USER.415 CreatePopupMenu` (CreateMenu's twin here: Menu is a
    pure item tree and the renderer derives bar-vs-popup from where it hangs).
  - **Palette** — `GDI.373 SetSystemPaletteUse` (recorded, not enforced: the
    single-app palette model already gives the app every entry, so there are no
    static colours to reserve; GetSystemPaletteUse now answers truthfully
    instead of a constant), `USER.180/181 GetSysColor/SetSysColors` —
    `SYS_COLORS` becomes the DEFAULTS behind a live **per-machine** table
    (`gdi.sys_colors`) every reader resolves through at paint time, so SimAnt
    repainting Windows' whole colour scheme (saves all 19, installs its own,
    restores on exit) takes effect with no WM_SYSCOLORCHANGE broadcast to model.
  - **Atoms** — `USER.268/269 GlobalAddAtom/GlobalDeleteAtom` + `USER.118
    RegisterWindowMessage` on one `AtomTable`: unique per distinct string,
    case-insensitive, ref-counted, 0xC000..0xFFFF.  RegisterWindowMessage mints
    from the same space, which is exactly what puts a registered message above
    every WM_USER-relative control message.  Integer atoms ("#123") raise.
  - **Lines** — `GDI.19/20 LineTo/MoveTo` with a current position on the DC that
    SaveDC/RestoreDC round-trips; LineTo moves the CP whether or not it painted,
    so a polyline stays connected under a NULL pen.
  - **Debug + PANIC** — `KERNEL.115 OutputDebugString` (to the harness console,
    where stops and gaps already go; a host may take it via "debug_out"),
    `KERNEL.1/137 FatalExit/FatalAppExit` — the C runtime's panic path, which
    must NEVER return: the caller has already declared its state unrecoverable
    and is written on the assumption it dies there.  They raise
    `FatalAppExitError` carrying the CRT's own "run-time error R6xxx" text.
  - Plus `USER.36 GetWindowText`.  `GDI.136 RemoveFontResource` was already done
    (5e08353) — the coverage artifact calling it a tripwire is stale.
- **LEFT FAILING LOUD, ON EVIDENCE — the interesting half:**
  - **`KERNEL.103 NetBIOSCall` — its only caller is PHYSICALLY TRUNCATED in
    SIMANTW.EXE.**  `_NetBios` (275F:8380) is `push bp / mov bp,sp / pusha /
    push ds / push es / les bx,[bp+6] / mov ax,0100h / call far NetBIOSCall` —
    and **segment 4's data ends at 0x8391, which is exactly the end of that
    call**.  The epilogue is not in the file; the relocation at 0x838D (module
    1, ordinal 103) is the segment's LAST.  min_alloc == file_length == 0x8391,
    so on real Windows returning from that call runs past the segment and
    faults.  A handler could only ever return into nothing.  (The IR's extent
    for `_NetBios` runs to 85DF — irgen's linear sweep decodes PAST
    `file_length` into zeros.  Cosmetic here, but it is an irgen bug.)
    Reachable on paper (_NbCall <- _NetworkSend <- MYTIMERFUNC <- a WM_TIMER
    dispatch case), which makes SimAnt's network mode dead by construction.
  - **`USER.416 TrackPopupMenu`** — no caller at all
    (`_ms_PopUpMenuResource` is dead Maxis-library code), and it needs real
    popup-menu host UI.
  - **`KERNEL.6/13 LocalReAlloc/LocalCompact`** — callers (`__nexpand`,
    `__nrealloc`, `__freect`, `__memavl`) are dead CRT code: no near call, no
    far call, and absent from every dispatch/indirect-target fact.  Writing an
    implementation for a site that cannot execute is speculative generality;
    the tripwire costs nothing and now names itself.
  - **`KERNEL.111/112/191/192`** (GlobalWire/GlobalUnWire/GlobalPageLock/
    GlobalPageUnlock) — the sound path, deferred to the concurrent audio work.
    Their NAMES are in the table now, so their gaps name themselves.
- **THEN THE DIALOGS OPENED FOR THE FIRST TIME — and were broken three ways
  (win16_re 084c717).  None was a missing API; all three were this layer
  answering wrong, and each failed as a PLAUSIBLE VALUE, never as an error.**
  1. **DlgDirList ignored its DDL_* filetype entirely** — `_fill_dir_control`
     did not take the argument and both callers dropped it.  That INVERTS the
     answer: SAVEASDLG asks DDL_EXCLUSIVE|DDL_DRIVES|DDL_DIRECTORY (0xC010) for
     "the folders and drives here" and deliberately passes an EMPTY spec
     (it wants no files) — which fell through a `or "*.*"` default and listed
     every file in the game folder, SIMANTW.EXE and README.WRI included.
  2. **LB_ERR/CB_ERR went back as 0x0000FFFF through a `ret="long"` API.**
     They are -1 and must be SIGN-EXTENDED (DX:AX = FFFF:FFFF); with DX=0 the
     guest reads 65535.  SAVEASDLG and `_ChangeDirectory` both test the FULL
     long after LB_GETCURSEL (`cmp ax,FFFF / jnz / cmp dx,ax / jnz`), so DX=0
     told them a directory WAS selected: **the Save As dialog re-listed forever
     and could never be dismissed.**
  3. **`_fill_dir_control` pre-selected item 0.**  Real DlgDirList never sends
     LB_SETCURSEL — the box comes up unselected and LB_GETCURSEL answers LB_ERR
     until a user clicks.  Same "a directory is selected" lie from the other
     end.  Selecting the first row looks helpful and silently makes Save As
     impossible.
  Plus **INT 21h AH=36h (Get Free Disk Space)**, the last thing between the
  dialog and a file: `_SaveGame` calls the MSC runtime's `__dos_getdiskfree` and
  refuses to save unless free clusters x sectors x bytes exceeds its save size
  by 25%.  Deterministic geometry (512-byte sectors, 4K clusters, 256MB) for the
  same reason AH=2Ah/2Ch already report a fixed date and time: a demo replay
  must not depend on how full the operator's real disk is.
- **A CORRECTION I OWE MY OWN FIRST WRITE-UP: "ordering is part of the DlgDirList
  contract" was an INFERENCE, and it was wrong.**  I deduced "USER appends the
  bracketed entries after the files" from `_UpdateListBox` hoisting them to the
  top.  Competing explanation, checked instead of assumed: **"[" is 0x5B, above
  "Z" (0x5A)** — in a SORTED box the brackets collate last on their own, and the
  game is merely choosing a nicer order.  **Both list boxes in the DLGTEMPLATE
  are LBS_SORT (style 0x50a10003)**, and our control model has no sorting of its
  own, so the listing is sorted unconditionally; for a sorted box the two are
  indistinguishable.  An UNSORTED box would differ and nothing has proven that
  case — said in the docstring, not claimed as a finding.
- **A path spec we cannot serve now FAILS LOUD.**  The file model is flat —
  `system._canonical` reduces every path to its last component — so there is
  exactly one directory and it IS the root.  A user picking "[SOUND]" makes the
  game build "SOUND\*.ANT", and answering with an empty list would say "that
  folder is empty": indistinguishable from the truth, and the worst answer
  available.  Traversal means changing the file model itself, so this raises and
  names the spec.  Listing a directory we cannot enter is still right — it is
  really there; hiding it would be the lie.
- **DRIVEN, not just unit-tested.**
  - **The owner's own Load crash (`crash_081109`, a play_vmless snapshot parked
    at 0060:027C) resumes and crosses the gap**: OPENDLG's separator walk runs
    to completion — **5 AnsiPrev calls stepping 93A5 -> 93A0 over "*.ANT"**,
    terminating on the caller's own `jbe` guard, then taking its no-separator
    arm (lstrcpy to the spec + re-list).
  - **Save/Load END TO END** (strict VMless runner, in-game snapshot
    crash_230334, far-calling the game's own cdecl `_SaveGame`/`_LoadGame` with
    a host that types a name and presses OK): SaveAs opens; list box 0x194 shows
    **`['[-c-]', '[SOUND]']`** (assets/ANTWIN has exactly one subdirectory);
    **`_SaveGame(0) -> AX=1` after 1 `_lcreat` + 307 `_lwrite`: AGENT1.ANT,
    48,386 bytes**; the **`Open`** dialog (the other DIALOG resource) opens and
    **`_LoadGame(0,0) -> AX=1`, the game's own reader reopening `C:\AGENT1.ANT`
    and reading all 48,386 bytes back**.  The COMMDLG.DLL probes still miss (-1)
    three times, so the game keeps taking its built-in-dialog fallback — the
    path under test.  Writes land in the guest's overlay filesystem
    (`Win16System.overlay_files`), which is the model: the host's asset
    directory is never mutated.
- **A CRASH INSIDE A MODAL DIALOG CANNOT BE RESUMED INTO THAT DIALOG** (found
  the hard way, spun off as a task).  `DialogBox`'s `try/finally` removes the
  Dialog and its controls as the gap propagates, so `_save_crash_snapshot` runs
  AFTER the dialog is gone: crash_081109's pickle has 49 handles and ZERO
  Dialogs while its `callback_frames` still records `DialogBox`.  Resuming and
  stepping dies on `handle 0164 is not a dialog`.  (`save_snapshot`'s own
  refusal to snapshot with a dialog open only passes because the dialog is
  already destroyed — the same bug wearing a different hat.)
- **Gates.  win16_re 289 (242 + 47 new in `tests/test_api_tripwire_tier.py`),
  simant_port 2258.**  Both measured against a CLEAN win16_re worktree at the
  committed tip: the working tree carries another agent's in-flight
  `system.py`/`mmsystem.py`/`audio.py`, whose missing `scheduled_messages`
  reddens 7 win16_re tests and the strict walls — **not this work**, and proven
  so by the clean run.  Tests pin the DECLARED pascal arg shape and return of
  every added API (a shape read wrong corrupts the guest stack), the returns
  callers branch on, and the fail-loud edges.
- **PINNED DIGESTS VERIFIED, NOT ASSUMED: the 45M clean-room prefix still pins
  instr 45,102,443 / digest 50365479… and `test_vmless_walls` is 6/6 green; the
  39-checkpoint cold_nohooks differential is byte-identical to the same trace
  generated on the pre-change tip (41028e9).**  Structural, not luck:
  cold_nohooks never opens a dialog, never presses a key reaching MapVirtualKey,
  and never paints an About box — every new handler sits beyond or outside it.
  `simant/tests/test_apicoverage.py::test_identity_is_honest` DID move, and it
  is the good kind: it asserted "SIMANTW imports ordinals nothing names yet",
  which the sweep made false.  Replaced by the stronger fact it became — **no
  import is unnamed**, and **every TRIPWIRE is named from the ordinal table** (a
  tripwire has no handler to borrow a name from, so the table is its only honest
  source), while an implemented target may still be handler-named (a few KERNEL
  ordinals are absent from the Wine tables).
- **What the owner can do now that they could not**: open the Load and Save As
  dialogs and actually save and load a game; open the About box; use the arrow
  keys to move the pointer and the cheat keys; let the game repaint Windows'
  system colours; draw the laser fire and the history graph (GDI lines).
- Queue: the four sound-adjacent ordinals (audio agent); DlgDirList traversal
  (needs the flat file model to grow paths — `system._canonical` drops the
  directory part of every path today, which is the same lie one level down);
  the dialog-crash-snapshot fix; irgen decoding past `file_length`.

## 2026-07-17 (cont.234) — the Quick Game scroll bars: the thumb that vanished at max, and the Win32 packing that told the game "go to the top" (win16_re 9089e12)
- **Owner report (live `play_vmless`): "dragging the scrollbar thumb does not
  scroll the view; the arrows are not visible by default, so I have to click
  the scrollbar first to give it focus and then use the arrow keys."  Owner
  note: long-standing, not a regression — "it was always like that in our vm,
  but it works correctly on W311."**
- **TRIAGE FIRST (standing directive), and it answered itself structurally:
  NOT a lifter bug.**  `play_vmless` imports `play.py`'s `PlayApp` — both
  paths share the SAME host `WindowView`, so the interpreted and strict-lifted
  paths cannot differ here.  No A/B needed; the code is common by construction.
- **What SimAnt's scroll bars ARE — settled from the IR + the EXE, not
  assumed.**  WINDOW scroll bars (non-client WS_H/VSCROLL), never SCROLLBAR
  child controls: the whole scroll API surface is `ANTEDIT_MODULE`
  (SetScrollPos ×13, SetScrollRange/GetScrollPos/GetScrollRange in
  `_ResetEditScrollRange` 3:6086, ScrollWindow in `_ScrollEditWindow` /
  `_UpdateEdit`), and zero `CreateWindow` with a SCROLLBAR class exists.
  Live in-game (crash_230334): **hwnd 014C "SimAnt - Quick Game", style
  44FD0000 = WS_VSCROLL|WS_HSCROLL|WS_THICKFRAME|WS_CHILD|WS_CAPTION**, with
  `scroll={1: (0,42,0), 0: (0,37,22)}` — the game sets its ranges correctly.
  It is the ONLY WS_?SCROLL window, and it is `presents_standalone()` (full
  caption) => promoted to its own host Toplevel.
- **Which SB_* codes the game honours — read off its own jump table, not
  guessed.**  `_DoEditScroll` (3:5C6E) is `(hwnd, msg, code, lParam)`:
  `[bp+8]` vs 0x114 picks the bar (WM_HSCROLL => SB_HORZ else SB_VERT),
  `[bp+0xa]` is the code, and `cmp ax,7 / ja default` indexes an 8-entry table
  at cs:5CAE — **every code 0..7 is handled**, THUMBPOSITION(4) and
  THUMBTRACK(5) sharing one arm (3:5F5C), with SB_ENDSCROLL(8) falling to the
  default (ignored, as most Win3.x apps do).  The thumb arm reads its position
  from **`[bp+0xc]` = LOWORD(lParam)**, and a DGROUP flag (5294:85F2) gates
  whether THUMBTRACK is honoured live or only THUMBPOSITION lands — the game's
  own "live scroll" option.  On THUMBTRACK it deliberately does NOT call
  SetScrollPos (USER owns the thumb while tracking); on THUMBPOSITION it does.
  The wndproc arm (1:3810) forwards wParam/lParam verbatim.
- **Root cause per symptom — our layer has NO scroll-bar UI at all.**
  `win16/api/user.py` keeps `Window.scroll[bar] = (min,max,pos)` as pure data;
  nothing renders, hit-tests or generates WM_?SCROLL anywhere in `win16/`.
  The bars are **host chrome**: `play.py`'s `WindowView` mirrors WS_H/VSCROLL
  onto real `tk.Scrollbar` widgets (the same pattern as the OS title bar,
  resize handles and close box of a promoted window).  Both bugs were in the
  host's translation:
  1. **The thumb vanished.**  `_sync_scrollbars` mapped (lo,hi,pos) -> tk's
     (first,last) with a fabricated 0.15 page: `first=(pos-lo)/(hi-lo);
     last=min(first+0.15,1.0)`.  The box therefore SHRANK as pos approached
     hi and hit **zero size exactly at pos==hi** — measured live on the
     owner's own window: vert `(0,42,42)` -> `set(1.0,1.0)`, tk elements
     `['arrow1','trough1','arrow2']`, **no slider**.  Nothing to grab, which
     is why "dragging does nothing" and why the thumb "is not present until
     you click on that bar" (a trough click moves pos off the max, and the
     thumb reappears).  Win 3.x has no page size at all: SetScrollRange gives
     a **FIXED-size thumb**; proportional thumbs are Win95's SetScrollInfo.
  2. **Every drag meant "go to position 0".**  `_post_scroll` packed the
     position **Win32-style** — `wparam = 4 | (newpos << 16)`, `lParam = 0`.
     Win16's wParam is a WORD, and `Win16System.call_wndproc` masks
     `wparam & 0xFFFF` (correctly): the position was **discarded**, and the
     game read `LOWORD(lParam)` = **0**.  Proven against the live guest
     pre-fix: "drag the thumb to the MIDDLE" (`moveto 0.5`) delivered
     `code=4 pos=0` and the view jumped to the TOP.  It fails as a plausible
     value, never as an error — which is why it survived this long.
  3. **Only the keyboard worked** because the GAME does that itself: 1:345C
     etc. poll GetAsyncKeyState and call `_DoEditScroll` DIRECTLY, bypassing
     the host path entirely.  Nothing was ever right about the mouse path.
  Also missing: no SB_THUMBTRACK stream and no SB_ENDSCROLL ever.
- **NEW (win16_re 9089e12, generic): `win16/scrollbar.py` — the Win16 scroll
  notification contract.**  `scroll_message()` packs the shape once (code in a
  16-bit wParam; position in LOWORD(lParam) for SB_THUMB* only; hwndCtl in
  HIWORD, 0 for a window bar).  `thumb_metrics()`/`thumb_position()` are the
  Win 3.x FIXED thumb and its exact inverse — the box keeps its size at every
  position, the TRAVEL absorbs the range, and a thumb dropped at an extreme
  reaches min/max instead of falling short by its own size.  `ScrollTracker`
  owns the SEQUENCE (order is contract): line/page on the host's repeat clock,
  a drag streaming SB_THUMBTRACK at the live position then landing on
  SB_THUMBPOSITION, a cancelled drag reporting the ORIGIN, and exactly one
  SB_ENDSCROLL closing every interaction.  **Zero game knowledge; the module
  is pixel-free and clock-free by design** — whoever draws the bar owns the
  gesture, this owns what the guest sees.
- **The DIVISION OF LABOUR, stated rather than fudged**: the bars are host
  chrome, so **tk owns the gesture** (arrow/trough/thumb hit-testing, the
  auto-repeat delay+rate, the drag) and win16 owns the protocol.  play.py now
  binds press/release/ESC (tk's `command` reports movement but never the
  button), starts a drag only when the press lands on the `slider` element,
  sizes the thumb from the real pixel geometry (a square the bar's breadth,
  the trough being what is left after the two arrows), and **skips
  `_sync_scrollbars` for a bar while it is being dragged** — USER owns the
  thumb during tracking, and an app calling SetScrollPos mid-drag does not get
  to yank it back (which is exactly what the game's own THUMBTRACK arm
  assumes).  Consequence, reported honestly: **hit-test geometry and
  auto-repeat timing are tk's, not ours, so they are not ours to unit-test** —
  our layer is not asked to draw a scroll bar anywhere (014C is the only
  WS_?SCROLL window and it is promoted), and inventing an unused non-client
  scroll-bar renderer would be speculative generality.
- **HEADLESS PROOF — all four interactions against the LIVE guest** (strict
  lifted machine resumed from crash_230334, driving play.py's own handlers
  exactly as tk drives them; scratchpad `sbfinal.py` / `sbcancel.py`).  Method
  note that matters: the physical desktop pointer sits 4 px from the real
  on-screen bar and its stray events polluted the first runs — tk's Scrollbar
  class bindings are dropped for the measurement so ONLY the synthetic gesture
  drives the app.  (The apparent "feedback loop" was that, not `set()`:
  `Scrollbar.set()` provably does not invoke `command`, and 2 s idle gives 38
  syncs and ZERO commands.)
  - thumb at pos==max `(0,42,42)`: `set=(0.944,1.000) thumb_size=0.056`,
    elements `['arrow1','trough1','slider','arrow2']` — **grabbable** (was: no
    slider at all);
  - **arrow** click held 3 repeats: `SB_LINEDOWN(0) x3 -> SB_ENDSCROLL(0)`;
  - **trough** click held 2 repeats: `SB_PAGEUP(0) x2 -> SB_ENDSCROLL(0)`,
    pos 42 -> 0;
  - **thumb drag**: `SB_THUMBTRACK(13) -> SB_THUMBTRACK(22) ->
    SB_THUMBTRACK(31) -> SB_THUMBPOSITION(31) -> SB_ENDSCROLL(0)`, and the
    view **lands where it was dropped: pos 0 -> 31** (pre-fix: -> 0, always);
  - **drag cancelled with ESC**: `SB_THUMBTRACK(4) -> SB_THUMBTRACK(0) ->
    SB_THUMBPOSITION(31) -> SB_ENDSCROLL(0)`; a direct watch on every write to
    `scroll[SB_VERT]` shows the only write after the cancel is **pos 31 at
    t+0.03 s, and it holds for 2 s** — the thumb snaps back to its origin.
    (In the four-interaction run the pos read later drifted off 31: the game
    scrolls its own view autonomously between steps; the SEQUENCE — the half
    we own — was byte-exact in both runs.)
- **Gates: win16_re 239 passed, simant_port 2257 passed.  NO pinned digest
  moved, and it is not luck — it is structural**: `win16.scrollbar` is
  imported ONLY by `scripts/play.py`, `WindowView` is constructed ONLY there,
  and the headless demo/checkpoint/verify path never imports either, so the
  change cannot reach a recorded baseline.  Confirmed rather than assumed —
  the clean-room 45M prefix still pins **instr 45,102,443 / digest
  50365479...**, and `test_boundary_parks` is green (8 passed together).  No
  re-pin, no re-record.
- **Regression tests (win16_re `tests/test_scrollbar.py`, 33)**: the message
  shape (code in wParam; position in LOWORD(lParam) and NOT in HIWORD(wParam);
  non-thumb codes carry no position; hwndCtl in HIWORD), the fixed-thumb
  geometry (same size across the whole range; **does not vanish at pos==max**
  — the live `(0,42,42)` state; parks at both ends; empty range fills the
  trough; exact round-trip with `thumb_position`; extremes reach min/max), and
  the SEQUENCES (arrow, arrow auto-repeat, trough, full drag incl. the
  THUMBTRACK stream and its live positions, a still thumb notifying nothing, a
  cancelled drag reporting the origin, a move with no grab being a POSITION
  not a track, and SB_ENDSCROLL sent exactly once per interaction).  **Two pin
  the pre-fix bugs directly**: re-running the old formulas verbatim gives
  `wParam=0x150004` (masked to `0x4`, position 21 destroyed) and thumb sizes
  `[0.0, 0.0238, ..., 0.15]` collapsing to **0.0** at max.
- **HOUSEKEEPING THE OWNER MUST SEE — my host-side hunks were swept into
  another agent's commit.**  A concurrent agent worked `scripts/play.py`
  (menu accelerators) at the same time and staged the WHOLE FILE: commit
  **9dd8b1f "play_vmless: ASCII-safe console output"** carries my 116-line
  scrollbar rewrite of `WindowView` under an unrelated message (its sibling
  d1fb982 is clean — 6 lines, theirs only).  The code is correct, tested and
  green; only its attribution is wrong.  I did NOT rewrite their history: they
  were still working the tree, and nothing here is pushed — reword/split at
  review if it matters.  Recording it so the next reader is not baffled by a
  console-encoding commit containing scroll bars.
- Queue / honest residue: (i) **real Win3.x tracking niceties tk does not
  give us** — the perpendicular snap-back margin (drag strays too far from the
  bar => thumb returns) is unimplemented; ESC-cancel IS wired and proven, but
  tk keeps dragging its own slider until the button comes up, so the host
  latches the cancel and ignores tk's remaining `moveto`s until then;
  (ii) a composited (non-promoted) WS_?SCROLL window would today have NO bars
  at all — no such window exists in SimAnt (014C is the only one and it is
  promoted), so the non-client renderer stays unbuilt rather than speculative;
  (iii) cont.232's popup promotion z-order closure still open; (iv) `dist/` is
  still stale.

## 2026-07-17 (cont.233) — the sim frame driver joins the parked set: per-head park costs (frame_gate/pacing_spin); MagnifyMenu + EndGameDialog promoted; 29 heads
- **Owner live crash: `simant_mytimerfunc exceeded MAX_ITERATIONS` at
  0100:27E2 (instr 89,397,794, ~15 min of play; crash_230334), api tail =
  SelectObject/GetTickCount/CreateSolidBrush/FillRect/DeleteObject/
  SelectPalette/ReleaseDC/PeekMessage×2 per iteration.**  Exactly the case
  cont.229 queued: SIMANT!MYTIMERFUNC is the SIM FRAME DRIVER — the SetTimer
  TimerProc the game lives inside for the whole of gameplay — and it is NOT a
  pure wait (my conservative waitscan rule correctly refused to auto-park it).
- **Disassembled, not assumed** (loop 25B6..2803): each pass bumps a counter,
  runs `_DoAntSim` + the frame's rendering when enough ticks are pending,
  drains WM_TIMERs (27BF..27DA, si counts them), services the MIDI song
  (`_myServiceSong`), then decides: exit if the game window is gone, or if
  si>=2 **and** di>=3 (behind — let the pump run), or if the left button is
  down; **otherwise loop**.  With si<2 the only exit is a click, so the loop
  spins until WM_TIMERs arrive — i.e. **it waits on the WALL CLOCK**, frozen
  inside a lifted invocation.  Same class as every other head.
- **A hypothesis of mine that my own measurements REFUTED (recorded because
  the correction is the useful part).**  I first read this as a RESIDENCE
  bug: the guard is a per-invocation block-transition budget, the game lives
  in the frame loop, so it accumulates over a session.  Two measurements say
  otherwise: (i) over cold_nohooks MYTIMERFUNC's max OWN-code span is ~50k
  instructions across 538 calls — short invocations; (ii) live in-game for
  5 minutes it parks only ~5 times per 30 s while ~480 ticks flow — the loop
  *exits by itself* almost every pass.  The truth is simpler and is the
  cont.229 class exactly: the loop only reaches its wait state when the host
  is KEEPING UP (fewer than 2 ticks pending), and then the frozen clock means
  no tick can ever arrive → it spins → the guard trips.  That is why the
  crash takes minutes to appear (it is the first moment the game gets ahead
  of its timers), not because anything accumulates.  A park does also reset
  the per-invocation budget — which is what makes the *productive* residence
  (a fast host legitimately living in this loop) safe — but that is a
  property of the fix, not the cause of the crash.
- **NEW (win16_re, generic): PER-HEAD PARK COSTS — dos_re's own
  frame_gate/pacing_spin vocabulary, priced by the host** (its 4-arg observer
  ABI passes the head identity precisely so a port can price heads).
  `arm_boundary_parks(cpu, head_kinds=...)`:
  - `PACING_SPIN` (default) — one pass of a BOUNDED wait: sleep ~1 ms (the
    watched clock moves, the loop's own exit still fires promptly, no core
    burnt).
  - `FRAME_GATE` — one pass IS one frame: pay the whole frame quota via the
    new `wait_for_work()` (return at once if a tick is already due or input
    is pending; else block until the next armed timer is due, capped at
    10 ms so a loop watching something else stays responsive).  Same rule
    `_next`/GetMessage already paces by, so the cadence matches the original
    busy-spin with the CPU idle between frames.
  - **A DRAIN loop must never be frame_gate** (documented in the facts +
    suite-pinned): waiting for the next timer to come due would hand
    MYTIMERFUNC's own drain (1:27BF) a message every pass and it would never
    end.  The kind decides termination, not just pacing.
- **The policy is generated data, not a second source of truth**:
  `simant/facts/boundary_heads*.txt` lines may carry a trailing kind;
  `scripts/liftemit.py` converts them through the IR's own ne_seg identity,
  CROSS-CHECKS the head set against `facts_applied` (drift fails loud), and
  writes `simant/lifted/graph/boundary_policy.json` — paragraph-keyed, so it
  ships wherever the graph ships and needs no NE mapping or facts files at
  run time.  `simant.vmless_boot.boundary_park_kinds()` reads it; play.py
  passes it to the driver.  **Emission is identical for every kind** — the
  kind prices the park, it never changes what executes.
- **Two more heads, both crash-evidenced, both reversing a "don't park this"
  hypothesis:**
  - **ANTEDIT!_MagnifyMenu 3:6CAA** (the press-and-hold popup's ButtonHeld
    poll; crash_231350 at 18C0:6CFB, instr 294,126,381).  I was told not to
    park it (theory: a win16 window-management bug, so parking would turn a
    loud crash into a silent hang).  **Disproven — and I re-derived it
    first-hand rather than taking it on report** (scratchpad
    `magnify_verify.py`): (A) resume the crash snapshot with NO head → the
    guard trips again after 93M instructions with the clock advanced **0 ms**
    (11468 → 11468: the frozen-clock signature; `GR!_ButtonHeld`'s 55 ms
    countdown can never drain); (B) with the head + parks, NO input → **25 s
    with no crash**, clock 11468 → 36467 (+24,999 ms), 33,432 parks, api tail
    now `GetAsyncKeyState(1)` — the menu politely waits for a press, which is
    its DESIGNED behaviour, not a hang; (C) with a press/release injected →
    the loop **exits** and execution leaves MagnifyMenu entirely (the run
    continued 757M instructions into other code).  The resize agent's
    independent cont.232 analysis agrees; the "popup renders behind the Quick
    Game window" symptom is a separate HOST PROMOTION bug (theirs).
  - **SIMANT!_EndGameDialog 1:6688** — found BY that verification: once the
    popup dismissed, the game reached the end-game dialog and died
    (`simant_endgamedialog exceeded MAX_ITERATIONS` at 0100:66D0, **757M
    instructions inside ONE invocation**, api tail = PeekMessage +
    IsWindowVisible(370) repeating).  Its loop (6688..66D5) is
    `_ShowIntro`'s structural twin — `or si,si` → `_win_GetEvent` pump →
    `_mySongIsDone` (re-loops the music) → `_win_IsWinOpen` → loop — i.e. it
    waits for a click while its song plays.  Promoted with that evidence.
- **Sibling frame loops — asked mechanically, answered honestly.**  The
  frame-gate signature (a loop whose PeekMessage filters WM_TIMER) matches
  exactly **3 functions**: MYTIMERFUNC (its outer loop + its drain) and the
  two shutdown waits `_StopSimulation`/`_CleanUp`, already derived heads.
  WINMAIN's message pump (440D..444F) is not one: it blocks in GetMessage,
  which yields to the host by construction — and its "loop 3FFE..45D8" in
  wait_report is a FALSE loop (backward jumps to the shared epilogue, like
  MAINWNDPROC's switch dispatcher).  waitscan's docstring now names both
  limits (false loops; the wait state the demo cannot surface) so the next
  reader does not mistake the mixed list for a to-do list.
- **The demo's own blind spot, stated rather than papered over**: its clock is
  derived from the instruction count, so every timer is due instantly and the
  frame driver never enters its wait state.  The whole-demo differential
  therefore proves the lifted BODY byte-for-byte and says nothing about the
  wait; live play is the only evidence for that half — which is why this
  slice's acceptance is a live in-game run, not just a green gate.
- **Gates (29 heads: 25 derived + 4 hand-promoted):**
  - **NEW `simant/tests/test_boundary_parks.py` — parks are TRANSPARENT**:
    the pinned clean-room 45M prefix replayed with EVERY head parking
    (cost-free) reproduces the park-free pin EXACTLY — instr 45,102,443 and
    digest 50365479… .  This is stronger than the differential alone: it
    proves the park/resume path itself (unwind → RESUME entry → continue),
    not just the emission.  Plus a pin that the frame-gate policy ships and
    that the drain stays pacing_spin.
  - whole-demo differential, literal graph vs interpreted oracle
    (api-aligned): **MATCH all 39 checkpoints + final** (269eb6db…);
  - **THE WALL RUN**, boot image + poison, masked: **MATCH all 39 + final**
    (05c738b2…), instr 199,612,996;
  - suites: win16_re 189+, simant_port 2254+ green; waitscan `--check` fresh
    (25 derived heads).
- **LIVE ACCEPTANCE (the half the demo cannot prove): 330 s of in-game play,
  strict graph, parks armed — ZERO VM stops.**  Method (scratchpad
  `live_gameplay.py`): drive to gameplay with the demo prefix the walls test
  already trusts (boot→intro→menus→NewGame→worldgen→gameplay), detach the
  demo, attach the real InteractiveDriver + the graph's park policy, play;
  sim rate measured mode-independently in the layer both share (every
  WM_TIMER `Win16System._due_timer` hands the game = one tick).  Result:
  **4591 ticks in 330 s = 13.9/s, 431M instructions (1.3M/s), 5 parks, 0 VM
  stops** — vs the **interpreted control's 6.0 ticks/s** over the identical
  procedure (2.3x faster).  Both hosts are CPU-bound below the game's 59 Hz
  timer on CPython, which is why the frame loop mostly exits on its own and
  parks only ~5 times: each of those 5 is a moment the host caught up — i.e.
  each is a crash pre-fix.  Two findings worth keeping: (a) a park
  inside the TimerProc is absorbed by `call_far`'s chunk loop — the callback
  keeps running and far-returns normally, which is the design, but it means
  `cpu.run` does not return to the worker for minutes: the wall clock and
  pause live on `yield_check`, as they already did; (b) the harness's own
  driver switch orphaned an in-flight callback frame — handled by win16's
  existing snapshot-resume orphan path, i.e. a harness seam, not a product
  one (the interpreted control hit it identically with no parks at all).
- Honest residue / queue: (i) **adopt dos_re's new `boundary_clock.py`**
  (upstream ce620ab, not yet pinned here): it is the generic seam for this
  mechanism — same observer ABI, same frame_gate/pacing_spin taxonomy, plus
  pass-counted quotas and exact cross-mode parks.  What I landed is the
  INTERACTIVE half it does not cover (what a park COSTS in wall-clock time:
  a fixed sleep vs `wait_for_work`), and my facts→`boundary_policy.json`
  pipeline is exactly the "heads and kinds are PORT FACTS the port passes
  in" input it asks for — so adoption should subsume win16's `head_kinds`
  dict + `BoundaryParked` and keep the facts side as-is.  Queued
  deliberately rather than hand-rolled further; (ii) the remaining `mixed`
  candidates stay report-only — each needs a crash or an A/B, and the
  wait-vs-platform-gap question (cont.232's lesson) answered first;
  (iii) `dist/` is still stale (deploy must re-run to ship the parked graph
  + the policy file it now reads); (iv) a park costs a Python unwind per
  pass — if a future head sits in a hot inner loop, price it deliberately.

## 2026-07-16 (cont.232) — resize refresh fixed at the PeekMessage pump (win16_re 39fc1c6); the MagnifyMenu popup crash proven to be an ENV-WAIT, not a window-management gap; the popup's "behind our window" traced to the host PROMOTION model
- **Owner report 1: "on vmless play: quick play window is not refreshing when
  resized."  Triaged lifter-first (standing directive) and answered by a
  DIFFERENTIAL, not plausibility: the identical scripted resize reproduces the
  same way on BOTH paths** — `play.py` (interpreted hybrid, EXE boot +
  islands) and the strict lifted graph (play_vmless's boot-image machine).
  **Not a lifter/park bug.**  Repro (scratchpad `resize_repro.py`): drive to
  the target state under the real InteractiveDriver with a `call_wndproc`
  tap, then emulate exactly what play.py's `WindowView._apply_resize` does
  (`win.w/h` assigned, `win._surface = None`, WM_SIZE posted).
  - PRE-FIX, both paths, main frame 640x480 -> 720x540: WM_SIZE **does**
    reach the game; it re-lays-out via SetWindowPos + InvalidateRect
    (dirty := True) — and then **nothing ever paints**: `dirty=1` forever,
    `surf=(no surface)` 20 s later.  The window renders stale/black.
  - **Root cause (win16, generic): real USER generates WM_PAINT at message-
    RETRIEVAL time (lowest priority) for any window with a non-empty update
    region — through GetMessage AND PeekMessage.  Our `peek_message` never
    synthesized it; only `get_message`'s pump did.**  SimAnt's title /
    scenario / in-game loops pump with PeekMessage and never call GetMessage
    for long stretches, so the paint the resize invalidated was never
    retrieved.  (The same asymmetry had already been fixed once for WM_TIMER
    — the sim-tick spin — which is exactly why in-game animation works but a
    resize does not.)
  - **Fix (win16_re 39fc1c6)**: `peek_message` synthesizes WM_PAINT for the
    first visible+dirty window matching the hwnd/message filter, after posted
    messages and due timers (real USER's priority order).  WM_PAINT is not
    consumed by retrieval, so `remove` is irrelevant — BeginPaint/ValidateRect
    clear the region and the paint stops repeating exactly then.  **Gated to
    `sysobj.interactive`** (set solely by InteractiveDriver; demo/tick replay
    and verify force it False) so no recorded instruction-keyed baseline moves.
  - POST-FIX: WM_PAINT dispatched within 100 ms, dirty 1 -> 0, surface
    reallocated.  In-game on the owner's actual window (Quick Game 014C,
    interpreted path): 423x346 -> 503x406, surface rebuilt, and the newly
    exposed strip carries **6872 painted (non-black) pixels**.
  - Gates: win16_re 173, simant_port 2254, full `play_vmless --demo
    cold_nohooks` **unchanged** — 199,612,996 instructions, digest
    05c738b2... == the pinned masked oracle final, VMless wall HOLDS.  No
    pixels move on the demo path (the demo driver never sets `interactive`).
  - `win16_re/tests/test_peek_paint.py` (7, four failing on old code).
- **Owner report 2 (the popup): "double clicking on quick game view ... shows
  a borderless popup ... it shown behind our window", plus a hard crash
  `LiftRuntimeError: antedit_magnifymenu exceeded MAX_ITERATIONS` at
  18C0:6CFB (instr 294,126,381; snapshot crash_231350).  These are TWO
  DIFFERENT BUGS, and the crash is NOT the window-management gap it looks
  like.**
- **The crash is a textbook ENV-WAIT (the cont.229 class) — proven from the
  snapshot's own state, not inferred.**  `_MagnifyMenu` (ANTEDIT 3:6B4C) is a
  press-and-hold menu:

      win_Open(popup); MySetCapture; ButtonHeldInit
      while ButtonHeld() and win_IsWinOpen(popup):        # 6CAA..6D00
          if not win_IsWinInFront(popup):
              BringWindowToTop; MSClipStart; win_DrawWindow; MSClipEnd; MySetCapture
      ButtonHeldEnd; MyReleaseCapture; win_Close(popup)   # 6D02

  `GR!_ButtonHeld` (2:4A4E) decoded: a **delay counter [7236]** (drains one
  per 55 ms `_TickCount` CHANGE — `_TickCount` = GetTickCount/0x37) gates a
  **press latch [7234]**; while [7234]==0 it returns 1 ("keep waiting"), and
  only once a press has been OBSERVED does a release make it return 0.
  - **Snapshot state (decisive): `[7236]=2`, stored tick `[7230]=208`, and
    `clock_ms=11468` -> `11468/55 = 208` — the stored tick EQUALS the current
    tick.  The wall clock is FROZEN at exactly the tick ButtonHeld recorded**,
    because the lifted invocation never returns to the chunk loop where
    `check_pause` advances it.  The countdown can therefore NEVER drain, the
    poll is never reached, the loop spins -> runaway guard.  The api tail
    shows exactly ONE GetTickCount per cycle = the compare is EQUAL every
    pass.  Re-verified live: resuming crash_231350 on a strict machine
    reproduces the owner's crash and tail byte-for-byte, `clock=11468` at
    resume AND at crash; arming boundary parks changes nothing (MagnifyMenu is
    not a declared head).
- **The coordinator's hypothesis ("parking would turn the crash into a HANG;
  the real fix is window management") is DISPROVEN — parking makes it WORK.**
  End-to-end proof (scratchpad `popup_dismiss4.py`: crash_231350 on the strict
  machine, clock advancing, runaway guard raised for the experiment — a park
  achieves both in production by re-entering at the resume entry):
  hold the button as the menu opens -> `[7236]` drains 2->0 -> **`[7234]`
  LATCHES to 1 at poll 4** -> release -> ButtonHeld returns 0 -> the loop
  exits and the api tail is the game's own dismissal sequence:
  `ReleaseCapture()` then **`ShowWindow(356, 0)` (SW_HIDE)** — **popup
  0164 `visible=False`, gone from the visible list, and gameplay resumes
  (attack.mid starts).**  So: our GetAsyncKeyState, ShowWindow(SW_HIDE),
  ReleaseCapture and z-order all serve the game CORRECTLY; the only thing
  missing was a clock that moves.  **The fix is a boundary head on
  MagnifyMenu's loop — the env-wait agent's machinery.**  It "waits" only
  while no button is pressed, which is the DESIGNED behaviour and is exactly
  the owner's own description ("this window disappears if you click away" =
  press to dismiss).
- **The window-management worries checked and CLEARED (evidence, not
  assertion):** (i) `ShowWindow(SW_HIDE)` DOES make a window vanish from the
  visible list + enumeration (the dismissal above is the proof); (ii) our
  Win16 z-order is CORRECT — popup 0164 is topmost in `sysobj.windows`,
  `_z_children` puts it first, and `_win_IsWinInFront` (7:C19E: GetTopWindow
  -> walk GW_HWNDNEXT to the first VISIBLE -> remap through GW_OWNER ->
  GetProp) returns TRUE, which is why **no BringWindowToTop appears in the
  crash tail**; (iii) `GetWindow(GW_OWNER)` returns 0 unconditionally — a real
  gap on paper, but 0164 is a **WS_CHILD** (style 44400000), not a WS_POPUP,
  and real USER returns NULL for a child's owner, so 0 is correct here.
- **The "behind our window" symptom is a SEPARATE, real bug — in the HOST
  PRESENTATION model, not win16 z-order.  Proven by construction:**
  - popup 0164 style `44400000` = WS_CHILD|WS_DLGFRAME — it has DLGFRAME
    (0x400000) but NOT the full WS_CAPTION (0xC00000), so
    `compositor.presents_standalone()` is **False** -> NOT promoted ->
    composited into the root frame.
  - Quick Game 014C style `44FD0000` has the full caption -> **promoted** to
    its own OS Toplevel.
  - popup rect x 256..358, y 152..249 lies **entirely inside** 014C's rect
    x 16..439, y 24..370.
  - The root frame sits BEHIND the promoted Toplevel -> the topmost Win16
    window is occluded by a lower one.  **The promotion set is not
    z-order-closed**: any visible child ABOVE a promoted sibling must also be
    promoted, or the OS window stacking inverts the Win16 z-order.
  - **Not fixed this session** (honest residue).  The generic slice is
    z-order closure in `compositor.own_windows`, but it is only correct
    together with a host change (`play.py` must give a caption-less promoted
    window a borderless Toplevel — `overrideredirect` — instead of an OS
    title bar, and must not treat its close box as "quit the app"), and
    tkinter stacking cannot be verified headlessly.  Specified here rather
    than half-landed.
- Queue: (i) **boundary head for MagnifyMenu's ButtonHeld loop** (env-wait
  agent — proven correct above, do NOT skip it on the "hang" theory);
  (ii) popup promotion z-order closure + borderless Toplevel (above);
  (iii) scrollbars (owner's third item, untouched); (iv) an interactive v4
  demo recorded under a driver replays without the new peek-time paints —
  the same live-vs-replay divergence class as the wall clock.

## 2026-07-16 (cont.231) — the "snake shhh" over the music: Windows 3.x MIDI Mapper level filtering (win16_re 0678e0f); the digitized-SFX (waveOut) contract recovered and queued
- **Owner report: MIDI music plays, but a short noise "constantly replays"
  over it — "like some snake shhhh" — under interactive play.**  Triage per
  the standing lifter-first directive came back clean: the strict lifted
  graph and the interpreted oracle issue the IDENTICAL audio API stream over
  the same drive (mci open/status/set/set/play GAMETHME + 59 waveOutOpen
  attempts over cold_nohooks) — not a lifter bug.
- **Ground truth, both shapes**: (i) headless cold_nohooks replay with every
  audio API instrumented: 59 waveOutOpen(WAVE_MAPPER, CALLBACK_WINDOW) calls
  all failing with MMSYSERR_BADDEVICEID (the deliberate stub), sndPlaySound
  resolved by GetProcAddress 59× and NULL-minted (it was registered by
  ordinal only), sound_log EMPTY — zero SOUND.DRV notes, zero WAVs; (ii) a
  240 s interactive-shaped strict run (InteractiveDriver + boundary parks,
  scripted click/F2/enter) with the real backends wrapped: the ONLY events
  reaching host audio are MidiBackend.open+play of GAMETHME.MID.  The
  digitized-effects path is entirely silent today — so the hiss had to live
  INSIDE the MIDI rendering itself.
- **Root cause (measured; independently confirmed by the owner-side A/B —
  the same .mid is clean in a normal media player)**: every one of the 29
  `sound\*.MID` files is dual-format per the Windows 3.x MIDI Mapper
  authoring guideline — the extended-level arrangement on channels 1-10
  (percussion on 10) is mirrored on the base-level channels 13-16
  (percussion on 16).  Verified note-for-note: in ALL 29 files each base
  channel is a time-exact unison copy (13~3, 14~4, 15~5, 16~10).  Under real
  Windows the MCI sequencer ran through the MIDI MAPPER, which passed
  exactly ONE level to the synth; our SDL_mixer stream got the raw file and
  played BOTH — every melody doubled, and the ch16 percussion mirror
  rendered as a MELODIC General-MIDI instrument: program change 126 = GM
  "Applause" (a noise wash), 456 note-ons in GAMETHME.MID ≈ one "shhh" per
  drum hit, continuously.  That is the owner's snake.
- **Fix (generic Win16 platform mechanism, win16_re 0678e0f)**: new
  `win16.audio.midi_keep_channels()` — a pure SMF rewrite keeping only the
  requested channels (dropped events fold deltas into the next kept event so
  absolute times are exact; meta/sysex verbatim; running status resolved;
  Cn/Dn 1-param messages skip correctly; malformed SMF raises) — and
  `MidiBackend` now plays the Mapper's extended-level view (channels 1-10,
  GM-aligned; `level="base"` selectable), filtered once per path, cached,
  loaded via BytesIO.  A file the filter cannot parse plays unfiltered with
  a loud console note.  Presentation-only: mci_log, digests, demos and every
  pinned baseline are untouched (backends are only wired in interactive
  play).  Side effect the owner will hear immediately: the unison doubling
  is gone too — the whole soundtrack plays as authored.
- **Tests**: win16_re `tests/test_midi_mapper.py` (8: dual-level fixture,
  base-level complement, meta survival, delta folding, running status across
  dropped events, 1-param skips, multi-track, fail-loud non-SMF) +
  `test_audio.py` MCI-semantics harness updated (filter stubbed to identity
  there; the rewrite has its own suite).  Game side:
  `simant/tests/test_midi.py::test_gamethme_mapper_filter_strips_the_base_level_mirror`
  pins the real file — note-on census {3:346, 4:182, 5:98, 10:456} + the
  456-hit ch16 mirror with program 126, mirror stripped, extended events
  preserved time-exact.
- **The waveOut/sndPlaySound investigation (closed out honestly — the
  effects are a real capability gap, NOT part of the hiss)**: recovered the
  full observed contract from GR_MODULE disassembly (`_CheckMMWave` 2:7766,
  `_myBeginSound` 2:98B0, `_MciOutWave` 2:959C).  SimAnt's digitized effects
  are 4-bit delta-PCM: a 16-byte delta table (copied `rep movsw` cx=8)
  indexes each nibble, high then low, accumulating into a running 8-bit
  UNSIGNED sample seeded 0x80 — two output samples per input byte.  The
  game's OWN declared format is **4096 Hz mono, 8-bit** — PCMWAVEFORMAT
  {1,1,0x1000,0x1000,1,8}, written literally at 2:9D72..9D99 and rebuilt
  identically in the RIFF it hands sndPlaySound.  Cross-check: the 57
  pre-extracted WAVs in artifacts/sounds_wav/ (gitignored, provenance not
  ours — NOT evidence of our decode) are 8-bit unsigned mono with DC
  centred at ~128, independently corroborating the 0x80-seeded unsigned
  model; but they are tagged **11025 Hz**, i.e. the extractor's choice, NOT
  the game's declaration.  When the SFX slice lands, 4096 Hz is what the
  disassembly proves and 11025 is a claim to re-derive, not inherit.
  Flow: waveOutOpen(&hWaveOut@8D14, WAVE_MAPPER=0xFFFF, &fmt, hwnd@DGROUP
  :CD78, dwInstance=0, CALLBACK_WINDOW=0x10000) → _MciOutWave: GlobalAlloc
  WAVEHDR+data, DPCM decode, waveOutPrepareHeader, waveOutWrite; busy
  window = GetTickCount() + waveOutGetPosition(TIME_MS) stored at
  DGROUP:1238/123A and re-checked at 2:9C0B; the game PUMPS
  PeekMessage(&msg, NULL, 0x3BD, 0x3BD, PM_REMOVE) — MM_WOM_DONE — and its
  WndProc unprepares/frees/closes (a refcount at 8D1A gates waveOutClose).
  Note the gate is the LoadLibrary HANDLE at 8D08, not the device count:
  `_CheckMMWave` leaves 8D08 set even when waveOutGetNumDevs returns 0, so
  the game attempts waveOutOpen regardless — which is exactly why our stub
  sees 59 of them.  On open failure it builds a complete
  in-memory RIFF/WAVE and calls sndPlaySound(lpMem, SND_MEMORY|SND_NOSTOP=
  0x14) — resolved by NAME, so minting it would light effects up via the
  existing play_wav path.  **Deliberately NOT enabled this slice**: either
  route changes guest control flow from ~instr 41M — inside the pinned 45M
  cold_nohooks prefix — so every instruction-keyed demo desyncs and every
  pinned digest moves: enabling digitized SFX is its own slice with the
  re-record + re-pin ceremony (and WOM_DONE needs a deterministic virtual-
  clock completion model, not a host-audio callback).
- **Gates**: win16_re 182 passed (includes the concurrent resize-agent's
  in-tree tests), simant_port 2255 passed; live verification: the strict
  interactive run plays the filtered GAMETHME through SDL_mixer (banner
  names the filter; play #1 clean).  Commits: win16_re 0678e0f + the port's
  test/journal/submodule bump (this entry).  Scratch evidence:
  audio_probe.py / audio_live_repro.py / midi_inspect.py in the session
  scratchpad.

## 2026-07-16 (cont.229) — env-waits parked: interactive play_vmless past the splash/intro/title into gameplay; the wait class mechanically enumerated
- **The "hang at the Maxis logo" was never a hang: the CPU worker died with
  `LiftRuntimeError: gr_ibminitstuff exceeded MAX_ITERATIONS` at 0E99:07F3
  (~instr 523M; re-verified headless) while the GUI thread kept the window
  alive.**  A textbook env-wait (dos_re_2.0 §3a): `GR!_IBMInitStuff`'s
  splash-timeout loop (0E99:07E6..07F8) spins on `_WaitedEnough` →
  `_TickCount` → GetTickCount against a 0x48-tick deadline — but the
  interactive WALL clock only advances between cpu.run chunks
  (driver.check_pause), and one lifted invocation never returns to the
  worker, so the spin's clock is FROZEN: it cannot exit, and the runaway
  guard (correctly) kills it.  Demo replay never trips this — the recorded
  clock advances per instruction count.
- **The fix is dos_re's OWN designed machinery, not new mechanism: boundary
  heads (dos_re 1dd8b8d/186003d) + an interactive park policy.**  Each
  fact-declared wait-loop head gets an emitted observer event + RESUME
  entries; `resume_calls` goes pipeline-wide (THE UNWIND RE-ENTRY RULE —
  every call-site continuation in every module is a resume entry, ~7.9k
  resume hooks registered by dos_re's installer).  New in win16_re
  (generic): `InteractiveDriver.arm_boundary_parks(cpu)` — the observer
  sleeps the pacing cost (1 ms), re-points CS:IP at the RESUME entry and
  raises `BoundaryParked`; EVERY step loop treats it as a YIELD, not a
  stop: the CPU worker (play.py `_run_cpu` → `driver.boundary_yield()` =
  check_pause, advancing the clock the wait polls) AND
  `win16.callback.call_far`'s chunk loop (a wait reached INSIDE a
  WndProc/TimerProc parks there and the callback still far-returns
  normally — proven live: `_DoMouse` → `_WaitHundredths` mid-scenario,
  crash_214734).  The next step() re-enters the lifted body at the resume
  entry; abandoned outer lifted frames re-establish through their own
  continuation resume hooks as the guest stack unwinds — zero interpreted
  instructions.  Headless/demo runs never arm the hook: the emitted
  observer is inert.
- **Second manifestation, interpreted path (owner live, crash_214204):
  `CallbackOverrun: callback 0100:2930 did not return within 20000000
  steps` — the title screen polling GetAsyncKeyState/GetCursorPos INSIDE
  MAINWNDPROC killed as a "runaway" while the user idled.**  The policy
  seam already existed (`Win16System.callback_max_steps`; the interactive
  driver sets None because a live wait is user-interruptible) but only
  DispatchMessage's TimerProc branch honoured it.  Fix (win16_re):
  `call_wndproc`, the dialog-proc dispatcher and EnumChildWindows now pass
  the SYSTEM's budget — one policy, every callback dispatch site; no
  constant raised anywhere.  Headless/replay keeps the 20M cap.
  Regression tests pin all of it (failing on old code):
  tests/test_callback.py (budget policy ×2 + park-inside-callback),
  tests/test_boundary_park.py (repoint+raise, clock advance, the whole
  spin-exits-through-parks model).
- **Owner triage directive (now standing): every observed glitch is FIRST
  suspected to be an automatic-lifter bug, answered by an oracle
  differential at the site, never by plausibility.**  Applied:
  `_WaitHundredths` per-call A/B (fresh machine, interpreted vs lifted,
  headless clock, 50-tick wait): identical AX/DX/SP/flags/instruction
  count (50,011) — the lifted loop is faithful; the trips are genuinely
  the clock model.  The whole-demo differential (below) covers every other
  parked body byte-exactly through splash+intro+dialogs+pacing.
- **Owner directive escalation: stop fixing crashes one at a time — the
  class is mechanically enumerable.  NEW `scripts/waitscan.py`**: scans the
  recovery IR for loops (backward jcc/jmp) reaching a polling-class api
  (GetTickCount/GetAsyncKeyState/GetKeyState/GetCursorPos/PeekMessage,
  call closure ≤2), derives each loop's head, and classifies HONESTLY:
  `wait` (every call in the loop targets a WAIT PRIMITIVE — the fixpoint-
  derived tick/input leaf helpers + the evidence-curated pump/dialog-wait
  family `_win_GetEvent`/`_win_Events`/`_win_FlushEvents`/`_Dialog*`/
  `_myDelay`/`_myButton`/`_StillDown`) vs `mixed` (any other call = real
  per-iteration work possible — NEVER parked mechanically; a sim loop
  reading the clock is timestamping, not waiting).  Census: 183 candidate
  loops in 129 functions → **25 derived wait heads**
  (simant/facts/boundary_heads_derived.txt, one evidence comment each;
  freshness pinned by `--check`, suite-held) + 158 mixed reported in
  artifacts/wait_report.json (the review queue).  Facts split:
  boundary_heads.txt now holds ONLY evidence-promoted mixed loops —
  `_ShowIntro` 1:D4BE (intro event-poll; headless crash at 0100:D4C7) and
  `_PictureDialog` 1:6418 (dialog event-poll; headless crash at 0100:6422)
  — each with loop shape + crash evidence; irgen reads both files.  The
  live-crash sites all land in the set: splash 0E99:07E6, _WaitHundredths
  0E99:4A08, _processEdit tick-spin 0100:6A88 (crash_220104), _DoScenario
  idle pump 0100:59C3.  simant/tests/test_waitscan.py pins freshness, the
  evidenced heads, and that MAINWNDPROC/WINMAIN/sim-frame loops are NOT
  mechanically parked.
- **Regenerated end to end** (irgen → liftemit → liftlink → adaptgen) with
  27 heads: 1903/1904 modules, VMless wall HOLDS (0 interp_one), linking
  unchanged (1730 near + 3209 far into 1271 callers, emulate_call 2550→73),
  adaptgen re-routes into graph_routed as before.  scripts/liftemit.py and
  scripts/liftlink.py read the heads from the IR's own `facts_applied`
  (ONE source; liftlink materializes dos_re's @FILE flag as a generated
  artifacts/boundary_heads_para.txt) — the linker's re-emitted callers
  carry the identical observer/resume configuration.
- **The gates (all green, re-proven on the 27-head graph, post-cont.228):**
  - my own fresh interpreted EXE-full oracle (scratch): all aligned
    checkpoints byte-identical; final stop-point 269eb6db… (plain) /
    05c738b2… (masked) — independently confirming cont.228's re-pinned
    baseline (the InvertRect device-domain fix moved the stop-point
    surface state; the earlier 3-head round of these same gates, run
    PRE-52691da, matched the PRE-fix final 2abf3ce5…/43bd4596… the same
    bidirectional way — each round self-consistent, cause fully attributed);
  - CONTROL, literal graph vs oracle, plain digests: **MATCH all 39
    checkpoints + final** (269eb6db…);
  - WALL RUN, boot-image + poison, masked digests: **MATCH all 39 +
    final** (05c738b2…);
  - `play_vmless --demo cold_nohooks`: 199,612,996 instructions, wall
    HOLDS, final digest 05c738b2… == the masked oracle final;
  - suites: dos_re 513, win16_re 166, simant_port 2254 — all green
    (win16_re/dos_re trees carry no game knowledge: the park machinery is
    generic, the heads are simant facts).
- **Interactive verification (headless repro, wall-clock driver, strict
  machine): the game now PLAYS.**  Idle 240 s: zero VM stops, 153k parks,
  splash → intro → title (windows evolve exactly as the interpreted
  control: SimAnt/Ribbon/Root + Generic Windows, one visible), idle parked
  in _DoScenario's pump at 0100:59C6 polling GetAsyncKeyState — where the
  control also sits.  Scripted 300 s click-through (click/F2/enter/clicks):
  zero stops, all input consumed, same stable endpoint, 193k parks.  Owner
  live progression during the work: splash → menus → NewGame →
  DoScenario → in-game mouse handling — each formerly-fatal wait now
  parking (their session died only at the next UNPARKED wait of the day,
  which is how the last two facts were found and is exactly the
  crash→fact→regen loop waitscan now replaces with enumeration).
- **UX hardening — a dead CPU worker is now unmissable:** play.py prints a
  `====`-framed VM STOPPED banner (status, CS:IP, instruction count, "the
  game window is now FROZEN — close it to exit") before the traceback;
  every surviving game window gets a red stop banner AND a
  `[VM STOPPED — see console]` title prefix (the old banner only attached
  to the is_main frame, which doesn't exist during the splash — exactly the
  silent case); play_vmless prints a closing VM-STOP verdict as its LAST
  console output and exits 1.  Proven live by the owner mid-session.
- Honest residue / queue: (i) 158 mixed wait candidates in
  artifacts/wait_report.json — promotion is evidence-driven (a crash or an
  A/B), one line in boundary_heads.txt each; (ii) the pacing feel of parked
  pumps (1 ms observer sleep per pass) is untuned — if a menu/animation
  paces oddly, per-head park costs are the dos_re-designed next step
  (186003d's frame_gate vs pacing_spin); (iii) **dist/ is stale**: the
  packaged v0.1.0-pre bundles the graph + play host + win16 tree — the
  deploy (scripts/deploy_vmless.py) must be re-run to pick up the parked
  graph, the loud-stop UX and cont.228's GDI fix (not rebuilt this
  session); (iv) demos remain hook-config-specific; the strict runner
  still refuses snapshot-anchored demos.

## 2026-07-16 (cont.228) — nest/map-view drag rectangle visible again: InvertRect now inverts in the 16-colour device's palette-index domain (win16_re 52691da, re-pin b04a164)
- **Owner report**: in the Black Nest View the selection/drag rectangle all but
  disappears over the medium-grey background — under OTVDM AND under us,
  because inverting RGB (128,128,128) per channel gives (127,127,127).
  Directive: trace the real drawing path first, no blind patching; OTVDM is
  NOT the reference — original 16-colour Windows is.
- **The actual operation (traced, not assumed)**: the IR has exactly ONE
  InvertRect call site — `_GInvBox` (GR_MODULE 0E99:1966, `enter; normalize
  rect; push hdc[DGROUP 0xCF52]; lea rect; call USER.82`).  Callers:
  `_GRectInvOutline` (the 4-sided rubber-band outline) ← `_DrawMapCursor` /
  `_EraseMapCursor` / `_ToggleMapCursor` / `_win_DrawMapWindow` /
  `_OpenMiniMapWin` / mini-map variants (ANTEDIT), plus `_GRectInv`
  (SIMTWO object select) and `_DrawYardData`/`_DrawMapData`.  So the drag
  rectangle is pure **USER.82 InvertRect** — no SetROP2/PatBlt/XOR pen.
  Live probe over cold_nohooks: 4,992 InvertRect calls, first at instr
  36.8M, classic draw/erase toggle pairs on the same rect.
- **Not a lifter bug** (owner's standing first question): none of
  _GInvBox/_GRectInvOutline/_Draw|Erase|ToggleMapCursor is an island or in
  the lifted graph's hot set with GDI effects — and OTVDM (real Windows GDI)
  reproduces the same washout, so the gap was GDI-layer semantics.
- **What we computed vs the real device**: win16 InvertRect did
  `pixels ^= 0xFF` per RGB channel on the truecolor Surface → 128→127,
  invisible.  Real Windows 3.x 16-colour VGA inverts the 4-bit PHYSICAL
  palette index (DSTINVERT = idx ^ 0xF): grey is physical 8, NOT 8 = 7 =
  light grey (192,192,192) — clearly visible.  SimAnt's own realized palette
  measured at boot (CreatePalette→SelectPalette→RealizePalette): entries
  0..15 are exactly the standard Windows 16-colour set (grey at 7, light
  grey at 8 — same complement pair, opposite order), so the device-domain
  result is what the original displayed.
- **Fix (option A, generic platform mechanism — win16_re 52691da)**: new
  `DEVICE_PALETTE_16` constant + `invert_rect_16color()` in win16/api/gdi.py;
  USER.82 now nearest-matches each destination pixel into the 16-colour
  device palette and writes the entry at index ^ 0xF.  Involutive on device
  colours (draw/erase restores exactly); a non-device colour snaps to its
  nearest device entry — what the real 4-bit device would have shown.  No
  game knowledge in win16_re; blit()'s SRCINVERT/NOTSRCCOPY (the verified
  monochrome-mask sprite path) deliberately untouched.  Regression suite
  `win16_re/tests/test_invert_rect.py` (7 tests): grey → visibly-different
  light grey, black↔white, all 16 complement pairs, toggle involution,
  snap stability, clipping, degenerate no-op.
- **Gates**: win16_re 166 passed; simant_port 2250 passed; visual check
  (first InvertRect crop, intro ant portrait) inverts to exact device
  complements.  The map-cursor InvertRects land inside the pinned 45M
  cold_nohooks prefix, so the clean-room digest moved — **re-pinned**
  e9e4aafd… → 50365479… (end instruction UNCHANGED at 45,102,443;
  presentation-only).  Attribution proven by reverting only the GDI hunks:
  old digest reproduces byte-exact.  Whole-demo differential re-run
  post-fix: strict boot-image vs fresh interpreted oracle, cold_nohooks,
  api-aligned masked digests — **MATCH all 39 checkpoints + final**
  (199,612,996); local `artifacts/cp_interp_aligned_masked.trace` refreshed
  to the post-fix oracle.

## 2026-07-16 (cont.227) — v0.1.0-pre: the strict-VMless runner packaged standalone (dist folder + PyInstaller EXE)
- **NEW `scripts/deploy_vmless.py` (+ `scripts/simant_vmless.spec`) — the
  pre2 `deploy_native.py` shape adapted to Stage-1 VMless: compute the import
  closure of the runner surface (play_vmless + play + vmless_boot, the same
  package-dir mapping the M3 lint uses; package `__init__.py` files are
  WALKED, not just copied — dos_re/lift/__init__ imports decode/cfg), deny
  what must not ship, copy closure + generated data + launcher + README +
  requirements into `dist/simant_vmless/`, smoke-test in an isolated
  subprocess, and with `--exe` run PyInstaller (onedir, console=True) and
  smoke-test THE BUILT EXE the same way.**  `VMLESS_RELEASE = "0.1.0-pre"`
  lives in play_vmless.py and prints in the runner banner.
- **The deny-list is the inverse of pre2's**: dos_re (the CPU carrier) and
  win16 (the Python OS layer) ARE the runtime and ship whole; denied are the
  EXE-boot entry points (`simant.runtime`, `win16.app` — lazy uses in shipped
  files fail loud), the interpreter-era islands (`simant.hooks`), the RE
  workbench (probes/tests/bridge/native/recovered, win16 irgen/callgraph/
  apicoverage/tick_demo, the dos_re lift EMIT toolchain + verification tiers).
  `win16/ne.py` + `win16/loader.py` ship as CLASS CARRIERS (program.pickle
  holds an NEExecutable, the machine is a Win16Machine) — reachable on the
  lint-proven graph, never called to parse anything.  The emitted graph's own
  load-time imports (dos_re.cpu/hooks/lift.runtime) are DERIVED by scanning
  the generated modules (they ship as data, invisible to the AST walk); the
  spec derives its hiddenimports the same way.
- **Loader-free interactive host**: `GAME_NAME`/`demo_out_path` moved to
  `simant/vmless_boot.py` (simant.runtime re-exports); `scripts/play.py`'s
  module level no longer imports simant.runtime/win16.app (the EXE-boot and
  --resume branches import them function-locally, fail-loud in the dist) —
  and play.py is now a third ROOT of `scripts/lint_vmless_independence.py`,
  so the whole shipped surface is lint-proven loader-free (24 module edges,
  2 deferred loader imports reported INFO, PASS).
- **What ships**: 55 closure code files (~1 MB), the data-only boot image
  (4.3 MB), the 1903-module lifted graph as plain DATA files (loaded by path
  at run time; ~25 MB), cold_nohooks.jsonl as the reference demo, launcher
  (`simant_vmless.py`), README.md (v0.1.0-pre: data-files-only requirement,
  SIMANTW.EXE explicitly NOT needed, --game-root, controls, limitations),
  requirements (pygame/numpy/Pillow — numpy+pygame verified via win16.audio,
  Pillow/tkinter via the play host).  Dist tree 30.5 MB / EXE onedir ~150 MB
  (tcl/tk + numpy + pygame + the win16_re data tree).  The deploy ASSERTS no
  executable ships in any disguise (suffix, MZ head, or content hash ==
  the manifest's source EXE) and prunes smoke `__pycache__` before bundling.
- **PyInstaller layout (the real risks, solved properly)**: the graph + boot
  image bundle at their repo-relative destinations, so `simant.vmless_boot`'s
  `__file__`-derived defaults resolve inside `_internal` (= `sys._MEIPASS`)
  unchanged; the deny-filtered win16_re source tree also ships as data and
  the launcher points the documented `WIN16_RE_PATH`/`DOS_RE_PATH` escape
  hatches at it so the `_env` bootstraps' existence checks pass under frozen
  (module resolution still uses the byte-identical frozen modules —
  PyInstaller's importer runs first).  The feared frozen-unpickle bug did not
  materialize: system.pickle/program.pickle load fine because win16.api.* /
  win16.ne are frozen importable — VERIFIED by the exe smoke, not assumed.
- **Smoke tests (MANDATORY, both green)**: isolated subprocess, sys.path =
  the dist tree only, cwd = a temp dir, SIMANTW.EXE physically ABSENT, env
  scrubbed (PYTHONPATH + the *_PATH hatches); replay the pinned
  45M-instruction cold_nohooks prefix (the walls-test recipe/constants,
  single-sourced from test_vmless_walls.py) and require the release banner,
  BOTH wall banners, the pinned digest AND instruction count, zero
  deny-listed modules in sys.modules, and simant/win16/dos_re resolving
  inside the dist.  Results: dist tree AND built exe both end
  `instructions 45,102,443 / digest e9e4aafd… / EXE-independence wall: HOLDS
  / VMless wall: HOLDS`.  Frozen INTERACTIVE mode boots too (30 s run:
  banner, 1903 modules installed, pygame audio + MIDI up, no stderr).
- Suite: `simant/tests/test_deploy_vmless.py` (5 tests — closure spine +
  deny assertions, derived graph deps, the no-executable assert catching
  suffix/MZ/hash disguises, release-constant/pin coherence); .gitignore
  gains dist/ + build/; README gains the "Standalone pre-release" section.
- Honest residue: (i) the routed-adapter flavor is NOT shipped (cont.223 —
  separate build, no virtual-time preservation); (ii) the launcher's
  interactive default needs the game DATA dir (--game-root or files next to
  the launcher; marker = SHARED.DAT/SIMANT.CFG) — without it the first INT
  21h open fails loud; (iii) play.py's --resume/EXE-boot branches
  intentionally ImportError in the dist (denied edge); (iv) the exe smoke
  asserts from OUTSIDE (console text) — the in-process deny check runs on
  the dist-tree smoke only.

## 2026-07-16 (cont.226) — API coverage report: the IR's api:* surface joined against the registry + a counted strict replay
- **NEW `win16/apicoverage.py` (generic, game-free) + `scripts/apicoverage.py`
  (the SimAnt wrapper) -> `artifacts/api_coverage.json` (gitignored,
  regenerable) + a human table.**  Joins three sources per API target:
  the recovery IR's `api:MODULE.ord[:Name]` platform-effect call sites
  (static usage, deduped by global (CS,IP) — dispatch-fact/closure records
  overlap their containing functions, so per-record counting double-counts;
  attribution prefers the non-generated record), the ApiRegistry's
  implemented surface (handler / handler-raw / equate / tripwire — a slot
  with no handler raises Win16ApiGap), and RUNTIME dispatch counts from an
  instrumented replay on the STRICT VMless runner (the shipping config:
  boot-image machine, poison armed, EXE guarded).  Instrumentation =
  counting wrappers around every thunk-slot replacement hook +
  `mint_proc_thunk` (GetProcAddress mints wrap lazily as they appear;
  NULL mints recorded as honest misses) + `cpu.interrupt_handler`
  (per-service: `int21:<AH>` / `int2F`).  Identity resolution order:
  Wine-spec ordinal table -> registry entry name -> handler `__name__` ->
  the IR tag's name part -> **`unnamed`, honestly — never guessed from
  Win3.x ordinal folklore**.  win16_re: 8 synthetic-machine tests
  (mock CPU + real ApiRegistry: site dedupe, honest identity, status
  partition, live slot/proc/int counting incl. idempotent re-instrument
  and the tripwire fail-loud path, report classification, table shape) —
  no game knowledge upstream, layering intact.  simant side: 4 tests
  pinning join invariants on the real artifacts (every statically-called
  target is an import; status partition covers targets; honest unnamed;
  int21+int2f static surface present).
- **The run (full cold_nohooks on the strict runner, wall held, all
  199,612,996 instructions / 4212 events): 196 imported targets = 165
  implemented + 3 equates (__AHSHIFT/__AHINCR/__WINFLAGS) + 28 tripwires;
  1353 static call sites; 128 targets exercised at runtime, 37
  implemented-but-never-exercised; 25 unnamed (ALL of them unreached
  tripwires — zero runtime dispatches, so nothing the demo proves is
  missing a name or an implementation).**  Name sources: 161 ordinal-table
  + 8 handler-`__name__` (GlobalFlags/GlobalCompact/GlobalLRUOldest/
  GetFreeSpace/lstrcat/lstrlen/EnumChildWindows/DlgDirList) + 2
  registry-entry.  Top runtime dispatch: GetTickCount 1,249,019,
  PeekMessage 258,081, GetAsyncKeyState 179,245, SelectObject 162,672,
  SelectPalette 162,055.
- **The stub-quality risk set (implemented, never exercised by this demo —
  where a new demo/gameplay path could expose behavioral gaps), top 10 by
  static sites: MessageBox 20, SendDlgItemMessage 15, KillTimer 13,
  _lwrite 10, WriteProfileString 8, WinHelp 8, GetClassWord 7,
  UnlockSegment 6, FreeProcInstance 6, CloseSound 6** (37 total — the
  file-dialog/save-DB tier (OPENDLG/SAVEASDLG/_DBAdd), the help path, the
  SOUND.DRV voice tier, and shutdown paths the demo never reaches).
- **Dynamic (GetProcAddress) surface**: minted + called =
  `MMSYSTEM.mciSendCommand` (5: the MIDI music path),
  `MMSYSTEM.midiOutGetNumDevs` (1), `MMSYSTEM.waveOutOpen` (59);
  `MMSYSTEM.sndPlaySound` requested 59x and honestly NULL-minted (not
  implemented) — the game takes its documented waveOut fallback each time;
  7 more waveOut procs implemented but never minted by this demo.
- **Findings the instrumented run surfaced**: (i) `WIN87EM.1:__fpMath` has
  ZERO static api:* sites (it is reached through a relocated pointer, not
  a decoder-taggable far call) yet dispatched 2x at runtime — the join's
  union keyed on slots+runtime catches what static tagging cannot;
  (ii) int21 static sites after (CS,IP) dedupe = 45 distinct (the
  capability report's 67/68 counts per-record across the overlapping
  internal_*/case_* closure entries — both true, different units);
  runtime int21 services: 3F(read) 47, 43(chmod probe) 8, 3D(open) 6,
  3E(close) 4, 44(ioctl) 4, 19/47/25/30/35 few — plus int2F 76 (the TSR
  probe, honest no-op); (iii) 3 NAMED tripwires exist (FatalExit,
  FatalAppExit, LocalReAlloc — fatal-error paths + 2 LocalReAlloc sites),
  fail-loud by design; (iv) 47 registry handlers are unreferenced by
  SIMANTW entirely (surface other games proved).
- Suites: win16_re 153 green (145+8), simant_port 2245 green (2241+4).
  README pipeline section gains the apicoverage subsection.
- Queue: the 37-target risk set is the natural demo-recording checklist
  (a save/load + help + dialog demo would exercise the biggest tier);
  `sndPlaySound` is the one dynamic proc the game actively wants and we
  NULL — worth an implementation decision when sound fidelity matters.

## 2026-07-16 (cont.225) — M3: play_vmless with BOTH hard walls — VMless execution + EXE independence
- **The strict runner exists and both walls HOLD over the whole cold_nohooks
  demo: `python scripts/play_vmless.py --demo cold_nohooks` boots from a
  generated data-only boot image (no NE parse, no SIMANTW.EXE read — the
  clean-room test runs with the EXE physically absent), installs the full
  1903-module graph, arms `cpu.interp_forbidden` from instruction zero, and
  replays all 199,612,996 instructions with ZERO interpreted instructions.**
  Three-repo chain: dos_re 793b754+92acb0f, win16_re d3e7bad (pin bump +
  bootimage), simant_port (this commit + the wall-1 closure commit).
- **Wall 1 — the runtime closure to an empty frontier.**  The collect-mode
  audit (poison records instead of raising) over the whole demo drove three
  closure mechanisms:
  1. **`scripts/dispatchgen.py`** — mechanical switch-table derivation: 47
     jmp_ind table sites proven from their own guard code (three MSC
     families: F1 `cmp ax,N/ja/shl/xchg`, F2 inverted `jbe +3/jmp DEFAULT`,
     F3 scaled-compare-no-shl with even bound), 934 slots read from the
     image, 547 distinct case-body census entries as evidence-backed facts
     (`simant/facts/dispatch_entries.txt`) + the table byte ranges
     (`code_as_data.txt`).  8 register-indirect CRT tails STOPPED and
     reported, never guessed (`jmp [di]`/`jmp bx`/far-through-pointer:
     sqrt/trandisp/chkstk/setargv/LD12MULTTENPOWER + `__output`).
     dispatchgen⇄irgen iterate to a FIXPOINT (2 iterations — closure-added
     `internal_D7A0` carries its own table); `--check` pins the committed
     facts to a fresh derivation (suite-held).
  2. **irgen census closure** — static near/far CALL targets inside code
     segments that are no .SYM entry join mechanically as `internal_<OFF>`
     entries (34: the CRT-internal labels; fixpoint).  Hand-verified facts
     (`simant/facts/indirect_targets.txt`, 10): `__output`'s 8-slot
     state-machine table (bound not guard-provable; word 9 decodes as code —
     manually verified, MSC printf's 8 states) and 2 CRT initializer-table
     far-call targets observed by the collector.  IR now 1904 functions /
     1903 liftable; sole refusal stays `_DoInt3` (dead).
  3. **The chkstk finding (the deep one): `emulate_call` can NEVER terminate
     on a frame-allocating computed-return callee.**  First collect run:
     5,668 interpreted addresses — almost all one cascade: `__output`'s
     `push cs; call __aFchkstk` opened an emulate frame whose done()
     requires SP at/above the call point, but chkstk's whole job is to MOVE
     SP BELOW it (pop far return, probe, `mov sp,ax`, push-back + retf) —
     the frame never closed and ~180M instructions ran interpreted inside
     it (semantically correct, structurally wrong — the island convention's
     blind spot found live).  Fix upstream (dos_re 793b754): port-declared
     COMPUTED-RETURN linker facts (`--computed-return-near/far`) qualify
     such callees for linking — the lifted body reproduces the exact SP
     trajectory and the linked helper continues at the very continuation
     the computed jump targets.  `simant/facts/computed_return.txt`:
     `__aNchkstk` (near), `__aFchkstk` + `__setargv` (far).  E2E dos_re
     test: chkstk-shaped link runs byte-exact vs the interpreter.
  - Collect runs: 5,668 → 492 → **0** frontier addresses.  Verified with
    the wall ARMED: full demo, EXE-free, `VMless wall: HOLDS`.
- **Wall 2 — the EXE goes in at build time and never again.**
  - **win16_re `win16/bootimage.py` (generic)**: `build_boot_image` captures
    a freshly loaded NE machine (canonical entry = instruction 0 — the NE
    loader does its work at load time; no in-VM loader phase to interpret)
    as a vmsnap snapshot + an EXE-free **program identity** (parsed NE minus
    `raw`; NE resources carry their own bytes, so menus/dialogs/bitmaps work
    fileless) + a manifest (provenance, seg map, the resolved API import
    slot table — assigned in relocation order, NOT re-derivable without the
    EXE, so it is data in the manifest and cross-checked against the fresh
    loader-free registry at load), then **poisons**: every IR-decoded
    instruction byte zeroed (328,070 bytes / 2,410 runs) minus declared
    `code_as_data`, and — the win16 strengthening — **stage 2 zeroes every
    remaining nonzero undeclared code-segment byte** (3,288: alignment
    NOPs, statically unreached FP-dispatch bodies, dead tails, undeclared
    inline data), so a code segment carries NOTHING but declared data.  A
    `code_as_data` fact may not overlap decoded instructions (fail-loud).
    `load_boot_image`/`boot_vmless_machine` = the EXE-free path
    (`win16.api.surface.build_registry` split out of `win16.app` so the
    loader is off the strict import graph); `audit_boot_image` +
    `independence_report` + `mask_ranges_from_manifest` complete the layer;
    `vmsnap` gains `restore_machine_state` + `digest(mask_ranges=)`.
  - **Facts**: `code_as_data.txt` (47 derived jump tables, generated) +
    `code_as_data_manual.txt` (5 hand-verified data tables with evidence:
    `__output`'s state table, the FP long-double constants, `$i8_output`'s
    pow10 tables, `_font_MakeImage`'s mask byte table).  An omitted
    read-as-data range cannot pass silently — the masked whole-demo
    differential diverges at the first checkpoint after the zeroed data is
    read.
  - **Guards + proofs**: `dos_re.independence.exe_access_guard` (by name AND
    content hash) wraps every strict session; dos_re's lint gained
    `--package-dir` mapping + walk-hole failure + bare-suffix-literal fix
    (92acb0f) so `scripts/lint_vmless_independence.py` PROVES the strict
    import graph (play_vmless + simant/vmless_boot + win16.bootimage) never
    reaches `parse_ne`/`load_ne`/`create_machine`/`load_snapshot` or an EXE
    path literal; `scripts/audit_vmless_boot_image.py` PASSES (no bundled
    executable, full poison, code-segment coverage).
- **The acceptance gate — the demo replay vs the oracle.**  A poisoned-image
  digest legitimately differs from the EXE-full oracle digest by exactly the
  zeroed bytes, so `checkpoints.py` gained `--boot-image` (drive the strict
  machine through the harness), `--mask-poison` (record `mdigest` — the
  digest with the manifest's zeroed ranges masked) and `--check-field`.
  Results over cold_nohooks, api-aligned, 5M interval:
  - fresh interpreted EXE-full oracle: final instr 199,612,996, plain digest
    **2abf3ce5…** (the historical chain digest — unchanged), masked digest
    **43bd4596…**;
  - CONTROL, literal EXE-full graph (1903 modules) vs oracle, plain digests:
    **MATCH all 39 checkpoints + final 2abf3ce5…** (the enlarged corpus is
    still byte-identical);
  - **THE WALL RUN**: boot-image machine (EXE-free load, poison armed) vs
    oracle, masked digests: **MATCH all 39 checkpoints + final** (instr
    199,612,996, mdigest 43bd4596… — and `play_vmless --demo`'s plain final
    digest equals it, since the mask is a no-op on the poisoned side).
  - `play_vmless --demo cold_nohooks --collect-frontier`: frontier EMPTY.
  - Interactive `play_vmless` boots and runs under both walls (smoke-tested).
- **Suite-held walls** (`simant/tests/test_vmless_walls.py`, 7 tests): the
  static lint, the boot-image audit, manifest wall-derivation, dispatchgen
  fact freshness (`--check`), the exe-access guard (name + renamed-hash),
  and the CLEAN-ROOM replay: temp dir, game data copied, EXE physically
  absent, poison armed, 45M-instruction demo prefix (918 input events:
  boot→intro→menus→NewGame→worldgen→gameplay) pinned to instr 45,102,443 /
  digest e9e4aafd… (~19 s).  Suites: simant_port 2241, win16_re 145,
  dos_re 513 — all green.
- Honest residue / queue: (i) the 8 STOPPED register-indirect CRT tails
  stay outside the derivable-table set — their runtime targets are census
  entries or emulate-return continuations (frontier empty proves it for
  this corpus); a future demo touching an uncovered FP-dispatch body fails
  loud at the wall, and the closure move is a
  runtime-observed/indirect-target fact, exactly as designed; (ii) the
  routed-adapter flavor (adaptgen) regenerates fine but stays a separate
  build (it does not preserve virtual time — cont.223), so play_vmless's
  demo gate runs the literal graph; routing under the strict walls is the
  next dissolution step; (iii) statically unreached code zeroed by poison
  stage 2 (chkstk error paths, x87-present sqrt bodies, two unnamed pack
  helpers, `_EditMultipleScroll*Asm` tails, `_MemRChr`'s scrambled-looking
  tail bytes) is enumerated in the manifest's `undeclared_runs` — if any is
  ever READ as data the masked differential will finger it; (iv) demos are
  hook-config-specific still — the strict runner refuses snapshot-anchored
  demos (cold demos only) until the semantic-boundary clock lands.

## 2026-07-16 (cont.224) — the enlarged corpus: native x87 + push-cs linking land through the chain
- **Integration of dos_re 24b4366 (irgen-core = origin/main): the two new
  generic capabilities — native x87 ESC scan/emit via the interpreter's own
  FPU helpers, and push-cs near-call-to-retf linking — become the committed,
  verified state of the whole chain.**  win16_re bumps dos_re
  5adcdbc → 24b4366 (suite green, 136); simant_port bumps win16_re to that
  commit and makes facts/tests/artifacts coherent in the same commit.
- **The x87 census frontier DISSOLVED — exactly as the keep-interpreted
  charter prescribed.**  `simant/facts/keep_interpreted.txt` drops the 31 x87
  addresses (32 SYM names incl. the `__aFftol`/`__ftol` alias: the MSC FP
  runtime in `_TEXT` + `_AdjustWndMinMax` in seg 1); they lift natively now.
  What remains is ONE fact: `_DoInt3` (7:F85B) — still scan-refused (grp5
  invalid /r: the body decodes straight into 0xFFFF padding, i.e. it is not
  code) and confirmed DEAD in the fresh IR (zero static callers: no
  calls_near/calls_far edge targets 430E:F85B).  It stays in keep_interpreted
  with that evidence in the comment rather than in new exclusion machinery —
  keep-interpreted already expresses the right contract (outside the graph,
  fail-loud if ever reached).
- **Regenerated end to end** (irgen → liftemit → liftlink → adaptgen):
  - IR: 1319 SYM entries → 1313 functions, **1312 liftable** (was 1281),
    1 unsupported over 1 refusal record (`_DoInt3`).
  - liftemit: **1312/1313 modules**, VMless wall HOLDS (0 interp_one sites).
  - liftlink: **1191 near + 2353 far edges into 795 re-emitted callers**
    (was 93 near + 2349 far into 606), emulate_call in re-emitted callers
    1799 → 124.  The 1088-edge `near-call-to-retf-callee(push-cs-idiom)`
    class is GONE — dos_re's linker claims the idiom directly — leaving a
    one-edge honest residue in its own class,
    `retf-callee-no-push-cs-idiom`: `__fpsignal -> __amsg_exit` (a genuine
    near CALL into a retf callee with NO `push cs` before it; `__amsg_exit`
    is the CRT's noreturn fatal-error exit, so the mismatched return frame
    can never unwind).  The `keep-interpreted-fact` blocked class vanished
    with the frontier (edges into the FP runtime now link);
    simant's classifier drops its now-dead push-cs branch
    (`scripts/liftlink.py`).
  - capability report: api 1265 call sites over 191 imports (USER=718
    KERNEL=398 GDI=133 SOUND=9 KEYBOARD=7 — up from 1248: the newly
    liftable x87 modules' call sites now count); int21_dos 67 + int2f 8;
    exit-shape (jmp_ind-mixed) 111 edges; not-a-census-entry 47 edges over
    23 distinct non-.SYM CRT-internal targets (was 52/27 — CRT scans are
    complete now); entry roots 524 root-unreferenced + 1 wndproc; indirect
    near=7 far=79.
  - adaptgen: **166/309 adapters re-route UNCHANGED** (same ABI-shape
    spread, same 143 kept-literal by the same reasons).
- **The convergence gate — both halves pass, now exercising REAL native
  x87.**  (a) literal graph vs a FRESH interpreted-oracle baseline over the
  whole cold_nohooks demo (`checkpoints.py --api-aligned`): **byte-identical
  at all 39 aligned checkpoints AND the final state** — both runs end at
  instruction 199,612,996 with digest 2abf3ce5… (the same digest cont.222
  recorded, i.e. the natively-emitted ESC bodies running through the shared
  FPU helpers are semantically identical to interpretation;
  `_AdjustWndMinMax` fires at boot via MAINWNDPROC, so FP drift would have
  shown at cp0).  (b) the routed graph self-check: --save/--check over the
  routed config — **MATCH, all 39 checkpoints**.
- Suites: win16_re 136 green at the new dos_re pin; simant_port **2229
  green** (test_irgen rewritten for the new frontier: the facts file pins to
  exactly `[(7, 0xF85B)]`, `__aFftol`/`__ftol` is pinned LIFTABLE with alias
  identity and its 4 x87-mnemonic body, env_wait/ledger identity moves to
  `_DoInt3` at 430E:F85B — the one pre-existing failure at the old pins is
  resolved by the coherent bump, not loosened).  README pipeline numbers
  updated.

## 2026-07-16 (cont.223) — M2b: adapter routing — the graph routes through the recovered corpus
- **M2b of the 2.0 adoption: the VMless graph now routes 166/309 matched
  entries through GENERATED CPU/ABI adapters into the authoritative
  `simant/recovered/` corpus — one graph, one implementation per entry**
  (owner directive: `original Win16 entry -> generated CPU/ABI adapter ->
  existing verified CPUless recovered implementation`; a routed entry's
  literal lift no longer exists on disk).  The four-tier architecture is now
  real: authoritative-manual + generated-adapter + literal-lift + api-effects
  (README pipeline section updated).
- **`scripts/adaptgen.py`** — the generator.  Consumes
  `simant/facts/recovered_map.json` + `artifacts/recovery_ir.json` +
  NEW `simant/facts/adapter_facts.json` (explicit evidence-backed routing
  facts), emits one adapter module per routable entry INTO the emitted graph
  dir under the entry's manifest stem — the `dos_re.lift.naming` seam works
  exactly as designed: swapping the module behind a stem re-routes both
  step-dispatch and every linked caller with zero machinery changes.
  Pipeline: `liftemit -> liftlink -> adaptgen` (routing runs last; liftlink
  re-emits linked callers).  Adapters are AUTOGENERATED, disposable, and
  carry the generalized island marshalling: args off the emulated stack per
  the `[bp+N]` map (sign-extended, the state-diff-oracle convention), the
  three fixed views bound per call (DGROUP = DS; SIMANT_DATA_GROUP / PACK via
  their load-time-constant DGROUP pointer globals `0xC49A`/`0xC49C` —
  live-verified against `seg_bases[8]`/`[9]`), result to AX (`-> int`) or
  nothing (`-> None`), near/far frame pop, entry-signature guard
  (`self_disable_if_patched`), CS:IP to the caller.
- **Routing policy (every exclusion is a per-entry reason in
  `artifacts/routing_report.json`)**: routed 166 = shapes far×{args,noargs}×
  {ax,none} 149 + near×{...} 18 − 1 fact-excluded (below).  Kept literal 143:
  args-incomplete 96 (docstring-only arg names — the island-backed leaf tier,
  where the island IS a hand adapter with genuinely nonstandard marshalling:
  `_GetDir`/`_GetDis` take pre-wrapped 16-bit deltas, `_GetMap`/`_IsItHole`
  compose validity+residue — NOT closable into the template),
  **presentation-effects 48** (see below), result-convention 36 (`int|None`/
  tuple returns need explicit facts), gated 18 (policy (a): `_YellowFight`/
  `_DoTroph`/`_GetRedBestDirs` carriers stay literal until the gates
  dissolve — the lift executes the gate branches natively, the impl would
  raise mid-graph), sig-mismatch 13 (`_Unpack` resumable streaming + the
  transformed-signature tier), callback-injected 12 (render tier),
  table_view/view 6, arg-frame-shape 3 (`_FindEggAt`/`_FindLifeAt`/
  `_GetMyInitialRandDir` have out-pointer args at [bp+6..] — the contiguity
  check caught them), callee-cleans 2 (`__aFldiv`/`__aFulmul` dword ABI),
  fact-excluded 1.
- **The presentation-effects exclusion (new fact class, load-bearing)**: the
  recovered corpus deliberately OMITS presentation side calls — the
  state-diff oracle stubs `_ZapEuMapAt`/`_FightBalloons`/`_QueenBalloons`/
  `_EggBalloons`/`_RestBalloons`/`_myBeginSound`/`_myBeginSong`/
  `_EditMessage`/`_PictStrnDialog`/`_win_LockWin`/`_win_UnlockWin` when
  running the ASM.  A routed adapter replaces the entry's WHOLE subtree with
  pure Python, so any entry whose static IR call closure reaches one of
  those sinks (or any api: call site) would silently drop real effects —
  fail-loud-never-fake forbids it.  adaptgen walks the IR call graph per
  candidate; 48 entries (the balloon/sound-adjacent behavior ticks:
  `_DoRestAnt`, `_SimEggB/R`, `_DoNestFightB/R`, the antlion tier, ...) stay
  literal until a presentation-effect sink seam exists (the dissolution the
  recovery inventory already predicted).  Sink list = data in
  `adapter_facts.json`, resolved through SIMANTW.SYM.
- **Verification tier 1 — `simant/tests/test_adaptgen.py` (22 tests)**:
  routing policy pinned (166/309, per-reason keeps incl. the gated and
  presentation classes, map-vs-IR ret conflicts fail loud), template shape
  pinned (near/far frame pop, `[bp+N]` -> `[sp+N-2]`), and a REAL A/B oracle:
  ten entries across the ABI matrix (far/near × args/noargs × ax/none, RNG-
  threaded and view-mutating cases) run as ORIGINAL ASM vs generated adapter
  from identical machine states — identical return frame, callee-saved regs,
  result regs, and byte-identical DGROUP/SIMANT_DATA_GROUP/PACK.
- **Verification tier 2 — NEW `scripts/adaptverify.py`** (liftverify's
  adapter-routing sibling): demo-driven per-call A/B — on each call of a
  routed entry, clone the machine (win16.verify.clone_machine, adapter hooks
  stripped from the oracle side), run the adapter live and the ASM on the
  clone to the adapter's continuation, compare the ABI contract (continuation,
  SP, callee-saved SI/DI/BP/DS/SS, result regs, full memory minus the
  dead-stack band below SP).  Deliberately NOT compared: caller-save scratch,
  flags, virtual time — the same exemptions every island has always had;
  dos_re's strict HookVerifier stays the byte-exact gate for literal lifts.
  Results: the FULL routed corpus (all 166 adapters installed, one A/B
  sample each) over the whole cold_nohooks demo — the replay runs to the
  demo's END (199.6M instructions) and scores **58 entries
  CONTRACT_PASSING, 0 DIVERGED, 108 NOT_REACHED by that drive**, covering
  all seven ABI shapes live; the in-game snap_185520/demo_185520 drive adds
  the smell-diffusion near tier (ColonySmell*/SmoothAlarm/DecTSmell/
  FillHoles*).  Honest coverage: NOT_REACHED entries are verified by the
  test-tier A/B and the state-diff oracles only, and the cross-config
  replay's game path drifts from the recorded intent after tens of millions
  of instructions (instruction-keyed demos under a changed hook config), so
  live sampling beyond that point exercises real-but-undirected gameplay.
- **FINDING (the A/B loop working as designed): `_PlaceBlackQueen` (7:65CE)
  diverges from the ASM on live worldgen state.**  On the cold_nohooks
  pre-state at ~instr 37.73M, `gameplay.place_black_queen` applied
  state-diff-style to ByteBackend copies produces a different tunnel than
  the interpreted ASM (46+17+34 bytes across dgroup/seg8/seg9; queen
  recorded at seg8[0x8362] 0x20/0x1E vs 0x22/0x21).  The generated adapter
  reproduces the impl EXACTLY (impl==adapter != ASM), so the divergence is
  in the recovered implementation on a path the test seeds never exercised —
  likely the sticky-drift/reroll interaction against a partially-dug map
  (the docstring records one such trap already caught by instrumented
  tracing).  Recorded as `route: false` with full repro evidence in
  `adapter_facts.json`; the impl itself is untouched (not this session's
  workpiece) — **next recovery session: re-prove `place_black_queen` against
  this pre-state, then drop the fact.**  Its `status: proven` in
  recovered_map is now known to be seed-limited, not wrong-per-oracle — the
  state-diff proof holds over its seeds; the live path is new evidence.
- **The whole-demo differential — measured, and the virtual-time analysis it
  confirms.**  Baseline re-recorded (interpreted oracle, api-aligned, 39+
  final checkpoints).  (a) CONTROL: the untouched literal graph vs baseline:
  **MATCH — all 39 checkpoints byte-identical** (M2a's result reproduces in
  this environment).  (b) The ROUTED graph replays the whole demo to its end
  and is **self-consistent**, but vs the interpreted baseline it diverges at
  cp0 **by instruction count** (−16 instr; digest at cp0 IDENTICAL), and
  from cp1 on the digests drift too.  This is structural, not a bug:
  adapters follow the proven island convention (one dispatch step per call,
  NOT `count_instructions`) because a contract-marshalling adapter CANNOT
  know the data-dependent instruction count of the path it replaces — and
  the demo clock derives from the instruction count, so once a
  GetTickCount-derived value lands in game state on a shifted timeline, the
  runs are different (self-consistent) executions.  **Byte-identical
  whole-demo convergence therefore remains the literal graph's gate; the
  routed graph's gates are the per-call ASM A/B (tiers 1+2) plus its own
  replay determinism (checkpoints --save/--check over the routed config:
  MATCH).**  Dissolution paths, recorded as queue entries: (i) per-entry
  virtual-time contracts (per-branch instruction counts attached to the
  recovered corpus, verifiable per call against the oracle), (ii) the
  semantic-boundary clock — key demos and the tick source to API-call
  ordinals instead of instruction counts, which makes EVERY config
  (interpreted / literal / routed / islands) share one timeline and
  dissolves the demos-are-hook-config-specific caveat for good (win16-level
  redesign; demos are re-recordable by owner policy).
- **win16_re fix (unpushed, this checkout): `HugeHeap.__setstate__`** —
  snapshots recorded before the GlobalFlags state existed pickle a heap
  without `_discardable`/`_locks`; resuming one died with AttributeError on
  the first GlobalLock (hit by adaptverify over snap_185520).  Backfill on
  unpickle + regression test (`tests/test_hugeheap.py`, 8 pass).
- **Environment note**: the nested dos_re checkout is mid-flight on
  `irgen-core` (another agent's session; ahead of win16_re's pin with native
  x87 ESC lifting).  One pre-existing simant test fails under that checkout
  (`test_irgen.py::test_keep_interpreted_fact_tags_env_wait...` — the x87
  CRT entry regenerates as `liftable: True` live, while the committed IR
  artifact and the test pin the pinned-dos_re truth `liftable: False`).
  Green at the committed pins; not touched here (dos_re is owned elsewhere
  right now).  All other suites green: simant_port 2205+22 new = 2227
  passing, win16_re 135+1 new.
- Promotion candidates recorded: (1) adaptgen's generic core (contract JSON
  -> adapter emission over any recovered corpus) into win16/dos_re once a
  second consumer exists — the SimAnt-side script stays the wrapper either
  way; (2) adaptverify's relaxed ABI-contract comparison as a HookVerifier
  profile (dos_re.verification currently only has the byte-exact one);
  (3) the semantic-boundary clock (ii above); (4) win16 flavour of
  tools/hook_bisect.py (still queued from cont.222).

## 2026-07-16 (cont.222) — M2a: the full VMless lifted graph, symbolically named, linked, converged to 68M
- **M2a of the 2.0 adoption: the whole SIMANTW liftable corpus is one
  installed, structurally linked, symbolically named VMless graph, run over
  a real demo against the interpreted oracle — and the whole-demo
  differential is CLEAN.**  Three-repo chain (none pushed): dos_re
  `irgen-core` 1c2c718+c6ef3d6+5adcdbc, win16_re 5d0d019+f9e1de7 (bumps)
  +eade086 (replay-determinism fix), simant_port (this commit).
- **dos_re: the symbolic-naming seam** (owner directive 1, upstream — not a
  game-side fork): `dos_re/lift/naming.py` (`GraphNaming`) makes module
  naming DATA — `graph_manifest.json` beside the emitted modules maps
  "CS:IP" -> stem; `resolve_links`/`install_vmless_graph`/`tools/liftlink`
  resolve through it, empty manifest = the historical `lifted_CS_IP` names
  byte-for-byte.  10 new tests (validation, determinism, fail-loud missing
  modules).  The manifest doubles as M2b's adapter-routing seam: swapping
  one entry's module re-routes every consumer without touching machinery.
- **dos_re: the whole-corpus emit frontier became 5 native-emitter
  capabilities** (2.0 loop: observe frontier -> improve tooling ->
  regenerate).  First full emit left 437 interpreter-fallback sites in the
  1281-module corpus; ALL were just 80186/Win16-era opcodes the emitter
  lacked: ENTER/LEAVE (337 sites — every MSC prologue/epilogue),
  3-operand IMUL (77), PUSHA/POPA (20), LAHF/SAHF (2 — the CRT's raw-INT21
  flag save; 0x9E/0x9F were never serviced by CPU8086 either, so the
  interpreter gained them too).  Differential tests per family.  After:
  **VMless wall HOLDS over all 1281 modules — zero interp_one sites.**
- **simant_port `scripts/liftemit.py`**: IR -> 1281 symbolically named
  modules in 1.0s (gitignored `simant/lifted/graph/` + manifest).  Naming
  policy (game-owned): `{module}_{symbol}` snake-case (`simone_srand1`),
  collisions get address suffixes (2 in the corpus: `gr_mem_free`,
  `text_exit` case-collisions), `$`-CRT names sanitized; paragraph-base
  ENTRY stays inside each module as provenance.  Skips = exactly the
  committed keep_interpreted frontier (32 x87 + _DoInt3).
- **simant_port `scripts/liftlink.py`**: dos_re's structural linker over
  the IR (unforked, via the manifest): **93 near + 2349 far edges linked
  into 606 re-emitted callers**.  Plus the fine-grained capability report
  (`artifacts/capability_report.json`, owner directive 2 — never one
  generic bucket): 1248 api:* call sites over 191 imports
  (USER=702 KERNEL=397 GDI=133 SOUND=9 KEYBOARD=7; top:
  GetProcAddress 64, GetAsyncKeyState 61, InvalidateRect 38); int21_dos 67
  + int2f 8; **1088 blocked near edges are ONE idiom — MSC's `push cs` +
  near CALL into an all-retf callee** (the same-segment far call; a
  dedicated link helper would claim them all — top queue entry); 97
  jmp_ind-mixed-exit callees; 52 edges to 27 non-.SYM targets (CRT
  internal labels — the strict-wall to-do); indirect calls near=7 far=79;
  entry roots: 1 wndproc (0100:2930 shared by AntRoot/GenericWindow/
  RibbonWindow) + 527 statically-unreferenced (dialog/enum/GetProcAddress
  procs + dead code — runtime classification found no live TimerProc in
  the inspected snapshots).
- **Convergence gate (which gate ran): cold_nohooks demo (199.6M instrs,
  cold start, no hooks) — interpreted-oracle baseline vs the FULL
  1281-module linked graph**, `scripts/checkpoints.py --api-aligned`
  (new): checkpoints sampled at import-thunk dispatches, where
  instruction counts are IDENTICAL between oracle and a
  count_instructions graph (virtual-time preservation) — the
  cross-config-comparison problem dissolved.  **Result: CLEAN — all 39
  aligned 5M-checkpoints AND the final stop state byte-identical
  (digest = memory + CPU + surfaces + clock; both runs end at
  instruction 199,612,996 with digest 2abf3ce5…).**  Whole-demo graph
  run: ~4 min CPython.
- The one real divergence found on the way was NOT an emitter bug but a
  **win16 replay-determinism gap**, localized mechanically: first
  divergence at cp 68/199 (1M grain), bisected (scratchpad
  graph_bisect.py, skip=-driven subsets over the UNLINKED emit) to
  `_DoEvent`, then traced to a `GetAsyncKeyState(VK_LBUTTON)` poll racing
  a recorded WM_LBUTTONDOWN: in-callback arrival injection rode ONLY
  `yield_check`, which fires per interpreter STEP — one instruction on
  the oracle, a whole lifted function under the graph.  Fix upstream
  (win16_re eade086 + regression test): `refresh_polled_input` injects
  arrivals due at the poll's own instruction count, making every
  game-observable input read instruction-aligned and replay timing
  config-invariant.  `_DoEvent` and the whole graph were innocent —
  the bisect had found the module whose step-compression crossed the
  race threshold.
- Frontier facts found live and dissolved: `_WaitHundredths`
  (GetTickCount pacing spin) tripped the 100k runaway guard — the spins
  are virtual-time-bounded (the demo clock derives from instruction
  count), so the corpus emits with a 5M iteration floor instead of an
  exclusion fact; deep guest recursion (worldgen) needs
  sys.setrecursionlimit under the graph (mirrored guest stack).
- **Remaining M3 runtime frontier (enumerated, for the armed-poison
  wall)**: 47 liftable functions end in a `jmp_ind` TAIL (switch
  dispatchers incl. MAINWNDPROC) — their case bodies re-enter the
  interpreter until `dispatch_entries` facts are recorded from the jump
  tables; 27 non-.SYM CRT-internal call targets (52 edges); the 32 x87
  records + `_DoInt3` (keep_interpreted facts).
- **liftverify spot-check** (per-call ASM differential, config-invariant):
  43 sim routines (seg4 blitters, seg5 SIMONE, seg6 SIMANT1 ticks, seg7
  win layer) over snap_004053/demo_004053 + snap_185520/demo_185520:
  **24 distinct ORACLE_PASSING, 0 DIVERGED**, rest NOT_REACHED by those
  drives (honest coverage; the 004053 replay stops at 27.6M on its known
  pre-frame-recording OrphanReturnError).
- New `simant/tests/test_liftgraph.py` (naming policy pinned pure +
  miniature real-code emit/install through the manifest); liftverify now
  resolves demo names via resolve_demo.  Suites: simant_port 2206,
  win16_re 135, dos_re 482 — all green.
- Capability queue, top entries (full detail in
  artifacts/capability_report.json):
  1. link the `push cs`+near-CALL far-call idiom (1088 edges — one
     dedicated dos_re link helper claims all of them);
  2. dispatch_entries facts for the 47 jmp_ind switch dispatchers (the
     designed dissolution — case leaders become re-entry hooks into the
     containing function);
  3. the 27 non-.SYM CRT-internal near targets (extend the entry corpus
     or record facts — 52 edges, today emulated+interpreted);
  4. win16 flavour of dos_re tools/hook_bisect.py (this session used a
     scratchpad bisector; promote it);
  5. M2b adapter routing through simant/facts/recovered_map.json via the
     graph manifest (the seam is in place — swap an entry's module in
     data).

## 2026-07-16 (cont.221) — recovery inventory: the manual corpus mapped for adapter routing
- **Owner directive**: the existing hand-recovered corpus is AUTHORITATIVE
  where verified — the 2.0 pipeline generates CPU/ABI adapters around it,
  never parallel literal lifts; SIMANTW.SYM identity is first-class through
  the whole pipeline. (Both directives recorded; irgen already carries
  symbol identity in every IR record as of cont.220.)
- NEW `docs/recovery_inventory.md` + `simant/facts/recovered_map.json` (the
  explicit machine-readable mapping — the pipeline never parses handwritten
  Python): **309 SYM entries matched by 300 distinct impls** (297 exact,
  12 derived twins, 0 mismatches, 0 duplicate claims), 291 proven +
  18 proven-gated, 53 helper-splits, 68 islands (61 routing through pure
  twins), 1009 code symbols in the lift-don't-recover set.
- Gate keystones: `_YellowFight` (6:823E) un-gates 16 matched entries;
  `_DoTroph` (1:846E) gates 5; `_GetRedBestDirs` (6:9A18) gates 1.
- Adapter-relevant findings: ret convention is per-entry (144 entries have
  no docstring-stated convention — close mechanically from the IR's own
  `exits` field, never guess); all three state views are fixed NE segments
  (bind once per image; modal signature `(dgroup, simant_data_group, pack,
  slot)` covers 92 entries); matching must stay address-keyed and
  shape-flexible (1 impl↔8 entries, 2 impls↔1 entry, alias pairs).
- Minor findings queued: duplicate `_GenNestMap` row in `hooks._ISLANDS`,
  `gameplay._sx32` dead, `_Unpack` island defers mid-op resume to ASM.

## 2026-07-16 (cont.220) — M1: recovery_ir.json — the win16 irgen over all 1319 SYM entries
- **M1 of the 2.0 adoption lands: a deterministic Recovery IR document for
  SIMANTW** (`win16_re/dos_re/docs/recovery_ir.md`), produced by a three-layer
  irgen with strict layering — one commit per repo, review chain
  dos_re → win16_re → simant_port (none pushed):
  - **dos_re `irgen-core` branch, 1e6d77c**: `tools/irgen.py`'s generic half
    factored into importable `dos_re/lift/irgen_core.py` (per-entry scan →
    record serialization → fail-loud unsupported ledger → deterministic JSON),
    parameterized by the pluggable triple `fetch_for(cs)` / `probe_for(cs)` /
    `effect_tagger(inst)` plus an `identity_for(cs, ip)` provider.  The DOS
    tool becomes a thin front-end; byte-identical output; seam contracts under
    test (custom tagger tags land in the document; identity embeds in records
    AND ledger entries).  467 dos_re tests green.
  - **win16_re main, 2d68467** (bumps dos_re → 1e6d77c): new `win16/irgen.py`
    — the Win16 triple: selector-map fetch, a FRESH `verify.clone_machine`
    scratch per entry (the cont.219 census bug class, avoided by design), and
    an effect tagger naming far transfers into THUNK_SEG as `api:*` from
    `cpu.hook_names` (callgraph's classification, run on the decoder's
    far_target), falling through to the DOS INT/port tagger (SimAnt's C
    runtime does raw INT 21h).  Entries/facts are NE pairs; ALL records keyed
    by paragraph-base CS:IP (the lift/link/install convention) with NE
    identity (`ne_seg` + caller symbol metadata) embedded.  134 tests green
    (5 new, synthetic machine — no game here).
  - **simant_port**: new `scripts/irgen.py` (all SIMANTW.SYM code-seg entries
    → `artifacts/recovery_ir.json`, gitignored/regeneratable; `--snapshot`,
    `--seg`, `--no-probe`) + `simant/facts/keep_interpreted.txt` (committed
    facts: the 33-name census frontier, per-line symbol + reason) +
    `simant/tests/test_irgen.py` (pins keying, identity, api:* tag, cross-seg
    far call, env_wait fact, symbol-named ledger).
- **Owner directive folded in: .SYM identity is FIRST-CLASS in the IR.**
  Every record carries `symbol`/`module`/`ne_seg` (+ `aliases` where two .SYM
  names share an address); unsupported-ledger entries name the symbol; the
  .SYM sha1 sits in provenance beside the EXE's.  Purpose: downstream symbolic
  naming of generated modules + matching IR entries against the ~300
  hand-recovered routines so the pipeline generates adapters around proven
  code.  Layering kept: dos_re stays symbol-source-agnostic (a pluggable
  identity provider); win16_re passes metadata through; only simant_port
  reads SIMANTW.SYM.
- **The run: 1319 SYM entries → 1313 functions in 12.6 s** (6 alias pairs
  collapse onto 6 addresses; each address scanned once, probe clone per
  entry).  1281 liftable + 32 unsupported = exactly the census frontier
  (33 names / 32 addresses: 32× x87-esc incl. the `__aFftol`/`__ftol` alias,
  1× `_DoInt3` grp5-invalid), 40 refusal records.  **Determinism gate: two
  fresh runs byte-identical** (sha1 761008a9…, 16.9 MB) — no timestamps,
  sorted keys, commit-hash provenance.
- Alias discovery (new datum): the SYM carries 6 aliased addresses, not just
  `__ftol`'s — census counted names (1319/1286), the IR counts functions
  (1313/1281); both are right, the delta is exactly the 6 aliases (5 liftable
  + 1 refused).
- Next (M2): batch `liftemit --from-ir` over the 1281 liftable records +
  structural link (near + far) — the IR is now the single input.
- **Direction change (owner-directed): adopt the DOS_RE 2.0 staged recovery
  pipeline** (`win16_re/dos_re/docs/dos_re_2.0.md` — owner-ratified upstream,
  proven through M2 on the Lemmings pilot). Mechanical lifting is done by
  scripts; AI only unblocks tooling; target = the strict VMless wall for
  SIMANTW (whole reachable graph lifted, interpreter poisoned inside the
  corpus). Hand recovery into `simant/recovered/` continues as the SEMANTIC
  stage (2.0 Stage 4) — the readable-source goal is unchanged; the pipeline
  gets the game VMless without waiting for it.
- Bumped dos_re to 7c1afa3 (2.0 merge) through the chain: win16_re 2f2f78e,
  simant_port 188b0aa. Both suites green UNCHANGED (129 + 2198) — zero API
  breakage.
- NEW `scripts/census.py` — the 2.0 frontier probe: batch scan_function +
  emit_function over EVERY SIMANTW.SYM entry in the 7 code segments (1319
  routines), refusal/emit ledgers with details, `--json` ledger output.
  **Result: 1286/1319 = 97.5% mechanically liftable, zero emit blockers.**
  The full frontier is 2 classes: 32× inline x87 (31 = the MSC FP runtime in
  `_TEXT` — runtime-role, native-port-provided; 1 = `_AdjustWndMinMax` seg1)
  + 1× `_DoInt3` (a debug-break stub that decodes into 0xFFFF padding).
  Segs 5/6/7 — the ENTIRE simulation — are 100% liftable minus that stub.
- Census bug caught (same class the cont.217 scoping agent self-caught): a
  probe scratch machine shared across entries gets mutated by probe
  execution and later reports FALSE decoder-mismatches (11 of them, incl.
  `_DoFoodInB`/`_SpiderScan`). Fix: fresh scratch clone per entry. All 11
  "decoder-mismatch" entries were false positives — they lift fine.
- Milestones tracked as tasks: M1 census (done) → M1 win16 irgen → M2 batch
  emit + structural link (near + far) → M2 demo-differential convergence →
  M3 play_vmless + wall. Generic mechanisms land in win16_re; SimAnt facts +
  runners live here.
- RECOVERED `do_red_initiator` (`_DoRedInitiator`, seg6:0x96D4, arg
  slot=[bp+6]; FAR return — the ONLY far-return routine in this whole
  batch, independently confirmed via all three of its own raw `ret far`
  epilogues, unlike its seven NEAR-return siblings). Composes the
  already-recovered `get_new_red_task`.
- Genuine, independently-verified finding: this routine's `pack[0x9D74]
  == 0` branch is a ~150-byte INLINED DUPLICATE of the already-recovered
  `get_new_red_task`'s own body — same `_UnRecruitRed` call, same
  `dgroup[0xCE80]==1` raid-roll gate with identical thresholds/fields,
  same fallback general-task sizing/clamp formula — confirmed field-by-
  field against `get_new_red_task`'s own already-oracle-verified body
  (not assumed from the similar name) and composed here as a direct call
  rather than re-derived. A cross-check test runs both functions from an
  identical starting state and compares the resulting `pack` fields.
- Genuinely new blocker (NOT `_DoTroph`/`_YellowFight`, NOT listed in this
  batch's own 8 targets): every code path — regardless of the
  `pack[0x9D74]` task-state dispatch — converges UNCONDITIONALLY on a call
  to `_GetRedBestDirs` (seg6:0x9A18), a substantial, previously-
  unrecovered routine (its own disassembly was still going past ~450
  bytes with multiple branches and a loop when this session stopped
  reading it, though it does call the already-recovered `get_dis`).
  Since the call is UNCONDITIONAL, `do_red_initiator` raises
  `NotImplementedError` on EVERY real invocation — genuinely different
  from this batch's other seven routines, where the two established gates
  are narrow, data-dependent branches most seeded scenarios avoid
  entirely. This is treated as the SAME fail-loud precedent as
  `_YellowFight`/`_DoTroph` (a new dependency surfaced by this routine's
  own from-scratch disassembly, exactly like `do_forage_ant`'s own session
  found an undocumented `_YellowFight` call) — NOT an attempt to also
  recover `_GetRedBestDirs`, which is out of this session's scope.
- Tests: 5 new cases (raises for every `pack[0x9D74]` status value,
  verifying the unconditional rally-point stamp — `pack[0x80A6]=x`,
  `[0x80AC]=y`, `[0x7606]=1` — plus the `get_new_red_task` cross-check).
  No full ASM state-diff oracle for this routine (it can never reach a
  natural return without `_GetRedBestDirs`), matching the SAME precedent
  `do_forage_ant`'s own gate tests already established. Full suite: 2198
  passed (was 2193).
- Commit: (pending).
- **ALL 8 of `_DoAntSimA`'s scoped dependencies are now recovered**:
  `_DoRandAntA`, `_DoRandAntAA`, `_DoRecruitAnt`, `_DoAttackAnt`,
  `_DoToAlarm`, `_DoToNestAnt`, `_DoRepoExit`, `_DoRedInitiator`. Plus
  the previously-recovered `_DoForageAnt`/`_DoDigOutAntA`/`_DoFightA`/
  `_DoReturnFoodAnt`. `_DoAntSimA` itself (seg6:0x4D8, 1348 bytes) is the
  natural next session's target — a deliberate follow-up, not attempted
  here per this session's own scope boundary. Two callees it ALSO reaches
  remain unconfirmed: `ANTEDIT!_RestBalloons` (established
  presentation-only precedent, safe to omit) and `SIMANT!
  _InvalQueenStorageDisp` (flagged by the prior scoping pass as "likely
  omit but not yet independently confirmed" — still not confirmed by
  THIS session either, since `_DoAntSimA` itself was out of scope; the
  next session recovering `_DoAntSimA` should confirm this independently
  before omitting it).

## 2026-07-15 (cont.217) — /goal grind: _DoAttackAnt (A-list "_DoAntSimA" dependency batch, 7/8)
- RECOVERED `do_attack_ant` (`_DoAttackAnt`, seg6:0x2A40, NEAR return, 640
  bytes) — a yard ant invading the ENEMY colony: heads toward the
  OPPOSITE colony's own NEST scent (`get_nest_dir` called with the colony
  bit FLIPPED — `colony_flag = effective_caste ^ 0x80`, independently
  confirmed via the raw disassembly's own `xor al, 80h` right before the
  push). Composed entirely from already-recovered leaves.
- A genuinely NEW mechanic this routine alone has: a per-caste-sub "mode
  substitution" front, indexing two SIMANT_DATA_GROUP tables with no
  prior codebase precedent (`[0x74 + caste_sub*2]` a per-sub flag,
  `[0x94 + caste_sub]` a per-sub signed-byte replacement) — a flag of
  exactly `1` replaces the caste's own middle "sub" nibble with the
  table's (sign-extended, shifted back into position) value, preserving
  the colony bit and low-3 direction bits. This EFFECTIVE caste (not a
  fresh field re-read) is what every subsequent `high_bits`/`caste_low3`/
  colony-bit check in the routine uses — described purely mechanically
  in the docstring since its broader game-design purpose (what game
  event sets that flag) is out of this session's scope.
- One independently-confirmed asymmetry within the SAME routine: the
  fight branch's `acting_caste` (fed to `get_winner`) is a FRESH re-read
  of `simant_data_group[0x2F62+slot]`, NOT the effective (possibly-
  substituted) caste the yellow-fight/same-colony checks just used —
  matching `do_to_nest_ant`'s own established fresh-re-read precedent at
  exactly this site, verified independently rather than assumed.
- Unlike `_DoRecruitAnt`, this routine's crowd-jitter and same-colony
  jitter share the IDENTICAL mode-table-based `_forage_jitter` landing
  code (confirmed via the raw disassembly's own shared jump target) — no
  trophallaxis gate at all (no `pack[0x9AF2]` read, no `_DoTroph` call).
- Tests: 9 new cases (at-entrance, move-empty black/red, a substituted-
  caste move, crowded-jitter, occupied-same-colony, edge) plus dedicated
  fight-found and yellowfight-gate tests — all passed on the first run.
  Full suite: 2193 passed (was 2184).
- Commit: (pending).
- Still missing (1/8): `_DoRedInitiator` — the last one, a genuinely
  different shape (calls `SIMTWO!_RecruitRed`/`_UnRecruitRed`, not the
  shared A-list move/fight tail every other routine in this batch has).

## 2026-07-15 (cont.216) — /goal grind: _DoRecruitAnt (A-list "_DoAntSimA" dependency batch, 6/8)
- RECOVERED `do_recruit_ant` (`_DoRecruitAnt`, seg6:0x22A8, NEAR return,
  722 bytes) — a defending/recruited yard ant: flees via `get_alarm_dir`
  when its OWN territory is alarmed, else steers via the colony-specific
  `get_defend_dir`/`get_red_defend_dir` (picked by a numeric `caste >
  0x7F` compare against the zero-extended caste byte — a plain compare
  standing in for a bit test on the SAME `0x80` colony bit). Composed
  entirely from already-recovered leaves.
- TWO genuine, independently-confirmed asymmetries from every other
  A-list sibling recovered this batch (neither assumed, both re-checked
  against this routine's own raw disassembly):
  1. The crowd-jitter path is COMPUTATIONALLY DIFFERENT from
     `_forage_jitter` — a fresh `_SRand8()` roll is used DIRECTLY as the
     new direction bits (`roll8 | high_bits`), with NO mode-table lookup
     at all. Every OTHER A-list sibling's crowd-jitter and same-colony-
     occupant jitter share the identical mode-table computation; this is
     the first one where they genuinely differ (the post-trophallaxis-gate
     tail here STILL uses the mode table, i.e. still is `_forage_jitter`
     — only the crowd path itself differs).
  2. The trophallaxis gate's polarity is INVERTED from
     `do_rand_ant_a`/`do_to_nest_ant`'s own `pack[0x9AF2] == 1` test: this
     routine's raw disassembly is `cmp ...,0000h; jz <skip>`, i.e. the
     gate fires on ANY nonzero value, not just `1` — caught by re-reading
     the actual compare/jump bytes rather than trusting the sibling
     precedent, and covered by a dedicated test seeding `pack[0x9AF2]=7`
     (nonzero, NOT `1`) to prove the gate still fires.
- Tests: 10 new cases (at-entrance, defend-dir black/red, alarmed-flee,
  the raw-roll8 crowd-jitter, occupied-same-colony, edge) plus dedicated
  fight-found, yellowfight-gate, and the polarity-sensitive dotroph-gate
  tests — all passed on the first run. Full suite: 2184 passed (was 2174).
- Commit: (pending).
- Still missing (2/8): `_DoAttackAnt`, `_DoRedInitiator`.

## 2026-07-15 (cont.215) — /goal grind: _DoToAlarm (A-list "_DoAntSimA" dependency batch, 5/8)
- RECOVERED `do_to_alarm` (`_DoToAlarm`, seg6:0x1A0A, NEAR return, 682
  bytes) — a yard ant fleeing danger. Same entrance/crowd/move/fight tail
  shape as the other A-list siblings, direction via the already-recovered
  `get_alarm_dir`, composed entirely from already-recovered leaves.
- One genuinely NEW front this routine has that none of the prior four
  do: a territory "self-heal" gate BEFORE the direction roll — own-cell
  ALARM scent `== 0` AND a fresh `_SRand4()==0` (1-in-4) re-stamps the
  ant's own caste onto its own life cell (a no-op reassert) and computes
  `field_c` via `get_new_mode`, with NO movement at all on that path.
  Independently confirmed the territory-index formula
  (`(x&0xFE)<<4 + (y>>1)`) is byte-identical to `do_forage_ant`'s own.
- Three more absences independently confirmed via the raw disassembly and
  resolved call-target list (no assumptions carried over from siblings):
  no pickup-food logic anywhere (no `PickupFoodA` call at all — this
  routine doesn't even have the "pickup-tile-but-caste_sub-wrong" wrinkle
  its siblings do, since there's no pickup branch to skip into), no
  `field_e`/jam-scent step on an empty move (the same genuinely-simpler
  shape `do_rand_ant_aa`'s empty-move has), and no trophallaxis gate at
  all (no `pack[0x9AF2]` read, no `_DoTroph` call) — same-colony occupants
  (yellow or not) always just `_forage_jitter`.
- Tests: 10 new cases (self-heal hit/miss, at-entrance, move-empty
  black/red, crowded-jitter, occupied-same-colony, edge) plus dedicated
  fight-found and yellowfight-gate tests — all passed on the first run.
  Full suite: 2174 passed (was 2164).
- Commit: (pending).
- Still missing (3/8): `_DoRecruitAnt`, `_DoAttackAnt`, `_DoRedInitiator`.

## 2026-07-15 (cont.214) — /goal grind: _DoRepoExit (A-list "_DoAntSimA" dependency batch, 4/8)
- RECOVERED `do_repo_exit` (`_DoRepoExit`, seg6:0xC7A, NEAR return, 208
  bytes) — a small dispatcher, unblocked once `_DoToNestAnt`/`_DoRandAntAA`
  both landed: reads the acting ant's own-cell NEST scent (the SAME grid
  `get_nest_dir` reads) and calls `do_to_nest_ant` if `< 100`, else
  `do_rand_ant_aa`; then a per-colony population-cap roll can force
  `field_c = 0x10` on `pack[0x9B6A]`'s CURRENT acting slot.
- One subtlety independently confirmed via the raw disassembly (not
  assumed): the population-cap tail re-reads the colony bit FRESH from
  `simant_data_group[0x2F62+slot]` AFTER the dispatch call, not a cached
  pre-dispatch value — so a fight inside the composed call that clears the
  acting ant's own caste changes which of `pack[0x7C44]`/`[0x8078]` this
  tail reads. Also: `pack[0x9B6A]`, not this routine's own `slot` argument,
  is what the final override write targets — a dedicated test
  (`test_dorepoexit_acting_slot_matches_asm`) forces the two to differ to
  prove this isn't accidentally-equivalent.
- A count of exactly `1` is a forced hit with NO `_SRand1` call at all (the
  real ASM special-cases it since `_SRand1(1)` is deterministically `0`
  anyway) — ported as an explicit `if count == 1: hit = True` branch
  rather than always calling `srand1`.
- Tests: 9 new cases (dispatch branch x colony x population-cap
  hit/miss/forced/zero) plus the acting-slot test — all passed on the
  first run. Full suite: 2164 passed (was 2155).
- Commit: (pending).
- Still missing (4/8): `_DoRecruitAnt`, `_DoAttackAnt`, `_DoToAlarm`,
  `_DoRedInitiator`.

## 2026-07-15 (cont.213) — /goal grind: _DoToNestAnt (A-list "_DoAntSimA" dependency batch, 3/8)
- RECOVERED `do_to_nest_ant` (`_DoToNestAnt`, seg6:0x1676, NEAR return, 916
  bytes) — a yard ant heading toward its own nest. Same overall shape as
  `do_rand_ant_a` (entrance/pickup/crowd-check/move/fight tail, including
  the SAME "pickup-tile-but-caste_sub-wrong skips the crowd check" wrinkle
  the previous slice caught), but direction comes from the already-
  recovered `get_nest_dir` instead of `get_rand_dir`. Composed entirely
  from already-recovered leaves — no new seg5/seg7 primitives needed.
- Two more genuine, independently-confirmed asymmetries vs. `do_rand_ant_a`
  (neither assumed from that precedent — both checked against this
  routine's own raw disassembly and its own resolved call-target list):
  (1) the empty-cell move tail has NO `dec_t_smell` call and NO post-move
  `_SRand8` field_c-refresh roll — a zero `field_e` returns immediately
  with no jam-scent attempt at all; (2) the same-colony-not-yellow branch
  is a PLAIN `_forage_jitter` with no `get_new_mode`/`field_c` change
  (`do_rand_ant_a`'s own equivalent branch DOES call `get_new_mode`).
- Since `get_nest_dir`'s "own cell has scent" branch is a deterministic
  scent-gradient argmax (not RNG-driven), the state-diff seeds fix the
  NEST scent grid directly instead of hunting for a magic `_SRand` seed —
  a cleaner oracle-seeding technique than the RNG-driven routines needed.
- Tests: 13 new cases in `test_state_diff.py` (parametrized table +
  dedicated field_e-jam, fight-found, yellowfight-gate, dotroph-gate
  tests) — all passed on the FIRST run (no oracle mismatch this time,
  unlike the prior slice's pickup/crowd-check bug). Full suite: 2155
  passed (was 2142).
- Commit: (pending).
- Still missing (5/8): `_DoRecruitAnt`, `_DoAttackAnt`, `_DoToAlarm`,
  `_DoRepoExit` (composes `_DoToNestAnt`+`_DoRandAntAA`, both now done —
  unblocked), `_DoRedInitiator`.

## 2026-07-15 (cont.212) — /goal grind: _DoRandAntA + _DoRandAntAA (A-list "_DoAntSimA" dependency batch, 2/8)
- Task: recover the 8 still-unrecovered dependencies `_DoAntSimA` (seg6:0x4D8,
  1348 bytes — NOT itself attempted this session, a deliberate follow-up)
  calls: `_DoRandAntA`, `_DoRandAntAA`, `_DoRecruitAnt`, `_DoAttackAnt`,
  `_DoToAlarm`, `_DoToNestAnt`, `_DoRepoExit`, `_DoRedInitiator`. Every
  address was re-verified fresh via `symbols.symbols_in_segment(6)` against
  a fresh `create_machine()` (matched the prior scoping pass's numbers
  exactly, but independently confirmed rather than trusted).
- RECOVERED `do_rand_ant_a` (`_DoRandAntA`, seg6:0xE66, NEAR return, 974
  bytes) and `do_rand_ant_aa` (`_DoRandAntAA`, seg6:0x1234, NEAR, 588
  bytes) — both yard-ant ("A-list") random-wander ticks, siblings of the
  already-recovered `do_forage_ant` but WITHOUT its scent-gradient
  direction pick (`get_rand_dir` instead of `get_forage_dir`, no "stay
  put" sentinel) and without its `_SRand32` idle short-circuit or
  alarm-territory gate. Composed entirely from ALREADY-recovered leaves —
  `is_valid_a`, `go_in_nest`, `get_rand_dir`, `pickup_food_a`,
  `is_yellow_ant`, `find_in_a_list`, `get_winner`, `get_new_mode`,
  `jam_scent_bn`/`rn`, `dec_t_smell`, `alarm_here2`, and `do_forage_ant`'s
  own `_forage_jitter` helper (reused verbatim where the real ASM's jitter
  computation is byte-identical — independently confirmed per call site,
  not assumed) — no new seg7/seg5 primitives were needed at all.
- `_DoRandAntA` has two genuine differences from `_DoRandAntAA` beyond the
  presence/absence of pickup-food logic: (1) after an empty-cell move, an
  extra `_SRand8()==0` (1-in-8) roll, gated on `caste_sub in {2,6}`, can
  additionally set `field_c=2` — `_DoRandAntAA`'s empty-move tail has NO
  such follow-up (returns immediately after the move, independently
  confirmed via the raw disassembly's own absence of any post-move
  `call far _SRand8`). (2) `_DoRandAntA` has a same-colony-yellow/
  trophallaxis gate (`pack[0x9AF2]==1` → unrecovered `_DoTroph`, raises
  `NotImplementedError` per the established `do_forage_ant`/
  `try_move_dir_b` precedent) that `_DoRandAntAA` does NOT have at all —
  its same-colony branch (yellow OR not) just falls straight into a plain
  `_forage_jitter`, no `pack[0x9AF2]` read anywhere in its body.
- Genuine bug caught by the oracle (not by inspection): `_DoRandAntA`'s
  pickup-tile check is evaluated UNCONDITIONALLY, but when the tile IS a
  pickup tile and `caste_sub` is NOT `2`/`6`, the real ASM's `jnz` jumps
  STRAIGHT to the move/occupant-resolution code, BYPASSING the crowding
  check (`dest_tile > pack[0x7604]`) entirely — the first draft ran the
  crowd check unconditionally in that case too. A dedicated
  `pickup-tile-caste-sub-not-2-6` state-diff scenario (dest tile in the
  pickup range, threshold set low enough that a wrongly-run crowd check
  would divert into a jitter) caught the 2-byte real-ASM mismatch
  immediately; fixed by restructuring the `is_pickup`/crowd-check as an
  `if/elif` matching the real control flow, not two independent `if`s.
- Both `_DoAttackAnt`/`_DoRecruitAnt`/`_DoToAlarm`/`_DoToNestAnt` share
  this SAME "move → yellow-fight/trophallaxis-or-fight" tail shape
  (confirmed via a batch `resolve_calls.py` pass over all 8 fresh
  disassemblies — every one of them calls the identical fixed addresses
  `SIMONE!_IsYellowAnt`/`SIMANT1!_YellowFight`/`SIMONE!_FindInAList`/
  `SIMANT1!_GetWinner`/`SIMANT1!_AlarmHere2`, and `_DoRandAntA`/
  `_DoToNestAnt`/`_DoRecruitAnt` additionally reach `SIMANT!_DoTroph`),
  which should make the remaining 6 routines faster to recover — only
  their "front" (how they pick a direction) genuinely differs.
- Tests: `simant/tests/test_state_diff.py` — parametrized state-diff
  tables for both routines (at-entrance, empty-move black/red, the
  field_c-refresh roll via a brute-forced seed `0x0` — `srand_step(0)&7==0`
  for BOTH the direction roll and the immediately-following field_c roll,
  same "find a concrete seed" technique as `do_forage_ant`'s own
  idle-srand32 test — pickup outside/inside, the caste_sub-gated pickup
  miss, crowded-jitter, occupied-same-colony, an edge-adjacent position),
  plus dedicated `fight_found` (forces a `find_in_a_list` hit),
  `yellowfight_gate_raises`, and (for `_DoRandAntA` only) `dotroph_gate_
  raises` tests. Full suite: 2142 passed (was 2121).
- Commit: `aff2652`.
- Still missing (6/8): `_DoRecruitAnt`, `_DoAttackAnt`, `_DoToAlarm`,
  `_DoToNestAnt`, `_DoRepoExit` (composes `_DoToNestAnt`+`_DoRandAntAA`,
  so ordered after them), `_DoRedInitiator` (a genuinely different shape —
  calls `SIMTWO!_RecruitRed`/`_UnRecruitRed`, not the shared A-list tail).

## 2026-07-15 (cont.211) — /goal grind: _DoNestAntB + _DoAntSimB (seg6 behavior tier — the dispatcher itself, COMPLETE)
- RECOVERED `do_nest_ant_b` (`_DoNestAntB`, SIMANTW.SYM seg6:2DAE, FAR
  return, 1910 bytes, args `x=[bp+6]`, `y=[bp+8]`, `mode=[bp+10]`) — the
  per-tick orchestrator the five prior sessions' routines
  (`do_forage_ant`/`do_dig_in_b`/`sim_queen_b`/`do_food_in_b`/
  `do_dig_out_b`) exist to unblock, plus `do_ant_sim_b` (`_DoAntSimB`,
  seg6:2D4E, NEAR return via the established `push cs; call near`
  far-call-emulation idiom — confirmed by its OWN `ret far` epilogue), the
  trivial B-list loop that calls it. Located a fresh linear disassembly
  already sitting in the scratchpad from a prior scoping pass
  (`donestantb.txt`); this session decoded the raw jump-table bytes at
  seg6:0x2E4E (18 words, NOT trusted from the prior pass's own
  terminal-call summary) and cross-referenced every unique `call`/`jmp`
  target via `symbols.nearest_symbol` before writing a line of Python.
- TWO genuine surprises beyond the pre-existing scoping table, both fully
  recovered (not stubbed) after independent verification:
  1. The routine is NOT a single 18-arm dispatcher — `mode & 0x80` gates a
     SEPARATE ~450-byte body (`_do_nest_ant_b_foreign`) for a
     foreign-colony "raider" ant occupying a B-list slot (a genuine
     mechanic: raiders get added to the black nest's own B-list with a
     foreign-flavored caste byte and ticked here in the same coordinate
     space). Its own valid-caste range (`1..0x67`), inverted `_YellowFight`
     gate polarity (matching the SAME inversion `check_nest_fight_r`/
     `do_rest_r`/`do_rand_r` already established for red-flavored logic),
     egg-kill (`1..7`, bumps the SAME `pack[0x7C1E:0x7C20]` accumulator
     `sim_egg_b`'s own failed-hatch branch bumps) and queen-kill
     (`0x60..0x67`, the established `make_blk_queen` caste range) special
     cases, and its `raid_in_b`/`raid_out_b` clear-tail dispatch were all
     traced from the raw bytes and confirmed via a real-ASM state-diff
     oracle (8 scenarios covering every branch, including a raise-loudly
     yellow-fight-gate test).
  2. `_SimQueenB`'s prior recovery (`5aa2d91`) documented its signature
     BY ANALOGY to `_DoDigInB`'s (`mode=[bp+10], caste_sub=[bp+12]`)
     rather than independently re-verifying it. This session's own
     push-order analysis of arm `9`'s call (cross-checked against TWO
     independently-correct call shapes in the SAME function — arm `1`'s
     `_DoNestingB` and arm `4`'s `_DoDigInB`, both of which push `sub`
     before `mode`) found arm `9` pushes `mode` BEFORE `sub` — the
     OPPOSITE order. `_SimQueenB`'s REAL frame is `[bp+10]=sub,
     [bp+12]=mode` — swapped relative to `sim_queen_b`'s own parameter
     NAMES (though NOT a bug in that already-shipped, oracle-verified
     function — it's proven positionally, and its own internal logic
     independently confirms the swap: the value checked against literal
     `0x0C`/`0x0D` is far more naturally a `sub`, and the value
     XOR'd/masked for a compass direction is far more naturally a full
     caste). Ported by calling `sim_queen_b(x, y, sub, mode)` — a
     deliberately confusing-looking but real-ASM-verified swap, called
     out at length in `do_nest_ant_b`'s own docstring so no future session
     "corrects" it back.
- Every unique `call far`/`call near` target across the WHOLE 1910-byte
  body was audited (both branches): all resolve to an already-recovered
  sibling, an inline `_SRand256`/`32`/`8`/`1`/`_GetNewMode(B)`/
  `_IsYellowAnt`/`_FindInBList`/`_GetWinner` primitive, the established
  `_YellowFight` raise-loudly gate (TWO call sites this routine — one per
  branch, with genuinely different gate polarities), or the
  presentation-only `ANTEDIT!_RestBalloons`-family balloon call (omitted,
  core/presentation split) — no NEW unrecovered dependency, so the whole
  routine landed in one slice rather than a partial/gated stub.
- The own-colony branch's 18-arm table itself: a starvation-gate prologue
  (`_SRand256()==0` AND `field_c!=9` AND `_SRand32() > dgroup[0xAC86]` ->
  kill outright, no dispatch at all — ported with the conditional-
  `_SRand32`-call-count discipline this tier's sessions keep
  re-discovering as a bug class); SEVEN of the eighteen arms
  (`2,5,7,0xB,0xC,0xF,0x10`) share one identical jump-table cell, an
  unconditional `do_dig_out_b` call; arm `6` is the ONLY `dgroup[0xCE80]`-
  gated one (`!=2` -> `do_dig_out_b`, `==2` -> a same-tick self-fight
  re-check + move); arms `0`/`>0x11`(fallback)/`6`-gated/`0xD`/`0xE` all
  share a "did a fight already land on my own cell this tick?" pattern
  (factored as `_nest_ant_b_selfcheck`, verified byte-identical at all
  five call sites) — a genuine same-tick race check since every acting
  ant's turn writes through the SAME shared life-grid within one frame;
  arm `0x11` is `do_drown_b`'s own established body FULLY INLINED (no
  `call` instruction at all, confirmed via the raw disassembly) rather
  than called, composed here as `do_drown_b(x, y, mode)` directly.
- New PACK fields resolved fresh (never assumed): `0xC354`/`0xC35A`/
  `0xC35C`/`0xC35E`/`0xC360` all resolve to the PACK selector (confirmed
  via `m.mem.rw(dg, off)` on a fresh machine), same as the already-
  established `0xC350`/`0xC356`; `0xC352`/`0xC358` resolve to
  SIMANT_DATA_GROUP. `pack[0x786A + 2*field_c]`/`pack[0x7BE4 + 2*field_c]`
  are the SAME "mode population" tally arrays `tally_mode_pop`/
  `clr_mode_pop` already established (own-colony vs. foreign-caste counts
  respectively — confirmed by the overlapping address ranges);
  `pack[0x7C44]` is a threshold gate (own-colony arm `0xE`'s population
  cap, `> 0x64` -> `field_c=0x0F`) that `clr_mode_pop` already decrements
  each tick — its producer/consumer chain is left unresolved (same
  "unclear ultimate meaning" caveat several tally-table fields already
  carry) but the byte-level behavior is fully verified regardless.
- 32 new state-diff tests (`test_donestantb_*` x30, `test_doantsimb_*` x2)
  covering all 18 own-colony arms, both `field_c=6` gate outcomes, the
  `_SimQueenB` arg-swap arm specifically, starvation death (+ `field_c=9`
  exemption), both yellow-fight raise gates (own-colony AND the inverted
  foreign one), all four foreign-branch outcomes (mode>0xEF, egg-kill,
  queen-kill, normal fight), both foreign clear-tail dispatches, and
  `_DoAntSimB`'s empty-list no-op + reverse-order/skip-dead-slot wiring —
  every scenario verified against the real ASM via `_run_and_diff_segs`
  (discovered mid-session that `_DoAntSimB`, despite zero args, needs
  `near=False` in the oracle harness: it's reached via the same
  far-call-emulation idiom every OTHER seg6-internal call in this tier
  uses, so it executes a genuine `ret far` epilogue — `near=True` pops two
  bytes short and sends IP/CS into garbage stack memory, the exact
  "region window"/stack-shape bug class this project's own workflow notes
  warn about, just manifesting as a wild INT3 instead of a silent
  mismatch this time). Full suite: 2121 passed.
- seg6's `_DoNestAntB`/`_DoAntSimB` cluster is now fully closed out — the
  whole B-list per-tick behavior chain from the top-level loop down
  through every dispatch arm is real, byte-exact Python.

## 2026-07-15 (cont.210) — /goal grind: _DoDigOutB (seg6 behavior tier, _DoNestAntB dependency 3/3 — COMPLETE)
- RECOVERED `do_dig_out_b` (`_DoDigOutB`, SIMANTW.SYM seg6:4EB0, FAR
  return, 686 bytes) — the black nest ant heading OUT of the nest through
  already-clear passages: pick an exit-seeking direction and move into an
  open destination (fighting/deferring on an occupant, same shape as
  `_DoDigInB`/`_DoFoodInB`'s own move tails), bail with a small local
  exit-appeal penalty if the destination is blocked, or do NOTHING at all
  if it's still dirt (unlike `_DoDigInB`, this routine never digs). Third
  and LAST of the three `_DoNestAntB` dispatch-arm dependencies flagged
  by the original scoping survey — all three now recovered this session.
- Same THREE-arg signature `_DoFoodInB` has (`x=[bp+6]`, `y=[bp+8]`,
  `mode=[bp+10]`, no caste_sub — confirmed via the raw `enter 0014h,0`
  frame never referencing `[bp+14]`).
- Composes `get_exit_dir_b`, `rand_turn`, `get_out_b`, `is_it_dirt`,
  `is_yellow_ant`, `find_in_b_list`, `get_winner`, and — reused VERBATIM,
  byte-for-byte matching field set — `_try_eat_food` for the move tail's
  trailing food-nibble/growth block: the SAME `(MAP_PLANE_BASE[2],
  0x9EA4, 0xAC82, 0xAC98, 0x7402, 0xAC86)` arguments AND the SAME
  `_SRand64() > dgroup[0xAC86]` outer gate `_DoDigInB`'s own analogous
  tail already established — three-for-three now on this exact
  composable block across the whole `_DoNestAntB` dispatch cluster.
- A genuinely new mechanic this routine introduces: on a BLOCKED
  destination (map tile `>= 0x30`), it decrements
  `simant_data_group[0x3A4 + (x << 6) + y]` — the ant's own current-
  position cell of the SAME exit-distance map `_GetExitDirB`/
  `_GetEnterDirB` read — a "this route turned out blocked, lower my own
  exit appeal" self-penalty, then (via `sub = (mode & 0x78) >> 3`) either
  bumps the caste field down by `0x18` and sets `field_c = 4` (`sub in
  (5, 9)`), sets `field_c = 4` alone (`sub in (2, 6)`), or leaves it
  untouched — traced carefully via the raw disassembly's own fallthrough
  structure (the `sub in (5, 9)` arm's `field_c=4` write and the `sub in
  (2, 6)` arm's write are the SAME single instruction reused via
  fallthrough, not two separate writes, confirmed rather than assumed).
- One raise-loudly gate, the SAME established `_YellowFight(2, slot)`
  precedent (seg6:823E), reached through the SAME textually-redundant
  double `is_yellow_ant`/`CE98` re-check `_DoFoodInB` has on the exact
  same unchanged occupant byte — collapses to the identical single-check
  gate without loss of byte-exactness, confirmed independently for this
  routine's own disassembly rather than assumed from the sibling.
- No bugs caught this session — all 17 new tests passed on the FIRST
  real-ASM run, unlike the two prior `_DoNestAntB`-dependency sessions
  (both of which caught an `_SRand*`-call-count polarity mistake via the
  oracle). Attributed to applying that exact lesson proactively this time:
  every branch point was checked for "does this skip an RNG call
  entirely, or just skip a downstream effect" before writing any Python,
  rather than after a failing test forced the question.
- 17 new state-diff/gate tests, all green on real ASM (rand_turn
  fallback, move-into-empty, out-of-bounds, `new_y<1`-get_out_b, 6-way
  parametrized blocked-tile sub-code coverage, dirt-tile no-op, yellow-
  CE98-zero, invader-out-of-blist-move, fight-found, eat-food-tail
  triggered/skipped, and a dedicated yellowfight-gate-raises test using
  the direct-call pattern established by the other two). Suite: simant
  2089 (+17), full suite green.
- **All three `_DoNestAntB` dispatch-arm dependencies from this session's
  original brief are now recovered** (`_SimQueenB` cont.208, `_DoFoodInB`
  cont.209, `_DoDigOutB` this entry) — `_DoNestAntB` itself (the ~18-arm
  jump-table dispatcher) and the trivial 96-byte `_DoAntSimB` wrapper
  that calls it are the next natural target, not attempted this session
  per the brief's own scope (dependencies only).

## 2026-07-15 (cont.209) — /goal grind: _DoFoodInB (seg6 behavior tier, _DoNestAntB dependency 2/3)
- RECOVERED `do_food_in_b` (`_DoFoodInB`, SIMANTW.SYM seg6:492A, FAR
  return, 678 bytes) — the black nest ant's carry-food-in per-tick
  behavior: head toward a chosen direction and move in (fighting/
  deferring on an occupied destination, same shape as `_DoDigInB`'s own
  move tail), or — no good direction, or a 1-in-16 override — drop/grow a
  food pile at her CURRENT position and re-pick her mode instead. Second
  of the three `_DoNestAntB` dispatch-arm dependencies from this
  session's brief (`_SimQueenB` done in cont.208, `_DoDigOutB` next).
- Only THREE args (`x=[bp+6]`, `y=[bp+8]`, `mode=[bp+10]`) — confirmed via
  the raw `enter 001Ch,0` frame never referencing `[bp+14]` anywhere —
  NOT the caller-precomputed `caste_sub` fourth arg `_DoDigInB`/
  `_SimQueenB` both take. A genuine signature asymmetry among the three
  `_DoNestAntB` dispatch arms, not a porting oversight.
- Composes `get_enter_dir_b`, `get_out_b`, `is_yellow_ant`,
  `find_in_b_list`, `get_winner`, `get_new_mode_b`, and — a genuine
  simplification this session found — the PRIVATE shared body
  `_eat_food` (not the public `try_eat_food_b`/`eat_food_b` wrappers)
  reused VERBATIM for the alt-branch's own food-supply tail: its
  disassembled `(MAP_PLANE_BASE[2], 0x9EA4, 0xAC82, 0xAC98, 0x7402,
  0xAC86)` field-access set matches `_eat_food`'s own byte for byte
  (reached via a different outer gate here — an `_SRand1(100)` roll vs
  food supply — than `_EatFoodB`'s own unconditional call site), again
  confirming both independently rather than re-deriving a near-duplicate.
- One raise-loudly gate, matching the established `_YellowFight(2, slot)`
  precedent exactly (same call/argument pair, same seg6:823E target). The
  real ASM reaches it through a second, textually-redundant
  `is_yellow_ant` re-call plus a second `dgroup[0xCE98]` re-check on the
  SAME unchanged occupant byte (confirmed via the raw disassembly that
  nothing writes to that byte in between the two checks) — collapses to
  the same single-check gate `_DoDigInB`/`_DoForageAnt` already use,
  without loss of byte-exactness.
- **A real bug caught before trusting the ASM oracle, the SAME polarity-
  mistake class `_SimQueenB` caught last session**: an early reading had
  `_SRand16()` rolled unconditionally before deciding between the main
  branch and the "grow food at current position" alt-branch. The real
  ASM's own `jge`/`jmp` pair actually skips the `_SRand16()` call site
  ENTIRELY when `get_enter_dir_b` returns negative (no valid direction),
  going straight to the alt-branch with zero extra RNG consumed — `_SRand16()`
  is only ever rolled when a direction WAS found, and only THEN can a
  1-in-16 roll of `0` override it back to the alt-branch anyway. Caught
  immediately by the first real-ASM state-diff run on a divergent `_SRand*`
  LFSR seed value (the tell-tale signature this bug class leaves,
  independently confirmed twice now).
- A second, smaller bug caught the same way: `get_winner`'s C-runtime
  `_RRand` state (`RAND_STATE_OFF`) needed explicit pinning in the new
  seed helper (the "fight found" scenario's own life-grid byte mismatched
  by exactly the `0x80` colony bit until pinned) — the exact same
  drift-unless-pinned trap `_DoForageAnt`'s own session already
  documented, re-encountered and fixed here rather than re-discovered as
  a surprise.
- 15 new state-diff/gate tests, all green on real ASM (alt-branch no-
  direction with/without the `_eat_food` tail, 3-way tile-growth
  threshold, caste-bit3-clear, 1-in-16 override, move-into-empty, out-of-
  bounds, `new_y<1`-get_out_b, blocked-tile, yellow-CE98-zero, invader-
  out-of-blist-move, fight-found, and a dedicated yellowfight-gate-raises
  test using the direct-call pattern `_DoDigInB`'s own equivalent test
  established). Suite: simant 2072 (+15), full suite green.
- `_DoDigOutB` (the last of the three `_DoNestAntB` dependencies from
  this session's brief) not yet attempted — next up.

## 2026-07-15 (cont.208) — /goal grind: _SimQueenB (seg6 behavior tier, _DoNestAntB dependency 1/3)
- RECOVERED `sim_queen_b` (`_SimQueenB`, SIMANTW.SYM seg6:3DC2, FAR return,
  668 bytes) — the black queen's own `_DoNestAntB` dispatch arm: try to
  relocate her, else decide whether to expand the colony (place an egg) or
  die. First of three `_DoNestAntB` dispatch-arm dependencies flagged by
  the prior scoping survey (`_DoFoodInB`, `_DoDigOutB` still to go).
  Re-verified all three addresses/sizes fresh via `symbols_in_segment(6)`
  against a fresh `runtime.create_machine()` before starting — all matched
  the survey exactly (`_SimQueenB` seg6:3DC2/668, `_DoFoodInB` seg6:492A/678,
  `_DoDigOutB` seg6:4EB0/686). Disassembled via a from-scratch linear
  disassembler (recreated `lindis_win16.py` was already sitting in the
  scratchpad from a prior session — reused rather than rebuilt).
- Same 4-arg `_DoNestAntB` dispatch signature `_DoDigInB` already
  established: `x=[bp+6]`, `y=[bp+8]`, `mode=[bp+10]`, `caste_sub=[bp+12]`.
  Only `mode == 0x0C`/`== 0x0D` do anything; any other value is a complete
  no-op. Composes `queen_move_b`, `find_in_b_list`, `in_nest_bounds`,
  `place_egg_b`, and — a genuine simplification this session found —
  `dec_eat_b` reused VERBATIM for this routine's own inline trailing
  hunger-tick block (`pack[0x7402]`/`dgroup[0xAC82]`/`dgroup[0xAC86]`/
  `simant_data_group[0x8A60]`): its disassembled field accesses match
  `_DecEatB`'s own byte for byte, confirming both independently.
- Resolved 11 DGROUP pointer-globals (`0xC350`..`0xC378`) fresh via
  `m.mem.rw(dg, off)` vs `m.seg_bases[...]` on a live machine rather than
  trusting the prior report — all PACK/SIMANT_DATA_GROUP selectors,
  matching already-established field names (`pack[0x9B6A]` slot,
  `simant_data_group[0x3D18+slot]` caste, `pack[0x78E8]` queen counter,
  `simant_data_group[0x8362]/[0x8364]` the SAME "recorded dig position"
  pair `_PlaceBlackQueen` initializes at colony founding, `pack[0x75FC]`
  throttle bitmask, `simant_data_group[0x8A60]` no-starve flag).
- Two presentation-only calls omitted per the established core/
  presentation split (stubbed in the oracle tests): `SIMANT!_PictStrnDialog`
  (seg1:615A, mode 0x0C's starvation-death message) and
  `ANTEDIT!_QueenBalloons` (seg3:4A44, mode 0x0C's post-move speech
  balloon, gated on `simant_data_group[0x85FC] != 0`).
- **A real bug caught before trusting the ASM oracle**: an early reading of
  mode 0x0C's control flow had `queen_move_b` called whenever the
  starvation check didn't fire (i.e. on `NOT(roll64==0 AND AC86==0)`).
  The real ASM's `or ax,ax` / `jnz` pair actually jumps PAST the
  `queen_move_b` call site on ANY nonzero `_SRand64()` roll (the common
  63-in-64 case) straight to the occupancy pre-check — `queen_move_b` is
  only ever reached on the rare `roll64==0 AND dgroup[0xAC86]!=0`
  combination. Caught immediately by the FIRST real-ASM state-diff run: a
  divergence in the `_SRand*` LFSR seed itself (the two control-flow
  readings consume a different number of `_SRand*` calls), not a subtle
  state mismatch — fixed before any test beyond the reproducer was kept,
  exactly the "confidently-traced but wrong" mistake class this project's
  workflow warns about. Independently double-checked by calling the real
  `_QueenMoveB` in isolation against the recovered `queen_move_b` with the
  post-`_SRand64` seed value — the two already agreed, proving the bug was
  in `_SimQueenB`'s own control flow, not in the pre-existing composed
  function.
- Also confirmed real, non-obvious asymmetries ported verbatim rather than
  unified: the occupancy pre-check's facing direction is `(caste ^ 0xFC) &
  7` in mode 0x0C but plain `caste & 7` (no XOR) in mode 0x0D's own first
  pre-check, and the "expected occupant" byte is `(caste + 8) & 0xFF` in
  mode 0x0C but `(caste - 8) & 0xFF` in mode 0x0D — genuinely different
  arithmetic at each site, confirmed via the raw disassembly, not assumed
  symmetric. Mode 0x0D's SECOND (placement-direction) computation uses yet
  a THIRD direction formula, `(caste_sub ^ 0xFC) & 7` — the caller-supplied
  arg, not a live caste re-read, matching `_DoDigInB`'s own dig-direction
  precedent. The occupancy pre-checks dereference the nest life-grid with
  NO bounds check beforehand (unlike `_DoDigInB`'s explicit `0..0x3F`
  gate) — ported as flat 16-bit-wrapped address arithmetic, not
  artificially bounds-checked.
- 17 new state-diff/gate tests, all green on real ASM (no-op mode
  parametrized over 4 values, mode 0x0C: starvation/move-succeeds/
  move-fails-then-occupancy-check/occupied-direct/occupied-via-blist/
  clear-kills-self/balloon-flag; mode 0x0D: queen-count-zero-places-egg/
  ahead-clear-kills-self/ahead-occupied-places-egg/out-of-bounds/
  throttle-blocks/food-roll-blocks). Suite: simant 2057 (+17), full suite
  green.
- `_DoFoodInB` and `_DoDigOutB` (the other two `_DoNestAntB` dependencies
  from this session's brief) not yet attempted — next up.

## 2026-07-15 (cont.207) — /goal grind: _DoDigInB (seg6 behavior tier, cont.)
- RECOVERED `do_dig_in_b`/`_dig_in_b_mode_refresh` (`_DoDigInB`, SIMANTW.SYM
  seg6:4BD0, FAR return, 736 bytes) — the black nest ("B"-list) ant
  dig-forward per-tick behavior, the second Target from this session's
  brief. FOUR args, not the three the scoping survey's summary named:
  `x=[bp+6]`, `y=[bp+8]`, `mode=[bp+10]`, and an unlisted fourth
  `caste_sub=[bp+12]` (the caller's own precomputed `(mode&0x78)>>3` —
  confirmed by its direct `push ss:[bp+12]` into `get_new_mode_b`'s sole
  arg, no computation from `mode` at this call site at all).
- Composes `get_new_mode_b`, `get_enter_dir_b`, `is_it_dirt`,
  `dig_tile_them_b`, `is_yellow_ant`, `find_in_b_list`, `get_out_b`,
  `get_winner`, and — a genuine simplification this session found —
  `_try_eat_food` reused VERBATIM for the tile-range-gated food-nibble +
  colony-growth-trigger tail: its own `(MAP_PLANE_BASE[2], 0x9EA4, 0xAC82,
  0xAC98, 0x7402, 0xAC86)` argument set matches this routine's own
  disassembled field accesses byte for byte, confirming both
  independently rather than composing a near-duplicate. `GR!_myBeginSound`
  (the dig-success sound) is presentation-only, omitted per established
  precedent (stubbed in the oracle test, same as `_AddFood`'s own call).
- One raise-loudly gate, matching `check_nest_fight_b`'s EXACT precedent
  for the exact same call: `SIMANT1!_YellowFight(2, pack[0x9B6A])` (seg6:
  823E, same `push cs; call near` far-call-emulation idiom, same `(2,
  slot)` argument pair already established) — fires when the destination
  cell holds the player's yellow ant AND `dgroup[0xCE98] != 0`; when
  `dgroup[0xCE98] == 0` the yellow ant is instead treated as an empty
  cell and the move proceeds normally (independently confirmed via a
  300-case randomized fuzz harness: zero disagreements between "real ASM
  reaches an unrecovered opcode" and "recovered code raises").
- **A real bug caught before trusting the ASM oracle**: an early reading
  of the `caste_sub in (2, 6)` branch had the polarity BACKWARDS (shortcut
  triggers on `caste_sub NOT in (2, 6)`, matching `do_forage_ant`'s own
  polarity for the analogous check — a `jz` at seg6:4BDA/4BE0 jumps PAST
  the shortcut to the main body exactly when `caste_sub` matches `2`/`6`,
  the opposite of an initial mis-reading). Caught immediately by the
  FIRST real-ASM state-diff run (a hard mismatch, not a subtle one) and
  fixed before writing any test beyond the reproducer — exactly the class
  of "confidently-traced but wrong" mistake this session's brief warned
  about, resolved by trusting the oracle over the manual read.
- Also independently confirmed the scoping survey's claim that the
  mode-derived 8-neighbour table at DGROUP `0xC364`/`0xC366` resolves to
  the SAME `SIMANT_DATA_GROUP` segment (`0x5294`) `do_forage_ant`'s own
  compass-table reads use (fresh `m.mem.rw` against a live
  `runtime.create_machine()`, not trusted from the report) — true, and
  reused the identical live-read pattern rather than the `GET_BEST_DIR_DX`/
  `DY` Python constants, matching this routine's own compiled reads.
- `dir_caste` (the stamped "turn to face" value, computed once early) is
  what `get_winner`'s second argument uses in the fight branch — NOT a
  fresh re-read of the acting slot's caste field, which by that point may
  already reflect `dig_tile_them_b`'s own `+0x18` success bump. The FINAL
  move's own new-caste computation, by contrast, DOES re-read fresh
  (confirmed via the raw disassembly: two textually-similar computations,
  genuinely different data sources, ported as such rather than unified).
- 12 new state-diff/gate tests (9 parametrized branch cases, a dedicated
  yellow+CE98==0 case, a forced fight-found case exercising the
  get_winner tail, and the `_YellowFight` gate-raises test), all green on
  real ASM. Suite: simant 2040 (+12), full suite green.
- Both scoping-report Targets now fully recovered and verified this
  session: `_DoForageAnt` (cont.206) and `_DoDigInB` (this entry).

## 2026-07-15 (cont.206) — /goal grind: _DoForageAnt (seg6 behavior tier)
- RECOVERED `do_forage_ant`/`_forage_jitter` (`_DoForageAnt`, SIMANTW.SYM
  seg6:1E42, arg slot=[bp+4]; NEAR return, 1126 bytes) — the yard ("A"-list)
  foraging-ant per-tick behavior, the biggest remaining gap per README's
  own "what's done vs missing". Re-verified the prior scoping survey's
  call table from scratch via a fresh linear disassembly (recreated the
  `dos_re.lift.decode.decode_one`-over-`runtime.create_machine()` scratch
  disassembler cont.85 established) rather than trusting the report.
- Composes `is_valid_a`, `go_in_nest`, `get_new_mode`, `get_forage_dir`,
  `pickup_food_a`, `is_yellow_ant`, `find_in_a_list`, `get_winner`,
  `jam_scent_bn`/`rn`, `dec_t_smell`, `alarm_here2` — all already
  recovered. Two genuine raise-loudly gates, matching the established
  `try_move_dir_b`/`check_nest_fight_b`/`r` fail-loud precedent for
  unrecovered dependencies:
  - `SIMANT!_DoTroph` (seg1:846E) — gated on `pack[0x9AF2] == 1`. The
    scoping report flagged this routine's own comparison as possibly
    `==1` vs `try_move_dir_b`'s `!=0` on the SAME field; independently
    re-derived from the raw ASM (`cmp ..., 0001h`, a genuine equality
    test, not `!=0`) AND cross-checked for behavioral equivalence: the
    field's only write site anywhere in this codebase (`set_my_health`)
    only ever stores `0` or `1`, so the two forms agree here, but `==1`
    is what's ported (byte-exact to the real instruction regardless).
  - `SIMANT1!_YellowFight` (seg6:823E, called via the same-segment
    `push cs; call near` far-call-emulation idiom, args `(slot, 1)`) —
    **a call the prior scoping survey's exhaustive-call-scan did NOT
    list**, found independently in this session's own from-scratch
    disassembly of the "occupied destination" branch. Not a low-
    confidence surprise (unlike the class of bug `_CreateNewHole` hit
    once): the gate condition (`(caste ^ dgroup[0xCE98]) & 0x80`) and its
    placement were fully, confidently traced and confirmed against the
    real ASM (see below), so recovery proceeded rather than stopping.
  - Both gates independently CONFIRMED against the real ASM oracle, not
    just derived: a fuzz harness (300 randomized slot/position/caste/
    occupant/RNG-seed trials) found the real ASM crashes on an unrecovered
    downstream opcode in EVERY case (and only those cases) where the
    derived gate condition evaluates true, and matches byte-exact in
    EVERY case it evaluates false — zero disagreements. One case was
    further hand-traced instruction-by-instruction to confirm the real
    ASM actually reaches `_DoTroph`'s call site (seg6:21B0), not
    `_YellowFight`'s (2171), when only the troph gate should fire.
  - Also confirmed a real asymmetry: `dec_t_smell`'s "no better forage
    direction, stay put" call site passes ALREADY-HALVED `(x>>1, y>>1)`
    coordinates into a function that halves its args AGAIN internally —
    a genuine quarter-resolution quirk in the compiled code (the OTHER
    call site in this same routine passes full-resolution coords),
    ported verbatim rather than "corrected".
- Two real test-authoring bugs caught and fixed before trusting the ASM
  oracle: (1) `get_forage_dir`'s neighbor scan reads simant_data_group's
  OWN live 8-entry compass dx/dy table (offsets 0..15) — a from-scratch
  `ByteBackend` test fixture left all-zero collapses every neighbor onto
  the ant's own cell; (2) `get_winner`'s C-runtime `_RRand` state
  (`RAND_STATE_OFF`) drifts during normal VM execution (confirmed via a
  live before/after read) — every existing get_winner-consuming test
  already pins it explicitly; the `_DoForageAnt` fight-path test hadn't,
  causing a real winner-computation mismatch until pinned to a fixed value.
- 15 new state-diff/gate tests, all green on real ASM (14 covering every
  branch: idle-roll32, alarmed-territory, caste-sub-not-2-6, move black/
  red, pickup outside/inside, crowded-jitter, occupied-same-colony,
  occupied-yellow-no-troph, near-origin-edge, direction<0 quirk, and a
  dedicated forced-direction fight-found case exercising the
  get_winner/alarm_here2 tail specifically, plus 2 dedicated
  `pytest.raises(NotImplementedError)` gate tests).
- Suite: simant 2028 (+15), full suite green.
- `_DoDigInB` (Target 2 per this session's brief) not yet attempted this
  session — next up.

## 2026-07-15 (cont.205) — /goal grind: house/yard tile-decoration cluster (11-for-11)
- RECOVERED the full Tier-1 house/yard tile-decoration cluster flagged by the
  prior scoping survey — all 11 symbols, all seg5, all clean/deterministic
  yard-map tile stampers with NO RNG/ant-list involvement, all byte-exact on
  the FIRST real-ASM run (no bugs caught this session):
  - `fill_map` (`_FillMap`, seg5:3F54, args x1/x2/y1/y2/value=[bp+6..14];
    FAR, 86 bytes) — fills yard-map columns `x1..x2`, rows `y1..y2`
    (inclusive) with a fixed byte; no-ops if `x1>x2` or `y2<y1` (the ASM's
    own per-column re-check of `y2<y1` is loop-invariant across columns, so
    it collapses to one whole-rectangle gate rather than needing to be
    modeled per-column).
  - `tile_frame1`/`tile_frame2` (`_TileFrame1`/`_TileFrame2`, seg5:3944/3AA2,
    same x1/x2/y1/y2 signature; FAR, 350 bytes each) — draw a rectangular
    border (top/bottom rows gated on `x1<=x2`, left/right columns gated on
    `y1<=y2`, but the 4 corner tiles are written LAST and UNCONDITIONALLY,
    always overwriting any edge tile underneath — confirmed via the raw
    disassembly, not assumed). `_TileFrame2` is instruction-for-instruction
    identical to `_TileFrame1` with a different (self-consistent) 8-tile
    palette — extracted a shared `_tile_frame` helper.
  - `make_plug_v`/`make_plug_h`/`make_knob`/`make_penny`/`make_clip`
    (`_MakePlugV`/`_MakePlugH`/`_MakeKnob`/`_MakePenny`/`_MakeClip`,
    seg5:3D02/3E46/3E88/3ECA/3F0C, args x/y=[bp+6/8]; FAR, 66/66/66/66/72
    bytes) — each blits a small fixed-size glyph raster (5x4, 4x5, 5x5,
    3x3, 3x3) read from a contiguous run of DGROUP static data
    (`0x2314..0x2368`) onto the yard map at `(x,y)`. `_MakeClip` is the ONE
    asymmetric case: a `0` table byte is a transparent "skip this cell"
    marker (confirmed real — the live table's own `0x2360`/`0x2368` bytes
    ARE `0`), whereas `_MakePenny`'s otherwise-identical 3x3 shape copies
    every byte unconditionally. Extracted a shared `_stamp_glyph(table_base,
    dest_base, width, height, sparse)` helper reused by all five, plus
    `make_outlet_v`/`make_outlet_h` below.
  - `make_outlet_v`/`make_outlet_h` (`_MakeOutletV`/`_MakeOutletH`,
    seg5:3C00/3D44, args x/y=[bp+6/8]; FAR, 258 bytes each) — a
    `0x63`-filled background panel (9x13 / transposed 13x9) +
    `tile_frame1` border (BOTH orientations use `_TileFrame1`, not
    `_TileFrame2` — confirmed via the raw call target) + two copies of the
    matching plug glyph (V reuses `make_plug_v`'s own `0x2314` table
    stacked vertically 5 rows apart; H reuses `make_plug_h`'s `0x2328`
    table side-by-side 5 columns apart) + one `0x65` "screw" tile. The
    panel-size preamble is byte-for-byte `_FillMap`'s own inlined shape
    with `x2`/`y2` hardcoded to `x+8`/`y+12` (V) or `x+12`/`y+8` (H) —
    composed directly as `fill_map(dgroup, x, x+8, y, y+12, 0x63)` rather
    than re-derived.
  - `make_kitchen_wall` (`_MakeKitchenWall`, seg5:3698, NO args; FAR, 196
    bytes) — repaints the ENTIRE yard-map plane (all 128 columns; shared
    between the outdoor yard and indoor house-interior views) as a fixed
    kitchen scene: floor band (`y=0..23`->`0x62`, `y=24..63`->`0`, both
    composed as whole-plane `fill_map` calls), 3 full-width wall lines at
    `y=0/8/16` (`0x68`), a stud-post grid at every 8th column checking the
    CURRENT tile (`0x62`->`0x66`, else->`0x67`), a bottom border row `y=23`
    with the same current-tile test (`0x62`->`0x68`, else->`0x69`), two
    `make_outlet_v` panels at `(0x24,2)`/`(0x54,2)`, and a `pack[0x9C66]`
    write (the SAME "current fall-direction table index" word `food_fall`
    already reads) set to `2`. Resolved a NEW DGROUP pointer-global,
    `0xC434`, fresh against `runtime.create_machine()`'s own `seg_bases` —
    it lands on PACK, distinct from `create_new_hole`'s own `0xC3FE`/
    `0xC400` pair (which resolve to SIMANT_DATA_GROUP).
- 33 new state-diff cases (6 `fill_map`, 6 `tile_frame1`, 3 `tile_frame2`,
  11 glyph-stamper mid/origin/near-max cases, 6 `make_outlet_v`/`h`, 1
  `make_kitchen_wall`) — every one green on the FIRST real-ASM run; the
  offline `python -c` prototype (validated before ever touching the VM,
  per this project's own standing practice) caught zero bugs this time —
  a genuinely clean cluster, unlike most sessions' `_FixExitMapR`/region-
  window surprises.
- Suite: simant 2013 (+33), full suite green.

## 2026-07-15 (cont.204) — /goal grind: _CreateNewHole/_DigMyNewHole/_DigMyTile
- RECOVERED `create_new_hole` (`_CreateNewHole`, SIMANTW.SYM seg5:171A, args
  x=[bp+6], y=[bp+8]; FAR return, 506 bytes) — the low-level "stamp a hole
  marker on the yard map + dig the connecting nest tunnel" primitive.
  No-ops unless `1 <= x <= 0x7E` and `1 <= y <= 0x3E` (independently
  re-derived from the raw `jge`/`jl` pairs, not trusted from a prior
  partial trace). `pack[0x9B6E]` ("inside") nonzero stamps the yard cell
  `0x59`; zero stamps `0x50` and ALSO carves the same `HOLE_EDGE_TILES`
  8-neighbour edge pattern `make_new_hole_b`/`r` use (confirmed via a
  THIRD independent access path — DGROUP pointer-globals `0xC3FE`/`0xC400`
  both resolving to the SIMANT_DATA_GROUP selector, reached through
  `es:[bx+8]`/`es:[bx+0]`, landing on the exact same fixed compass table
  those routines' own `sbyte(0/8+di)` literal reads use) before
  reconverging onto the SAME dispatch the "inside" branch uses (a genuine
  `jmp` back into shared code, not two independent tails). Dispatches on
  `x < 0x40` (black) vs `x >= 0x40` (red), recording into the SAME
  `_FillHolesBN`/`_FillHolesRN` per-column arrays and "last hole" 4-word
  scratch tables `make_new_hole_b`/`r` already established.
  - Re-verified the branch-polarity question from the prior session's
    partial trace from scratch, independently: `jz` at seg5:1750 jumps to
    the `0x50`/OUTSIDE branch when `pack[0x9B6E] == 0`, confirming the
    prior session's own correction (fallthrough = INSIDE) was right.
  - Resolved the `dig_tile_b` argument-mapping question definitively: the
    near call at seg5:1774 (`push 1; push si; call 1FE4`) really is
    `dig_tile_b(x=y, y=1)` — a genuine coordinate SWAP using this
    routine's own `y` argument as `dig_tile_b`'s `x`, confirmed by fully
    decoding the `x >= 0x40` sibling branch (seg5:1806-18EF) and finding
    it BYTE-IDENTICAL to `dig_tile_r`'s entire body (same
    `_dig_tile_reroll_and_track` fields `0x9DDC`/`0x9DE2`/`0x7A56`/
    `0x9FBA`/`0x9FD2`, same 4x `_SmoothEdgesR` + `_FixExitMapR` closing
    sequence) called as `dig_tile_r(x=y, y=1)` — the SAME swap, on the red
    twin, independently confirming the pattern rather than assuming
    symmetry. Composed `dig_tile_b(y, 1)`/`dig_tile_r(y, 1)` directly
    instead of re-deriving either reroll/track/smooth chain a second time
    — the single biggest composition win of this slice, exactly as
    flagged. The `0x58E9` address noted in the handoff (`MAP_PLANE_BASE[3]
    + 1`) resolved to option (a): a genuine, deliberate off-by-one that's
    really `map_cell_offset(3, y, 1)` in disguise (`(y<<6)+base+1 ==
    base+(y<<6)+1`), not a stray field or a transcription slip.
- RECOVERED `dig_my_new_hole` (`_DigMyNewHole`, SIMANTW.SYM seg5:16AE, args
  x=[bp+6], y=[bp+8]; FAR return, 108 bytes) — the gate + trigger for
  `create_new_hole`. Its own bounds gate (`1 <= x <= 0x7F`, `1 <= y <=
  0x3F`) is genuinely LOOSER than `create_new_hole`'s own (`0x7E`/`0x3E`),
  confirmed by direct comparison of both raw bound checks — a coordinate
  that clears this gate can still be silently rejected one level down.
  `pack[0x9B6E]` inside: clear iff the yard tile is `< 0xC8`; outside:
  composes the already-recovered `_clear_3x3(dgroup, plane=1, x, y)`
  helper (confirmed via the near call's `push y; push x; push 1` order —
  `plane=1` is the last-pushed/first-formal argument, matching
  `_IsClear3x3`'s established `(plane, x, y)` signature). Clear: composes
  `create_new_hole(x, y)` and returns 1; not clear: returns 0.
- RECOVERED `dig_my_tile` (`_DigMyTile`, SIMANTW.SYM seg5:1914, args
  plane=[bp+6], x=[bp+8], y=[bp+10]; FAR return, 498 bytes) — gated by a
  new private helper `_is_it_digable_at` (the VM-touching wrapper around
  the already-pure `is_it_digable(plane, tile)`, matching `_clear_3x3`'s
  own precedent: `plane < 2` short-circuits with no map read at all,
  matching the ASM's own residue notes in `hooks.py`). Confirmed exactly
  9 distinct call targets via `symbols.nearest_symbol` on every call site
  (matching the pre-session survey's count precisely): `_IsItDigable`,
  `_MakeNewHoleB`, `_DigTileB`, `_MakeNewHoleR`, `_IsItDirt`, `_SRand8`
  (the survey's one unresolved dependency — now composed via
  `srand_pow2(seed, 7)` inside `dig_tile_r`'s own reroll helper),
  `__aFldiv`, `_SmoothEdgesR`, `_FixExitMapR`.
  - `plane == 2`: if `y <= 1`, unconditionally stamps row 0 of column `x`
    on the black nest map (`MAP_PLANE_BASE[2] + (x << 6)` — no `y` term at
    all, confirmed via the raw address arithmetic) to `0x18` and composes
    `make_new_hole_b(x)`; `y == 0` then returns immediately. Otherwise
    (`y == 1`, or the original `y > 1` skipping the prelude): composes
    `dig_tile_b(x, y)` — a direct, UNSWAPPED pass-through, genuinely
    different from `create_new_hole`'s own swapped call — then returns.
  - `plane != 2`: the same `y <= 1` prelude on the red map, composing
    `make_new_hole_r(x)` instead; the remainder (reached for `y == 1`
    after the prelude, or directly for `y > 1`) is BYTE-IDENTICAL to
    `dig_tile_r`'s own entire body at this routine's own unswapped `(x,
    y)`, so it composes `dig_tile_r(x, y)` rather than re-deriving it.
  - Caught a genuine bug on the FIRST real-ASM run, not a region-window
    one this time: a first draft additionally composed `fix_exit_map_r(x,
    y)` after `dig_tile_b` in the `plane == 2` branch, misreading the
    `jmp near -> 2F99:1AFF` at seg5:1961 as landing BEFORE the
    `_FixExitMapR` call at seg5:1AFC. It actually lands EXACTLY on that
    call's own `ret=1AFF` return address (the byte immediately after the
    3-byte `call near` instruction) — i.e. the jump SKIPS the entire
    `_SmoothEdgesR`x4 + `_FixExitMapR` closing block below it, landing on
    a stray (harmless, `leave`-discarded) `add sp,4`. Caught by one stray
    SDG byte at `_FixExitMapR`'s own target address that the real ASM
    never touched; fixed by dropping the spurious extra call.
- 30 new cases (10 `create_new_hole` branch/boundary cases + 4 dedicated
  out-of-bounds no-ops, 7 `dig_my_new_hole` gate/clear cases, 9
  `dig_my_tile` plane/row-boundary cases) — all green after the one
  `_FixExitMapR` fix; every case validated first via a plain script
  (mirroring `_run_and_diff_segs`) before being ported into pytest, per
  this session's own standing practice.
- Suite: simant 1980 (+30), full suite green.

## 2026-07-15 (cont.203) — /goal grind: _AddWater
- RECOVERED `add_water` (`_AddWater`, SIMANTW.SYM seg5:0B8A, arg
  col=[bp+6]; FAR return, 202 bytes; composes the already-recovered
  `drown_b_list`/`drown_r_list`; far-calls `ANTEDIT!_ZapEuMapAt` twice
  per row — screen-redraw invalidation, presentation-only, stubbed in
  the oracle test rather than modeled). Floods one full nest column
  `y=col`: marks any black/red ants standing there as drowning, then
  erodes every cell's tile on BOTH nest planes 2 and 3 across the
  whole `x=0..63` range — a tile `< 0x20` becomes the fixed `0x4E`;
  otherwise `tile + 0x2F` (truncated to a byte).
- 4 cases (erosion below/above the `0x20` threshold, drowning marks
  on both colonies, and the column-index boundaries `0`/`0x3F`) — all
  green on the first real-ASM run.
- Suite: simant 1950 (+4), full suite green.

## 2026-07-15 (cont.202) — /goal grind: _PlaceDrop/_InitWater
- RECOVERED `place_drop` (`_PlaceDrop`, SIMANTW.SYM seg5:0ACC, arg
  slot=[bp+6]; FAR return, 170 bytes; composes the already-recovered
  `r_rand`) and `init_water` (`_InitWater`, seg5:0B76, NO args; FAR
  return, 20 bytes; composes `place_drop` 100 times) — a clean 2-for-1
  from the fresh survey's "water micro-chain".
- `place_drop` rolls a random yard cell via `_RRand` (the C-runtime
  generator, NOT the `_SRand*` LFSR), records it into
  `pack[0x79E6+slot]`/`[0x7A72+slot]` regardless of outcome, and — if
  the cell is clear enough (`< 0x0E`) — stamps the water tile `0x74`
  and washes away nearby scent at the SAME half-res (`>>1`, 64x32)
  grid cell the alarm/scent family already established: zeroes the
  alarm grid and its still-unrecovered companion field outright
  (the SAME two fields `clr_arrays` already zeroes in bulk), zeroes
  the trail-scent grids outright, but DECAYS (by `0x14`, floored at
  `0`, not zeroed) the nest-scent grids — a genuine asymmetry
  confirmed via the raw disassembly (two fields use `sub`+clamp, the
  other four use a flat `mov ...,0`).
- 4 cases (`place_drop`'s clear-cell decay and floor-to-zero
  sub-branches, its blocked-cell no-op, and `init_water`'s full
  100-iteration composition) — 3 of 4 hit a region-window bug (this
  session's most common bug class) TWICE in a row: the SDG region's
  upper bound needed widening first for a single clear-cell hit at a
  high half-res index, then again for `init_water`'s 100 random rolls
  eventually landing on the grid's own maximum index (`2047`) — no
  code bug, both fixes were pure test-region widening, caught
  immediately by the same `IndexError` pattern this bug class always
  produces.
- Suite: simant 1946 (+4), full suite green.

## 2026-07-15 (cont.201) — /goal grind: _IsLiftable
- RECOVERED `is_liftable` (`_IsLiftable`, SIMANTW.SYM seg5:97CA, args
  plane=[bp+6], x=[bp+8], y=[bp+10]; FAR return, 276 bytes; composes
  the already-recovered `find_egg_at` and `is_it_food`). Whether an
  ant could pick up whatever's at `(plane, x, y)`.
- `find_egg_at` runs first and unconditionally (it has its own
  internal validity gate); only its SECOND tuple element (the
  AX-returned egg/larva tile value) is ever consulted — the first
  (the OUT-pointer slot) is written but never read, matching the real
  ASM's own unused locals. Separately reads the raw map tile: an
  out-of-bounds `is_valid_location` failure, OR (independently
  confirmed via the raw disassembly) a `plane` other than exactly
  `0`/`1`/`2`/`3`, falls back to a `-1` sentinel that never matches
  any range check — even though a `plane > 3` could still legitimately
  PASS `is_valid_location`'s own nest-bounds check, since that routine
  only distinguishes "yard" (`<=1`) from "nest" (`>1`), not the exact
  plane number. Liftable if the tile is food (`plane <= 1`, composing
  `is_it_food`), a fixed "liftable object" tile range (`plane > 1`,
  `[0x10,0x13]`), a `plane`-specific special tile (`[0x51,0x53]` only
  for `plane == 1` exactly, `[0x30,0x31]` only for `plane > 1` —
  `plane == 0` matches neither, confirmed via the raw disassembly's
  `jnz`-skip polarity), or the egg tile's growth-stage byte is `1..7`.
- 8 cases (food/plain-dirt on the yard, the plane-1-only and
  plane>1-only special-tile ranges, the plane-0-doesn't-count check,
  an out-of-0..3-range plane forcing the sentinel fallback, and an
  out-of-bounds position) — all green on the FIRST real-ASM run (one
  test-fixture bug caught and fixed immediately: the seed helper tried
  to write a map tile for the deliberately-invalid `plane=4` case,
  which has no real map base — not a recovery bug).
- Suite: simant 1942 (+8), full suite green.

## 2026-07-15 (cont.200) — /goal grind: _SRand2.._SRand256 explicit coverage
- Ran a fresh Explore survey now that the third survey's whole
  candidate list (pillar/sow cluster, `_GstrR`/`_GetStrategy`,
  `_AddFood`/`_FeedAnts`) is exhausted. Investigated `_ClearLife`/
  `_SetMyLife`/`_SetLife` as prep work for `_YellowFight` per the
  prior survey's own suggestion, but abandoned partway through
  `_SetLife` (432 bytes, seg5:5D18) — it's genuinely more complex than
  it looked (a maze of plane-specific nest-tile/dig logic around grass
  tiles 0x1C-0x1F, composing the already-recovered `is_valid_location`
  and calling into `_IsItDirt` — confirmed already recovered — but
  ALSO a far call into an unidentified segment) — and even a full
  recovery of the trio wouldn't unblock `_YellowFight` itself (it has
  its own further blockers: `_AnimYellowFight`, `_GotoMyAnt`,
  `_ResetYellowVars`, `_YellowDeath`). Deferred; the fresh survey's
  top pick was better-scoped.
- The survey identified seg5:1182 (my `_SetLife` trace's one
  unresolved call) as `_IsItDirt` — already recovered, so not
  actually a blocker on its own; and far segment `0x18C0:0` as
  `ANTEDIT!_ZapEuMapAt` (a map-redraw invalidation entry point,
  matching this session's established presentation-only-stub
  precedent) — useful context for a future `_SetLife` attempt, but
  not pursued further this entry.
- Its #1 pick: the `_SRand1`.._SRand256` family (seg5, 9 symbols, 0
  calls each) was ALREADY semantically implemented generically via
  `srand1`/`srand_pow2` in `simone.py` (used pervasively since early
  in this session) but had NO explicit per-symbol byte-exact test —
  each of the 8 power-of-two siblings (`_SRand2` through `_SRand256`)
  differs from `_SRand1` only in a compile-time AND mask, confirmed
  once more directly from a fresh disassembly of all 9 back-to-back.
  Added `test_srand1_matches_asm`/`test_srand_pow2_family_matches_asm`,
  each hitting its own real ASM address directly (not just trusting
  the shared formula), plus cited every address in `simone.py`'s own
  module docstring. This is a bookkeeping/verification pass — no new
  Python logic — but it closes the "0 calls, real symbol, no
  dedicated test" gap the survey flagged as the single highest-
  leverage remaining item (many further seg5/6 routines cite specific
  `_SRand8`/`_SRand32`/etc. dependencies by name).
- 45 cases (5 seeds x 9 routines) — all green immediately (no bugs,
  since the underlying formula was already correct).
- Suite: simant 1934 (+45), full suite green.

## 2026-07-15 (cont.199) — /goal grind: _AddFood (clears _FeedAnts' gate)
- RECOVERED `add_food` (`_AddFood`, SIMANTW.SYM seg7:6A58, args
  count=[bp+6], flag=[bp+8]; FAR return, 514 bytes; calls `_SRand1`/
  `8`/`48`/`64`/`128`/`256`, composes `frac_sin`/`frac_cos`/
  `a_f_ldiv`, far-calls `GR!_myBeginSound` — presentation-only,
  stubbed per `_StartAttack`'s precedent). Scatters up to `count`
  food/rock piles in a roughly circular pattern around a center,
  using fixed-point trig to pick each candidate's offset from a
  random angle and radius, then range-classifies the existing map
  tile (split by `pack[0x9B6E]` "inside the nest") to decide whether
  to skip, increment, or stamp a new value — this was the LAST item
  from the third Explore survey, and its recovery clears the
  `NotImplementedError` gate `feed_ants` (recovered earlier this
  session) has carried since before this window.
- FOUR independent bugs caught across two rounds of real-ASM runs —
  this function's dense mix of arithmetic-shift math, signed 32-bit
  division, and swapped-looking branch pairs made it this session's
  highest bug-per-line recovery:
  1. `a_f_ldiv`'s 32-bit result must be truncated to its low word
     before use (only `AX`, never `DX`, feeds the ASM's addition) —
     caught immediately by absurd multi-billion coordinate values in
     an offline simulation, before ever touching the real ASM oracle.
  2. The `count >= 0` / `count < 0` branch pair was backwards: `count
     >= 0` takes the `_SRand128`/`_SRand64` fully-random-center branch
     (the ACTUAL loop count), `count < 0` is the FIXED-center,
     hardcoded-200-iteration branch — caught via a real-ASM run
     leaving the mirrored center fields at their pre-seeded stale
     values, which only makes sense under the corrected reading.
  3. `simant_data_group[0x836A]`/`[0x836C]` (the mirrored center), not
     `pack` — independently confirmed via a direct machine memory read
     of the pointer-global, not assumed from the surrounding
     PACK-heavy fields.
  4. The `tile < 4` and `tile in [4, 0x18)` stamp formulas were
     swapped — caught by a real-ASM run landing on the exact predicted
     cell but with a different tile value than either formula alone
     would explain until the branch pairing itself was re-checked
     against the raw `cmp bx,4; jge` target.
  A slow-pytest-diff false alarm along the way: a mismatched byte
  comparison inside a ~42KB region made pytest's assertion-rewriting
  diff machinery appear to hang for minutes on a failing case: same
  root cause, no separate fix needed, but worth remembering to
  fast-verify big-region cases with a plain script BEFORE trusting
  pytest's runtime when something looks stuck.
- Then wired `feed_ants` (previously gated) to actually compose
  `add_food` — and caught a FIFTH bug in the process: `_FeedAnts`
  pushes `1` then `0x96` before calling, which (per the established
  push-order convention) means `count=0x96` (150) and `flag=1` — the
  OPPOSITE of the pre-recovery docstring's `add_food(1, 0x96)` guess,
  caught when the real ASM's post-call food counter landed at 75
  (consistent with ~150 attempted placements), not the 1 a `count=1`
  reading would predict. `feed_ants`'s signature grew two new
  parameters (`table_view`, `table_off`) to reach the genuine runtime
  trig-table pointer, matching `frac_sin`/`frac_cos`'s own established
  convention.
- 6 new `add_food` cases (single-pile placement inside/outside with
  clear and non-clear starting tiles, a candidate landing exactly on
  the center, the 200-iteration fixed-center branch, and the
  presentation-call-firing `flag=1` case) plus 1 new `feed_ants` case
  (composing `add_food` for real) — all green after the fixes.
- Suite: simant 1889 (+5, net of the removed
  `test_feedants_addfood_gate_raises`), full suite green. This closes
  out every candidate from the third Explore survey.

## 2026-07-15 (cont.198) — /goal grind: _GetStrategy
- RECOVERED `get_strategy` (`_GetStrategy`, SIMANTW.SYM seg7:0000, NO
  args; FAR return, 460 bytes; calls `_SRand1(5)` x2, `_GetDis`,
  near-calls `_GstrR`/`_SetCasteProd`/`_SetModeProd`). The top-level
  per-tick strategy update — this closes out the `_GstrR`/
  `_GetStrategy` pair the survey flagged as newly tractable.
- Composes ALL THREE near-callees plus `get_dis`. Structure: zeroes a
  "danger nearby" flag (`pack[0x72EC]`); if `dgroup[0xCE80]==1` (the
  SAME world-state "mode" flag `is_it_yellow` reads), jitters two
  marker fields via `_SRand1(5)`, clamps to the yard bounds, and
  stores them — genuinely CONFIRMED dead-but-executed work for
  `pack[0x9FE4]` specifically, since it's unconditionally overwritten
  moments later regardless of this branch (kept faithfully because the
  `_SRand1` draws it consumes are observable via the shared LFSR
  seed); if `pack[0x9BD2]` is nonzero, composes `get_dis` and sets the
  danger flag on a close threat. Then mirrors `gstr_r`'s own tier
  logic but over swapped fields (`dgroup[0xAC86]` not `[0xAC88]`, no
  `_SRand32`/`_SRand128` longshot, a plain stored code instead of a
  fired attack) into `pack[0x9B8A]`. Finally composes `gstr_r` itself
  (storing ITS result into `pack[0x7690]`) then `set_caste_prod`/
  `set_mode_prod`.
- 6 cases (the `dgroup[0xCE80]` gate both ways, the `pack[0x9BD2]`
  danger-distance gate both ways, and two `dgroup[0xAC86]>=50` tier
  outcomes) — all green on the SECOND real-ASM run (two region-window
  bugs caught and fixed on the first: `0xCE80`/`0xCE7E` above the
  DGROUP region's upper bound, then `0x9FE4` above the PACK region's —
  this session's most common bug class, now recurring after several
  clean runs).
- Suite: simant 1884 (+6), full suite green.

## 2026-07-15 (cont.197) — /goal grind: _GstrR
- RECOVERED `gstr_r` (`_GstrR`, SIMANTW.SYM seg7:03C2, NO args; FAR
  return, 332 bytes; calls `_SRand32`, `_SRand128`). The red colony's
  "should we attack?" strategy pick — returns a threat-tier code
  `1..5`, or `0` whenever it decides to fire an attack this tick.
  Previously deferred because it fires `GR!_myBeginSong`/
  `SIMANT!_EditMessage` — re-examined now that `_StartAttack` (cont.189)
  already proved those two calls are presentation-only and stubbable;
  confirmed the inlined firing sequence is byte-identical to
  `_StartAttack`'s own body, so it composes `start_attack` instead of
  re-deriving it (the same "compiler inlined it, we compose it"
  pattern used throughout this session's pillar cluster).
- The whole function runs with `DS` explicitly overridden to the raw
  PACK selector (`5EF3h` literal, matching `_StartAttack`'s own
  precedent), reaching DGROUP fields via explicit `SS:` prefixes
  instead (SS == DGROUP in this small-model app) — independently
  confirmed both segments are genuinely in play, not a transcription
  slip. Logic: an idle "attack timer" gate (`pack[0x78DC]`, the SAME
  field `start_attack` sets) short-circuits to `0` while cooling down;
  otherwise a tick counter (`dgroup[0xAC88]`) selects a tier, with two
  independent chances to fire an attack early (a threshold check when
  the tier is fresh, and a randomized `_SRand32()`/`_SRand128()`
  double-longshot check when the tier is stale) before falling back to
  a plain tier-code return.
- 10 cases (using a new test helper, `_run_and_diff_segs_with_ax`,
  since this is the session's first multi-segment-mutating function
  that ALSO returns a meaningful value — covering the timer gate, both
  attack-firing paths including the exact SRand32/128 double-hit,
  every tier boundary, and the near-miss/condition-failure returns) —
  all green on the FIRST real-ASM run, no bugs this time.
- Suite: simant 1878 (+10), full suite green.

## 2026-07-15 (cont.196) — /goal grind: _DoPillar (cluster complete)
- RECOVERED `do_pillar` (`_DoPillar`, SIMANTW.SYM seg7:4CDC, NO args;
  FAR return, 1576 bytes — the largest single recovery this session;
  near-calls `_DoSow`/`_MakeAPill`/`_MakePillFood`, far-calls
  `_IsValidA` many times). The per-tick pillar lifecycle orchestrator
  — this closes out the entire "pillar/sow" cluster the third Explore
  survey flagged (`_InitSow`, `_DoSow`, `_InitAntLions`,
  `_MakePillFood`, `_MakeAPill`, and now `_DoPillar` itself, all
  recovered this session).
- Composes ALL THREE already-recovered near-callees plus
  `store_pillar_map`/`replace_pillar_map`/`is_valid_a`. Structure:
  `pack[0x9B6E]==1` ("inside") no-ops entirely (before even `do_sow`
  runs); otherwise always runs `do_sow` first. If inactive
  (`simant_data_group[0x8A8A]==0`): activates via `make_a_pill`, sets
  active + seeds the growth counter `pack[0x78D4]=4`. Once active: a
  mode-selected (`pack[0x9B1E]`) "front" cell one step ahead gates
  everything — if occupied, counts occupied cells in the surrounding
  3x3 block and kills the pillar (composing `make_pill_food`) past a
  threshold of 5, else no-ops; if clear, decrements the growth
  counter and, on a 5-tick cycle, alternates a `replace_pillar_map`
  cache-restore (the "growth" event, `counter==4`) with a direct
  arm-segment tile stamp (any other counter) at the SAME distance —
  always ALSO stamping a second, closer arm tile — then, once the
  counter reaches `0`, shifts the pillar's own position one step
  toward the front, checks a generous bounding box (deactivating if
  exceeded), and otherwise caches + repaints its new position
  (composing `store_pillar_map`) before resetting the counter to `5`.
- Caught a genuine MUTUAL-EXCLUSIVITY bug on the first real-ASM run:
  first-draft code did BOTH the growth cache-restore (on
  `counter==4`) AND the direct near-tile stamp (unconditionally) at
  the SAME distance — but the raw disassembly's control flow shows
  these two are mutually exclusive branches (the `counter==4` growth
  dispatch converges directly to the FAR-tile dispatch, entirely
  bypassing the near-tile dispatch that the `counter!=4` path takes
  instead). Caught immediately via a single failing byte at the exact
  cache-vs-direct-tile collision cell; fixed by making the near-tile
  stamp an `else` branch of the `counter==4` check instead of an
  unconditional follow-on.
- 8 cases (inside-nest no-op, activation, front-occupied at both
  crowd thresholds, a mid-cycle direct-stamp-only tick, the
  counter-hits-4 growth tick, the counter-hits-0 movement tick, and a
  movement-goes-out-of-bounds deactivation) — all green after the
  mutual-exclusivity fix, covering every major branch of this state
  machine.
- Suite: simant 1868 (+8), full suite green.

## 2026-07-15 (cont.195) — /goal grind: _MakeAPill
- RECOVERED `make_a_pill` (`_MakeAPill`, SIMANTW.SYM seg7:53DA, NO
  args; FAR return, 768 bytes; calls `_SRand1` x2, `_IsValidA` x2).
  Rolls a fresh direction (`_SRand1(4)`, stored into `pack[0x9B1E]` —
  the SAME `_pillar_cache_index` rule flag `make_pill_food` reads),
  places the tracked pillar at a random point along ONE of the yard's
  4 edges (mode 0: south, `x` random `y=0x3F`, tile `0x6C`; mode 1:
  west, `x=0`, `y` random, tile `0x6B`; mode 2: north, `x` random,
  `y=0`, tile `0x6F`; mode 3: east, `x=0x7F`, `y` random, tile `0x68`)
  and, if valid, caches its current tile (composing the
  already-recovered `store_pillar_map` — the real ASM's cache-store
  math is inline but byte-identical to that routine's own body) then
  stamps the mode-specific pillar tile.
- The real ASM's two `_IsValidA` calls use IDENTICAL `(x, y)` (a pure
  deterministic predicate, same coordinates both times) — collapsed to
  one check, matching `pill_food_tile`/`_paint_pillar_arm`'s own
  precedent. Also noted: since each mode's fixed coordinate (`0`,
  `0x3F`, `0x7F`) and each mode's random draw range (`_SRand1(0x80)`
  or `_SRand1(0x40)`) are both always within `is_valid_a`'s bounds,
  the "invalid" branch is mathematically unreachable for this
  function's own random-generation scheme — same category as
  `make_a_pill`'s own dead `mode>=4` branch (`_SRand1(4)` can only
  return `0..3`).
- 4 cases (one per mode/edge, coordinates precomputed offline via the
  already-verified `srand1`) — all green on the first real-ASM run.
- Suite: simant 1860 (+4), full suite green.

## 2026-07-15 (cont.194) — /goal grind: _MakePillFood
- RECOVERED `make_pill_food` (`_MakePillFood`, SIMANTW.SYM seg7:57D2,
  NO args; FAR return, 560 bytes; calls `_IsValidA` x2 per cell).
  Paints one 6-cell "arm" out from the tracked pillar's own position
  (`simant_data_group[0x8A8C]`/`[0x8A8E]`, the SAME pair
  `is_pill_dead` reads), direction selected by `pack[0x9B1E]` (the
  SAME `_pillar_cache_index` rule flag `_InitPillar` resets) —
  `0`=south, `1`=west, `2`=north, `3`=east; any other value is a
  no-op.
- The four direction blocks in the raw ASM are BYTE-IDENTICAL past
  their own fixed `(dx_step, dy_step)` per-cell delta — including the
  SAME `_pillar_cache_index` bit0 test (which axis is "the one that's
  changing" always matches which axis the existing rule already
  selects) and the SAME food-tile (`0x4B` if `<0x18`) stamp — so all
  four compose ONE new shared helper, `_paint_pillar_arm`, rather than
  four independent bodies. Each cell's `_IsValidA` gate is called
  TWICE with identical args in the real ASM (the SAME genuine
  redundant-double-check precedent as `pill_food_tile`) — collapsed to
  one call here since it's a pure, deterministic predicate.
- 5 cases (all four directions, including one deliberately near the
  grid edge to exercise the per-cell `is_valid_a` skip, plus the
  no-op `mode=4` case) — all green on the first real-ASM run.
- Suite: simant 1856 (+5), full suite green.

## 2026-07-15 (cont.193) — /goal grind: _InitAntLions
- RECOVERED `init_ant_lions` (`_InitAntLions`, SIMANTW.SYM seg7:40C6,
  arg count=[bp+6]; FAR return, 348 bytes). Zeroes
  `simant_data_group[0x8A88]` (the ant-lion live-count field) and a
  companion `pack[0x9C6E]`, then places up to `count` (clamped to a
  max of 10; non-positive skips the loop) ant lions, finally storing
  the clamped count into `pack[0x9E8C]` unconditionally.
- The real ASM INLINES the entire `_AddRandAntLion` body (identical
  instructions — same 200-attempt/100-threshold search, same
  placement tail — confirmed by direct comparison of both
  disassemblies) once per iteration rather than calling it; ported as
  `clamped` calls to the already-recovered `add_rand_ant_lion` instead
  of re-deriving the same search+place logic a second time (the
  session's now-familiar "compiler inlined it, we compose it instead"
  pattern already used for `_AddAntLion`/`_AddRandAntLion` and
  `_InitPillar`/`_InitSow`).
- 4 cases (count `0` and negative both no-op but still reset/store,
  count `2` and count `15`→clamped-10 both against a fully-clear yard
  so every placement succeeds immediately, reusing `add_rand_ant_lion`'s
  own "attempt0 succeeds" fixture pattern) — all green on the first
  real-ASM run.
- Suite: simant 1851 (+4), full suite green.

## 2026-07-15 (cont.192) — /goal grind: _DoSow
- RECOVERED `do_sow` (`_DoSow`, SIMANTW.SYM seg7:3F8A, NO args; FAR
  return, 316 bytes; calls `_SRand4` x2, `_SRand1(3)`, `_IsValidA`).
  Per-tick update for the 3 tracked "sown rocks" (slots `si=0,2,4` —
  unlike `init_pillar`/`init_sow`, which only ever fill slots 1/2,
  this processes ALL THREE). Each slot: `_SRand4()==0` skips it
  entirely; otherwise a second `_SRand4()` may trigger "growth" (steps
  the rock-tile-lookup roll by a signed `_SRand1(3)-1` random walk mod
  8, re-stamping the CURRENT cell), then unconditionally attempts
  "movement" — the (possibly just-updated) roll indexes the SAME
  `GET_BEST_DIR_DX`/`DY` compass table `add_ant_lion` uses (confirmed
  via the raw disassembly: identical `+0`/`+8` DGROUP-pointer-global-
  into-SIMANT_DATA_GROUP addressing) to pick a candidate neighbour;
  `is_valid_a`, an unoccupied life cell, and a clear (`<0x10`) tile
  gate an actual move (old cell restored from its saved snapshot, new
  cell's pre-overwrite tile becomes the new snapshot, tracked position
  updates).
- Caught a genuine INVERTED-COMPARISON bug on the first real-ASM run
  (this session's recurring bug class): read the growth gate's `jnz
  -> [skip growth]` as "growth fires when the second `_SRand4()` is
  NONZERO" — backwards. The real ASM's `jnz` jumps PAST the growth
  block when the roll IS nonzero, so growth actually fires on `0`.
  Caught immediately (2 of 4 first-draft test cases failed, including
  a seed-mismatch proving the LFSR draw COUNT itself was fine but the
  BRANCH taken was wrong) and fixed by flipping `gate2 != 0` to
  `gate2 == 0`; all test case labels/target cells were then corrected
  offline (some had accidentally been testing the RIGHT byte-exact
  outcome under the WRONG mental model, e.g. a "movement only, no
  growth" label whose seed actually exercised growth) rather than
  trusting the first green run's labels.
- 5 cases (total no-op, pure movement across all 3 slots with no
  growth at all, single-slot growth-then-movement succeeding and
  blocked-by-occupied-life, and a mixed 3-slot case combining
  movement-only with growth-then-movement) — all green after the
  polarity fix, each seed's exact gate/step sequence precomputed
  offline via the already-verified `srand1`/`srand_pow2`.
- Suite: simant 1847 (+5), full suite green.

## 2026-07-15 (cont.191) — /goal grind: _InitSow (+ refactor)
- RECOVERED `init_sow` (`_InitSow`, SIMANTW.SYM seg7:3EF8, NO args; FAR
  return, 146 bytes, calls `_SRand1`). Disassembly is byte-identical to
  `_InitPillar`'s own placement tail — same DGROUP pointer-globals,
  same PACK slot offsets, same SDG rock-tile lookup table — just
  without `_InitPillar`'s state-reset prologue or its "outside the
  nest" gate.
- Extracted the shared body into a new private helper
  `_place_two_random_rocks(dgroup, pack, simant_data_group)` and
  refactored `init_pillar` to compose it (previously inlined directly
  in `init_pillar`'s own body), rather than re-deriving the same
  retry-until-clear loop a second time — matching this session's
  standing "composition over re-derivation" discipline. `init_pillar`'s
  own behavior is unchanged (its existing 3 tests still pass
  unmodified after the refactor, confirmed before writing `init_sow`'s
  own tests).
- 2 cases (reusing `init_pillar`'s own precomputed retry-target
  fixture, since the underlying loop is identical) — both green on the
  first real-ASM run.
- Suite: simant 1842 (+2), full suite green.

## 2026-07-15 (cont.190) — /goal grind: _InitSimYard + _ClrArrays
- Ran a fresh Explore survey (previous candidate queue exhausted).
  It flagged a self-contained "pillar/sow" cluster (7 leaves + 1
  orchestrator, all 100%-recovered call targets) as the top priority,
  plus confirmed `_GstrR`/`_GetStrategy` and `_AddFood` are now
  tractable given `_StartAttack`'s presentation-call-stubbing
  precedent. Picked the two smallest, zero-call candidates first.
- RECOVERED `init_sim_yard` (`_InitSimYard`, SIMANTW.SYM seg7:1378, NO
  args; FAR return, 304 bytes, NO calls) — a large batch of ~35 fixed
  field resets across PACK/SIMANT_DATA_GROUP/DGROUP, every one
  independently resolved via a direct machine memory read of its
  DGROUP pointer-global. One field is already-named:
  `simant_data_group[0x8A80]` is the SAME ant-lion live-count field
  `find_in_lion_list`/`kill_ant_lion`/`add_ant_lion` already use.
  - Caught a genuine REGION-WINDOW bug (this session's most common bug
    class, now recurring after several clean runs): three PACK writes
    (`0x72B6`, `0x72C0`, `0x72F2`) fell just BELOW the test region's
    lower bound of `0x7300` — Python's negative-index wraparound
    silently aliased them to the tail of the region buffer instead of
    erroring. Diagnosed by direct whole-machine before/after byte
    diffs against a fresh (un-prefilled) machine, which confirmed the
    recovered function's own isolated writes exactly matched the real
    ASM's 7 true PACK mutations — proving the bug was in the test's
    region bounds, not the recovered code. Fixed by widening the PACK
    region's lower bound to `0x7200`.
- RECOVERED `clr_arrays` (`_ClrArrays`, SIMANTW.SYM seg7:6DEC, NO
  args; FAR return, 274 bytes, NO calls) — a new-game reset zeroing
  essentially every named world-sim array this session tracks: the
  full yard + nest map/life planes, the `_FixExitMap` exit-map arrays
  (`[0x3A4]`/`[0x13A4]`), six evenly-spaced `0x800`-byte scent grids
  (the alarm grid, one still-unrecovered companion field at
  `[0x5AD2]`, and the black/red nest+trail scent grids), the full
  A/B/R-list per-slot arrays (caste/field_c/field_e, 1000/500 bytes
  each), and `reproduce`'s two 192-byte per-colony grids.
  - Verification technique for a function this wide-reaching: rather
    than trust the region bounds blind, first ran a direct whole-
    machine before/after byte diff on the REAL ASM (with regions
    deliberately pre-seeded nonzero, scoped tightly to each real
    segment's own bounds — a whole-64K blind fill was tried first and
    produced a bogus 13864-byte "PACK changed" artifact, traced to
    this VM's flat memory model letting adjacent NE segments share
    physical space past a segment's declared size; re-ran scoped and
    got a clean 0-byte PACK diff, confirming `_ClrArrays` never
    touches PACK at all). The clean diff's contiguous merged ranges
    (e.g. one 32768-byte DGROUP range, one 12288-byte SDG range)
    turned out to exactly match the SUM of several adjacent
    independently-derived arrays sitting back-to-back in memory — not
    a discrepancy, just confirmation that the plane/grid layout has no
    padding between same-family arrays.
  - Both functions' loops were flattened from the ASM's nested
    `di`/`si` two-level counters into flat `range()` loops in the
    recovered Python (provably equivalent, since the nested bounds are
    exact multiples covering every offset in the flat range exactly
    once) — cleaner recovered source without changing byte-exactness.
- 2 cases (one each, no branches to exercise) — both green after the
  region-window fix.
- Suite: simant 1840 (+2), full suite green.

## 2026-07-15 (cont.189) — /goal grind: _StartAttack
- RECOVERED `start_attack` (`_StartAttack`, SIMANTW.SYM seg7:050E, NO
  args; FAR return, 66 bytes). Confirmed by resolving both far-call
  targets against real machine segment bases: the routine's ONLY
  sim-state mutation is `pack[0x78DC] = _SRand1(100) + 0x1E`; the rest
  is two presentation-only calls — `GR!_myBeginSong` (fixed
  song-index/volume) and `SIMANT!_EditMessage` (fields from a
  far-pointer struct at `pack[0x7C94]`) — neither touching any sim
  state this session tracks, matching `set_map`'s own established
  "rendering side effect, not sim state" precedent. Rather than a
  `NotImplementedError` gate (reserved for calls into genuinely
  UNRECOVERED sim logic, per the `_YellowFight`/`_AddFood` precedent),
  this is the session's FIRST use of `_run_and_diff_segs`'s existing
  `stubs=` mechanism: the oracle test neutralizes both far calls with
  a plain far return so the byte-exact diff covers only the one
  genuine sim-state write, since these are confirmed-presentation
  calls rather than unknown ones.
- 4 cases (varied seeds, exercising the full `_SRand1(100)` roll
  range) — all green on the first real-ASM run, including the
  `les`-loaded far-pointer struct dereference (whose target segment is
  uninitialized on a fresh test machine) resolving safely since its
  read values are only ever pushed as args to the now-stubbed callee.
- Suite: simant 1838 (+4), full suite green.

## 2026-07-15 (cont.188) — /goal grind: _InitPillar
- RECOVERED `init_pillar` (`_InitPillar`, SIMANTW.SYM seg7:4BF8, NO
  args; FAR return, 228 bytes). Always zeroes the tracked pillar's own
  position/state (`simant_data_group[0x8A8A]`/`[0x8A8C]`/`[0x8A8E]` —
  the SAME `[0x8A8C]`/`[0x8A8E]` position pair `is_pill_dead` already
  reads), the `_pillar_cache_index` rule flag `pack[0x9B1E]`, a
  companion field `pack[0x78D4]`, and the already-recovered
  `store_pillar_map`/`replace_pillar_map` 6-entry cache
  (`pack[0x7C0E..0x7C19]`). When `pack[0x9B6E]` ("inside the nest")
  is nonzero, returns there. Otherwise fills 2 small PACK arrays
  (x/y/roll/old-tile, indexed by a raw ASM loop counter `si=4,2`) by
  rolling random yard cells via `_SRand1(0x80)`/`_SRand1(0x40)` and
  overwriting each with a random rock tile from an 8-entry
  `simant_data_group[0x8A90..]` lookup table (indexed by a fresh
  `_SRand1(8)` roll).
- Caught a genuine CONTROL-FLOW bug on the first real-ASM run, not the
  usual region-window class: a blocked (`>= 0x10`) candidate cell does
  NOT skip to the next slot — the raw disassembly's blocked branch
  jumps directly past the `sub si,2` slot-advance instruction, so `si`
  is UNCHANGED and the loop rerolls the SAME slot. This is an
  unbounded retry with no attempt cap (unlike `add_rand_ant_lion`'s
  200-attempt ceiling), and the 8-roll LFSR draw only happens on a
  hit. First-draft code treated a blocked cell as "skip this slot,
  move to the next" — caught because the real ASM wrote a rock tile
  at a cell my precomputed candidates never touched; re-disassembling
  the exact branch target (rather than assuming symmetry with
  `add_rand_ant_lion`) found the missing `sub si,2`. Also caught a
  SECOND bug in the test fixture itself while fixing this: an
  un-seeded map plane defaults to all-zero (clear), so a "blocked
  cell" test that only pokes two specific offsets doesn't actually
  block anything else the retry loop might land on — fixed by bulk-
  filling the whole yard plane via `Memory.load` before carving out
  exceptions (the same bulk-fill technique `add_rand_ant_lion`'s
  fixture already used).
- 3 cases (inside-nest early return, both slots succeeding
  immediately, and slot 1 retrying twice before both slots succeed —
  coordinates precomputed offline via the already-verified `srand1`,
  interleaving the conditional 8-roll draw exactly like the real ASM)
  — all green after the control-flow fix.
- Suite: simant 1834 (+3), full suite green.

## 2026-07-15 (cont.187) — /goal grind: _AddRandAntLion
- RECOVERED `add_rand_ant_lion` (`_AddRandAntLion`, SIMANTW.SYM
  seg7:4222, NO args; FAR return, 286 bytes). Searches up to 200
  random locations (`x = _SRand1(0x40)+_SRand1(0x41)`, `y =
  _SRand1(0x20)+_SRand1(0x21)`, four LFSR draws EVERY attempt
  regardless of outcome) for a spot to place a new ant lion —
  preferring a fully-clear 3x3 block (composing `map_cell_offset` +
  `life_cell_offset` + `is_clear_tile` over the centre and 8
  neighbours, the same pattern `add_ant_lion`'s ring check already
  uses, confirmed against `_IsClear3x3`'s own island in `hooks.py` for
  the out-of-range-cell residue), but falling back to a merely-clear
  centre tile once the 0-indexed attempt count reaches 100. On
  success, composes `add_ant_lion` directly for the placement — its
  body is byte-identical to this routine's own placement tail
  (confirmed by direct disassembly comparison, the same centre-stamp +
  ring-stamp + PACK-slot-append sequence cont.186 already recovered).
- Test strategy: precomputed each seed's exact per-attempt `(x, y)`
  candidates offline via the already-verified `srand1`, so the
  fixture could deliberately bulk-clear or bulk-occupy the whole yard
  plane and pre-clear exactly the cell needed to force a given branch
  — immediate full-3x3 success at attempt 0, total failure across all
  200 attempts (life occupied everywhere), and the single-tile
  fallback landing exactly at attempt 100 (only that one precomputed
  cell cleared, its neighbours left occupied so the full-3x3 check
  keeps failing there). All 3 cases green on the first real-ASM run —
  no region-window, segment, or off-by-one bug this time.
- Suite: simant 1831 (+3), full suite green.

## 2026-07-15 (cont.186) — /goal grind: _AddAntLion
- RECOVERED `add_ant_lion` (`_AddAntLion`, SIMANTW.SYM seg7:4340, args
  x=[bp+6], y=[bp+8]; FAR return, 186 bytes). Composes `set_map`
  (plane 1, the yard plane) to stamp the pit centre to tile `0x38`
  unconditionally, then for each of the 8 compass directions checks
  the neighbour cell via the real `_IsClearTile` routine's own
  composition (`map_cell_offset` + `life_cell_offset` + the
  `is_clear_tile` predicate) and, if clear, stamps it to a
  per-direction ring-tile (`ADD_ANT_LION_RING_TILE[dir] + 0x30`).
  Finally appends one slot to the PACK ant-lion arrays (x/y plus three
  zeroed status bytes), bumping the SIMANT_DATA_GROUP live count
  `[0x8A88]` (the same field `find_in_lion_list`/`kill_ant_lion` read)
  only while it's `< 9` — a hard cap at 10 slots.
- Independently confirmed via direct memory reads (not assumed) that:
  (a) the direction table reached through DGROUP pointer-globals
  `[0xC57E]`/`[0xC580]` — both of which resolve to the SIMANT_DATA_GROUP
  selector — is byte-for-byte the SAME `GET_BEST_DIR_DX`/`DY` compass
  table already used throughout this session, just addressed as one
  contiguous 16-byte block (`dx` at `SDG+0`, `dy` at `SDG+8`) rather
  than through the usual literal DGROUP offsets; (b) the per-direction
  ring-tile table at `DGROUP[0x25E8]` is a plain compile-time literal
  (DS stays DGROUP throughout this function's loop — no segment
  override before the loop, unlike `recruit`/`un_recruit`'s `ds=5294h`
  pattern), so it was hardcoded as a new module constant
  `ADD_ANT_LION_RING_TILE = (1, 2, 4, 7, 6, 5, 3, 0)` following the
  same precedent as `GET_BEST_DIR_DX`/`DY`.
- 5 cases (all-neighbours-clear, some-blocked-by-a-real-ant, the two
  count=8/9 cap-boundary cases, and a grid-corner case exercising the
  out-of-range-neighbour-skip branch) — all green on the first
  real-ASM run.
- Suite: simant 1828 (+5), full suite green.

## 2026-07-15 (cont.185) — /goal grind: _Reproduce
- RECOVERED `reproduce` (`_Reproduce`, SIMANTW.SYM seg7:3D4C, args
  x=[bp+6], y=[bp+8], colony=[bp+10]; FAR return, 166 bytes). Composes
  the already-recovered `sg_s_rand` twice to jitter `(x, y)`, clamps
  the result into a 12-wide (`0..0x0B`) by 16-tall (`0..0x0F`) grid,
  and — unless the jittered cell equals the untouched input exactly
  (a genuine no-op branch, confirmed via the raw `cmp`/`jz` pair) —
  increments a per-cell BYTE counter on SIMANT_DATA_GROUP at
  `(di<<4)+si+base` (`base=0xA4` for `colony==0`, `0x164` for
  `colony!=0`: two contiguous 0xC0-byte grids, one per colony). The
  FIRST time any given cell is hit (counter was `0`), also bumps a
  colony-wide PACK WORD counter (`[0x80D4]`/`[0x9C80]`).
- Test strategy: rather than guessing RNG outcomes, precomputed
  `sg_s_rand`'s exact jitter for each seed offline using the
  already-verified `srand1`/`srand_pow2` primitives, then chose
  `(x, y, colony)` per case to deliberately hit every branch (exact
  no-op, lower/upper clamp on each axis, first-hit-bumps-counter,
  already-visited-no-bump, the other colony's grid). All 7 cases green
  on the first real-ASM run — no region-window or segment bug this
  time.
- Suite: simant 1823 (+7), full suite green.

## 2026-07-15 (cont.184) — /goal grind: _Recruit/_UnRecruit
- RECOVERED `recruit` (`_Recruit`, SIMANTW.SYM seg7:06D2, arg count=
  [bp+6]; FAR return, 184 bytes, NO calls). Converts up to `count` idle
  A-list then B-list ants (mode `2` or `6`, via the standard
  `(v & 0x78) >> 3` extraction, and not already recruited) into
  "recruited" mode `6` (`field_c=6`, `field_e=0`), scanning each list
  backward, stopping once the budget is exhausted. Never touches the
  R-list.
- RECOVERED `un_recruit` (`_UnRecruit`, SIMANTW.SYM seg7:078A, arg
  flag=[bp+6]; FAR return, 220 bytes, NO calls). The inverse: clears
  `field_c==6` across A-list, B-list, AND (unlike `recruit`) the
  R-list, up to a budget of `pack[0x7876]//2` (C-style truncating
  division, `flag==0`) or `pack[0x7876]+0x64` (`flag!=0`). A/B hits
  reset `field_c` to `0`; the R-list's OWN hits reset it to `7`, not
  `0` — independently confirmed via the raw disassembly (`mov
  ds:[si+17648],07h` vs the A/B passes' `mov ds:[si+N],ch` where
  `ch` was zeroed).
- Caught a genuine SEGMENT bug on the first real-ASM run, not a
  region-window bug this time: every per-slot field (`0x2F62`,
  `0x2B78`, `0x334C`, `0x3D18`, `0x3B22`, `0x3F0E`, `0x46E6`, `0x44F0`)
  is reached through an explicit `mov ax,5294h; mov ds,ax` segment
  override — i.e. SIMANT_DATA_GROUP, not PACK — while the counts
  (`0x80F0`/`0x99D4`/`0x72CC`) and the `un_recruit` baseline
  (`0x7876`) stay on PACK (`es=ds:[C4D2]`/`[C4D4]`/`[C4D8]`/`[C4D6]`).
  First-draft code used `pack` for everything, which the real ASM
  showed leaving every per-slot field untouched (asm stayed at its
  seeded value while the recovered side mutated it) — fixed by adding
  a second `simant_data_group` parameter to both functions and a
  second SDG region to the test harness, matching the same
  SDG-for-per-slot-fields convention already established by
  `find_in_a_list`/`find_in_b_list`/`force_mode_a`/`force_mode_b`
  elsewhere in this file.
- 8 cases (4 recruit + 4 un_recruit, covering both lists, budget
  exhaustion, already-recruited/wrong-mode skips, both `flag` values,
  and the R-list `field_c=7` asymmetry) — all green after the segment
  fix.
- Suite: simant 1816 (+8), full suite green.

## 2026-07-15 (cont.183) — /goal grind: _InitGrassMap/_InitSimVars
- Ran a fresh Explore survey now that the pillar-family batch is
  exhausted. It flagged `_SRand4` as an unrecovered blocker for several
  candidates — that's a false alarm: `srand_pow2(seed, mask=3)`
  already covers the WHOLE `_SRand2..256` family generically (the SAME
  primitive this session has repeatedly called with different masks
  for `_SRand8`/`_SRand16`/`_SRand32` etc.), so those candidates were
  never actually blocked. Picked the two smallest, zero-call
  candidates first: `_InitGrassMap`/`_InitSimVars`.
- RECOVERED `init_grass_map` (`_InitGrassMap`, SIMANTW.SYM seg7:2096,
  NO args; FAR return, 32 bytes) — startup init, no calls. Clears
  three PACK counters and fills a 9-entry WORD table with `0xFFFF`.
- RECOVERED `init_sim_vars` (`_InitSimVars`, SIMANTW.SYM seg7:5A70, NO
  args; FAR return, 62 bytes) — startup init, no calls. Sets a
  handful of fixed constants and zeroed counters, including the SAME
  `pack[0x9C26]`/`[0x807A]` fields `maintain_swarm` decays and
  `dgroup[0xAC8C]`/`[0xAC8E]` fields `start_migrate`/`end_migrate` use
  as floors — confirms this routine is (part of) their initializer.
- 2 cases (one each, no branches to exercise) — ALL GREEN ON THE FIRST
  REAL-ASM RUN.
- Suite: simant 1808 (+2), full suite green.

## 2026-07-15 (cont.182) — /goal grind: _PillFoodTile/_IsPillDead
- RECOVERED `pill_food_tile` (`_PillFoodTile`, SIMANTW.SYM seg7:5A02,
  args x=[bp+6], y=[bp+8]; FAR return, 110 bytes) — composes
  `is_valid_a` and the already-recovered `replace_pillar_map`.
  Restores the cached map tile, then stamps it to a fixed "food" tile
  (`0x4B`) if it's `< 0x18`. The real ASM calls `_IsValidA` TWICE with
  the identical `(x, y)` — a genuine redundant double-check
  (independently confirmed: same args both times on a pure,
  deterministic predicate) that has no observable effect beyond a
  single check; ported as one, composing `replace_pillar_map` for the
  body that duplicate second check guards (the real ASM inlines that
  body again rather than calling the function).
  - Caught a genuine INVERTED-comparison bug via the first failing
    test: read `jnb` (jump if tile `>= 0x18`, skipping the stamp) as
    "stamp when `>= 0x18`" instead of the correct "stamp when `< 0x18`"
    — caught immediately by a deliberately contrasting pair of test
    cases (a cached tile clearly above vs below the threshold), fixed
    by re-reading the raw jump condition.
- RECOVERED `is_pill_dead` (`_IsPillDead`, SIMANTW.SYM seg7:572A, NO
  args; FAR return, 168 bytes) — a genuinely self-contained predicate
  (reads the pillar's own position from `simant_data_group[0x8A8C]`/
  `[0x8A8E]`, no parameters) scanning its 3x3 neighborhood on the yard
  life plane; `1` ("dead") once MORE than 5 of the (up to 9) cells are
  alive. Composes `is_valid_a` (an out-of-bounds neighbor contributes
  `0`, not counted).
- 7 cases (3 `pill_food_tile`, 4 `is_pill_dead` including an
  out-of-bounds-neighbor edge case) — all green after the one
  comparison-direction fix.
- Suite: simant 1806 (+7), full suite green.

## 2026-07-15 (cont.181) — /goal grind: _StorePillarMap/_ReplacePillarMap
- RECOVERED `store_pillar_map`/`replace_pillar_map`
  (`_StorePillarMap`/`_ReplacePillarMap`, SIMANTW.SYM seg7:5304/5372,
  args x=[bp+6], y=[bp+8]; FAR return, 110/104 bytes) — a save/restore
  pair caching a single yard map tile into a 6-entry PACK table.
  Composes the already-recovered `is_valid_a`; a shared local
  `_pillar_cache_index` picks the slot: `x % 6` when `pack[0x9B1E]`'s
  low bit is set, else `y % 6`.
  - `store_pillar_map`: caches `dgroup[MAP_PLANE_BASE[0]+(x<<6)+y]`
    into `pack[0x7C0E+idx*2]`.
  - `replace_pillar_map`: the inverse — restores the cached value back
    onto the map. Both are no-ops when `(x, y)` isn't `is_valid_a`.
- 6 cases (both flag polarities per routine, plus an invalid-position
  no-op each) — ALL GREEN ON THE FIRST REAL-ASM RUN.
- Suite: simant 1799 (+6), full suite green.

## 2026-07-15 (cont.180) — /goal grind: _PlacePillTile/_PillGetLife
- RECOVERED `place_pill_tile`/`pill_get_life` (`_PlacePillTile`/
  `_PillGetLife`, SIMANTW.SYM seg7:56DA/5702, args x=[bp+6], y=[bp+8]
  (+value=[bp+10] for place); FAR return, 40 bytes each). Composes the
  already-recovered `is_valid_a`. Despite the "pill" naming, these
  operate on the plain yard MAP/LIFE planes (`MAP_PLANE_BASE[0]`/
  `LIFE_PLANE_BASE[0]`, confirmed via the raw disassembly's own
  offsets) — validated cousins of `set_map`/a plain life read, but
  gated on a direct `is_valid_a` call rather than `set_map`'s own
  `map_cell_offset` range check.
  - `place_pill_tile`: writes the map tile only when `is_valid_a(x,y)
    == 1`; a no-op otherwise.
  - `pill_get_life`: returns `0` for an invalid position (not the
    usual empty-cell sentinel), else the life-plane tile.
- 6 cases (3 each: in-range, x-out-of-range, y-out-of-range) — ALL
  GREEN ON THE FIRST REAL-ASM RUN.
- Suite: simant 1793 (+6), full suite green.

## 2026-07-15 (cont.179) — /goal grind: _fracSIN/_fracCOS — fixed-point trig
- RECOVERED `frac_sin`/`frac_cos` (`_fracSIN`/`_fracCOS`, SIMANTW.SYM
  seg7:69C8/6A0E, arg angle=[bp+6]; FAR return, 70/74 bytes) —
  16-bit fixed-point sine/cosine of an 8-bit angle (`0..255` =
  `0..360` degrees) via quarter-wave symmetry into a shared 64-entry
  WORD table. Composes a shared local `_frac_trig` helper (`cos(a) =
  sin(a + 0x40)`, confirmed by both routines sharing the SAME `bx ==
  0x40 -> 0x7FFF` special case).
  - The 64-entry table is reached through a genuine runtime FAR
    POINTER at `pack[0x9FCA]`/`[0x9FCC]` (confirmed zero on a fresh,
    pre-init machine — populated by some not-yet-recovered
    initialization routine), NOT a fixed compile-time address. Rather
    than solve "resolve an arbitrary runtime segment value to a
    bridge view" as new infrastructure, `frac_sin`/`frac_cos` take the
    already-resolved table as an explicit `(view, offset)` pair — the
    same "take an explicit view" convention `set_map` already
    established.
- 12 cases (both routines: angle 0, the `0x40`/`0xC0` special-case
  boundary, a plain lookup, a reflected lookup, the negate-check
  boundary, and the `255` wraparound edge) — ALL GREEN ON THE FIRST
  REAL-ASM RUN.
- Suite: simant 1787 (+12), full suite green.

## 2026-07-15 (cont.178) — /goal grind: _StartMigrate/_EndMigrate
- RECOVERED `start_migrate`/`end_migrate` (`_StartMigrate`/
  `_EndMigrate`, SIMANTW.SYM seg7:3DF2/3E6C, args x=[bp+6], y=[bp+8];
  FAR return, 122/140 bytes) — a grass-patch "migration" mechanic
  projecting screen-space `(x, y)` onto the SAME 12x16 grid
  `get_nearby_patches` scores.
  - `start_migrate`: `y_bucket = (y-0x42)//10`, `combined =
    (x+y-0xEE)//28` (both C-style truncating division), stored in
    `pack[0x9D72]`/`[0x9CEE]`. Out-of-`[0,15]`/`[0,11]` range, or a
    zero grid cell at the computed slot, resets `pack[0x9CEE] = -1`
    (the "no migration in progress" sentinel `end_migrate` checks).
  - `end_migrate`: a no-op if no migration is in progress, OR if the
    NEWLY projected `(x, y)` is out of range (no partial effect either
    way — the origin cell is untouched on an out-of-range abort).
    Otherwise halves the ORIGIN cell (`start_migrate`'s saved slot/
    y-bucket pair) and adds that half onto the newly-projected
    destination cell, capped at `0xFA`.
  - Given two prior decimal-vs-hex transcription slips this session
    (cont.163, cont.177), cross-checked EVERY offset against the raw
    bytes before writing any code (disambiguating hex-digit-containing
    or `h`-suffixed immediates, which are safe, from all-decimal-digit
    memory displacements, which this disassembler prints in decimal)
    — plus an extra direct instrumented single-call trace against a
    hand-picked in-range `(x, y)` before writing the test suite, to
    catch anything the audit missed. No offset bug this time.
  - Still caught the SAME region-window class of bug this session has
    now hit four times (cont.163, cont.165, cont.170 x2): the `_PACK`
    test region started at `0x9D00`, excluding `pack[0x9CEE]` (just
    below it) — surfaced as `end_migrate` silently leaving both grid
    cells at their seeded values instead of mutating them. Diagnosed
    by reproducing the exact failing scenario via a plain unwindowed
    `ByteBackend` call, which gave the CORRECT result — proving the
    recovered Python was right and the test region was the bug (same
    diagnostic pattern as every prior instance of this bug class).
- 7 cases (3 `start_migrate`, 4 `end_migrate`) — all green after the
  one region-window fix.
- Suite: simant 1775 (+7), full suite green.

## 2026-07-15 (cont.177) — /goal grind: _GetNearbyPatches — score 6 delta cells
- RECOVERED `get_nearby_patches` (`_GetNearbyPatches`, SIMANTW.SYM
  seg7:3CE4, args x=[bp+6], y=[bp+8]; FAR return, 104 bytes) — a PURE
  predicate (no calls, nothing written) scoring 6 delta-table
  neighbor cells on the SAME 12x16 boy's-yard grid `is_valid_yard`
  bounds-checks: `+3` per in-bounds cell whose first SDG 12x16 grid
  byte is nonzero, `-3` per cell whose SECOND parallel grid
  (immediately following the first, `0xC0` bytes later) is nonzero.
  The 6-entry `(dx, dy)` delta table is genuine runtime-populated
  scratch data (confirmed all-zero on a fresh machine — unlike the
  fixed 8-entry compass tables used throughout this session).
- Caught the SAME decimal-vs-hex transcription slip as cont.163's
  `0x2F6A`/`0x2F62` bug, this time on the delta-table base offsets:
  `lindis_win16.py` prints displacements in DECIMAL (`9692`/`9698`),
  and those got typed directly as `0x9692`/`0x9698` instead of first
  converting — the correct hex values are `0x25DC`/`0x25E2`. Caught by
  a direct instrumented single-instruction trace showing the real
  ASM's `si`/`di` registers held values that couldn't possibly come
  from the (correctly-seeded, independently verified) memory my test
  was writing to — proving the OFFSET, not the seeded data, was wrong.
  Two of the five test cases had been passing by coincidence before
  the fix (both sides landing on an out-of-bounds skip for unrelated
  reasons) — re-ran the full parametrized set after the fix, including
  the case that would have caught this immediately
  (`mixed-deltas-mixed-grids`, distinct per-index values).
- 5 cases — ALL GREEN after the fix.
- Suite: simant 1768 (+5), full suite green.

## 2026-07-15 (cont.176) — /goal grind: _GrabMap — wrap-clamped map read
- RECOVERED `grab_map` (`_GrabMap`, SIMANTW.SYM seg7:6DAC, args x=
  [bp+6], y=[bp+8]; FAR return, 64 bytes) — a PURE predicate (no
  calls, no side effects) reading the yard map tile at `(x, y)`.
  Per axis, a genuine WRAPAROUND clamp — independently confirmed via
  the raw disassembly, NOT the "clamp to the nearer bound" shape one
  might expect from the name: `x > 0x7F` (signed) maps to `0`, but
  `x < 0` maps to `0x7F` (the MAX, not `0`); same shape for `y` against
  `0x3F`.
- 5 cases (in-range, both directions of both axes' wraparound) — ALL
  GREEN ON THE FIRST REAL-ASM RUN, confirming the wraparound reading
  (rather than the more intuitive clamp-to-nearer-bound) was correct.
- Suite: simant 1763 (+5), full suite green.

## 2026-07-15 (cont.175) — /goal grind: _FollowCatDir — cat pursuit direction
- RECOVERED `follow_cat_dir` (`_FollowCatDir`, SIMANTW.SYM seg7:32A6,
  NO args; FAR return, 68 bytes) — a PURE predicate (no calls, nothing
  written) picking the cat's pursuit compass direction from its own
  countdown state, all three fields PACK-resident.
  `pack[0x77B0] < 5`: `1`. `> 8`: `3`. Otherwise (`5..8`):
  `pack[0x789C] > 0` (signed): `0`; else `pack[0x7A5C] & 3`.
- 6 cases (both boundary edges of the `5..8` range, both sides of the
  `789C` gate, and the low-2-bit mask) — ALL GREEN ON THE FIRST
  REAL-ASM RUN.
- Suite: simant 1758 (+6), full suite green.

## 2026-07-15 (cont.174) — /goal grind: _KillAntLion — remove + compact
- Ran a fresh Explore survey now that the previous batch is exhausted;
  picked `_KillAntLion` first since it directly extends the antlion-list
  cluster (`_FindInLionList`/`_SetAntLion`, cont.164/166) already in
  this session. The rest of the survey's list (`_BlockMove`,
  `_FollowCatDir`, `_fracSIN`/`_fracCOS`, `_GrabMap`,
  `_GetNearbyPatches`, `_StartMigrate`/`_EndMigrate`,
  `_PlacePillTile`/`_PillGetLife`, plus an untested pillar-array family)
  remains for continued work.
- RECOVERED `kill_ant_lion` (`_KillAntLion`, SIMANTW.SYM seg7:4B58, arg
  slot=[bp+6]; FAR return, 160 bytes). Composes the already-recovered
  `set_map`. Clears the antlion's pit tile back to open ground
  (`set_map(plane=1, x, y, 0x3F)`), then removes it from the list:
  a non-positive count is a no-op; decrements the count, and if the
  removed slot was the LAST live one that's the whole effect;
  otherwise compacts by shifting every later slot down by one across
  FIVE parallel PACK arrays — the SAME `[0x809C]`(x)/`[0x80BC]`(y)
  `find_in_lion_list`/`set_ant_lion` use, `[0x7D4E]` (the antlion
  "type" byte `set_ant_lion` reads), and two further per-slot fields
  at `[0x7A68]`/`[0x7D34]` whose exact meaning wasn't independently
  determined — ported literally by offset, not guessed at.
- 4 cases (empty list, remove-last-no-shift, remove-first-shifts-two,
  remove-middle-shifts-one) — ALL GREEN ON THE FIRST REAL-ASM RUN.
- Suite: simant 1752 (+4), full suite green.

## 2026-07-15 (cont.173) — /goal grind: _SetCasteProd/_SetModeProd/_GstrB
- RECOVERED `set_caste_prod`/`set_mode_prod` (`_SetCasteProd`/
  `_SetModeProd`, SIMANTW.SYM seg7:026E/0326, NO args; FAR return,
  210/156 bytes) — `_GetStrategy`'s own two unexplored callees flagged
  at cont.172's deferral, now closed out. Composes `a_f_ldiv` (signed)
  and `a_f_ulmul` (unsigned) respectively.
  - `set_caste_prod`: 4-slot target-vs-actual caste-production
    percentage comparison (`a_f_ldiv`-based), argMIN of the difference
    (strict `<`, ties keep the first slot, defaults to slot 0),
    writing the winner into `simant_data_group[0x8A56]` — the SAME
    "hatch mode" field `sim_egg_b` already reads.
  - `set_mode_prod`: NOT a mechanical mirror (independently confirmed)
    — only 3 slots, no total-positive guard (moot, fixed `0xFFFF`
    divisor), a genuinely UNSIGNED multiply/divide pair (the ASM still
    sign-extends via `cwd` before the unsigned multiply — a real
    quirk, replicated by passing the signed Python int through
    `a_f_ulmul`'s own masking), and argMAX (not argMIN) of the
    difference. `__aFuldiv` has no plain-Python composable form (only
    a `hooks.py` VM island), so this inlines the identical unsigned
    floor-divide semantics as a local helper. Writes
    `simant_data_group[0x8A58]` — the SAME fixed WORD `get_new_mode`'s
    own fallback path reads.
- RECOVERED `gstr_b` (`_GstrB`, SIMANTW.SYM seg7:01CC, NO args; FAR
  return, 162 bytes) — a PURE predicate (no calls, no side effects at
  all) that picks a black-colony "strategy" tier 0-5. A STANDALONE
  callable duplicate of the SAME threshold logic `_GetStrategy`'s own
  inline code computes (that routine writes its copy straight into
  `pack[0x9B8A]` instead of returning it — the two never literally
  call each other, confirmed via disassembly, not assumed).
  - Caught a genuine bug in my OWN test seeding (not in the recovery)
    via a first failing assertion: assumed `pack[0x79DC]`/`[0x72C8]`
    were direct DGROUP reads like `[0xAC82]`/`[0xAC84]`/`[0xAC86]`
    (all SS-segment-prefixed, and SS == DGROUP for this app) — but a
    close re-read of the raw ES-override bytes showed those TWO
    fields are reached through the SAME hardcoded `0x5EF3` PACK
    segment literal seen throughout this session. Fixed by adding
    `pack` as a real parameter and correcting which segment each field
    reads from.
- Deferred `_GstrR` after disassembling it fully: genuinely NOT a
  mechanical twin of `_GstrB` (independently confirmed) — it has an
  extra "attack cooldown" gate at the top (`pack[0x8078]`/`[0x78DC]`),
  swapped `[0xAC82]`/`[0xAC84]` operand roles vs `_GstrB`'s, different
  gate fields (`pack[0x78E8]`/`[0x7A56]` instead of B's `[0x79DC]`/
  `[0x72C8]`), and — where `_GstrB` just returns `0`, `_GstrR`'s
  equivalent branch instead rolls MORE randomness (`_SRand32()`, then
  conditionally `_SRand128()`) and — if it decides to "attack" — reseeds
  `pack[0x78DC]` and fires TWO external notification calls into
  GR_MODULE (seg2, graphics — likely presentation) AND SIMANT_MODULE
  (seg1, the top-level module — NOT confidently presentation-only,
  unlike GR_MODULE) before returning `0`. Given that ambiguity, this
  is deferred rather than guessed at — a good next-session target once
  those two external calls are understood (or a `NotImplementedError`
  gate is judged the honest choice instead). `_GetStrategy` itself
  (the master orchestrator, seg7:0) remains deferred too, pending a
  full pass now that both its former blockers are resolved.
- 13 cases (3 `set_caste_prod`, 3 `set_mode_prod`, 7 `gstr_b`) — ALL
  GREEN (after the one seeding fix above, which was caught before
  being mistaken for a recovery bug).
- Suite: simant 1748 (+13), full suite green.

## 2026-07-15 (cont.172) — /goal grind: _FeedAnts — hunger-decay food tick
- Deferred `_GetStrategy`/`_GstrB`/`_GstrR` after a first look:
  `_GetStrategy` (seg7:0, 460 bytes) is genuinely the master orchestrator
  the survey's guess had backwards — it CALLS `_GstrR` internally (not
  the other way around) and sets `pack[0x9B8A]`/`[0x7690]`, the EXACT
  two fields `get_new_mode` reads to pick a colony's active mode table.
  It also calls two more not-yet-examined near targets (seg7:026E,
  seg7:0326) past the point already mapped. A genuinely bigger
  undertaking than the byte count suggested — good next-session
  candidate, not attempted further this entry.
- RECOVERED `feed_ants` (`_FeedAnts`, SIMANTW.SYM seg6:0474, NO args;
  NEAR return, 100 bytes) instead — this one turned out tractable
  despite depending on the UNRECOVERED `_AddFood` (514 bytes), because
  `_AddFood`'s call site discards its return value entirely. Ages both
  colonies' hunger-decay food supplies by one tick (the SAME
  `dgroup[0xAC86]`/`[0xAC88]` counters `dec_eat_b`/`dec_eat_r` drain,
  floored at `0`; black's decrement is skipped while
  `simant_data_group[0x8A60]` — the SAME "no-starve" cheat flag
  `dec_eat_b` gates on — is set), then, unless `pack[0x80B4] == 3`,
  occasionally drops a fresh food pile once `pack[0x9E84]` (the SAME
  per-drop counter `food_fall`/`drop_food_a` bump) falls behind a
  rolling threshold at `simant_data_group[0x8A62]`.
  - The `_AddFood` branch raises `NotImplementedError` rather than a
    silently-wrong guess — NOT just because `_AddFood` itself is
    unrecovered, but because its unknown internal `_SRand*`
    consumption would make the REAL ASM's subsequent
    `_SRand1(50)`-based threshold reseed unpredictable even if the
    call's OWN return value didn't matter.
- 5 cases (both-decrement, no-starve-skip, zero-floor-clamp,
  pack80B4-skip, plus the `_AddFood`-gate raise check) — ALL GREEN ON
  THE FIRST REAL-ASM RUN.
- Suite: simant 1735 (+5), full suite green.

## 2026-07-15 (cont.171) — /goal grind: _MaintainSwarm — swarm-size decay
- RECOVERED `maintain_swarm` (`_MaintainSwarm`, SIMANTW.SYM seg7:3580,
  NO args; FAR return, 120 bytes) — self-contained, no dependencies.
  Not a genuine B/R pair despite touching both colonies: one routine
  applies the SAME decay formula to `pack[0x807A]` (black) and
  `pack[0x9C26]` (red) back to back. Each: `<= 0` stays put; `< 4`
  decrements by `1`; otherwise decays ~25% (`value -= value // 4`, an
  arithmetic-shift-right-by-2 in the real ASM), then floors at
  `dgroup[0xAC8C]`/`[0xAC8E]` (each colony's own configured minimum,
  read directly — no pointer-global indirection) and caps at `0x32`
  (50).
- 5 cases (no-op, small-value decrement, quarter-decay, floor-clamp,
  cap-clamp) — ALL GREEN ON THE FIRST REAL-ASM RUN.
- Suite: simant 1730 (+5), full suite green.

## 2026-07-15 (cont.170) — /goal grind: _ForceModeA/B — force mode-transition state
- RECOVERED `force_mode_a`/`force_mode_b` (`_ForceModeA`/`_ForceModeB`,
  SIMANTW.SYM seg7:0550/0622, args slot=[bp+6], mode=[bp+8], arg3=
  [bp+10]; FAR return, 210/176 bytes) — self-contained (no unrecovered
  dependencies), a 9-way jump-table dispatcher (several `mode` values
  sharing one handler) that force-stamps an ant's `field_c`/`field_e`
  (A) or `caste`/`field_e` (B) mode-transition fields. `lindis_win16.py`
  can't follow indirect jumps (it single-steps linearly and decodes the
  jump table's own DATA bytes as garbage instructions), so the table
  itself was read directly as raw words from a live machine, then each
  of the 5 distinct handler blocks it points into was disassembled by
  address.
  - Confirmed via independent disassembly that B is NOT a mechanical
    twin of A: B's `mode in (3, 7)` handler is a PLAIN caste bump with
    no further effect, where A's SAME modes ALSO force-set the slot's
    own yard map tile to `0x48` if it's below that.
  - `mode in (4, 8)` (or any out-of-`1..9` value): a genuine no-op
    past a SHARED tail (both routines): if `arg3 == 6` AND
    `dgroup[0xCE80] == 1` (read directly, no pointer-global
    indirection — like `get_winner`'s own strength tables), overwrites
    `field_e` with a small fixed bit-packed status byte built from
    `dgroup[0xCE7E]`/`[0xCD88]`.
  - Caught and fixed a genuine transcription slip BEFORE it ever
    reached the real-ASM oracle's first failing assertion (a plain
    decimal-to-hex miscopy, `12130 → 0x2F6A` instead of the correct
    `0x2F62`, plus a byte-vs-word size mismatch on the same field — the
    real opcode is `ADD r/m8,imm8`, not the word-sized form); caught
    when the ASSERTION diff pointed at a shifted-by-8 byte offset,
    fixed by re-decoding the exact opcode bytes.
  - Also fixed two instances of the SAME region-window bug this
    session has now hit three times (`_FoodFall` cont.163,
    `_CheckNestFightB/R` cont.165): the SDG test region excluded first
    the caste/field_e fields (B, above the window's upper bound) and
    then the x/y fields (A, below the window's lower bound) —
    widened both bounds to cover everything the recovered code reads.
- 14 cases (8 A covering every distinct mode-family branch, 6 B) — all
  green after the transcription and harness fixes.
- Suite: simant 1725 (+14), full suite green.

## 2026-07-15 (cont.169) — /goal grind: _DoRandB/R — random wander tick
- RECOVERED `do_rand_b`/`do_rand_r` (`_DoRandB`/`_DoRandR`, SIMANTW.SYM
  seg6:3876/5F7A, args x=[bp+6], y=[bp+8], attacker=[bp+10], sub=
  [bp+12]; FAR return, 246/248 bytes) — the survey's guessed
  "`_RandTurn` sibling" was, like `_DoRestB`/`R`, actually a
  `check_nest_fight_b`/`r`-shaped combat routine: an unconditional
  periodic `_SRand32()`-gated (1-in-32) `field_c` refresh via
  `get_new_mode_b`/`r`, THEN the same combat resolution `do_rest_b`/`r`
  compose, and — only when no fight happens — a plain
  `try_move_dir_b`/`r` wander step (retried once with a fresh
  `_SRand8()` direction on a `0` result, the SAME epilogue shape
  `do_nesting_b`/`r`'s `finish()` uses). Composes `get_new_mode_b`/`r`,
  `is_yellow_ant`, `find_in_b_list`/`find_in_r_list`, `get_winner`, and
  `try_move_dir_b`/`r` — no new primitives, the fourth recovery this
  session built on the `check_nest_fight_b`/`r` combat shape.
  - Confirmed the SAME B/R asymmetries (check order, `_YellowFight`
    gate polarity/argument) hold here too — independently re-verified
    via the raw disassembly for THIS pair.
  - The `_YellowFight` branch raises `NotImplementedError` for the
    same reason `check_nest_fight_b`/`r`/`do_rest_b`/`r`'s does.
- 6 cases (2 B, 2 R, plus a `NotImplementedError`-gate check per
  colony) — ALL GREEN ON THE FIRST REAL-ASM RUN.
- Suite: simant 1711 (+6), full suite green.

## 2026-07-15 (cont.168) — /goal grind: _DoRestB/R — nest combat + retreat
- RECOVERED `do_rest_b`/`do_rest_r` (`_DoRestB`/`_DoRestR`, SIMANTW.SYM
  seg6:367E/5D7E, args x=[bp+6], y=[bp+8], attacker=[bp+10]; FAR
  return, 294/298 bytes). Despite the name shared with `do_rest_ant`,
  disassembly showed these are genuinely nest-COMBAT-resolution
  routines, not "take a rest" ones — the survey's size-based guess
  ("colony-specific counterpart to `_DoRestAnt`") was wrong; their
  opening phase is essentially `check_nest_fight_b`/`r` (cont.165)
  inlined again, with a SECOND "retreat" phase appended for when no
  fight happens. Composes `is_yellow_ant`, `find_in_b_list`/
  `find_in_r_list`, `get_winner`, and `get_new_mode` — all already
  recovered.
  - Confirmed the SAME B/R asymmetries `check_nest_fight_b`/`r`
    established (check order, `_YellowFight` gate polarity, argument)
    hold here too — independently re-verified via the raw
    disassembly for THIS pair, not assumed from the sibling.
  - Retreat phase (reached only when no fight happened): the acting
    ant moves into the target cell (stamps its own caste there), then
    a `_SRand1(20)` roll of `0` (1-in-20) recomputes the acting ant's
    own `field_c` via `get_new_mode`; any other roll ends in a
    presentation-only balloon tail, deliberately not ported (same
    split as `do_rest_ant`'s own balloon gate).
  - The `_YellowFight` branch raises `NotImplementedError` for the
    same reason `check_nest_fight_b`/`r`'s does.
- 8 cases (3 B, 3 R, plus a `NotImplementedError`-gate check per
  colony) — ALL GREEN ON THE FIRST REAL-ASM RUN.
- Suite: simant 1705 (+8), full suite green.

## 2026-07-15 (cont.167) — /goal grind: _NotMowed — grass-cut test-and-clear
- RECOVERED `not_mowed` (`_NotMowed`, SIMANTW.SYM seg7:203E, args
  index=[bp+6], bit=[bp+8]; FAR return, 52 bytes) — a packed-bit
  test-and-clear over a PACK word array (accessed via the same
  hardcoded `0x5EF3` segment literal seen in `_FindInLionList`/
  `_SetAntLion`): `index` selects a WORD slot at `0xA0B6 + index*2`,
  `bit` (0..15) selects a bit within it. Returns `1` and clears the bit
  the FIRST time called for a given `(index, bit)`, `0` (no-op)
  thereafter.
- A first read of the disassembly's `D1 /4`/`D3 /4` shift-group opcodes
  mis-guessed SHR (right-shift, `index >> 1`); a direct single-
  instruction execution probe (`shl di,cl` with known register inputs)
  confirmed reg-field `4` is SHL, not SHR, correcting the index/mask
  computation to `index << 1` BEFORE writing any test — caught before
  it ever reached the real-ASM oracle.
- 5 state-diff cases (set/clear, low/mid/high bit) plus a return-value
  cross-check against a freshly-seeded (non-mutated) machine per case
  (the established "don't reuse the ASM's post-execution machine for a
  mutating routine's own recovered call" discipline) — ALL GREEN ON THE
  FIRST REAL-ASM RUN.
- Suite: simant 1697 (+5), full suite green.

## 2026-07-15 (cont.166) — /goal grind: _SetAntLion — antlion pit re-stamp
- RECOVERED `set_ant_lion` (`_SetAntLion`, SIMANTW.SYM seg7:4AD8, arg
  slot=[bp+6]; FAR return, 58 bytes) — composes the already-recovered
  `set_map`. Re-stamps an antlion's pit tile onto the YARD map plane
  (`plane=1`) at its own recorded position, reading the SAME two PACK
  arrays `find_in_lion_list` searches (`pack[0x809C+slot]`=x,
  `pack[0x80BC+slot]`=y — confirmed and cross-referenced back into
  `find_in_lion_list`'s own docstring, which only had them as generic
  `val0`/`val1` until now) plus a third per-slot growth/type byte at
  `pack[0x7D4E+slot]`, written as `set_map(1, x, y, type+0x38)`.
- 2 cases — all green on the first real-ASM run.
- Suite: simant 1692 (+2), full suite green.

## 2026-07-15 (cont.165) — /goal grind: _CheckNestFightB/R — nest combat trigger
- RECOVERED `check_nest_fight_b`/`check_nest_fight_r` (`_CheckNestFightB`/
  `_CheckNestFightR`, SIMANTW.SYM seg6:3BA2/61A2, args x=[bp+6],
  y=[bp+8], attacker=[bp+10]; FAR return) — the combat-TRIGGER gate
  the already-recovered `do_nest_fight_b`/`r` are the AFTERMATH of.
  Composes `is_yellow_ant`, `find_in_b_list`/`find_in_r_list`, and
  `get_winner` — all already recovered.
  - Confirmed via independent disassembly that R is genuinely NOT a
    mechanical twin of B: the caste-range check runs FIRST in R
    (opposite order from B, which checks `is_yellow_ant` first,
    unconditionally); a range HIT always attempts the fight in R with
    no yellow-ant check at all; the `_YellowFight` gate flag polarity
    is INVERTED between the two (`dgroup[0xCE98] != 0` triggers it for
    B, `== 0` for R); the `_YellowFight` first argument differs (`2`
    for B, `3` for R); and a non-yellow out-of-range tile returns `0`
    immediately in R, where B still falls through to attempt a normal
    fight.
  - `_YellowFight` itself (seg6:823E, ~458 bytes) is NOT recovered —
    its call site's own return value is discarded by the real ASM
    either way (both callers force `1`/`0` unconditionally after the
    call), so only the SIDE EFFECTS are unmodeled; per this project's
    fail-loud rule (same precedent as `try_move_dir_b`'s trophallaxis
    gate), that one branch raises `NotImplementedError` instead of a
    silently-wrong guess. Every other outcome is fully byte-exact.
- Caught the SAME class of test-harness region-window bug `food_fall`
  hit at cont.163, this time surfacing as a silent WRONG ANSWER instead
  of a hang: the SDG region started at `0x3B00`, excluding
  `find_in_b_list`'s own field arrays at `0x3736`/`0x392C`/`0x3D18`
  (below that bound) — the windowed `ByteBackend`'s negative-offset
  wraparound fed garbage into the search, silently causing a false
  "not found" instead of an exception. Diagnosed by reproducing the
  EXACT scenario standalone via plain `ByteBackend`s (no windowing),
  which passed cleanly — proving the recovered Python was correct and
  the harness's region bound was the bug. Fixed by widening the SDG
  region's low bound to `0x3700`.
- 8 cases (3 B, 3 R, plus one `NotImplementedError`-gate check per
  colony) — all green after the harness-window fix.
- Suite: simant 1690 (+8), full suite green.

## 2026-07-15 (cont.164) — /goal grind: _IsValidYard + _FindInLionList
- Dispatched an Explore survey now that every previously-deferred
  candidate is resolved; it enumerated seg6/seg7 symbols not yet
  recovered and cross-referenced against `gameplay.py` + `hooks.py`'s
  `_ISLANDS`. Picked the two smallest, highest-confidence hits to close
  the turn out; the rest of the survey's list (`_CheckNestFightB/R`,
  `_FeedAnts`, `_DoRestB/R`, `_DoRandB/R`, `_ForceModeA/B`,
  `_GstrB/R`, etc.) is recorded there for a future session.
- RECOVERED `is_valid_yard` (`_IsValidYard`, SIMANTW.SYM seg7:2072, args
  x=[bp+6], y=[bp+8]; FAR return, 36 bytes) — a THIRD bounds-check grid
  alongside the already-recovered `is_valid_a` (128x64) and
  `is_valid_b` (64x64): the small 12x16 boy's-yard grid (`0 <= x <=
  0xB`, `0 <= y <= 0xF`).
- RECOVERED `find_in_lion_list` (`_FindInLionList`, SIMANTW.SYM
  seg7:4B12, args val0=[bp+6], val1=[bp+8]; FAR return, 70 bytes) —
  the antlion-list twin of `find_in_a_list`/`find_in_b_list`/
  `find_in_r_list`'s reverse-scan idiom, but with two genuine
  differences independently confirmed via the raw disassembly: the
  live count comes from `simant_data_group[0x8A88]` (SDG, not `pack`
  like every sibling search), while the per-slot fields are parallel
  byte arrays in PACK instead (`pack[0x809C+slot]`/`[0x80BC+slot]`,
  reached via a hardcoded `0x5EF3` segment literal confirmed to equal
  the real PACK selector) — and there's no third nonzero-field gate.
- 22 cases total (9 `is_valid_yard`, 4 `find_in_lion_list`, plus this
  entry's own regression coverage) — ALL GREEN ON THE FIRST REAL-ASM
  RUN.
- Suite: simant 1682 (+13 from this entry: 9+4 test cases), full suite
  green.

## 2026-07-15 (cont.163) — /goal grind: _FoodFall/_DropFoodA — yard food-fall physics
- RECOVERED `food_fall`/`drop_food_a` (`_FoodFall`/`_DropFoodA`,
  SIMANTW.SYM seg5:0EAA/0D86; the LAST of the originally-deferred
  round-5 candidates, closing that batch out entirely). Resolved the
  "unexplained zero-extension quirk on a signed delta table" flagged at
  deferral time: `food_fall`'s per-step `(dx, dy)` walk delta is read
  from `dgroup[pack[0x9C66] + 0x22BE/0x22C2]` as an UNSIGNED byte (zero-
  extended) even though the table holds signed deltas (`0xFF` meaning
  `-1`) — confirmed via the raw disassembly (no sign-extend instruction
  anywhere in either routine) and ported literally, not corrected. Since
  `pack[0x9C66]` never changes mid-call, `(dx, dy)` are effectively
  constants per call.
  - `food_fall(x, y)`: walks the yard map plane by `(dx, dy)` each
    step, "hardens" the first tile `< 4` it lands on
    (`(tile + 6) << 2`, bumping `pack[0x9E84]`), and keeps walking
    until it goes out of the signed `[0, 0x7F]`x`[0, 0x3F]` bounds.
    Returns `dx` itself — the real ASM's natural fall-through leaves
    whatever the constant x-delta byte was in AX; no explicit return
    value is ever set, a genuine leftover-register quirk, independently
    confirmed and ported as-is (NOT the clean `0`/`1` `_DropFoodA`'s own
    inlined copy of this same loop explicitly forces).
  - `drop_food_a(x, y)` dispatches on `pack[0x9B6E]` ("inside") and the
    current yard tile: hardens tiles `< 4`; recursively re-harden tiles
    `8..0x17` via `(tile-8)>>2`; plain-increments `0x18..0x26`; runs
    `food_fall`'s walk (for side effects only, ALWAYS returning `1`
    regardless of the walk's own quirky return) for tiles `4..7` OR
    `0x27..0x3F` — a genuine NON-CONTIGUOUS union, independently
    confirmed via the raw disassembly, not a transcription slip; and
    no-ops `>= 0x40`. Outside ("not inside"): force-sets tiles `< 0x48`
    to exactly `0x48`, increments `0x48..0x4A`, no-ops `>= 0x4B`.
- Caught and fixed a genuine TEST-HARNESS bug (not a recovery bug) via
  a real hang: the first `food_fall` state-diff test's DGROUP region
  window started at `0x28E8` (the yard map plane base), excluding the
  `0x22BE`/`0x22C2` delta-table bytes the recovered Python reads —
  the windowed `ByteBackend`'s negative-offset wraparound fed garbage
  into the walk, and the resulting bogus `(dx, dy)` sent the Python
  loop into (what looked like, and given enough bad luck could
  genuinely be) an unbounded walk while the REAL ASM run — using the
  correctly-seeded live machine memory, unaffected by the Python-side
  windowing bug — finished in 136 steps every time. Diagnosed via two
  side-by-side instrumented traces (one hand-built matching the harness
  exactly, one via the harness's own `_run_and_diff_segs`) that proved
  the ASM itself was never the problem; fixed by widening the region's
  low bound to `0x22BE`.
- 13 cases (4 `food_fall`, 9 `drop_food_a` spanning both `inside`
  polarities and every tile-range branch including the non-contiguous
  scatter union) — all green after the harness-window fix.
- Suite: simant 1669 (+13), full suite green. This closes out every
  originally-deferred round-5/6 candidate from this session
  (`_GetMyDir`, `_GetMyDis`, `_FoodFall`/`_DropFoodA`) — none remain.

## 2026-07-14 (cont.162) — /goal grind: _GetMyDis — cross-plane distance
- RECOVERED `get_my_dis` (`_GetMyDis`, SIMANTW.SYM seg6:8682, args
  plane=[bp+6], cur_x=[bp+8], cur_y=[bp+10], tgt_plane=[bp+12],
  tgt_x=[bp+14], tgt_y=[bp+16]; FAR return, 0x1A6 bytes) — the routine
  cont.145-era survey originally deferred as "multi-branch colony-anchor
  distance routing." Composes only `get_dis`, reused against the SAME
  four SDG "connector" coordinate slots `get_my_dir` (cont.161) reads
  as its alternate-destination table — confirms those four slots are a
  genuine shared yard<->nest2/nest3 tunnel-endpoint table, not
  incidental to either routine alone.
  - Same-plane query: a plain `dis(cur, tgt)`.
  - Cross-plane: sums 2 or 3 `_GetDis` legs routed through the
    connector table (2 legs when either endpoint's plane is the yard,
    3 legs — via BOTH nest-side connectors plus an anchor-to-anchor
    hop — when routing nest2<->nest3 directly). Every running sum is
    masked `& 0xFFFF` to match the real ASM's `add ax,cx` on `_GetDis`'s
    low word only (DX/the high word is never consulted here, unlike
    `get_dis`'s own full 32-bit-squared-distance return).
  - Two branches push the SAME anchor-to-anchor `_GetDis` call in
    SWAPPED argument order (`dis(table_A, table_B)` vs `dis(table_B,
    table_A)`) — independently confirmed via the raw disassembly, not
    a transcription slip, and ported literally.
- 9 cases spanning same-plane, both yard<->nest directions, both
  three-leg nest2<->nest3 directions, and the `plane == 0` edge — ALL
  GREEN ON THE FIRST REAL-ASM RUN.
- Suite: simant 1656 (+9), full suite green.

## 2026-07-14 (cont.161) — /goal grind: _GetMyDir — target-select + probe
- Resumed cont.160's deferred `_GetMyDir` and finished the map: the
  "SDG-resident far-pointer dispatch table" flagged in cont.160 was a
  MISREAD on first pass — re-disassembling the exact bytes at
  `8F76`-`8F8D` showed the earlier "jge -> 90A1" transcription was
  backwards (it's `jge -> 8F8C`, falling to `90A1` only when NOT
  taken); once corrected, the SDG table entries at `0x835A/0x835C`,
  `0x835E/0x8360`, `0x8352/0x8354`, `0x8356/0x8358` turned out to be
  plain (x, y) COORDINATE pairs (an "alternate destination" table), not
  code pointers — no new dispatch mechanism after all, just more
  branches over already-recovered composables.
- RECOVERED `get_my_dir` (`_GetMyDir`, SIMANTW.SYM seg6:8ECA, args
  plane=[bp+6], cur_x=[bp+8], cur_y=[bp+10], sub=[bp+12], tgt_x=[bp+14],
  tgt_y=[bp+16]; FAR return, 0x314 bytes). Composes `check_my_best_dirs`,
  `get_my_best_dirs`, `get_my_rand_dirs`, and `get_dir` — no new
  primitives. Picks a target: the caller's own `(tgt_x, tgt_y)` when
  `plane <= 1` (yard) and `sub <= 1`, or when `plane > 1` (nest) and
  `sub == plane`; otherwise one of the four SDG coordinate-table slots
  above. Then runs the SAME `pack[0x72E4]`-gated probe `get_my_best_dir`
  (cont.157) already established (non-negative sentinel: try
  `check_my_best_dirs`, falling back to `get_my_rand_dirs` on total
  failure; negative sentinel: a single fresh `get_my_best_dirs`,
  escalating to a freshly-seeded `get_my_rand_dirs` only once the
  sentinel is ALREADY exactly `-2` from a prior tick) — independently
  re-verified byte-for-byte against `_GetMyDir`'s own disassembly
  rather than assumed identical to the sibling, and genuinely found ONE
  divergence: this routine's "sentinel >= 0, `check_my_best_dirs`
  succeeds" tail does an EXTRA `pack[0x72E4] -= 1` after resetting it to
  `-1` that `_GetMyNextRandDirs` (the closest sibling) does not do —
  confirmed present via a byte-level re-check, not assumed from
  symmetry.
- 7 cases spanning both `plane` tiers, all four alt-target table slots,
  and both stuck-sentinel polarities — ALL GREEN ON THE FIRST REAL-ASM
  RUN.
- Suite: simant 1647 (+7), full suite green.

## 2026-07-14 (cont.160) — /goal grind: _GetMyDir deferred (partial map)
- Picked `_GetMyDir` (SIMANTW.SYM seg6:8ECA, 0x314 bytes, SIX args
  `[bp+6..16]`) back up after cont.159 deferred it. Confirmed all of
  its composed near/far calls are already-recovered: `_GetMyBestDirs`
  (`get_my_best_dirs`), `_GetMyRandDirs` (`get_my_rand_dirs`),
  `_CheckMyBestDirs` (`check_my_best_dirs`), `_GetDir` (`get_dir`) — no
  new primitives needed for the branches mapped so far.
- Fully mapped the `plane <= 1` (yard) branch, both `[bp+12]` sub-cases
  (`<= 1` and reaching `_GetMyBestDirs`/`_CheckMyBestDirs` directly) and
  the `pack[0x72E4] < 0` ("stuck sentinel already pending") path: a
  `check_my_best_dirs` call; on total failure (`-2`) AND the sentinel
  was ALREADY `-2` from a prior tick, falls back to a `_GetDir(cur,tgt)
  - 1` raw compass direction seeded into `get_my_rand_dirs`'s two
  far-pointer outputs (`out1=pack[0x78A4]=0` fresh-commit, `out2=
  pack[0xA0D8]=` that direction), resetting `pack[0x72E4]=0x10` (a
  fresh 16-step retry budget) — the SAME `pack[0x72E4]` stuck-sentinel
  field `get_my_best_dir` (cont.157) already established.
- Started the `[bp+12] > 1` (`dx >= 2`) sub-case and hit a genuinely
  new mechanism: reads a SDG-resident TABLE at `0x835A:0x835C`
  (`[bp+12] == 2`) or `0x835E:0x8360` (`[bp+12] >= 3`) as a 4-byte FAR
  POINTER pair — the SAME encoding this routine's own `pack[0x72E4]`
  far-pointer local uses — and pushes one half of it before jumping
  into a not-yet-mapped tail (`9121`) that looks like it sets up an
  INDIRECT CALL through that table entry (a callback/dispatch
  mechanism, not a plain composed call to an already-recovered
  routine). The `plane > 1` (nest) branch (`8F90` onward) and the
  `9121`/`911E`/`90A1`/`9074` tail regions are still completely
  unmapped.
- DEFERRED, not a bug: this is a genuinely bigger/different-shaped
  routine than `_DoNestingB`/`R` (a dynamic dispatch table, not just
  more branches over already-known composables) — a good candidate to
  resume fresh next session with this partial map as the starting
  point, rather than sinking a third massive orchestrator into an
  already very long session. No test/code changes this entry — pure
  reconnaissance, nothing to revert.

## 2026-07-14 (cont.159) — /goal grind: _DoNestingB/_DoNestingR — nest dig/tend tick
- RECOVERED `do_nesting_b`/`do_nesting_r` (`_DoNestingB`/`_DoNestingR`,
  seg6:44A8/690A, args x=[bp+6], y=[bp+8], mode=[bp+10], sub=[bp+12];
  FAR return) — the largest orchestrators recovered this session
  (0x31E/0x22E bytes). Composes `get_enter_dir_b/r`, `get_exit_dir_b`,
  `place_egg_b/r`, `find_in_b/r_list`, `get_new_mode_b/r`, and
  `try_move_dir_b/r`, all already recovered — no new primitives needed.
  Confirmed by independent disassembly that R is NOT a mechanical
  table-swap of B: genuinely different branch shapes (a third `_SRand4`
  gate B never rolls, an unconditional single `get_new_mode_r` refresh
  where B does a two-step erosion-then-roll, and a clean early-return
  value in R's list-search-hit branch vs B's AH-clobber artifact — B's
  compiler loads `AX = 0x5EF3` right before a byte-only `mov al,...`
  and never clears AH before an early `ret far`, independently
  confirmed via the raw disassembly).
- Caught a real bug via the instrumented-exploration-before-testing
  discipline: mistranscribed the epilogue's retry roll as
  `_SRand1(100)` in both `finish()` closures when it's actually
  `_SRand8()` (far call target `2F99:15EE`, not `2F99:158A`) — caught
  immediately by a scratch exploration script (not yet the real ASM
  oracle) when `try_move_dir_b`'s `GET_BEST_DIR_DY[direction]` raised
  `IndexError` for a roll in `0..99` used where a `0..7` compass index
  was required. Also caught and fixed (before ANY test run) two
  "erosion/refresh block runs on a list-search-miss" control-flow bugs
  in the initial draft of both routines' `sub == 2` branches — the ASM
  has the "not found in list" case skip erosion/refresh entirely and
  fall straight to the default direction, which an early sloppy
  fall-through in my first draft didn't honor.
- 14 cases (9 for B, 5 for R) spanning every `sub` value's major
  sub-branches (entry/exit-dir success and failure, egg-spawn vs
  skip-spawn, list-search hit and miss, hole-erosion vs `field_c`
  refresh, and the numeric-default fallback) — ALL GREEN ON THE FIRST
  REAL-ASM RUN (after the exploration-script fixes above).
- Suite: simant 1640 (+14), full suite green.

## 2026-07-14 (cont.158) — /goal grind: _FindLifeAt/_FindEggAt — locate an ant at (x,y)
- Deferred `_GetMyDir` after a first pass: it's turning out comparably
  large/branchy to `_GetMyBestDir` (composing `check_my_best_dirs`,
  `get_dir`, `get_my_best_dirs`, `get_my_rand_dirs` across several
  nested dispatch levels) — picked the smaller `_FindLifeAt`/
  `_FindEggAt` pair instead to keep the "smallest tractable target
  first" discipline; `_GetMyDir` remains a good next-session candidate.
- RECOVERED `find_life_at`/`find_egg_at` (`_FindLifeAt`/`_FindEggAt`,
  seg5:8A96/88A2, args OUT_slot_ptr=[bp+6] (far pointer — ported as the
  first element of a returned tuple), list_type=[bp+10], x=[bp+12],
  y=[bp+14], FAR return) — composes `is_yellow_ant`, `find_ant_index`,
  `find_life_index`, `get_ant_index`, all already recovered, no new
  primitives needed for either. Locates whatever ant occupies `(x, y)`:
  trusts a direct life-plane tile read (unless it's empty or the
  player's yellow-ant marker — same "distrust the yellow sentinel"
  idiom as `_LostHead*`), falling back to a list search + full-record
  fetch otherwise. `_FindEggAt` is a narrowly-scoped twin, confirmed by
  independent disassembly: it ALSO requires the tile's masked caste to
  be in the egg/larva growth-stage range `1..7` (`sim_egg_b`/`r`'s own
  range) before trusting a direct read, and narrows the list-fallback
  search to that same range instead of `find_life_at`'s general
  `1..0x7F`.
- 14 cases (7 scenarios × both routines: direct-tile success both
  found-and-not-found in the list, the yellow-sentinel and empty-cell
  fallback triggers, empty-cell-plus-empty-list total failure, and
  direct hits on both the B-list and R-list) — ALL GREEN ON THE FIRST
  RUN.
- Suite: simant 1626 (+14), full suite green.

## 2026-07-14 (cont.157) — /goal grind: _GetMyBestDir — stuck-sentinel gate + probe walk
- RECOVERED `get_my_best_dir` (`_GetMyBestDir`, seg6:8D3A, args
  plane=[bp+6], cur_x=[bp+8], cur_y=[bp+10], tgt_x=[bp+12],
  tgt_y=[bp+14], FAR return) — composes `get_my_best_dirs`, `get_dir`,
  and `get_my_rand_dirs`. First checks a "stuck" sentinel
  (`pack[0x72E4]`, the SAME field `get_my_next_rand_dirs`/
  `get_my_initial_rand_dir` already use); when clear, runs the SAME
  probe-and-dispatch walk `get_my_next_rand_dirs` performs. When set
  (a stuck signal from a PREVIOUS call), retries `get_my_best_dirs`
  once and only commits a fresh `get_my_rand_dirs` search (the exact
  `get_my_initial_rand_dir` commit sequence) when DOUBLE-confirmed
  still stuck.
- First test run caught a genuine gap in the reuse plan, not a logic
  bug: the "normal path" is compiler-duplicated from the SAME shape as
  `get_my_next_rand_dirs`, so the plan was to just call it directly —
  but the real ASM has ONE extra instruction past where a first read of
  the disassembly stopped (`dec es:[bx]` on `pack[0x72E4]`, right after
  what looked like the routine's natural end at the shared return
  point). The state-diff mismatch pointed straight at the exact byte
  (`pack[0x72E4]`'s low byte, `0xFE` vs `0xFF`) — fixed by wrapping the
  `get_my_next_rand_dirs` call with an unconditional
  `pack[0x72E4] -= 1` afterward, confirmed correct across both the
  "commits a fresh search" and "doesn't" sub-paths.
- 4 cases (normal path at-target, stuck-retry succeeding outright,
  stuck-retry failing with a sentinel mismatch, and double-confirmed
  stuck committing a fresh search) — green after the fix.
- Suite: simant 1612 (+4), full suite green.

## 2026-07-14 (cont.156) — /goal grind: _DoReturnFoodAnt — a food-carrying ant heads home
- RECOVERED `do_return_food_ant` (`_DoReturnFoodAnt`, seg6:1CB4, arg
  slot=[bp+4], NEAR return) — another genuinely TOP-LEVEL `_Do*Ant*`
  behavior. Composes the already-recovered `is_valid_a`, `go_in_nest`,
  `get_nest_dir`, and `jam_scent_bt`/`rt` — no new primitives needed.
  Same nest-entrance tile check as `do_rest_ant` (identical `0x50`
  outside / `0x80..0x8F` inside band); on a match, enters via
  `go_in_nest`. Otherwise steps one cell via `get_nest_dir`'s gradient/
  homing direction, unless the destination is too crowded
  (`pack[0x7604]` threshold), in which case it jitters caste in place
  via a small SDG table lookup instead of moving. On an actual move,
  decrements a carried-food counter (`field_e`) if nonzero and jams the
  mover's own colony's TRAIL scent at the new position — a food-
  carrying ant leaves a trail; `field_e==0` skips this entirely.
- 5 cases (nest-entrance entry, a plain move with no trail, a move
  leaving a trail for each colony, and the crowded-destination jitter-
  in-place path) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1608 (+5), full suite green.

## 2026-07-14 (cont.155) — /goal grind: _PlaceBlackQueen — scenario-init black queen founding
- User asked mid-turn whether "readme coverage" needed updating; clarified
  there are two separate metrics — the "Coverage by segment" table
  (recovered+state-diff-verified, kept current after every commit this
  session, already pushed) vs. the "native-port progress" table
  (tied to `hooks._ISLANDS`, a live-runtime-hook registry, currently 69
  entries and untouched this session since none of this session's work
  involved wiring functions into the live hook system). User confirmed
  they meant the first (already current) — nothing further needed there.
- RECOVERED `place_black_queen` (`_PlaceBlackQueen`, seg7:65CE, NO args,
  FAR return) — the black-colony sibling of `place_red_queen`, same
  overall dig-a-tunnel-then-found-a-queen shape.
- Caught a genuine hand-derivation bug via instrumented tracing, not
  just re-reading the disassembly harder: assumed the wander loop's
  x-drift reset to `0` on any step where `_SRand2()` didn't roll a
  reroll, matching a naive first read of the ASM. An instrumented trace
  of the real ASM's register values showed `di` (the drift) holding a
  STICKY, unchanging nonzero value across several consecutive steps
  that never re-entered the reroll branch — the drift is genuinely
  persistent across iterations (initialized to `0` once before the
  loop, only ever REPLACED by a reroll, never reset). This is a
  different flavor of bug than `_PlaceRedQueen`'s earlier `+2`-offset
  miss, but the SAME fix discipline caught it: don't trust a hand
  trace's confidence, verify against real register values before
  writing the test.
- 3 cases (matching `_PlaceRedQueen`'s own test shape: default seed,
  a seed producing different wander/count rolls, and near the B-list
  500-slot cap) — green after the fix.
- Suite: simant 1603 (+3), full suite green.

## 2026-07-14 (cont.154) — /goal grind: _DoNestFightB/_DoNestFightR — nest combat tick
- RECOVERED `do_nest_fight_b`/`do_nest_fight_r` (`_DoNestFightB`/
  `_DoNestFightR`, seg6:3A54/6072, args x=[bp+6], y=[bp+8], FAR
  return) — the nest-list cousin of `do_fight_a`'s yard combat tick,
  same overall shape (reroll caste low bits via `_SRand1(7)`, stamp the
  life grid, `_SRand16()` 1-in-16 kill-tick). Composes the already-
  recovered `get_new_mode`, `add_ant_to_b_list`/`r_list`.
- Confirmed a GENUINE structural asymmetry by independent disassembly
  of both (not assumed): on a kill tick, both spawn a corpse-tail
  record when the dying caste's mode is exactly `0x60`, using the SAME
  coordinate-role-swap convention already established elsewhere. But
  the final `field_c` resolution diverges completely — `_DoNestFightB`
  calls the general `get_new_mode(sub, full_byte=caste)`; `_DoNestFightR`
  does NOT call `get_new_mode`/`get_new_mode_r` at all, instead reading
  a plain 16-entry static DGROUP table (`dgroup[0x22E6+mode]`) directly.
  The "wrong colony" fallback branch is also inverted (B's abnormal
  case is colony-bit SET, R's is colony-bit CLEAR) — consistent in
  INTENT (both mean "this ant's colony bit doesn't match my routine")
  but genuinely different code shape, not a copy-paste twin.
- 8 cases (4 scenarios × both colonies: no-kill roll, kill without
  corpse spawn, kill with corpse spawn into the normal tail, kill with
  corpse spawn into the wrong-colony fallback) — ALL GREEN ON THE
  FIRST RUN, confirming the careful independent-disassembly discipline
  continues to pay off on asymmetric B/R pairs.
- Suite: simant 1600 (+8), full suite green.

## 2026-07-14 (cont.153) — /goal grind: _SimEggB/_SimEggR — nest egg/larva growth tick
- RECOVERED `sim_egg_b`/`sim_egg_r` (`_SimEggB`/`_SimEggR`, seg6:3CA0/
  62A6, args x=[bp+6], y=[bp+8], FAR return). Both advance a nest egg/
  larva's growth-stage counter each tick (gated on a bitmask check
  against `pack[0x75FC]`), and possibly hatch it into a real ant every
  8 ticks (`counter & 0xF == 8`) by composing the already-recovered
  `get_new_mode_b`/`r`.
- Confirmed a genuine, SUBSTANTIAL asymmetry by independent
  disassembly of both (not assumed): `_SimEggB`'s hatch path is gated
  on `pack[0x9FCE]`, optionally rolls `sg_rand(0xFF)` against a
  threshold to decide between hatching (reads a mode byte from
  `simant_data_group[0x8A56]`, with a `mode==2` special case) or
  resetting the counter to 0 and bumping an unrelated 32-bit counter.
  `_SimEggR` has NO gate at all — it unconditionally rolls a fresh
  `_SRand8()`, combines it with a `pack[0x7690] % 7`-indexed table
  lookup, and ALWAYS composes `get_new_mode_r` every hatch tick, with
  no reset-counter path and no mode==2 special case. Even the bitmask
  gate's own DGROUP field and comparison direction differ
  (`dgroup[0xAC82] > 2` for B vs `dgroup[0xAC84] == 1` for R).
- 10 cases (6 for B covering every branch — bitmask block, plain
  increment, hatch-with-gate-set both mode==2 and mode!=2, hatch-
  without-gate both roll outcomes; 4 for R covering bitmask block,
  plain increment, and hatch under two different `dgroup[0xAC84]`
  values) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1592 (+10), full suite green.

## 2026-07-14 (cont.152) — /goal grind: _GetMyNextRandDirs — probe ahead, dispatch on outcome
- DEFERRED `_GetMyDis` after partial disassembly: the 6th survey's
  "thin wrapper around `get_dis`" characterization was WRONG — it's a
  genuine colony-anchor-routing distance function with up to 6
  asymmetric colony-pair branches, each reading a different SDG
  scratch-field pair (`_MakeNewHoleB`'s own `[0x8352]/[0x8354]` and
  `[0x835A]/[0x835C]` "last placed hole" fields, plus two more pairs
  presumably from `_MakeNewHoleR`). Genuinely tractable in principle
  (composes only `get_dis`) but the branch count made it a poor value
  a rushed pass — deferred rather than guess at the anchor semantics,
  same call as `_DropFoodA`/`_CreateNewHole` earlier this session.
- RECOVERED `get_my_next_rand_dirs` instead (`_GetMyNextRandDirs`,
  seg6:8BEA, args plane=[bp+6], x=[bp+8], y=[bp+10], tgt_x=[bp+12],
  tgt_y=[bp+14], FAR return) — a genuinely unusual "probe ahead but
  discard the result" pattern: walks a SHADOW position up to 64 steps
  via the already-recovered `get_my_best_dirs`, but the walked-to
  position itself is NEVER used for anything except whether the walk
  ever hit a `-2` ("nothing clear at all") outcome. The final dispatch
  always re-calls from the ORIGINAL `(x, y)`: a `-2` anywhere in the
  walk falls back to `get_my_rand_dirs`; anything else (including a
  forced `-1` when the walk succeeded the full 64 steps) re-calls
  `get_my_best_dirs` one more time and returns that directly.
- 3 cases (immediate `-1` at-target, immediate `-2` all-blocked falling
  through to `get_my_rand_dirs`, and a one-step-then-default-terrain
  walk) — ALL GREEN ON THE FIRST RUN (one quick lambda-arity fix in the
  test harness itself, unrelated to the recovered logic).
- Suite: simant 1582 (+3), full suite green.

## 2026-07-14 (cont.151) — /goal grind: _DoRepoFly — reproductive-flight departure
- RECOVERED `do_repo_fly` (`_DoRepoFly`, seg6:0D4A, arg slot=[bp+4],
  NEAR return) — a yard ant occasionally departs on a "reproductive
  flight": gated on a `_SRand32()` roll of `0` (1-in-32) and the slot's
  own colony departure counter (`pack[0x807A]` black / `[0x9C26]` red)
  being `< 50`, clears the slot's caste and yard life-grid cell. If
  `pack[0x80B4] == 2` (an outer game-phase gate), also increments that
  SAME colony counter and rolls `_SRand16()`; a further `0` (1-in-16)
  bumps a DGROUP milestone counter.
- Caught a real test-infrastructure bug via a genuine crash, not a
  silent divergence: the FIRST test run failed EVERY case (including
  the trivial "roll32 nonzero, immediate no-op" one) with `INT 03h ...
  no Win16 service installed` — traced to forgetting `near=True` on
  `_run_and_diff_segs` for this NEAR-return routine, which mismatches
  the sentinel-return convention (pushes a CS word the real `ret near`
  never pops) and sends the CPU off into garbage code after return. One
  keyword fix, all 10 cases passed immediately after.
- The real ASM's rare-milestone branch also calls a presentation-only
  redraw-invalidation stub (`SIMANT!_InvalQueenStorageDisp`) — omitted,
  and confirmed harmless by the state-diff itself passing with it
  un-simulated (same core/presentation split as `_FightBalloons`).
- 10 cases (5 scenarios × both colonies) — all green after the fix.
- Suite: simant 1579 (+10), full suite green.

## 2026-07-14 (cont.150) — /goal grind: _DoRestAnt — a top-level _Do*Ant* orchestrator
- Dispatched a 6th research survey. Confirmed `_IsThisFood` was already
  a false lead (it's already `is_this_food`, recovered in cont.60) —
  caught before wasting effort. Re-verified all of round 5's larger
  deferred candidates (`_PlaceBlackQueen`, `_GetMyNextRandDirs`,
  `_GetMyDis`, `_GetMyBestDir`, `_DoReturnFoodAnt`, `_DoNestingB`/`R`)
  are still accurate and zero-blocker. Found a fresh
  `_Do*Ant*`-orchestrator-tier lead worth prioritizing.
- RECOVERED `do_rest_ant` (`_DoRestAnt`, seg6:0B76, arg slot=[bp+4],
  NEAR return) — a genuinely TOP-LEVEL `_Do*Ant*` behavior (a yard ant
  on a "rest spot" heads into the nest via the already-recovered
  `go_in_nest`; otherwise a `_SRand4()` roll of `0` (1-in-4) marks it
  "resting" via `field_c=2`). The 3-in-4 no-roll path's presentation-
  only speech-balloon UI call (`ANTEDIT!_RestBalloons`) was deliberately
  NOT ported — same core/presentation split as `_FightBalloons` in
  `do_fight_a`.
- A test-infra gap, not a logic bug: `_GOINNEST_REGIONS`'s own PACK
  upper bound (`0x9A00`) didn't reach `pack[0x9B6E]` (the new "inside"
  flag read this routine needs) — widened the LOCAL region copy to
  `0x9C00` rather than touching the shared constant (this routine's own
  test doesn't need `_GOINNEST_REGIONS` itself, just a region with the
  same shape plus headroom).
- 5 cases (outside rest-spot exact tile, inside rest-band range, no
  rest spot with both SRand4 outcomes, and an out-of-range position) —
  ALL GREEN on the second run (first run caught the PACK-bound gap
  immediately, fixed in one edit).
- Suite: simant 1569 (+5), full suite green.

## 2026-07-14 (cont.149) — /goal grind: _StayInR — idle-in-nest: nibble food or wander
- RECOVERED `stay_in_r` (`_StayInR`, seg6:5C16, args x=[bp+6], y=[bp+8],
  direction=[bp+10], FAR return) — the last item in this session's
  research-survey batch. Composes the already-recovered
  `try_move_dir_r` and `get_enter_dir_r`. If the red nest map tile at
  `(x, y)` is in the food-pile band (`0x10..0x13`): a depleted (`0x10`)
  tile rerolls via `_SRand8()`, otherwise decrements by 1; either way
  bumps the slot's R-list `field_c`/caste flags and falls to a shared
  tail. Otherwise: rerolls a new direction and tries `try_move_dir_r`;
  on failure, looks for an entry direction via `get_enter_dir_r`
  (falling back to a fresh `_SRand1(8)` roll if none found) and tries
  again — either successful move returns immediately. The shared tail
  (food branch, or both movement attempts failing) just re-stamps the
  slot's current caste onto the life-grid cell.
- Region reused `_TRYMOVE_GETOUT_REGIONS` directly (identical bounds to
  `_MAKENEWHOLEB_REGIONS`) since it already covers everything this
  composition touches.
- 5 cases (food reroll, food decrement with the counter both nonzero
  and zero, a movement branch where the first try succeeds, and one
  where it fails and falls through the `get_enter_dir_r`/fallback-roll
  path) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1564 (+5), full suite green. This closes out the 5th
  research-survey batch dispatched in cont.141 — all zero-blocker leads
  from that survey are now recovered or explicitly deferred
  (`_DropFoodA`/`_FoodFall`). Next session should dispatch a fresh
  survey before continuing.

## 2026-07-14 (cont.148) — /goal grind: _DigOutBNest/_DigOutRNest — wander a nest tunnel up
- RECOVERED `dig_out_b_nest`/`dig_out_r_nest` (`_DigOutBNest`/
  `_DigOutRNest`, seg7:62DE/63B8, arg count=[bp+6], FAR return) via a
  shared `_dig_out_nest` helper — carves a wandering tunnel up from a
  fixed `(32, 1)` starting cell, one `count`-bounded step at a time.
  Each step rerolls the wander direction, steps by the compass delta,
  clamps `x`/`y` into range (each clamp forcing a specific direction for
  the CURRENT step, `y`'s clamp separately staging the direction for the
  NEXT step), then calls the already-recovered `dig_tile_them_b`/`r`;
  only on success does the tunnel advance, and only then — back at the
  surface row with no hole already tracked for that column — calls
  `make_new_hole_b`/`r`. Composes 3 already-recovered routines per
  colony (`dig_tile_b`/`r`, `dig_tile_them_b`/`r`, `make_new_hole_b`/`r`).
- The trickiest part was the direction-staging interplay: the register
  holding "current direction" gets reused across iterations in a way
  where the x-clamp and y-clamp both independently override it, and the
  y-clamp's override captures whatever the x-clamp left behind — traced
  both colonies' disassembly independently line-by-line before writing
  any Python, rather than porting incrementally and debugging failures.
- 6 cases (3 scenarios × both colonies: count=0 up-front-dig-only, a
  few wander steps with holes pre-tracked, more steps with holes NOT
  tracked to exercise the `make_new_hole` trigger) — ALL GREEN ON THE
  FIRST RUN, confirming the careful upfront tracing paid off.
- Suite: simant 1559 (+6), full suite green.

## 2026-07-14 (cont.147) — /goal grind: _GetNewRedTask — reassign the red recruit task
- RECOVERED `get_new_red_task` (`_GetNewRedTask`, seg6:9940, NO args,
  FAR return) — the routine that unblocked `_UnRecruitRed`/`_RecruitRed`
  earlier this session. Always starts by clearing every red ant's
  "recruited" marker via `un_recruit_red`. In game mode 1, rolls two
  chance gates (`_SRand1(32)+64 < dgroup[0xCD88]`, then `_SRand1(10) <
  pack[0x9E7A]`); both passing sets a "raid" task marker and calls
  `recruit_red(pack[0x9E7A])` directly. Otherwise (mode isn't 1, or
  either gate failed) falls back to a "general" task: recomputes two
  PACK-resident running estimates from SIMANT_DATA_GROUP fields
  (clamped back toward `20..40`/capped past `30`), then recruits a count
  derived from a DGROUP population-estimate sum (`>>2` or `>>3`
  depending on its size) via `recruit_red`.
- 6 cases (raid path with both gates passing, fallback via wrong mode,
  fallback via each gate failing individually, and two fallback-branch
  clamp variants) — ALL GREEN ON THE FIRST RUN; pre-computed the exact
  SRand seed via the recovered `srand1` function to hit both gates
  deterministically, same discipline as `_DoDrownB`/`R`.
- Suite: simant 1553 (+6), full suite green.

## 2026-07-14 (cont.146) — /goal grind: _DoDrownB/_DoDrownR — age/drown a nest-water ant
- RECOVERED `do_drown_b`/`do_drown_r` (`_DoDrownB`/`_DoDrownR`,
  seg6:37A4/5EA8, args x=[bp+6], y=[bp+8], caste=[bp+10], FAR return)
  via a shared `_do_drown` helper — below a drowning threshold (map tile
  `< 0x14`), just re-derives the slot's `field_c` via the already-
  recovered `get_new_mode_b`/`r`; at/above it, rerolls the caste's low 3
  direction bits, stamps the new caste onto both the slot and the
  life-grid cell, then rolls `_SRand1(100)`: 99-in-100 is a no-op
  (returns the roll), 1-in-100 drowns the ant (clears the cell + caste,
  bumps one of two 32-bit PACK counters by the REROLLED caste's colony
  bit — confirmed the SAME counter pair for both colonies by independent
  disassembly of both routines).
- Region merged `_GETNEWMODE_REGIONS`'s own SDG/PACK tables with the
  B/R-list field bases and drown counters into one window per segment.
- 8 cases (below-threshold/no-drown/drown-each-colony-bit x both
  colonies) — ALL GREEN ON THE FIRST RUN; pre-computed exact SRand seed
  values via the already-recovered `srand1` Python function to hit both
  the drown and no-drown branches deterministically rather than
  guessing seeds and hoping.
- Suite: simant 1547 (+8), full suite green.

## 2026-07-14 (cont.145) — /goal grind: _IsItYellow — is the player's yellow ant at (x,y)?
- RECOVERED `is_it_yellow` (`_IsItYellow`, seg5:96B6, args colony=[bp+6],
  x=[bp+8], y=[bp+10], FAR return) — a 3-way dispatch: gated first on
  `dgroup[0xCE80]` matching `colony` (with `colony==0` defaulting to `1`
  for THIS check only, not for the later plane lookup); then
  `pack[0x9FE8]==1` switches to a distance check against the RAW
  (un-`>>4`'d) attack-marker fixed-point position instead of a tile
  read; otherwise reads the life-plane tile at `LIFE_PLANE_BASE[colony]`
  and defers to the already-recovered `is_yellow_ant`.
- A genuinely unreachable branch (`colony` outside `0..3` under the
  tile-read path) reads UNINITIALIZED stack memory in the original
  binary — every established caller elsewhere in this codebase already
  uses `colony` in `0..3`, so this was intentionally left unmodeled: a
  `colony` outside `LIFE_PLANE_BASE`'s keys raises `KeyError` rather
  than guessing at stack garbage, consistent with "fail loud, never
  fake" for a path with no real game-logic meaning.
- 9 cases (mode mismatch, colony-0 defaulting, distance-mode near/far,
  distance-mode colony>1 rejection, both yellow-ant tile sentinels
  across two different planes, a non-yellow tile) — ALL GREEN ON THE
  FIRST RUN.
- Suite: simant 1539 (+9), full suite green.

## 2026-07-14 (cont.144) — /goal grind: _LeaveNestB — send a black ant out through a hole
- RECOVERED `leave_nest_b` (`_LeaveNestB`, seg6:515E, args col=[bp+6],
  x=[bp+8], FAR return) — tries to send the current black ant
  (`pack[0x9B6A]`'s slot) out through an above-ground hole at `col`,
  carving a fresh one via the already-recovered `make_new_hole_b` first
  if `_FillHolesBN`'s per-column tracking array
  (`simant_data_group[0x82D2+col]`) has nothing recorded yet. Rerolls a
  fresh caste (`_SRand8() + (orig_caste & 0xF8)`, keeping the high bits,
  replacing only the low 3 direction bits) and calls the already-
  recovered `exit_hole`; on success clears the black nest life-grid cell
  and returns `1`, on failure restores the slot's original caste/
  field_c and returns `0`.
- Regions reused `_MAKENEWHOLEB_REGIONS` directly — it's already a
  strict superset of `_EXITHOLE_REGIONS` across all three segments, so
  no new region constants were needed despite composing both routines.
- 3 cases (hole already tracked + exit succeeds, hole already tracked +
  exit fails/restores, and hole NOT tracked so `make_new_hole_b` fires
  first) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1530 (+3), full suite green.

## 2026-07-14 (cont.143) — /goal grind: _GetMyInitialRandDir — commit a fresh sticky search
- RECOVERED `get_my_initial_rand_dir` (`_GetMyInitialRandDir`,
  seg6:8CDE, args plane=[bp+14], cur_x=[bp+16], cur_y=[bp+18],
  tgt_x=[bp+20], tgt_y=[bp+22], FAR return — 4 leading stack words at
  `[bp+6..0xd]` genuinely unused by the body) — commits a fresh
  `get_my_rand_dirs` search: sets its "committed direction" PACK cell
  (`[0xA0D8]`) to `get_dir(cur,tgt) - 1` and its "commitment mode" cell
  (`[0x78A4]`) to `0` (forcing the bidirectional fresh-search path),
  stamps an unrelated new field (`pack[0x72E4] = 0x10`), then calls the
  already-recovered `get_my_rand_dirs` and writes its two output cells
  back to PACK.
- Untangled a genuine ambiguity across `get_my_best_dirs`/
  `check_my_best_dirs`/`get_my_rand_dirs`'s existing docstrings: none of
  them list "inside" among their real ASM stack args, yet their Python
  signatures all take it as an explicit parameter — confirmed by
  cross-referencing this routine's own call site (which passes exactly
  5 words: plane/cur_x/cur_y/tgt_x/tgt_y, matching the documented count)
  that "inside" is a world-state PACK read (`pack[0x9B6E]`) every
  caller in this chain is expected to compute itself, not a real
  parameter — resolved by having this routine read it directly, per the
  same convention `is_it_food_at`/`make_new_hole_b` already established.
- 6 cases (fresh-search forward/backward hits, already-at-target,
  nothing-clear, the yard-plane threshold band, and an inside+adjacent
  variant) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1527 (+6), full suite green.

## 2026-07-14 (cont.142) — /goal grind: _SGIRand/_SGRand/_SGSRand — two-roll RNG combinators
- RECOVERED `sg_i_rand`/`sg_rand`/`sg_s_rand` (`_SGIRand`/`_SGRand`/
  `_SGSRand`, seg5:147C/14A4/14CC, arg n=[bp+6], FAR return) — three
  two-roll RNG combinators built on `_SRand1(n)`: `sg_i_rand` returns
  `max` of two rolls (bias high), `sg_rand` returns `min` (bias low),
  and `sg_s_rand` returns `min` again but negates it half the time via
  a `_SRand2()` coin flip (a signed, symmetric-around-zero variant).
  All three thread the shared LFSR seed through 2-3 sequential rolls.
- Reused the established "pure aside from the SRand seed" test pattern
  from `_Bounce`'s own test verbatim (a fresh `bytearray(0x10000)` view
  seeded with just the PRE-state seed word, not `_run_and_get_ax`'s own
  post-execution machine) — confirms this pattern generalizes cleanly
  to a THIRD family of routines beyond `_Bounce`/`_GetForageDir`.
- 18 cases (3 routines x 6 seed/n combinations) — ALL GREEN ON THE
  FIRST RUN.
- Suite: simant 1521 (+18), full suite green.

## 2026-07-14 (cont.141) — /goal grind: _GetAntIndex/_FindLifeIndex — list-search siblings
- Dispatched a 5th research survey. It flagged `_SetAntIndex` and
  `_BlockMove` as candidates; both turned out to be non-issues once
  checked — `_SetAntIndex` is ALREADY recovered (`set_ant_index`), and
  `_BlockMove` is a generic memcpy already accounted for inline in
  `remove_from_a_list`'s own docstring. Caught before wasting effort
  re-deriving either.
- RECOVERED `get_ant_index` (`_GetAntIndex`, seg5:573C) — the read
  counterpart of `set_ant_index`. The real ASM writes through 5 far-
  pointer OUT params; ported as a function returning `(target0, target1,
  caste, field_c, field_e)` on success or `None` on an out-of-range
  slot, since Python has no equivalent calling convention. Test needed a
  genuinely new harness shape: pointed all 5 OUT pointers into unused
  DGROUP scratch (`0xF000+`) and read the words back after the real ASM
  run — reused `_run_and_get_ax`'s existing push-arg mechanism by
  passing the far pointers as (offset, selector) word pairs in natural
  declaration order, no new harness code needed.
- RECOVERED `find_life_index` (`_FindLifeIndex`, seg5:5922) — a
  `find_ant_index` variant: exact match on two fields, but a RANGE check
  (`lo <= (caste & mask) <= hi`) on a masked caste sub-field instead of
  an exact caste match.
- 20 cases (5 for `get_ant_index` across all three list dispatches plus
  2 failure modes; 15 for `find_life_index` — 5 scenarios x 3 list
  dispatches) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1503 (+20), full suite green.

## 2026-07-14 (cont.140) — /goal grind: _SFoundAnt — locate an ant near the attack marker
- RECOVERED `s_found_ant` (`_SFoundAnt`, seg5:53F6, NO args, FAR return) —
  the routine `_FindAntIndex` unblocked. Locates an ant near the current
  attack-marker target (`dgroup[0xAC7C]`/`[0xAC7E]`, the SAME fixed-point
  `>>4` target `get_defend_dir`/`scan_for_ants` already use), dispatched
  on `pack[0x7D60]`'s exact value:
  - `==7`: searches the yard A-list backward for an ant within squared
    distance `0x320` of the target; on no match, falls back to a fixed
    marker position gated on `pack[0x9FE8]==0` AND `dgroup[0xCE80]==1`.
  - anything else: walks up to 20 steps outward along a FIXED compass
    direction (`dgroup[0xAC80]` indexes the SAME compass table
    `sim_queen_a`/`make_blk_queen` use), requiring `is_valid_a` and range
    at each step; an occupied cell either aborts on the player's yellow
    ant or resolves via the just-recovered `find_ant_index`.
  Composes 4 already-recovered routines (`get_dis`, `is_valid_a`,
  `is_yellow_ant`, `find_ant_index`) with no new primitives needed.
- 10 cases (5 per branch, covering every early-return path in both) —
  ALL GREEN ON THE FIRST RUN despite the routine's size — the
  compositional approach (reuse already-verified primitives, trust their
  own proofs) kept this tractable where `_DropFoodA` wasn't.
- Suite: simant 1483 (+10), full suite green.

## 2026-07-14 (cont.139) — /goal grind: _UnRecruitRed/_RecruitRed — task recruitment flag
- DEFERRED `_DropFoodA`/`_FoodFall` (seg5:0D86/0EAA) after partial
  disassembly: a cascading food-pile-growth routine with a genuine
  outward search over a 4-direction table (`dgroup[0x22BE..)`/
  `[0x22C2..)`) driven by a PACK-resident "current search direction"
  index (`pack[0x9C66]`, set by some OTHER unrecovered caller, never
  written here) — and the ASM zero-extends (not sign-extends) the signed
  delta bytes before scaling one of them by 64, which reads like either
  a genuine quirk or a subtlety I haven't nailed down yet. Bigger and
  less certain than its size estimate suggested; deferred rather than
  guess, same call as `_CreateNewHole` earlier this session.
- RECOVERED `un_recruit_red`/`recruit_red` instead (`_UnRecruitRed`/
  `_RecruitRed`, seg7:08DA/0866) — the companion pair `_RecruitRed(n)`
  scans the yard A-list backward, marking up to `n` red ants (caste
  `>0x7F`, whose caste's `(caste&0x78)>>3` "mode" sub-field is `2` or
  `6`, and whose current `field_c` isn't already `0x13` or `6`) as
  recruited (`field_c=6`, `field_e=0`); `_UnRecruitRed()` (no args)
  clears that same `field_c==6` marker off every red ant. Confirmed via
  `symbols.nearest_symbol` that the address range immediately after
  `_UnRecruitRed` is actually `_GetNewMode`/`_GetNewModeB`/`_GetNewModeR`
  (already recovered) rather than a third undiscovered routine — caught
  before wasting a disassembly pass re-deriving already-done work.
- 12 cases (list-scan edge cases: mode 2, mode 6, already-recruited
  skip, wrong-mode skip, black-ant skip, count-exhausted, count=0
  no-op, empty list) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1473 (+12), full suite green.

## 2026-07-14 (cont.138) — /goal grind: _FindAntIndex — colony-dispatching list search
- RECOVERED `find_ant_index` (`_FindAntIndex`, seg5:59FC, args
  colony=[bp+6], field0=[bp+8], field1=[bp+10], caste=[bp+0xc], FAR
  return) — a generalized reverse-linear list search that dispatches on
  `colony` (<=1 -> A-list, ==2 -> B-list, else -> R-list) and matches
  THREE fields per slot at once (unlike `find_in_a_list`'s two-field-
  plus-nonzero-check, though it reuses the exact same per-slot field
  bases `find_in_a_list`/`find_in_b_list`/`find_in_r_list` already use).
  This unblocks `_SFoundAnt` (seg5:53F6, 320B) for a future slice.
- 20 cases (4 count/match scenarios x 5 colony selectors spanning all
  three dispatch branches, including the `colony==9` "anything else"
  fallthrough) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1461 (+20), full suite green.

## 2026-07-14 (cont.137) — /goal grind: _IsItFoodAt — bounds-checked food predicate
- RECOVERED `is_it_food_at` (`_IsItFoodAt`, seg5:5F7E, args plane=[bp+6],
  x=[bp+8], y=[bp+10], FAR return) — validates `(x, y)` against
  plane-dependent bounds (plane<=1: yard, `x` 0..0x7F; plane>1: nest,
  `x` 0..0x3F; both `y` 0..0x3F) and `plane` against `0..3`, returns 0
  immediately (no tile read at all) if either is out of range, otherwise
  reads the map tile and tail-calls the ALREADY-recovered `is_this_food`.
  Turned out to be a genuinely small wrapper once traced fully: grepped
  for the far-call target (`395E:2D1A`) before assuming it needed its own
  port, and found `_IsItFoodAt`'s entire tail (from the tile read onward)
  is exactly `is_this_food`'s existing logic — composing it instead of
  re-deriving it.
- 10 cases (both plane bands, the food/non-food boundaries on each,
  an out-of-range plane, and each out-of-range coordinate case) — ALL
  GREEN ON THE FIRST RUN.
- Suite: simant 1441 (+10), full suite green.

## 2026-07-14 (cont.136) — /goal grind: _AddBlackAnts/_AddRedAnts — scenario-init yard population
- RECOVERED `add_black_ants`/`add_red_ants` (`_AddBlackAnts`/`_AddRedAnts`,
  seg7:6C5A/6CFE, arg count=[bp+6], FAR return) via a shared `_add_ants`
  helper — scans the yard for empty walkable cells in a fixed
  `y=0x10..0x2F` band, placing up to `count` scenario-init ants with a
  random caste (`_SRand1(10)<=3` picks base `0x30`/`field_c=2`, else base
  `0x10`/`field_c=4`, plus `_SRand8()`), stopping at `count` placements or
  the A-list's `0x3E8` global cap. Both colonies' initial ants land in the
  SAME yard A-list (`_AddAntToAList`), distinguished only by `caste_bonus`
  (`0x80` for red). Confirmed a genuine twin by independent disassembly:
  black scans the LEFT half (`x=0..0x3F` ascending), red the RIGHT half
  (`x=0x7F..0x40` descending) — a coordinate-role realization caught by
  re-deriving the map-offset formula against the established `(x<<6)+y`
  convention rather than trusting the ASM's raw "row"/"col" register
  naming from a first read (the outer-loop 0x40-step variable is `x`, the
  inner 1-step variable is `y`, per `build_ant_list_a`'s established
  layout — not "row"/"col" as a naive read of the loop shape suggests).
- Test seeding fully zeroed the map+life planes across the scanned band
  so every candidate cell is deterministically valid — avoids depending
  on whatever terrain happens to be in the default boot state, and makes
  the RNG-threading sequence fully predictable.
- 6 cases (3 per colony, including a near-global-cap case) — ALL GREEN
  ON THE FIRST RUN, no re-derivation needed this time.
- Suite: simant 1431 (+6), full suite green.

## 2026-07-14 (cont.135) — /goal grind: _PlaceRedQueen — scenario-init red queen founding
- RECOVERED `place_red_queen` (`_PlaceRedQueen`, seg7:67DA, NO args, FAR
  return) — the scenario-init/no-args sibling of `make_red_queen`: rolls
  `_SRand4()+7` (7..10) as a row count, digs a wandering vertical tunnel
  from `(0x20, 1)` (each step nudging x by `_SRand1(3)-1`, clamped to
  `8..0x38`), steps 2 more cells diagonally, digs one more, records that
  position into new SDG scratch fields `[0x8366]`/`[0x8368]` (the red
  analogue of `_MakeNewHoleB`'s `[0x835A]`/`[0x835C]`), then digs 3 more
  cells and appends 2 ant-list records anchored on a hardcoded `x+2`
  bump. Composes `dig_tile_r` (up to 15x) and `add_ant_to_r_list` (2x).
- Caught a real derivation bug via instrumentation, not by re-reading
  harder: a hand-trace of the disassembly predicted a formula that
  looked internally consistent but was WRONG — missed that a
  `lea ax,[si+2]` between the SDG scratch-store and the compass-offset
  digs permanently bumps x by 2 before every downstream use, including
  both `_AddAntToRList` calls. First-pass state-diff test caught it (an
  off-by-2-rows life-plane mismatch), but rather than keep re-deriving
  by hand, wrote a throwaway script that ran the REAL ASM with a
  breakpoint at `_AddAntToRList`'s entry to print its actual (y, x,
  caste, fc, fe) stack args directly, and a second breakpoint at the SDG
  scratch-store to capture the real si/di — ground truth in two
  instrumented runs instead of a third manual re-trace. Confirms the
  session's "verify branch polarity/derivations against a real trace,
  don't just re-read the listing" discipline generalizes past
  branch-polarity bugs to plain arithmetic-derivation bugs too.
- 3 cases (including a near-cap R-list count) — green after the fix.
- Suite: simant 1425 (+3), full suite green.

## 2026-07-14 (cont.134) — /goal grind: _MakeBlkQueen/_MakeRedQueen — founding queen chamber
- RECOVERED `make_blk_queen`/`make_red_queen` (`_MakeBlkQueen`/`_MakeRedQueen`,
  seg7:671A/6906, FAR return, args x=[bp+6]/y=[bp+8]/direction=[bp+10]) —
  carves a founding queen's chamber: digs her own tile plus two farther
  cells along the compass opposite her facing (`direction ^ 4`, reading
  the SAME `simant_data_group` compass tables `sim_queen_a` uses), then
  appends two ant-list records for the chamber (one at her own position,
  one at the 1x-offset cell) with `caste = direction + 0x60`/`+0x68`
  (black) or `+0xE0`/`+0xE8` (red), and bumps a per-colony queen counter
  (`pack[0x78E8]` black, `pack[0x79DC]` red — both new, previously
  unreferenced fields). Composes `dig_tile_b`/`r` (called 3x each) and
  `add_ant_to_b`/`r_list` (called 2x each), all already recovered.
- Confirmed a genuine B/R twin by independent disassembly of both (not
  assumed symmetric): identical shape, but distinct caste-encoding
  constants and distinct counter fields, matching the discipline already
  established for `_QueenMoveB`/`R`.
- Selector-resolution discipline paid off again: verified `C5DC`/`C5DE`
  (both resolve to SDG) and `C5D4`/`C5D6` (both resolve to PACK) against
  a real machine before trusting them, rather than assuming from
  proximity to the already-known compass-table pattern.
- Region discipline: both routines touch the SAME real SDG/PACK segments
  their two composed callees separately touch — merged into ONE window
  per segment (`(_SDG, 0, 0x4200)` for black / `(_SDG, 0, 0x4C00)` for
  red) rather than two disjoint region entries, per the established
  "one real segment, one window" rule.
- 6 cases (3 per colony, including a near-cap A-list count) — ALL GREEN
  ON THE FIRST RUN.
- Suite: simant 1422 (+6), full suite green.

## 2026-07-14 (cont.133) — /goal grind: _BuildAntListA — full yard A-list rebuild
- RECOVERED `build_ant_list_a` (`_BuildAntListA`, seg5:3046, FAR return, NO
  args) — rebuilds the entire yard A-list from scratch by scanning the
  whole 128x64 yard life plane. Resets `pack[0x80F0]` (count) to `0`, then
  for every occupied cell that isn't a yellow-ant tile (`is_yellow_ant`
  gate, its only callee), appends a new A-list entry with `x`/`y` from the
  scan position, `field_c` hardcoded to `2`, `caste` set to the life-plane
  byte itself, `field_e` cleared. Ported the genuine silent cap at `0x3E5`
  (997) entries literally: once the count hits the cap it stops advancing,
  so further matches keep overwriting slot 997 rather than appending or
  erroring.
- Test infra bug, not a logic bug: the state-diff regions for this test
  were sized for narrower single-cell-position tests elsewhere and didn't
  cover a full-yard scan. Two rounds of `IndexError` → widened the SDG
  region from `(0x2300, 0x2400)` to `(0x2300, 0x3800)` (covers all five
  A-list field bases: `0x23A4`/`0x278E`/`0x2B78`/`0x2F62`/`0x334C`), and
  the DGROUP region from `(0x68E8, 0x78E8)` to `(0x68E8, 0x88E8)` — the
  yard `LIFE_PLANE_BASE[0]` plane is `0x2000` (8192) bytes (128x64), not
  `0x1000` (64x64); the seed function's own zeroing slice needed the same
  fix. Left the other, already-passing test at the older narrower bound
  alone — it only ever touches single-cell positions within that
  sub-range, so it isn't actually broken by the same underlying mistake.
- Suite: simant 1416 (+1), full suite green.

## 2026-07-14 (cont.132) — /goal grind: _SimQueenA — yard queen tick, round 3 survey
- Dispatched a fourth research survey (round 2 closed out in cont.131).
  Independently re-verified `_DoTroph` directly (not just trusting the
  cached verdict): confirmed it calls `_MoveMyLife` (unrecovered, itself
  reaching into presentation-entangled `_SetLife`) and `_EatMyFood`
  (unrecovered) plus a genuine `ANTEDIT!_DoEditUpdateDraw` redraw — NOT a
  clean separable no-op like `_FightBalloons` was for `_DoFightA`, so
  `_DoForageAnt`/`_DoNestAntB`/`_DoRecruitAnt` remain blocked. The survey
  found a genuine "previously-blocked-now-unblocked" discovery: the hole
  family's `_CreateNewHole` (seg5:171A, 506B) — flagged as blocked in an
  earlier session, but every one of its 9 callees is now recovered
  (`_DigTileB`, `_IsItDirt`, `_SRand8`, `__aFldiv`, `_SmoothEdgesR`,
  `_FixExitMapR`). Deferred it for now (large, multi-stage — spans a
  `_dig_tile_reroll_and_track`-style running average feeding
  `_QueenMoveR`'s own target fields) in favor of smaller candidates from
  the same batch: `_MakeBlkQueen`/`_MakeRedQueen`, `_SimQueenA`,
  `_BuildAntListA`, `_AddBlackAnts`/`_AddRedAnts`, `_IsItFoodAt`,
  `_DropFoodA`/`_FoodFall`, `_FindAntIndex` (which unblocks `_SFoundAnt`),
  `_UnRecruitRed`/`_RecruitRed` (which unblock `_GetNewRedTask`).
- RECOVERED `sim_queen_a` (`_SimQueenA`, seg6:0A74, NEAR return, arg:
  slot) — stamps the yard queen's caste onto the life grid, and once her
  caste's low 7 bits exceed `0x67`, checks a marker cell one step in her
  facing direction (the SAME encoded-marker relationship
  `_LostHeadA`/`B`/`R` use: tile `== caste - 8`) to decide whether she
  should vanish into the nest — only when the marker does NOT match AND
  no ant is already at that cell.
- Caught the SAME match/no-match branch-polarity trap already fixed once
  this session in `_LostHead*`/`_LostTail*` — traced the jnz/fallthrough
  order twice before writing any code this time, avoiding a repeat bug.
- 4 cases (early-return gate, marker-intact no-vanish, ant-blocks-vanish,
  and the actual vanish) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1415 (+4). Next: the small queen-spawn/ant-list/food
  routines from this round's batch, or `_CreateNewHole` if time allows.

## 2026-07-14 (cont.131) — /goal grind: _RaidInB/R — entering the nest with food, round 2 batch COMPLETE
- RECOVERED `raid_in_b`/`r` (`_RaidInB`/`R`, seg6:3524/5B2A, FAR return,
  args x/y/exclude_direction) — the entry-side twin of `raid_out_b`/`r`,
  composing `try_move_dir_b`/`r` and `get_enter_dir_b`/`r` with no new
  dependencies.
- If the ant's OWN cell is a food-pile tile: nibbles it (same shape as
  `_steal_food`/`_eat_food`), then unconditionally sets `field_c=3` and
  ORs `0x08` into its caste (a "carrying food" bit), re-stamping the
  updated caste on its CURRENT cell — no movement at all on this path.
  Otherwise: tries a move biased by `exclude_direction` via a genuinely
  different roll — `_SRand1(3)` (not `_SRand8`) combined as
  `(roll + exclude - 2) & 7`; if blocked, tries `get_enter_dir` (falling
  back to a fresh `_SRand1(8)` — again `_SRand1`, not the pow2-masked
  `_SRand8`, when it finds nothing); if that's also blocked, gives up on
  moving and sets `field_c=1` (distinct from the food-pile branch's `3`)
  with the caste UNCHANGED.
- 6 cases (all 3 branches x both colonies) — ALL GREEN ON THE FIRST RUN,
  a full end-to-end composition test of the entire routine.
- Suite: simant 1411 (+6). **This closes the entire round-2 survey
  batch** (14 routines: `_CanBeHouseHole`, `_HoleBorder`,
  `_GetFromAlist`, `_PickupFoodB/R`, `_PlaceEggB/R`, `_ScanForAnts`,
  `_MakeNewTailB/R`, `_RaidInB/R`, plus `_RaidOutB/R` from earlier).
  `_DoForageAnt` and the other top-level `_Do*Ant*` behaviors remain
  blocked on `_YellowFight`/`_DoTroph`'s sound/dialog UI chain,
  unchanged. A third survey pass would be needed to find the next
  batch, or this is a natural point to shift focus.

## 2026-07-14 (cont.130) — /goal grind: _MakeNewTailB/R — append a trailing tail segment
- RECOVERED `make_new_tail_b`/`r` (`_MakeNewTailB`/`R`, seg6:424A/66FC,
  FAR return, arg: slot) — composes `add_ant_to_b`/`r_list` with no new
  dependencies; pure DGROUP table math otherwise. Appends a trailing tail
  segment one step BEHIND `slot` (the OPPOSITE of its own facing
  direction, `caste & 7` XOR `4`), with a `caste + 8` "tail" caste,
  `field_c=9`, `field_e=0`.
- `add_list`'s own `(y, x)` argument order once again takes the DX-table
  delta added to the ant's OWN y-field into its `y` slot and the
  DY-table delta added to its OWN x-field into its `x` slot — the SAME
  swapped convention already caught in `_QueenMoveB`/`R` and
  `_PlaceEggB`/`R`, now confirmed a genuine THIRD time across this
  batch, ported as a literal positional pass-through again.
- 4 cases (two caste/position scenarios x both colonies) — ALL GREEN ON
  THE FIRST RUN.
- Suite: simant 1405 (+4). Only `_RaidInB/R` remains from this round's
  batch.

## 2026-07-14 (cont.129) — /goal grind: _ScanForAnts — 3x3 occupancy count
- RECOVERED `scan_for_ants` (`_ScanForAnts`, seg5:5362, FAR return, NO
  args) — a pure double-loop scan, no calls at all. Counts occupied yard
  life-plane cells in the 3x3 block around
  `(dgroup[0xAC7C] >> 4, dgroup[0xAC7E] >> 4)`; out-of-bounds neighbors
  are simply skipped, not treated as occupied.
- 3 cases (nothing occupied, a partial 3-of-9 count, and the
  `base_x == 0` boundary skipping its off-grid west column) — ALL GREEN
  ON THE FIRST RUN.
- Suite: simant 1401 (+3). Remaining from this round's batch:
  `_MakeNewTailB/R`, `_RaidInB/R`.

## 2026-07-14 (cont.128) — /goal grind: _PlaceEggB/R — place a new egg
- RECOVERED `place_egg_b`/`r` (`_PlaceEggB`/`R`, seg5:1004/1068, FAR
  return, args x/y/caste) — composes `dig_tile_b`/`r` and
  `add_ant_to_b`/`r_list` with no new dependencies. No-op if the
  colony's list is already at its 500-slot cap, or `(x, y)` is out of
  bounds (`0 <= x <= 0x3F`, `1 <= y <= 0x3F` — `y` excludes `0`,
  asymmetric with `x`, ported literally). Otherwise: digs the tile,
  appends a new list record, and stamps the caste onto the life plane.
- `add_list`'s own `(y, x)` argument order genuinely takes THIS
  routine's `x` into its `y` slot and vice versa — the SAME coordinate-
  role swap already caught in `_QueenMoveB`/`R`'s ant-list writes,
  ported as a literal positional pass-through again rather than
  "corrected" a second time.
- 6 cases (valid placement, list-at-cap no-op, out-of-range no-op, x2
  colonies) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1398 (+6). Remaining from this round's batch:
  `_MakeNewTailB/R`, `_ScanForAnts`, `_RaidInB/R`.

## 2026-07-14 (cont.127) — /goal grind: _PickupFoodB/R — NEST-map food pickup
- RECOVERED `pickup_food_b`/`r` (`_PickupFoodB`/`R`, seg5:0F40/0FA2, FAR
  return, args x/y) — the NEST-map siblings of `pickup_food_a` (which
  operates on the YARD map). Only callee `_SRand8`. Shape is exactly
  `_try_eat_food`'s gate-and-reroll-or-decrement step (tile must be in
  `[0x10, 0x13]` or the whole call is a no-op) with NO colony-growth
  trigger tail at all — confirmed by tracing the full 98-byte body of
  both, not assumed from the name pairing with `_try_eat_food_b`/`r`.
- 10 cases (2 in-range scenarios + 2 out-of-range no-ops + the count-floor
  guard, x2 colonies) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1392 (+10). Remaining from this round's batch:
  `_PlaceEggB/R`, `_MakeNewTailB/R`, `_ScanForAnts`, `_RaidInB/R`.

## 2026-07-14 (cont.126) — /goal grind: _GetFromAlist — find + remove by colony
- RECOVERED `get_from_a_list` (`_GetFromAlist`, seg5:2FFE, FAR return,
  arg: colony_bit) — searches the yard A-list backward for the last ant
  of a given colony (caste's top bit) and removes it. Only callee: the
  already-recovered `remove_from_a_list`.
- A genuine quirk, ported literally rather than "fixed": the ASM's own
  found/not-found check is just "is the final slot index nonzero", so
  if the search lands on slot `0` — whether because THAT slot matched or
  because the list was exhausted — the function returns `0` (as if
  nothing were found) and removes nothing. Slot 0 can therefore never
  actually be returned as a match, confirmed by a dedicated test case
  seeding a match ONLY at slot 0.
- 4 cases (match at the top slot, the slot-0 quirk, no match at all,
  dead/empty slots correctly skipped mid-search) — ALL GREEN ON THE
  FIRST RUN.
- Suite: simant 1382 (+4). Remaining from this round's batch:
  `_PickupFoodB/R`, `_PlaceEggB/R`, `_MakeNewTailB/R`, `_ScanForAnts`,
  `_RaidInB/R`.

## 2026-07-14 (cont.125) — /goal grind: _CanBeHouseHole + _HoleBorder — new batch, round 2 survey
- Dispatched a THIRD research survey pass (the second candidate list
  closed out in cont.124) — found 14 more zero-blocker routines across
  the hole/list/food/tail/ant/raid families, and re-confirmed
  `_DoForageAnt`/`_DoRecruitAnt` are STILL blocked on exactly
  `_YellowFight`/`_DoTroph` (traced fresh, not cached) with no smaller
  tractable sub-piece inside either — both genuinely bottom out in
  redraw/camera-follow/animation UI. `_DoNestAntB` is a new non-starter:
  an 18-case jump-table dispatch that defeats linear disassembly, a
  materially larger job than anything recovered this session.
- RECOVERED `can_be_house_hole` (`_CanBeHouseHole`, seg5:1CBA, FAR
  return, arg: dy) — a pure constant lookup, NO calls at all: `dy in
  (0, 2, 3)` and `dy in (0x66, 0x68)` each map to a fixed house-hole
  tile ID, `0x5E <= dy < 0x62` maps to `dy + 0x22`, everything else `0`.
- RECOVERED `hole_border` (`_HoleBorder`, seg5:1F8E, FAR return, args
  x/y) — borders a newly-placed hole's 8 compass neighbors, overwriting
  any "soft" (`< 0x50`) tile with a direction-specific border tile.
  **Caught and fixed a real selector mis-read before it reached a
  passing-but-wrong state**: first assumed the border-tile table lived
  at `simant_data_group[0x230C..)` (by visual proximity to the ES-
  prefixed compass-table reads earlier in the same routine), but the
  test failed with the real ASM writing values that didn't match my
  seed at all. Re-checked the SPECIFIC instruction for an `ES:` override
  byte (there wasn't one) and found the table is a genuinely DIRECT
  DGROUP read — and turned out to be the ALREADY-ESTABLISHED
  `HOLE_EDGE_TILES` constant `_MakeNewHoleB`/`R` already use (should
  have grepped for the offset before assuming it was new/unknown).
- 18 cases (16 house-hole lookup boundaries + 2 hole-border scenarios) —
  all green after the fix.
- Suite: simant 1378 (+18). Next: `_GetFromAlist`, `_PickupFoodB/R`,
  `_PlaceEggB/R`, `_MakeNewTailB/R`, `_ScanForAnts`, `_RaidInB/R` remain
  from this round's batch.

## 2026-07-14 (cont.124) — /goal grind: _QueenMoveB/R — queen movement + trail-marker relocation
- RECOVERED `queen_move_b`/`r` (`_QueenMoveB`/`R`, seg6:4154/6606, FAR
  return, args x/y/exclude_direction) — the last two routines from the
  fresh survey's candidate list. Composes `get_best_dir` (the seg6:405E
  copy — confirmed the SAME address/routine as the already-recovered
  pathfinding core, not a distinct duplicate), `try_move_dir_b`/`r`, and
  `find_in_b`/`r_list`.
- Moves the queen one step toward her stored target (`pack`-resident,
  colony-specific fields) via `get_best_dir`, falling back to a random
  direction when already there is impossible (`-1`) or no neighbor
  improves (`-2`); near the yard's top edge (`y < 3`) the chosen
  direction must land in `[3, 5]` ("roughly downward") or the ENTIRE call
  is a no-op — verified this exact 3-way branch structure (not just "any
  restriction near the edge") with a dedicated test.
- On a successful move: clears the OLD trail marker one cell in
  `exclude_direction`'s opposite, then searches for a matching ant
  record there (`find_in_b`/`r_list`) and, if found and still alive,
  relocates it to the queen's OLD position and restamps its caste with a
  transformed direction byte.
- **Independently disassembled BOTH colonies rather than assuming
  symmetry — and caught a genuine, non-obvious asymmetry**: the search
  marker's offset constant (`0x68` for B, `0xE8` for R — consistent with
  this project's established "R fields = B fields + 0x80" pattern) AND
  the final caste transform are DIFFERENT shapes entirely (`direction +
  0x68` for B vs `direction - 0x18` for R), not just a different
  constant plugged into the same formula. Ported both as explicit
  parameters/callables rather than forcing a shared formula that would
  have silently been wrong for one colony.
- 8 cases (already-there no-op, successful-move-no-marker, marker
  relocation exercising the confirmed B/R asymmetry, and the top-edge
  direction restriction — each x2 colonies) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1360 (+8). **This closes the entire fresh-survey
  candidate list.** `_DoForageAnt` and the other top-level `_Do*Ant*`
  behaviors remain blocked on the unrecovered sound-engine/dialog-UI
  chain (`_YellowFight`/`_DoTroph`); a new survey pass is needed to find
  the next batch of tractable routines, or this becomes a natural
  session boundary.

## 2026-07-14 (cont.123) — /goal grind: _RaidOutB/R — move toward an exit or give up in place
- RECOVERED `raid_out_b`/`r` (`_RaidOutB`/`R`, seg6:3610/5D10, FAR return,
  args x/y) — composes FOUR already-recovered routines
  (`get_exit_dir_b`/`r`, `try_move_dir_b`/`r`) with no new dependencies.
- Tries to move the acting ant (`pack[0x9B6A]`) one step toward an exit
  via `get_exit_dir_b`/`r` (or a random direction if none found —
  `exclude=8` is a deliberate "don't exclude any direction" sentinel,
  since `8 ^ 4 = 12` never matches any real 0-7 compass index); if that
  move is blocked, tries ONE more purely random direction; if THAT'S also
  blocked, gives up on moving and instead just re-stamps the acting ant's
  own caste onto its CURRENT cell (a visual/state correction with no
  position change).
- Reused `try_move_dir_b`/`r`'s own established seed helpers/regions
  directly — every dependency (compass tables, exit-distance map, A/B/R-
  list fields) was already fully seeded there, so no new test
  infrastructure was needed at all.
- 4 cases (both colonies x first-attempt-succeeds / both-attempts-fail)
  — ALL GREEN ON THE FIRST RUN, including the composed fallback path.
- Suite: simant 1352 (+4). Only `_QueenMoveB/R` remains from the fresh
  survey's candidate list.

## 2026-07-14 (cont.122) — /goal grind: _PickupFoodA — genuine _DoForageAnt dependency
- RECOVERED `pickup_food_a` (`_PickupFoodA`, seg5:0D18, FAR return, args
  x/y) — a genuine `_DoForageAnt` dependency (chips at that top-level
  routine's blocker list directly, even though `_YellowFight`/`_DoTroph`
  still block the rest of it). Only dependency `_SRand16`.
- Gated on `pack[0x9B6E]` (the SAME "inside the nest" flag `_DeadAntHere`
  reads) — genuinely TWO DIFFERENT tile transforms, not just a colony
  split like every other food routine recovered this session: flag CLEAR
  (outside) rerolls tile `0x48` fresh via `_SRand16`, else plain
  decrements; flag SET (inside) REPLACES a tile that's an exact multiple
  of 4 with `(tile - 0x18) >> 2` (a shrinking transform, no RNG), else
  falls back to the same plain decrement. Finally decrements a
  food-count-ish PACK stat (distinct field from every other food
  routine), floored at exactly `0`.
- 5 cases (both flag states x both tile-shape branches, plus the
  count-floor guard) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1348 (+5). Food family fully closed. Remaining from the
  fresh survey: the larger stretch targets `_RaidOutB/R`/`_QueenMoveB/R`.

## 2026-07-14 (cont.121) — /goal grind: _EatFood*/_TryEatFood* — food-nibble + colony-growth trigger
- RECOVERED all four food-eating routines: `eat_food_b`/`r` (`_EatFoodB`/`R`,
  seg6:4844/6BB6, FAR return) and `try_eat_food_b`/`r` (`_TryEatFoodB`/`R`,
  seg6:47C6/6B38, FAR return), args x/y. `_EatFood*` is UNCONDITIONAL —
  the SAME "nibble a food tile" step `steal_food_*` already recovered
  (reroll on `0x10`, else a byte-wrapping decrement), always followed by
  a colony-growth trigger. `_TryEatFood*` is the SAME body but GATED — a
  complete no-op unless the tile is in `[0x10, 0x13]` (the valid
  food-pile range); `_EatFood*` processes ANY tile value unconditionally,
  a real behavioral difference from its "Try" sibling, not just naming.
- The growth trigger (`_food_growth_trigger`, shared by all four):
  accumulates a per-colony timer (`pack[timer_off] += 5` every call)
  against a threshold derived from two DGROUP ant-count-ish stats
  (`(count1 + count2) >> 4`); once the timer catches up, resets it to `0`
  and bumps a DGROUP counter capped at `100` — the timer reset happens
  BEFORE the cap check, so it resets even on the tick where the cap is
  already maxed (verified this exact ordering with a dedicated test
  case, not just assumed from a first read of the branch).
- 16 cases (4 scenarios x 2 families x both colonies) — ALL GREEN ON THE
  FIRST RUN.
- Suite: simant 1343 (+16). Only `_PickupFoodA` (a genuine `_DoForageAnt`
  dependency) remains in the food family; `_RaidOutB/R`/`_QueenMoveB/R`
  are the larger stretch targets after that.

## 2026-07-14 (cont.120) — /goal grind: _LostHead*/_LostTail* — trail-marker occupancy family
- RECOVERED all five `_Lost*` routines: `lost_head_a` (`_LostHeadA`,
  seg6:0B1E, NEAR return, yard/A-list), `lost_head_b`/`r` (`_LostHeadB`/`R`,
  seg6:42DE/6790, FAR return, nest/B-R-list), `lost_tail_b`/`r`
  (`_LostTailB`/`R`, seg6:433C/67EE, FAR return, nest/B-R-list) — each
  checks whether a trail-head (one step AHEAD in `direction`) or
  trail-tail (one step in the OPPOSITE direction, `direction ^ 4`) marker
  cell still holds its expected encoded tile value; if so, trusts it
  without consulting the ant list; if the tile has changed, falls back to
  an actual `find_in_a/b/r_list` search to determine whether an ant is
  still physically there.
- **Caught and fixed a genuine bug in my own first-pass transcription**
  before committing: I had the match/no-match branch inverted (assumed
  "tile matches marker → check the list", when the real ASM is
  "tile matches marker → trust it, return `0` immediately; only a
  MISMATCH falls through to the list search"). Caught by writing the
  state-diff tests BEFORE trusting my own pseudocode read, running them,
  and getting two failures that pointed straight at the branch — then
  confirming with a standalone instrumented run of the real ASM before
  touching the fix, exactly the discipline this project has used all
  session for every prior branch-polarity mistake.
- 15 cases (3 scenarios x head-A, head-B/R, tail-B/R) — all green after
  the fix, confirming the corrected branch against the real ASM in every
  scenario (fast-path match, list-search hit, list-search miss).
- Suite: simant 1327 (+15). This closes the entire 5-routine `_Lost*`
  family from the fresh survey. Next: the food family (`_EatFoodB/R`,
  `_TryEatFoodB/R`, `_PickupFoodA`) or the larger stretch targets
  (`_RaidOutB/R`, `_QueenMoveB/R`).

## 2026-07-14 (cont.119) — /goal grind: _SimEggA — yard egg tick
- RECOVERED `sim_egg_a` (`_SimEggA`, seg6:0A1C, NEAR return, arg: slot) —
  a tiny (88-byte) routine, only dependency `_SRand1`. Always stamps the
  egg's caste onto the yard life grid at its own recorded position (even
  on a tick where nothing else happens — a redundant-looking but faithful
  re-stamp), then rolls `_SRand1(200)`; only on an exact `0` does it clear
  the egg's caste field and its life-grid cell (hatched or died, either
  way gone).
- 2 cases (roll misses, roll hits) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1312 (+2).

## 2026-07-14 (cont.118) — /goal grind: _StealFoodB/R — new candidate batch after a fresh survey pass
- The prior survey's zero-blocker candidate list is now fully closed
  (cont.117). Dispatched a fresh research pass (Explore agent) to find
  the next batch — confirmed `_DoForageAnt` is STILL genuinely blocked
  (re-traced all 19 callees; the only unrecovered ones are `_PickupFoodA`,
  `_YellowFight`, and `SIMANT!_DoTroph`, the latter two on the same sound/
  dialog/camera-follow UI chain flagged since early this session) and
  surfaced a new ranked list: `_StealFoodB/R`, `_SimEggA`, a 5-routine
  "Lost*" tier (`_LostHeadA/B/R`, `_LostTailB/R`), a food family
  (`_EatFoodB/R`/`_TryEatFoodB/R`), `_PickupFoodA` itself (a genuine
  `_DoForageAnt` dependency), and larger stretch targets
  (`_RaidOutB/R`, `_QueenMoveB/R`).
- RECOVERED `steal_food_b`/`r` (`_StealFoodB`/`R`, seg6:48B4/6C26, FAR
  return, args x/y) — the smallest of the new batch (68 bytes each), only
  callee `_SRand8`. An ant nibbling stored food at `(x, y)` on the
  colony's nest map: if the cell is exactly the "full pile" tile
  (`0x10`), rerolls it fresh via `_SRand8`; otherwise decrements the tile
  by one — a genuine byte-wrapping `dec` with NO underflow guard (`0x00`
  wraps to `0xFF`), ported faithfully. Also decrements the colony's
  food-count stat, but only while it's still positive (floors at exactly
  `0`, doesn't wrap).
- 8 cases (4 scenarios x both colonies) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1310 (+8). `_SimEggA` (seg6:0A1C, 88 bytes, single
  `_SRand1` dependency) is a good next small target.

## 2026-07-14 (cont.117) — /goal grind: _GetExitDir*/_GetEnterDir* — nest tunneling direction family
- RECOVERED all four remaining zero-blocker `_Get*Dir`-style routines from
  the original survey, closing that list entirely: `get_exit_dir_b`/`r`
  (`_GetExitDirB`/`R`, seg5:119C/1240) and `get_enter_dir_b`/`r`
  (`_GetEnterDirB`/`R`, seg5:12E4/137C), each FAR return, args x/y/exclude.
  Both pairs are byte-identical B/R twins (confirmed by independently
  disassembling BOTH of each pair, not assuming symmetry from the name) —
  ported as one shared `_get_exit_dir`/`_get_enter_dir` body parametrized
  by map/exit-map base, mirroring this project's established B/R-twin
  pattern (`_dig_tile_reroll_and_track`, `_jam_scent`, `_compact_list`).
  No new dependencies: the exit-distance arrays (`[0x3A4..)` black,
  `[0x13A4..)` red) are the SAME ones `fix_exit_map_b`/`r` already
  maintain.
- `get_exit_dir_*`: at `y == 1` (the tunnel row), a fast path — an exact
  `0x18` tile at `(x, 0)` on the nest map returns `1` outright; otherwise
  a coin-flip (`_SRand2`) picks between `3`/`7` without consulting the
  exit-distance map at all. Elsewhere: scans the 8 neighbors for the
  HIGHEST exit-distance (heading toward an exit), biased away from
  `exclude`'s opposite (`exclude ^ 4`), returns 1-8 or `0` for none found.
- `get_enter_dir_*`: the inverse search — heads toward the LOWEST
  exit-distance (deeper into the nest), starting from the ant's own cell
  value and updating as better neighbors are found (not just competing
  against the starting cell); a neighbor of exactly `0` is never a valid
  target (unlike `_get_exit_dir`, where `0` is just "no signal yet");
  ties are broken by a coin-flip; returns 0-7 or `-1` for none found.
- 24 cases (12 per family, doubled across both colonies via a
  `colony`-parametrized fixture) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1302 (+24). This closes the ENTIRE zero-blocker candidate
  list from the original research survey — every small, self-contained
  routine it flagged as reachable is now recovered. What remains needs
  either fresh scoping (a new survey pass) or tackling a genuinely
  top-level `_Do*Ant*` behavior routine directly (`_DoForageAnt`,
  `_DoNestAntB/R`, `_DoAntSimA/B`), most of which still terminate in the
  unrecovered sound-engine/dialog-UI dependency chain.

## 2026-07-14 (cont.116) — /goal grind: _RandTurn — unconditional random caste-mode direction
- RECOVERED `rand_turn` (`_RandTurn`, seg6:2A22, NEAR return, arg:
  caste_low3) — a tiny (30-byte) routine, byte-identical to the random-
  fallback tail every seg7 `_Get*Dir` routine shares
  (`simant_data_group[0x24 + roll + (caste_low3 << 3)]` after a fresh
  `_SRand8()`), minus the `_Bounce` edge check those all have — this one
  is unconditional, no position argument at all.
- 3 cases — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1278 (+3). Remaining zero-blocker candidates:
  `_GetExitDirB/R`, `_GetEnterDirB/R` (seg5).

## 2026-07-14 (cont.115) — /goal grind: _GoInNest — move a yard ant into the nest
- RECOVERED `go_in_nest` (`_GoInNest`, seg6:257A, NEAR return, args
  x/y/slot, plus `pack`) — composes FIVE already-recovered routines
  (`compact_list_b/r`, `add_ant_to_b_list/r_list`, `dig_tile_b/r`) with no
  new dependencies at all. `x < 0x40` picks black, `x >= 0x40` picks red
  (the yard is split down the middle at the map's x-midpoint).
  Independently disassembled the full 240-byte body and caught my own
  transposed x/y role guess mid-derivation (a life-plane index write's
  shifted-vs-unshifted operand order briefly looked backwards against an
  earlier assumption) — resolved by cross-checking against the SAME two
  values' roles in the `add_ant_to_b_list` call site's OWN established
  argument order, not by re-guessing.
- Compacts the target colony's list first if it's at its 500-slot cap; if
  it's STILL full afterward, the ant stays exactly where it is — no
  further effect at all, not even the final vanish (this only shows up
  by tracing exactly where a `jmp` lands, past several instructions that
  looked at first glance like they'd always run). Otherwise appends a new
  nest-list record (copying `field_c`/`field_e` and a `+4`-bumped caste)
  at a fixed nest-entrance column with `y` as the row, optionally digs
  that entrance tile if its exit-distance map cell is nonzero (the SAME
  arrays `_FillHolesBN`/`RN` maintain), then — regardless of which branch
  ran — clears the ant's own A-list caste and its yard life-grid cell.
- 3 cases (black/red colony success, and the still-full-after-compaction
  no-op) — ALL GREEN ON THE FIRST RUN (deliberately didn't exercise the
  dig-tile sub-path here; `dig_tile_b`/`r` already have their own
  dedicated, thorough tests, and duplicating their large region/seed
  surface here would add little).
- Suite: simant 1275 (+3).

## 2026-07-14 (cont.114) — /goal grind: _StartFightA — initiate yard combat
- RECOVERED `start_fight_a` (`_StartFightA`, seg6:266A, NEAR return, args
  slot1/x1/y1/x2/y2, plus `pack`) — composes three already-recovered
  routines (`find_in_a_list`, `get_winner`, `alarm_here2`) with no new
  dependencies at all.
- UNCONDITIONALLY, before even searching for a target: clears the
  attacker's own caste field and its yard life-grid cell — it "vanishes"
  whether or not a fight actually resolves (a detail that only shows up
  by tracing instruction order, not by reading the high-level control
  flow). Searches the A-list for an ant at `(x2, y2)`; if none found,
  that's the entire effect. Otherwise resolves the matchup via
  `get_winner(arg_a=defender's caste, arg_b=attacker's caste)` — verified
  the exact push-order argument mapping (the caller's own local variable
  order doesn't match the callee's positional argument order 1:1, easy to
  get backwards) — and stamps the DEFENDER's slot (not the winner's) with
  a "defeated" caste, `field_c=10`, and `field_e=winner`, then bumps the
  ALARM grid there by 40. Notably, this "defeated" stamp lands on the
  defender's slot regardless of which side actually won the roll —
  confirmed intentional (not a misread) by testing both outcomes and
  matching the real ASM byte-for-byte in each case.
- 4 cases (no target found; the cheat-gate path; both real-calculation
  win/lose outcomes) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1272 (+4).

## 2026-07-14 (cont.113) — /goal grind: _GetWinner — one-on-one combat matchup resolution
- RECOVERED `get_winner` (`_GetWinner`, seg6:26F4, NEAR return, args
  arg_a/arg_b, plus `pack`) — found while investigating `_StartFightA`
  (seg6:266A)'s own dependencies. Independently disassembled the full
  242-byte body — first correcting my own size estimate (an earlier
  symbol-table scan had computed `_GetWinner`'s span assuming the next
  NAMED symbol, `_RandTurn`, was adjacent, missing that the already-
  recovered `_DoFightA` sits un-filtered in between; `_GetWinner`'s real
  span is 0x26F4-0x27E6, not the ~800 bytes first assumed).
- A test/cheat gate first: if `simant_data_group[0x8A5C] == 1`, skips the
  real calculation and returns whichever side does NOT have colony bit
  `0x80` set (bumping one win-count stat). Otherwise: looks each side's
  "sub" up in a per-caste strength table (`dgroup[0x8902 + sub]`, a
  genuinely DIRECT DGROUP read with no pointer-global indirection —
  notably different from every other table lookup this project has
  recovered), combines them into an outcome-probability table
  (`dgroup[0x8918 + strength_a*4 + strength_b]`), and rolls `_RRand(10)`
  (the C-runtime generator via `RAND_STATE_OFF`, NOT the `_SRand*` LFSR)
  against it. Bumps a colony-keyed pair of PACK win-count stats for
  whichever side wins — the two win-paths in the ASM are byte-identical
  except which side's colony bit gates them, ported as one shared tail.
- 4 cases (both cheat-gate branches, both real-calculation outcomes) —
  ALL GREEN ON THE FIRST RUN.
- Suite: simant 1268 (+4). `_StartFightA` (its only remaining dependency
  before it can be completed) is next.

## 2026-07-14 (cont.112) — /goal grind: _GetRedDefendDir — seg7 `_Get*Dir` family COMPLETE
- RECOVERED `get_red_defend_dir` (`_GetRedDefendDir`, seg7:1194, FAR
  return, args x/y/caste_low3, plus `pack`) — the sixth and last of the
  seg7 `_Get*Dir` family. The red-colony-specific sibling of
  `get_defend_dir`: same overall shape (yard-edge `_Bounce`, mode 2/3
  delegate to `get_nest_dir`, other modes echo `caste_low3`), but the mode
  selector comes from `pack[0x7606]` (not `dgroup[0xCE80]`) and mode 1's
  target/threshold are different PACK fields (`[0x80A6]`/`[0x80AC]`/
  `[0xA08E]`) with NO `pack[0x72EC]`-style attack-marker alternative —
  mode 1 here is always the distance-gated geometric branch. Independently
  disassembled the full 344-byte body and verified all 4 new selectors
  (all PACK) before writing any code.
- 7 cases (edge, both mode-2/3 delegations, both other-mode no-ops, and
  both mode-1 distance-threshold branches) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1264 (+7). **The entire six-routine seg7 `_Get*Dir`
  family is now recovered**: `_GetForageDir`, `_GetNestDir`,
  `_GetAlarmDir`, `_GetRandDir`, `_GetDefendDir`, `_GetRedDefendDir` — all
  zero-blocker, all byte-exact on the first test run once written. None
  have a caller in this repo yet (`_DoForageAnt`/`_DoNestAntB`/combat
  orchestration remain unrecovered) — this closes out an entire
  self-contained "direction picker" tier the way the pathfinding-
  selection and dig-subsystem tiers closed out earlier in the project,
  ready for whichever top-level behavior routine gets picked up next.

## 2026-07-14 (cont.111) — /goal grind: _GetDefendDir — game-mode-switched defend direction
- RECOVERED `get_defend_dir` (`_GetDefendDir`, seg7:1026, FAR return, args
  x/y/caste_low3, plus `pack`) — fifth of the seg7 `_Get*Dir` family, and
  the first whose behavior is entirely gated on a global game-mode selector
  (`dgroup[0xCE80]`), not just colony/scent state. Independently
  disassembled the full 366-byte body.
- Yard-edge handling: `_Bounce`'s formula compiled inline (as in
  `_GetNestDir`/`_GetAlarmDir`/`_GetRandDir`), checked BEFORE the mode
  dispatch — an on-edge ant returns via that path regardless of mode.
- Mode 2/3: delegate WHOLESALE to `_GetNestDir` (colony B/R respectively)
  via a near-call to its own address in the original ASM — this project's
  established near-call-to-far-retf bridge pattern, ported as an ordinary
  call to the already-recovered `get_nest_dir` (which redundantly re-runs
  its own edge check on the now-known-interior position — harmless, since
  interior `_Bounce` costs no RNG, and faithfully mirrors what the ASM
  itself does). Any OTHER mode (not 1/2/3) is a no-op that echoes
  `caste_low3` back as if it were already a direction — presumably dead
  code, unreachable during normal play.
- Mode 1: if `pack[0x72EC] == 1`, steers via the already-recovered `get_dir`
  toward a DGROUP-resident attack marker (`dgroup[0xAC7C]`/`[0xAC7E]`,
  each `>> 4` — a finer coordinate system scaled down to the map grid).
  Otherwise, checks the squared distance (`get_dis`, truncated to a
  SIGNED word exactly as the ASM's own `mov si,ax` does) from the ant to a
  PACK-resident target (`pack[0x9FE4]`/`[0x9FEA]`) against half of
  `pack[0x9E7A]`: close enough picks a `_SRand1(8)`-random direction
  (genuinely random, unlike the other family members' `_SRand8()`); too
  far calls `get_dir` toward that same target directly — no RNG on that
  path, an asymmetry preserved exactly.
- 8 cases (edge, both mode-2/3 delegations, both other-mode no-ops, the
  mode-1 attack-marker path, and both mode-1 distance-threshold branches)
  — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1257 (+8). Five of six seg7 `_Get*Dir` routines now
  recovered; only `_GetRedDefendDir` remains.

## 2026-07-14 (cont.110) — /goal grind: _GetRandDir — purely random direction
- RECOVERED `get_rand_dir` (`_GetRandDir`, seg7:0F72, FAR return, args
  x/y/caste_low3) — fourth of the seg7 `_Get*Dir` family, and the simplest
  yet: no gradient-following at all. Just the shared `_Bounce`-inline
  yard-edge handling, or (strictly interior) a fresh `_SRand8()`-random
  mode-table pick — byte-identical to the random-fallback tail every other
  family member shares.
- 3 cases (a corner, two interior random-pick scenarios) — ALL GREEN ON
  THE FIRST RUN.
- Suite: simant 1249 (+3). Four of six seg7 `_Get*Dir` routines now
  recovered; remaining: `_GetDefendDir`, `_GetRedDefendDir`.

## 2026-07-14 (cont.109) — /goal grind: _GetAlarmDir — ALARM-scent gradient direction
- RECOVERED `get_alarm_dir` (`_GetAlarmDir`, seg7:0E54, FAR return, args
  x/y/caste_low3) — third of the seg7 `_Get*Dir` family, and the simplest
  so far: no colony argument at all — the ALARM scent grid
  (`simant_data_group[0x52D2..)`) is shared by both colonies. Independently
  disassembled the full 286-byte body.
- Yard-edge handling: `_Bounce`'s formula compiled inline again (same as
  `_GetNestDir`), ported as a `bounce()` call plus the `(r-1)&7`
  conversion. Interior: scans the 8 compass neighbors for the highest
  ALARM value (never checks the ant's own cell, unlike `_GetForageDir`;
  ties keep the lowest index, no random tie-break seed, like
  `_GetNestDir`) — falls back to a fresh `_SRand8()`-random mode-table
  pick only when every neighbor is exactly zero.
- 3 cases (an edge/corner, gradient-follow, all-neighbors-zero fallback)
  — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1246 (+3). Three of the six-routine seg7 `_Get*Dir` family
  now recovered (`_GetForageDir`/`_GetNestDir`/`_GetAlarmDir`); remaining:
  `_GetRandDir`, `_GetDefendDir`, `_GetRedDefendDir`.

## 2026-07-14 (cont.108) — /goal grind: _GetNestDir — NEST-scent gradient / queen-homing
- RECOVERED `get_nest_dir` (`_GetNestDir`, seg7:0C30, FAR return, args
  x/y/caste_low3/colony_flag) — second of the seg7 `_Get*Dir` family, and
  meaningfully more involved than `_GetForageDir`. Independently
  disassembled the full 548-byte body.
- Yard-edge handling here is genuinely `_Bounce`'s OWN formula, compiled
  INLINE (not a call) — confirmed byte-identical offset-per-edge/corner to
  `bounce()`, so ported as a literal `bounce()` call plus the `(r-1)&7`
  conversion `_DoDigOutAntA` already applies to its own `_Bounce` result
  (code reuse the ASM itself didn't have available to it).
- Interior: if the ant's own NEST-grid cell has ANY scent, scans its 8
  neighbors for the best gradient direction (same shape as
  `_GetForageDir`, but no random tie-break seed) — and, surprisingly,
  rolls a `_SRand2()` whose VALUE never affects the outcome at all: both
  of its branches independently compute and return the identical
  mode-table read. Verified this wasn't a misread by testing the SAME
  scenario at two different seeds landing on `_SRand2()==0` and `==1`
  respectively — both matched the real ASM byte-for-byte, confirming it's
  a genuine dead-code artifact of the original compile (the roll still
  has to be reproduced for its LFSR-advancing side effect, or later
  chained `_SRand*` calls in the same tick would desync).
- If the own cell has NO scent, skips the neighbor scan and instead calls
  the already-recovered `get_dir` toward the colony's stored queen/nest-
  entrance target (`simant_data_group` words at `[0x835E]`/`[0x8360]` red,
  `[0x835A]`/`[0x835C]` black) — on `get_dir`'s result of `0` or a failed
  `_SRand4()` roll, falls back to a fresh `_SRand8()`-random mode-table
  pick, matching `_GetForageDir`'s fallback shape. Noted that the ASM
  indexes the mode table through a `[0x23 + ...]` base with `get_dir`'s
  native `1..8` result rather than `[0x24 + ...]` with a `-1` adjustment —
  byte-address-identical, ported as the latter for consistency with the
  rest of this module.
- 7 cases (an edge/corner, the gradient path at both `_SRand2` outcomes,
  the homing path for both colonies, the `_SRand4`-fails fallback, and the
  `get_dir==0` fallback) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1243 (+7).

## 2026-07-14 (cont.107) — /goal grind: _GetForageDir — TRAIL-scent gradient direction
- RECOVERED `get_forage_dir` (`_GetForageDir`, seg7:0AB0, FAR return, args
  x/y/caste_low3/colony_flag) — first of the seg7 `_Get*Dir` family.
  Independently disassembled the full 384-byte body; its yard-edge handling
  superficially resembles `_Bounce` but is a genuinely DIFFERENT, simpler
  scheme (confirmed by tracing every branch, not assumed from the visual
  similarity): all four corners return a FIXED constant with NO RNG call at
  all (1/3/5/7), the left/top/right general edges roll `_SRand1(3)` plus the
  adjacent corner's offset, and the general BOTTOM edge alone uses a
  different transform, `(_SRand1(3) - 1) & 7`, not `+7`.
- Strictly interior: scans the 8 compass neighbors of the half-res
  `(x>>1, y>>1)` cell on the colony's TRAIL scent grid (the SAME grids
  `jam_scent_bt`/`rt` write, confirmed via the same selector-verification
  discipline as every prior slice) for the highest-scent direction, falling
  back to the `_DoDigOutAntA`-style `caste_low3`-indexed mode table when no
  neighbor beats zero, or returning a `-1` sentinel when the ant's own cell
  already out-scents every neighbor.
- Pure aside from the SRand seed (like `_Bounce`): tested with the same
  fresh-view-seeded-PRE-state pattern, extended to a 2-segment (DGROUP +
  SDG) version since this routine ALSO reads SDG — SDG is untouched by the
  routine itself, so its post-execution state was safe to reuse directly
  for the recovered call (no separate "before" SDG snapshot needed, unlike
  the seed word).
- 8 cases (2 corners, general-left-edge, general-bottom-edge's special
  formula, gradient-found for both colonies, own-cell-already-best, and
  no-gradient-anywhere) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1236 (+8).

## 2026-07-14 (cont.106) — /goal grind: _GetNewModeB / _GetNewModeR — thin per-colony wrappers
- RECOVERED `get_new_mode_b`/`get_new_mode_r` (`_GetNewModeB`/`_GetNewModeR`,
  seg7:09D0/0A50, FAR return, arg: sub). Found while scoping the seg7
  `_Get*Dir` family (this round's next planned target) — both sit right
  after `_GetNewMode` in the symbol table and are small (128/96 bytes).
  Independent disassembly showed each is byte-for-byte identical control
  flow to ONE of `_GetNewMode`'s two top-level branches: `_GetNewModeB` is
  `_GetNewMode`'s `full_byte & 0x80 == 0` branch (the `pack[0x9FCE]`-gated
  one, mode base `pack[0x9B8A]`) with no `full_byte` input at all;
  `_GetNewModeR` is the `full_byte & 0x80` branch (ungated, mode base
  `pack[0x7690]`) — confirmed by tracing every selector/table-base pair,
  not assumed from the name pairing alone. Implemented as one-line wrappers
  around the already-recovered `get_new_mode` (`full_byte=0` / `0x80`).
- Proved against the REAL ASM, not just against `get_new_mode` (which would
  be circular): reused `get_new_mode`'s own state-diff regions/seed helper.
  8 cases (every branch each routine can reach) — ALL GREEN ON THE FIRST
  RUN, confirming the wrapper hypothesis exactly.
- Suite: simant 1228 (+8).

## 2026-07-14 (cont.105) — /goal grind: _DoDigOutAntA — second TOP-LEVEL `_Do*Ant*` routine
- RECOVERED `do_dig_out_ant_a` (`_DoDigOutAntA`, seg6:1480, NEAR call/return,
  arg: `slot`) — a yard ant's per-tick "dig out" resolution: aging/mode
  transition, or a move (with a natural-decay kill chance, distinct from
  `_DoFightA`'s combat kill) toward a `_Bounce`-biased or mode-table-random
  direction. Independently disassembled the full 502-byte body; a careful
  re-read of the bp-relative locals caught my own first-pass mis-transcription
  (I'd initially inverted which of the `sub==5`/`sub==9` branches uses the
  computed `new_x`/`new_y` — fixed by re-tracing every `[bp-N]` write/read in
  address order rather than trusting the first skim) before any code was
  written, so no wasted test iteration.
- The routine's only two dependencies the earlier research survey hadn't
  named by symbol were two NEAR calls (seg6:0x94F6/0x94B6) reached solely on
  the "successful move AND carrying dirt (`field_e`!=0)" path — resolved via
  `symbols_in_segment` to `_JamScentRN`/`_JamScentBN`, ALREADY recovered in
  an earlier round (`gameplay.py`'s scent/pheromone tier). So the survey's
  "zero-blocker" call stands; these just weren't visible from a call-graph
  summary alone.
  `_Bounce` itself was the other dependency, recovered last round (cont.104).
  Independently verified the mode-table's real byte contents (`simant_data_
  group[0x24..0x63]`, values 0-7) and the two 8-entry compass delta tables at
  `simant_data_group[0..7]`/`[8..15]` (the canonical N/NE/E/SE/S/SW/W/NW
  dx/dy pairs) directly from a fresh machine, rather than assuming their
  shape from the disassembly's `cbw` sign-extend alone.
- 9 state-diff cases (early mode-transition; natural-decay kill; terrain-
  blocked retry; occupant-blocked retry; successful move with no cargo;
  successful move + `_JamScentBN`; successful move + `_JamScentRN`; the
  `sub==9` twin of the `sub==5` movement path; and a yard-corner case that
  exercises `_Bounce` overriding the mode-table direction) — ALL GREEN ON
  THE FIRST RUN. Seeded every one of the 8 possible destination cells around
  the ant with the same override value rather than hand-computing which
  index the seeded RNG sequence would land on — robust to *which* direction
  gets picked, only to *whether* a move happens.
- Suite: simant 1220 (+9). **Second top-level `_Do*Ant*` routine recovered**
  (after `_DoFightA`, cont.103). Both of this round's top-level routines
  compose entirely from already-recovered supporting tiers — no new gaps
  opened. Next candidates per the survey, all zero-blocker: the rest of the
  seg7 `_Get*Dir` family (`_GetForageDir`/`_GetNestDir`/`_GetAlarmDir`/
  `_GetRandDir`/`_GetDefendDir`/`_GetRedDefendDir`); `_GetWinner`,
  `_RandTurn`, `_GetExitDirB/R`, `_GetEnterDirB/R`, `_GoInNest`,
  `_StartFightA`. `_DoForageAnt` remains a maybe (needs its own `_DoTroph`/
  `_YellowFight` gate-shape re-verified end-to-end, same pattern as
  `try_move_dir_b`'s trophallaxis gate).

## 2026-07-14 (cont.104) — /goal grind: _Bounce — yard-edge compass, unblocks _DoDigOutAntA
- RECOVERED `bounce` (`_Bounce`, seg7:12EC, FAR call/return, args x=[bp+6]/
  y=[bp+8]) — picks a "bounce back into the map" compass value for an ant at
  the yard edge (x in `0..0x7F`, y in `0..0x3F`, the same axes
  `LIFE_PLANE_BASE[0] + (x << 6) + y` indexes), or `0` for a strictly
  interior position. Eight cases (four edges, four corners) each roll
  `_SRand1(3)` (corners) or `_SRand1(5)` (edges) plus a per-edge offset
  (left=1/top=3/right=5/bottom=7). Independently disassembled the full
  140-byte body (found while investigating `_DoDigOutAntA`'s only unrecovered
  dependency, a far call to seg7:0x12EC that symbol lookup identified as
  `_Bounce` — part of the seg7 `_Get*Dir` family the research survey had
  already flagged as fully unblocked) and hand-traced its compiler-shared
  tail-jump structure (several corner/edge branches jump into a SHARED
  `roll+offset;return` tail rather than duplicating it — a pattern not seen
  elsewhere in this project's recovered code so far).
- Pure(ish): its only mutation is the SRand LFSR seed, so its test needed a
  variant of the return-value oracle — `_run_and_get_ax`'s own machine is
  POST-execution (past the ASM's own `_SRand1` call), so the recovered call
  can't reuse it the way `find_in_a_list`'s test does; built a fresh
  `ByteBackend` seeded with just the PRE-state seed instead, and checked
  both the returned AX and the post-call seed word.
- 12 cases (every edge/corner + 3 interior positions, incl. one interior
  point immediately diagonal to a corner) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1211 (+12). This was the last missing piece for
  `_DoDigOutAntA` (seg6:1480) — a 502-byte top-level `_Do*Ant*` routine that
  composes `_Bounce`, the already-recovered `_GetNewMode`, and two small
  SDG-resident compass delta tables; next up.

## 2026-07-14 (cont.103) — /goal grind: _GetNewMode + _DoFightA — first TOP-LEVEL `_Do*Ant*` routine
- A research survey (Explore agent) found the entire seg7 `_GetNewMode*`
  family and `_DoFightA` (seg6:27E6) fully unblocked — every callee already
  recovered or zero-blocker — correcting cont.84's stale claim that this
  family was blocked. `_DoFightA` is the recommended target: a genuine
  top-level combat-resolution routine, one call-hop below `_DoAntSimA`.
- RECOVERED `get_new_mode` (`_GetNewMode`, seg7:0910, FAR return, args
  sub/full_byte) — a caste mode-transition lookup with three tiers: if
  `full_byte & 0x80`, an `_SRand1(7)`-plus-caste-derived-index lookup into
  one of two 8-row tables (`sub==2`/`sub==6`) or a direct table read
  otherwise; else gated on `pack[0x9FCE]` between a second pack-resident
  mode-base (`[0x9B8A]`) feeding the SAME rolled-lookup tables, or a flat
  word constant (`[0x8A58]`) for `sub in (2, 6)`. Verified all 7 DGROUP
  pointer-global selectors independently (`0xC4C4`→PACK, `0xC4DA`→SDG,
  `0xC4E0`/`0xC4E2`→PACK, `0xC4DC`/`0xC4DE`/`0xC4CC`→SDG) rather than
  trusting the survey's callee-list-only summary. 9 state-diff cases
  (every branch combination) — ALL GREEN ON THE FIRST RUN.
- RECOVERED `do_fight_a` (`_DoFightA`, seg6:27E6, NEAR call/return, arg:
  `slot` — the A-list index of the yard ant being resolved). Always
  rerolls the ant's caste low 3 bits via `_SRand1(7)` and stamps the
  result into the yard life grid; then rolls `_SRand16()` — on a 1-in-16
  hit, resolves a KILL: overwrites the life-grid cell and caste field with
  the ant's `field_e`, computes a new mode via the just-recovered
  `get_new_mode(sub=(field_e & 0x78) >> 3, full_byte=field_e)`, writes it
  into the ACTING ant's (`pack[0x9B6A]`, NOT this ant's) `field_c`, clears
  this ant's `field_e`, and calls the already-recovered `dead_ant_here`.
  On any other roll, the ASM conditionally calls `ANTEDIT!_FightBalloons`
  (a speech-balloon UI routine gated on `simant_data_group[0x85FC]`) — a
  pure presentation side effect with no simulation feedback, deliberately
  OMITTED (not stubbed-and-called, just never invoked) per this project's
  core/presentation split, matching the existing `_ZapEuMapAt`-style
  redraw-stub convention. Caught and corrected a stale research-survey
  guess in passing: selector `0xC322` (the `[0x85FC]` gate) resolves to
  SDG, not PACK as the survey had assumed — verified independently via
  `m.mem.rw(dg, 0xC322)` against both segment bases on a fresh machine.
  4 state-diff cases (no-kill early-return; kill via the direct-table
  sub; kill via each rolled-table sub, one per mode-base gate state) —
  ALL GREEN ON THE FIRST RUN. Added a `stubs=` parameter to
  `_run_and_diff_segs` (mirroring the existing `_run_and_diff` one) to
  neutralize the `_FightBalloons` far call cleanly.
- Suite: simant 1199 (+13: 9 + 4). **`_DoFightA` is the first genuinely
  TOP-LEVEL `_Do*Ant*`-tier routine recovered** — previous rounds only
  closed supporting tiers (pathfinding selection, dig subsystem, movement
  execution). Next candidates per the survey, all zero-blocker: the rest
  of the seg7 `_GetNewMode*`/`_Bounce`/`_Get*Dir` family; `_GetWinner`,
  `_RandTurn`, `_GetExitDirB/R`, `_GetEnterDirB/R`, `_GoInNest`,
  `_StartFightA`; `_DoDigOutAntA` (seg6:1480, fully disassembled by the
  survey, zero-blocker). `_DoForageAnt` remains a maybe (needs its own
  `_DoTroph`/`_YellowFight` gate-shape re-verified end-to-end); the other
  `_Do*Ant*` routines (`_DoDigInB`, `_DoNestAntB`, `_DoAntSimA/B`) stay
  blocked on a real sound-engine routine and/or a dialog/camera-follow UI
  subsystem.

## 2026-07-14 (cont.102) — /goal grind: _TryMoveDirB <-> _GetOutB — movement EXECUTION DONE
- RECOVERED `try_move_dir_b`/`get_out_b` (seg6:439E/520A; both FAR return)
  — the black-colony twins, closing the movement-EXECUTION tier for BOTH
  colonies. Independently re-disassembled `_TryMoveDirB`'s full body
  (not trusting the pre-cont.101 research survey, since it predated
  discovering the AL-clobber bug pattern) and confirmed the SAME field
  layout as `_TryMoveDirR` (`[0x3736]`=new_x, `[0x392C]`=new_y,
  `[0x3D18]`=dir-encoded caste byte) and the SAME `mov ax,si` clobber
  right before the position writes — ported correctly on the first pass
  by applying cont.101's lesson up front instead of re-deriving it.
- `_TryMoveDirB` has exactly ONE extra branch its red twin lacks:
  trophallaxis (food-sharing) with a blocking ant, reached only when the
  destination LIFE cell is exactly `0xFF` (empty) AND `pack[0x9AF2]` (the
  `_SetMyHealth` "not-healing" flag) is nonzero AND
  `simant_data_group[slot+0x3736] < 0x80` — that field is genuinely
  dual-purpose (a status-ish byte read here, BEFORE this same routine
  later overwrites it with the new-position X coordinate on ANY completed
  move) — this is now independently confirmed the field really is
  overloaded, not evidence of an earlier mislabeling. Since the gate's
  body calls the unrecovered `SIMANT!_DoTroph` (whose own chain bottoms
  out in a real sound-engine routine and a dialog/busy-wait UI routine —
  materially more work than this whole session), the port computes the
  gate condition exactly and, if it WOULD fire, raises
  `NotImplementedError` with the call's arguments — per this project's
  "fail loud, never fake" rule. A dedicated test confirms the gate raises
  under the exact seeded conditions that would trigger it in the ASM, and
  every OTHER move outcome (including a plain successful move with the
  gate correctly NOT firing) is fully byte-exact.
- `_GetOutB` is byte-for-byte the same shape as `_GetOutR` (own
  disassembly confirmed, not assumed) — same inverted `hole_x == 0`
  trigger condition for `make_new_hole_b` that cont.101 caught on the red
  side, applied correctly here without re-discovering it.
- 10 scenarios (mirroring `_TryMoveDirR`/`_GetOutR`'s coverage, plus the
  dedicated trophallaxis-gate-raises test) — ALL GREEN ON THE FIRST RUN.
- Suite: simant 1186 (+10). **The movement-EXECUTION tier is now fully
  recovered for both colonies** (modulo the documented, fail-loud
  trophallaxis gap). Combined with the movement-SELECTION tier
  (`_TileCanBeMovedOn`/`_GetMyBestDirs`/`_GetRedBestDirs`/etc.) and the
  full dig subsystem, SimAnt's entire ant-MOVEMENT pipeline — pick a
  direction, then actually execute the step, digging through the nest as
  needed — is byte-exact from seg5/seg6, a substantial fraction of the
  per-ant behavior tier's real dependencies. Continuing per /goal — next
  candidates: a fresh survey of what's left in seg5/seg6 now that
  movement is fully closed (likely combat `_YellowFight`/`_GetWinner`,
  or the `_GetNewMode*` family in seg7, or attempting a real `_Do*Ant*`
  top-level behavior routine now that so much of its dependency graph
  exists).

## 2026-07-14 (cont.101) — /goal grind: _TryMoveDirR <-> _GetOutR — movement EXECUTION (red)
- Surveyed whether the movement-EXECUTION mutual-recursion pair
  (`_TryMoveDirB/R` <-> `_GetOutB/R`, flagged since cont.84) is tractable
  now that the dig subsystem is fully closed (dispatched to a research
  subagent to re-disassemble both pairs fresh, not trust the stale
  cont.90 callee list). Verdict: **split tractability** — the red-colony
  pair is fully clean (zero unrecovered dependencies once re-verified
  against the CURRENT `gameplay.py`), but the black-colony pair has one
  narrow gated branch in `_TryMoveDirB` calling `_DoTroph` (trophallaxis —
  food-sharing between ants), whose own dependency chain bottoms out in
  a genuine sound-engine routine (`_myBeginSound`, 2264B, no existing hook)
  and a dialog/sound/busy-wait UI routine (`_EatMyFood`) — a materially
  different, larger class of work, not a same-session win.
- RECOVERED `try_move_dir_r`/`get_out_r` (seg6:6850/74BA; both FAR return)
  — the movement-EXECUTION tier one level below the movement-SELECTION
  tier (`get_red_best_dirs` etc.) already closed this session. Confirmed
  by disassembly that `_TryMoveDirR` has NO trophallaxis branch at all
  (unlike its black twin) — it goes straight from the obstacle/bounds
  check to the move-tail writes, making it genuinely simpler and fully
  portable today.
  - `_TryMoveDirR`: computes a candidate cell via the same
    `GET_BEST_DIR_DX`/`DY` compass tables (confirmed byte-identical via a
    THIRD DGROUP pointer-global alias pair into the same table, after
    `get_my_best_dirs`'s and `get_red_best_dirs`'s own); a new_y below 1
    delegates entirely to `get_out_r(x)`, returning its result verbatim.
    Otherwise moves the ant if the destination tile is passable, updating
    the LIFE grid and the acting ant's per-slot record.
  - `_GetOutR`: on the row-0 hole marker, completes an exit hole via
    `exit_hole` (conditionally preceded by `make_new_hole_r`); otherwise
    nudges the dig frontier via `dig_tile_them_r` and recursively retries
    the move one row in — genuinely mutually recursive with
    `try_move_dir_r`, ported as an ordinary Python call (no VM stack to
    manage).
  - **CAUGHT TWO REAL BUGS**, both via state-diff tests plus register-
    level instrumented traces of the real ASM (not by re-reading the
    listing a second time):
    1. In `_TryMoveDirR`'s move-tail, a `mov ax,si` instruction two lines
       before a field write silently clobbers AL with `new_x` — the
       direction-encoded status byte that had been sitting in AL since
       two instructions earlier is gone by the time that write executes.
       The first port assumed AL still held the encoded byte there and
       skipped writing the caste field (`[0x46E6]`) entirely. Caught via
       a plain "successful move" test case (no recursion involved) whose
       expected vs. actual SDG diff didn't match a hand-computed formula
       — resolved by an instrumented trace printing real register values
       at each relevant instruction, which is what actually revealed the
       clobber.
    2. In `_GetOutR`, the `jnz` after `cmp hole_x[x], 0` was misread as
       "skip `make_new_hole_r` when the hole-tracking value is zero" —
       it's the opposite: `jnz` skips when the value is NONZERO, so the
       call fires exactly when the tracked value IS zero. Caught by
       reading `_ExitHole`'s actual stack arguments off an instrumented
       run and finding an `x` argument (106) that could only have come
       from a `make_new_hole_r` search result the buggy port never
       triggered — not from re-reading the disassembly, which reads
       exactly the same either way without spotting the sign of the
       jump.
  - 9 scenarios (direction<0, both bounds-rejection axes, the new_y<1
    GetOutR delegation, destination-blocked, two successful-move variants
    with different slot/caste, and GetOutR's three main branches — not-a-
    hole, hole-with-hole_x-zero, hole-with-hole_x-nonzero) — all green
    after both fixes.
- Suite: simant 1176 (+9). This closes the RED-colony half of the
  movement-EXECUTION tier — pathfinding SELECTION and EXECUTION are now
  both fully recovered for red. Continuing per /goal — next candidates:
  either attempt the analogous black-colony pair up to (but stopping
  before) the `_DoTroph` branch (raising fail-loud if that specific path
  is ever hit, per the project's "fail loud, never fake" rule), or survey
  for smaller tractable routines elsewhere given `_DoTroph`'s dependency
  chain is a genuinely different, larger body of work.

## 2026-07-14 (cont.100) — /goal grind: _DigTileThemB/R — the dig subsystem is DONE
- RECOVERED `dig_tile_them_b`/`dig_tile_them_r` (seg5:22D4/241C, args x, y;
  FAR return; genuinely return 1/0, unlike the other dig routines) — the
  last dig-subsystem member from cont.90's original survey. Cleanest
  disassembly of this entire thread (fits on one screen, no bugs caught).
  - Opens a NEW nest tile at (x, y), but only if its EXISTING dirt
    neighbours already look diggable: the tile at `(x, y+1)` must be
    `is_it_dirt` (when `y < 0x3F`), and `(x, y-1)` too (when `y > 2`) —
    either check failing rejects immediately with NO state changes at
    all. `x` must be in `1..0x3E`.
  - `y == 0`: writes `0x18` and calls `make_new_hole_b`/`make_new_hole_r`
    directly — row 0 doesn't reroll a tile here, it triggers a whole new
    hole search. Any other row: rerolls via `_SRand8`, exactly like
    `_DigTileB`/`R`'s own reroll step.
  - Either way: accumulates into the SAME running-average dig-position
    PACK fields `_DigTileB`/`R` already use (confirmed by the identical
    offsets, not assumed by naming), then smooths the 4 map neighbours and
    refreshes the exit-map, returning 1.
  - 16 scenarios (each neighbour-rejection independently, both x-boundary
    rejections, the plain reroll path, both y-boundary edge cases where
    one neighbour check is skipped, and the y=0 trigger path that fires a
    fully inert `make_new_hole_b`/`r` call) for BOTH colonies — ALL GREEN
    ON THE FIRST RUN.
- **This completes the entire dig subsystem** cont.90's survey identified
  as blocking `_TryMoveDirB/R` <-> `_GetOutB/R`: `_FixExitMapB/R`,
  `_SmoothEdgesB/R`, `_ExitHole`, `__aFldiv`, `_DigTileB/R`,
  `_MakeNewHoleB/R`, and now `_DigTileThemB/R` — 11 routines recovered
  across cont.90-cont.100, all cross-verified, zero shortcuts.
- Suite: simant 1167 (+16). Continuing per /goal — next: attempt
  `_TryMoveDirB/R` itself (seg6:439E/6850), the mutual-recursion pair
  with `_GetOutB/R` flagged since cont.84/85/90 as the actual unlock for
  the movement-EXECUTION tier (as opposed to the movement-SELECTION tier
  this session already closed). This is a new kind of challenge — genuine
  co-recursion between two routines — so it may need a fresh survey pass
  first to confirm the closure is really complete before diving in.

## 2026-07-14 (cont.99) — /goal grind: _MakeNewHoleR — the fixes from cont.98 paid off
- RECOVERED `make_new_hole_r` (seg5:1D02, arg: col; FAR return). Confirmed
  by disassembly rather than assumed symmetry: the search, classification,
  marker values, and 8-neighbour edge carve ALL operate on the SAME shared
  yard map (`MAP_PLANE_BASE[0]`) `make_new_hole_b` uses — "R" is about
  which nest the CLOSING step tunnels into, not which map the search
  happens on. Only the candidate row formula differs (`0x7E - ((roll+i) %
  0x20)`, searching down from 126, vs. black's `+2` searching up from 2)
  and the SDG scratch/hole-tracking offsets are R's own.
  - The closing step genuinely diverges from `_MakeNewHoleB`'s simple
    `dig_tile_b(col, 1)` call: it INLINES the same reroll/track logic
    `dig_tile_r` uses (via the shared `_dig_tile_reroll_and_track` helper,
    on the FIXED red-nest cell `(col, 1)`), then a specific 4-step
    sequence that is NOT `dig_tile_r`'s own closing smooth — it smooths
    `(col, 0)` and `(col, 2)` and `(col-1, 1)`, then refreshes the
    exit-map at `(col, 1)` instead of smoothing `(col+1, 1)` (confirmed
    by tracing every push/call in that tail individually, not assumed).
  - Every lesson from cont.98's three bugs applied cleanly here: the
    decimal/hex marker fix (`tile + 0x22`) carried straight over (the same
    `lea dx,[si+34]` pattern appears in this routine's own classify block),
    the carve loop reused the same `sbyte` 8-bit-sign-extension closure
    from the start, and the "inside" success path's jump-past-the-carve
    asymmetry was checked for and confirmed present here too before
    writing any code.
  - 8 scenarios (marker classification, search-advance, the "every
    candidate excluded" no-op, the closing step both with and without a
    dirt tile at the fixed cell, and both x-boundary cases) — ALL GREEN ON
    THE FIRST RUN, no bugs this time.
- Suite: simant 1151 (+8). Continuing per /goal — `_DigTileThemB/R`
  (seg5:22D4/241C, the last dig-subsystem layer, needing `_MakeNewHoleB/R`
  and `_DigTileB/R` — all now recovered) is next; landing it should
  finally make `_TryMoveDirB/R` <-> `_GetOutB/R` attemptable.

## 2026-07-14 (cont.98) — /goal grind: _MakeNewHoleB — three real bugs caught
- RECOVERED `make_new_hole_b` (seg5:1B06, arg: col; FAR return) — by far
  the trickiest single routine this session. Searches up to 34 candidate
  positions (`row = ((_SRand1(31) roll + i) % 32) + 2`) for a place to
  open a new above-ground exit hole near yard column `col`, using ONE of
  two totally different acceptance tests depending on PACK's `[0x9B6E]`
  "inside" flag:
  - flag SET: classify the yard map tile at each candidate into a
    priority/marker byte via a small lookup (tile `0`->`0x86`; `2`/`3`->
    `0x8A`; `0x5E..0x61`->`tile+0x22`; `0x66`->`0x85`; `0x68`->`0x84`;
    else not usable) — first usable candidate wins, write its marker.
  - flag CLEAR: instead calls the real `_IsClear3x3` (via the new
    `_clear_3x3(dgroup, plane, x, y)` VM-touching counterpart of the
    already-pure `is_clear_3x3(cells_clear)`) — first candidate whose
    whole 3x3 block is clear wins, writes the canonical hole tile `0x50`.
  On success either way: records the found position into SIMANT_DATA_GROUP
  scratch fields and `_FillHolesBN`'s per-row hole-tracking array
  (`[0x82D2 + col]`), then calls `dig_tile_b(col, 1)` to dig the
  connecting nest tunnel. The "flag CLEAR" success path ALSO carves an
  8-neighbour edge pattern (`HOLE_EDGE_TILES`, one fixed tile per compass
  direction) — confirmed via disassembly that the "flag SET" path jumps
  PAST this carve step entirely, an asymmetry easy to miss.
- Caught THREE real bugs in this one routine, each via a dedicated state-
  diff test case, none by re-reading the disassembly cold:
  1. **Decimal-vs-hex transcription**: the scratch disassembler renders
     `cmp reg,imm` operands in hex (`0062h`) but a nearby `lea dx,[si+34]`
     in bare DECIMAL — misread `34` as `0x34` when it's `0x22`. A
     dedicated tile-in-`[0x5E,0x61]`-band test case caught the wrong
     marker value immediately. Lesson: when a disassembler mixes
     notations, check every immediate's radix explicitly, especially
     `lea`'s displacement operand — don't assume consistency across
     instruction forms.
  2. **Control-flow bypass missed on first read**: the "flag SET" success
     path doesn't fall through into the 8-neighbour carve loop the way the
     "flag CLEAR" path does — it jumps straight past it to the shared
     tail. The first port ran the carve unconditionally for both paths;
     caught via full-suite diffs on the "inside=True" scenarios once the
     first bug's fix cleared the way to see it.
  3. **8-bit vs 16-bit sign extension**: the carve loop's compass delta
     bytes need 8-bit sign extension (`0xFF` -> `-1`) before use, but the
     first port used the module's `_sx16` helper on the raw byte read —
     `_sx16(0xFF)` treats it as the SMALL POSITIVE value `255`, not `-1`,
     since 16-bit sign extension only flips on bit 15, never bit 7. This
     silently produced wildly out-of-range neighbour coordinates that
     failed the bounds check on every direction, so the carve loop
     appeared to run but wrote nothing — no exception, no obvious symptom,
     just quietly wrong. Caught by manually re-deriving the ACTUAL
     candidate row a specific test seeded (empirically, via an
     instrumented CPU trace matching real register values against my
     assumption) and finding my own port's carve loop produced nonsense
     coordinates for it. Fixed by using the same local `sbyte(off)`
     8-bit-sign-extension closure `_fix_exit_map`/`get_smell_t` already
     established as the house style for this exact situation — the bug
     was deviating from an established pattern, not lacking one.
  - 10 scenarios (all 5 marker bands for the "inside" path, search-
    advance-past-a-rejected-candidate for both flag states, the "every
    candidate excluded" no-op, and both x-boundary cases) — all green
    after the three fixes.
- Suite: simant 1143 (+10). Continuing per /goal — `_MakeNewHoleR`
  (seg5:1D02, likely the red-colony twin with a similarly asymmetric
  carve-loop reachability) is next, then `_DigTileThemB/R` (the last
  layer before `_TryMoveDirB/R` <-> `_GetOutB/R` becomes attemptable).

## 2026-07-14 (cont.97) — /goal grind: _DigTileR + a shared reroll/track helper
- RECOVERED `dig_tile_r` (seg5:21DE, args x, y; FAR return) — the red-
  colony twin of `_DigTileB`'s core dig logic, but genuinely simpler: no
  y-threshold gate, no `_SRand1` roll, no black-side interaction at all —
  it's what a red ant's OWN dig calls, versus the rare cross-colony
  punch-through `_DigTileB` occasionally triggers into the exact same red
  PACK fields (`[0x9DDC..)`/`[0x9DE2..)`/`[0x7A56]`/`[0x9FBA]`/`[0x9FD2]`).
- Refactored `_DigTileB`'s reroll/accumulate/average logic out into a new
  shared `_dig_tile_reroll_and_track` helper (map base + 5 PACK field
  offsets + x/y in, dirt-or-not out) — `_DigTileR` and `_DigTileB`'s red
  branch both reuse it now instead of duplicating the same 15 lines twice.
  Re-ran `_DigTileB`'s existing 8 test cases after the refactor to confirm
  it's a pure restructuring (all passed unchanged, no behavior drift).
- Hit two small HARNESS setup mistakes while wiring the new test (neither
  a logic bug): first, passed 4 region tuples but a 3-arg lambda (split
  the count/sum PACK fields into two separate windows by habit, forgetting
  `_run_and_diff_segs` gives each region its OWN view argument) — merged
  into one wide PACK window. Second, the merged DGROUP region only covered
  the red map plane, missing the `_SRand8` seed at `0xCBF2` — same "region
  doesn't cover every table the function touches" class of mistake seen
  repeatedly this session (cont.91, cont.93); fixed by widening to the
  seed offset, same as `_DigTileB`'s own region already does.
- 5 scenarios (not-dirt no-op, dirt-with-average, count-starts-at-zero,
  both x-boundary cases) — all green after the two harness fixes.
- Suite: simant 1133 (+5). Continuing per /goal — `_MakeNewHoleB`/
  `_MakeNewHoleR` (seg5:1B06/1D02, need `_SRand1`✅, `_IsClear3x3`✅, and
  now `_DigTileB`/`_DigTileR`✅) are next, followed by `_DigTileThemB/R`
  (which need those plus `__aFldiv`✅) — the last layer before
  `_TryMoveDirB/R` <-> `_GetOutB/R` becomes attemptable.

## 2026-07-14 (cont.96) — /goal grind: _DigTileB — the first payoff of __aFldiv
- RECOVERED `dig_tile_b` (seg5:1FE4, args x, y; FAR return) — the first
  routine specifically unblocked by a PRIOR slice's recovery this session
  (every callee — `_IsItDirt`, `_SRand1`, `_SRand8`, `__aFldiv`,
  `_FixExitMapB/R`, `_SmoothEdgesB/R` — was already recovered before this
  one, several of them earlier in this same /goal run).
  - Rerolls the black nest map tile at (x,y) to a random 0..7 when it's
    dirt (`_SRand8`), while accumulating `x`/`y` into 32-bit PACK running
    sums and a dig counter, recomputing a running-average dig position via
    TWO GENUINE `__aFldiv` calls (sum/count) once the counter turns
    positive — confirmed the push order matches `__aFldiv`'s own ABI
    (divisor=counter pushed first, dividend=the 32-bit sum pushed second)
    by cross-checking against `crt_math.py`'s own established convention
    rather than re-deriving it from scratch.
  - When additionally `y > 0x35` (near the yard-facing end of the nest)
    AND a `_SRand1(0x40)` roll comes up exactly 0 (1-in-64): tunnels
    through into the red colony's map at the SAME (x,y) — repeating the
    identical dirt-check/reroll/running-average dance against a separate
    set of PACK fields, then smoothing the 4 red-map neighbours and the
    red exit-map, marking both colonies' tiles `0x14` (a tunnel-through
    sentinel).
  - Always smooths the 4 black-map neighbours and refreshes the black
    exit-map at (x,y) — even on a complete no-op "tile wasn't dirt" call,
    matching the ASM's unconditional tail (every early-exit path converges
    on it via `jmp`, not `ret`).
  - Added a small shared `_acc_add32` helper (sign-extend a 16-bit delta,
    add onto a 32-bit PACK accumulator, write both words back, return the
    raw total for immediate reuse as `a_f_ldiv`'s dividend) and a module-
    level `_sx32` (mirroring the existing `_sx16`, needed for the signed
    `count > 0` gate before each average recompute).
  - 8 scenarios (not-dirt no-op, dirt-with-running-average, count-starts-
    at-zero, the SRand1(64) roll both ways, red-tile-dirt vs. not, and
    both x-boundary cases) — ALL GREEN ON THE FIRST RUN, no bugs caught
    this time (the disassembly discipline from the last several slices —
    re-tracing every jump target's actual destination rather than trusting
    a first read, verifying selector resolutions empirically rather than
    assuming — is paying off in fewer round-trips).
- Suite: simant 1128 (+8). Continuing per /goal — `_DigTileR` (seg5:21DE,
  the red-colony twin, likely near-identical structure) is next, followed
  by `_MakeNewHoleB/R` (needs `_IsClear3x3`✅, `_SRand1`✅, and now
  `_DigTileB`✅) and `_DigTileThemB/R` (needs the same plus `_MakeNewHoleB/R`)
  — closing in on the full dig subsystem and, eventually, `_TryMoveDirB/R`
  <-> `_GetOutB/R`.

## 2026-07-14 (cont.95) — /goal grind: _ExitHole closes the dig-subsystem "tractable now" trio
- RECOVERED `exit_hole` (seg5:2DB6, args x, y, caste, field_c, field_e_hint;
  FAR return) — the third and last independently-tractable dig-subsystem
  member from cont.90's survey. The biggest/most involved single routine
  recovered this session by field-layout complexity, sitting immediately
  before `_AddAntToAList` (seg5:2EF0) in the ASM and sharing its exact
  5-field A-list layout, but is NOT a call to it — a hand-inlined variant
  with real behavioural differences:
  - Scans the 8 compass neighbours (same live-read SIMANT_DATA_GROUP delta
    tables as `_FixExitMapB`/`get_smell_t`), keeping the first one that is
    both `is_valid_a` and has a yard map tile `< 0x50` (unsigned `jb`) —
    confirmed via `_IsValidA`'s existing push-order convention that x/y
    aren't swapped.
  - `field_e` is COMPUTED, not passed straight through like
    `add_ant_to_a_list`'s: `field_c==6` uses the caller's `field_e_hint`
    verbatim; `field_c` in `{3,7}` forces 0; anything else picks 0 or 0x78
    from a `caste` high-bit + the ORIGINAL (pre-scan) x compared to 0x40.
  - Does NOT stamp the life grid at all (a genuine divergence from
    `add_ant_to_a_list`, not an oversight — re-verified by reading every
    instruction between the field writes and the tail, twice).
  - Handles a FULL list (`pack[0x80F0] >= 0x3E8`) completely differently:
    rather than silently refusing (like `add_ant_to_a_list`), it writes the
    new entry into the just-past-cap slot regardless, then runs a
    `compact_list_a`-style single-pass mark-and-sweep over the EXISTING
    0..cap-1 slots and re-derives the count from the shrunk total — the
    newly-written slot itself is outside that scan, so in the genuinely-
    zero-holes edge case the new entry ends up permanently uncounted.
    Ported byte-exact, not "fixed" — the project reconstructs what
    SimAnt actually does, bugs included.
  - CAUGHT A REAL BUG via the state-diff test: the caste-bit/x-threshold
    rule's TWO branches both had their inequality direction backwards in
    the first draft (`caste&0x80==0: 0 if x<=0x40 else 0x78`, when the
    ASM's `jge`/`jle` pair actually means `0x78 if x<0x40 else 0` — and
    the mirror-image mistake in the other branch too). A dedicated
    `x=20,caste=0` test case caught it immediately (ASM wrote 0x78, the
    buggy port wrote 0). Fixed both branches by re-tracing the exact
    jump targets rather than trusting the first read.
  - 11 scenarios (no-clear-direction failure, all four caste-bit x
    x-threshold combinations, all three `field_c` special cases, multiple-
    clear-directions-picks-first, and both full-list edge cases — WITH
    holes and with zero holes) — all green after the fix.
- Suite: simant 1120 (+11). This closes cont.90's "independently tractable
  now" trio (`_FixExitMapB/R`, `_SmoothEdgesB/R`, `_ExitHole`) entirely.
  Continuing per /goal — next per cont.90's survey: `_DigTileThemB/R` and
  `_MakeNewHoleB/R` (seg5, both now unblocked since `__aFldiv` is
  recovered) are the next layer, closing in on `_TryMoveDirB/R` <->
  `_GetOutB/R` (the movement-EXECUTION mutual-recursion pair) finally
  becoming attemptable.

## 2026-07-14 (cont.94) — /goal grind: _SmoothEdgesB/R (dig-subsystem, second slice)
- RECOVERED `smooth_edges_b`/`smooth_edges_r` (seg5:255A/26E4, args x, y;
  FAR return) — the second independently-tractable dig-subsystem member
  from cont.90's survey (only calls the already-recovered `_SRand8`).
  Rounds off a dirt tile's exposed edges after a dig: row 0 is special-
  cased (tile < 0x30 forced to 0x18, the same exit marker `_FixExitMapB`
  uses); every other row builds a 4-bit neighbour-dirt bitmask (a
  neighbour off the 64x64 grid always counts as "dirt") and either writes
  a 4-bit auto-tile edge/corner variant selector (`bits + center_class +
  0x1F`) when any neighbour is dirt, or — when fully surrounded by
  non-dirt — rerolls to a random 0..7 via `_SRand8` (centre in the
  0x20-0x2F band) or writes the literal 0x4E (centre >=0x4F band).
  - Identified but deliberately did NOT recover `_RIsItDirt` (seg5:26C4) as
    a separate function: it's byte-identical to the classification this
    routine already inlines four times, but `_SmoothEdgesB/R` never
    actually CALLS it (confirmed the near-call target at `push cs; call
    near 0x15EE` resolves to `_SRand8`, not `_RIsItDirt`) — recovering an
    unused-by-anything-yet leaf would be scope creep; noted for a future
    session if something else needs it.
  - CAUGHT A REAL BUG via the state-diff test, not by re-reading the
    disassembly: the first port had the row-0 threshold exactly backwards
    (`if tile >= 0x30: write 0x18`). The ASM's `cmp tile,0x30; jb -> write`
    is an UNSIGNED "jump if BELOW" — i.e. `tile < 0x30` triggers the
    write, `>= 0x30` is the no-op — and I'd swapped which branch did
    which. A dedicated `tile=0x20` (below 0x30) test case caught it
    immediately (ASM wrote 0x18, the buggy port left it unchanged).
  - 32 scenarios (both twins x all four boundary-default directions,
    both reroll bands, one-neighbour and all-four-neighbours-dirt bitmask
    cases, two out-of-range coordinate cases) — all green after the fix.
- Suite: simant 1109 (+32). Continuing per /goal — next per cont.90's
  survey: `_ExitHole` (seg5:2DB6, only calls the already-recovered
  `_IsValidA`) is the last of the three independently-tractable dig-
  subsystem members; after that, `_DigTileThemB/R` and `_MakeNewHoleB/R`
  (which additionally need `__aFldiv`, already recovered) become the next
  layer, closing in on `_TryMoveDirB/R` <-> `_GetOutB/R`.

## 2026-07-14 (cont.93) — /goal grind: _FixExitMapB/R (dig-subsystem, first slice)
- Started the dig-subsystem chain cont.90's survey flagged as independently
  tractable now: `_FixExitMapB`/`_FixExitMapR` (seg5:284E/2914, pure leaves,
  no calls at all).
  - Rows 0-1 (right at the nest exit) are special-cased against the
    colony's own nest map (`_GetMap` plane 2/3): tile `0x18` (the exit tile
    itself) marks the exit-map cell `0xFF`, anything else `0xFE` —
    sentinels, not real distances.
  - Every other row scans the 8 compass neighbours — the SAME direction-
    delta tables `get_smell_t` already reads LIVE from
    `simant_data_group[0+dir]/[8+dir]` (confirmed via the local `sbyte`
    closure precedent, not hardcoded) — and takes the highest existing
    exit-map value among the in-bounds ones, writing `max - 1` (or 0 if
    every neighbour was still 0). This is a flood-fill-by-one-step-per-call
    "distance from the nest exit" gradient, seeded by the row-0/1
    sentinels: something that calls `_FixExitMapB` repeatedly across the
    whole nest (presumably during dig/exit-hole maintenance, not yet
    recovered) will eventually converge every cell to its BFS distance
    from the nearest exit tile.
  - Shared the body between the B/R twins via a private `_fix_exit_map`
    helper (map plane 2 vs 3, exit-map array at SIMANT_DATA_GROUP `[0x3A4..)`
    vs `[0x13A4..)`) — first genuinely-shared-helper pair since
    `_ColonySmellDecay*`/`_JamScent*` earlier this session.
  - First test run hit ANOTHER instance of the by-now-familiar "which SDG
    offsets does the region actually need to cover" harness mistake (same
    root cause as cont.91's `_DeadAntHere` bug, different symptom): the
    exit-map array's region started at `0x3A4`/`0x13A4`, excluding SDG
    offsets 0-15 where the direction-delta tables the recovered function
    ALSO reads actually live — `IndexError: bytearray index out of range`
    on the very first `sbyte(0)` call. Fixed by widening the SDG region to
    start at 0. Lesson for future dig-subsystem routines (several more of
    which will need these same delta tables): remember to include SDG[0:16]
    in the region whenever a function reads them, not just the routine's
    "primary" data.
  - 14 scenarios (both twins x row-0/1 exit-tile / non-exit-tile, all-zero
    neighbours, a picks-the-max case, and three off-grid-neighbour boundary
    cases at x=0/x=0x3F/y=0x3F) — all green after the region fix.
- Suite: simant 1077 (+14). Continuing per /goal — next per cont.90's
  survey: `_SmoothEdgesB`/`_SmoothEdgesR` (seg5:255A/26E4, only calls the
  already-recovered `_SRand8`) is the next independently-tractable dig-
  subsystem member; `_ExitHole` (seg5:2DB6, only calls `_IsValidA`) after
  that. Landing all three clears the way for `_DigTileThemB/R` and
  `_MakeNewHoleB/R` (which additionally need `__aFldiv`, already done) —
  and those, in turn, are most of what stands between today and
  `_TryMoveDirB/R` <-> `_GetOutB/R` (the movement-EXECUTION mutual-
  recursion pair) finally becoming attemptable.

## 2026-07-14 (cont.92) — /goal grind: __aFulmul + the MSC rand()/_RRand trio
- RECOVERED `a_f_ulmul` (`crt_math.py`, seg4:096E, `__aFulmul`): unsigned
  32-bit long multiply truncated to 32 bits. The ASM computes three of the
  four 16x16 cross-terms and never touches the fourth (`hi*hi`, which only
  affects bits 32-63) — confirms it's `(a*b) & 0xFFFFFFFF`, trivially
  correct via Python's arbitrary-precision multiply. 10 cases (including
  both-wide operands that overflow 32 bits), all green first try.
- RECOVERED the standard Microsoft C runtime `rand()`/`srand()` pair plus
  SimAnt's own `_RRand` wrapper — a fully independent RNG family from the
  `_SRand*` LFSR already in `simone.py` (that one is deterministic map-gen;
  this one is "genuinely unpredictable" combat-roll-style randomness).
  - `c_srand`/`c_rand` land in `crt_math.py` (pure MSC library, not SimAnt
    logic — same reasoning as `__aFldiv`/`__aFulmul`). `c_rand` genuinely
    near-calls `a_f_ulmul` for `state * 0x343FD` (confirmed via
    disassembly, the same near-call-to-far-retf ABI bridge seen repeatedly
    this session), then `+ 0x269EC3` — the textbook MSVC LCG.
  - `r_rand` (SimAnt's own `_RRand` wrapper, seg5:156E) lands in
    `simone.py` instead — genuinely SIMONE_MODULE-domain, calls `c_rand`
    and takes the signed remainder mod `n`. Ported the ASM's defensive
    `abs()` on `_rand`'s result faithfully even though it's provably dead
    code (`_rand` always returns 0..0x7FFF, so the abs() never fires) —
    byte-exact means porting what's there, not what's provably redundant.
  - 19 cases across all three (including the DGROUP-dword RNG state
    round-tripped through the ASM, not just the return value), all green
    first try.
- This completes the `_RRand`/`_rand`/`_srand`/`__aFulmul` chain flagged
  as "cheap, worth landing on its own merits" back in cont.84's original
  survey and reconfirmed in cont.90 — removes one of `_GetWinner`'s two
  blockers ahead of the day `SIMTWO!_GetNewMode` (seg7) becomes reachable.
- Suite: simant 1063 (+16 this stretch: 4 `c_srand` + 6 `c_rand` + 6
  `r_rand`; `a_f_ulmul`'s 10 cases landed in the prior commit's count).
  Continuing per /goal.

## 2026-07-14 (cont.91) — /goal grind: _DeadAntHere (100-slot corpse ring buffer)
- RECOVERED `dead_ant_here` (seg6:28C0, args: new_x, new_y, mode; FAR
  return) — the strongest pure-gameplay pick from cont.90's survey (fan-in
  9 across top-level caste dispatchers and combat/predation routines,
  zero new dependencies beyond already-recovered `_SRand1`/`_SRand4`/
  `_SRand16`).
  - Decoded a 100-slot ring buffer living entirely in PACK: a word counter
    (`[0x9EA8]`, incremented + wrapped to 0 at 100 every call), a byte-per-
    slot X table (`[0x9C82..)`), and a word-per-slot-but-byte-written Y
    table (`[0x9D76..)`) — both indexed by the RAW counter value (not
    counter*width), the same convention the per-ant list arrays already
    use. Each call reads the slot the counter now points at (the position
    recorded ~100 calls ago), fades whatever corpse-marker tile is there
    (random via `_SRand16` outside the nest, a deterministic `(tile-8)>>2`
    inside), then overwrites that slot with the caller's OWN (new_x,
    new_y) and — if the map's already-quiet there — plants a fresh marker
    (`_SRand4`-based outside, `_SRand1(2)`-based inside), and finally
    always clears the yard life-grid cell at the new position.
  - Resolved all four DGROUP pointer-globals this routine reads
    (`[0xC344]/[0xC346]/[0xC348]/[0xC320]`) by reading real memory rather
    than assuming — ALL FOUR resolve to PACK (not SIMANT_DATA_GROUP),
    including the `[0x9B6E]` "inside" world flag, which turns out to be
    PACK-resident too (same field `is_it_food`/`tile_can_be_moved_on`
    already read through a different selector alias — confirms this is
    the SAME shared flag, just reached via yet another of the project's
    many redundant DGROUP pointer-globals into the same fixed segment).
  - Threaded the shared `_SRand*` LFSR seed through up to two RNG calls per
    invocation (one for the evicted slot's fade, one for the fresh marker)
    in exact ASM call order, mirroring `drop_water`'s existing seed-
    threading convention.
  - First test run hit a HARNESS bug, not a logic bug: `_run_and_diff_segs`
    takes one window per SEGMENT, but this routine touches THREE separate
    DGROUP sub-ranges (yard map, yard life, the RNG seed) — the initial
    3-DGROUP-window + 1-PACK-window region list crashed with a wrong-arg-
    count TypeError (the harness treats each region as its own segment
    slot, not each window within one segment). Fixed by combining the
    three DGROUP windows into one wide `[0x28E8, 0xCBF4)` region; all 11
    scenarios (fade bands on/off, RNG vs. deterministic paths, the mode
    flag both ways, the counter wrap boundary at 99->0, and a slot-equals-
    new-position self-overlap case) then passed on the first re-run.
- Suite: simant 1037 (+11). Continuing per /goal — next per cont.90's
  survey: `__aFulmul` (seg4:096E, `__aFldiv`'s unsigned-multiply sibling
  and a hard prerequisite for `_rand`/`_RRand`) is the cheapest follow-on
  in `crt_math.py`.

## 2026-07-14 (cont.90) — /goal grind: __aFldiv (dig-subsystem unlock, new module)
- Ran a fresh survey (dispatched to a research subagent) now that the whole
  pathfinding-selection tier is closed. Confirmed `_TryMoveDirB/R` and
  `_SetLife` are BOTH still genuinely blocked — fully traced both closures:
  `_TryMoveDirB/R` needs an 8-9-routine chain including a mutual-recursion
  pair (`_TryMoveDirB/R` <-> `_GetOutB/R`) and the whole dig subsystem
  (`_MakeNewHoleB/R`, `_DigTileThemB/R`, `_DigTileB/R`, `_SmoothEdgesB/R`,
  `_FixExitMapB/R`); `_SetLife` needs the SAME dig subsystem plus an
  `indirect`-classified 2264-byte sound-engine routine with zero existing
  hook infrastructure. Top pick instead: `__aFldiv` (seg4:08D4, the signed
  32-bit long-division C-runtime helper) — a genuine leaf, 40+ call sites
  project-wide (the highest fan-in found this session, ahead of even
  `_GetWinner`'s 30), and specifically the ONE new dependency standing
  between today and `_DigTileB/R` + `_MakeNewHoleB/R` + `_DigTileThemB/R`
  becoming tractable (three more dig-subsystem members already confirmed
  independently tractable: `_ExitHole`, `_SmoothEdgesB/R`, `_FixExitMapB/R`).
- Investigated where `__aFldiv` belongs before writing any code: it's not
  SimAnt logic, just the MSC compiler's own runtime library (every
  MSC-compiled 16-bit app links the identical helper), so checked whether
  `win16_re`/`dos_re` already have a "generic C-runtime helper" home per
  CLAUDE.md's "genuinely new mechanism -> win16_re" rule. Confirmed (via a
  research subagent) there is NO such upstream convention — the existing
  sibling `__aFuldiv` (unsigned divide) lives entirely in `simant/hooks.py`
  as a lifted performance island (it was PC-sampled at ~14% of runtime, a
  genuine hot loop), and `lifted_islands.md` is explicit that islands are a
  per-game pattern, never promoted to `win16_re`. Since `__aFldiv` hasn't
  been profiled hot and the actual need here is COMPOSABILITY (future
  dig-subsystem routines calling it as a plain Python function, the way
  `get_my_best_dirs` calls `get_dis`), not a VM-level lift, it belongs in
  `simant/recovered/` as source, not `hooks.py` as an island.
- RECOVERED `a_f_ldiv` in a NEW module `simant/recovered/crt_math.py`
  (following `byteops.py`'s exact precedent — tiny, generic, seg4-`_TEXT`,
  VM-free, A/B-oracle-tested in `test_hooks.py`, not gameplay.py, since it
  isn't simulation logic). Confirmed the disassembly independently (own
  pass with the scratch disassembler, not just trusting the survey): takes
  absolute values of both 32-bit operands (tracking sign parity), divides
  the magnitudes via a double-precision shift-estimate-correct routine
  (classic single-step Knuth algorithm-D), negates the quotient iff exactly
  one operand was negative. Zero divisor reaches the ASM's own `div` and
  `#DE`-faults, so the port raises `ZeroDivisionError` rather than
  fabricate a value (matches `__aFuldiv`'s existing island precedent for
  the same situation). 17 cases including both `INT_MIN` edge cases and the
  divisor-fits-16-bits vs. divisor-needs-full-32-bits branch split, all
  green on the first run.
- Suite: simant 1026 (+17). Continuing per /goal — next per the survey:
  `_DeadAntHere` (seg6:28C0, 354B, fan-in 9, ZERO new dependencies beyond
  already-recovered `_SRand1/4/16` — tractable today) is the strongest
  pure-gameplay pick; `__aFulmul` (seg4:096E, __aFldiv's 32x32 unsigned-
  multiply sibling, a hard prerequisite for `_rand`/`_RRand`) is the
  cheapest follow-on in the same `crt_math.py` module.

## 2026-07-14 (cont.89) — /goal grind: _GetRedBestDirs closes the pathfinding family
- RECOVERED `get_red_best_dirs` (seg6:9A18, args: plane, cur_x, cur_y,
  tgt_x, tgt_y; FAR return) — the red-colony twin of `get_my_best_dirs`,
  and the last symbol in `SIMANT1_MODULE` (seg6). Structurally identical
  control flow (verified byte-for-byte against the same disassembly
  pattern), but simpler: reads NO PACK state at all. Where
  `get_my_best_dirs` threads PACK-resident `cand_plane`/`cand_x`/`cand_y`/
  `check_adjacent` into `tile_can_be_moved_on`, this routine passes its own
  `plane`/`tgt_x`/`tgt_y` for the candidate site and hardcodes
  `check_adjacent` to False (a literal `push 0` in the ASM, not a PACK
  read). Confirmed the compass delta tables it reads via a DIFFERENT pair
  of DGROUP pointer-globals (`[0xC648]`/`[0xC64A]` vs. `get_my_best_dirs`'s
  `[0xC3C4]`/`[0xC3CA]`) hold the exact same values as `GET_BEST_DIR_DX`/
  `GET_BEST_DIR_DY` by reading real memory rather than assuming symmetry.
  9 scenarios (mirroring `get_my_best_dirs`'s test shape minus the PACK
  candidate-site cases, since none apply here), all green on the first run.
- Suite: simant 1009 (+9). This closes the entire pathfinding-tier thread
  surveyed in cont.84/85: `_TileCanBeMovedOn` -> `_GetMyBestDirs` /
  `_GetRedBestDirs` -> `_GetMyRandDirs` / `_CheckMyBestDirs` are all
  recovered and cross-verified. `seg6` (SIMANT1) is now fully scanned for
  this family — the remaining unrecovered seg6 routines are the movement-
  execution tier (`_TryMoveDirB/R` -> `_GetOutB/R` -> the dig subsystem)
  and combat (`_YellowFight`/`_GetWinner`), both flagged in cont.84's
  survey as blocked on further seg5/seg7 dependencies. Continuing per
  /goal — next step is either tackling the `_TryMoveDirB/R`/`_GetOutB/R`
  chain (movement EXECUTION, now that movement SELECTION is fully
  recovered) or re-surveying for smaller tractable seg5 leaves first.

## 2026-07-14 (cont.88) — /goal grind: _CheckMyBestDirs (1000 tests)
- RECOVERED `check_my_best_dirs` (seg6:8B40, args: one FAR pointer output,
  then plane, cur_x, cur_y, tgt_x, tgt_y; FAR return) — walks
  `get_my_best_dirs` up to 64 steps toward a target, accumulating a step
  count into the output pointer. Another genuine caller of `_GetMyBestDirs`
  via the near-call/far-retf ABI bridge (same pattern as `_TallyModePop` ->
  `_MakeRedInitiator` in cont.83, and `_GetMyRandDirs` calling the same
  callee in cont.87).
  - CAUGHT A REAL BUG via the state-diff test itself, not by re-reading the
    disassembly first: the initial port had the final result exactly
    backwards. The ASM's tail (`or si,si; jl -> 8BE3 [skip]; mov si,0xFFFF;
    8BE3: mov ax,si`) actually means "if the last `get_my_best_dirs` call
    FAILED (si<0), return that failure code UNCHANGED (so callers can still
    tell -1 'blocked' apart from -2 'nothing clear'); if it SUCCEEDED
    (si>=0), DISCARD the actual direction and force the return to exactly
    -1" — i.e. success collapses to a generic -1 marker and only failure
    preserves its real value. My first draft had `return si if si>=0 else -1`
    (backwards) instead of `return si if si<0 else -1`. A dedicated "walled
    in on the very first call" test case (expects the real -2 sentinel to
    survive) caught it immediately: ASM returned 0xFFFE, the buggy port
    returned 0xFFFF. Also had to move the clamp so the "first call fails
    immediately" path shares the SAME finalize logic as the loop-exit path
    (both routes in the ASM converge on the identical tail code at 8BD6) —
    my first draft special-cased the immediate-failure branch to hardcode
    -1, which is what caused the mismatch in the first place.
  - Needed a real step-count measurement before sizing the test harness:
    empirically measured a full-ish 57-step open-field walk taking ~81,000
    CPU steps (each `get_my_best_dirs` sub-call is itself thousands of
    steps), so the harness budget is 1,500,000 (vs. the shared
    `_run_and_diff*` helpers' 200,000) — this routine's own bespoke harness,
    not the shared one, since it also needs a single far-pointer output
    checked alongside AX like `_GetMyRandDirs`'s did.
  - 6 scenarios (already-at-target, short open-field hop, long open-field
    walk near the 64-step cap, walled-in-immediately, walled-in-partway-
    through the loop, yard-plane) — all green after the fix.
- Suite: simant 1000 (+6). Continuing per /goal — `_GetRedBestDirs`
  (seg6:9A18, the red-colony twin of this pathfinding family, last symbol
  in seg6) is next.

## 2026-07-14 (cont.87) — /goal grind: _GetMyRandDirs (stateful sticky-direction search)
- RECOVERED `get_my_rand_dirs` (seg6:8928, args: TWO far-pointer outputs then
  plane, cur_x, cur_y, tgt_x, tgt_y; FAR return; genuine MUTATOR via the
  pointer outputs, not pure) — `_GetMyBestDirs`'s immediate successor in the
  symbol table and the biggest/most stateful routine recovered this session.
  Implements a "keep moving the same way, don't re-pick every tick" search:
  the two far-pointer cells are a tri-state mode flag (0 = nothing committed,
  1 = committed via a forward scan, 0xFFFF = committed via a backward scan)
  and the committed direction index, both READ on entry and WRITTEN on exit
  — modelled in the recovered Python as 1-element lists standing in for the
  caller's far-pointer cells (no existing gameplay.py routine needed this
  in/out-pointer shape before).
  - Clearance mask over the 8 neighbours reuses `tile_can_be_moved_on` (same
    PACK-resident candidate-site fields as `get_my_best_dirs`), plus ONE new
    wrinkle: a neighbour exactly matching a PACK "avoid" cell
    (`pack[0xA0D6]`/`[0xA0DA]`) is forced blocked WITHOUT even calling
    `tile_can_be_moved_on` — confirmed this routine has NO life/occupancy
    check anywhere (unlike `get_my_best_dirs`), verified with a dedicated
    test case seeding a nonzero life byte on an otherwise-clear cell and
    confirming it's still treated as clear.
  - `out1[0] == 0`: sweeps outward from `out2[0]` in both directions at once
    (`fwd`++, `back`--, mod 8) for the first clear cell.
  - `out1[0] != 0`: re-validates the SAME remembered index each of up to 8
    iterations (tracked via two independently-advancing "chosen1"/"chosen2"
    trackers depending on which mode is active) — recomputes in place if
    still clear (and only writes fresh output values if the recomputed
    distance actually improved; otherwise returns the index with NO writes
    at all — a branch worth its own test case), else advances and retries.
  - CAUGHT AND FIXED a wrong-direction read mid-decode: my first pass had
    "if the tracked direction is STILL clear -> advance / keep searching,
    else -> recompute" — exactly backwards from the real `jz`/`jnz` targets.
    Re-traced every branch target against its actual destination label
    (not the trusted-at-a-glance mnemonic) before writing any Python, which
    caught it before it became a bug — same discipline that saved
    `_TileCanBeMovedOn` in cont.85.
  - Verification needed a bespoke harness (not the shared `_run_and_diff*`
    helpers): a genuine far-pointer ARGUMENT pair on the stack (offset word
    then segment word, LES-order) pointing at real writable PACK memory,
    checking both the returned AX and the two output words read back after
    the run. 12 hand-built scenarios (at-target, nothing-clear, forward-hit,
    backward-hit, avoid-cell-forces-fallback, both re-entrant recompute
    paths in both scan-mode directions, the "recompute but distance didn't
    improve -> no writes" branch, the occupied-but-still-clear case, and the
    yard-plane threshold gate) — ALL GREEN on the first run.
- Suite: simant 994 (+12). This is the largest single routine recovered
  this session by control-flow complexity; the pathfinding-tier thread
  (`_TileCanBeMovedOn` -> `_GetMyBestDirs` -> `_GetMyRandDirs`) is now fully
  closed. Continuing per /goal — remaining siblings per cont.84's original
  survey: `_CheckMyBestDirs` (seg6:8B40, right after this one) and
  `_GetRedBestDirs` (seg6:9A18, the red-colony twin, last symbol in seg6).

## 2026-07-14 (cont.86) — /goal grind: _GetMyBestDirs (player-ant pathfinding done)
- RECOVERED `get_my_best_dirs` (seg6:8828, args: plane, cur_x, cur_y, tgt_x,
  tgt_y; FAR return; PURE READ) — `_TileCanBeMovedOn`'s only remaining caller
  from cont.85's survey, and the last unlock needed for the player-ant
  pathfinding tier. Same scan-8-neighbours-keep-the-closer shape as the
  already-recovered `get_best_dir`, but composed from different building
  blocks: the movement gate is `tile_can_be_moved_on` (not
  `is_not_obstacle`/`is_this_pebble`), and it genuinely CALLS `_GetLife` and
  `_IsClearTile(plane,x,y)` as subroutines (confirmed via the scratch
  disassembler) rather than inlining the reads the way `_GetBestDir` does.
  Proved the `_GetLife` 0->0xFFFF sentinel transform is a no-op for this
  routine's own "occupied" check (byte 0 -> sentinel -1 signed -> "not
  occupied"; byte 1..255 -> itself, always positive-signed -> "occupied") so
  a single raw life-byte read serves both the occupied check AND (unlike
  `_GetLife`) the OWN raw-byte input `is_clear_tile` needs — no need to
  duplicate the read or carry the transformed sentinel around.
  - Before the scan it reads 4 PACK-resident fields ONCE through DGROUP
    pointer-globals `[0xC3AE]`/`[0xC3BE]`/`[0xC3B8]`/`[0xC3BC]` (confirmed
    all four resolve to the PACK segment, not SIMANT_DATA_GROUP, by reading
    a fresh machine's actual selector values) and threads them into every
    `tile_can_be_moved_on` call as `cand_plane`/`cand_x`/`cand_y`/
    `check_adjacent` — this is the "self/candidate site" cont.85 could only
    infer the SHAPE of from `_TileCanBeMovedOn`'s own body; this routine's
    call site confirms it really is read from fixed world state, not passed
    down from a further caller.
  - Confirmed empirically (not by reading a table dump) that the two 8-entry
    delta tables this routine reads via DGROUP pointer-globals `[0xC3C4]`/
    `[0xC3CA]` (offsets `+8`/`+0` within, byte-sized, sign-extended) hold the
    EXACT SAME compass values as the already-recovered `GET_BEST_DIR_DX`/
    `GET_BEST_DIR_DY` constants — read a fresh machine's actual bytes at
    both locations and diffed against the constants rather than assuming.
  - Building the scratch disassembler in cont.85 paid for itself again here:
    caught mid-decode that a run of four `push`es reading FIXED (non-`si`-
    indexed) DGROUP-selector offsets were the "candidate site" scalars, not
    a second direction table as first assumed from the raw mnemonic text —
    resolved by checking the modrm byte's addressing mode by hand (mod=00,
    rm=110 = direct address, no index register) rather than trusting the
    printed `es:[80D2]`-style text at face value.
  - 13 hand-built scenarios (already-at-target, all-blocked, one-clear,
    two-clear-picks-closer, clear-but-occupied-falls-back, clear-beats-
    occupied-when-both-present, pebble-tile-clear, boundary-adjacent
    (skips out-of-range directions without crashing), candidate-site self-
    exclusion suppressing the only clear direction, check_adjacent +
    extended-dirt-band tile, and both yard-plane threshold-gate cases)
    all passed on the FIRST run.
- Suite: simant 982 (+13). This closes out the `_TileCanBeMovedOn` /
  `_GetMyBestDirs` pathfinding-unlock thread from cont.85 entirely.
  Continuing per /goal — next candidates per cont.84's original survey are
  `_GetMyRandDirs` (seg6:8928, immediately after `_GetMyBestDirs` in the
  symbol table, likely a close sibling) and `_GetRedBestDirs` (the red-
  colony twin), or re-surveying seg5/seg6 fan-in now that this pathfinding
  branch is closed.

## 2026-07-14 (cont.85) — /goal grind: _TileCanBeMovedOn (yellow-ant pathfinding unlock)
- Built a scratch linear disassembler (`dos_re.lift.decode.decode_one` for
  static instruction lengths + the CPU's own `execute_opcode` capture for
  mnemonic text, same trick as `win16_re/dos_re/tools/lindis.py` but pointed
  at a throwaway `runtime.create_machine()` instead of a DOS snapshot dir —
  win16 NE segments have no snapshot-loader equivalent). This actually
  EXECUTES each instruction on a scratch machine (only for text capture; the
  static length keeps the linear walk aligned regardless of what a branch
  does), so it's throwaway-machine-only, never used against the real test
  harness's machine.
- RECOVERED `tile_can_be_moved_on` (seg5:9342, 7 args: plane, x, y,
  cand_plane, cand_x, cand_y, check_adjacent; FAR return; PURE READ, no
  mutation) — the routine flagged in cont.84's survey as the unlock for
  `_GetMyBestDirs`/`_GetMyRandDirs`/`_GetRedBestDirs` (the yellow-ant/red-
  colony pathfinding tier one level below the `_Do*Ant*` behaviors).
  - `plane <= 1`: bounds-check like `is_valid_a`, read the yard map, and
    return 1 if tile <= (0x90 if the `[0xC4AC]`-selector world flag is set,
    else 0x53) — same selector `is_not_barrier`/`is_not_obstacle` already
    read, confirmed by reusing their exact seeding idiom in the test
    (`world = mem.rw(DG, 0xC4AC); wb(world, 0x9B6E, flag)`).
  - `plane > 1`: bounds-check like `is_valid_b`; base is `MAP_PLANE_BASE[2]`
    only when `plane == 2` exactly, else `MAP_PLANE_BASE[3]` for ANY other
    plane value (not a 4-way dispatch like `_GetMap`'s `_cell_offset` — this
    routine's own inline `cmp cx,2` is a straight 2-way branch, confirmed by
    tracing the raw bytes rather than assuming symmetry with already-
    recovered helpers). `tile <= 0x18` or a pebble (`0x30..0x31`) is
    unconditionally "clear" (exactly `is_not_obstacle`'s plane>1 rule — a
    useful cross-check that the decode was right); when `check_adjacent` is
    set, the wider dirt band `0x1C..0x2E` also counts as clear ("extended").
    A clear cell then runs a second comparison against a caller-supplied
    second site (`cand_plane/x/y`) with intricate but now byte-exact-decoded
    branching (documented in the function's docstring) — reads as "assume
    clear unless this coincides with that other site" (`_GetMyBestDirs`
    always passes its own position there, so in practice this is a self-
    exclusion filter, though this routine itself has no way to know that).
  - Went through the disassembly BY HAND twice: the first read of the
    `dx`-return convention was backwards (assumed `jz -> 9499` meant "return
    0", when 9499 is actually the generic "return whatever dx currently
    holds" exit and only `9497`'s `xor dx,dx; ...` explicitly zeroes it) —
    caught before writing any Python by re-tracing every exit target's
    predecessor instruction rather than trusting the first pass.
  - Once ported, all 31 hand-picked state-diff-adjacent cases (via the pure-
    read `_run_and_get_ax` oracle, not a mutation diff — this routine writes
    nothing) passed on the FIRST run, including 6 cases dedicated to the
    "extended"/dirt-band/neighbour-tile sub-branch and full coverage of the
    `check_adjacent` x `y==0/1/>1` x `cand_plane==/!=plane` combinatorics.
- Suite: simant 969 (+31). Continuing per /goal — `_TileCanBeMovedOn` is
  fully decoded now; next candidate is `_GetMyBestDirs` (seg6:8828), whose
  only remaining unrecovered callee was this routine (its other 3 callees —
  `_GetDis`, `_GetLife`, `_IsClearTile` — are already in `gameplay.py`).

## 2026-07-14 (cont.84) — /goal grind: _SmoothAlarm + _FloodNestB; README recovery-map refresh
- Updated `README.md`'s "Recovery map" section (Mermaid graph + coverage table)
  to reflect this /goal session's actual progress: seg6 (SIMANT1) jumped
  2/123 -> 26/123 with the ant-list/scent/mode-pop mutator tier recovered in
  cont.72..83; seg5 (SIMONE) 38/169 -> 57/169. Added a "mutator tier" subgraph
  layer to the diagram and refreshed the load-bearing ranking (`_SRand1` 88,
  `_SRand8` 71, `_win_IsWinOpen` 67, `_win_GetObjRect` 50, `_FindInAList` 16,
  `_FindInBList` 15, ...).
- Surveyed seg5/seg6 for the next tractable candidates (dispatched to a
  research subagent to avoid burning main-context budget on disassembly). Key
  finding: **no `_Do*Ant*` behavior routine is end-to-end tractable yet** —
  every one of `_DoDigInB`/`_DoForageAnt`/`_DoNestAntB`/`_DoAntSimA/B` bottoms
  out on a still-unrecovered cluster (`_GetNewMode*` in `seg7`, `_TryMoveDirB/R`
  -> `_GetOutB/R` -> the dig-subsystem, `_YellowFight`/`_GetWinner` combat).
  `_GetWinner` (30 callers) looked promising but makes far calls into
  `SIMTWO!_GetNewMode` (seg7, unrecovered) and `ANTEDIT!_FightBalloons`
  (seg3, presentation) — not tractable this session. Also fully decoded the
  `_RRand`/`_rand`/`_srand` C-runtime LCG chain (seg5:156E / seg4:070A/06F6,
  `seed = seed*0x343FD + 0x269EC3`) as a cheap future win once `_GetNewMode`
  is tackled, but did not land it this round (no seg5/seg6 caller needs it
  yet on its own).
- RECOVERED `smooth_alarm` (seg6:9380, no args, NEAR return): a one-step
  4-neighbour box blur of the same 64x32 half-res alarm grid `alarm_here`/
  `alarm_here2` operate on (`simant_data_group[0x52D2..)`), snapshotting into
  a scratch copy at `[0x4AD2..)` first (mirrored byte-for-byte even though
  nothing else reads it, to stay diffable over the ASM's actual touched
  region) then computing `(4*center + sum_of_in_bounds_neighbours) >> 3` per
  cell, storing 0 when that's <= 8. The initial port guessed the wrong
  formula (`(center + 4*sum) >> 1`) from disassembly alone — a uniform-input
  (`0xFF` everywhere) state-diff case caught it immediately (real ASM output
  was 0xBF/0xDF/0xFF for corner/edge/interior, not the guessed formula's
  values), so the fix was derived by solving the three observed constants
  directly rather than re-reading the trace: `4*255+2*255=1530, >>3=191=0xBF`
  (corner, 2 neighbours), `+3*255 -> 1785,>>3=223=0xDF` (edge), `+4*255 ->
  2040,>>3=255` (interior, exactly preserved — the tell that centre weight
  is 4/8 not 1/8). 6 state-diff cases green (uniform, ramp, sparse, checker,
  and a low-value case exercising the <=8 snap threshold).
- RECOVERED `flood_nest_b` (seg5:29DA, no args, FAR return): floods the black
  colony's nest map plane 2 (`dgroup[0x48E8..)`, rows 0..63, cols 3..63 only
  — cols 0..2 are never touched): dirt-band tiles (0x20..0x2D) bump by 0x31
  into the flooded-dirt band (0x51..0x5E); nest-food/floor tiles (<=0x13)
  become the canonical hole tile (0x50); everything else is untouched. Pure
  single-DGROUP transform, ported clean on the first pass (no formula bugs).
  Confirmed no `_FloodNestR` sibling exists in the seg5 symbol table — this
  one's genuinely colony-B-only, not a missing-twin gap.
- Harness fix: bumped the `_run_and_diff`/`_run_and_diff_segs`/
  `_run_and_get_ax` step budget from 50,000 to 200,000 — both new routines'
  full 64x(32 or 61) nested-loop sweeps genuinely need more than 50k CPU
  steps to reach their return sentinel (`_FloodNestB` ~51k, `_SmoothAlarm`
  ~98k); this isn't a correctness bug like cont.82/83, just headroom, and
  raising it doesn't change behavior for any faster-returning existing test.
- Suite: simant 938 (+12 from this stretch). Continuing per /goal — next up
  per the survey: `_TileCanBeMovedOn` (seg5:9342, 7 args, FAR) is the unlock
  for `_GetMyBestDirs`/`_GetMyRandDirs`/`_GetRedBestDirs` (the yellow-ant/
  red-colony pathfinding tier one level below the `_Do*Ant*` behaviors); its
  `plane<=1` branch is fully pinned (mirrors `is_valid_a` + a world-flag-gated
  tile threshold already seen in `is_not_barrier`) but the `plane>1` branch's
  3 extra coordinate args + boolean flag need one more disassembly pass
  before porting byte-exact.

## 2026-07-14 (cont.83) — /goal grind: _TallyModePop (chained mutator-calls-mutator)
- RECOVERED `tally_mode_pop` (seg6:038E, no args): rolls up 12 mode-population
  tally fields (all PACK-resident — confirmed all 4 "world selector" globals it
  reads resolve to PACK) into an 11-field summary structure, then conditionally
  invokes `make_red_initiator` when `pack[0x7C0A]` (signed) is < 1.  Confirmed the
  call target precisely by decoding the raw relative-call bytes by hand (the
  disassembler had garbled it as `0xffff967c`) — a `push cs; call rel16` NEAR-call
  construct whose target (0x967C) is `_MakeRedInitiator`, which itself ends in a
  FAR `retf`; the manually-pushed CS makes the mismatched near-call/far-return
  ABI work (the callee is normally called far from elsewhere too).
- Hit and fixed a SIMPLE instance of the SAME class of harness mistake from
  cont.82: forgot `near=True` for this near-`ret` routine, so the completion
  check compared against the wrong CS and ran the CPU straight into garbage
  past the intended return.  Caught immediately by tracing raw CS:IP in
  isolation before assuming a logic bug — a useful diagnostic pattern to reuse.
- 4 state-diff cases green, including TWO where the gate genuinely fires and
  `make_red_initiator` executes for real through the chained near-call/far-
  return — verifying a mutator invoking ANOTHER already-recovered mutator, not
  just leaf routines in isolation.
- Suite: simant 926.  This closes out the mode-population/red-initiator
  subsystem entirely (ClrModePop + TallyModePop + MakeRedInitiator all
  recovered and cross-verified).  Continuing per /goal.

- Recovered 5 more routines this stretch (`_MakeRedInitiator`, `_ClrModePop`,
  `_FillHolesBN`/`RN`, and confirmed `_TallyModePop`/`_DrownBList` neighbors) —
  but while writing `_MakeRedInitiator`'s test, caught a SERIOUS bug in the
  test-harness pattern used across ~13 prior commits this session.
- THE BUG: `_run_and_diff_segs`/`_run_and_get_ax` each create their OWN internal
  `runtime.create_machine()`.  Every test that did `m = runtime.create_machine();
  m.mem.wb(m.seg_bases[...], off, val); results = _run_and_diff_segs(...)` was
  seeding a THROWAWAY machine — `create_machine()` returns an INDEPENDENT memory
  image each call (verified empirically: writes to one instance never appear in
  another), so the ASM/recovered comparison ran against UNSEEDED (mostly-zero)
  boot data instead of the intended parametrized values.  This did NOT invalidate
  the core state-diff PROOF (both ASM and recovered code still ran against the
  SAME real starting state, so a passing test still proved byte-exact agreement
  for THAT state) — but it meant most parametrized "different x/y/caste/etc."
  test cases silently collapsed onto the SAME default data, so test coverage was
  far narrower than the test names/parameters claimed.
- FIX: added a `seed_fn(m)` hook to `_run_and_diff_segs` (mirroring
  `_run_and_diff`'s existing correct pattern) and `_run_and_get_ax`, which runs
  against the function's OWN internal machine before the pre-state snapshot.
  Rewrote EVERY affected test (~19 of them: SetMyHealth, DecEatB/R, KillTailB/R,
  ColonySmell x4, JamScent x4, AlarmHere/2, AddAntTo*List x3, DropFoodB/R,
  RemoveFromAList, ClrModePop, FillHoles x2, DrownList x2, ClearList x2,
  KillSpider, CompactList x3, SetAntIndex) to seed via `seed_fn` instead.
- THE PAYOFF: re-running with CORRECTED seeding immediately caught a REAL LOGIC
  BUG in `_fill_holes` (fill_holes_bn/rn) — I had the branch condition EXACTLY
  BACKWARDS (jam-to-0xFF when hole intact / clear-to-0 when filled, when the ASM
  actually does the OPPOSITE: clear-to-0 while the hole is STILL OPEN, jam-to-
  0xFF once it's FILLED IN).  Fixed and reverified byte-exact.  This validates
  the whole exercise: a broken harness would have LET this bug through
  silently forever (both sides trivially "matched" on unseeded zero data).
- Also recovered along the way: `_MakeRedInitiator` (convert an eligible yard
  ant to a red-colony initiator, gated on the black colony's hunger reset-rate),
  `_ClrModePop` (reset mode-population tally arrays).
- Suite: simant 922 (237 in test_state_diff.py alone).  ALL PACK/SIMANT_DATA_GROUP
  seeding across the whole file now correctly reaches the machine the ASM
  actually runs on.
- LESSON for future work: when adding a new `_run_and_diff_segs`/
  `_run_and_get_ax` test, ALWAYS use `seed_fn=`, NEVER seed a separately
  constructed `m`.  Consider auditing test_hooks.py's older patterns too if a
  similar double-machine mistake seems possible there (not yet checked).

- RECOVERED `drown_b_list`/`r_list` (seg5:2D16/2D66): sweep a colony's list
  BACKWARD for ants standing at a given X column, marking them "drowning"
  (field 0x3B22/0x44F0 <- 0x11) when their caste's bits [6:3] (a 4-bit
  sub-field, `(caste & 0x78) >> 3`) fall in 1..11.  Pinned the exact boundary
  behavior (sub=0xB included, sub=0xC excluded, sub=0 excluded, dead
  caste=0 always skipped) — flood-response logic for the water/drop-water
  system recovered earlier.  10 cases green.
- RECOVERED `clear_list_b`/`r` (trivial count resets) and `kill_spider` (reset
  3 PACK fields: mode->5, health/timer->500, a third field->0).  7 cases green.
- Suite: simant 910.  ~20 gameplay routines recovered so far this /goal
  session across 12 commits, all pushed.  Continuing per /goal.

- RECOVERED `compact_list_a`/`b`/`r` (seg5:2A16/2A7A/2ADE, no args): a SECOND,
  DIFFERENT deletion strategy from `remove_from_a_list` — a single-pass sweep
  that removes EVERY entry whose caste field is 0 (dead/empty) at once, using a
  running (<=0) hole-counter to shift surviving entries into the gaps, then
  subtracts the total hole count from the list's count.  Unlike
  `remove_from_a_list`, this does NOT touch the life grid (the caller is
  expected to have already cleared it when marking an entry dead by zeroing its
  caste — a different removal PROTOCOL: mark-then-sweep vs remove-in-place).
  All three list flavors confirmed byte-identical in structure (just different
  field bases), matching the established A/B/R symmetry throughout this
  recovery.  15 state-diff cases green (scattered holes / all-dead / no-dead /
  empty / edge-first).
- MILESTONE: the ant-list subsystem is now comprehensively recovered — both
  deletion strategies (single-slot remove-in-place, bulk mark-and-sweep),
  Create/Read/Update, plus the whole scent/alarm pheromone system and colony
  hunger clocks.  16 new gameplay routines recovered this /goal session (on top
  of the pre-existing map/predicate/pathfinding foundation).
- Suite: simant 893.  Continuing per /goal — next: reassess the leaf queue for
  remaining self-contained candidates, or consider moving up a tier now that
  the ant-record data structure and its full CRUD are understood (a real
  `_Do*Ant*` behavior routine is now much more tractable, since its list/scent
  dependencies are largely in place).

- RECOVERED `remove_from_a_list` (seg5:2B42): remove the ant at `slot`, closing
  the gap.  Clears the removed ant's life-grid cell FIRST (using its recorded
  position before the fields are overwritten), decrements the count (floored
  0), then shifts every field array's tail down by one via a byte-exact copy.
- The ASM calls a SHARED far-memcpy helper (seg7:783E) 5x — recognized it as a
  standard word+odd-byte `rep movsw`/`rep movsb` memcpy (the generic C runtime
  helper, not SimAnt-specific logic), so it's NOT separately "recovered" as its
  own routine — the Python byte-by-byte shift is OBSERVABLY identical to its
  word/byte-pair optimization, and it's exercised for REAL (not stubbed) in the
  state-diff harness since it performs genuine sim-state mutation, not a
  rendering side effect.  Verified overlap-direction safety (source ahead of
  dest, increasing-address copy — matches Python's read-full-tail-then-write
  approach).  4 state-diff cases green (covering slot=0/mid/last/single-elem).
- MILESTONE: the ant-list CRUD is now fully recovered — Create (add_ant_to_*),
  Read (find_in_*), Update (set_ant_index), Delete (remove_from_a_list for A;
  B/R deletion likely via _CompactListB/R, not yet recovered — A's list uses
  remove-in-place, B/R may use a different compaction strategy, worth checking
  next before assuming symmetry).
- Suite: simant 878.  Continuing per /goal.

- RECOVERED `get_smell_t` (seg6:9612): the READ side of the trail-scent grid —
  reads a per-direction delta from two small tables in SIMANT_DATA_GROUP (read
  LIVE, not hardcoded, since they're genuine game data) and returns the scent
  value at (p,q)+delta on the same 0x6AD2/0x7AD2 grids the whole scent family
  covers.  This is the pheromone-sensing primitive ant AI uses to pick a
  direction.  Pure predicate, return-value A/B, 7 cases green.
- RECOVERED `set_ant_index` (seg5:584A): a UNIFIED "overwrite an EXISTING ant
  record's fields at a slot" dispatcher across all three lists (list_type<=1
  ->A, ==2->B, else->R — the SAME plane-numbering convention as
  MAP_PLANE_BASE/LIFE_PLANE_BASE, confirmed from the ASM's own dispatch, not
  assumed).  Unlike add_ant_to_*_list this does NOT append, touch the life
  grid, or change the count — a genuine "update" (bounds-checked 0<=slot<count,
  no-op otherwise) completing the C(reate)/R(ead)/U(pdate) trio for the ant-list
  data structure (find_in_*_list=R, add_ant_to_*_list=C, set_ant_index=U).
  25 state-diff cases green (all 3 list-type dispatches x bounds edge cases).
- Suite: simant 874.  Continuing per /goal — next: the Delete side
  (_RemoveFromAList, _CompactListA/B/R) to close out the CRUD story, then
  reassess candidates toward the behavior tier.

- RECOVERED `dec_t_smell` (seg6:95B6): single-cell decrement (guarded nonzero)
  of a colony's TRAIL scent grid — confirmed it operates on the EXACT SAME
  64x32 grids `jam_scent_bt/rt` and `colony_smell_decay_bt/rt` already cover
  (red @0x7AD2, black @0x6AD2), closing out the scent-system recovery.
- RECOVERED `drop_food_b`/`drop_food_r` (seg6:3C3C / 6242): grow a food pile on
  the map (tile <0x10 -> set to 0x10; tile <0x13 -> +1; already-0x13 -> no
  change), unconditionally followed by bookkeeping: increments a "total
  dropped" counter in PACK, then clears bit 0x08 of the ACTING ant's caste byte
  in SIMANT_DATA_GROUP.  DISCOVERS `pack[0x9B6A]` as a shared "which ant is
  dropping" context slot the caller sets — reused identically by both colonies
  (confirmed: both B and R read the SAME offset).  Also confirms the caste
  field (0x3D18/0x46E6, from kill_tail_*/find_in_*list/add_ant_to_*_list) is a
  BIT-PACKED byte, not a plain enum — bit 0x08 = "carrying food", cleared here.
  18 new state-diff cases green (6 dec_t_smell + 12 drop_food).
- Suite: simant 842.  Continuing per /goal.

- RECOVERED the insert side of the ant lists (seg5:2EF0/2F4A/2FA4): append a new
  ant record at the current count (capped at 1000/A, 500/B, 500/R — jge skip, a
  full list is a silent no-op), writing the SAME per-ant arrays discovered
  across kill_tail_*/find_in_*_list, PLUS TWO NEW per-ant fields each (0x2B78/
  0x334C for A, 0x3B22/0x3F0E for B, 0x44F0/0x48DC for R — meaning not yet
  confirmed, documented honestly as such).  Also stamps the caste value into the
  ant's LIFE-GRID cell (plane 0/2/3 matching A/B/R) — the SAME arithmetic
  kill_tail_[br] uses in reverse (add vs clear).  Increments the count last.
- Confirmed the per-ant array's own x/y-role convention (the *64 term vs the +1
  term) is CONSISTENT across all three lists and matches find_in_*_list's arg
  order exactly (arg1=[bp+6] is always the *64 term, arg2=[bp+8] the +1 term) —
  cross-checked against kill_tail_b's naming, which uses the SAME (if
  map_cell_offset-inverted) convention; no naming bug, just a locally-consistent
  scheme distinct from the map grid's own x=*64/y=+1 convention.
  18 state-diff cases green (including the exact list-full boundary: count ==
  cap-1 succeeds, count == cap and cap+1 both no-op).
- Suite: simant 824.  Continuing per /goal — the ant-list CRUD is now largely
  recovered (find + add); next: survey what's left in the leaf queue (the
  DecTSmell/DropFoodB/DropFoodR family, or move up a tier toward
  _DoAntSim/_DoForageAnt now that most of their leaf dependencies exist).

- RECOVERED the three ant-list search predicates (seg5:2C42/2C86/2CCE): search
  the yard/black/red ant lists BACKWARD (highest slot first — last-added wins on
  a tie) for a slot whose recorded fields match, using the SAME per-ant arrays
  discovered in cont.73 (kill_tail_b/r): _FindInBList/RList match (Y,X,caste)
  against the exact array offsets kill_tail_[br] reads/clears; _FindInAList
  matches a DIFFERENT array set (0x23A4/278E/2F62, the yard "A list", 2-value +
  nonzero-flag match).  All three read their live count from PACK
  (0x80F0/0x99D4/0x72CC) via the SAME "world selector" indirection pattern as
  every other DGROUP pointer-global found so far.
- Pure read-only predicates -> proven via return-value A/B (not state-diff):
  seed one machine, run the ASM to return, capture AX, then feed the SAME
  seeded machine's PACK/SIMANT_DATA_GROUP data to the recovered function and
  compare.  Caught and fixed a bug while writing this: an early draft created
  TWO SEPARATE machines (seeded one, ran ASM on an unseeded other) and used a
  hacky global-mutable-state memory view — replaced with one seeded machine
  shared by both the ASM run and the recovered-fn comparison, and a proper
  `m.mem.block(seg, 0, 0x10000)` extraction (the segment-translation-safe
  pattern already used elsewhere in this file) instead of indexing `m.mem.data`
  directly with a raw selector value (selectors need translation, not linear
  offsets).  13 cases green.
- Suite: simant 806.  Continuing per /goal — next: _AddAntToAList/BList/RList
  (the INSERT side of these lists — likely the next state-diff mutators, and
  the natural companion to what's just been recovered).

- RECOVERED the whole pheromone/alarm subsystem `_DoForageAnt`/`_DoRandAntA` feed
  on: `_ColonySmellB/RN`, `_ColonySmellB/RT`, `_JamScentB/RN`, `_JamScentB/RT`,
  `_AlarmHere`, `_AlarmHere2` (seg6:92AA/92D8/9306/9344/94B6/94F6/9536/9576/
  943C/947E).  All operate on SIMANT_DATA_GROUP-resident 64x32 half-res grids:
    - Colony smell (NEST): linear -1/tick decay, floor 0 (grids @0x62D2 B /
      0x72D2 R) — a guarded nonzero check (naive dec would underflow to 0xFF).
    - Colony smell (TRAIL): exponential halving decay `v - (v>>1)`, snapping to
      0 below 8 (grids @0x6AD2 B / 0x7AD2 R) — a visibly different decay curve
      from the nest variant, confirmed from the ASM branch structure, not
      assumed.
    - JamScent: "set if greater" at cell `((x&0xFFFE)<<4)+(y>>1)` on the SAME
      grids the ColonySmell family decays (shared base offsets, confirmed).
    - AlarmHere: add a delta to a cell (grid @0x52D2), clamped to <=200 but
      with NO LOWER CLAMP — a large negative delta wraps the stored byte
      (matches the ASM's cmp/jg exactly: only checks the upper bound before
      the byte-truncating store).  AlarmHere2: "set if not less".
  All ants-index/coordinate math done via a shared `_sx16` signed-word helper
  (added to gameplay.py) for exact SAR/CMP fidelity across the full word range.
- HARNESS FIX: found and fixed a real gap — `_run_and_diff_segs` always pushed
  a FAR return frame (CS+IP), but most of this family returns via a NEAR `ret`
  (pops IP only; CS unchanged) since they're called from OTHER seg6 routines in
  the SAME segment.  Added a `near=True` mode (push IP only; completion check is
  IP-only, CS assumed unchanged).  _JamScentBT is the one FAR-retf outlier in an
  otherwise-near family — confirmed per-routine from the actual ASM ending, not
  assumed uniform.
- 44 new state-diff cases green.  Suite: simant 793.
- Continuing per /goal.  These are DIRECT AI inputs (_DoForageAnt/_DoRandAntA
  read the smell grids via _GetSmellT-family accessors, not yet recovered) —
  next candidates: _FindInAList/BList/RList (ant-list search, read-only —
  likely predicate-tier, quick), then _AddAntToAList/BList/RList (list insert).

- RECOVERED `kill_tail_b`/`kill_tail_r` (seg6:42B0 / 6762): clear an ant's
  has-tail flag and clear the corresponding life-grid cell.  DISCOVERS the
  per-ant record layout in SIMANT_DATA_GROUP: parallel flat arrays indexed by
  ant_idx as a RAW BYTE offset (not scaled) — an X-coordinate byte array
  (0x392C B / 0x42FA R), a Y-coordinate word array where only the low byte
  matters (0x3736 B / 0x4104 R, so consecutive ants' "words" overlap by one
  byte — read-and-mask, harmless, replicated exactly), and a has-tail flag byte
  array (0x3D18 B / 0x46E6 R).  The life-grid write itself has NO ES override in
  the ASM (defaults to DS=DGROUP), unlike the per-ant field reads (ES=
  SIMANT_DATA_GROUP) — confirms life planes 2/3 are DGROUP-resident (matches the
  bridge's existing LIFE_PLANE_BASE) while the ant records live in a separate
  fixed segment.  No bounds check on the recorded (x,y) in the ASM — replicated
  exactly (no clamping in the recovered fn either).
  10 state-diff cases green.  Suite: simant 749.
- Continuing per /goal — next: the scent/alarm system (_JamScentBN/RN/BT/RT,
  _ColonySmellBN/RN/BT/RT, _AlarmHere/_AlarmHere2) that _DoForageAnt/_DoRandAntA
  call directly for pheromone-trail AI.

- User set an autonomous /goal: continue core game logic recovery until stopped
  or genuinely blocked.  Surveyed all un-recovered seg5/seg6 routines ranked by
  subcall count to find the next batch of clean mutators for the state-diff
  oracle (bypassing the oversized _DigTileB for now).
- RECOVERED `dec_eat_b`/`dec_eat_r` (seg6:48F8 / 6C6A) — the two colonies' food
  hunger-decay clocks (tick a countdown timer; on expiry, reset it to
  reset_rate>>5 and starve the colony by 1 food unit).  Confirms and extends the
  DGROUP/SIMANT_DATA_GROUP/PACK field layout found fixing _SetMyHealth:
  DGROUP holds parallel per-colony fields (0xAC82/0xAC84 = reset rate B/R,
  0xAC86/0xAC88 = food supply B/R, 0xAC8A = player health); PACK holds the
  countdown timers (0x7402 B, 0x7C8E R) and the earlier player-status fields;
  SIMANT_DATA_GROUP:[0x8A60] is a "no-starve" cheat flag gating ONLY the B path
  (asymmetric — R has no such gate, confirmed from the ASM, not assumed).
  28 state-diff cases green (both colonies, all timer/rate/food edge cases).
- Suite: simant 739.  Continuing the grind per the /goal — next: _KillTailB/R,
  then the scent/alarm system (_JamScentBN/RN/BT/RT, _ColonySmellBN/RN/BT/RT,
  _AlarmHere/_AlarmHere2) that feeds the _DoForageAnt/_DoRandAntA AI directly.


## 2026-07-14 (cont.71) — CORRECTNESS FIX: _SetMyHealth spans 3 fixed NE segments, not 1
- Scouting the dig chain (_DigTileB) found it far bigger than expected (running
  statistical accumulators via __aFuldiv, not just a tile write) — deferred.
  Surveyed cleaner mutators instead and found a real bug in the LAST session's
  work while picking the next one.
- BUG FOUND: `_SetMyHealth`'s `es:[0x8a5e]`/`[0x9cf0]`/`[0x9bec]`/`[0x9af2]`
  reads/writes go through DGROUP pointer-globals ([0xC49A]/[0xC49C]/[0xC49E]/
  [0xC4A0]) that at REAL boot resolve to TWO OTHER fixed NE data segments —
  `SIMANT_DATA_GROUP` (NE seg 8) and `PACK` (NE seg 9) — not DGROUP.  My
  recovered `set_my_health` had folded all fields into one flat DGROUP view; the
  state-diff test only passed because it artificially pointed those selectors AT
  DGROUP for both the ASM run and the recovered fn (tautological, not faithful).
  Confirmed by exhaustive scan: no instruction in seg1-7 ever WRITES those
  pointer-globals — they are load-time-relocated constants (a compiler "based
  pointer" idiom for cross-segment data), permanently fixed, exactly like DGROUP
  itself.  (The existing PREDICATE islands — _IsItFood etc. — are unaffected:
  they dynamically follow the selector at runtime in both ASM and island, so
  their byte-exactness proof holds regardless of what the selector points to.)
- FIXED: added `hooks.SIMANT_DATA_GROUP_SEG_INDEX`(8) / `PACK_SEG_INDEX`(9).
  `set_my_health(dgroup, simant_data_group, pack, new_health)` now takes one
  view per real segment.  Added `_run_and_diff_segs` (multi-segment state-diff:
  N (seg_index, lo, hi) windows, seeded/diffed against their REAL fixed-segment
  addresses, not artificial ones).  18 cases still green, now faithfully.
- Suite: simant 718.  Lesson for future mutators: a `mov es, word ptr [Gxxxx]`
  world-state global should be checked against a fresh boot (does it resolve to
  DGROUP, or another fixed segment?) before assuming a flat DGROUP view — grep
  all 7 code segments for writers to the global to confirm it's load-fixed, not
  dynamically reassigned.


## 2026-07-14 (cont.70) — state-diff oracle threads RNG; recover _DropWater
- RECOVERED `_DropWater` (seg5:0C54), the third mutator and a new axis: RNG
  threading.  It flows/evaporates the water column at Y=x across nest planes 2/3
  — for each of 64 rows, a source tile (0x4E) becomes _SRand1(8) (advancing the
  shared LFSR seed at 0xCBF2), any other tile drops by 0x2F.
- The oracle now proves RNG determinism THROUGH a mutator: the ASM calls the real
  _SRand1 (which mutates the seed = sim state, so it is NOT stubbed — only the
  redraw is), and the recovered drop_water advances the recovered LFSR (simone.
  srand1) identically.  Seed + 128 map cells match byte-exact across seeds.
- Harness gained a seed_fn callback (arbitrary byte/region seeding) and a bigger
  step budget for loop mutators.  5 cases green.  Suite: simant 718.
- Oracle coverage now: ds-direct + selector-indirect, single + multi-field, and
  RNG-threaded.  Next: the dig chain (_DigTileB -> _MakeNewHoleB -> _DigMyTile),
  recovered bottom-up, toward a real ant behavior.


## 2026-07-14 (cont.69) — state-diff oracle generalized; recover _SetMyHealth
- Generalized the state-diff harness to the correct design: run the ASM mutator
  over the REAL DGROUP (side calls stubbed), then apply the recovered Python
  mutator to a COPY of that same pre-state and require byte-identical images.
  Both start from the identical pre-state, so only the mutation must match —
  fixes the earlier fresh-bytearray approach (which failed on all the real game
  data).  Diff excludes the top stack band (ss==ds==DGROUP for a small-model app,
  so the call frame lives at the top of DGROUP; sim state is all < ~0xC4A0).
- RECOVERED `_SetMyHealth` (seg5:8C70) — the second mutator, and a harder one:
  multi-field + SELECTOR-INDIRECT.  God mode -> 100; alive clears the dead flag
  ([0x9CF0]); clamp 0..100; store at [0xAC8A]; set a damage/heal flag ([0x9AF2],
  0 only when actually healing: old [0x9BEC] < new and new >= 10).  The test
  points the world-state selector globals ([0xC49A..0xC4A0]) at DGROUP and seeds
  the read fields.  Pure set_my_health(view, new_health) on a bridge word view;
  18 cases green (god / clamp / heal-vs-damage / negative-as-word).
- Suite: simant 713.  Oracle now proven on ds-direct AND selector-indirect,
  single- AND multi-field mutation.  Next: a mutator with RNG (_DropWater uses
  the recovered _SRand1) or start up the dig chain (_DigTileB -> _DigMyTile).


## 2026-07-14 (cont.68) — STATE-DIFF ORACLE bootstrapped; _SetMap (first mutator)
- Built the verification tier for MUTATING routines (the seg6 behavior layer needs
  it — return-value proof doesn't apply when a routine changes world state).
  Harness (simant/tests/test_state_diff.py): seed the sim-state arrays, run the
  ORIGINAL ASM with its screen-redraw far-call STUBBED (a no-op far return, so only
  sim state changes), then diff the resulting DGROUP map region against the
  recovered Python mutator applied to the same seed.  Byte-identical delta =
  byte-exact.
- The redraw side call is ubiquitous on write-side routines: _SetMap / _SetLife /
  _ClearLife all `lcall 0x18C0:0` = ANTEDIT!_ZapEuMapAt (draw the changed tile).
  Stubbing it is the key that makes sim-state diffing clean.
- RECOVERED `set_map` (seg5:617A), the write twin of get_map: writes value's low
  byte at map_cell_offset(plane,x,y) when in range, else no-op.  Operates on a
  bridge ByteBackend (the state-view seam) — the shape a native backend uses.
  10 state-diff cases green (valid/all-planes/out-of-range/byte-truncation).
- Suite: simant 695.  This harness now unlocks the mutating behavior tier; next
  is a real behavior (start small — e.g. a food pickup/drop) verified by state
  diff, growing toward _DoAntSim.


## 2026-07-14 (cont.67) — _GetBestDir: FIRST BEHAVIOUR routine (return-value tier)
- RECOVERED `_GetBestDir` (seg6:405E), the ant pathfinding core — the routine that
  composes the recovered movement predicates.  For each of 8 neighbours it scores
  the squared distance to the target (get_dis) and keeps the closest that is
  passable (is_not_obstacle), not a pebble (is_this_pebble), strictly closer;
  prefers a clear cell (is_clear_tile), else an occupied/blocked fallback.  ALL 6
  of its sub-calls were already recovered — this is the payoff of the predicate
  grind.
- METHODOLOGY SHIFT (behaviour tier): _GetBestDir has 7 interleaved sub-calls per
  iteration, so a full-register-residue island is impractical.  Recovered instead
  as clean source (recovered/gameplay.get_best_dir) and verified against the ASM's
  RETURN VALUE — what its callers read — over seeded scenarios (clear paths,
  obstacle/pebble/occupied direct dir, both planes, grid edges).  16 cases green
  first try.  This is a NEW, deliberately-labelled recovery category for
  behaviours (return-value proof, not a lifted island); the pure fn is exactly
  what a native sim tick calls.  No island installed (count stays 69).  Suite 685.
- Tiers now: leaves + predicates + looping composites = byte-exact islands;
  behaviours = source + return-value.  Next behaviours: _DoForageAnt, _DoNestAntB,
  _DoDigInB (each composes recovered predicates + RNG + list ops) toward the
  sim-tick a native backend runs.

## 2026-07-14 (cont.66) — _IsClear3x3: first looping composite recovered
- RECOVERED `_IsClear3x3` (seg5:5AD2): the centre + 8 neighbours (offsets from the
  DGROUP direction tables at [0xC478]/[0xC47A]) must all be clear per the recovered
  is_clear_tile.  First routine that CALLS a recovered routine in a LOOP — the
  island reimplements the 9-iteration loop over the real dir tables and threads the
  LAST _IsClearTile call's residue through it (dx=plane; cx=that call's cx; bx
  sticky — only a valid cell sets it; es=dir-table selector once a neighbour ran).
- The A/B oracle caught a real bug: the direction offsets are SIGNED BYTES (0xFF =
  -1, the ASM's cbw), which I'd sign-extended as 16-bit words — neighbours landed
  off-grid.  One fix, 9 cases green (all-clear / centre+each-neighbour blocked /
  both planes / grid corners).  Islands 68 -> 69.  Suite: simant 669.
- Gave the A/B harness's _step_to_return a step budget so it can drive routines
  that call sub-routines in a loop (9x _IsClearTile ~= 500 instrs > the old 200).
- The loop-composite pattern (reimplement the loop, thread the last inner call's
  residue) now works — a stepping stone toward the behaviour layer (_GetBestDir
  etc. loop over directions calling the recovered helpers).

## 2026-07-14 (cont.65) — _IsItAHole recovered; map/life predicate tier complete
- RECOVERED `_IsItAHole` (seg5:9B4A): plane<=1 tail-calls _IsItHole (so the island
  reproduces _IsItHole's residue exactly); plane>1 is a yard-plane hole (top row
  y==0, tile 0x18).  Intricate multi-case residue (y>0/coord-invalid/plane>3/valid)
  green first try.  Islands 67 -> 68.  Suite: simant 660.
- TIER BOUNDARY: the self-contained map/life-query PREDICATE family is now done —
  _IsNotObstacle, _IsClearTile, _IsValidLocation, _IsItDigable, _IsItAHole (+ the
  earlier leaf predicates).  What remains is a step-change in complexity, each a
  bigger careful slice:
    * _IsItYellow (5:96B6): 2 modes (a _GetDis proximity test vs a life read), the
      yellow-ant position globals [0xAC7C/E], a mode flag es:[0x9FE8].
    * _IsClear3x3 (5:5AD2): 9x _IsClearTile via DGROUP direction tables; residue =
      the last inner call's.
    * _TileCanBeMovedOn (5:9342): 7 args, many map reads, cross-arg logic.
    * behaviors (_DoForageAnt/_DoNestAntB/_GetBestDir...): many subcalls, RNG, ds
      swaps — the sim-tick layer that a native backend ultimately needs.
    * writers _SetMap (5:617A) / _SetLife (5:5D18): side-effecting redraw far-calls
      (partial islands — invoke the sub-call through the VM).

## 2026-07-14 (cont.64) — autonomous grind: 3 more compound map-query routines
- Continued the compound map-query grind (oracle-guided residue).  Recovered:
    _IsClearTile   (5:5B2C) map passable + no blocking ant (life not in {0,FE,FF});
                   composes map_cell_offset + life_cell_offset.  Green first try.
    _IsValidLocation (5:56DA) plane-aware validity (is_valid_a/is_valid_b); dx=ax.
    _IsItDigable   (5:95C6) yard dig = dirt (delegates _IsItDirt) or grass 0x1C..0x1F.
                   Oracle corrected the two out-of-range residues (coord-invalid
                   returns early bx=0/dx=x; plane>3 reaches the grass sbb, dx=0).
- With _IsNotObstacle (cont.63) this completes ALL of _GetBestDir's leaf deps
  (_GetMap/_GetDis/_GetLife/_IsThisPebble/_IsNotObstacle/_IsClearTile) — the
  pathfinding helper is now recoverable.  Islands 62 -> 67.  Suite: simant 638.
- The oracle makes compound residue tractable: derive best-effort, one guided fix
  per miss.  Remaining are harder (sub-call-residue chains / direction tables /
  ds-swapping behaviors): _IsClear3x3 (9x _IsClearTile via dir tables), _IsItAHole
  (delegates _IsItHole), _TileCanBeMovedOn (7 args), _GetExitDirB (RNG + ds swap).

## 2026-07-14 (cont.63) — recovered _IsNotObstacle (first compound map-query)
- RECOVERED `_IsNotObstacle` (seg5:94C6), the first COMPOUND map-query predicate
  and a `_GetBestDir` (pathfinding) dependency.  It composes the recovered map
  addressing (map_cell_offset + a DGROUP tile read) with the world inside flag
  (hardcoded selector 0x5EF3, the value DGROUP:[0xC320] holds).  Clear when:
  nest planes (plane<=1) tile <= 0x5F inside / <= 0x53 outside; yard planes
  (plane>1) tile <= 0x18 OR pebble (0x30..0x31); out-of-range = obstacle.
- The intricate part was the residue across ~6 exit paths; the A/B oracle guided
  it in one iteration — it flagged that es (=0x5EF3) is set ONLY on the nest
  branch (plane>1 never reads the flag), which I'd wrongly set unconditionally.
  Final residue: bx=ax; cx=tile/0xFFFF; dx = plane / 1 (pebble) / 0 (obstacle) /
  0xFFFF (invalid); es = world selector only on the nest path.  27 A/B cases.
- Islands 63 -> 64.  Suite: simant 573.  Pattern proven for the remaining
  compound map/life routines (_IsClearTile, _TileCanBeMovedOn, _IsItAHole): the
  oracle makes the residue tractable, one guided fix at a time.

## 2026-07-14 (cont.62) — SaveAs dialog fully brought up (list box + DlgDirList)
- Continued the SaveAs frontier by REPLAYING cold4 past the WM_SETREDRAW fix to
  OBSERVE (deterministically, no resume artifact) what the dialog needs next.
  Walked the whole chain in one session, each gap confirmed by re-observing:
    1. DlgDirList (USER.100) — fill the list box with the dir listing (the twin of
       DlgDirListComboBox; both now share _fill_dir_control).
    2. LB_GETTEXT / LB_GETCOUNT + the item-list family — a new ListBox branch in
       SendDlgItemMessage mirroring the ComboBox one (same items/sel store).
    3. InvalidateRect on a control HWND (from GetDlgItem) — no-op for non-Window
       handles (our dialog UI paints on demand) instead of a Window-only crash.
- RESULT: the SaveAs dialog now opens, populates + reads its file list, and
  reaches the wait-for-user-event state (DemoEnded — cold4 has no dialog input).
  Fully functional.  win16_re 0418984 (bumped).  Suites: win16_re 129, simant 547.
- METHOD note: replaying the recorded demo past a fix is the clean way to observe
  the next gap on the SAME input path — beats resuming a mid-callback crash
  snapshot (which artifacts on the half-built dialog handle).

## 2026-07-14 (cont.61) — SaveAs dialog frontier: WM_SETREDRAW to a list box
- FRONTIER (owner's `--record cold4 --no-hooks`): the SaveAs file dialog gapped
  at instr 187,559,268 — `SendDlgItemMessage ListBox msg 0x000B` (control 404)
  during WM_INITDIALOG.  0x000B = WM_SETREDRAW (disable repaint while the list box
  is bulk-populated, then re-enable).
- FIXED (win16_re 25bc492, bumped): handle WM_SETREDRAW before the per-class
  dispatch in SendDlgItemMessage as a benign no-op (our dialog model paints on
  demand) for any control.  win16_re 129, simant 547.
- BONUS confirmation: crash_081759 recorded its callback frames
  ([DispatchMessage, DialogBox]) — the cont.59 frame-preservation fix works, so a
  mid-DialogBox crash snapshot is now resumable enough to observe the gap.
- COULDN'T cleanly observe the SaveAs list box's NEXT messages: resuming past
  WM_SETREDRAW hits `handle 0176 is not a dialog` (a resume artifact — the dialog
  was mid-creation at snapshot time, so its object graph isn't fully rebuilt).
  Per the charter (implement observed behaviour), the list box populate/read
  messages (LB_RESETCONTENT / LB_ADDSTRING / LB_DIR / LB_GETCURSEL / LB_GETTEXT)
  await the next fresh run that reaches the dialog — likely the next frontier.

## 2026-07-14 (cont.60) — recovered _IsThisFood; completes the _IsThis* family
- RECOVERED `_IsThisFood` (seg5:5F04): plane<=1 tail-calls the recovered
  `_IsItFood` (world-state driven); plane>1 is the yard nest-food band 0x10..0x13.
  Pure is_this_food(plane, tile, inside); the island replicates _IsItFood's
  residue on the nest path (dx=tile, es=world selector) and dx=result on the yard
  path.  Completes the _IsThis* classification family (egg/grass/pebble/food).
  18 A/B cases green.  Islands 62 -> 63.  Suite: simant 547.
- NOTE: re-synced after a prior compacted context — _GetDir/_GetDis/_SGetDis/
  _GetLife + the bridge/dgroup_view.py state seam were already recovered.  The
  clean-leaf phase is nearly done; the remaining sim routines (_IsNotObstacle,
  _IsClearTile, _IsItAHole, _TileCanBeMovedOn) are COMPOUND (multi-plane map+life
  reads, sub-call residues, a hardcoded world selector 0x5EF3) — each a careful
  map/life-reading island, the next phase.

## 2026-07-14 (cont.59) — FIXED OrphanReturnError: crash snapshots dropped the callback frame
- The owner resumed crash_003251 (the prior CallbackOverrun crash snapshot),
  played through game-over -> Quick Start, and hit `OrphanReturnError` at 455M.
  ROOT CAUSE: crash_003251's `callback_frames` is [] even though the CPU is parked
  INSIDE the sim-tick callback (0060:005C) — call_far's `finally: frames.pop()`
  unwound the frame during the CallbackOverrun exception BEFORE play.py saved the
  crash snapshot.  So the snapshot recorded an in-flight callback with no frame;
  when it later far-returned to the sentinel, no frame matched -> OrphanReturnError.
- Not a Quick-Start bug and not a fresh-play bug — purely resuming a poisoned
  mid-callback-abort crash snapshot.
- FIX (win16_re c4ad261, bumped): call_far pops the frame ONLY on the clean
  _CallbackReturn path; on any other exception (VM gap/halt/overrun) it leaves the
  frame on win16_callback_frames, so a crash snapshot captures the in-flight
  callback and resumes via the orphan path.  Nothing catches these and continues,
  so no leak.  +1 test.  Suites: win16_re 129, simant 529.
- crash_003251 itself stays poisoned (empty frames, can't retro-fix).  Guidance:
  play FRESH (the cont.58 CallbackOverrun fix removes the mid-session death) and
  use F12 for clean save points; future crash snapshots are now resumable.

## 2026-07-14 (cont.58) — FIXED the recurring sim-tick CallbackOverrun (false positive)
- FIXED the `CallbackOverrun: callback 0100:2440 ... 20000000 steps` crash the
  owner keeps hitting in interactive `--record` runs.  Diagnosed from the crash
  snapshot: the return addr is GR!_TickCount (0E99:18B5) — MYTIMERFUNC's pacing
  loop (_WaitedEnough/_WaitHundredths) busy-polling GetTickCount + GetAsyncKeyState(32).
  check_pause DOES advance the wall clock during the callback, so the wait was
  progressing — the game was just in a long, legitimate, input-driven wait (a
  paused game / "press a key"), and the fixed 20M-step cap killed it.
- FIX (win16_re d9517f4, submodule bumped): call_far accepts max_steps=None (no
  cap; still chunked + yield_check'd, so pausable).  system.callback_max_steps
  defaults to 20M (headless/replay keeps the runaway cap) but the interactive
  driver sets None — interactive is user-interruptible.  DispatchMessage passes
  it.  +2 game-free callback tests.  Suites: win16_re 128, simant 529.
- Note: the crash SNAPSHOT still can't resume past this (it froze interactive=False
  with a stuck clock — a separate resume-fidelity limit), but LIVE play/record is
  fixed: the sim-tick wait now runs uncapped, paced by real time and real input.

## 2026-07-14 (cont.57) — map/life grids named in the bridge; state ownership proven
- Native-port step 2 (state ownership, per vmless_port.md): moved the map/life
  plane BASES (0x28E8/0x48E8/0x58E8, 0x68E8/...) into bridge/dgroup_view.py — they
  are layout ("WHERE"), so they belong in the state view, not recovered.  Added a
  `_Bytes` byte-grid field descriptor + `map_planes`/`life_planes` accessors on
  SimAntState; recovered/gameplay.py now IMPORTS the bases from the bridge
  (recovered -> bridge, the sanctioned direction; no cycle — bridge imports no
  recovered).
- PROVEN (test_state_view.py, +2): the named map-plane views index the exact bytes
  _GetMap's map_cell_offset addresses, for every plane; and the whole map/life grid
  migrates to an owned NativeGameState (write via VM view -> bootstrap -> read back
  natively, owned copy independent).  So SimAnt's biggest structure is now ownable
  with no VM.  Suite: simant 529.
- NEXT: route the _GetMap/_GetLife/_IsItHole islands to READ through map_planes/
  life_planes (currently raw m.rb) — then those core reads run over the owned image
  unchanged, the last step before a native map tick.

## 2026-07-14 (cont.56) — native-port role classifier: an honest progress metric
- Clarified the game/backend boundary for the VM-less endgame.  TWO boundaries:
  (1) win16_re (the Win16 OS layer) is the backend a native port REPLACES
  wholesale; (2) inside the game each routine is core (the sim a native backend
  RUNS byte-exact) / presentation (render/window/editor it REIMPLEMENTS) /
  runtime (C-runtime the language provides).
- Added `classify_domain(seg,name)` to simant/probes/callgraph.py + a `Routine.domain`
  property; `python -m simant.probes.callgraph` now reports native-port progress by
  role.  KEY INSIGHT: only `core` counts toward the endgame — the honest metric is
  **core 33 / 583**, not the flat 62-island count (presentation 18/490 + runtime
  9/240 are workbench scaffolding a native backend discards).  Rule = module default
  (GR/ANTEDIT presentation, _TEXT runtime save its tile builders) + name-prefix
  override (win/font/db/ch/... presentation).  +1 test pinning the split.
- README "Recovery map" gained a "what gets lifted vs replaced" subsection + the
  by-role table.  Suite: simant 527.
- NEXT for the native goal: route the recovered SIM-CORE islands (map/life-grid,
  predicates) through simant/bridge/dgroup_view.py so state ownership migrates, not
  just logic — every core routine recovered THROUGH the view is native-runnable.

## 2026-07-14 (cont.55) — recovered _SGetDis (spider Manhattan distance)
- RECOVERED `_SGetDis` (seg5:56BA), a clean geometry leaf: |x2-x1| + |y2-y1| (the
  cheap Manhattan metric the spider AI uses vs get_dis's squared-Euclidean).  Pure
  s_get_dis; island residue ax=result, bx=|dx|, dx=|dy| (each abs'd via 16-bit
  neg).  8 A/B cases.  Islands 61 -> 62.  Suite: simant 526, seg5 31/169.
- Used the callgraph leaf-queue (`python -m simant.probes.callgraph`) to pick it:
  the remaining seg5/6/7 unrecovered LEAVES are now mostly trivial stubs (2-4B) or
  specialized (spider/list/window accessors); the high-value un-recovered routines
  are COMPOUND (map+flag+branch) — _IsNotObstacle (5:94C6), _IsClearTile (5:5B2C),
  _RandTurn (6:2A22, calls RNG + a turn table via es:[0xc32e]) — or the behaviors.

## 2026-07-14 (cont.54) — recovered _GetLife (life-grid accessor, _GetMap's twin)
- RECOVERED `_GetLife` (seg5:6040), structurally identical to _GetMap but over the
  life-grid planes (DGROUP 0x68E8 yard / 0x88E8 / 0x98E8) with one extra rule: an
  empty cell (byte 0) reads as 0xFFFF.  Refactored the shared plane addressing into
  `_cell_offset(plane,x,y,bases)`; added `life_cell_offset` + `get_life_value`.
  Island mirrors _GetMap's three-exit bx residue (y / 0xFFFF / 0).  23 A/B cases.
- Islands 60 -> 61.  Suite: simant 519.  README map: _GetLife now green, seg5 30/169.
  `_GetBestDir` is now down to two unrecovered callees (_IsNotObstacle, _IsClearTile).

## 2026-07-14 (cont.53) — recovered the geometry primitives _GetDir + _GetDis
- RECOVERED the two load-bearing movement-geometry helpers the recovery map
  flagged (17 and 15 callers): `_GetDir` (seg5:10CC) — 8-way compass direction by
  the sign of (dx,dy), dirs 0..8, a pure leaf; and `_GetDis` (seg5:1122) — squared
  Euclidean distance dx*dx+dy*dy (the sim never roots it).  Pure fns get_dir /
  get_dis in gameplay.py.  20 A/B cases green.
- _GetDis calls the C long-multiply helper (__aFlmul) twice; its DX:AX result is
  byte-exact, and BX/CX are left holding __aFlmul's internal scratch — the same
  caller-unobserved residue __aFuldiv's oracle already excludes.  The island
  doesn't fabricate that scratch and the _GetDis oracle checks the contract
  (DX:AX + preserved SI/DI/BP/DS/ES), not BX/CX.
- Islands 58 -> 60.  Suite: simant 509.  README recovery map updated (seg5 29/169,
  _GetDis now green in the diagram).

## 2026-07-13 (cont.52) — recovered _IsNotBarrier (movement passability leaf)
- RECOVERED `_IsNotBarrier` (seg5:94A0), a clean world-flag leaf (same seam as
  _IsLessThanHole, selector [0xC4AC]): a tile is passable when <= 0x5F inside the
  nest, <= 0x50 in the outside yard.  Pure is_not_barrier(tile, inside); island
  clobbers bx=arg, es=world selector.  17 A/B cases green.
- Islands 57 -> 58.  Suite: simant 490.
- Surveyed the rest of the movement family: _IsNotObstacle (5:94C6),
  _TileCanBeMovedOn (5:9342), _IsItDigable (5:95C6) are all COMPOUND — multi-plane
  validity + map reads (enter N,0 with locals, like _GetMap but larger).  Next
  focused slice: recover those as map-reading islands (they compose _GetMap's
  plane layout + tile predicates), and _SetMap's redraw-far-call partial island.

## 2026-07-13 (cont.51) — recovered _IsItHole (first map-query predicate)
- RECOVERED `_IsItHole` (seg6:2CC0), the first STATEFUL map-query predicate — it
  composes the pieces already recovered: is_valid_a bounds check + the plane-0
  yard map at map_cell_offset(0,x,y) + the world inside/outside flag.  Hole =
  0x80..0x8F inside, ==0x50 outside.  Pure recovered fn is_it_hole(tile, inside);
  the island bridges the map + flag reads.
- The tricky part (handled): _IsItHole CALLS _IsValidA, so the island replicates
  that nested call's dx residue exactly (x_word if the x-check failed else
  y_word) across all three ASM exits (invalid / valid-inside / valid-outside),
  plus bx=x<<6 and es=world selector on the valid paths.  A/B oracle seeds both
  the flag and the tile; 18 cases incl. both invalid-coord residues green.
- Islands 56 -> 57.  Suite: simant 474.
- NEXT map-query targets: the movement family _TileCanBeMovedOn / _IsNotBarrier /
  _IsNotObstacle / _IsItDigable (5:9342..95C6).  And the WRITE side _SetMap
  (5:617A) needs a partial island (it makes a redraw far-call lcall 0x18C0:0
  after writing) — invoke the sub-call through the VM.

## 2026-07-13 (cont.50) — null-proc frontier RESOLVED: it was a snapshot-fidelity bug
- DIAGNOSED the cont.49 null far-call (`lcall [bp-4]` at GR seg2:9DB1).  Both
  indirect calls come from GetProcAddress (thunk 0x1ec = KERNEL.50); the game
  looks up MMSYSTEM's digital-audio procs — `waveOutClose` (null-checked) then
  `waveOutOpen` (NOT null-checked, called at 9DB1).  Our GetProcAddress returned
  NULL for waveOutOpen because `hinst=0x0100`'s module wasn't in `libraries`.
- ROOT CAUSE: not a live crash — a SNAPSHOT artifact.  load_snapshot rebuilds a
  fresh API registry, dropping the `libraries` map (LoadLibrary name->HINSTANCE),
  which GetProcAddress keys off.  The game's stored FARPROC then far-called NULL.
  Proof: seeding `libraries={MMSYSTEM.DLL:0x0100}` in the resume makes
  GetProcAddress('waveOutOpen') return the stub thunk (0x00600304) and the game
  advances +5.8M instrs past 9DB1 (then only the known mid-callback-stack
  OrphanReturnError, another resume-only artifact).  Live, waveOutOpen resolves
  to the BADDEVICEID stub and the game backs off — no crash.  (So the frontier
  the USER actually hit was USER.55, already fixed in cont.49.)
- FIXED (win16_re 7d35282, submodule bumped): vmsnap persists+restores
  `libraries` in state.json (backward compatible).  +1 simant round-trip test
  (test_snapshot.py).  Suites: win16_re 126, simant 456.
- The waveOut* digital-sound path itself remains a safe stub (BADDEVICEID); a
  real PCM backend is a future phase only if a game path needs it.

## 2026-07-13 (cont.49) — implemented EnumChildWindows (USER.55); new null-proc frontier
- FRONTIER (from `pypy scripts/play.py --record cold_nohooks2 --no-hooks`): a
  Win16ApiGap `USER.55` at instr 427,756,629.  Identified it (via the crash
  snapshot's stack + the thunk table) as EnumChildWindows(hWndParent=0x118,
  lpEnumFunc=0100:1C38, lParam=0) — the pascal-order args put the FARPROC at the
  2nd slot; the callback is EnumChildProc(hwnd, lParam) (retf 6).  SimAnt uses it
  to walk its 7 leaf child windows and InvalidateRect(child, NULL, FALSE) each.
- IMPLEMENTED in win16_re (482351f, submodule bumped): EnumChildWindows over
  _z_children in top-to-bottom Z-order, callback via call_far, early-stop on a
  FALSE return; +2 game-free tests.  win16_re 126.  Resuming the crash snapshot
  now runs the callback for every child, returns, and advances ~926K instrs past
  the gap.
- NEW FRONTIER (deeper, journalled for next time): ~926K instrs later the game
  does `lcall [bp-4]` at seg2(GR):9DB1 through a NULL far pointer -> executes
  garbage in segment 0 -> INT3 at 0000:0601.  The local [bp-4] holds a proc
  pointer that our framework left null.  NOTE: observed from a HEADLESS snapshot
  RESUME (interactive=False), which the loader warns can differ from a live run
  (clock/paint pacing) — reproduce fresh from `cold_nohooks2` before diagnosing;
  it may be a different missing proc-pointer setup rather than a live crash.

## 2026-07-13 (cont.48) — recovered _GetMap: the keystone map-cell accessor
- RECOVERED `_GetMap` (seg5:60E2), the map accessor the whole map-query family
  builds on.  This maps out the world storage: THREE plane arrays packed in
  DGROUP — planes 0-1 (yard, 128x64) at ds:0x28E8, plane 2 (nest) at 0x48E8,
  plane 3 at 0x58E8 — addressed column-major with x-stride 64
  (offset = base + (x<<6) + y).  Coordinate validity is exactly the already-
  recovered is_valid_a (yard) / is_valid_b (nest).  Out-of-range returns 0xFFFF.
- The recovered logic is a pure `map_cell_offset(plane,x,y) -> int|None`; the
  island reads the DS byte and replicates _GetMap's THREE distinct exits and
  their bx residue exactly (bx=y valid, 0xFFFF coord-invalid, 0 plane-invalid),
  proven over valid/coord-invalid/plane-invalid cases on every plane.
- Islands 55 -> 56.  Suite: simant 455.
- NEXT (now unblocked by the map seam): the map-query predicates that read the
  planes — _IsItHole (6:2CC0: valid + inside 0x80..0x8F / outside ==0x50 at
  ds:0x28E8), and the movement family _TileCanBeMovedOn / _IsNotBarrier /
  _IsNotObstacle / _IsItDigable (5:9342..95C6), which compose _GetMap + the leaf
  tile predicates already recovered.

## 2026-07-13 (cont.47) — recovered the world-state predicate seam (_IsLessThanHole/_IsSamePlane)
- RECOVERED the first two STATEFUL leaf predicates, using the _IsItFood
  world-state seam (island reads DGROUP globals, recovered fn stays pure):
    _IsLessThanHole (seg5:9784)  tile < (0x59 inside / 0x50 outside); the
        inside/outside flag is the same one is_it_food reads (world selector at
        DGROUP:[0xC4AC], flag at [0x9B6E]).  Clobbers bx=arg, es=world selector.
    _IsSamePlane    (seg5:97AA)  current-plane (DGROUP:[0xCE80]) == (plane==0 ? 1
        : plane).  Clobbers dx = the normalized plane.
  Kept the "inside == flag set" convention consistent with is_it_food (the ASM
  flag test direction differs between the two routines, so is_less_than_hole's
  thresholds are 0x59 inside / 0x50 outside — verified by the A/B oracle).
- Islands 53 -> 55.  Suite: simant 443.  verifyislands over cold_nohooks: the
  one new island it reaches (_IsValidA) is byte-exact in registers + memory,
  FLAGS_ONLY residue (same accepted no-caller-reads category as every other
  predicate island); the colony-sim predicates aren't reached by cold_nohooks.
- NEXT: the stateful MAP predicates — _IsItHole (6:2CC0), _TileCanBeMovedOn /
  _IsNotBarrier / _IsNotObstacle / _IsItDigable (5:9342..95C6) — which index the
  live map array [bx+di+0x28E8]; needs a map-backed A/B harness (seed a small
  map region + world selector), a natural next seam.

## 2026-07-13 (cont.46) — recovered _IsValidA/_IsValidB; centralized the island count
- RECOVERED the two coordinate-validity predicates (islands + byte-exact A/B):
    _IsValidA (seg5:9C02)  valid iff x in 0..0x7F and y in 0..0x3F (wide yard grid)
    _IsValidB (seg5:9C26)  valid iff x in 0..0x3F and y in 0..0x3F (64x64 nest)
  dx residue replicated (dx=x when the x-check fails, else reloaded to y).
- REFACTOR: the island count now lives in one constant `hooks.EXPECTED_ISLAND_COUNT`
  (= 53); the ~29 A/B-oracle install-count asserts reference it instead of a
  literal, so adding an island updates one line, not every test.  install() still
  returns the live count, so the dedicated count test catches _ISLANDS drift.
- Islands 51 -> 53.  Suite: simant 419.
- NEXT: the world-state-flag predicate seam — _IsLessThanHole (5:9784) and
  _IsSamePlane (5:97AA) read the inside/outside flag (es:[0x9B6E] via a selector
  global) and the current plane ([0xCE80]); recover them like _IsItFood using a
  DGROUP-view seam, then the stateful map predicates (_IsItHole 6:2CC0, the
  _TileCanBeMovedOn / _IsNotBarrier / _IsItDigable movement family).

## 2026-07-13 (cont.45) — recovered the seg5 tile-classification predicate family
- Continued gameplay-core recovery (pivot to sim logic).  Profiled the `cold`
  hooks replay: dominated by pacing-spin (GR!_WaitedEnough/_TickCount), the ant
  editor (ANTEDIT seg3) and font rendering (SIMTWO seg7) — it doesn't exercise
  the ant sim hot, so byte-exact recovery uses the A/B oracle over crafted inputs
  rather than a hot-loop profile.
- RECOVERED 5 pure tile-classification predicates in recovered/gameplay.py, each
  an island + byte-exact A/B oracle (island vs ASM, and ASM vs recovered Python):
    _RIsItDirt   (seg5:26C4)  dirt = 0x20..0x2F or >=0x4F  (red-colony; wider
                              than _IsItDirt's 0x20..0x2E)
    _IsItNFood   (seg5:5F64)  in-nest food band 0x10..0x13
    _IsThisEgg   (seg5:5EC8)  low byte & 0x7F in 1..7 (high bit is a sim flag)
    _IsThisGrass (seg5:5EE4)  plane>=2 and tile 0x1C..0x1F
    _IsThisPebble(seg5:5F32)  plane>1 -> 0x30..0x31; plane==1 -> 0x51..0x53
  The dx-clobber residue is replicated exactly per routine (e.g. _IsThisGrass
  leaves dx untouched when plane<2 because the ASM returns before loading it;
  _IsThisPebble keeps plane-1 on the plane<=0 path).  All compares signed.
- Islands 46 -> 51; the 28 install-count asserts updated.  Suites: win16_re 124,
  simant 401 (+45 oracle cases).
- NEXT candidates (same seg5 leaf family): _IsThisFood (5F04, tail-calls
  _IsItFood so needs the world-state seam), _TileCanBeMovedOn/_IsNotBarrier/
  _IsItDigable/_IsLiftable (9342..97CA), and the stateful _IsItHole (6:2CC0,
  reads the map array) once a map-backed A/B harness exists.

## 2026-07-13 (cont.44) — cache panic FIXED: GMEM_DISCARDABLE + lock counts (win16_re 4e91095)
- FIXED the `'no memory over 0 in age!'` fatal panic (repro: the owner's 46-hook
  `cold` demo, deterministic at instr 193,254,862).  Root cause was a win16_re
  memory-manager gap, not a hook bug: the global heap stubbed GlobalFlags -> 0 and
  GlobalLock/Unlock as no-ops.
- MECHANISM (traced in ASM): SimAnt's `_ch_` tile chunk-heap (150-entry cache)
  evicts the oldest block that is BOTH discardable AND unlocked, gated on
  GlobalFlags: seg2:5660 returns 3 iff `GlobalFlags & 0x0100` (GMEM_DISCARDABLE),
  seg2:5492 returns 0 iff discardable AND lock-count (low byte) == 0.  With the
  stubs every tile reported flags=0 -> non-discardable -> NO eviction candidate ->
  panic on the 151st distinct tile.
- KEY EVIDENCE: instrumented the live replay — GlobalAlloc is called 28,591x with
  flags=0x0042 (MOVEABLE|ZEROINIT, never discardable), then **GlobalReAlloc is
  called 2,419x with flags=0x0180 (GMEM_MODIFY|GMEM_DISCARDABLE)** to re-mark the
  tiles discardable in place.  Our GlobalReAlloc GMEM_MODIFY path did `return
  handle` and dropped the attribute change on the floor -> identical instr count
  before/after a first (irrelevant) discardable-on-alloc attempt.
- FIX (win16_re 4e91095, submodule bumped): HugeHeap tracks a per-handle lock
  count (GlobalFlags low byte, saturating) + GMEM_DISCARDABLE set; GlobalAlloc
  passes discardable; GlobalReAlloc(GMEM_MODIFY) toggles it in place;
  GlobalLock/Unlock count; GlobalFlags reports count|discardable.  +2 game-free
  hugeheap unit tests.  Stays game-agnostic (documented Win16 contract).
- VERIFIED: replaying `cold` now runs CLEAN past 193.25M to 209.4M (DemoEnded,
  demo exhausted) — panic gone.  Suites green: win16_re 124, simant 356.
- RE-BASELINE (expected): cold_nohooks replay digest shifts 74bf3228... ->
  2abf3ce5... (199,612,542 -> 199,612,993 instrs, +451).  Cause is the now-real
  GlobalUnlock return (TRUE-while-locked vs the old constant 0) + GlobalFlags
  reporting lock state; the no-hooks session runs clean to completion with sane
  state.  No committed oracle asserted the old digest (suite green); the new
  behaviour is faithful Win16 semantics, so this is a legitimate re-baseline, not
  a weakened oracle.
- STILL OPEN (unchanged): tick-demo clock-read misalignment at ~tick 91 (#17,
  option (b) — advance the boundary to after MYTIMERFUNC's retf); the interactive
  CallbackOverrun busy-wait (0100:2440) seen in the owner's other `cold` run is a
  separate sim-tick catch-up spin, not this crash.

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
