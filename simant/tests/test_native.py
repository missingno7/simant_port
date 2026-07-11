"""Native (product) execution mode: a recovered routine run with NO VM, proven
byte-exact against the original ASM.

Captures a real ``_Unpack`` call from a pure-ASM VM, snapshots the machine into an
owned :class:`NativeGameState`, lets the ASM complete the call, then runs
``native_unpack`` over the snapshot — no ``cpu``, no stack, no emulator — and
requires identical decompressed output AND identical exit decoder state.  This is
the endgame direction working end to end for one routine: the VM is the oracle,
the recovered source is the engine.
"""
import pytest

from simant import hooks, runtime           # puts win16 -> dos_re on sys.path
from simant.bridge.dgroup_view import (ByteBackend, SelectorBackend, UnpackState,
                                       UNPACK_STATE_BASE)
from simant.native.state import NativeGameState
from simant.native.unpack import native_unpack

from dos_re.cpu import CPU8086

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="simant assets not present")

# The decoder's persistent exit state — the same fields the island's own
# byte-exact oracle checks.  `match_rem` (0xB7D2) is deliberately EXCLUDED: it is
# scratch only meaningful mid-match-copy; on a chunk that ends otherwise the ASM
# leaves a don't-care value there that no consumer reads (the island oracle omits
# it too), so requiring it would test noise, not behaviour.
_FIELDS = ("win_seg", "thresh", "src_off", "src_seg", "in_rem", "r", "flags",
           "dx", "cx", "resume")


def _unpack_state(view) -> dict:
    return {f: getattr(view, f) for f in _FIELDS}


def test_native_unpack_matches_asm():
    m = runtime.create_machine()                    # pure ASM — no islands
    m.cpu.trace_enabled = False
    cs7 = m.seg_bases[hooks.UNPACK_SEG_INDEX]
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    cap = {}

    orig = CPU8086.step

    def watch(self):
        s = m.cpu.s
        cur = (s.cs & 0xFFFF, s.ip & 0xFFFF)
        if "args" not in cap and cur == (cs7, hooks.UNPACK_OFF):
            sp = s.sp
            # Only a FRESH call (resume == 0) — a mid-op resume has no clean
            # native entry, and native_unpack drives a fresh chunk.
            if m.mem.rw(dg, UNPACK_STATE_BASE + 0x14) == 0:
                cap["args"] = (m.mem.rw(s.ss, (sp + 6) & 0xFFFF),      # out_seg
                               m.mem.rw(s.ss, (sp + 4) & 0xFFFF),      # out_off
                               m.mem.rw(s.ss, (sp + 8) & 0xFFFF))      # budget
                cap["ret"] = (m.mem.rw(s.ss, (sp + 2) & 0xFFFF),      # (cs, ip)
                              m.mem.rw(s.ss, sp))
                cap["state"] = NativeGameState.from_machine(m)        # pre-call snapshot
        elif "args" in cap and "done" not in cap and cur == cap["ret"]:
            # the ASM call just returned — capture its output + exit decoder state
            out_seg, out_off, budget = cap["args"]
            out_lin = m.mem._xlat(out_seg, out_off)
            cap["asm_out"] = bytes(m.mem.data[out_lin:out_lin + budget])
            cap["asm_state"] = _unpack_state(
                UnpackState(SelectorBackend(m.mem, dg), UNPACK_STATE_BASE))
            cap["done"] = True
        orig(self)

    CPU8086.step = watch
    try:
        while "done" not in cap and m.cpu.instruction_count < 12_000_000:
            m.cpu.run(200_000)
    finally:
        CPU8086.step = orig
    assert cap.get("done"), "no fresh _Unpack call captured + completed"

    out_seg, out_off, budget = cap["args"]

    # Native run over the owned snapshot — no VM, no cpu, no stack.
    native_out = native_unpack(cap["state"], out_seg, out_off, budget)
    native_state = _unpack_state(
        UnpackState(ByteBackend(cap["state"], cap["state"].dgroup_base), UNPACK_STATE_BASE))

    assert len(native_out) > 0, "decoder produced no output to compare"
    assert native_out == cap["asm_out"][:len(native_out)], \
        "native decompressed output differs from the ASM"
    assert native_state == cap["asm_state"], (
        f"native exit decoder state differs: {native_state} != {cap['asm_state']}")
