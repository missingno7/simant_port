"""adaptverify — per-call A/B verification of generated CPU/ABI adapters.

The verification tier for M2b adapter routing (scripts/adaptgen.py): replay a
demo, and each time a ROUTED entry is called, run the generated adapter on the
live machine and the ORIGINAL ASM on a clone from the identical pre-state,
then compare the ABI contract:

    * continuation (CS:IP) and the popped return frame (SP)
    * the MSC callee-saved registers (SI, DI, BP, DS, SS)
    * the result register(s) per the entry's contract (AX / DX:AX)
    * the FULL memory image, minus the dead-stack band below the returned SP
      (freed callee frames + scratch the calling convention leaves undefined)

Deliberately NOT compared (the adapter is a contract marshaller, not a
byte-exact carrier like the literal lift): caller-save scratch (BX, CX, ES,
and DX when not a result register), arithmetic flags, and the virtual
instruction-count timeline — the same exemptions every hand-written island in
simant/hooks.py has always had; dos_re's strict HookVerifier profile
(win16.verify) remains the tool for byte-exact literal lifts.

    python scripts/adaptverify.py --demo cold_nohooks              # cold boot
    python scripts/adaptverify.py --snapshot artifacts/snapshots/snap_185520 \
        --demo demo_185520 --entry 6:2A22 --samples 5

Default entry set: one routed entry per ABI shape (ret x args x result) from
artifacts/routing_report.json, so every generator template path is exercised;
--all-routed sweeps the whole routed set instead.

Run under CPython (clone-heavy, like scripts/liftverify.py).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401

from dos_re.lift.naming import GraphNaming  # noqa: E402
from simant.runtime import create_machine, resolve_demo  # noqa: E402
from win16.demo import DemoDriver, DemoDivergence, DemoEnded  # noqa: E402
from win16.verify import clone_machine  # noqa: E402
from win16.vmsnap import load_snapshot  # noqa: E402

DEFAULT_ROUTED_DIR = REPO_ROOT / "simant" / "lifted" / "graph_routed"
DEFAULT_REPORT = REPO_ROOT / "artifacts" / "routing_report.json"

#: dead-stack band below the returned SP that is never compared: freed callee
#: frames (locals + saves) whose bytes the calling convention leaves undefined.
DEAD_STACK_BYTES = 0x800
ASM_MAX_STEPS = 500_000


class AdaptVerifyDivergence(RuntimeError):
    pass


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pick_default_entries(routed: list[dict]) -> list[dict]:
    """One routed entry per ABI shape — deterministic (lowest key per shape)."""
    by_shape: dict[tuple, dict] = {}
    for c in sorted(routed, key=lambda c: c["key"]):
        shape = (c["ret"], bool(c["args"]), c["result"])
        by_shape.setdefault(shape, c)
    return list(by_shape.values())


def _compare(contract: dict, live, clone) -> list[str]:
    """Contract diff between the adapter's machine (live) and the ASM oracle's
    (clone) at the shared continuation.  Returns human-readable diff lines."""
    ls, cs_ = live.cpu.s, clone.cpu.s
    diffs: list[str] = []
    for reg in ("si", "di", "bp", "ds", "ss", "sp"):
        lv, cv = getattr(ls, reg) & 0xFFFF, getattr(cs_, reg) & 0xFFFF
        if lv != cv:
            diffs.append(f"callee-saved {reg.upper()}: adapter={lv:04X} asm={cv:04X}")
    result_regs = {"ax": ("ax",), "dxax": ("ax", "dx"),
                   "tuple_ax_dx": ("ax", "dx"),
                   "none": ()}[contract["result"]]
    for reg in result_regs:
        lv, cv = getattr(ls, reg) & 0xFFFF, getattr(cs_, reg) & 0xFFFF
        if lv != cv:
            diffs.append(f"result {reg.upper()}: adapter={lv:04X} asm={cv:04X}")

    lm, cm = live.mem.data, clone.mem.data
    if len(lm) != len(cm):
        return diffs + [f"memory size {len(lm)} != {len(cm)}"]
    # Mask the dead-stack band below the returned SP (same linear range both
    # sides — SS:SP already proven equal above, else we report and stop).
    ss, sp = ls.ss & 0xFFFF, ls.sp & 0xFFFF
    dead_lo = ((ss << 4) + ((sp - DEAD_STACK_BYTES) & 0xFFFF)) & 0xFFFFF
    dead_hi = ((ss << 4) + sp) & 0xFFFFF
    n_diff = 0
    first = None
    for i in range(len(lm)):
        if lm[i] != cm[i]:
            if dead_lo <= dead_hi:
                if dead_lo <= i < dead_hi:
                    continue
            elif i >= dead_lo or i < dead_hi:     # band wraps the segment top
                continue
            n_diff += 1
            if first is None:
                first = i
    if n_diff:
        diffs.append(f"memory: {n_diff} byte(s) differ, first at linear "
                     f"{first:#07x} (adapter={lm[first]:02X} asm={cm[first]:02X})")
    return diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", required=True)
    ap.add_argument("--snapshot", default=None,
                    help="resume this snapshot (default: cold boot)")
    ap.add_argument("--routed-dir", default=str(DEFAULT_ROUTED_DIR))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--entry", action="append", default=[], metavar="SEG:OFF",
                    help="recovered_map key to verify (repeatable; default: "
                         "one routed entry per ABI shape)")
    ap.add_argument("--all-routed", action="store_true")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--budget", type=int, default=300_000_000)
    args = ap.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    routed = report["routed"]
    if args.all_routed:
        chosen = routed
    elif args.entry:
        by_key = {c["key"]: c for c in routed}
        missing = [k for k in args.entry if k not in by_key]
        if missing:
            raise SystemExit(f"not in the routed set: {missing}")
        chosen = [by_key[k] for k in args.entry]
    else:
        chosen = _pick_default_entries(routed)

    routed_dir = Path(args.routed_dir)
    naming = GraphNaming.load(routed_dir)

    if args.snapshot:
        machine = load_snapshot(args.snapshot, create_machine)
    else:
        machine = create_machine()
    machine.cpu.trace_enabled = False
    cpu = machine.cpu

    counts: dict[str, int] = {}
    diverged: dict[str, str] = {}
    wrapped_keys: set[tuple[int, int]] = set()

    def make_wrapper(contract, fn, key_addr):
        key = contract["key"]

        def wrapper(cpu2):
            if counts.get(key, 0) >= args.samples or key in diverged:
                fn(cpu2)
                return
            clone = clone_machine(machine, create_machine)
            # clone_machine ports game-code hooks over; the ORACLE side must
            # run the original ASM, so strip our adapter wrappers from it.
            for k in wrapped_keys:
                clone.cpu.replacement_hooks.pop(k, None)
                clone.cpu.hook_names.pop(k, None)
            fn(cpu2)                                   # the adapter, live
            counts[key] = counts.get(key, 0) + 1
            s = cpu2.s
            tgt = ((s.cs & 0xFFFF) << 16) | (s.ip & 0xFFFF)
            tgt_sp = s.sp & 0xFFFF
            c = clone.cpu
            for _ in range(ASM_MAX_STEPS):
                c.step()
                if (((c.s.cs & 0xFFFF) << 16) | (c.s.ip & 0xFFFF)) == tgt \
                        and (c.s.sp & 0xFFFF) == tgt_sp:
                    break
            else:
                raise AdaptVerifyDivergence(
                    f"{key} {contract['symbol']}: ASM oracle never reached the "
                    f"adapter's continuation {s.cs:04X}:{s.ip:04X}")
            diffs = _compare(contract, machine, clone)
            if diffs:
                detail = "\n  ".join(diffs)
                diverged[key] = detail
                raise AdaptVerifyDivergence(
                    f"{key} {contract['symbol']} call {counts[key]}:\n  {detail}")

        return wrapper

    installed = []
    for c in chosen:
        stem = naming.stem_of(c["para_key"])
        path = routed_dir / f"{stem}.py"
        mod = _load_module(path)
        fn = getattr(mod, stem)
        key_addr = (c["cs"], c["ip"])
        cpu.replacement_hooks[key_addr] = make_wrapper(c, fn, key_addr)
        cpu.hook_names[key_addr] = f"adaptverify:{stem}"
        wrapped_keys.add(key_addr)
        installed.append(c)

    driver = DemoDriver(resolve_demo(args.demo))
    driver.install(machine.api.services["system"])
    print(f"replaying {args.demo} with {len(installed)} routed adapter(s) "
          f"under per-call ASM A/B ({args.samples} sample(s) each)...")

    status = "budget reached"
    try:
        while cpu.instruction_count < args.budget:
            if all(counts.get(c["key"], 0) >= args.samples for c in installed):
                status = "all entries sampled"
                break
            cpu.run(20_000)
    except DemoEnded:
        status = "demo ended"
    except DemoDivergence as exc:
        status = f"demo divergence: {exc}"
    except AdaptVerifyDivergence as exc:
        status = "DIVERGENCE"
        print(f"\nDIVERGENCE: {exc}\n")
    except Exception as exc:  # noqa: BLE001 — report where it stopped
        status = f"{type(exc).__name__}: {exc}"

    print(f"ran {cpu.instruction_count:,} instructions ({status})\n")
    rc = 0
    for c in installed:
        n = counts.get(c["key"], 0)
        shape = f"{c['ret']}, args={len(c['args'])}, result={c['result']}"
        if c["key"] in diverged:
            state, rc = "DIVERGED", 1
        elif n >= args.samples:
            state = "CONTRACT_PASSING"
        elif n > 0:
            state, rc = "PARTIAL", max(rc, 0)
        else:
            state = "NOT_REACHED"
        print(f"{state:16s} {c['key']} {c['symbol']} ({shape}): "
              f"{n} call(s) A/B-verified")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
