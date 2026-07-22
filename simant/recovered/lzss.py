"""SimAnt's LZSS asset decompressor — the recovered algorithm, VM-free.

A clean-room recovery of SimAnt's `_Unpack` (seg7:A668): the classic Haruhiko
Okumura LZSS.  A 4 KB sliding dictionary (`window`), pre-filled with spaces,
decode pointer starting at N-F; a bit-flag buffer selects, per step, a literal
byte or a (12-bit offset, 4-bit length) back-reference into the window.

This module is **pure Python** — no cpu / mem / hooks / offsets — so it is the
VM-less form the source port targets: a native build calls `decompress()` on a
whole compressed asset and gets the bytes, exactly as the original C `Unpack`
would.  `simant/hooks.py` is a thin adapter that drives this same core over the
STREAMING chunks the running game requests (feeding it memoryviews straight into
the VM's memory), so the interpreted game and a VM-less port share one decoder.

Verified byte-exact against the original routine call-for-call — see
`simant/tests/test_hooks.py` (the running game) and `test_lzss.py` (this module).
"""
from __future__ import annotations

from typing import NamedTuple

WINDOW_SIZE = 4096          # N — sliding-dictionary size
MAX_MATCH = 18             # F — longest back-reference (THRESHOLD + 0x0F)
THRESHOLD = 2             # a match encodes length THRESHOLD+1 .. THRESHOLD+16
WINDOW_START = WINDOW_SIZE - MAX_MATCH   # 0x0FEE — initial decode pointer
SPACE = 0x20              # the window is pre-filled with spaces

# Exit reasons.  These mirror the ASM's own resume codes ([B7D4]) so the hook
# adapter can hand a chunk that stopped mid-stream back to the interpreter to
# finish; a whole-buffer `decompress()` ignores them.
CODE_DONE = 0             # output budget reached at a flag boundary (a literal)
CODE_FLAG = 1             # input ran out while reading a flag byte
CODE_LITERAL = 2          # input ran out while reading a literal byte
CODE_MATCH_LO = 3         # input ran out reading a match's low/offset byte
CODE_MATCH_HI = 4         # input ran out reading a match's high/length byte
CODE_MATCH_COPY = 5       # output budget reached mid-match copy


class DecodeState(NamedTuple):
    """The resumable decoder state after a chunk (mirrors the ASM's globals)."""
    src_pos: int          # next source index consumed
    out_pos: int          # next output index written
    r: int                # window write pointer
    flags: int            # flag-bit buffer
    in_rem: int           # signed input-bytes-remaining counter
    dx: int               # last byte handled (ASM leaves it in DX)
    cx: int               # match offset cursor (ASM leaves it in CX)
    code: int             # why the chunk stopped (CODE_*)
    match_rem: int        # match bytes still to copy (only for CODE_MATCH_COPY)


