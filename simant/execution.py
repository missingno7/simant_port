"""SimAnt's dos_re 3.0 execution composition: identities, catalog, planning.

The game-side declaration layer the planner consumes.  Everything here is
DECLARATIVE — no machine is built, no hook installed, no module imported at
selection time.  Adapters carry the installation mechanics and run only when
``bind_plan_implementations`` activates a validated plan.

The implementation inventory (one catalog, per dos_re 3.0 invariant 5):

* ``interpreted-baseline`` — the untouched original bytes under the CPU8086
  interpreter.  origin=interpreted; requires the original code, the
  interpreter and the CPU model, so detached profiles reject it by policy.
* ``islands`` — the 69 hand-recovered islands (``simant/hooks.py`` over the
  pure bodies in ``simant/recovered/``).  origin=authored, category=FAITHFUL
  (byte-exact equivalence is the claim), evidence=REPLAY_CORPUS (the A/B
  oracles + per-call verifyislands differential + the demo corpus).  The
  adapter installs exactly the plan-selected subset as replacement hooks and
  refuses a prologue mismatch — hand-recovered logic is hooked through the
  plan, never by import-time side effect.
* ``cpuless-corpus`` — the generated CPUless skeleton
  (``simant/native/cpuless``): pure ``func(mem, plat, **regs)`` bodies.
  origin=generated, recovery level generated-cpuless; carrier-adapted, no
  interpreter/CPU capability required.

Identity scheme (must match every Atlas artifact): program ``simant:1.0``,
image = SIMANTW.EXE by sha256, address space ``win16-para`` (paragraph CS:IP
exactly as the whole recovery pipeline keys functions).
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from dos_re.execution import (BackendAdapter, EvidenceGrade,
                              ExeBootstrapProvider, ImplementationCatalog,
                              ImplementationDescriptor, ImplementationEntry,
                              ImplementationOrigin, OverrideCategory,
                              RecoveryLevel, profile_configuration,
                              plan_execution)
from dos_re.identity import (FunctionIdentity, ImageIdentity, ProgramIdentity,
                             real_mode_address)

from .runtime import EXE_PATH

PROGRAM_KEY = "simant:1.0"
ADDRESS_SPACE = "win16-para"

#: Win16 execution carriers (activation-seam mechanics, not recovery levels).
INTERPRETED_CARRIER = "win16-interpreted-cpu"
CPULESS_CARRIER = "win16-cpuless"

REPO_ROOT = Path(__file__).resolve().parent.parent
CPULESS_CORPUS_DIR = REPO_ROOT / "simant" / "native" / "cpuless"
VMLESS_GRAPH_DIR = REPO_ROOT / "simant" / "lifted" / "graph_cpuless"
BOOT_IMAGE_DIR = REPO_ROOT / "artifacts" / "vmless_boot"


def program() -> ProgramIdentity:
    return ProgramIdentity(PROGRAM_KEY)


_IMAGE_OVERRIDE: ImageIdentity | None = None


def use_image(name: str, sha256: str) -> None:
    """Pin the image identity from provenance (the boot manifest's
    source_exe record) instead of hashing the EXE file — the EXE-free
    compositions must plan with the executable physically absent."""
    global _IMAGE_OVERRIDE
    _IMAGE_OVERRIDE = ImageIdentity(program(), name, "sha256", sha256)
    image_identity.cache_clear()


@lru_cache(maxsize=1)
def image_identity() -> ImageIdentity:
    if _IMAGE_OVERRIDE is not None:
        return _IMAGE_OVERRIDE
    sha = hashlib.sha256(EXE_PATH.read_bytes()).hexdigest()
    return ImageIdentity(program(), EXE_PATH.name, "sha256", sha)


def function_id(cs: int, ip: int) -> str:
    return FunctionIdentity(image_identity(), ADDRESS_SPACE,
                            real_mode_address(cs, ip)).key


def address_of(identity: str) -> tuple[int, int]:
    """The paragraph (cs, ip) back out of one of our function identities."""
    from dos_re.identity import split_child_identity
    space, address = split_child_identity(identity, "function")
    if space != ADDRESS_SPACE:
        raise ValueError(f"not a {ADDRESS_SPACE} function identity: {identity}")
    cs, ip = address.split(":")
    return int(cs, 16), int(ip, 16)


def _dir_digest(directory: Path, pattern: str = "func_*.py") -> str:
    h = hashlib.sha256()
    for path in sorted(directory.glob(pattern)):
        h.update(path.name.encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def _corpus_targets(directory: Path) -> frozenset[str]:
    out = set()
    for path in directory.glob("func_*.py"):
        _, cs, ip = path.stem.split("_")
        out.add(function_id(int(cs, 16), int(ip, 16)))
    return frozenset(out)


_GRAPH_HEADER = None


def _graph_targets(directory: Path) -> frozenset[str]:
    """Targets of a symbol-named lifted graph (liftlink names modules by
    SYM symbol, e.g. ``antedit_antmenu.py``): every module's docstring
    declares its address as ``Function CS:IP`` — the emitter's contract."""
    import re
    global _GRAPH_HEADER
    if _GRAPH_HEADER is None:
        _GRAPH_HEADER = re.compile(
            r"^Function ([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\b", re.MULTILINE)
    out = set()
    for path in directory.glob("*.py"):
        m = _GRAPH_HEADER.search(path.read_text(encoding="utf-8",
                                                errors="replace")[:400])
        if m is not None:
            out.add(function_id(int(m.group(1), 16), int(m.group(2), 16)))
    return frozenset(out)


