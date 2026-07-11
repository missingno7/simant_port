"""SimAnt lifted islands — hot ASM routines reimplemented in Python.

The dos_re method applied to SimAnt: PC-sampling (`python -m simant.probes.
profile`) ranks the game's time by routine (names from SIMANTW.SYM).  The
runaway #1 is `__aFuldiv` — the Microsoft C far 32-bit UNSIGNED long-divide
runtime helper — called constantly for the map/coordinate scaling math (~14%
of all samples, its inner shift-subtract loop runs dozens of interpreted
instructions per divide).  It is a pure function with a fixed ABI, so it lifts
to one exact Python `//`.

Each island is installed at a routine's entry CS:IP, verified against the
routine's real prologue bytes at install time (an island landing on different
code corrupts silently — so we refuse to install on mismatch).  The island
computes the result, writes back the exact ABI-guaranteed exit state (result
registers, preserved registers, the `retf` stack unwind) and jumps to the
caller.  Correctness is gated by `simant/tests/test_hooks.py`, which runs the
ORIGINAL routine and the island over the same inputs and compares the full
register result — the byte-exact proof that makes this a recovery, not an
approximation.

ABI of __aFuldiv (far, callee-cleans — verified by live trace):
    entry SP -> [ret_ip][ret_cs][dividend:dword][divisor:dword]
    quotient in DX:AX; CX clobbered to divisor-low; BX/SI/DI/BP preserved;
    returns `retf 8` (SP += 4 ret + 8 args = 12).
"""
from __future__ import annotations

from . import _env  # noqa: F401  — puts win16_re on sys.path
import win16  # noqa: F401  — win16/_env in turn puts the dos_re submodule on sys.path

from dos_re.cpu import AF, CF, OF, PF, SF, ZF

from .recovered import lzss

_ARITH = CF | PF | AF | ZF | SF | OF

# NE segment (1-based) holding the C runtime helpers; resolved to a base at
# install time.  SimAnt's __aF* math helpers live in segment 4.
RT_SEG_INDEX = 4

# __aFuldiv entry offset within segment 4 (SIMANTW.SYM) and its prologue:
#   55        push bp
#   8b ec     mov bp,sp
#   53        push bx
#   56        push si
#   8b 46 0c  mov ax,[bp+0C]     ; divisor high word
#   0b c0     or ax,ax
#   75        jnz ...            ; high != 0 -> full 32-bit path
AFULDIV_OFF = 0x0A60
AFULDIV_SIG = bytes.fromhex("558bec53568b460c0bc075")


def _stack_word(cpu, delta: int) -> int:
    return cpu.mem.rw(cpu.s.ss, (cpu.s.sp + delta) & 0xFFFF)