def decode_chunk(src, src_pos: int, window, out, out_pos: int, r: int,
                 flags: int, in_rem: int, budget: int, thresh: int = THRESHOLD,
                 dx: int = 0, cx: int = 0, resume: int = CODE_DONE,
                 match_rem: int = 0) -> DecodeState:
    """Decode one streaming chunk of Okumura LZSS.

    Reads bytes from ``src[src_pos:]``, writes decoded bytes into ``out``
    starting at ``out_pos`` (and mirrors each into ``window`` at ``r``), until
    ``budget`` output bytes are produced or ``in_rem`` input bytes are consumed.
    ``src``/``out``/``window`` are any writable bytes-like buffers (a native
    port passes bytearrays; the hook passes memoryviews into VM memory), so this
    function never touches the VM.  Returns the full resumable `DecodeState`.

    ``resume`` is the ``code`` of the previous chunk (``CODE_DONE`` = a fresh
    start or a clean flag boundary), with ``cx``/``match_rem`` its saved
    cursors — so a chunk that stopped mid-token (input ran out) or mid-match
    (budget ran out) continues from EXACTLY the ASM's re-entry point
    (_Unpack's [B7D4] resume dispatch at 430E:A692) rather than restarting.
    This is what lets the whole streaming decode stay in this recovered
    routine instead of falling back to the interpreter for every continuation.
    """
    N = WINDOW_SIZE

    # -- resume mid-match copy (budget ran out mid-match): A758 decrements the
    # match countdown FIRST, then copies (A736) — the mirror of the fresh
    # copy loop below, which copies first.  ``off`` was saved in cx.
    if resume == CODE_MATCH_COPY:
        off = cx
        while True:
            match_rem -= 1
            if match_rem < 0:
                break
            c = window[off]
            dx = c
            off = (off + 1) & (N - 1)
            out[out_pos] = c
            out_pos += 1
            window[r] = c
            r = (r + 1) & (N - 1)
            budget -= 1
            if budget == 0:
                return DecodeState(src_pos, out_pos, r, flags, in_rem, dx,
                                   off, CODE_MATCH_COPY, match_rem)
        cx = off
        resume = CODE_DONE                          # fall into the main loop

    # -- resume the phase dispatch (input ran out mid-token): re-enter exactly
    # where the ASM does — CODE_FLAG at the flag refill, CODE_LITERAL at the
    # literal read, CODE_MATCH_LO/HI at the offset/length reads (lo saved in
    # cx for the HI re-entry).  CODE_DONE enters the main loop at the top.
    phase = resume
    lo = cx & 0xFF
    while True:
        if phase == CODE_DONE:                      # A6C8: shift a flag bit in
            flags >>= 1
            if (flags & 0x100) != 0:
                phase = CODE_LITERAL if flags & 1 else CODE_MATCH_LO
            else:
                phase = CODE_FLAG                   # flag bits exhausted — refill
        if phase == CODE_FLAG:                      # A6CF: refill the flag byte
            in_rem -= 1
            if in_rem < 0:
                return DecodeState(src_pos, out_pos, r, flags, in_rem, dx, cx,
                                   CODE_FLAG, 0)
            flags = src[src_pos] | 0xFF00
            src_pos += 1
            phase = CODE_LITERAL if flags & 1 else CODE_MATCH_LO
        if phase == CODE_LITERAL:                   # A6DD: emit a literal
            in_rem -= 1
            if in_rem < 0:
                return DecodeState(src_pos, out_pos, r, flags, in_rem, dx, cx,
                                   CODE_LITERAL, 0)
            c = src[src_pos]
            src_pos += 1
            dx = (dx & 0xFF00) | c                   # ASM's `mov dl,[si]`
            out[out_pos] = c
            out_pos += 1
            window[r] = c
            r = (r + 1) & (N - 1)
            budget -= 1
            if budget == 0:
                return DecodeState(src_pos, out_pos, r, flags, in_rem, dx, cx,
                                   CODE_DONE, 0)
            phase = CODE_DONE
            continue
        if phase == CODE_MATCH_LO:                   # A706: read the offset low byte
            in_rem -= 1
            if in_rem < 0:
                return DecodeState(src_pos, out_pos, r, flags, in_rem, dx, cx,
                                   CODE_MATCH_LO, 0)
            lo = src[src_pos]
            src_pos += 1
            cx = (cx & 0xFF00) | lo                  # ASM's `mov cl,[si]` (saved for HI resume)
            phase = CODE_MATCH_HI
        # phase == CODE_MATCH_HI: A710 — read the length/offset high byte
        in_rem -= 1
        if in_rem < 0:
            return DecodeState(src_pos, out_pos, r, flags, in_rem, dx, cx,
                               CODE_MATCH_HI, 0)
        hi = src[src_pos]
        src_pos += 1
        off = lo | ((hi >> 4) << 8)                 # 12-bit window offset
        match_rem = (hi & 0x0F) + thresh            # copies match_rem+1 bytes
        dx = match_rem
        while True:                                  # A736: fresh copy — copy first
            c = window[off]
            dx = c
            off = (off + 1) & (N - 1)
            out[out_pos] = c
            out_pos += 1
            window[r] = c
            r = (r + 1) & (N - 1)
            budget -= 1
            if budget == 0:
                return DecodeState(src_pos, out_pos, r, flags, in_rem, dx,
                                   off, CODE_MATCH_COPY, match_rem)
            match_rem -= 1
            if match_rem < 0:
                break
        cx = off
        phase = CODE_DONE


def decompress(data: bytes, out_len: int, thresh: int = THRESHOLD) -> bytes:
    """Decompress a whole LZSS asset — the VM-less recovery of `Unpack`.

    `data` is the complete compressed stream and `out_len` its decompressed
    length (SimAnt stores both).  Fresh dictionary, no streaming.
    """
    window = bytearray([SPACE]) * WINDOW_SIZE
    out = bytearray(out_len)
    decode_chunk(data, 0, window, out, 0, WINDOW_START, 0, len(data), out_len,
                 thresh)
    return bytes(out)
