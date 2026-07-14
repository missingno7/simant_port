"""Recovered from SIMONE_MODULE (SIMANTW.SYM seg5) — the simulation PRNG.

SimAnt's simulation randomness is one 16-bit Fibonacci-free LFSR stepped by
every `_SRand*` call (seed lives in DGROUP at 0xCBF2):

    seed <<= 1;  if the shifted-out bit was set:  seed ^= 0x1BF5

`_SRand1(n)` returns `seed % n` (any modulus); the nine power-of-two
siblings `_SRand2 .. _SRand256` are compiled copies differing only in their
AND mask and return `seed & (n-1)`.  `_Set/GetSRandSeed` access the seed;
`_GetRRandSeed` reads the BIOS tick dword at 0040:006C (the "real random"
source used to seed a game) and `_SetRRandSeed` is an empty stub.

Byte-proven by the island A/B oracles in ../tests/test_hooks.py — this file
is the readable source; the islands in ../hooks.py are its adapters.
"""
from __future__ import annotations

SRAND_TAP = 0x1BF5
SRAND_SEED_OFF = 0xCBF2                  # DGROUP word holding the SRand LFSR seed


def srand_step(seed: int) -> int:
    """One LFSR step: the new seed after any `_SRand*` call."""
    shifted = (seed << 1) & 0xFFFF
    return shifted ^ SRAND_TAP if seed & 0x8000 else shifted


def srand1(seed: int, n: int) -> tuple[int, int]:
    """_SRand1: step the LFSR, return (new_seed, new_seed % n).  n=0 divide-
    faults in the original; callers never pass it."""
    seed = srand_step(seed)
    return seed, seed % n


def srand_pow2(seed: int, mask: int) -> tuple[int, int]:
    """_SRand2.._SRand256: step the LFSR, return (new_seed, new_seed & mask)."""
    seed = srand_step(seed)
    return seed, seed & mask


def r_rand(rand_state: int, n: int) -> tuple[int, int]:
    """`rand() % n` — SimAnt's own wrapper around the standard C runtime
    generator (`recovered/crt_math.py`'s `c_rand`), NOT the `_SRand*` LFSR
    above; used where the game wants "genuinely unpredictable" values
    (combat rolls, etc.) rather than the deterministic map-gen sequence.

    Recovered from `_RRand` (SIMANTW.SYM seg5:156E, far, arg: n): calls
    `_rand()`, takes its absolute value (defensive — `_rand`'s result is
    always 0..0x7FFF, so this never actually changes anything, but is
    ported faithfully), then returns the SIGNED remainder of dividing by
    `n`.  `n == 0` reaches the ASM's own `idiv`, which `#DE` faults.
    """
    from .crt_math import c_rand
    rand_state, v = c_rand(rand_state)
    if v < 0:
        v = -v
    if n == 0:
        raise ZeroDivisionError("r_rand: divide by zero — the ASM would #DE here")
    return rand_state, v % n
