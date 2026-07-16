"""liftverify — prove a machine-lifted hook byte-exact against SimAnt's own ASM.

The Win16 flavour of `dos_re/tools/liftverify.py`.  Point it at a snapshot and
the demo recorded from it, name the functions to lift, and it will:

    emit a literal Python hook per entry  (dos_re.lift)
      -> install them on a machine resumed from the snapshot
      -> replay the demo, and on each call of a lifted function re-interpret
         the ORIGINAL ASM from the same pre-state to that hook's continuation,
         diffing full CPU state + memory   (win16.verify -> dos_re HookVerifier)
      -> report ORACLE_PASSING / DIVERGED / NOT_REACHED + block coverage

    python scripts/liftverify.py --snapshot artifacts/snapshots/snap_125747 \
        --demo ghost.jsonl --entry 7:C2D2 --entry 7:C256

Entries are `NE_SEGMENT:OFFSET` (the form SIMANTW.SYM speaks), so they stay
stable across relocations; `--symbol _win_IsWinOpen` resolves through the .SYM.

Why a demo and not a free run: a Win16 snapshot resumed and left to run is not
a terminating workload (the message loop waits for input; a modal wndproc waits
forever).  The demo is the deterministic drive, and it is our evidence baseline
anyway — the same one `replay.py` uses.

Sampling: each verified call clones the machine TWICE and re-runs the ASM
oracle, so a hot function would crawl.  Each function is verified `--samples`
times and then retired from verification (it keeps running) — per-hook, so a hot
function never starves the others' budget.  Coverage reports which basic blocks
the sample actually exercised, so "verified" never overstates.

Run this under **CPython, not PyPy**.  Verification is thousands of short bursts
(clone, re-interpret a few dozen instructions, diff) rather than one long
interpretation loop, so the JIT never amortises while pickle and allocation get
slower: measured 42.5 ms vs 70.5 ms per verification, and 10.2 ms vs 49.8 ms per
clone.  PyPy stays the right tool for `replay.py` and `play.py`.

A passing lift is NOT recovered source.  It is a verified, refactorable
artifact: the input to the refactor step that produces `simant/recovered/`.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:                                    # the report uses → and — ; keep them on any console
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401  (puts win16_re on sys.path)
import win16  # noqa: E402,F401  (its _env puts the nested dos_re on sys.path)

from dos_re.lift.cfg import scan_function  # noqa: E402
from dos_re.lift.emit import EmitUnsupported, emit_function  # noqa: E402
from dos_re.lift.runtime import LiftRuntimeError  # noqa: E402
from dos_re.verification import HookVerifyDivergence  # noqa: E402
from simant.probes.symbols import nearest_symbol  # noqa: E402
from simant.runtime import create_machine  # noqa: E402
from win16.demo import DemoDriver, DemoEnded  # noqa: E402
from win16.verify import install_lift_verifier  # noqa: E402
from win16.vmsnap import digest, load_snapshot  # noqa: E402

EMIT_DIR = REPO_ROOT / "simant" / "lifted"


def _parse_entry(text: str) -> tuple[int, int]:
    seg, off = text.split(":", 1)
    return int(seg, 0), int(off, 16)


def _resolve_symbol(name: str) -> tuple[int, int]:
    from simant.probes.symbols import _segments
    for seg_i, (_mod, syms) in enumerate(_segments(), start=1):
        for off, sym in syms:
            if sym == name:
                return seg_i, off
    raise SystemExit(f"symbol {name!r} not found in SIMANTW.SYM")


def _probe(machine, cs: int):
    """Interpreter-measured IP delta of one step at `ip` — the decoder's oracle."""
    from win16.verify import clone_machine
    scratch = clone_machine(machine, create_machine)
    cpu = scratch.cpu
    cpu.replacement_hooks.clear()
    cpu.hook_names.clear()
    cpu.hook_verifier = None
    cpu.trace_enabled = False

    def probe(ip: int):
        ip &= 0xFFFF
        cpu.s.cs, cpu.s.ip = cs & 0xFFFF, ip
        try:
            cpu.step()
        except Exception:  # noqa: BLE001 — an unprobeable address is recorded, not fatal
            return None
        return ((cpu.s.ip - ip) & 0xFFFF) or None
    return probe


