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


def program() -> ProgramIdentity:
    return ProgramIdentity(PROGRAM_KEY)


@lru_cache(maxsize=1)
def image_identity() -> ImageIdentity:
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


# --- adapters (installation mechanics; run at bind time only) ---------------

def _install_islands(machine, targets) -> None:
    """Install exactly the plan-selected islands as replacement hooks."""
    from . import hooks
    only = {address_of(t) for t in targets}
    installed = hooks.install(machine, only=only)
    if installed != len(only):
        raise AssertionError(
            f"island adapter installed {installed} of {len(only)} selected")


def build_catalog(seg_bases, reachable: frozenset[str]) -> ImplementationCatalog:
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

    if CPULESS_CORPUS_DIR.is_dir():
        corpus_targets = _corpus_targets(CPULESS_CORPUS_DIR) & reachable
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


def bootstrap_provider() -> ExeBootstrapProvider:
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
        bootstrap_provider=bootstrap_provider(),
    )


def plan(profile: str, coverage_source, seg_bases, *,
         selected_overrides=(), provider_preference=("interpreted-baseline",)):
    coverage = coverage_source.coverage_for("development")
    catalog = build_catalog(seg_bases, coverage.reachable)
    return plan_execution(
        configuration(profile, selected_overrides=selected_overrides,
                      provider_preference=provider_preference),
        coverage_source, catalog)