def _make_uldiv_island(entry_off: int):
    """Island for __aFuldiv at segment-relative `entry_off` (only used for the
    hook-name label; the island reads everything live off the stack)."""

    def island(cpu) -> None:
        s = cpu.s
        sp = s.sp
        ret_ip = _stack_word(cpu, 0)
        ret_cs = _stack_word(cpu, 2)
        dividend = _stack_word(cpu, 4) | (_stack_word(cpu, 6) << 16)
        divisor = _stack_word(cpu, 8) | (_stack_word(cpu, 10) << 16)
        if divisor == 0:
            # The real routine faults (#DE) inside `div`.  Never hit in normal
            # play; fail loud rather than silently returning a wrong quotient.
            raise ZeroDivisionError(
                "__aFuldiv island: divide by zero (dividend "
                f"{dividend:#x}) — the ASM would #DE here")
        quotient = (dividend // divisor) & 0xFFFFFFFF
        s.ax = quotient & 0xFFFF
        s.dx = (quotient >> 16) & 0xFFFF
        s.cx = divisor & 0xFFFF          # routine leaves divisor-low in CX
        # BX, SI, DI, BP, ES, DS, flags: untouched (routine preserves them).
        s.sp = (sp + 12) & 0xFFFF        # retf 8: pop ret (4) + args (8)
        s.cs = ret_cs
        s.ip = ret_ip

    return island


# -- _Unpack: the LZSS asset decompressor (the load bottleneck) --------------
#
# ~90% of load time is this one loop at seg7:A668 (`_Unpack`, per SIMANTW.SYM) —
# the classic Okumura LZSS: 4KB sliding window (window seg = [B7C0], byte r at
# offset r+4), window pre-filled with spaces, decode pointer r starts at
# 0x0FEE = N-F, THRESHOLD = [B7C2] (=2), F = 18.  It is a *resumable streaming*
# decoder: the caller asks for [bp+10] output bytes per call, and cross-call
# state lives in DGROUP globals.
#
# The decode ALGORITHM is recovered VM-free in `simant/recovered/lzss.py` (a
# native port calls it directly).  This island is a thin ADAPTER: it reads the
# routine's state from the DGROUP globals + stack, drives `lzss.decode_chunk`
# over memoryviews straight into VM memory, and writes back the exact ABI exit
# state — gated byte-exact against the ASM by simant/tests/test_hooks.py.  On a
# mid-operation resume (entry [B7D4] != 0) it passes through to the real routine
# (keeps the delicate two-sided-streaming resume path authoritative).  Exit
# codes written to [B7D4] mirror the ASM's own resume re-entry codes (see
# lzss.CODE_*): 0 clean, 1-4 input-exhaust points, 5 mid-match.
UNPACK_SEG_INDEX = 7
UNPACK_OFF = 0xA668
UNPACK_SIG = bytes.fromhex("558bec83ec045756")   # push bp;mov bp,sp;sub sp,4;push di;push si
DG_SEG_INDEX = 10                                # DGROUP (auto-data) segment


def _make_unpack_island(machine):
    from .bridge.dgroup_view import SelectorBackend, UnpackState, UNPACK_STATE_BASE
    dg = machine.seg_bases[DG_SEG_INDEX]

    def island(cpu) -> None:
        m = cpu.mem
        s = cpu.s
        rw, ww = m.rw, m.ww
        u = UnpackState(SelectorBackend(m, dg), UNPACK_STATE_BASE)

        resume = u.resume
        if resume != 0:
            # Mid-operation resume — let the real routine handle it.  Emulate the
            # hooked `push bp` and continue at A669 (mov bp,sp).
            s.sp = (s.sp - 2) & 0xFFFF
            ww(s.ss, s.sp, s.bp)
            s.ip = (UNPACK_OFF + 1) & 0xFFFF
            return

        sp = s.sp
        ret_ip, ret_cs = rw(s.ss, sp), rw(s.ss, (sp + 2) & 0xFFFF)
        out_off = rw(s.ss, (sp + 4) & 0xFFFF)
        out_seg = rw(s.ss, (sp + 6) & 0xFFFF)
        budget = rw(s.ss, (sp + 8) & 0xFFFF)      # [bp+10] output byte count

        r = u.r                                   # window write pos (bx)
        dx = u.dx
        cx = u.cx
        flags = u.flags                           # flag bit buffer (ax)
        win_seg = u.win_seg
        thresh = u.thresh
        src_seg = u.src_seg
        src_off = u.src_off
        in_rem = u.in_rem                         # _S16 -> already sign-extended

        # Drive the recovered VM-free decoder (simant/recovered/lzss.py) over
        # memoryviews straight into VM memory — no copies.  window[i] IS the
        # ASM's win_seg:[i+4]; source and output are contiguous from their far
        # pointers.  The pure decoder writes the window + output in place and
        # returns the full resumable state.
        data = memoryview(m.data)
        win_lin = m._xlat(win_seg, 4)
        out_lin = m._xlat(out_seg, out_off)
        st_ = lzss.decode_chunk(
            data[m._xlat(src_seg, src_off):],             # source (reads <= in_rem)
            0,
            data[win_lin:win_lin + lzss.WINDOW_SIZE],     # 4KB sliding window
            data[out_lin:out_lin + budget],               # output (writes <= budget)
            0, r, flags, in_rem, budget, thresh, dx, cx)
        code = st_.code
        count = st_.out_pos
        r, flags, in_rem, dx, cx = st_.r, st_.flags, st_.in_rem, st_.dx, st_.cx
        src_off = (src_off + st_.src_pos) & 0xFFFF
        if code == lzss.CODE_MATCH_COPY:
            u.match_rem = st_.match_rem & 0xFFFF          # save match countdown

        # -- write back the exit state exactly as A779 does ------------------
        u.resume = code
        u.flags = flags & 0xFFFF
        u.r = r & 0xFFFF
        u.src_off = src_off & 0xFFFF
        u.dx = dx & 0xFFFF
        u.cx = cx & 0xFFFF
        u.in_rem = in_rem                         # _S16 setter wraps to 16-bit
        # Reproduce the values the ASM leaves in its stack frame — after the
        # retf that memory is freed, but SimAnt (C, uninitialised locals) can
        # read the scratch, so the freed-frame contents must match byte-for-byte
        # or a later read diverges.  Frame (bp = sp-2 after push bp):
        #   [sp-2]=old bp  [sp-4]=[bp-2] count  [sp-6]=[bp-4] win seg
        #   [sp-8]=pushed di  [sp-10]=pushed si  [sp-12]=pushed ds
        ww(s.ss, (sp - 2) & 0xFFFF, s.bp)
        ww(s.ss, (sp - 4) & 0xFFFF, count & 0xFFFF)
        ww(s.ss, (sp - 6) & 0xFFFF, win_seg)
        ww(s.ss, (sp - 8) & 0xFFFF, s.di)
        ww(s.ss, (sp - 10) & 0xFFFF, s.si)
        ww(s.ss, (sp - 12) & 0xFFFF, s.ds)
        # Registers at retf: AX=output count, BX=r, CX, DX as above; ES=output
        # seg; SI/DI/DS/BP restored to the caller's (the island never touched
        # the real SI/DI/DS/BP).  retf has no arg cleanup — caller pops args.
        s.ax = count & 0xFFFF
        s.bx = r & 0xFFFF
        s.cx = cx & 0xFFFF
        s.dx = dx & 0xFFFF
        s.es = out_seg
        s.sp = (sp + 4) & 0xFFFF
        s.cs = ret_cs
        s.ip = ret_ip

    return island


# -- a far byte-memcpy (seg2:3460) — the tile/map block copy -----------------
#
# A compiler-emitted far byte-copy loop (the profiler mislabels the region;
# the neighbouring hot 24% is actually a GetTickCount frame-pacing busy-wait,
# NOT a tile blit, and is left alone because accelerating it would shift the
# RNG-seeded worldgen).  This loop copies SI bytes from a huge source pointer
# (offset @bp-8, selector @bp-6) to a huge dest pointer (offset @bp-12,
# selector @bp-10), each advancing one byte at a time with a +8 selector bump
# on every 64K wrap.  Observed: 960-byte tile rows, ~9.5% of load.  Consecutive
# hugeheap selectors map to contiguous linear memory, so the whole run is one
# linear block move (the island detects the rare overlapping-forward case and
# falls back to a smearing byte copy to stay byte-exact).
BYTECOPY_SEG_INDEX = 2
BYTECOPY_OFF = 0x3460
BYTECOPY_SIG = bytes.fromhex(                    # les..jnz, 37 bytes
    "c45ef88346f80173058146fa0800268a07c45ef48346f40173058146f608002688074e75db")
BYTECOPY_EXIT = BYTECOPY_OFF + len(BYTECOPY_SIG)  # 0x3485 (after jnz not taken)


def _make_bytecopy_island(machine):
    def island(cpu) -> None:
        m = cpu.mem
        s = cpu.s
        ss, bp = s.ss, s.bp
        rw, ww, xlat = m.rw, m.ww, m._xlat

        n = s.si or 0x10000                      # SI==0 loops the full 64K
        src_off, src_sel = rw(ss, (bp - 8) & 0xFFFF), rw(ss, (bp - 6) & 0xFFFF)
        dst_off, dst_sel = rw(ss, (bp - 12) & 0xFFFF), rw(ss, (bp - 10) & 0xFFFF)
        src_lin, dst_lin = xlat(src_sel, src_off), xlat(dst_sel, dst_off)

        d = m.data
        if src_lin < dst_lin < src_lin + n:      # overlapping forward -> smear
            for i in range(n):
                d[dst_lin + i] = d[src_lin + i]
        else:                                    # non-overlapping linear move
            d[dst_lin:dst_lin + n] = bytes(d[src_lin:src_lin + n])

        # Advance both huge pointers exactly as the per-byte adds + selector
        # bumps would, and set the registers/flags the loop exit leaves.
        ww(ss, (bp - 8) & 0xFFFF, (src_off + n) & 0xFFFF)
        ww(ss, (bp - 6) & 0xFFFF, (src_sel + 8 * ((src_off + n) >> 16)) & 0xFFFF)
        ww(ss, (bp - 12) & 0xFFFF, (dst_off + n) & 0xFFFF)
        ww(ss, (bp - 10) & 0xFFFF, (dst_sel + 8 * ((dst_off + n) >> 16)) & 0xFFFF)
        s.ax = (s.ax & 0xFF00) | d[src_lin + n - 1]      # AL = last byte copied
        s.bx = (dst_off + n - 1) & 0xFFFF                # last dest offset used
        s.es = (dst_sel + 8 * ((dst_off + n - 1) >> 16)) & 0xFFFF
        s.si = 0
        # `dec si` -> 0 then `jnz` not taken: ZF/PF set, others clear; CF from
        # the last pointer add is 0 for any run that does not overflow a 16-bit
        # offset into a >0xFFFF selector (never happens for these buffers).
        s.flags = (s.flags & ~_ARITH) | ZF | PF
        s.ip = BYTECOPY_EXIT & 0xFFFF

    return island


# -- _Windows_MakeTable4x4 (seg4:4674) — the terrain tile-to-pixel expander ---
#
# The game's own routine that paints a 4-scanline terrain band into a huge DIB
# frame buffer.  Per column it does one `lodsb` (a tile colour index) then four
# `stosw`, reading each scanline's fill word from a 4x32-word table at
# SS:0x1A56 (row stride 0x40).  The four rows sit at DI, DI+2*count, DI+4*count,
# DI+6*count (stride = 2*count words = the DIB scanline); DI advances one word
# per column.  ES stays a single selector for the call (the huge-pointer walk
# across selectors is the caller's), so the whole band is a plain write within
# one linear span.  Preserves every register/segment (pusha/popa + push bp +
# push ds/es); `retf` (caller cleans the 10 arg bytes).  The pixel logic is
# recovered VM-free in simant/recovered/render.py.
MAKETABLE4X4_SEG_INDEX = 4
MAKETABLE4X4_OFF = 0x4674
MAKETABLE4X4_TABLE_OFF = 0x1A56                  # SS-relative colour table base
MAKETABLE4X4_SIG = bytes.fromhex(                # prologue + the table-base load
    "558bec601e06c57606c47e0a8b4e0e8bd1d1e24a4abb561a")


def _make_maketable4x4_island(machine):
    from .recovered.render import windows_make_table_4x4

    def island(cpu) -> None:
        m = cpu.mem
        s = cpu.s
        ss, sp = s.ss, s.sp
        rw = m.rw
        ret_ip, ret_cs = rw(ss, sp), rw(ss, (sp + 2) & 0xFFFF)
        src_off, src_seg = rw(ss, (sp + 4) & 0xFFFF), rw(ss, (sp + 6) & 0xFFFF)
        dst_off, dst_seg = rw(ss, (sp + 8) & 0xFFFF), rw(ss, (sp + 0x0A) & 0xFFFF)
        count = rw(ss, (sp + 0x0C) & 0xFFFF)

        tiles = [m.rb(src_seg, (src_off + i) & 0xFFFF) for i in range(count)]
        table = [[rw(ss, (MAKETABLE4X4_TABLE_OFF + row * 0x40 + t * 2) & 0xFFFF)
                  for t in range(32)] for row in range(4)]
        rows = windows_make_table_4x4(tiles, table)

        stride = (2 * count) & 0xFFFF             # DI += dx+2 between scanlines
        for r in range(4):
            base = (dst_off + r * stride) & 0xFFFF
            row = rows[r]
            for c in range(count):
                m.ww(dst_seg, (base + c * 2) & 0xFFFF, row[c])

        # Every register/segment/flag is preserved by the routine; only SP and
        # CS:IP change (retf pops the return address, the caller cleans args).
        s.sp = (sp + 4) & 0xFFFF
        s.cs = ret_cs
        s.ip = ret_ip

    return island


# -- _Windows_MakeTable1x1 (seg4:46BB) — the 1:1 (no-zoom) tile packer --------
#
# The sibling of MakeTable4x4 for the un-zoomed view: it packs pairs of source
# tile bytes into single 4bpp pixel bytes via an XLAT table at SS:0x1B56.  Per
# iteration (count>>1 of them): lodsb t0; al = ss:[0x1B56+t0]; ah = al; lodsb
# t1; al = ss:[0x1B66+t1]; al |= ah; stosb.  Same full-preservation + retf ABI.
MAKETABLE1X1_SEG_INDEX = 4
MAKETABLE1X1_OFF = 0x46BB
MAKETABLE1X1_TABLE_OFF = 0x1B56                  # SS-relative XLAT table base
MAKETABLE1X1_SIG = bytes.fromhex(
    "558bec601e06c57606c47e0abb561b8b4e0ed1e9")


def _make_maketable1x1_island(machine):
    from .recovered.render import windows_make_table_1x1

    def island(cpu) -> None:
        m = cpu.mem
        s = cpu.s
        ss, sp = s.ss, s.sp
        rw, rb = m.rw, m.rb
        ret_ip, ret_cs = rw(ss, sp), rw(ss, (sp + 2) & 0xFFFF)
        src_off, src_seg = rw(ss, (sp + 4) & 0xFFFF), rw(ss, (sp + 6) & 0xFFFF)
        dst_off, dst_seg = rw(ss, (sp + 8) & 0xFFFF), rw(ss, (sp + 0x0A) & 0xFFFF)
        count = rw(ss, (sp + 0x0C) & 0xFFFF)

        pairs = count >> 1
        tiles = [rb(src_seg, (src_off + i) & 0xFFFF) for i in range(2 * pairs)]
        table = bytes(rb(ss, (MAKETABLE1X1_TABLE_OFF + i) & 0xFFFF)
                      for i in range(0x110))       # covers XLAT of 0..255 at +0 and +0x10
        out = windows_make_table_1x1(tiles, table)
        for i, byteval in enumerate(out):
            m.wb(dst_seg, (dst_off + i) & 0xFFFF, byteval)

        s.sp = (sp + 4) & 0xFFFF
        s.cs = ret_cs
        s.ip = ret_ip

    return island


# -- _WindowsMono_MakeTable4x4a (seg4:442C) — the zoomed mono tile packer -----
#
# The monochrome sibling of MakeTable4x4: packs pairs of source tiles (even ->
# high nibble, odd -> low nibble) across four 0x40-strided scanlines, each pixel
# read from a per-tile 8-byte pattern row selected by (mode & 7) at SS:0x26A0.
# Fixed 0x40-pair count; full register preservation + retf ABI.  The "a" half
# emits scanlines 0..3.  Recovered logic in simant/recovered/render.py.
MONOMAKE4X4_SEG_INDEX = 4
MONOMAKE4X4A_OFF = 0x442C
MONOMAKE4X4B_OFF = 0x44B9
MONOMAKE4X4_TABLE_BASE = 0x26A0                   # SS-relative pattern table base
MONOMAKE4X4A_PAIRS = 0x40                         # `mov cx, 0x40` (a half)
MONOMAKE4X4B_PAIRS = 0x20                         # `mov cx, 0x20` (b half)
MONOMAKE4X4A_SIG = bytes.fromhex(
    "558bec601e068b5e1083e30781c3a026")
MONOMAKE4X4B_SIG = bytes.fromhex(
    "558bec601e068b5e1083e30781c3a026")           # identical prologue to "a"


def _make_monomake4x4_island(machine, pairs):
    from .recovered.render import windows_mono_make_table_4x4

    def island(cpu) -> None:
        m = cpu.mem
        s = cpu.s
        ss, sp = s.ss, s.sp
        rw, rb = m.rw, m.rb
        ret_ip, ret_cs = rw(ss, sp), rw(ss, (sp + 2) & 0xFFFF)
        src_off, src_seg = rw(ss, (sp + 4) & 0xFFFF), rw(ss, (sp + 6) & 0xFFFF)
        dst_off, dst_seg = rw(ss, (sp + 8) & 0xFFFF), rw(ss, (sp + 0x0A) & 0xFFFF)
        mode = rw(ss, (sp + 0x0E) & 0xFFFF)          # (mode & 7) is the table phase

        base = (MONOMAKE4X4_TABLE_BASE + (mode & 7)) & 0xFFFF
        tiles = [rb(src_seg, (src_off + i) & 0xFFFF) for i in range(2 * pairs)]
        table = [[rb(ss, (base + t * 8 + r) & 0xFFFF) for r in range(4)]
                 for t in range(256)]
        rows = windows_mono_make_table_4x4(tiles, table, pairs)
        for r in range(4):
            band = (dst_off + r * pairs) & 0xFFFF    # scanline stride == pair count
            row = rows[r]
            for j in range(pairs):
                m.wb(dst_seg, (band + j) & 0xFFFF, row[j])

        s.sp = (sp + 4) & 0xFFFF
        s.cs = ret_cs
        s.ip = ret_ip

    return island


# -- _WindowsMono_MakeTable2x2a/b (seg4:4542/45DB) — half-res mono packer ------
# Two scanlines, FOUR tiles per byte (2-bit slots); same SS pattern table as the
# 4x4 packer (rows 0..1 here).  count 0x20 (a) / 0x10 (b); stride == count.
MONOMAKE2X2A_OFF = 0x4542
MONOMAKE2X2B_OFF = 0x45DB
MONOMAKE2X2A_COUNT = 0x20
MONOMAKE2X2B_COUNT = 0x10
MONOMAKE2X2A_SIG = bytes.fromhex(
    "558bec601e068b5e1083e30781c3a026")           # identical prologue to 4x4
MONOMAKE2X2B_SIG = bytes.fromhex(
    "558bec601e068b5e1083e30781c3a026")


def _make_monomake2x2_island(machine, count):
    from .recovered.render import windows_mono_make_table_2x2

    def island(cpu) -> None:
        m = cpu.mem
        s = cpu.s
        ss, sp = s.ss, s.sp
        rw, rb = m.rw, m.rb
        ret_ip, ret_cs = rw(ss, sp), rw(ss, (sp + 2) & 0xFFFF)
        src_off, src_seg = rw(ss, (sp + 4) & 0xFFFF), rw(ss, (sp + 6) & 0xFFFF)
        dst_off, dst_seg = rw(ss, (sp + 8) & 0xFFFF), rw(ss, (sp + 0x0A) & 0xFFFF)
        mode = rw(ss, (sp + 0x0E) & 0xFFFF)

        base = (MONOMAKE4X4_TABLE_BASE + (mode & 7)) & 0xFFFF
        tiles = [rb(src_seg, (src_off + i) & 0xFFFF) for i in range(4 * count)]
        table = [[rb(ss, (base + t * 8 + r) & 0xFFFF) for r in range(2)]
                 for t in range(256)]
        rows = windows_mono_make_table_2x2(tiles, table, count)
        for r in range(2):
            band = (dst_off + r * count) & 0xFFFF    # scanline stride == count
            row = rows[r]
            for j in range(count):
                m.wb(dst_seg, (band + j) & 0xFFFF, row[j])

        s.sp = (sp + 4) & 0xFFFF
        s.cs = ret_cs
        s.ip = ret_ip

    return island


# -- _FlipWord / _FlipLong / _XFlipLong (seg4) — endian/word-order helpers -----
# Tiny leaf helpers: FlipWord byte-swaps a word (xchg ah,al); FlipLong byte-swaps
# each half of a long (AX=flip(hi), DX=flip(lo)); XFlipLong swaps the two WORDS of
# a dword in place through a far pointer.  All `retf` (caller cleans args).
FLIP_SEG_INDEX = 4
FLIPWORD_OFF = 0x7356
FLIPWORD_SIG = bytes.fromhex("558bec8b460686c45dcb")
FLIPLONG_OFF = 0x7360
FLIPLONG_SIG = bytes.fromhex("558bec8b460886c48b560686d65dcb")
XFLIPLONG_OFF = 0x52D8
XFLIPLONG_SIG = bytes.fromhex("558becc45e06268b0f268b470226890726894f02c9cb")


def _make_flipword_island(machine):
    from .recovered.byteops import flip_word

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        s.ax = flip_word(m.rw(s.ss, (sp + 4) & 0xFFFF))
        s.sp = (sp + 4) & 0xFFFF                  # retf: caller cleans the 1 arg
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_fliplong_island(machine):
    from .recovered.byteops import flip_long

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        lo = m.rw(s.ss, (sp + 4) & 0xFFFF)
        hi = m.rw(s.ss, (sp + 6) & 0xFFFF)
        s.ax, s.dx = flip_long(lo, hi)
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_xfliplong_island(machine):
    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        off = m.rw(s.ss, (sp + 4) & 0xFFFF)
        seg = m.rw(s.ss, (sp + 6) & 0xFFFF)
        w0 = m.rw(seg, off)                       # cx = es:[bx]
        w1 = m.rw(seg, (off + 2) & 0xFFFF)        # ax = es:[bx+2]
        m.ww(seg, off, w1)                        # es:[bx]   = ax
        m.ww(seg, (off + 2) & 0xFFFF, w0)         # es:[bx+2] = cx
        s.es, s.bx, s.cx, s.ax = seg, off, w0, w1  # register residue
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _exchange (seg4:6E05) — swap `count` bytes between two buffers ------------
# In-order byte swap (buffer 1 <-> buffer 2); pushaw/popaw preserve every reg.
EXCHANGE_OFF = 0x6E05
EXCHANGE_SIG = bytes.fromhex("558bec601e068b4e0ec47e06c5760aac268a25aa8864ffe2f6")


def _make_exchange_island(machine):
    from .recovered.byteops import exchange

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        b1_off, b1_seg = m.rw(s.ss, (sp + 4) & 0xFFFF), m.rw(s.ss, (sp + 6) & 0xFFFF)
        b2_off, b2_seg = m.rw(s.ss, (sp + 8) & 0xFFFF), m.rw(s.ss, (sp + 0x0A) & 0xFFFF)
        count = m.rw(s.ss, (sp + 0x0C) & 0xFFFF)
        exchange(count,
                 lambda i: m.rb(b1_seg, (b1_off + i) & 0xFFFF),
                 lambda i: m.rb(b2_seg, (b2_off + i) & 0xFFFF),
                 lambda i, v: m.wb(b1_seg, (b1_off + i) & 0xFFFF, v),
                 lambda i, v: m.wb(b2_seg, (b2_off + i) & 0xFFFF, v))
        # every register preserved (pushaw/popaw + push/pop ds,es)
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _CopyChar / _CopyCharRep (seg4:6C62 / 6CAA) — DIB glyph-column blits -------
# Blit a 16-row glyph into a DIB whose byte stride is the header width word >> 3
# (cached to DGROUP scratch 0x1D70).  Row 0 lands at 4+x+((y*2)&0xFF)*(stride&0xFF)
# past the DIB offset; each row steps by the full stride.  _CopyChar copies one
# source byte per row; _CopyCharRep replicates each source byte `rep` times
# horizontally (a run-fill).  Both preserve every register (pushaw/popaw + ds,es).
COPYCHAR_SEG_INDEX = 4
COPYCHAR_OFF = 0x6C62
COPYCHARREP_OFF = 0x6CAA
COPYCHAR_STRIDE_G = 0x1D70                       # DGROUP word: cached byte stride
COPYCHAR_SIG = bytes.fromhex(
    "558bec601e06b802698ed8c47e0e268b0583c702c1e803a3701d83c7028b460a"
    "03f88b460c8b16701dd1e0f6e203f88b16701d83ea01")
COPYCHARREP_SIG = bytes.fromhex(
    "558bec601e06b802698ed8c47e0e268b0583c702c1e803a3701d83c7028b460a"
    "03f88b460c8b16701dd1e0f6e203f88b16701d8b4e12")


def _make_copychar_island(machine, rep_arg):
    """rep_arg is None for _CopyChar (implicit rep=1) or the sp-offset of the
    horizontal repeat count for _CopyCharRep."""
    from .recovered.render import copy_char, copy_char_rep

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        rw = m.rw
        ret_ip, ret_cs = rw(ss, sp), rw(ss, (sp + 2) & 0xFFFF)
        src_off, src_seg = rw(ss, (sp + 4) & 0xFFFF), rw(ss, (sp + 6) & 0xFFFF)
        x = rw(ss, (sp + 8) & 0xFFFF)
        y = rw(ss, (sp + 0x0A) & 0xFFFF)
        dst_off, dst_seg = rw(ss, (sp + 0x0C) & 0xFFFF), rw(ss, (sp + 0x0E) & 0xFFFF)

        stride = m.rw(dst_seg, dst_off) >> 3          # DIB header width word >> 3
        m.ww(machine.seg_bases[DG_SEG_INDEX], COPYCHAR_STRIDE_G, stride)
        src = [m.rb(src_seg, (src_off + i) & 0xFFFF) for i in range(16)]
        if rep_arg is None:
            writes = copy_char(src, x, y, stride)
        else:
            writes = copy_char_rep(src, x, y, stride, rw(ss, (sp + rep_arg) & 0xFFFF))
        for off, b in writes.items():
            m.wb(dst_seg, (dst_off + off) & 0xFFFF, b)

        # Every register preserved (pushaw/popaw + push/pop ds,es).
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _MoveTextToBalloon (seg4:6CF8) — inverting bitmap blit into a balloon DIB --
# Copies a {u16 width, u16 height, far* pixels} source bitmap into a destination
# DIB, XOR-ing every byte (invert) and landing source rows on every other dst
# scanline (dst step = dst_stride*2 - src_stride).  Same DGROUP stride scratch
# (0x1D70) and all-registers-preserved profile as _CopyChar.
MOVETEXTTOBALLOON_OFF = 0x6CF8
MOVETEXTTOBALLOON_SIG = bytes.fromhex(
    "558bec601e06b802698ed8c47e0a268b0583c702c1e803a3701d83c7028b460e"
    "03f88b46108b16701dd1e0f6e203f88b")


def _make_movetexttoballoon_island(machine):
    from .recovered.render import move_text_to_balloon

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        rw = m.rw
        ret_ip, ret_cs = rw(ss, sp), rw(ss, (sp + 2) & 0xFFFF)
        src_off, src_seg = rw(ss, (sp + 4) & 0xFFFF), rw(ss, (sp + 6) & 0xFFFF)
        dst_off, dst_seg = rw(ss, (sp + 8) & 0xFFFF), rw(ss, (sp + 0x0A) & 0xFFFF)
        x = rw(ss, (sp + 0x0C) & 0xFFFF)
        y = rw(ss, (sp + 0x0E) & 0xFFFF)

        stride = m.rw(dst_seg, dst_off) >> 3          # dst DIB header width >> 3
        m.ww(machine.seg_bases[DG_SEG_INDEX], COPYCHAR_STRIDE_G, stride)
        src_width = m.rw(src_seg, src_off)
        src_height = m.rw(src_seg, (src_off + 2) & 0xFFFF)
        pix_off, pix_seg = m.rw(src_seg, (src_off + 4) & 0xFFFF), m.rw(src_seg, (src_off + 6) & 0xFFFF)
        n = ((src_width + 7) >> 3) * src_height
        pixels = [m.rb(pix_seg, (pix_off + i) & 0xFFFF) for i in range(n)]
        for off, b in move_text_to_balloon(pixels, src_width, src_height, stride, x, y).items():
            m.wb(dst_seg, (dst_off + off) & 0xFFFF, b)

        # Every register preserved (pushaw/popaw + push/pop ds,es).
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _os_ClipLine (seg4:6E24) — midpoint line clipper (near call) --------------
# Register ABI: endpoints in si/di (P0) and dx/bx (P1); clip bounds in DGROUP
# words 0x1D7A (a-axis) / 0x1D78 (b-axis); persistent swap-parity in 0x1D82.
# Returns CF=1 (trivial reject) or CF=0 (accept, endpoints clipped in place).
# ax is preserved (push/pop); cx is clobbered.
CLIPLINE_SEG_INDEX = 4
CLIPLINE_OFF = 0x6E24
CLIPLINE_BOUND_A_G = 0x1D7A
CLIPLINE_BOUND_B_G = 0x1D78
CLIPLINE_SWAP_G = 0x1D82
CLIPLINE_SIG = bytes.fromhex(
    "50c706821d000032c083fe007c133b367a1d7f1c83ff007c263b3e781d7f24")


def _make_clipline_island(machine):
    from .recovered.geometry import clip_line

    def _sx(v):
        return v - 0x10000 if v & 0x8000 else v

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)                          # near call: one return word
        ds = s.ds
        bound_a = _sx(m.rw(ds, CLIPLINE_BOUND_A_G))
        bound_b = _sx(m.rw(ds, CLIPLINE_BOUND_B_G))
        accepted, a0, b0, a1, b1, swap, cx = clip_line(
            _sx(s.si), _sx(s.di), _sx(s.dx), _sx(s.bx), bound_a, bound_b, s.cx)
        m.ww(ds, CLIPLINE_SWAP_G, swap)
        s.si, s.di = a0 & 0xFFFF, b0 & 0xFFFF
        s.dx, s.bx = a1 & 0xFFFF, b1 & 0xFFFF
        s.cx = cx & 0xFFFF                             # clobbered residue (last b-midpoint)
        s.flags = (s.flags & ~CF) | (0 if accepted else CF)
        # ax / bp / ds preserved; near ret pops the single return word.
        s.sp = (sp + 2) & 0xFFFF
        s.ip = ret_ip

    return island