# --- adapters (installation mechanics; run at bind time only) ---------------

def _install_islands(machine, targets) -> None:
    """Install exactly the plan-selected islands as replacement hooks."""
    from . import hooks
    only = {address_of(t) for t in targets}
    installed = hooks.install(machine, only=only)
    if installed != len(only):
        raise AssertionError(
            f"island adapter installed {installed} of {len(only)} selected")


def _with_points(targets: frozenset[str], contained) -> frozenset[str]:
    """An implementation that owns a function owns its interior execution
    points (the dispatch sites inside it) — the containment attribution the
    Atlas records."""
    if not contained:
        return targets
    out = set(targets)
    for func in targets:
        out.update(contained.get(func, ()))
    return frozenset(out)


def build_catalog(seg_bases, reachable: frozenset[str],
                  contained=None, *,
                  graph_dir: Path = VMLESS_GRAPH_DIR) -> ImplementationCatalog:
    """The one implementation inventory, scoped to ``reachable`` coverage.

    ``seg_bases`` — the deterministic segment layout (a live machine's, or the
    boot manifest's) that places the island entries in paragraph space.
    """
    from . import hooks

    baseline = ImplementationEntry(
        ImplementationDescriptor(
            implementation_id="interpreted-baseline",
            # The untouched original bytes own EVERYTHING reachable —
            # functions and the execution points inside them alike.
            targets=frozenset(reachable),
            origin=ImplementationOrigin.INTERPRETED,
            recovery_level=RecoveryLevel.INTERPRETED,
            required_capabilities=frozenset(
                {"original-code", "interpreter", "cpu-model"}),
            implementation_digest="win16-interpreted-baseline-v1",
        ),
        implementation=None,            # the untouched bytes; no bridge
    )

    island_ids = frozenset(
        function_id(cs, ip)
        for (cs, ip) in hooks.island_addresses(seg_bases))
    hooks_digest = hashlib.sha256(
        (Path(hooks.__file__)).read_bytes()).hexdigest()
    islands = ImplementationEntry(
        ImplementationDescriptor(
            implementation_id="islands",
            targets=island_ids,
            origin=ImplementationOrigin.AUTHORED,
            category=OverrideCategory.FAITHFUL,
            recovery_level=RecoveryLevel.AUTHORED_NATIVE,
            properties=frozenset({"hand-recovered", "byte-exact-oracle"}),
            required_capabilities=frozenset({"cpu-model"}),
            evidence_grade=EvidenceGrade.REPLAY_CORPUS,
            verification_evidence=frozenset(
                {"simant/tests/test_hooks.py A/B oracles",
                 "scripts/verifyislands.py per-call differential"}),
            implementation_digest=hooks_digest,
        ),
        implementation=_install_islands,
        adapters=(BackendAdapter(
            adapter_id="islands/interpreted-cpu",
            carrier_id=INTERPRETED_CARRIER,
            activate=_install_islands,
            adapter_digest=hooks_digest,
        ),),
    )

    entries = [baseline, islands]

    if graph_dir.is_dir():
        graph_targets = _with_points(
            _graph_targets(graph_dir) & reachable, contained)
        if graph_targets:
            graph_digest = _dir_digest(graph_dir, "*.py")

            def _activate_graph(machine, targets) -> None:
                from dos_re.lift.install import activate_generated_graph
                installed = activate_generated_graph(machine.cpu, graph_dir)
                missing = {t for t in targets
                           if address_of(t) not in installed} \
                    if isinstance(installed, dict) else set()
                if missing:
                    raise AssertionError(
                        f"vmless graph missing {len(missing)} planned "
                        f"targets, e.g. {sorted(missing)[:3]}")

            entries.append(ImplementationEntry(
                ImplementationDescriptor(
                    implementation_id="vmless-graph",
                    targets=graph_targets,
                    origin=ImplementationOrigin.GENERATED,
                    recovery_level=RecoveryLevel.GENERATED_VMLESS,
                    properties=frozenset({"vmless", "instruction-exact"}),
                    required_capabilities=frozenset({"cpu-model"}),
                    implementation_digest=graph_digest,
                ),
                implementation=None,
                adapters=(BackendAdapter(
                    adapter_id="vmless-graph/interpreted-cpu",
                    carrier_id=INTERPRETED_CARRIER,
                    activate=_activate_graph,
                    adapter_digest=graph_digest,
                ),),
            ))

    if CPULESS_CORPUS_DIR.is_dir():
        corpus_targets = _with_points(
            _corpus_targets(CPULESS_CORPUS_DIR) & reachable, contained)
        if corpus_targets:
            corpus_digest = _dir_digest(CPULESS_CORPUS_DIR)

            def _activate_cpuless(machine, targets) -> None:
                # The CPU-free carrier resolves bodies through the corpus
                # dispatch (win16.cpuless bridge until callables are
                # materialized at plan time); nothing to install on the
                # machine — selection is the plan itself.
                del machine, targets

            entries.append(ImplementationEntry(
                ImplementationDescriptor(
                    implementation_id="cpuless-corpus",
                    targets=corpus_targets,
                    origin=ImplementationOrigin.GENERATED,
                    recovery_level=RecoveryLevel.GENERATED_CPULESS,
                    properties=frozenset({"cpuless", "instruction-exact"}),
                    implementation_digest=corpus_digest,
                ),
                implementation=None,
                adapters=(BackendAdapter(
                    adapter_id="cpuless-corpus/cpuless",
                    carrier_id=CPULESS_CARRIER,
                    activate=_activate_cpuless,
                    adapter_digest=corpus_digest,
                ),),
            ))

    return ImplementationCatalog(tuple(entries))


