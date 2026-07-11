"""Validate the captured-state A/B harness (capture_ab.capture_call_ab).

Drives a REAL routine call and A/Bs the island against the original ASM from the
identical pre-state — the mechanism future stateful-simulation recoveries verify
with.  Validated on `_Unpack` (reached during boot, so no demo needed): the only
differences must be the KNOWN don't-care scratch — match_rem (DGROUP:0xB7D2) and
the freed local stack frame (below the entry ss:sp) — never the decode output.
"""
import pytest

from simant import hooks, runtime
from simant.tests.capture_ab import capture_call_ab

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="simant assets not present")

_MATCH_REM_DG_OFF = 0x00B7D2                     # UnpackState.match_rem (don't-care)


def test_harness_ab_unpack_only_dontcare_scratch():
    m0 = runtime.create_machine()
    dg_lin = m0.mem._xlat(m0.seg_bases[hooks.DG_SEG_INDEX], 0)
    match_rem_lin = {dg_lin + _MATCH_REM_DG_OFF, dg_lin + _MATCH_REM_DG_OFF + 1}

    r = capture_call_ab(hooks.UNPACK_SEG_INDEX, hooks.UNPACK_OFF,
                        lambda m, off: hooks._make_unpack_island(m),
                        demo=None, nth=1)
    assert r.reached, "no _Unpack call captured from boot"

    # Only the arithmetic flags may differ (undefined after the routine).
    assert set(r.reg_diffs) <= {"flags"}, f"register divergence: {r.reg_diffs}"

    # Every differing byte must be don't-care scratch: match_rem, or the routine's
    # own stack frame (freed locals below the entry ss:sp / args above it the
    # caller pops) — a small window around it.  A decoded OUTPUT byte would land
    # far from the stack and outside match_rem, and must NOT differ.
    STACK_WINDOW = 64

    def _stack(lin):
        return abs(lin - r.stack_low) <= STACK_WINDOW

    offending = [(lin, a, b) for (lin, a, b) in r.mem_diffs
                 if lin not in match_rem_lin and not _stack(lin)]
    assert not offending, (
        f"{len(offending)} non-scratch byte(s) differ (first {offending[:4]}) — "
        f"a real _Unpack divergence")
