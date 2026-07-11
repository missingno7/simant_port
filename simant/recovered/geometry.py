"""Recovered SimAnt line geometry — VM-free, byte-exact.

Reconstructed from the shipped code (names from SIMANTW.SYM), verified against
the original ASM by the A/B oracle in simant/tests/test_hooks.py.
"""
from __future__ import annotations


def _outcode(a: int, b: int, bound_a: int, bound_b: int) -> int:
    """The Cohen-Sutherland-style 4-bit region code of point (a, b) against the
    clip rectangle [0, bound_a] x [0, bound_b].

    Bit layout matches the ASM's `or al, N`: a<0 -> 8, a>bound_a -> 4,
    b<0 -> 1, b>bound_b -> 2.  The `a` test is an if/elif (8 or 4 or neither);
    `b` is always tested afterwards (its own if/elif).
    """
    code = 0
    if a < 0:
        code |= 8
    elif a > bound_a:
        code |= 4
    if b < 0:
        code |= 1
    elif b > bound_b:
        code |= 2
    return code


def _sar1_sum(u: int, v: int) -> int:
    """`add`/`sar 1`: 16-bit two's-complement add of two (signed) coordinates,
    then an arithmetic shift right by one (floor toward -inf), matching the
    original's `add ax,dx` / `sar ax,1` midpoint step."""
    s = (u + v) & 0xFFFF
    if s & 0x8000:
        s -= 0x10000
    return s >> 1


def _bisect(a0: int, b0: int, a1: int, b1: int,
            test_a: bool, target: int, inc_a: bool):
    """Binary-subdivide the segment (a0,b0)-(a1,b1) until the tested coordinate's
    integer midpoint equals `target`, returning that midpoint point (am, cm).

    `test_a` picks which coordinate is driven to `target` (a for the vertical
    clip edges, b for the horizontal ones).  On the `>target` side the far
    endpoint P1 is pulled in to the midpoint; on the `<target` side P0 is pulled
    in and nudged by one (`inc si`/`inc di`) — the original's rounding that
    guarantees the search makes progress.
    """
    for _ in range(64):                         # 16-bit bisection converges fast
        am = _sar1_sum(a0, a1)
        cm = _sar1_sum(b0, b1)
        t = am if test_a else cm
        if t == target:
            return am, cm
        if t > target:
            a1, b1 = am, cm
        else:
            a0, b0 = am, cm
            if inc_a:
                a0 += 1
            else:
                b0 += 1
    raise RuntimeError("clip_line bisection did not converge")   # fail loud


def clip_line(a0: int, b0: int, a1: int, b1: int,
              bound_a: int, bound_b: int, cx_in: int):
    """Clip the segment (a0,b0)-(a1,b1) to the rectangle [0,bound_a]x[0,bound_b]
    by iterated midpoint subdivision.

    Returns ``(accepted, a0, b0, a1, b1, swap_flag, cx_out)`` — all coordinates
    signed.  `accepted` is False for a trivial reject (both endpoints share an
    outside edge -> the original's `stc`), True otherwise (`clc`).  `swap_flag`
    is the persistent parity of P0<->P1 swaps the routine leaves in DGROUP scratch
    0x1D82; `cx_out` is the clobbered `cx` residue (the last midpoint's b, or the
    entry value if no subdivision ran).

    Recovered from `_os_ClipLine` (SIMANTW.SYM seg4:6E24, _TEXT): a near call
    taking the endpoints in si/di (P0) and dx/bx (P1), clip bounds in DGROUP
    words 0x1D7A (bound_a) / 0x1D78 (bound_b), preserving ax (push/pop) and
    clobbering cx.  Each iteration recomputes both outcodes, trivially
    accepts/rejects, else orders the endpoints (swapping toward the outside one,
    toggling 0x1D82) and drives whichever endpoint is out-of-bounds onto the
    violated edge via :func:`_bisect`, then loops.
    """
    swap = 0
    cx = cx_in
    for _ in range(256):                        # each pass clips one endpoint edge
        code0 = _outcode(a0, b0, bound_a, bound_b)
        code1 = _outcode(a1, b1, bound_a, bound_b)
        if code0 & code1:
            return False, a0, b0, a1, b1, swap, cx
        if code0 == 0 and code1 == 0:
            if swap:
                a0, b0, a1, b1 = a1, b1, a0, b0
            return True, a0, b0, a1, b1, swap, cx

        if not (b0 < b1):                       # cmp di,bx; jl skip
            a0, b0, a1, b1 = a1, b1, a0, b0
            code0, code1 = code1, code0
            swap ^= 1
        if code0 & 1:                           # P0 below -> clip to b == 0
            am, cm = _bisect(a0, b0, a1, b1, False, 0, False)
            a0, b0, cx = am, cm, cm
            continue
        if code1 & 2:                           # P1 above -> clip to b == bound_b
            am, cm = _bisect(a0, b0, a1, b1, False, bound_b, False)
            a1, b1, cx = am, cm, cm
            continue

        if not (a0 < a1):                       # cmp si,dx; jl skip
            a0, b0, a1, b1 = a1, b1, a0, b0
            code0, code1 = code1, code0
            swap ^= 1
        if code0 & 8:                           # P0 left -> clip to a == 0
            am, cm = _bisect(a0, b0, a1, b1, True, 0, True)
            a0, b0, cx = am, cm, cm
            continue
        if code1 & 4:                           # P1 right -> clip to a == bound_a
            am, cm = _bisect(a0, b0, a1, b1, True, bound_a, True)
            a1, b1, cx = am, cm, cm
            continue
        # neither remaining edge matched: recompute and loop (the ASM's jmp back)
    raise RuntimeError("clip_line did not converge")            # fail loud