# -- _IsItFood (seg6:2D1A) — simulation: is this map tile food? ----------------
# The first of the pure-gameplay islands (recovered/gameplay.py).  A world-state
# flag ([0xC320]:[0x9B6E]) picks the food tile range: inside the nest 0x18..0x27,
# in the outside yard 0x48..0x4B.  Returns AX=1/0; clobbers dx (=arg) and es
# (=the world selector); bx/cx/si/di/bp preserved.
ISITFOOD_SEG_INDEX = 6
ISITFOOD_OFF = 0x2D1A
ISITFOOD_WORLD_SEG_G = 0xC320                    # DGROUP word: world-state selector
ISITFOOD_INSIDE_FLAG_OFF = 0x9B6E               # offset of the inside-nest flag
ISITFOOD_SIG = bytes.fromhex(
    "558bec8e0620c326833e6e9b0075138b560683fa487c1883fa4b7f13b801")


def _make_isitfood_island(machine):
    from .recovered.gameplay import is_it_food

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        ret_ip, ret_cs = m.rw(ss, sp), m.rw(ss, (sp + 2) & 0xFFFF)
        arg = m.rw(ss, (sp + 4) & 0xFFFF)
        tile = arg - 0x10000 if arg & 0x8000 else arg
        world_seg = m.rw(s.ds, ISITFOOD_WORLD_SEG_G)
        inside = m.rw(world_seg, ISITFOOD_INSIDE_FLAG_OFF) != 0
        s.ax = is_it_food(tile, inside)               # AX = 1/0
        s.dx = arg                                    # clobbered = the loaded arg
        s.es = world_seg                              # clobbered = the world selector
        # bp/bx/cx/si/di preserved; retf pops ip+cs, caller cleans the arg.
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _IsYellowAnt (seg5:5720) — simulation: is this the player's yellow ant? ----
# Returns AX=1 when the caste/marker value is 0xFE or 0xFF (the yellow-ant
# sentinels), else 0.  Clobbers dx (=arg); bp/bx/cx/si/di/es/ds preserved.
ISYELLOWANT_SEG_INDEX = 5
ISYELLOWANT_OFF = 0x5720
ISYELLOWANT_SIG = bytes.fromhex("558bec8b560681faff00740a81fafe00740433c0")


