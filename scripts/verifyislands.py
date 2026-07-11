"""verifyislands — prove the PRODUCTION islands (simant/hooks.py) byte-exact
against SimAnt's own ASM over a real demo replay.

This is the deterministic, config-invariant way to compare "islands vs the
original" — the pre2_port / dos_re `HookVerifier` mechanism, pointed at the
hand-written islands the game actually ships with (not the auto-lifted hooks
`liftverify.py` emits).

    python scripts/verifyislands.py --snapshot artifacts/snapshots/snap_185520 \
        --demo artifacts/demos/demo_185520.jsonl [--samples 5] [--only _XferTileColor]

It installs the islands, wraps each (or the `--only` subset) in the verifier,
and replays the demo.  On every island CALL it clones the machine, re-runs the
ORIGINAL ASM from the same pre-state to that island's continuation, and diffs
full CPU state + the whole 4 MB memory image.

Why this is the right tool for "compare demos deterministically":

* It verifies each island AT THAT CALL'S REAL PRE-STATE.  It does NOT compare two
  separate whole-run replays (which desync: v4 demos key input by instruction
  count, and islands change the instruction timeline — see the run_status
  journal / the `demos-are-hook-config-specific` note).  Because each call is
  checked independently against the ASM oracle, the verdict is correct EVEN IF
  the overall replay timeline has drifted — a divergence means the island really
  computes something the ASM doesn't, not that the input landed a frame off.
* A HOOKS-recorded demo replays faithfully and exercises every island over the
  whole session; a no-hooks demo still verifies every island call it reaches
  before any desync-induced crash.

Run under CPython, not PyPy: verification is thousands of short clone+re-run
bursts, so the JIT never amortises (see liftverify.py's note).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401  (puts win16_re on sys.path)
import win16  # noqa: E402,F401  (its _env puts the nested dos_re on sys.path)

from dos_re.verification import HookVerifyDivergence  # noqa: E402
from simant import hooks  # noqa: E402
from simant.probes.symbols import nearest_symbol  # noqa: E402
from simant.runtime import create_machine  # noqa: E402
from win16.demo import DemoDriver, DemoEnded  # noqa: E402
from win16.verify import install_lift_verifier  # noqa: E402
from win16.vmsnap import digest, load_snapshot  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", help="anchor the demo was recorded from")
    ap.add_argument("--demo", help="demo replayed as the drive")
    ap.add_argument("--only", action="append", default=[], metavar="NAME",
                    help="verify only these island(s) by name (repeatable); "
                         "others still run, just unverified")
    ap.add_argument("--samples", type=int, default=5,
                    help="verified calls per island before it is retired from "
                         "verification (each re-runs the ASM oracle; 0 = unlimited)")
    ap.add_argument("--budget", type=int, default=300_000_000, help="max instructions")
    ap.add_argument("--verify-timeout", type=float, default=20.0,
                    help="wall-clock seconds one ASM-oracle re-run may take")
    ap.add_argument("--list", action="store_true", help="list island names and exit")
    args = ap.parse_args(argv)

    if args.list:
        for _si, _off, _sig, _fac, name in hooks._ISLANDS:
            print(name)
        return 0

    machine = load_snapshot(args.snapshot, create_machine)
    machine.cpu.trace_enabled = False

    # 1. Install every production island, then pick which to VERIFY.
    installed = hooks.install(machine)
    # Map (cs, off) -> island name for the installed set.
    key_name: dict[tuple[int, int], str] = {}
    key_seg: dict[tuple[int, int], tuple[int, int]] = {}
    for si, off, sig, _fac, name in hooks._ISLANDS:
        cs = machine.seg_bases[si]
        if machine.mem.block(cs, off, len(sig)) == sig:
            key_name[(cs, off)] = name
            key_seg[(cs, off)] = (si, off)

    only = set(args.only)
    if only:
        unknown = only - {n for n in key_name.values()}
        if unknown:
            ap.error(f"unknown island name(s): {', '.join(sorted(unknown))} "
                     f"(see --list)")
    verify_keys = {k for k, n in key_name.items() if not only or n in only}

    print(f"installed {installed} island(s); verifying {len(verify_keys)} "
          f"of them against the ASM oracle\n")

    # 2. Attach the strict differential verifier to the chosen islands.
    to_verify = set(verify_keys)
    verifier = install_lift_verifier(machine, create_machine, hooks=to_verify,
                                     asm_wall_timeout_s=args.verify_timeout)

    if args.samples > 0:
        def _retire_when_sampled(_msg: str) -> None:
            for k, n in verifier.counts.items():
                if n >= args.samples:
                    verifier.config.hooks.discard(k)
                    to_verify.discard(k)
        verifier.config.progress_callback = _retire_when_sampled

    # 3. Replay the demo as the deterministic drive.
    driver = DemoDriver(args.demo)
    sysobj = machine.api.services["system"]
    driver.install(sysobj)
    print(f"replaying {args.demo} ({len(driver.records)} records) ...\n")

    STEP = 20_000
    diverged: dict[tuple[int, int], str] = {}
    status, done = "budget reached", 0
    while done < args.budget:
        if args.samples > 0 and not to_verify:
            status = "all islands sampled"
            break
        try:
            done += machine.cpu.run(min(STEP, args.budget - done))
        except DemoEnded:
            status = "demo ended"
            break
        except HookVerifyDivergence as exc:
            # Attribute to the island being dispatched (the CPU is parked at its
            # entry — the live hook did NOT run), record it, then RETIRE that
            # island (run its real ASM from here on) and CONTINUE the sweep so
            # one divergence doesn't hide the rest.
            key = (machine.cpu.s.cs & 0xFFFF, machine.cpu.s.ip & 0xFFFF)
            if key not in verify_keys:
                key = next((k for k, n in key_name.items()
                            if n in str(exc) or f"{k[0]:04X}:{k[1]:04X}" in str(exc)),
                           key)
            diverged[key] = str(exc)
            name = key_name.get(key, f"{key[0]:04X}:{key[1]:04X}")
            print(f"\nDIVERGED {name}:\n{str(exc).strip()}\n")
            # Stop VERIFYING it, but keep the island installed and RUNNING — the
            # divergence fired before the live hook ran, and popping a hook while
            # a callback frame is open corrupts the frame accounting.  On the next
            # call the verifier passes it through unchecked.
            to_verify.discard(key)
            verifier.config.hooks.discard(key)
            continue
        except Exception as exc:  # noqa: BLE001 — report everything
            status = f"{type(exc).__name__}: {exc}"
            break

    # 4. Report.
    print(f"ran {done:,} instructions ({status}); "
          f"digest {digest(machine)[:16]}\n")
    rc = 0
    passing = reached = notreached = 0
    for k in sorted(verify_keys, key=lambda k: key_name[k]):
        name = key_name[k]
        si, off = key_seg[k]
        label = nearest_symbol(si, off) or name
        verified = verifier.counts.get(k, 0)
        if k in diverged:
            state, rc = "DIVERGED", 1
        elif verified > 0:
            state, passing = "ORACLE_PASSING", passing + 1
        else:
            state, notreached = "NOT_REACHED", notreached + 1
        line = f"{state:15s} {name:26s} {verified} call(s) byte-exact  [{label}]"
        print(line)
    print(f"\n{passing} passing, {len(diverged)} diverged, {notreached} not reached "
          f"of {len(verify_keys)} verified islands.")
    if diverged:
        print("A DIVERGED island computes something the original ASM does not — "
              "that is a real bug in the recovered logic; fix it before trusting "
              "the island in play.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
