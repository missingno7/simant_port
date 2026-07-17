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

Output:
    ``--out observed.json``       {"executed": ["CS:IP", ...]}  -- every function
                                  whose ENTRY ran (the closure roots + the
                                  runtime/static-only split).
    ``--dyn-out indirect_sites.json``  {"sites": [{"site": "CS:IP",
                                  "targets": {"CS:IP": count, ...}}, ...]}  --
                                  per-site observed dynamic-dispatch targets
                                  (near call/jmp indirect + ISR-chain far jmp),
                                  the evidence dos_re's dyn/ISR-chain gates and
                                  the composability fixpoint consume.

    pypy scripts/entry_probe.py cold_nohooks --out artifacts/observed.json \
        --dyn-out artifacts/indirect_sites.json

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
    """A ``cpu.coverage_telemetry`` that records IR function entries AND per-site
    dynamic-dispatch target evidence (the capture side of the capture<->close
    fixpoint, dos_re cpuless_closure).

    ``record_interpreted_instruction`` runs on EVERY interpreted instruction, so
    it stays as tight as possible: a frozenset membership test for the entry
    set, and -- for the dispatch capture -- a single ``pending`` slot. When a
    NEAR-indirect dispatch site (``call [..]`` reg 2, ``jmp [..]`` reg 4) or an
    ISR-CHAIN far ``jmp [..]`` (reg 5) executes, the VERY NEXT interpreted
    instruction is its resolved target (the dispatch transfers control with no
    instruction in between), so we stash the site and bind it on the next call.
    Far-indirect CALLs (reg 3, the API/platform thunks) are NOT captured: their
    target is a Python hook, not an interpreted instruction, so "next
    interpreted" would misattribute the post-return continuation."""

    __slots__ = ("entries", "sites", "observed", "dyn", "pending")

    def __init__(self, entries: frozenset[tuple[int, int]],
                 sites: frozenset[tuple[int, int]]):
        self.entries = entries
        self.sites = sites
        self.observed: set[tuple[int, int]] = set()
        # site (cs,ip) -> {target (cs,ip): count}
        self.dyn: dict[tuple[int, int], dict[tuple[int, int], int]] = {}
        self.pending: tuple[int, int] | None = None

    def record_interpreted_instruction(self, addr) -> None:
        pend = self.pending
        if pend is not None:
            tgts = self.dyn.get(pend)
            if tgts is None:
                self.dyn[pend] = {addr: 1}
            else:
                tgts[addr] = tgts.get(addr, 0) + 1
            self.pending = None
        if addr in self.entries:
            self.observed.add(addr)
        if addr in self.sites:
            self.pending = addr

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


def _modrm_reg(hexbytes: str) -> int | None:
    """The ModRM reg/opcode-extension field of a decoded instruction, skipping
    prefix bytes. For an 0xFF-group transfer this is the /digit that separates
    NEAR (call /2, jmp /4) from FAR (call /3, jmp /5) indirect."""
    b = bytes.fromhex(hexbytes)
    j = 0
    while j < len(b) and b[j] in (0x26, 0x2E, 0x36, 0x3E, 0xF0, 0xF2, 0xF3,
                                  0x66, 0x67):
        j += 1
    j += 1                                   # opcode
    if j < len(b):
        return (b[j] >> 3) & 7
    return None


def _dispatch_sites(ir: dict) -> frozenset[tuple[int, int]]:
    """The NEAR-indirect dispatch + ISR-chain sites whose runtime targets the
    dos_re dyn/ISR-chain gates consume: ``call [..]`` /2, ``jmp [..]`` /4 (jump
    tables / message-pump arms), ``jmp [..]`` /5 (far ISR-chain tail). Excludes
    far-indirect CALLs (/3 -- API thunks, serviced by a hook)."""
    out: set[tuple[int, int]] = set()
    for key, fn in ir["functions"].items():
        cs = int(key.split(":")[0], 16)
        for blk in fn["blocks"]:
            for i in blk["instructions"]:
                kind = i.get("kind")
                if kind not in ("call_ind", "jmp_ind"):
                    continue
                reg = _modrm_reg(i["bytes"])
                if (kind == "call_ind" and reg == 2) or \
                        (kind == "jmp_ind" and reg in (4, 5)):
                    out.add((cs, int(i["ip"], 16)))
    return frozenset(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo", help="demo replayed as the deterministic drive")
    ap.add_argument("--ir", default=str(REPO_ROOT / "artifacts" / "recovery_ir.json"))
    ap.add_argument("--budget", type=int, default=1_000_000_000,
                    help="max instructions (default: no practical limit)")
    ap.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "observed.json"))
    ap.add_argument("--dyn-out",
                    default=str(REPO_ROOT / "artifacts" / "indirect_sites.json"),
                    help="per-site observed dynamic-dispatch targets (feeds "
                         "dos_re cpuless_promote --dyn-evidence / cpuless_closure "
                         "--dyn-evidence)")
    args = ap.parse_args(argv)
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    entries = _entry_set(ir)
    sites = _dispatch_sites(ir)
    probe = EntryProbe(entries, sites)

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

    # per-site dynamic-dispatch evidence (the capture side of the fixpoint).
    dyn_sites = []
    for (scs, sip), tgts in sorted(probe.dyn.items()):
        dyn_sites.append({
            "site": f"{scs:04X}:{sip:04X}",
            "targets": {f"{tcs:04X}:{tip:04X}": n
                        for (tcs, tip), n in sorted(tgts.items())},
        })
    dyn_out = Path(args.dyn_out)
    dyn_out.write_text(json.dumps({
        "_notice": "GENERATED by scripts/entry_probe.py -- per-site observed "
                   "dynamic-dispatch targets (near call/jmp indirect + ISR-chain "
                   "far jmp) over a cold interpreter replay. Feeds dos_re "
                   "cpuless_promote/cpuless_closure --dyn-evidence. Disposable.",
        "demo": Path(args.demo).name,
        "sites": dyn_sites,
    }, indent=1), encoding="utf-8")
    fired = sum(len(s["targets"]) for s in dyn_sites)
    print(f"[entry_probe] wrote {dyn_out}: {len(dyn_sites)} of {len(sites)} "
          f"dispatch sites fired, {fired} distinct site->target edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