def _make_isyellowant_island(machine):
    from .recovered.gameplay import is_yellow_ant

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        ret_ip, ret_cs = m.rw(ss, sp), m.rw(ss, (sp + 2) & 0xFFFF)
        arg = m.rw(ss, (sp + 4) & 0xFFFF)
        s.ax = is_yellow_ant(arg)                     # AX = 1/0
        s.dx = arg                                    # clobbered = the loaded arg
        # bp/bx/cx/si/di/es/ds preserved; retf pops ip+cs, caller cleans the arg.
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _IsItDirt (seg5:1182) — simulation: is this tile diggable dirt? -----------
# Returns AX=1 when 0x20 <= tile <= 0x2E (signed), else 0.  Clobbers dx (=arg);
# bp/bx/cx/si/di/es/ds preserved.  Companion of _IsItFood.
ISITDIRT_SEG_INDEX = 5
ISITDIRT_OFF = 0x1182
ISITDIRT_SIG = bytes.fromhex("558bec8b560683fa207c0b83fa2e7f06b801")


def _make_isitdirt_island(machine):
    from .recovered.gameplay import is_it_dirt

    def _sx(v):
        return v - 0x10000 if v & 0x8000 else v

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        ret_ip, ret_cs = m.rw(ss, sp), m.rw(ss, (sp + 2) & 0xFFFF)
        arg = m.rw(ss, (sp + 4) & 0xFFFF)
        s.ax = is_it_dirt(_sx(arg))                   # AX = 1/0
        s.dx = arg                                    # clobbered = the loaded arg
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _InNestBounds (seg5:115C) — simulation: is (x, y) a valid nest cell? -------
# 64x64 grid: x in 0..0x3F, y in 1..0x3F (row 0 excluded).  AX=1/0.  Clobbers dx
# (= x if the x-check failed, else y — the ASM reloads dx before the y-check);
# bp/bx/cx/si/di/es/ds preserved.
INNESTBOUNDS_SEG_INDEX = 5
INNESTBOUNDS_OFF = 0x115C
INNESTBOUNDS_SIG = bytes.fromhex("558bec8b56060bd27c1883fa3f7f138b560883fa01")


def _make_innestbounds_island(machine):
    from .recovered.gameplay import in_nest_bounds

    def _sx(v):
        return v - 0x10000 if v & 0x8000 else v

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        ret_ip, ret_cs = m.rw(ss, sp), m.rw(ss, (sp + 2) & 0xFFFF)
        x_word = m.rw(ss, (sp + 4) & 0xFFFF)
        y_word = m.rw(ss, (sp + 6) & 0xFFFF)
        x = _sx(x_word)
        in_x = 0 <= x <= 0x3F
        s.ax = in_nest_bounds(x, _sx(y_word))
        s.dx = x_word if not in_x else y_word         # ASM reloads dx before y-check
        # bp/bx/cx/si/di/es/ds preserved; retf pops ip+cs, caller cleans the args.
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _CopyName (seg4:7438) — the NetBIOS 16-byte name-field copy ---------------
# Space-fill 16 bytes, copy min(strlen(src),16), force byte 15 to NUL.  di/si
# preserved (push/pop); ax/bx/cx/dx clobbered (residue below); es = dst seg.
COPYNAME_SEG_INDEX = 4
COPYNAME_OFF = 0x7438
COPYNAME_SIG = bytes.fromhex("c80200005756b82000b91000c47e06f3aac47e0a")


def _make_copyname_island(machine):
    from .recovered.netbios import copy_name

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        dst_off, dst_seg = m.rw(s.ss, (sp + 4) & 0xFFFF), m.rw(s.ss, (sp + 6) & 0xFFFF)
        src_off, src_seg = m.rw(s.ss, (sp + 8) & 0xFFFF), m.rw(s.ss, (sp + 0x0A) & 0xFFFF)

        src = bytearray()
        for i in range(0x110):                    # bounded scan for the NUL
            b = m.rb(src_seg, (src_off + i) & 0xFFFF)
            src.append(b)
            if b == 0:
                break
        field = copy_name(src)
        for i in range(0x10):
            m.wb(dst_seg, (dst_off + i) & 0xFFFF, field[i])

        s.ax, s.bx, s.cx, s.dx = 0, dst_off, 0, src_seg   # clobbered-reg residue
        s.es = dst_seg
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _GenOverMap (seg4:46E9) — the overlay-map compositor ----------------------
# Composites two column-major source layers (via LUTs) into a 64x128 row-major
# overlay.  pushaw/popaw preserve every register; the only effects are the dst
# write set and echoing the two table bases to DGROUP scratch (0x1B76/0x1B78).
GENOVERMAP_SEG_INDEX = 4
GENOVERMAP_OFF = 0x46E9
GENOVERMAP_TBL1_G = 0x1B76
GENOVERMAP_TBL2_G = 0x1B78
GENOVERMAP_SIG = bytes.fromhex("558bec601e06c47e068b4e0a8b560c")


