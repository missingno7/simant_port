"""entry_probe -- runtime function-entry trace over a demo replay (task #44).

Records which IR functions ACTUALLY execute during a cold replay, keyed by the
CS:IP function entry.  This is the ground truth for "reachable" that the static
near-call walk over-approximates (a call in a never-taken branch reaches a
target that never runs).  It feeds ``dos_re/tools/cpuless_closure.py --observed``
(cont.241 flagged that flag could not run for lack of a runtime trace).

HOW.  It replays under the plain INTERPRETER (create_machine -- no islands, no
lifted graph), so every guest instruction goes through the interpret path and
fires ``cpu.coverage_telemetry.record_interpreted_instruction((cs, ip))``.  A
tiny telemetry object tests each executed address against the IR function-entry
set; a hit records the function as observed.  (A boot-image/graph run would
execute promoted functions as HOOKS, which never fire that telemetry -- so the
interpreter is exactly the right engine for an execution-reachability probe.)

Output (``--out observed.json``):
    {"executed": ["CS:IP", ...],          # every function whose ENTRY ran
     "call_sites": {"CS:IP": ["target", ...]}}   # per-site observed targets
                                          # (indirect/far), suitable for the
                                          # closure walk's dyn evidence

    pypy scripts/entry_probe.py cold_nohooks --out artifacts/observed.json

Run under **pypy** (a 199M-instruction replay); the per-instruction set probe
is cheap but the replay is long.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from simant.runtime import (assets_present, create_machine,  # noqa: E402
                            resolve_demo)
from win16.demo import DemoDriver, DemoDivergence, DemoEnded  # noqa: E402


class EntryProbe:
    """A ``cpu.coverage_telemetry`` that records only IR function entries.

    ``record_interpreted_instruction`` runs on EVERY interpreted instruction, so
    it stays as tight as possible: a frozenset membership test, and a set add
    only on the (rare) entry hit."""

    __slots__ = ("entries", "observed")

    def __init__(self, entries: frozenset[tuple[int, int]]):
        self.entries = entries
        self.observed: set[tuple[int, int]] = set()

    def record_interpreted_instruction(self, addr) -> None:
        if addr in self.entries:
            self.observed.add(addr)

    # the CPU also calls this on a replacement-hook dispatch; a no-op here (a
    # cold_nohooks run installs only the platform API thunks, never IR funcs).
    def record_hook_unverified(self, addr, name) -> None:  # noqa: D401
        pass

    # unused telemetry surface -- present so the CPU never AttributeErrors.
    def record_hook_verified(self, *a, **k) -> None:
        pass


def _entry_set(ir: dict) -> frozenset[tuple[int, int]]:
    out = set()
    for key in ir["functions"]:
        cs, ip = key.split(":")
        out.add((int(cs, 16), int(ip, 16)))
    return frozenset(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo", help="demo replayed as the deterministic drive")
    ap.add_argument("--ir", default=str(REPO_ROOT / "artifacts" / "recovery_ir.json"))
    ap.add_argument("--budget", type=int, default=1_000_000_000,
                    help="max instructions (default: no practical limit)")
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "observed.json"))
    args = ap.parse_args(argv)
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    entries = _entry_set(ir)
    probe = EntryProbe(entries)

    m = create_machine()
    cpu = m.cpu
    cpu.trace_enabled = False
    cpu.coverage_telemetry = probe
    sysobj = m.api.services["system"]

    driver = DemoDriver(resolve_demo(args.demo))
    driver.install(sysobj)

    status = "budget reached"
    while cpu.instruction_count < args.budget:
        try:
            cpu.run(200_000)
        except DemoEnded:
            status = "demo ended"
            break
        except DemoDivergence as exc:
            status = f"demo divergence: {exc}"
            break
        except Exception as exc:  # noqa: BLE001 -- record where it stopped
            status = f"{type(exc).__name__}: {exc}"
            break

    observed = sorted(f"{cs:04X}:{ip:04X}" for (cs, ip) in probe.observed)
    print(f"[entry_probe] {args.demo}: {status}; "
          f"instr={cpu.instruction_count:,}; "
          f"functions executed: {len(observed)} / {len(entries)}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "_notice": "GENERATED by scripts/entry_probe.py -- runtime function-"
                   "entry trace over a cold interpreter replay. Disposable.",
        "demo": Path(args.demo).name,
        "final_instr": cpu.instruction_count,
        "status": status,
        "executed": observed,
    }, indent=1), encoding="utf-8")
    print(f"[entry_probe] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
