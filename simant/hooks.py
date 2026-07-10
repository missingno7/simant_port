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
    dg = machine.seg_bases[DG_SEG_INDEX]

    def island(cpu) -> None:
        m = cpu.mem
        s = cpu.s
        rw, ww = m.rw, m.ww

        resume = rw(dg, 0xB7D4)
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

        r = rw(dg, 0xB7CA)                        # window write pos (bx)
        dx = rw(dg, 0xB7CE)
        cx = rw(dg, 0xB7D0)
        flags = rw(dg, 0xB7CC)                    # flag bit buffer (ax)
        win_seg = rw(dg, 0xB7C0)
        thresh = rw(dg, 0xB7C2)
        src_seg = rw(dg, 0xB7C6)
        src_off = rw(dg, 0xB7C4)
        in_rem = rw(dg, 0xB7C8)                   # signed input-remaining counter
        if in_rem >= 0x8000:
            in_rem -= 0x10000

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
            ww(dg, 0xB7D2, st_.match_rem & 0xFFFF)        # save match countdown

        # -- write back the exit state exactly as A779 does ------------------
        ww(dg, 0xB7D4, code)
        ww(dg, 0xB7CC, flags & 0xFFFF)
        ww(dg, 0xB7CA, r & 0xFFFF)
        ww(dg, 0xB7C4, src_off & 0xFFFF)
        ww(dg, 0xB7CE, dx & 0xFFFF)
        ww(dg, 0xB7D0, cx & 0xFFFF)
        ww(dg, 0xB7C8, in_rem & 0xFFFF)           # ASM decremented this in place
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

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        n = m.rw(s.ss, (sp + 4) & 0xFFFF)
        seed = m.rw(s.ds, SRAND_SEED_OFF)
        if n == 0:
            raise ZeroDivisionError(
                "_SRand1 island: modulus 0 — the ASM would #DE here")
        new, result = srand1(seed, n)
        _srand_common(cpu, seed, new)            # div writes no flags (dos_re)
        m.ww(s.ds, SRAND_SEED_OFF, new)
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
    cs = machine.seg_bases[SRAND_SEG_INDEX]
    mask = machine.mem.rw(cs, off + len(SRAND_MASK_SIG_PREFIX))

    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        seed = m.rw(s.ds, SRAND_SEED_OFF)
        new, result = srand_pow2(seed, mask)
        _srand_common(cpu, seed, new)
        cpu.set_logic_flags(result, 16)          # the AND's flags
        m.ww(s.ds, SRAND_SEED_OFF, new)
        m.ww(s.ss, (sp - 2) & 0xFFFF, s.bp)
        m.ww(s.ss, (sp - 4) & 0xFFFF, result)
        s.ax = result
        s.dx = 0
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_setsrandseed_island(machine):
    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        v = m.rw(s.ss, (sp + 4) & 0xFFFF)
        m.ww(s.ds, SRAND_SEED_OFF, v)
        m.ww(s.ss, (sp - 2) & 0xFFFF, s.bp)      # push bp / leave residue
        s.ax = v
        s.sp = (sp + 4) & 0xFFFF
        s.cs, s.ip = ret_cs, ret_ip

    return island


def _make_getsrandseed_island(machine):
    def island(cpu) -> None:
        m, s = cpu.mem, cpu.s
        sp = s.sp
        ret_ip, ret_cs = m.rw(s.ss, sp), m.rw(s.ss, (sp + 2) & 0xFFFF)
        s.ax = m.rw(s.ds, SRAND_SEED_OFF)
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
    from win16.callback import call_far
    from win16.loader import THUNK_SEG

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        obj_handle = m.rw(ss, (sp + 4) & 0xFFFF)

        def hwnd_of_slot(slot: int) -> int:
            off = (ISWINOPEN_HWND_TABLE_OFF + (slot * 2 & 0xFFFF)) & 0xFFFF
            return m.rw(s.ds, off)

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
GETOBJRECT_WINTAB_OFF = 0xCE9A                   # DGROUP far-ptr table: slot -> winrec
GETOBJRECT_OBJARR_OFF = 0x2C                     # winrec+0x2C: far-ptr array, obj -> RECT
GETOBJRECT_FLAG_OFF = 0xBD0A                     # DGROUP "inclusive rects" flag
GETOBJRECT_SIG = bytes.fromhex(                  # prologue + push arg + call _win_LockWin
    "558bec5756ff7606900ee8c92083c4028a5e06")


def _make_getobjrect_island(machine):
    from .recovered.window import win_get_obj_rect

    def island(cpu) -> None:
        s, m = cpu.s, cpu.mem
        ss, sp = s.ss, s.sp
        ret_ip = m.rw(ss, sp)
        ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)
        obj_handle = m.rw(ss, (sp + 4) & 0xFFFF)
        lprect_off = m.rw(ss, (sp + 6) & 0xFFFF)
        lprect_seg = m.rw(ss, (sp + 8) & 0xFFFF)

        # The far-pointer walk that resolves an object's stored RECT; also
        # captures the source far pointer, which the ASM leaves in DX:AX.
        src = {}

        def resolve_rect(slot: int, obj: int):
            t = (GETOBJRECT_WINTAB_OFF + (slot * 4 & 0xFFFF)) & 0xFFFF
            rec_off, rec_seg = m.rw(s.ds, t), m.rw(s.ds, (t + 2) & 0xFFFF)
            p = (rec_off + GETOBJRECT_OBJARR_OFF + obj * 4) & 0xFFFF
            src["off"], src["seg"] = m.rw(rec_seg, p), m.rw(rec_seg, (p + 2) & 0xFFFF)
            return tuple(m.rw(src["seg"], (src["off"] + i * 2) & 0xFFFF) for i in range(4))

        flag = m.rw(s.ds, GETOBJRECT_FLAG_OFF)
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


_ISLANDS = [
    (RT_SEG_INDEX, AFULDIV_OFF, AFULDIV_SIG,
     lambda machine, off: _make_uldiv_island(off), "__aFuldiv"),
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