def _make_genovermap_island(machine):
    from .recovered.render import gen_over_map

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ss, sp = s.ss, s.sp
        rw = m.rw
        ret_ip, ret_cs = rw(ss, sp), rw(ss, (sp + 2) & 0xFFFF)
        dst_off, dst_seg = rw(ss, (sp + 4) & 0xFFFF), rw(ss, (sp + 6) & 0xFFFF)
        cx0 = rw(ss, (sp + 8) & 0xFFFF)
        dx0 = rw(ss, (sp + 0x0A) & 0xFFFF)
        tbl1 = rw(ss, (sp + 0x0C) & 0xFFFF)
        tbl2 = rw(ss, (sp + 0x0E) & 0xFFFF)
        mode = rw(ss, (sp + 0x10) & 0xFFFF)

        ds = s.ds                                 # sources + tables + scratch are DS-relative
        m.ww(ds, GENOVERMAP_TBL1_G, tbl1)
        m.ww(ds, GENOVERMAP_TBL2_G, tbl2)
        writes = gen_over_map(cx0, dx0, tbl1, tbl2, mode, lambda off: m.rb(ds, off))
        for di, val in writes.items():
            m.wb(dst_seg, (dst_off + di) & 0xFFFF, val)

        # Every register is preserved (pushaw/popaw + push/pop ds,es).
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- the SIMONE PRNG family (seg5) -------------------------------------------
#
# The simulation's one LFSR (recovered in simant/recovered/simone.py): every
# _SRand* call steps `seed <<= 1; if carry: seed ^= 0x1BF5` on the DGROUP word
# at 0xCBF2, then reduces — `% n` in _SRand1, `& mask` in the nine compiled
# power-of-two copies (identical code except the AND immediate, which the
# island reads out of the matched bytes).  ABI (cdecl far, caller cleans):
# result in AX; DX = remainder (_SRand1, where BX = n) or 0 (masked); flags
# from the last flag-writer (shl/xor -> and, or the flagless div); freed-frame
# residue [sp-2]=saved BP, [sp-4]=result must match (C reads uninitialised
# locals — the _Unpack lesson).  Islands reproduce flags via the CPU's own
# set_logic_flags/shift helpers, so they match the interpreter by construction.
SRAND_SEG_INDEX = 5
SRAND_SEED_OFF = 0xCBF2                          # DGROUP word: the LFSR state
SRAND1_OFF = 0x158A
SRAND1_SIG = bytes.fromhex(
    "c8020000ba0000a1f2cbd1e0730335f51ba3f2cb8b5e06f7f38bc28946fe8b46fec9cb")
# The masked variants share this shape; the 16-bit AND immediate sits between.
SRAND_MASK_SIG_PREFIX = bytes.fromhex(
    "c8020000ba0000a1f2cbd1e0730335f51ba3f2cb25")
SRAND_MASK_SIG_SUFFIX = bytes.fromhex("8946fe8b46fec9cb")
SRAND_MASK_OFFS = [
    (0x15AE, "_SRand2"), (0x15CE, "_SRand4"), (0x15EE, "_SRand8"),
    (0x160E, "_SRand16"), (0x162E, "_SRand32"), (0x164E, "_SRand64"),
    (0x166E, "_SRand128"), (0x168E, "_SRand256"),
]
# The seed accessors that complete the module's RAND section:
#   _SetSRandSeed(v): seed = v            (AX = v at exit, [sp-2]=saved BP)
#   _GetSRandSeed(): DX:AX = 0:seed       (sub dx,dx sets flags)
#   _GetRRandSeed(): DX:AX = BIOS ticks   (ES=0x046C, BX=0, xor sets flags)
#   _SetRRandSeed(): empty stub (retf)
SETSRANDSEED_OFF, SETSRANDSEED_SIG = 0x1506, bytes.fromhex("558bec8b4606a3f2cbc9cb")
GETSRANDSEED_OFF, GETSRANDSEED_SIG = 0x1512, bytes.fromhex("a1f2cb2bd2cb")
SETRRANDSEED_OFF, SETRRANDSEED_SIG = 0x1518, bytes.fromhex("cb90")
GETRRANDSEED_OFF, GETRRANDSEED_SIG = 0x151A, bytes.fromhex(
    "bb6c048ec333db268b07268b5702cb")
BIOS_TICK_SEG = 0x046C                           # 0040:006C as a segment


def _srand_common(cpu, seed: int, new: int) -> None:
    """The shl (+ xor when the carry was set) flag effects, via the CPU's own
    helpers — identical to interpreting the instructions."""
    cpu.shift(4, seed, 1, 16)
    if seed & 0x8000:
        cpu.set_logic_flags(new, 16)


def _make_srand1_island(machine):
    from .recovered.simone import srand1
    from .bridge.dgroup_view import SelectorBackend, SimAntState

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        state = SimAntState(SelectorBackend(m, s.ds))   # the state-view seam
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        n = m.rw(s.ss, (sp + 4) & 0xFFFF)
        seed = state.rng_seed
        if n == 0:
            raise ZeroDivisionError(
                "_SRand1 island: modulus 0 — the ASM would #DE here")
        new, result = srand1(seed, n)
        _srand_common(cpu, seed, new)            # div writes no flags (dos_re)
        state.rng_seed = new
        m.ww(s.ss, (sp - 2) & 0xFFFF, s.bp)      # freed frame: enter's push bp
        m.ww(s.ss, (sp - 4) & 0xFFFF, result)    # [bp-2] result scratch
        s.ax = result
        s.dx = result                            # mov ax,dx after div: both = rem
        s.bx = n
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_srand_mask_island(machine, off):
    from .recovered.simone import srand_pow2
    from .bridge.dgroup_view import SelectorBackend, SimAntState
    cs = machine.seg_bases[SRAND_SEG_INDEX]
    mask = machine.mem.rw(cs, off + len(SRAND_MASK_SIG_PREFIX))

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        state = SimAntState(SelectorBackend(m, s.ds))
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        seed = state.rng_seed
        new, result = srand_pow2(seed, mask)
        _srand_common(cpu, seed, new)
        cpu.set_logic_flags(result, 16)          # the AND's flags
        state.rng_seed = new
        m.ww(s.ss, (sp - 2) & 0xFFFF, s.bp)
        m.ww(s.ss, (sp - 4) & 0xFFFF, result)
        s.ax = result
        s.dx = 0
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_setsrandseed_island(machine):
    from .bridge.dgroup_view import SelectorBackend, SimAntState

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        v = m.rw(s.ss, (sp + 4) & 0xFFFF)
        SimAntState(SelectorBackend(m, s.ds)).rng_seed = v
        m.ww(s.ss, (sp - 2) & 0xFFFF, s.bp)      # push bp / leave residue
        s.ax = v
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_getsrandseed_island(machine):
    from .bridge.dgroup_view import SelectorBackend, SimAntState

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        s.ax = SimAntState(SelectorBackend(m, s.ds)).rng_seed
        cpu.set_sub_flags(s.dx, s.dx, 0, 16)     # sub dx,dx
        s.dx = 0
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_setrrandseed_island(machine):
    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        ret_ip, ret_cs = m.rw(s.ss, s.sp), m.rw(s.ss, (s.sp + 2) & 0xFFFF)
        s.sp = (s.sp + 4) & 0xFFFF               # the routine is a bare retf
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_getrrandseed_island(machine):
    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        s.es = BIOS_TICK_SEG
        cpu.set_logic_flags(0, 16)               # xor bx,bx
        s.bx = 0
        s.ax = m.rw(BIOS_TICK_SEG, 0)            # BIOS tick dword at 0040:006C
        s.dx = m.rw(BIOS_TICK_SEG, 2)
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


# Registry of (segment index, entry offset, signature, island factory, name).
# Each factory takes (machine, off) and returns the hook fn.
# -- _win_IsWinOpen (seg7:C256, SIMTWO_MODULE) --------------------------------
#
# Recovered logic in recovered/window.py: a window "object handle" packs its
# window-table slot in the HIGH byte; the window is open iff g_window_hwnd[slot]
# (the word table at DGROUP:0xBCA6, read via DS) is non-zero AND USER reports it
# visible.  The routine far-calls USER.IsWindowVisible (import thunk 0060:00F0,
# the exact target the compiled `lcall 0x60,0xF0` names) and folds the result to
# a 0/1 boolean.  ABI (far, caller cleans the one word arg):
#   entry SP -> [ret_ip][ret_cs][objHandle]
#   AX = 0/1 result; BX = &g_window_hwnd[slot] (the shl+add pointer, never
#   restored — a compiled artifact the oracle still checks); flags = the
#   logic-flags of that 0/1 (the final or/xor writer; IsWindowVisible returns
#   canonical 0/1, so `set_logic_flags(result)` reproduces them exactly);
#   CX/DX/SI/DI/BP/DS/ES preserved; retf (no arg cleanup).
ISWINOPEN_SEG_INDEX = 7
ISWINOPEN_OFF = 0xC256
ISWINOPEN_HWND_TABLE_OFF = 0xBCA6                # DGROUP word table: slot -> HWND
ISWINVISIBLE_THUNK_OFF = 0x00F0                  # USER.IsWindowVisible import thunk
ISWINOPEN_SIG = bytes.fromhex(                   # enter..push si..sar..shl..add 0xBCA6
    "c8020000568b76068bdec1fb08d1e381c3a6bc")


def _make_iswinopen_island(machine):
    from .recovered.window import win_is_win_open, _sar16
    from .bridge.dgroup_view import SelectorBackend, SimAntState
    from win16.callback import call_far
    from win16.loader import THUNK_SEG

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        state = SimAntState(SelectorBackend(m, s.ds))
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        obj_handle = m.rw(ss, (sp + 4) & 0xFFFF)

        def hwnd_of_slot(slot: int) -> int:
            return state.window_hwnd[slot & 0xFFFF]

        def is_window_visible(hwnd: int) -> int:
            ax, _dx = call_far(cpu, THUNK_SEG, THUNK_SEG, ISWINVISIBLE_THUNK_OFF, [hwnd])
            return ax

        result = win_is_win_open(obj_handle, hwnd_of_slot, is_window_visible)

        # Compiled-form residue the ASM leaves, which the register oracle checks:
        slot = _sar16(obj_handle, 8)
        s.bx = (ISWINOPEN_HWND_TABLE_OFF + slot * 2) & 0xFFFF   # &g_window_hwnd[slot]
        s.ax = result
        cpu.set_logic_flags(result, 16)                        # final or/xor flags

        s.sp = (sp + 4) & 0xFFFF        # retf pops ret_ip+ret_cs; caller cleans the arg
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _win_GetObjRect (seg7:C2D2, SIMTWO_MODULE) -------------------------------
#
# Recovered logic in recovered/window.py: copy object `objHandle`'s stored RECT
# into `*lpRect`, bumping right/bottom when the DGROUP:0xBD0A "inclusive rects"
# flag is set.  The source RECT is reached by a two-level far-pointer walk:
#   winrec far* = *(farptr*)(DGROUP:0xCE9A + (objHandle>>8)*4)
#   src   far* = *(farptr*)(winrec + 0x2C + (objHandle&0xFF)*4)
# The routine brackets the copy with _win_LockWin/_win_UnlockWin (both `retf`
# no-ops under the fixed Win16 memory model — no state effect, so the island
# need not re-issue them).  ABI (far, caller cleans the 3 arg words):
#   entry SP -> [ret_ip][ret_cs][objHandle][lpRect.off][lpRect.seg]
#   writes the 4-word RECT to lpRect; AX = src RECT offset, DX = src segment
#   (the es:[bx+si+0x2c/2e] loads), ES = lpRect segment, BX = &lpRect (adjust)
#   or (objHandle&0xFF)*4 (no adjust); CX/SI/DI/BP/DS preserved; retf.
# (Flags at retf come from the final `add sp,2` arg-cleanup — a calling-
#  convention artifact, not logic; the register+memory oracle covers this
#  island, the machine lift covers full-state incl. flags.)
GETOBJRECT_SEG_INDEX = 7
GETOBJRECT_OFF = 0xC2D2
# The DGROUP slot->winrec far-ptr table (0xCE9A) and the inclusive-rects flag
# (0xBD0A) are named in the bridge (SimAntState.window_records / obj_rect_inclusive).
GETOBJRECT_OBJARR_OFF = 0x2C                     # winrec+0x2C: far-ptr array, obj -> RECT
GETOBJRECT_SIG = bytes.fromhex(                  # prologue + push arg + call _win_LockWin
    "558bec5756ff7606900ee8c92083c4028a5e06")