def _native_pct(src: str) -> float:
    body = [ln for ln in src.splitlines() if ln.lstrip().startswith("# ") and ":" in ln]
    fb = src.count("(interpreter fallback)")
    return 100.0 * (len(body) - fb) / len(body) if body else 0.0


def _load_hook(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, getattr(mod, path.stem)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", required=True, help="anchor the demo was recorded from")
    ap.add_argument("--demo", required=True, help="demo replayed as the drive")
    ap.add_argument("--entry", action="append", default=[], metavar="SEG:OFF",
                    help="NE segment : hex offset (repeatable)")
    ap.add_argument("--symbol", action="append", default=[], metavar="NAME",
                    help="resolve an entry through SIMANTW.SYM (repeatable)")
    ap.add_argument("--samples", type=int, default=5,
                    help="verified calls per function before it is retired from "
                         "verification (each one re-runs the ASM oracle)")
    ap.add_argument("--budget", type=int, default=300_000_000, help="max instructions")
    ap.add_argument("--verify-timeout", type=float, default=20.0,
                    help="wall-clock seconds one ASM-oracle re-run may take")
    ap.add_argument("--emit-dir", default=str(EMIT_DIR))
    args = ap.parse_args(argv)

    entries = [_parse_entry(e) for e in args.entry] + \
              [_resolve_symbol(s) for s in args.symbol]
    if not entries:
        ap.error("no entries (--entry / --symbol)")

    emit_dir = Path(args.emit_dir)
    machine = load_snapshot(args.snapshot, create_machine)
    machine.cpu.trace_enabled = False

    # 1. Emit + install one literal hook per entry.
    hooks: dict[tuple[int, int], object] = {}
    modules: dict[tuple[int, int], object] = {}
    meta: dict[tuple[int, int], tuple[str, str, int, int, float]] = {}
    for seg_i, off in entries:
        cs = machine.seg_bases[seg_i]
        lin = machine.mem.sel_base.get(cs & 0xFFFC, cs * 16)
        fetch = lambda ip, lin=lin: machine.mem.data[lin + (ip & 0xFFFF)]
        label = nearest_symbol(seg_i, off) or f"seg{seg_i}:{off:04X}"
        name = f"lifted_{seg_i}_{off:04x}"

        scan = scan_function(fetch, off, probe=_probe(machine, cs))
        if not scan.liftable:
            reasons = ", ".join(sorted({r.reason for r in scan.refusals}))
            print(f"skip     {label}: not liftable ({reasons})")
            continue
        sig = bytes(machine.mem.data[lin + off:lin + off + 12])
        try:
            src = emit_function(scan, cs, name, signature=sig, coverage=True)
        except EmitUnsupported as exc:
            print(f"skip     {label}: emit-unsupported ({exc})")
            continue

        emit_dir.mkdir(parents=True, exist_ok=True)
        path = emit_dir / f"{name}.py"
        path.write_text(src, encoding="utf-8")
        mod, fn = _load_hook(path)
        key = (cs, off)
        hooks[key] = fn
        modules[key] = mod
        machine.cpu.replacement_hooks[key] = fn
        machine.cpu.hook_names[key] = name
        meta[key] = (label, path.name, len(scan.insts),
                     len(scan.block_leaders()), _native_pct(src))
        print(f"lifted   {label} -> {path.name}  "
              f"({len(scan.insts)} insts, {len(scan.block_leaders())} blocks, "
              f"{_native_pct(src):.0f}% native)")

    if not hooks:
        print("nothing liftable to verify")
        return 1

    # 2. Strict per-call differential verification, driven by the demo.
    #
    # Retire a function from verification the moment its sample budget is met —
    # it keeps RUNNING, we just stop re-running the ASM oracle for it.  This has
    # to happen per call, not per chunk of instructions: these routines are
    # called tens of times per 200k instructions, and each verification clones
    # the machine twice (~40 ms), so a chunk-granular check overshoots the
    # budget by three orders of magnitude.
    to_verify = set(hooks)
    verifier = install_lift_verifier(machine, create_machine, hooks=to_verify,
                                     asm_wall_timeout_s=args.verify_timeout)

    def _retire_when_sampled(_msg: str) -> None:
        for k, n in verifier.counts.items():
            if n >= args.samples:
                verifier.config.hooks.discard(k)
                to_verify.discard(k)

    verifier.config.progress_callback = _retire_when_sampled
    from simant.runtime import resolve_demo
    driver = DemoDriver(resolve_demo(args.demo))
    sysobj = machine.api.services["system"]
    driver.install(sysobj)  # instruction-count-keyed input + GetTickCount timeline

    print(f"\nreplaying {args.demo} ({len(driver.records)} records) with "
          f"{len(hooks)} lifted hook(s), {args.samples} sample(s) each...\n")
    diverged: dict[tuple[int, int], str] = {}
    runaway: dict[tuple[int, int], str] = {}
    # Replay in SMALL steps so the outer loop regains control promptly.  The
    # verification work itself is tiny (a handful of ASM-oracle re-runs, ~0.3s);
    # the trap is over-running past the last sample.  A demo drives on into
    # GetTickCount busy-wait regions that spin at ~3k instr/s, so every chunk of
    # replay done AFTER sampling is complete is pure waste — measured as 40x the
    # whole tool's runtime (0.7s vs 28s) between a 20k and a 200k step.
    STEP = 20_000
    status, done = "budget reached", 0
    try:
        while done < args.budget:
            if not to_verify:
                # Every function met its sample budget: stop now rather than
                # replay dead demo into a busy-wait.
                status = "all functions sampled"
                break
            try:
                done += machine.cpu.run(min(STEP, args.budget - done))
            except LiftRuntimeError as exc:
                bad = next((k for k, n in machine.cpu.hook_names.items()
                            if k in hooks and n in str(exc)), None)
                if bad is None:
                    raise
                runaway[bad] = str(exc)
                machine.cpu.replacement_hooks.pop(bad, None)
                to_verify.discard(bad)
                verifier.config.hooks.discard(bad)
                print(f"runaway  {meta[bad][0]}: {exc}")
                continue
    except DemoEnded:
        status = "demo ended"
    except HookVerifyDivergence as exc:
        status = "DIVERGENCE"
        print(f"\nDIVERGENCE: {exc}\n")
        for key in hooks:
            if meta[key][1].removesuffix(".py") in str(exc):
                diverged[key] = str(exc)
        if not diverged:
            for key in hooks:
                if verifier.counts.get(key):
                    diverged.setdefault(key, str(exc))
    except Exception as exc:  # noqa: BLE001 — the probe reports everything
        status = f"{type(exc).__name__}: {exc}"

    # 3. Report.
    print(f"ran {done:,} instructions ({status}); digest {digest(machine)[:16]}\n")
    rc = 0
    for key, mod in modules.items():
        label, module, insts, blocks, native = meta[key]
        verified = verifier.counts.get(key, 0)
        seen, total = mod.coverage()
        if key in runaway or key in diverged:
            state, rc = "DIVERGED", 1
        elif verified > 0:
            state = "ORACLE_PASSING"
        elif seen > 0:
            state, rc = "NOT_SAMPLED", 1
        else:
            state, rc = "NOT_REACHED", 1
        cov = f"{seen}/{total} blocks"
        if state == "ORACLE_PASSING" and seen < total:
            cov += " PARTIAL COVERAGE"
        print(f"{state:15s} {label}  ({module}: {insts} insts, {native:.0f}% native)"
              f"\n{'':15s}   {verified} call(s) verified byte-exact, {cov}")
    print("\nA passing lift is a verified artifact, not recovered source — "
          "refactor it into simant/recovered/ with these same oracles green.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