def bootstrap_provider(profile: str = "development"):
    """The initial-state source per profile: the original NE EXE for
    development/verification, the EXE-free data-only boot image for detached
    and release compositions (an ExeBootstrapProvider carries the
    original-exe capability those profiles FORBID)."""
    if profile in ("detached", "release"):
        from dos_re.execution import BuildImageBootstrapProvider
        return BuildImageBootstrapProvider(
            provider_id="simantw-boot-image",
            state_outputs=("win16-machine",),
            provider_digest="simantw-boot-image-v1",
        )
    return ExeBootstrapProvider(
        provider_id="simantw-ne-exe",
        state_outputs=("win16-machine",),
        provider_digest="simantw-ne-exe-v1",
    )


def configuration(profile: str, *, selected_overrides=(),
                  provider_preference=("interpreted-baseline",)):
    return profile_configuration(
        profile,
        program_identity=PROGRAM_KEY,
        product_profile="development",
        provider_preference=provider_preference,
        selected_overrides=selected_overrides,
        bootstrap_provider=bootstrap_provider(profile),
    )


def plan(profile: str, coverage_source, seg_bases, *,
         selected_overrides=(), provider_preference=("interpreted-baseline",)):
    coverage = coverage_source.coverage_for("development")
    contained = None
    if hasattr(coverage_source, "edges"):
        # Atlas-backed planning: attribute reachable interior points to
        # their containing functions so a function's owner owns its points.
        contained = {}
        for edge in coverage_source.edges():
            if (edge.status == "containment"
                    and edge.target in coverage.reachable
                    and ":point:" in edge.target):
                contained.setdefault(edge.source, set()).add(edge.target)
    catalog = build_catalog(seg_bases, coverage.reachable, contained)
    return plan_execution(
        configuration(profile, selected_overrides=selected_overrides,
                      provider_preference=provider_preference),
        coverage_source, catalog)


