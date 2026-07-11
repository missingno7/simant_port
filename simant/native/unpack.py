"""``native_unpack`` — the _Unpack LZSS decoder run over a :class:`NativeGameState`
with NO VM.

The win16 island (``simant/hooks.py``) marshals the call off a live VM: it reads
the arguments from the CPU stack, drives the recovered ``lzss.decode_chunk`` over
``cpu.mem``, and writes the exit state + register residue back.  This is the SAME
recovered decoder sourced from an owned image instead: the arguments arrive as a
plain Python call and the resumable state lives in the ``NativeGameState`` — no
``cpu``, no stack, no emulator.  Byte-exact against the ASM (``tests/`` proves the
native output equals the VM/ASM output for the same input), it is a first taste of
the native (product) execution mode.
"""
from __future__ import annotations

from ..bridge.dgroup_view import ByteBackend, UnpackState, UNPACK_STATE_BASE
from ..recovered import lzss


def native_unpack(state, out_seg: int, out_off: int, budget: int) -> bytes:
    """Decode up to ``budget`` bytes to ``out_seg:out_off`` in ``state``'s image,
    advancing the LZSS decoder's resumable DGROUP state.  Returns the bytes
    written this chunk (the decoder stops at a window/output/input boundary and
    parks its state for the next call, exactly as the ASM does)."""
    u = UnpackState(ByteBackend(state, state.dgroup_base), UNPACK_STATE_BASE)

    r, dx, cx, flags = u.r, u.dx, u.cx, u.flags
    win_seg, thresh = u.win_seg, u.thresh
    src_seg, src_off = u.src_seg, u.src_off
    in_rem = u.in_rem                                    # _S16 -> already signed

    data = memoryview(state.data)
    win_lin = state._xlat(win_seg, 4)                   # window is win_seg:[i+4]
    out_lin = state._xlat(out_seg, out_off)
    st_ = lzss.decode_chunk(
        data[state._xlat(src_seg, src_off):],           # source (reads <= in_rem)
        0,
        data[win_lin:win_lin + lzss.WINDOW_SIZE],       # 4 KB sliding window
        data[out_lin:out_lin + budget],                 # output (writes <= budget)
        0, r, flags, in_rem, budget, thresh, dx, cx)

    code = st_.code
    src_off = (src_off + st_.src_pos) & 0xFFFF
    if code == lzss.CODE_MATCH_COPY:
        u.match_rem = st_.match_rem & 0xFFFF

    # Write the exit state back exactly as the ASM does.
    u.resume = code
    u.flags = st_.flags & 0xFFFF
    u.r = st_.r & 0xFFFF
    u.src_off = src_off
    u.dx = st_.dx & 0xFFFF
    u.cx = st_.cx & 0xFFFF
    u.in_rem = st_.in_rem

    return bytes(data[out_lin:out_lin + st_.out_pos])
