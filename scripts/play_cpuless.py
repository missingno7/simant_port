"""play_cpuless — SimAnt's standalone **CPUless** runner (DOS_RE 2.0 stage 3).

The M4 capstone, and the third and hardest of the walls.  ``play_vmless``
proved the game runs with no *interpretation*: the lifted graph still lived
inside a CPU object, which still owned the registers, the stack and the clock.
This runner has no CPU at all.  What executes is the promoted CPUless corpus —
pure ``func_<cs>_<ip>(mem, plat, *, <regs>)`` modules under
``simant/native/cpuless/`` — over a memory image and a platform seam:

* **the CPUless import wall**, ARMED before anything else is imported
  (``dos_re.lift.standalone.install_import_guard``): any import of the
  interpreter, the CPU carrier, the VMless graph installer, the EXE/VM runtime
  builder, **or SimAnt's own CPU-ABI adapter packages** (``simant.lifted.*`` —
  verification shims, never runtime source) raises.  Relative imports are
  resolved first, so the framework's own intra-package edges cannot slip past.
* **the EXE-independence wall**, inherited from cont.226: the memory image is
  the generated data-only boot image (``artifacts/vmless_boot/``), loaded
  through the loader-FREE path (``win16.cpuless.load_cpuless_image`` — the NE
  loader itself is behind the wall, since it builds a ``CPU8086``), with
  ``builtins.open`` guarded against SIMANTW.EXE by name AND content hash.
* **a CPU-free Windows**: ``win16.cpuless.Win16CpulessPlatform`` services
  ``plat.farcall`` by resolving the import-thunk slot to its ``(module,
  ordinal)``, decoding the pascal arguments the recovered body already pushed,
  and running the handler through ``ApiRegistry.invoke_values`` — args in,
  result out, no emulated stack, no ``ret_far``, no ``cpu``.  ``plat.intr``
  runs the machine's own INT 21h/2Fh surface on the same carrier.

A PARTIAL run is the expected, informative outcome: the 597-function
runtime-reachable closure is composable *in the wall model*, but not all of it
is materialised, so the run stops at the first ``CpuStandaloneWitness`` — an
unpromoted function on the frontier, or a platform effect this host does not
own.  That stop is the empirical frontier the model has only predicted.  There
is deliberately NO interpreter fallback and NO stubbed platform effect: a
silent wrong answer would be worse than a loud stop.

    python scripts/play_cpuless.py                     # from the program entry
    python scripts/play_cpuless.py --entry 275F:0DF3   # from one promoted root
    python scripts/play_cpuless.py --demo cold_nohooks # headless, demo-driven
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import simant._env  # noqa: E402,F401  (puts win16_re on sys.path)
import win16        # noqa: E402,F401  (puts dos_re on sys.path; CPU-free)

from dos_re.lift.standalone import (  # noqa: E402
    CpuStandaloneWitness, install_import_guard, load_recovered, run_deep,
)

#: The CPU-ABI adapters and the lifted graphs are VERIFICATION artifacts: they
#: exist to prove a recovered body byte-exact against the original inside the
#: VM.  If the standalone runner could import them the wall would be
#: meaningless, so they join the framework's base forbidden set.  ``win16.
#: loader`` builds a ``CPU8086`` at module level; ``simant.runtime`` builds the
#: EXE-loading VM machine.
EXTRA_FORBIDDEN = (
    "simant.lifted",        # graph / graph_cpuless / _adapters — CPU-ABI shims
    "simant.hooks",         # the lifted islands: they take a live cpu
    "simant.runtime",       # the EXE loader / VM machine builder
    "win16.loader",         # the NE loader — imports dos_re.cpu
    "win16.bootimage",      # the VM boot path (install_vmless_graph)
)

install_import_guard(extra_forbidden=EXTRA_FORBIDDEN)   # <-- THE WALL, armed

from win16.console import make_console_safe          # noqa: E402
from win16.cpuless import (                          # noqa: E402
    CpuFreeExecutionAttempt, Win16CpulessPlatform, load_cpuless_image,
)

make_console_safe()

CORPUS = "simant.native.cpuless"
BOOT_DIR = REPO_ROOT / "artifacts" / "vmless_boot"
DATA_ROOT = REPO_ROOT / "assets" / "ANTWIN"
DEMOS_DIR = REPO_ROOT / "artifacts" / "demos"
CENSUS = REPO_ROOT / "artifacts" / "cpuless_census.json"
CLOSURE = REPO_ROOT / "artifacts" / "cpuless_closure_observed.json"


def registry_factory():
    """SimAnt's API surface, loader-free.  (Duplicated from
    ``simant.vmless_boot`` rather than imported: that module's own import graph
    is the VMless one; this runner keeps its closure minimal and provably
    CPU-free.)"""
    from win16.api.surface import WINFLAGS_NO_FPU, build_registry
    return build_registry(winflags=WINFLAGS_NO_FPU)


def resolve_demo(name: str) -> Path:
    for cand in (Path(name), DEMOS_DIR / name, DEMOS_DIR / f"{name}.jsonl"):
        if cand.exists():
            return cand
    return DEMOS_DIR / f"{name}.jsonl"


def corpus_size(package: str) -> int:
    d = REPO_ROOT / Path(*package.split("."))
    return len(list(d.glob("func_*.py"))) if d.is_dir() else 0


def entry_key(meta: dict) -> str:
    return f"{meta['cpu']['cs']:04X}:{meta['cpu']['ip']:04X}"


def closure_stats() -> dict:
    """What the wall model predicts for the run we are about to attempt."""
    if not CLOSURE.exists():
        return {}
    doc = json.loads(CLOSURE.read_text(encoding="utf-8"))
    return {"roots": len(doc.get("roots", [])), "reached": doc.get("reached"),
            "promoted": doc.get("promoted_reached"),
            "frontier": len(doc.get("frontier", {}))}


def why_absent(key: str) -> str:
    """The census's own reason a function has no promoted body — so the first
    witness names a WORK ITEM, not just an absence."""
    if not CENSUS.exists():
        return "no census (run scripts/census.py)"
    doc = json.loads(CENSUS.read_text(encoding="utf-8"))
    fn = doc.get("functions", {}).get(key.upper())
    if fn is None:
        return "not in the CPUless census (not carved as a function)"
    parts = [f"tier={fn.get('tier')}"]
    for reason, sites in (fn.get("refusals") or {}).items():
        parts.append(f"{reason} x{len(sites)}")
    return ", ".join(parts)


def banner(manifest: dict, plat, key: str, *, exe_present: bool,
           demo: str | None) -> None:
    stats = closure_stats()
    print("=" * 72)
    print("SimAnt standalone CPUless runner — the hard wall")
    print(f"Corpus: {corpus_size(CORPUS)} promoted CPUless module(s) in {CORPUS}")
    print(f"CPUless wall: ARMED before the first import "
          f"(forbidden: interpreter/CPU carrier, VMless graph, VM runtime, "
          f"{', '.join(EXTRA_FORBIDDEN)})")
    src = manifest["source_exe"]
    print(f"EXE-independence: booted from the data-only image "
          f"({manifest['memory_size'] // 1024}K, sha {manifest['memory_sha256'][:12]}); "
          f"{src['name']} {'present on disk (guarded, never opened)' if exe_present else 'NOT present'}")
    print(f"Windows: CPU-FREE API path "
          f"({len(plat.api.slots)} import thunk slots bound, "
          f"{len(plat.api.entries)} services registered)")
    if stats:
        print(f"Closure (observed, cont.243/249): {stats['roots']} functions "
              f"runtime-reachable; from the program entry {stats['reached']} "
              f"reached, {stats['promoted']} with a promoted body, "
              f"{stats['frontier']} on the frontier — the corpus is COMPOSABLE "
              f"in the wall model, not yet fully MATERIALISED")
    print(f"Root: {key}   demo: {demo or '(none — free run)'}")
    print("=" * 72)


def run(key: str, machine, plat, budget: int) -> int:
    fn = load_recovered(CORPUS, key)      # loud if the root is on the frontier
    meta = json.loads((BOOT_DIR / "state.json").read_text(encoding="utf-8"))
    regs = dict(meta["cpu"])
    params = inspect.signature(fn).parameters
    kwargs = {r: v for r, v in regs.items() if r in params}
    if "_flags_in" in params:
        kwargs["_flags_in"] = regs.get("flags", 2)
    print(f"[play_cpuless] entering {key} with "
          f"{', '.join(f'{k}={v:#x}' for k, v in sorted(kwargs.items()))}")
    out, compat = run_deep(plat.call, fn, **kwargs)
    print(f"[play_cpuless] {key} RETURNED — outputs "
          f"{ {k: hex(v) for k, v in sorted(out.items()) if isinstance(v, int)} }")
    print(f"[play_cpuless] virtual time: {compat['cost']:,} instruction(s)")
    print(f"[play_cpuless] CPU-free Win16 calls serviced: {len(plat.farcalls)}")
    for lab in plat.farcalls[:20]:
        print(f"    {lab}")
    return 0


def sweep(boot_dir: Path, game_root: str) -> int:
    """Run EVERY promoted body as a root, once, on a fresh CPU-free host, and
    classify how it stops.  Not a correctness oracle — a synthetic entry state
    feeds most bodies garbage arguments — but it is the runner's own evidence
    that the host is real: how many promoted bodies execute end to end, how
    many Win16 services the CPU-FREE API path actually delivers, and which
    stops are genuine frontier witnesses rather than bad inputs."""
    import collections
    corpus_dir = REPO_ROOT / Path(*CORPUS.split("."))
    keys = []
    for p in sorted(corpus_dir.glob("func_*.py")):
        para, ip = p.stem.split("_")[1:]
        keys.append(f"{para.upper()}:{ip.upper()}")
    meta = json.loads((boot_dir / "state.json").read_text(encoding="utf-8"))
    tally: collections.Counter = collections.Counter()
    apis: collections.Counter = collections.Counter()
    witnesses: dict[str, str] = {}
    serviced = 0
    for key in keys:
        machine, _ = load_cpuless_image(boot_dir, registry_factory,
                                        game_root=game_root)
        plat = Win16CpulessPlatform(machine)
        try:
            fn = load_recovered(CORPUS, key)
            params = inspect.signature(fn).parameters
            kwargs = {r: v for r, v in meta["cpu"].items() if r in params}
            if "_flags_in" in params:
                kwargs["_flags_in"] = meta["cpu"]["flags"]
            plat.call(fn, **kwargs)
            cls = "ran to completion"
        except CpuFreeExecutionAttempt:
            cls = "CPU-free host boundary (a Win16 callback into guest code)"
        except CpuStandaloneWitness as exc:
            cls = "CPUless frontier witness"
            witnesses.setdefault(key, str(exc)[:120])
        except RecursionError:
            cls = "recursion (tail-dispatch depth)"
        except Exception as exc:                    # noqa: BLE001
            cls = f"{type(exc).__name__} (synthetic-input artifact or frontier)"
            witnesses.setdefault(key, f"{type(exc).__name__}: {exc}"[:120])
        serviced += len(plat.farcalls)
        for lab in plat.farcalls:
            apis[lab.split("(")[0]] += 1
        tally[cls] += 1
    print(f"[sweep] {len(keys)} promoted root(s) exercised on a CPU-free host")
    for cls, n in tally.most_common():
        print(f"  {n:5d}  {cls}")
    print(f"[sweep] Win16 services delivered through the CPU-FREE API path: "
          f"{serviced} call(s), {len(apis)} distinct API(s)")
    for lab, n in apis.most_common(15):
        print(f"    {n:4d}  {lab}")
    if witnesses:
        print(f"[sweep] first witness per stopping root ({len(witnesses)}):")
        for key, why in list(witnesses.items())[:20]:
            print(f"    {key}  {why}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boot-dir", default=str(BOOT_DIR))
    ap.add_argument("--entry", default=None,
                    help="run this CS:IP instead of the program entry")
    ap.add_argument("--demo", default=None, help="drive the run from this demo")
    ap.add_argument("--budget", type=int, default=1_000_000_000,
                    help="virtual-time ceiling (instructions)")
    ap.add_argument("--game-root", default=str(DATA_ROOT))
    ap.add_argument("--sweep", action="store_true",
                    help="run every promoted body as a root and classify how "
                         "it stops (the host's own evidence; not an oracle)")
    args = ap.parse_args(argv)

    boot_dir = Path(args.boot_dir)
    if not (boot_dir / "manifest.json").exists():
        raise SystemExit(f"{boot_dir} has no boot image — build it: "
                         f"python scripts/build_vmless_boot_image.py")
    from dos_re.independence import exe_access_guard_from_manifest
    manifest = json.loads((boot_dir / "manifest.json").read_text(encoding="utf-8"))
    assets = REPO_ROOT / "assets"
    exe_present = assets.exists() and any(
        p.name.upper() == manifest["source_exe"]["name"].upper()
        for p in assets.rglob("*") if p.is_file())

    with exe_access_guard_from_manifest(manifest):
        machine, manifest = load_cpuless_image(
            boot_dir, registry_factory, game_root=args.game_root)
        plat = Win16CpulessPlatform(machine)
        meta = json.loads((boot_dir / "state.json").read_text(encoding="utf-8"))
        key = (args.entry or entry_key(meta)).upper()

        if args.demo:
            from win16.demo import DemoDriver
            driver = DemoDriver(resolve_demo(args.demo))
            if driver.snapshot:
                raise SystemExit(
                    f"demo {args.demo} is anchored to a snapshot; the CPUless "
                    f"runner boots only from the boot image")
            driver.install(machine.api.services["system"])

        banner(manifest, plat, key, exe_present=exe_present, demo=args.demo)

        if args.sweep:
            return sweep(boot_dir, args.game_root)

        try:
            return run(key, machine, plat, args.budget)
        except CpuStandaloneWitness as exc:
            print("=" * 72, file=sys.stderr)
            print("[play_cpuless] CPUless WITNESS — the run stopped at the "
                  "frontier (this is the informative outcome, not a bug):",
                  file=sys.stderr)
            print(f"  {exc}", file=sys.stderr)
            print(f"  census: {why_absent(key)}", file=sys.stderr)
            print(f"  serviced before the stop: {len(plat.farcalls)} "
                  f"CPU-free Win16 call(s)", file=sys.stderr)
            for lab in plat.farcalls[-10:]:
                print(f"    {lab}", file=sys.stderr)
            print("=" * 72, file=sys.stderr)
            return 3
        except CpuFreeExecutionAttempt as exc:
            print("=" * 72, file=sys.stderr)
            print("[play_cpuless] CPU-FREE HOST BOUNDARY — a Win16 service "
                  "asked to execute guest code (a callback):", file=sys.stderr)
            print(f"  {exc}", file=sys.stderr)
            print(f"  after {len(plat.farcalls)} CPU-free Win16 call(s); "
                  f"last: {plat.farcalls[-1] if plat.farcalls else '(none)'}",
                  file=sys.stderr)
            print("  next: dispatch Win16 callbacks into the recovered corpus "
                  "(the window/dialog/enum/timer proc seam).", file=sys.stderr)
            print("=" * 72, file=sys.stderr)
            return 4
        except Exception as exc:            # noqa: BLE001 — report, never mask
            print("=" * 72, file=sys.stderr)
            print(f"[play_cpuless] STOP: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            print(f"  after {len(plat.farcalls)} CPU-free Win16 call(s); "
                  f"last: {plat.farcalls[-1] if plat.farcalls else '(none)'}",
                  file=sys.stderr)
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
