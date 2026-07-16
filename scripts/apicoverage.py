"""apicoverage — SIMANTW's Win16 API coverage report.

Joins the recovery IR's ``api:*`` platform-effect surface
(artifacts/recovery_ir.json, scripts/irgen.py) against win16_re's implemented
API registry, plus runtime-exercise data from an instrumented demo replay on
the STRICT VMless runner (the shipping configuration: boot-image machine,
poison armed, EXE guarded) — win16.apicoverage is the generic mechanism, this
wrapper binds it to SimAnt.  Per API target: identity (honest ``unnamed``
where nothing names it), static call sites + calling symbols, implementation
status (handler/equate/tripwire), runtime dispatch count, classification.
Plus the GetProcAddress-minted dynamic surface (SimAnt's MMSYSTEM MIDI path)
and a per-service INT summary (int21:<AH>/int2f).

Writes ``artifacts/api_coverage.json`` (gitignored, regenerable) and prints
the human-readable table.  The full cold_nohooks replay takes ~4-8 min;
``--budget N`` replays only the first N instructions for a quick pass, and
``--no-replay`` skips runtime instrumentation entirely (static join only).

    python scripts/apicoverage.py [--demo cold_nohooks] [--budget N]
                                  [--no-replay] [--ir artifacts/recovery_ir.json]
                                  [--out artifacts/api_coverage.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant.vmless_boot as vb  # noqa: E402
import win16  # noqa: E402,F401

from dos_re.independence import exe_access_guard_from_manifest  # noqa: E402
from win16.apicoverage import (  # noqa: E402
    build_coverage, format_table, instrument_machine)
from win16.bootimage import load_boot_manifest  # noqa: E402

DEFAULT_IR = REPO_ROOT / "artifacts" / "recovery_ir.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "api_coverage.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--demo", default="cold_nohooks",
                    help="demo to replay for runtime-exercise data")
    ap.add_argument("--budget", type=int, default=1_000_000_000,
                    help="instruction budget (prefix replay for quick runs)")
    ap.add_argument("--no-replay", action="store_true",
                    help="static join only (no runtime instrumentation)")
    ap.add_argument("--boot-dir", default=str(vb.BOOT_DIR))
    ap.add_argument("--lift-dir", default=str(vb.LIFT_DIR))
    ap.add_argument("--risk-rows", type=int, default=40)
    args = ap.parse_args(argv)

    ir_path = Path(args.ir)
    if not ir_path.exists():
        raise SystemExit(f"{ir_path} not generated — run: python scripts/irgen.py")
    doc = json.loads(ir_path.read_text(encoding="utf-8"))

    boot_dir = Path(args.boot_dir)
    if not (boot_dir / "manifest.json").exists():
        raise SystemExit(f"{boot_dir} has no boot image — build it: "
                         f"python scripts/build_vmless_boot_image.py")
    manifest = load_boot_manifest(boot_dir)

    with exe_access_guard_from_manifest(manifest):
        machine, manifest, installed = vb.boot_strict(
            boot_dir, lift_dir=args.lift_dir)
        sys.setrecursionlimit(200_000)   # lifted chains mirror the guest stack

        counts = None
        if not args.no_replay:
            counts = instrument_machine(machine)
            from play_vmless import replay  # noqa: E402 (scripts/ on sys.path)
            demo_path = vb.resolve_demo(args.demo)
            print(f"[apicoverage] strict replay of {demo_path.name} "
                  f"({len(installed)} modules, budget {args.budget:,}) ...")
            status, events = replay(machine, demo_path, args.budget)
            counts.description = (
                f"strict VMless replay demo={demo_path.name} "
                f"events={events} instructions="
                f"{machine.cpu.instruction_count:,} ({status})")
            print(f"[apicoverage] {counts.description}")

        report = build_coverage(doc, machine.api, runtime=counts)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")

    print()
    print(format_table(report, risk_rows=args.risk_rows))
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
