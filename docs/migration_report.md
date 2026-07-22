# dos_re 3.0 migration — implementation report

SimAnt (`win16_re` + `simant_port`) migrated onto the dos_re 3.0 paradigm.
This report answers the migration brief's §14 in order.  It states only what
is verified; open work is named in §13–15.  Suite state at time of writing:
dos_re 1277, win16_re 429, simant 2325 — all green; the pinned byte-exact
gate MATCHes (mdigest `417cac5cd9aadb8c`).

Branches: `simant-3.0` (simant_port), `win16-re-3.0` (win16_re), dos_re `main`
(three fixes upstreamed).  Journal detail: `docs/run_status.md` cont.258–264.

---

## 1. Previous architecture (dos_re 2.0-era)

A staged pipeline with hard tooling-enforced walls per stage: interpreted VM →
hand islands over the VM → VMless (full lifted graph + `interp_forbidden`) →
CPUless (no CPU carrier) → (future) memoryless.  Each stage had its own runner
(`play.py`, `play_vmless.py`, `play_cpuless.py`), its own artifacts, and its
own verification tool (`checkpoints.py` byte-exact digests, `liftverify.py` /
`verifyislands.py` per-call differentials, `entry_probe.py` coverage).  The
replay format was v4 JSONL demos (`win16/demo.py`), instruction-count-keyed.
Mode-independent equivalence used tick demos (`win16/tick_demo.py`).

## 2. Final architecture (dos_re 3.0)

One evidence-driven model.  Stable identities (`dos_re.identity`) cross every
artifact.  Three evidence authorities — Recovery IR, ReplayArtifacts, explicit
facts — project into a queryable **Execution Atlas** that never executes or
selects code.  One **ImplementationCatalog** holds every implementation
(interpreted / generated / authored) as origins with recovery-level
*properties*; **`plan_execution`** binds exactly one owner to each reachable
identity and emits an immutable **ExecutionPlan** + **DetachmentReport**.  One
player selects composition by `--profile`; the same ReplayArtifact verifies
against any profile through a shared **CanonicalState** projection.  VMless /
CPUless are per-implementation properties and per-profile carriers, never
whole-game modes.

## 3. dos_re 3.0 concepts adopted

Stable identities; ReplayArtifact (the sole replay format); ContinuationState
vs CanonicalState (private resume state vs comparison projection);
ReplayPointCoordinate (backend-neutral stop coordinate); Execution Atlas
(projection + coverage source); ProgramCoverage / CoverageSource;
ImplementationCatalog / ImplementationDescriptor / BackendAdapter; override
categories (baseline / faithful / enhancement / behavioral / instrumentation);
ExecutionConfiguration + policy profiles (development / verification /
detached / release); ExecutionPlan + DetachmentReport; BootstrapProvider;
FallbackPolicy → the interpreter wall; detachment guard (import wall);
RuntimeExecutionFrontier (runtime miss); ReplayDriver + VerificationProjection
Contract + verify_interval.

## 4. Win16-specific adaptations

- **Address space `win16-para`** — paragraph CS:IP, exactly as the recovery
  pipeline already keys functions (the NE loader maps segments at fixed
  paragraph bases).
- **Replay channels** `win16.input / clock / dialog / messagebox / quit`;
  **coordinate schema** `win16-re:guest-instruction-count:v1` (a guest
  coordinate — host dispatch counts are forbidden by the 3.0 contract).
- **ContinuationState** `win16-re-continuation-v1` = guest memory region +
  the pickled Win16 OS object graph region + machine metadata (CPUState incl.
  x87, callback frames, loaded libraries, polled keys).  The Windows-object
  state that lives outside guest memory is first-class here.
- **CanonicalState** `win16-re-observable-v1` = CPU state, virtual instruction
  count (a comparison field — generated bodies preserve it exactly), clock,
  timers, window list, one surface hash per window, and guest memory with the
  recovered-code ranges masked (the EXE-independence comparison seam).
- **Boundary namespace `callback`** — WndProc / DialogProc / TimerProc
  dispatches resolve only at runtime; a `call_far` observer records them as
  the additional coverage ROOTS a message-driven program has beyond its NE
  entry point.
- **Carriers** `win16-interpreted-cpu`, `win16-cpuless`; the OS API surface is
  a set of `RuntimeService`s + `api:*` boundary transitions, never
  implementations claiming game code.

## 5. Important files changed