def conservative_coverage(machine) -> "ProgramCoverage":
    """Planner coverage from COMMITTED sources only — no Atlas.

    The interactive player's composition must be deterministic from the
    checkout alone: a recording is hook-config-specific, so the installed
    hook set must never depend on a disposable analysis artifact
    (``artifacts/atlas``).  Reachable = everything the catalog can claim
    (all island entries + the generated corpus + the NE entry); the
    Atlas-refined, narrower coverage is the ANALYSIS path (scripts/plan.py).
    """
    from dos_re.execution import ProgramCoverage

    from . import hooks

    header = machine.exe.header
    entry = function_id(machine.seg_bases[header.entry_seg - 1],
                        header.entry_ip)
    reachable = {entry}
    reachable.update(function_id(cs, ip)
                     for (cs, ip) in hooks.island_addresses(machine.seg_bases))
    if CPULESS_CORPUS_DIR.is_dir():
        reachable.update(_corpus_targets(CPULESS_CORPUS_DIR))
    return ProgramCoverage(
        roots=(entry,),
        reachable=frozenset(reachable),
        evidence_identity="simant-conservative-catalog-targets-v1",
    )


def development_plan(machine, *, selected_overrides=("islands",)):
    """The interactive player's plan: development profile over the
    deterministic conservative coverage.  With ``islands`` selected this
    binds every hand-recovered island — the historic hooks-on composition,
    now selected and installed through the plan."""
    coverage = conservative_coverage(machine)
    catalog = build_catalog(machine.seg_bases, coverage.reachable)
    return plan_execution(configuration("development",
                                        selected_overrides=selected_overrides),
                          coverage, catalog)


