"""Captured-state A/B verification harness for recovering stateful routines.

The gameplay/simulation routines read live ant-array and map state through
runtime selectors, so they cannot be exercised with synthetic inputs the way a
pure predicate can, and installing islands to run `verifyislands` over a full
session desyncs a no-hooks demo.  This harness closes that gap: it replays a
demo with NO hooks (a faithful drive), catches the N-th real call of a target
routine, snapshots the exact pre-state, runs the ORIGINAL ASM to the routine's
return (subroutine calls and all), then runs the island from the SAME pre-state
and diffs the full memory image + registers.

Because it drives the real binary, a test using it is gated on the assets AND a
demo being present (like test_native.py) — it runs on the porter's machine,
skips elsewhere.  The demo is `$SIMANT_DEMO` or a cold-start demo named on the
call; capture happens once per test, so it stays cheap.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from simant import hooks, runtime            # noqa: F401  (puts win16 on sys.path)
from win16.demo import DemoDriver, DemoEnded

from dos_re.cpu import CPU8086

_REGS = ("ax", "bx", "cx", "dx", "si", "di", "bp", "sp", "cs", "ip",
         "ds", "es", "ss", "flags")


def demo_path(default: str = "cold_nohooks") -> Path | None:
    """The demo to drive a capture: $SIMANT_DEMO, else `default` in the repo
    root; None if neither exists (the test skips)."""
    for cand in (os.environ.get("SIMANT_DEMO"), default):
        if cand and Path(cand).exists():
            return Path(cand)
    return None


@dataclass
class CaptureResult:
    reached: bool                       # was the call seen at all?
    instr: int                          # instruction count at the call
    mem_diffs: list                     # [(linear, asm_byte, isl_byte)]
    reg_diffs: dict                     # {reg: (asm, isl)}
    stack_low: int                      # linear addr of ss:sp at the call (frame top)


def capture_call_ab(seg_index: int, off: int, island_factory, demo: Path | None, *,
                    nth: int = 1, max_asm_steps: int = 5_000_000,
                    budget: int = 260_000_000) -> CaptureResult:
    """A/B the island against the ASM at the nth call of seg_index:off, and
    return the diffs.  `demo` drives input (gameplay routines); pass None to
    free-run from boot (routines reached during load, e.g. _Unpack)."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    cpu, s, mem = m.cpu, m.cpu.s, m.mem
    target_cs = m.seg_bases[seg_index]
    if demo is not None:
        DemoDriver(str(demo)).install(m.api.services["system"])
    island = island_factory(m, off)

    seen = {"n": 0}
    result = {"r": CaptureResult(False, 0, [], {}, 0)}
    _orig = CPU8086.step

    def watch(self):
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (target_cs & 0xFFFF, off & 0xFFFF):
            seen["n"] += 1
            if seen["n"] == nth:
                _do_ab()
                raise _Done
        return _orig(self)

    def _do_ab():
        data0 = bytearray(mem.data)
        regs0 = {k: getattr(s, k) for k in _REGS}
        ret_ip = mem.rw(s.ss, s.sp)
        ret_cs = mem.rw(s.ss, (s.sp + 2) & 0xFFFF)
        stack_low = mem._xlat(s.ss, s.sp)
        # --- ASM to the routine's own return (subcalls return elsewhere) ---
        CPU8086.step = _orig
        for _ in range(max_asm_steps):
            cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (ret_cs & 0xFFFF, ret_ip & 0xFFFF):
                break
        asm_mem = bytes(mem.data)
        asm_regs = {k: getattr(s, k) for k in _REGS}
        # --- restore, run the island from the same pre-state ---
        mem.data[:] = data0
        for k, v in regs0.items():
            setattr(s, k, v)
        island(cpu)
        isl_mem = bytes(mem.data)
        isl_regs = {k: getattr(s, k) for k in _REGS}
        diffs = [(i, asm_mem[i], isl_mem[i])
                 for i in range(len(asm_mem)) if asm_mem[i] != isl_mem[i]]
        rdiffs = {k: (asm_regs[k], isl_regs[k])
                  for k in _REGS if asm_regs[k] != isl_regs[k]}
        result["r"] = CaptureResult(True, cpu.instruction_count, diffs, rdiffs,
                                    stack_low)

    class _Done(Exception):
        pass

    CPU8086.step = watch
    try:
        while cpu.instruction_count < budget:
            cpu.run(200_000)
    except (_Done, DemoEnded):
        pass
    finally:
        CPU8086.step = _orig
    return result["r"]