New (win16_re): `win16/replay.py`, `win16/continuation.py`,
`win16/evidence.py`, `win16/replay_driver.py`, `docs/dos_re_3_0.md`,
`tests/test_replay_win16.py`, `tests/test_evidence_win16.py`,
`tests/test_replay_driver_win16.py`, `tests/test_architecture_contract.py`.
Ported (win16_re): `win16/cpuless.py`, `win16/bootimage.py`, `win16/irgen.py`,
`win16/machine.py`, `win16/vmsnap.py`, `win16/callback.py`, `win16/api/*`.
New (simant): `simant/execution.py`, `scripts/plan.py`,
`scripts/atlas_build.py`, `scripts/demo2replay.py`,
`scripts/replay_artifact.py`, `scripts/verify_replay.py`.
Changed (simant): `scripts/play.py`, `scripts/replay.py`, `simant/hooks.py`
(plan-selectable install + a duplicate-island fix), `scripts/irgen.py` +
lift pipeline (facts-key rename).
Upstream (dos_re `main`): `dos_re/atlas.py` (two fixes),
`dos_re/execution.py` (one fix), with regression tests.

## 6. Removed legacy paths

Deleted: `win16/tick_demo.py`, `win16/tests/test_tick_demo.py`,
`win16/tests/test_tick_record_replay.py`, `scripts/tickdemo.py` — tick demos,
mirroring dos_re 3.0's own removal.  The v4 demo *recorder* is retired
(recording is `ArtifactRecorder`).  **Still present, scheduled for removal**
once their byte-exact consumers migrate: the v4 *reader* (`win16/demo.py`
`DemoDriver`), `scripts/play_vmless.py`, `scripts/play_cpuless.py`,
`scripts/checkpoints.py` (→ `verify_replay.py`), `scripts/entry_probe.py` (→
`replay_artifact.py --evidence`).  These are the honest remainder (§13).

## 7. Replay and snapshot semantics

A **ReplayArtifact** is a directory: an immutable event stream on the Win16
channels, a per-ordinal instruction-count coordinate timeline, and per-profile
bases + boundary caches.  A **snapshot** is now a profile-local `ContinuationState`
(guest memory + OS graph + metadata) cached inside the artifact — the
directory `.snapshot` form (`win16/vmsnap.py`) survives for interactive
F12 and the boot-image load path, sharing `restore_machine_payload` with the
continuation codec.  Interval workflow: locate nearest cached boundary ≤ point
→ restore → replay to point (exact stop) → project → compare.  Win16 drivers
stop exactly at the timeline base or end today; mid-timeline stops need a
host-boundary parking protocol (not built — raises rather than approximating).

## 8. Execution Atlas schema (as used)

`atlas/manifest.json` + `sources/{static,replay,manual}-<key>.json` +
`indexes/{graph.json,replay_coverage.json}`.  Nodes: `program`, `image`,
`function`, `execution-point`, `boundary`, `runtime-code-slot/-variant`,
`region`.  Edges carry `{source, target, kind, status, observation_count,
evidence, conflicts}`; statuses `containment / resolved / frontier /
unresolved / boundary / observed`.  SimAnt Atlas: 2469 nodes (1904 functions
labelled from SIMANTW.SYM, 197 `api:` boundaries, 366 execution points), 9734
edges; roots = `__astart` + observed callback entries.  Built regenerably by
`scripts/atlas_build.py`.

## 9. Boundary taxonomy (Win16)

`api` (import-thunk transition into KERNEL/USER/GDI/…), `callback` (host→guest
re-entry: WndProc / DialogProc / TimerProc / EnumProc), `interrupt` (raw INT
21h etc.), plus the structural `contains` / `call` / `call_ind` / `jmp_ind` /
`tail-transfer` edges the IR import mints.  `api:*` and `callback` boundaries
are OS transitions, never program code — the identity grammar keeps them out
of coverage's reachable code set (a dos_re fix this port surfaced).

## 10. Override categories

- **Baseline** — interpreted original bytes (`interpreted-baseline`); generated
  graphs (`vmless-graph`, `cpuless-corpus`).
- **Faithful** — the 68 hand-recovered islands (`simant/hooks.py` over
  `simant/recovered/`): authored, evidence grade REPLAY_CORPUS (the A/B
  oracles + `verifyislands` per-call differential + the demo corpus).  These
  are the hand-recovered logic, plan-selected and installed by the catalog
  adapter — never by import-time side effect.
