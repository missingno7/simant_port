"""The recovered LZSS decompressor (simant/recovered/lzss.py) — pure, VM-free.

These tests need no VM: they exercise `decompress` as a plain bytes->bytes
function, the form a native port uses.  A round-trip encoder pins the decoder
against arbitrary data, and the Okumura invariants (window init, 4KB wrap) are
checked directly.  The byte-exact-vs-the-real-game proof lives in
test_hooks.py; this file locks the algorithm itself.
"""
from simant.recovered import lzss


def _lzss_compress(data: bytes) -> bytes:
    """A minimal Okumura-LZSS encoder matching lzss.decompress' bit layout:
    8-bit flag groups (LSB first, 1=literal / 0=match), match = (12-bit offset,
    4-bit length-THRESHOLD) over a 4KB space-initialised window.  Greedy; only
    needs to be a VALID encoding the decoder inverts, not optimal."""
    N, F, THRESH = lzss.WINDOW_SIZE, lzss.MAX_MATCH, lzss.THRESHOLD
    win = bytearray([lzss.SPACE]) * N
    r = lzss.WINDOW_START
    out = bytearray()
    flag_pos = None
    nbits = 0
    i = 0
    while i < len(data):
        if nbits == 0:                              # start a new flag group
            flag_pos = len(out)
            out.append(0)
        # find the longest match (>= THRESH+1) ending in the window
        best_len, best_off = 0, 0
        for off in range(N):
            k = 0
            while (k < F and i + k < len(data)
                   and win[(off + k) & (N - 1)] == data[i + k]):
                k += 1
            if k > best_len:
                best_len, best_off = k, off
        if best_len >= THRESH + 1:
            n = best_len
            out.append(best_off & 0xFF)
            out.append(((best_off >> 4) & 0xF0) | ((n - THRESH - 1) & 0x0F))
            for k in range(n):
                win[r] = data[i + k]
                r = (r + 1) & (N - 1)
            i += n
        else:
            out[flag_pos] |= (1 << nbits)           # mark literal
            out.append(data[i])
            win[r] = data[i]
            r = (r + 1) & (N - 1)
            i += 1
        nbits = (nbits + 1) & 7
    return bytes(out)


def test_roundtrip_various():
    cases = [
        b"",
        b"A",
        b"the ants go marching one by one, hurrah, hurrah",
        b"AAAAAAAAAAAAAAAAAAAAAAAA",                 # long run -> matches
        b"abcabcabcabcabcabcabc",                    # periodic
        bytes(range(256)) * 4,                       # every byte value
        b"SimAnt" * 200,                             # highly repetitive
    ]
    for original in cases:
        comp = _lzss_compress(original)
        assert lzss.decompress(comp, len(original)) == original, original[:20]


def test_window_starts_with_spaces():
    # A match at the very start references the space-filled window: two literal
    # 'X' then a length-3 match at offset WINDOW_START yields the 'X' just
    # written then spaces (the classic Okumura behaviour).
    original = b"X" + b" " * 5
    comp = _lzss_compress(original)
    assert lzss.decompress(comp, len(original)) == original


def test_constants_are_the_okumura_fingerprint():
    assert lzss.WINDOW_SIZE == 4096
    assert lzss.MAX_MATCH == 18
    assert lzss.WINDOW_START == 4096 - 18           # 0x0FEE
    assert lzss.THRESHOLD == 2


def test_decode_chunk_reports_clean_done_at_budget():
    # Decoding fewer bytes than the stream holds stops CLEAN on a flag boundary
    # (a literal), the resume code the streaming game relies on.
    data = _lzss_compress(b"hello world")
    win = bytearray([lzss.SPACE]) * lzss.WINDOW_SIZE
    out = bytearray(5)
    st = lzss.decode_chunk(data, 0, win, out, 0, lzss.WINDOW_START, 0,
                           len(data), 5)
    assert bytes(out) == b"hello"
    assert st.code == lzss.CODE_DONE
    assert st.out_pos == 5


def test_streaming_resume_matches_a_single_decode():
    """Decoding a stream in tiny output budgets — which forces the decoder to
    stop and RESUME through every code, including CODE_MATCH_COPY mid-match —
    must reconstruct byte-for-byte what a single unbounded decode produces.
    This is the pure-function guard behind the _Unpack island's resume path
    (the logo-draw fix): the island decodes each continuation in Python instead
    of punting to the interpreter."""
    # Repetitive text compresses to real back-references, so small budgets land
    # mid-match (CODE_MATCH_COPY) as well as on clean boundaries.
    original = (b"the quick brown fox " * 8) + (b"ABCABCABCABC" * 6) + b"!"
    comp = _lzss_compress(original)

    def decode_full():
        win = bytearray([lzss.SPACE]) * lzss.WINDOW_SIZE
        out = bytearray(len(original))
        st = lzss.decode_chunk(comp, 0, win, out, 0, lzss.WINDOW_START, 0,
                               len(comp), len(original))
        return bytes(out[:st.out_pos])

    def decode_chunked(budget):
        win = bytearray([lzss.SPACE]) * lzss.WINDOW_SIZE
        out = bytearray(len(original))
        r, flags, in_rem, src_pos, dx, cx = lzss.WINDOW_START, 0, len(comp), 0, 0, 0
        resume, match_rem, total, seen = lzss.CODE_DONE, 0, 0, set()
        while total < len(original):
            st = lzss.decode_chunk(comp, src_pos, win, out, total, r, flags,
                                   in_rem, budget, lzss.THRESHOLD, dx, cx,
                                   resume=resume, match_rem=match_rem)
            seen.add(st.code)
            if st.out_pos == total and st.code != lzss.CODE_DONE:
                break                                 # genuinely stuck (out of input)
            total = st.out_pos
            r, flags, in_rem, dx, cx = st.r, st.flags, st.in_rem, st.dx, st.cx
            src_pos, resume, match_rem = st.src_pos, st.code, st.match_rem
        return bytes(out[:total]), seen

    full = decode_full()
    assert full == original
    for budget in (1, 2, 3, 4, 7, 13, len(original)):
        chunked, seen = decode_chunked(budget)
        assert chunked == full, f"budget={budget}: {chunked!r} != {full!r}"
    # The tiniest budget must have exercised the mid-match resume.
    _, seen1 = decode_chunked(1)
    assert lzss.CODE_MATCH_COPY in seen1, f"resume path not hit: {sorted(seen1)}"