def detached_plan(machine, *, graph_dir: Path = VMLESS_GRAPH_DIR,
                  source_exe: tuple[str, str] | None = None):
    """The detached player's plan, deterministic from the generated
    artifacts' own claims (no Atlas): reachable = the lifted graph's declared
    targets + the CPUless corpus + the entry.  The detached profile forbids
    original-exe/original-code/interpreter, so the interpreted baseline is
    policy-rejected and every binding is generated; ``FallbackPolicy
    .FORBIDDEN`` makes ``bind_plan_implementations`` arm the interpreter
    wall — what ``boot_vmless_machine`` armed by hand."""
    from dos_re.execution import ProgramCoverage

    if source_exe is not None:
        # Identity from provenance (the boot manifest), never the EXE file:
        # detached planning must work with the executable physically absent.
        use_image(*source_exe)
    # The boot image is captured at instruction zero, so the restored CPU
    # state IS the entry (the stripped program identity's header segment
    # numbering is not authoritative for the flat layout).
    entry = function_id(machine.cpu.s.cs, machine.cpu.s.ip)
    reachable = {entry} | _graph_targets(graph_dir)
    if CPULESS_CORPUS_DIR.is_dir():
        reachable |= _corpus_targets(CPULESS_CORPUS_DIR)
    coverage = ProgramCoverage(
        roots=(entry,),
        reachable=frozenset(reachable),
        evidence_identity="simant-detached-artifact-claims-v1",
    )
    catalog = build_catalog(machine.seg_bases, coverage.reachable,
                            graph_dir=graph_dir)
    return plan_execution(
        configuration("detached",
                      provider_preference=("vmless-graph", "cpuless-corpus")),
        coverage, catalog)


def artifact_recorder(machine, out_dir, *, role="candidate", plan=None):
    """Build an interactive ReplayArtifact recorder for a running machine.

    The base continuation + the execution profile are captured HERE (the
    machine state and composition at record start); the returned recorder's
    tap surface (arrival/clock_sample/dialog_event/messagebox_result/quit)
    is what the interactive driver + dialog/message engines already call.
    ``role`` is an operator claim: ``oracle`` only for the untouched
    interpreter (--no-hooks), else ``candidate`` (earns trust via verify).
    """
    from win16.continuation import CONTINUATION_SCHEMA, capture_continuation
    from win16.replay import ArtifactRecorder
    from win16.replay_driver import PROJECTION_SCHEMA
    from dos_re.replay import ReplayExecutionIdentity

    comp = "interpreted-cpu:no-hooks" if role == "oracle" else (
        f"composition:{plan.plan_digest[:16]}" if plan is not None
        else "interpreted-cpu:recorded")
    profile = ReplayExecutionIdentity(
        profile_id=f"win16-{role}-{Path(out_dir).name}",
        role=role,
        implementation=comp,
        image=str(image_identity()),
        runtime="win16-re",
        devices="win16-api-surface",
        continuation_schema=CONTINUATION_SCHEMA,
        projection_schema=PROJECTION_SCHEMA,
    )
    start_instr = machine.cpu.instruction_count
    base = capture_continuation(machine, event_cursor=0,
                               note=f"interactive record start @{start_instr}")
    return ArtifactRecorder(
        out_dir, timeline_id=f"win16:{Path(out_dir).name}",
        profile=profile, base_state=base, start_instruction=start_instr,
        metadata={"recorded_role": role, "composition": comp})


def boot_detached(boot_dir=None, *, graph_dir=None, game_root=None):
    """The detached composition, constructed the one canonical way: state-only
    boot-image load (inside the caller's EXE-access guard), the deterministic
    detached plan, and ``bind_plan_implementations`` (which arms the
    interpreter wall and installs the graph via the plan adapter).  Returns
    ``(machine, manifest, plan)``.  Shared by the player and the headless
    replay runner so the composition can never drift between them."""
    import sys as _sys

    from dos_re.execution import bind_plan_implementations
    from win16.bootimage import load_boot_image

    from . import vmless_boot as vb

    boot = Path(boot_dir) if boot_dir else vb.BOOT_DIR
    machine, manifest = load_boot_image(
        boot, vb.registry_factory, game_root=game_root or vb.DATA_ROOT)
    _sys.setrecursionlimit(200_000)  # lifted chains mirror the guest stack
    src = manifest["source_exe"]
    plan = detached_plan(machine,
                         graph_dir=Path(graph_dir) if graph_dir
                         else Path(vb.LIFT_DIR),
                         source_exe=(src["name"], src["sha256"]))
    bind_plan_implementations(machine, plan, carrier_id=INTERPRETED_CARRIER)
    return machine, manifest, plan