- **Enhancement / behavioral / instrumentation** — none declared yet; the
  host renderer/audio/MIDI are `RuntimeService`s (they treat gameplay state as
  read-only), so they are services, not overrides.

## 11. SimAnt workflows used for validation

The `cold_nohooks` session (cold start → splash → menus → quick-game window
→ simulation ticks → MIDI soundtrack, 4212 input events, 199,619,366 guest
instructions) is the primary deterministic replay.  It exercises startup,
main-window creation, menu interaction, the sim-tick TimerProc, world
painting, child windows, and the MCI music path.  `cold2` (31,766 events, a
full game to a defeat) is a recorded wider session, a candidate second gate.

## 12. Verification results

- **v4 → ReplayArtifact conversion is byte-identical**: converted
  `cold_nohooks` replays to instr 199,619,366 with the same game-observable
  digest as the v4 original.
- **The pinned byte-exact gate MATCHes**: 39/39 checkpoints, mdigest
  `417cac5cd9aadb8c`, unchanged from before the migration.
- **`--profile detached` reproduces the gate byte-identically** (same instr,
  same mdigest) via the plan-bound composition — proven equal to the legacy
  hand-wired `boot_strict`.
- **`verify_interval` (the core 3.0 claim): oracle ≡ candidate.**  The
  interpreted oracle and the plan-bound detached composition drive the SAME
  artifact over the full timeline and project to the IDENTICAL CanonicalState
  `5fcf69c838409af5` (`equivalent=True`); the scoped ReplayValidation is
  persisted on the artifact.
- **The detached ExecutionPlan resolves 1007/1007** reachable identities (743
  vmless-graph + 264 cpuless-corpus), `is_detached_from(original-exe)` and
  `(interpreter)` both True.
- **`development --override islands` binds exactly the legacy 68 island hooks**
  (strict subset, identical names, prologue checks intact).
- **Three dos_re bugs found and fixed upstream** with failing-on-old-code
  regression tests (coverage traversal through containment; authored
  inventory may exceed coverage; observed-only endpoint kinds from the
  identity grammar).

## 13. Remaining interpreted / unresolved areas

- The detached plan's development coverage (1007 reachable) is the
  cold-session closure, not the whole binary; wider recordings extend it.
  22 of 68 islands sit outside that conservative coverage (routines the cold
  session never exercised, e.g. AntEdit / save-load) — the planner declines to
  bind them until wider trusted evidence arrives.  This is the model working.
- The x87 FP frontier is unbuilt by design (`win16/fpu.py`; measured 0
  reachable in the closure); `WIN87EM.1 __fpMath` deliberately refuses.
- Mid-timeline replay stops need a host-boundary parking protocol (base/end
  stops only today).

## 14. Known limitations

- **Phase 5b not complete.**  The v4 reader and the standalone
  `play_vmless`/`play_cpuless` runners still exist because their byte-exact
  consumers (checkpoints / liftverify / verifyislands / adaptverify /
  deploy-smoke / independence-lint and their tests) must migrate
  individually, each gate-verified.  `Win16ReplayInputDriver` has the same
  interface as `DemoDriver`, so each migration is mechanical — but not a safe
  single sweep.
- **Interactive recording is unit-verified, not GUI-verified** in this
  environment; a fresh owner recording is the integration proof.
- **The 3.0 closed-world export** (`tools/export.py` materialized plan) has not
  replaced the `deploy_vmless.py` standalone-EXE release path yet.
- Task #45 (memoryless / Stage 3) is deliberately NOT started — owner-gated.

## 15. Recommended next recovery targets

1. **Phase 5b**: migrate the byte-exact analysis scripts to
   `input_driver_for(ReplayArtifact)`, delete the fully-replaced tools
   (checkpoints, entry_probe) and the v4 reader, retire
   play_vmless/play_cpuless, add the simant architecture-contract test.
2. **Second behavioral gate**: convert `cold2` to an artifact and
   `verify_interval` it (a full-game session — the widest coverage).
3. **Closed-world export**: an `export_release` factory + materialized plan to
   replace the ad hoc standalone-EXE packaging (the 3.0 `detached`/`release`
   profiles already resolve).
4. **Recovery frontier**: widen far-call evidence (task #60) and route the
   skin corpus into the composable graph (task #53) to shrink the CPUless
   frontier the DetachmentReport now names precisely.
5. **MIDI contract** (task #62): decode the real MCI_STATUS dwItem from
   observed traffic — the one owner-reported behavioral symptom still open.