def _make_getobjrect_island(machine):
    from .recovered.window import win_get_obj_rect
    from .bridge.dgroup_view import SelectorBackend, SimAntState

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        state = SimAntState(SelectorBackend(m, s.ds))
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        obj_handle = m.rw(ss, (sp + 4) & 0xFFFF)
        lprect_off = m.rw(ss, (sp + 6) & 0xFFFF)
        lprect_seg = m.rw(ss, (sp + 8) & 0xFFFF)

        # The far-pointer walk that resolves an object's stored RECT; also
        # captures the source far pointer, which the ASM leaves in DX:AX.  The
        # DGROUP-side slot table is named (state.window_records); the record's
        # own object-rect array lives in the record's segment, so it stays a raw
        # far read.
        src = {}

        def resolve_rect(slot: int, obj: int):
            rec = state.window_records[slot & 0xFFFF]
            rec_off, rec_seg = rec.off, rec.seg
            p = (rec_off + GETOBJRECT_OBJARR_OFF + obj * 4) & 0xFFFF
            src["off"], src["seg"] = m.rw(rec_seg, p), m.rw(rec_seg, (p + 2) & 0xFFFF)
            return tuple(m.rw(src["seg"], (src["off"] + i * 2) & 0xFFFF) for i in range(4))

        flag = state.obj_rect_inclusive
        rect = win_get_obj_rect(obj_handle, resolve_rect, flag)
        for i, word in enumerate(rect):
            m.ww(lprect_seg, (lprect_off + i * 2) & 0xFFFF, word)

        # Compiled residue the register oracle checks:
        s.ax, s.dx = src["off"], src["seg"]          # DX:AX = the src RECT far ptr
        s.es = lprect_seg                            # ES = lpRect segment (les di,[bp+8])
        s.bx = (lprect_off if flag else (obj_handle & 0xFF) * 4) & 0xFFFF

        s.sp = (sp + 4) & 0xFFFF        # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _GenNestMap (seg4:4754, _TEXT) -------------------------------------------
#
# The hottest routine in the demo (~26% of samples): builds the 64x64 nest
# colour map.  Recovered logic in recovered/render.py: per cell, classify the
# terrain byte to a palette colour (border 0xFE/0xFF -> A; high bit -> B; low
# nonzero -> C; empty 0x00 -> leave it (mode!=0) or a secondary table lookup).
# A `pusha`/`popa` + push/pop ds/es/bp frame means EVERY register is restored —
# the only observable effects are the output buffer and four DGROUP globals the
# routine caches its args into.  ABI (far, caller cleans the 18 arg bytes):
#   entry SP -> [ret_ip][ret_cs] then, as words:
#     +4 out.off  +6 out.seg  +8 terrain.off(->cx)  +0xA alt.off(->dx)
#     +0xC table_base  +0xE colA  +0x10 colB  +0x12 colC  +0x14 mode
#   writes 64*64 bytes to out (di stosb, wrapping at 0xFFFF) + the four globals;
#   all registers preserved; retf.  (Sources + table are read via the caller DS.)
GENNESTMAP_SEG_INDEX = 4
GENNESTMAP_OFF = 0x4754
GENNEST_TABLE_GLOBAL = 0x1B78                    # DGROUP word: empty-cell table base
GENNEST_COLA_GLOBAL = 0x1B7A                     # DGROUP bytes: the three palette
GENNEST_COLB_GLOBAL = 0x1B7B                     #   colours cached from the args
GENNEST_COLC_GLOBAL = 0x1B7C
GENNESTMAP_SIG = bytes.fromhex(                  # prologue: push bp..pusha..les..mov args
    "558bec601e06c47e068b4e0a8b560c8b460e")


def _make_gennestmap_island(machine):
    from .recovered.render import gen_nest_map_cells, NEST_MAP_DIM

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        arg = lambda o: m.rw(ss, (sp + o) & 0xFFFF)
        out_off, out_seg = arg(4), arg(6)
        terrain_off, alt_off = arg(8), arg(0x0A)
        table_base = arg(0x0C)
        col_a, col_b, col_c = arg(0x0E) & 0xFF, arg(0x10) & 0xFF, arg(0x12) & 0xFF
        mode = arg(0x14)
        ds = s.ds

        # The routine caches its args into DGROUP globals first (observable).
        m.ww(ds, GENNEST_TABLE_GLOBAL, table_base)
        m.wb(ds, GENNEST_COLA_GLOBAL, col_a)
        m.wb(ds, GENNEST_COLB_GLOBAL, col_b)
        m.wb(ds, GENNEST_COLC_GLOBAL, col_c)

        def terrain(col, row):
            return m.rb(ds, (terrain_off + col + row * NEST_MAP_DIM) & 0xFFFF)

        def alt(col, row):
            return m.rb(ds, (alt_off + col + row * NEST_MAP_DIM) & 0xFFFF)

        def empty_lookup(alt_byte):
            return m.rb(ds, (table_base + (alt_byte >> 2)) & 0xFFFF)

        di = out_off
        for cell in gen_nest_map_cells(terrain, alt, empty_lookup,
                                       mode, col_a, col_b, col_c):
            if cell is not None:
                m.wb(out_seg, di & 0xFFFF, cell)
            di = (di + 1) & 0xFFFF        # stosb advances di even on the skip branch

        # pusha/popa + push/pop ds/es/bp restore every register — touch none.
        s.sp = (sp + 4) & 0xFFFF          # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _XferTileColor (seg4:47DD, _TEXT) ----------------------------------------
#
# A huge-pointer 2D tile-colour blit.  Recovered logic in recovered/render.py:
# copy `height` rows of `tile_w//2` bytes (4bpp) from a source tile into a DIB
# whose scanline is padded to a 32-bit boundary, advancing one `stride` per row.
# The destination is a >64K huge pointer (the ASM does `es += 8` per 64K); our
# selector heap maps consecutive selectors to contiguous memory, so the island
# resolves a linear destination offset back to (selector, off) with the same
# `+8 per 64K` rule — writing the identical bytes the ASM's rep-movsb loop does.
# A `pusha`/`popa` frame restores every register, so the only observable state
# is the destination bytes.  ABI (far, caller cleans the 22 arg bytes):
#   entry SP -> [ret_ip][ret_cs] then, as words:
#     +4 dst.off  +6 dst.seg  +8 dst_x  +0xA top  +0xC height  +0xE tile_w
#     +0x10 y_extent  +0x12 map_w  +0x14 src_tile  +0x16 src.off  +0x18 src.seg
XFERTILECOLOR_SEG_INDEX = 4
XFERTILECOLOR_OFF = 0x47DD
XFERTILECOLOR_HUGE_INCR = 8                       # selector delta per 64K (Win16 __AHINCR)
XFERTILECOLOR_SIG = bytes.fromhex(                # prologue + the stride mul
    "558bec601e068b4614f76610c1e002")


# -- _XferLifeTileColor (seg4:48FA, _TEXT) ------------------------------------
#
# The transparent sibling of _XferTileColor: identical DIB geometry + huge-
# pointer walk, but each source byte is blended over the destination instead of
# copied.  Recovered logic in recovered/render.py: sentinel 0xDD leaves the byte;
# a 0xD 4bpp pixel index is transparent (kept from the dest).  Reads AND writes
# the destination; pusha/popa preserves every register.  Same 22-byte far ABI
# as _XferTileColor (the setup code is byte-identical, hence the same prologue).
XFERLIFETILECOLOR_SEG_INDEX = 4
XFERLIFETILECOLOR_OFF = 0x48FA
XFERLIFETILECOLOR_SIG = bytes.fromhex(           # identical prologue to _XferTileColor
    "558bec601e068b4614f76610c1e002")


def _make_xferlifetilecolor_island(machine):
    from .recovered.render import xfer_life_tile_color

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        arg = lambda o: m.rw(ss, (sp + o) & 0xFFFF)
        dst_off, dst_seg = arg(4), arg(6)
        dst_x, top, height, tile_w = arg(8), arg(0x0A), arg(0x0C), arg(0x0E)
        y_extent, map_w, src_tile = arg(0x10), arg(0x12), arg(0x14)
        src_off, src_seg = arg(0x16), arg(0x18)

        def _dst_addr(off):
            full = dst_off + off
            return (dst_seg + XFERTILECOLOR_HUGE_INCR * (full >> 16)) & 0xFFFF, full & 0xFFFF

        def read_src(off):
            return m.rb(src_seg, (src_off + off) & 0xFFFF)

        def read_dst(off):
            seg, o = _dst_addr(off)
            return m.rb(seg, o)

        def write_dst(off, byte):
            seg, o = _dst_addr(off)
            m.wb(seg, o, byte)

        xfer_life_tile_color(read_src, read_dst, write_dst, dst_x, top, height,
                             tile_w, y_extent, map_w, src_tile)

        s.sp = (sp + 4) & 0xFFFF          # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _XferTileMono (seg4:486C, _TEXT) ----------------------------------------
#
# The 1bpp (monochrome) sibling of _XferTileColor: same 22-byte far ABI and huge-
# pointer DIB walk, but eight 1bpp pixels per byte (stride packs bits not nibbles,
# byte offsets are pixel>>3, tiles are 32 bytes) and the band is walked bottom-up.
# A pure copy (rep movsb), pusha/popa preserves every register.  Prologue matches
# _XferTileColor until the stride mul (mono `add ax,0x1F` where colour `shl ax,2`).
XFERTILEMONO_SEG_INDEX = 4
XFERTILEMONO_OFF = 0x486C
XFERTILEMONO_HUGE_INCR = 8                        # selector delta per 64K (Win16 __AHINCR)
XFERTILEMONO_SIG = bytes.fromhex(
    "558bec601e068b4614f7661083c01f")


