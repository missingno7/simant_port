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


def a_f_ulmul(a: int, b: int) -> int:
    """Unsigned 32-bit long multiply, truncated to 32 bits (overflow
    silently discarded).

    Recovered from `__aFulmul` (SIMANTW.SYM seg4:096E, _TEXT_MODULE, far,
    `ret far 8`): when both operands fit in 16 bits it's a single 8086
    `mul`; otherwise the three needed 16-bit cross-terms (`a.lo*b.lo`,
    `a.hi*b.lo`, `a.lo*b.hi`) are combined — the fourth term (`a.hi*b.hi`,
    which would only ever affect bits 32-63 of the true 64-bit product) is
    never even computed, matching C's `unsigned long * unsigned long`
    truncating overflow.  Returns the 32-bit result as the ASM leaves it in
    DX:AX.
    """
    return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF


RAND_STATE_OFF = 0xAE34   # DGROUP dword: low word @ +0, high word @ +2


def c_srand(seed: int) -> int:
    """New 32-bit `rand()` state after `srand(seed)` — the seed zero-
    extended into the low word, high word cleared.

    Recovered from `_srand` (SIMANTW.SYM seg4:06F6, _TEXT_MODULE, far).
    """
    return seed & 0xFFFF


def c_rand(state: int) -> tuple[int, int]:
    """One step of the standard Microsoft C runtime `rand()` LCG (distinct
    from SimAnt's own `_SRand*` LFSR family in `simone.py` — this is the
    "genuinely unpredictable" generator, LFSR is the deterministic one used
    for map generation).

    Recovered from `_rand` (SIMANTW.SYM seg4:070A, _TEXT_MODULE, far): a
    genuine near-call to `__aFulmul` for `state * 0x343FD` (confirmed via
    disassembly, not assumed — the classic `push cs; call near` bridge into
    a far-retf routine already seen elsewhere this session), then
    `+ 0x269EC3` (mod 2**32) — the textbook MSVC LCG constants.  Returns
    `(new_state, value)` where `value = (new_state >> 16) & 0x7FFF`.
    """
    state = (a_f_ulmul(state, 0x343FD) + 0x269EC3) & 0xFFFFFFFF
    return state, (state >> 16) & 0x7FFF
