"""Recovered SimAnt C-runtime long-arithmetic helpers (_TEXT, seg4) — VM-free,
byte-exact.

The Microsoft C 16-bit compiler auto-links a small family of far `_aF*`
helpers into any object that does `long` (32-bit) arithmetic — the 8086
`div`/`mul` opcodes only handle a 32-bit operand against a 16-bit one, so
every `long / long`, `long * long` etc. in the C source becomes a call to one
of these.  They are not SimAnt logic, just the compiler's own runtime
library, but several still-unrecovered simulation routines (the dig
subsystem's tile/map-scaling math) call them directly, so they are recovered
here as plain composable Python rather than as a `hooks.py` performance
island (unlike `__aFuldiv`, which WAS profiled as a hot loop and lives there
instead — this sibling has not been profiled as hot, and the need here is
composability from other recovered routines, not a VM-level lift).

Verified against the original ASM by the A/B oracle in ../tests/test_hooks.py.
"""
from __future__ import annotations


def _sx32(v: int) -> int:
    """Sign-extend a 32-bit value to a Python int."""
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def a_f_ldiv(dividend: int, divisor: int) -> int:
    """Signed 32-bit long division, quotient only (no remainder).

    Recovered from `__aFldiv` (SIMANTW.SYM seg4:08D4, _TEXT_MODULE, far, `ret
    far 8`): takes the absolute value of both operands (tracking how many
    were negative), divides the magnitudes with a classic double-precision
    shift-estimate-correct routine (a single-step Knuth algorithm-D
    correction — always sufficient here since the divisor is normalised to
    fill 32 bits before the estimate is taken), then negates the quotient
    iff exactly one of the two operands was negative.  A zero divisor
    reaches the ASM's own `div`, which `#DE` faults — so this raises
    `ZeroDivisionError` rather than fabricate a value.  Returns the 32-bit
    result as the ASM leaves it in DX:AX (i.e. `& 0xFFFFFFFF`).
    """
    dividend = _sx32(dividend)
    divisor = _sx32(divisor)
    if divisor == 0:
        raise ZeroDivisionError(
            "a_f_ldiv: divide by zero (dividend "
            f"{dividend:#x}) — the ASM would #DE here")
    q = abs(dividend) // abs(divisor)
    if (dividend < 0) != (divisor < 0):
        q = -q
    return q & 0xFFFFFFFF