# -- _XferLifeTileMono (seg4:49B7, _TEXT) ------------------------------------
#
# The transparent (masked) sibling of _XferTileMono: identical mono geometry +
# huge-pointer walk, but each byte is blended against a second source plane (the
# transparency mask, at a fixed +mask_delta from the data plane) instead of
# copied.  Reads AND writes the destination; pusha/popa preserves every register.
# Same mono prologue as _XferTileMono (hence the same 15-byte signature bytes).
XFERLIFETILEMONO_SEG_INDEX = 4
XFERLIFETILEMONO_OFF = 0x49B7
XFERLIFETILEMONO_HUGE_INCR = 8
XFERLIFETILEMONO_SIG = bytes.fromhex(
    "558bec601e068b4614f7661083c01f")


def _make_xferlifetilemono_island(machine):
    from .recovered.render import xfer_life_tile_mono

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        arg = lambda o: m.rw(ss, (sp + o) & 0xFFFF)
        dst_off, dst_seg = arg(4), arg(6)
        dst_x, top, height, tile_w = arg(8), arg(0x0A), arg(0x0C), arg(0x0E)
        y_extent, map_w, src_tile = arg(0x10), arg(0x12), arg(0x14)
        src_off, src_seg = arg(0x16), arg(0x18)

        def _dst_addr(off):
            full = dst_off + off
            return (dst_seg + XFERLIFETILEMONO_HUGE_INCR * (full >> 16)) & 0xFFFF, full & 0xFFFF

        def read_src(off):
            return m.rb(src_seg, (src_off + off) & 0xFFFF)

        def read_dst(off):
            seg, o = _dst_addr(off)
            return m.rb(seg, o)

        def write_dst(off, byte):
            seg, o = _dst_addr(off)
            m.wb(seg, o, byte)

        xfer_life_tile_mono(read_src, read_dst, write_dst, dst_x, top, height,
                            tile_w, y_extent, map_w, src_tile)

        s.sp = (sp + 4) & 0xFFFF          # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_xfertilemono_island(machine):
    from .recovered.render import xfer_tile_mono

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        arg = lambda o: m.rw(ss, (sp + o) & 0xFFFF)
        dst_off, dst_seg = arg(4), arg(6)
        dst_x, top, height, tile_w = arg(8), arg(0x0A), arg(0x0C), arg(0x0E)
        y_extent, map_w, src_tile = arg(0x10), arg(0x12), arg(0x14)
        src_off, src_seg = arg(0x16), arg(0x18)

        def read_src(off):
            return m.rb(src_seg, (src_off + off) & 0xFFFF)

        def write_dst(off, byte):
            full = dst_off + off
            seg = (dst_seg + XFERTILEMONO_HUGE_INCR * (full >> 16)) & 0xFFFF
            m.wb(seg, full & 0xFFFF, byte)

        xfer_tile_mono(read_src, write_dst, dst_x, top, height, tile_w,
                       y_extent, map_w, src_tile)

        # pusha/popa + push/pop ds/es/bp restore every register — touch none.
        s.sp = (sp + 4) & 0xFFFF          # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_xfertilecolor_island(machine):
    from .recovered.render import xfer_tile_color

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        arg = lambda o: m.rw(ss, (sp + o) & 0xFFFF)
        dst_off, dst_seg = arg(4), arg(6)
        dst_x, top, height, tile_w = arg(8), arg(0x0A), arg(0x0C), arg(0x0E)
        y_extent, map_w, src_tile = arg(0x10), arg(0x12), arg(0x14)
        src_off, src_seg = arg(0x16), arg(0x18)

        def read_src(off):
            return m.rb(src_seg, (src_off + off) & 0xFFFF)

        def write_dst(off, byte):
            full = dst_off + off                      # 32-bit huge offset from the far ptr
            seg = (dst_seg + XFERTILECOLOR_HUGE_INCR * (full >> 16)) & 0xFFFF
            m.wb(seg, full & 0xFFFF, byte)

        xfer_tile_color(read_src, write_dst, dst_x, top, height, tile_w,
                        y_extent, map_w, src_tile)

        # pusha/popa + push/pop ds/es/bp restore every register — touch none.
        s.sp = (sp + 4) & 0xFFFF          # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _DrawChar (seg7:B033, SIMTWO_MODULE) -------------------------------------
#
# A sub-byte-shifted OR-composite 1bpp glyph blit.  Recovered logic in
# recovered/render.py: per row, `width//8` overlapping shifted words OR'd into
# the destination, plus a partial edge column masked to `width & 7` top bits
# (mask table at CODE-seg:0xB02A, read via `xlatb` with a CS: override `2E`).
# Per-row strides come from
# DGROUP globals 0xB912 (src) / 0xB914 (dst); the routine hardcodes ds = DGROUP.
# Unlike the tile blits it does NOT pusha — it preserves si/di/ds/es/bp but
# CLOBBERS ax/bx/cx/dx, whose exit values the island reproduces:
#   bx = width; cx = (y&7)<<8 | (x&7);
#   no partial (width&7==0): ax = 0, dx = 0  (from `mov ax,bx; and ax,7`);
#   partial: dx = mask<<8, ax = the last row's partial shifted word.
# It also writes three scratch globals (0xB90E src seg, 0xB910 dst seg, 0xB918
# width>>3).  ABI (far, caller cleans 16 arg bytes): entry SP -> [ret_ip][ret_cs]
#   +4 src.off +6 src.seg +8 dst.off +0xA dst.seg +0xC width +0xE height
#   +0x10 x +0x12 y.
DRAWCHAR_SEG_INDEX = 7
DRAWCHAR_OFF = 0xB033
DRAWCHAR_MASK_TABLE_OFF = 0xB02A                 # CODE-seg top-n-bits mask table (CS: xlat)
# The blit's cached DGROUP scratch (0xB90E..0xB918) is named in
# simant/bridge/dgroup_view.py:DrawCharGlobals — the island reads/writes it there.
DRAWCHAR_SIG = bytes.fromhex(                     # push bp;mov bp,sp;add bp,6;push es/ds/si/di;mov ax,0x6902
    "558bec83c506061e5657b80269")


def _make_drawchar_island(machine):
    from .recovered.render import draw_char, shift_glyph_word
    from .bridge.dgroup_view import (SelectorBackend, DrawCharGlobals,
                                     DRAWCHAR_GLOBALS_BASE)
    dg = machine.seg_bases[DG_SEG_INDEX]
    code_seg = machine.seg_bases[DRAWCHAR_SEG_INDEX]     # CS: the mask table lives here

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        g = DrawCharGlobals(SelectorBackend(m, dg), DRAWCHAR_GLOBALS_BASE)
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        arg = lambda o: m.rw(ss, (sp + o) & 0xFFFF)
        src_off, src_seg = arg(4), arg(6)
        dst_off, dst_seg = arg(8), arg(0x0A)
        width, height, x, y = arg(0x0C), arg(0x0E), arg(0x10), arg(0x12)

        x_sub, y_sub = x & 7, y & 7
        si_base = (src_off + (x >> 3)) & 0xFFFF
        di_base = (dst_off + (y >> 3)) & 0xFFFF
        src_stride = g.src_stride
        dst_stride = g.dst_stride
        # The partial mask is read via `xlatb` with a CS: override (2E) — so it
        # comes from the CODE segment, NOT the glyph source segment.
        partial_mask = m.rb(code_seg, (DRAWCHAR_MASK_TABLE_OFF + (width & 7)) & 0xFFFF)

        def read_src(row, col):
            return m.rw(src_seg, (si_base + row * src_stride + col) & 0xFFFF)

        def read_dst(row, col):
            return m.rw(dst_seg, (di_base + row * dst_stride + col) & 0xFFFF)

        def write_dst(row, col, val):
            m.ww(dst_seg, (di_base + row * dst_stride + col) & 0xFFFF, val)

        draw_char(read_src, read_dst, write_dst, width, height, x_sub, y_sub,
                  partial_mask)

        # Scratch globals the routine caches (observable).
        g.src_seg = src_seg
        g.dst_seg = dst_seg
        g.words = width >> 3

        # Clobbered-register residue (not pusha-preserved):
        s.bx = width & 0xFFFF
        s.cx = ((y_sub << 8) | x_sub) & 0xFFFF
        if width & 7:
            s.dx = (partial_mask << 8) & 0xFFFF
            last = (si_base + (height - 1) * src_stride + (width >> 3)) & 0xFFFF
            s.ax = shift_glyph_word(m.rw(src_seg, last), x_sub, y_sub, partial_mask)
        else:
            s.ax = s.dx = 0                       # `mov ax,bx; and ax,7` leaves ax = 0

        s.sp = (sp + 4) & 0xFFFF          # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


# -- _DoCalcTile (seg4:4A6B, _TEXT) -------------------------------------------
#
# The map-cell tile resolver: the demo's #3 hot routine (184 insts, 4 view
# modes).  Recovered logic in recovered/render.py: given a tile (x, y) and the
# view mode (DGROUP:0xCC76), produce the cell's graphic index (CE96 byte) and
# attribute (CE7A word) by indexing per-mode graphic/attribute maps, with modes
# 0/1 first consulting five overlay layers (far-ptr table at 0xACAE, selector
# 0xAC58).  A pusha/popa frame restores every register, so the only observable
# state is the two DGROUP output globals.  ABI (far, caller cleans 4 arg bytes):
#   entry SP -> [ret_ip][ret_cs][tile_x][tile_y]
GENDOCALCTILE_SEG_INDEX = 4
DOCALCTILE_OFF = 0x4A6B
DOCALCTILE_MODE_G = 0xCC76                        # DGROUP: view mode selector
DOCALCTILE_SUB_G = 0xAC58                         # DGROUP: overlay-layer selector
DOCALCTILE_LAYER_TABLE = 0xACAE                   # DGROUP: 5 overlay far-pointers
DOCALCTILE_CE96, DOCALCTILE_CE7A = 0xCE96, 0xCE7A  # DGROUP: graphic + attribute out
DOCALCTILE_SIG = bytes.fromhex(                  # prologue + xor bx,bx + the two output inits
    "558bec601e0633db881e96ce891e7ace")


