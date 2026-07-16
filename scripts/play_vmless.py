"""play_vmless — SimAnt's strict-VMless runner (dos_re_2.0 §1a + §1a').

Both hard walls, enforced physically:

* **VMless execution wall** — the full lifted graph (scripts/liftemit.py +
  liftlink.py output) is installed and ``cpu.interp_forbidden`` is armed from
  instruction zero: any attempt to fetch/decode/execute an original
  instruction raises with CS:IP; interpretation is impossible, not merely
  unused.
* **EXE-independence wall** — the machine boots from the generated data-only
  boot image (scripts/build_vmless_boot_image.py): no NE parsing, no
  SIMANTW.EXE read (physically absent works), recovered code bytes poisoned,
  and ``builtins.open`` guarded against the EXE by name AND content hash for
  the whole session.

This module's import graph deliberately never reaches the NE loader or the
EXE path constants — proven by scripts/lint_vmless_independence.py.

    python scripts/play_vmless.py --demo cold_nohooks         # headless replay
    python scripts/play_vmless.py --demo cold_nohooks --collect-frontier
                                                              # audit mode: poison
                                                              # in COLLECT, frontier
                                                              # must be EMPTY
    python scripts/play_vmless.py                             # interactive
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import simant._env  # noqa: E402,F401  (puts win16_re on sys.path)
from win16.console import make_console_safe  # noqa: E402

make_console_safe()   # the banner's dashes must not become mojibake (win16/console.py)

import simant.vmless_boot as vb  # noqa: E402
import win16  # noqa: E402,F401

from dos_re.independence import exe_access_guard_from_manifest  # noqa: E402
from win16.bootimage import independence_report, load_boot_manifest  # noqa: E402
from win16.demo import DemoDivergence, DemoDriver, DemoEnded  # noqa: E402
from win16.vmsnap import digest  # noqa: E402

#: The standalone-release version (scripts/deploy_vmless.py packages this
#: runner; the deploy and its smoke tests read the constant from here).
VMLESS_RELEASE = "0.1.0-pre"


def banner(manifest: dict, installed: dict, *, exe_present: bool,
           collect: bool) -> None:
    print("=" * 72)
    print(f"SimAnt strict-VMless runner — release {VMLESS_RELEASE}")
    print(independence_report(manifest, exe_present_at_runtime=False))
    print(f"Lifted graph: {len(installed)} modules installed "
          f"(entries configured interpreted: {len(vb.STRICT_SKIP)})")
    for key, why in vb.CORPUS_EXCLUSIONS.items():
        print(f"Corpus exclusion (outside the graph, fail-loud): {key} {why}")
    print(f"Source EXE present on disk this run: "
          f"{'yes (guarded, never opened)' if exe_present else 'NO'}")
    mode = ("COLLECT (diagnostic: uncovered addresses recorded, run "
            "continues)" if collect else "ARMED from instruction zero")
    print(f"VMless wall: interpreter poison {mode}")
    print("=" * 72)


def replay(machine, demo_path: Path, budget: int) -> tuple[str, int]:
    driver = DemoDriver(demo_path)
    if driver.snapshot:
        raise SystemExit(
            f"demo {demo_path.name} is anchored to snapshot "
            f"{driver.snapshot!r}; the strict runner boots only from the "
            f"boot image (record cold demos for it)")
    driver.install(machine.api.services["system"])
    cpu = machine.cpu
    status = "budget exhausted"
    while cpu.instruction_count < budget:
        try:
            cpu.run(2_000)
        except DemoEnded as exc:
            status = f"demo ended: {exc}"
            break
        except DemoDivergence as exc:
            print(f"[play_vmless] DIVERGENCE: {exc}", file=sys.stderr)
            raise SystemExit(2)
    return status, driver._ei


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boot-dir", default=str(vb.BOOT_DIR),
                    help="the generated data-only boot image directory")
    ap.add_argument("--lift-dir", default=str(vb.LIFT_DIR))
    ap.add_argument("--demo", default=None,
                    help="replay this demo headlessly (name or path)")
    ap.add_argument("--budget", type=int, default=1_000_000_000)
    ap.add_argument("--collect-frontier", action="store_true",
                    help="runtime audit mode: the poison RECORDS uncovered "
                         "addresses instead of raising; the run passes only "
                         "if the collected frontier is EMPTY")
    ap.add_argument("--game-root", default=None,
                    help="directory holding the game DATA files (fonts/"
                         "sounds; default: the system pickle's file root)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="interactive mode: time multiplier")
    ap.add_argument("--scale", type=int, default=1,
                    help="interactive mode: integer pixel scale")
    args = ap.parse_args(argv)

    boot_dir = Path(args.boot_dir)
    if not (boot_dir / "manifest.json").exists():
        raise SystemExit(f"{boot_dir} has no boot image — build it: "
                         f"python scripts/build_vmless_boot_image.py")
    manifest = load_boot_manifest(boot_dir)
    exe_present = any(p.name.upper() == manifest["source_exe"]["name"].upper()
                      for p in Path(REPO_ROOT / "assets").rglob("*")
                      if p.is_file()) if (REPO_ROOT / "assets").exists() else False

    with exe_access_guard_from_manifest(manifest):
        machine, manifest, installed = vb.boot_strict(
            boot_dir, lift_dir=args.lift_dir, game_root=args.game_root)
        sys.setrecursionlimit(200_000)   # lifted chains mirror the guest stack

        frontier: set | None = None
        if args.collect_frontier:
            frontier = set()
            machine.cpu.interp_frontier = frontier

        banner(manifest, installed, exe_present=exe_present,
               collect=args.collect_frontier)

        if args.demo:
            demo_path = vb.resolve_demo(args.demo)
            status, events = replay(machine, demo_path, args.budget)
            sysobj = machine.api.services["system"]
            print(f"[play_vmless] {status}")
            print(f"[play_vmless] events consumed: {events}")
            print(f"[play_vmless] instructions: "
                  f"{machine.cpu.instruction_count:,}")
            print(f"[play_vmless] clock: {sysobj.clock_ms} ms")
            print(f"[play_vmless] final digest: {digest(machine)}")
            print(f"[play_vmless] (a poisoned-image digest equals an EXE-full "
                  f"oracle digest only under the poison mask — "
                  f"scripts/checkpoints.py --mask-poison)")
            if frontier is not None:
                if frontier:
                    print(f"[play_vmless] FRONTIER NOT EMPTY: {len(frontier)} "
                          f"uncovered address(es):", file=sys.stderr)
                    for cs, ip in sorted(frontier)[:40]:
                        print(f"    {cs:04X}:{ip:04X}", file=sys.stderr)
                    print("VMless wall: DOES NOT HOLD")
                    return 3
                print(f"[play_vmless] frontier EMPTY over "
                      f"{machine.cpu.instruction_count:,} instructions")
            print("VMless wall: HOLDS")
            return 0

        # Interactive: reuse play.py's driver/GUI with the strict machine.
        # (Deferred import: play.py's module graph names the EXE path — it is
        # never executed for the machine, which is already booted EXE-free.)
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from play import PlayApp  # noqa: E402
        app = PlayApp(None, 0, args.speed, args.scale, hooks=False,
                      machine=machine)
        app.run()
        if app.crashed:
            # The worker already printed the full stop report when it died;
            # repeat the verdict as the runner's LAST console output so a
            # "silently frozen window" session can never end quietly.
            print("\n" + "=" * 72, file=sys.stderr)
            print(f"[play_vmless] the session ended with a VM STOP:",
                  file=sys.stderr)
            print(f"[play_vmless] {app.status}", file=sys.stderr)
            print("=" * 72, file=sys.stderr, flush=True)
            return 1
        print(f"[play_vmless] session ended: {app.status}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