def _make_docalctile_island(machine):
    from .recovered.render import do_calc_tile
    dg = machine.seg_bases[DG_SEG_INDEX]

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        tile_x = m.rw(ss, (sp + 4) & 0xFFFF)
        tile_y = m.rw(ss, (sp + 6) & 0xFFFF)
        ds = s.ds

        mode = m.rw(ds, DOCALCTILE_MODE_G)
        sub_mode = m.rw(ds, DOCALCTILE_SUB_G)

        def read_byte(off):
            return m.rb(ds, off & 0xFFFF)

        def read_word(off):
            return m.rw(ds, off & 0xFFFF)

        def read_layer(sub, index):
            base = (DOCALCTILE_LAYER_TABLE + sub * 4) & 0xFFFF
            off = m.rw(ds, base)
            seg = m.rw(ds, (base + 2) & 0xFFFF)
            return m.rb(seg, (off + index) & 0xFFFF)

        ce96, ce7a = do_calc_tile(mode, tile_x, tile_y, sub_mode,
                                  read_byte, read_word, read_layer)
        m.wb(ds, DOCALCTILE_CE96, ce96)
        m.ww(ds, DOCALCTILE_CE7A, ce7a)

        # pusha/popa + push/pop ds/es restore every register — touch none.
        s.sp = (sp + 4) & 0xFFFF          # retf pops ret_ip+ret_cs; caller cleans args
        s.cs, s.ip = ret_cs, ret_ip

    return island


_ISLANDS = [
    (RT_SEG_INDEX, AFULDIV_OFF, AFULDIV_SIG,
     lambda machine, off: _make_uldiv_island(off), "__aFuldiv"),
    (DRAWCHAR_SEG_INDEX, DRAWCHAR_OFF, DRAWCHAR_SIG,
     lambda machine, off: _make_drawchar_island(machine), "_DrawChar"),
    (GENDOCALCTILE_SEG_INDEX, DOCALCTILE_OFF, DOCALCTILE_SIG,
     lambda machine, off: _make_docalctile_island(machine), "_DoCalcTile"),
    (ISWINOPEN_SEG_INDEX, ISWINOPEN_OFF, ISWINOPEN_SIG,
     lambda machine, off: _make_iswinopen_island(machine), "_win_IsWinOpen"),
    (GETOBJRECT_SEG_INDEX, GETOBJRECT_OFF, GETOBJRECT_SIG,
     lambda machine, off: _make_getobjrect_island(machine), "_win_GetObjRect"),
    (GENNESTMAP_SEG_INDEX, GENNESTMAP_OFF, GENNESTMAP_SIG,
     lambda machine, off: _make_gennestmap_island(machine), "_GenNestMap"),
    (XFERTILECOLOR_SEG_INDEX, XFERTILECOLOR_OFF, XFERTILECOLOR_SIG,
     lambda machine, off: _make_xfertilecolor_island(machine), "_XferTileColor"),
    (XFERLIFETILECOLOR_SEG_INDEX, XFERLIFETILECOLOR_OFF, XFERLIFETILECOLOR_SIG,
     lambda machine, off: _make_xferlifetilecolor_island(machine), "_XferLifeTileColor"),
    (XFERTILEMONO_SEG_INDEX, XFERTILEMONO_OFF, XFERTILEMONO_SIG,
     lambda machine, off: _make_xfertilemono_island(machine), "_XferTileMono"),
    (XFERLIFETILEMONO_SEG_INDEX, XFERLIFETILEMONO_OFF, XFERLIFETILEMONO_SIG,
     lambda machine, off: _make_xferlifetilemono_island(machine), "_XferLifeTileMono"),
    (MONOMAKE4X4_SEG_INDEX, MONOMAKE4X4A_OFF, MONOMAKE4X4A_SIG,
     lambda machine, off: _make_monomake4x4_island(machine, MONOMAKE4X4A_PAIRS),
     "_WindowsMono_MakeTable4x4a"),
    (MONOMAKE4X4_SEG_INDEX, MONOMAKE4X4B_OFF, MONOMAKE4X4B_SIG,
     lambda machine, off: _make_monomake4x4_island(machine, MONOMAKE4X4B_PAIRS),
     "_WindowsMono_MakeTable4x4b"),
    (MONOMAKE4X4_SEG_INDEX, MONOMAKE2X2A_OFF, MONOMAKE2X2A_SIG,
     lambda machine, off: _make_monomake2x2_island(machine, MONOMAKE2X2A_COUNT),
     "_WindowsMono_MakeTable2x2a"),
    (MONOMAKE4X4_SEG_INDEX, MONOMAKE2X2B_OFF, MONOMAKE2X2B_SIG,
     lambda machine, off: _make_monomake2x2_island(machine, MONOMAKE2X2B_COUNT),
     "_WindowsMono_MakeTable2x2b"),
    (FLIP_SEG_INDEX, FLIPWORD_OFF, FLIPWORD_SIG,
     lambda machine, off: _make_flipword_island(machine), "_FlipWord"),
    (FLIP_SEG_INDEX, FLIPLONG_OFF, FLIPLONG_SIG,
     lambda machine, off: _make_fliplong_island(machine), "_FlipLong"),
    (FLIP_SEG_INDEX, XFLIPLONG_OFF, XFLIPLONG_SIG,
     lambda machine, off: _make_xfliplong_island(machine), "_XFlipLong"),
    (FLIP_SEG_INDEX, EXCHANGE_OFF, EXCHANGE_SIG,
     lambda machine, off: _make_exchange_island(machine), "_exchange"),
    (COPYCHAR_SEG_INDEX, COPYCHAR_OFF, COPYCHAR_SIG,
     lambda machine, off: _make_copychar_island(machine, None), "_CopyChar"),
    (COPYCHAR_SEG_INDEX, COPYCHARREP_OFF, COPYCHARREP_SIG,
     lambda machine, off: _make_copychar_island(machine, 0x10), "_CopyCharRep"),
    (COPYCHAR_SEG_INDEX, MOVETEXTTOBALLOON_OFF, MOVETEXTTOBALLOON_SIG,
     lambda machine, off: _make_movetexttoballoon_island(machine), "_MoveTextToBalloon"),
    (CLIPLINE_SEG_INDEX, CLIPLINE_OFF, CLIPLINE_SIG,
     lambda machine, off: _make_clipline_island(machine), "_os_ClipLine"),
    (ISITFOOD_SEG_INDEX, ISITFOOD_OFF, ISITFOOD_SIG,
     lambda machine, off: _make_isitfood_island(machine), "_IsItFood"),
    (ISYELLOWANT_SEG_INDEX, ISYELLOWANT_OFF, ISYELLOWANT_SIG,
     lambda machine, off: _make_isyellowant_island(machine), "_IsYellowAnt"),
    (INNESTBOUNDS_SEG_INDEX, INNESTBOUNDS_OFF, INNESTBOUNDS_SIG,
     lambda machine, off: _make_innestbounds_island(machine), "_InNestBounds"),
    (ISITDIRT_SEG_INDEX, ISITDIRT_OFF, ISITDIRT_SIG,
     lambda machine, off: _make_isitdirt_island(machine), "_IsItDirt"),
    (COPYNAME_SEG_INDEX, COPYNAME_OFF, COPYNAME_SIG,
     lambda machine, off: _make_copyname_island(machine), "_CopyName"),
    (GENOVERMAP_SEG_INDEX, GENOVERMAP_OFF, GENOVERMAP_SIG,
     lambda machine, off: _make_genovermap_island(machine), "_GenOverMap"),
    (GENNESTMAP_SEG_INDEX, GENNESTMAP_OFF, GENNESTMAP_SIG,
     lambda machine, off: _make_gennestmap_island(machine), "_GenNestMap"),
    (UNPACK_SEG_INDEX, UNPACK_OFF, UNPACK_SIG,
     lambda machine, off: _make_unpack_island(machine), "_Unpack"),
    (BYTECOPY_SEG_INDEX, BYTECOPY_OFF, BYTECOPY_SIG,
     lambda machine, off: _make_bytecopy_island(machine), "bytecopy"),
    (MAKETABLE4X4_SEG_INDEX, MAKETABLE4X4_OFF, MAKETABLE4X4_SIG,
     lambda machine, off: _make_maketable4x4_island(machine),
     "_Windows_MakeTable4x4"),
    (MAKETABLE1X1_SEG_INDEX, MAKETABLE1X1_OFF, MAKETABLE1X1_SIG,
     lambda machine, off: _make_maketable1x1_island(machine),
     "_Windows_MakeTable1x1"),
    (SRAND_SEG_INDEX, SRAND1_OFF, SRAND1_SIG,
     lambda machine, off: _make_srand1_island(machine), "_SRand1"),
    (SRAND_SEG_INDEX, SETSRANDSEED_OFF, SETSRANDSEED_SIG,
     lambda machine, off: _make_setsrandseed_island(machine), "_SetSRandSeed"),
    (SRAND_SEG_INDEX, GETSRANDSEED_OFF, GETSRANDSEED_SIG,
     lambda machine, off: _make_getsrandseed_island(machine), "_GetSRandSeed"),
    (SRAND_SEG_INDEX, SETRRANDSEED_OFF, SETRRANDSEED_SIG,
     lambda machine, off: _make_setrrandseed_island(machine), "_SetRRandSeed"),
    (SRAND_SEG_INDEX, GETRRANDSEED_OFF, GETRRANDSEED_SIG,
     lambda machine, off: _make_getrrandseed_island(machine), "_GetRRandSeed"),
]
# The nine masked _SRand variants are compiled copies whose AND immediate is
# the power of two in the name minus one; building each signature with that
# mask makes install() verify the assumption byte-exactly (mismatch = refuse).
for _off, _name in SRAND_MASK_OFFS:
    _mask = int(_name[6:]) - 1
    _ISLANDS.append(
        (SRAND_SEG_INDEX, _off,
         SRAND_MASK_SIG_PREFIX + _mask.to_bytes(2, "little")
         + SRAND_MASK_SIG_SUFFIX,
         lambda machine, off: _make_srand_mask_island(machine, off), _name))


def install(machine) -> int:
    """Install every SimAnt island whose entry bytes still match its recorded
    prologue.  Returns the number installed.  Refuses (AssertionError) if a
    routine's signature does not match — an island on the wrong code corrupts
    silently."""
    cpu = machine.cpu
    count = 0
    for seg_index, off, sig, factory, name in _ISLANDS:
        cs = machine.seg_bases[seg_index]
        actual = machine.mem.block(cs, off, len(sig))
        if actual != sig:
            raise AssertionError(
                f"simant island {name}: prologue at seg{seg_index}:{off:04X} is "
                f"{actual.hex()}, expected {sig.hex()} — wrong binary/offset?")
        cpu.replacement_hooks[(cs, off)] = factory(machine, off)
        cpu.hook_names[(cs, off)] = f"{name}@{seg_index}:{off:04X}"
        count += 1
    return count
