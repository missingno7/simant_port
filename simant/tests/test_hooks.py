"""SimAnt lifted islands — the byte-exact A/B oracle gate.

For every island we run the ORIGINAL ASM routine and the island over the same
inputs and require an identical register result.  That equivalence is the whole
value of a hook: it must be a recovery (exact), not an approximation.  A math
helper is a pure function, so this is a precise unit oracle — no whole-game
replay / desync to reason about.
"""
import pytest

from simant import hooks, runtime

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="simant assets not present")

SENT_CS, SENT_IP = 0x0001, 0x0002          # sentinel return address (never run)
# Distinct marker values in the callee-preserved registers, so the oracle also
# proves BX/SI/DI/BP survive the call.
MARK = dict(bx=0x1111, si=0x2222, di=0x3333, bp=0x4444)


def _setup_call(m, entry_off, dividend, divisor):
    """Point CS:IP at the routine with a synthetic far-call frame
    (ret=SENT, then dividend:dword, divisor:dword) and marker registers."""
    s = m.cpu.s
    s.cs = m.seg_bases[hooks.RT_SEG_INDEX]
    s.ip = entry_off
    s.bx, s.si, s.di, s.bp = MARK["bx"], MARK["si"], MARK["di"], MARK["bp"]
    sp = s.sp
    for v in (divisor >> 16, divisor & 0xFFFF, dividend >> 16, dividend & 0xFFFF,
              SENT_CS, SENT_IP):                # pushed high-address-first
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp


def _regs(m):
    # The ABI contract of __aFuldiv: result in DX:AX, BX/SI/DI/BP preserved, and
    # the retf stack unwind (SP, CS:IP).  CX (and FLAGS) are caller-clobbered
    # scratch — the routine leaves an algorithm-internal intermediate in CX on
    # the full-32-bit path, which no caller observes; replicating it would mean
    # re-running the very loop the island exists to skip.  So the oracle checks
    # the contract, not the scratch.
    s = m.cpu.s
    return dict(ax=s.ax, dx=s.dx, bx=s.bx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, cs=s.cs, ip=s.ip)


def _run_asm(m, dividend, divisor):
    _setup_call(m, hooks.AFULDIV_OFF, dividend, divisor)
    for _ in range(2000):                       # the divide loop is bounded
        m.cpu.step()
        if (m.cpu.s.cs & 0xFFFF, m.cpu.s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
            return _regs(m)
    raise AssertionError("ASM __aFuldiv did not return to the sentinel")


def _run_island(m, dividend, divisor):
    _setup_call(m, hooks.AFULDIV_OFF, dividend, divisor)
    m.cpu.step()                                # the installed hook fires once
    return _regs(m)


# Small, full-32-bit (divisor high != 0), by-one, and identity cases — the two
# code paths (divisor high == 0 vs != 0) plus boundaries.
CASES = [
    (13, 55), (55, 13), (1000, 7), (0, 1), (0xFFFFFFFF, 1),
    (0xFFFFFFFF, 0xFFFF), (0x12345678, 0x100), (0x12345678, 0x10000),
    (0xABCD1234, 0x1234), (100, 100), (0x80000000, 3), (0xFFFFFFFF, 0xFFFFFFFF),
    (0x00010000, 0x00000200), (0x7FFFFFFF, 0x00020000),
]


@pytest.mark.parametrize("dividend, divisor", CASES)
def test_uldiv_island_matches_asm(dividend, divisor):
    ref = runtime.create_machine()
    ref.cpu.trace_enabled = False
    asm = _run_asm(ref, dividend, divisor)

    hk = runtime.create_machine()
    hk.cpu.trace_enabled = False
    assert hooks.install(hk) == hooks.EXPECTED_ISLAND_COUNT              # all islands, incl. the PRNG family
    isl = _run_island(hk, dividend, divisor)

    assert asm["ax"] | (asm["dx"] << 16) == (dividend // divisor) & 0xFFFFFFFF
    assert isl == asm, (
        f"{dividend:#x} // {divisor:#x}: island {isl} != asm {asm}")


def test_install_counts_and_verifies():
    m = runtime.create_machine()
    assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    assert runtime.install_hooks(runtime.create_machine()) == hooks.EXPECTED_ISLAND_COUNT


def _capture_unpack_output(with_island, max_calls, step_budget):
    """Boot SimAnt (optionally with the _Unpack island) and return the list of
    (output_bytes, exit_globals) for each of the first `max_calls` _Unpack
    calls — the decompressor's observable result, per call."""
    from dos_re.cpu import CPU8086
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    cs7 = m.seg_bases[hooks.UNPACK_SEG_INDEX]
    dg = m.seg_bases[hooks.DG_SEG_INDEX]
    st = m.cpu.s
    out = []
    pend = {}

    if with_island:
        isl = hooks._make_unpack_island(m)

        def hook(cpu):
            sp = cpu.s.sp
            pend["a"] = (cpu.mem.rw(cpu.s.ss, (sp + 4) & 0xFFFF),
                         cpu.mem.rw(cpu.s.ss, (sp + 6) & 0xFFFF),
                         cpu.mem.rw(cpu.s.ss, (sp + 8) & 0xFFFF))
            pend["ret"] = (cpu.mem.rw(cpu.s.ss, sp),
                           cpu.mem.rw(cpu.s.ss, (sp + 2) & 0xFFFF))
            return isl(cpu)
        m.cpu.replacement_hooks[(cs7, hooks.UNPACK_OFF)] = hook

    orig = CPU8086.step

    def watch(self):
        cs, ip = st.cs & 0xFFFF, st.ip & 0xFFFF
        if not with_island and cs == cs7 and ip == hooks.UNPACK_OFF:
            sp = st.sp
            pend["a"] = (m.mem.rw(st.ss, (sp + 4) & 0xFFFF),
                         m.mem.rw(st.ss, (sp + 6) & 0xFFFF),
                         m.mem.rw(st.ss, (sp + 8) & 0xFFFF))
            pend["ret"] = (m.mem.rw(st.ss, sp), m.mem.rw(st.ss, (sp + 2) & 0xFFFF))
        if "a" in pend and (cs, ip) == (pend["ret"][1], pend["ret"][0]):
            oo, osg, budget = pend.pop("a")
            pend.pop("ret")
            data = bytes(m.mem.rb(osg, (oo + i) & 0xFFFF) for i in range(budget))
            exitg = tuple(m.mem.rw(dg, a) for a in
                          (0xB7CA, 0xB7CC, 0xB7C4, 0xB7C8, 0xB7CE, 0xB7D0, 0xB7D4))
            out.append((data, exitg))
        orig(self)

    CPU8086.step = watch
    try:
        while len(out) < max_calls and m.cpu.instruction_count < step_budget:
            m.cpu.run(400_000)
    except Exception:  # noqa: BLE001 — a frontier past the load is acceptable
        pass
    finally:
        CPU8086.step = orig
    return out[:max_calls]


def test_unpack_island_is_byte_exact_vs_asm():
    """The A/B decompression gate: booting with the _Unpack island must produce
    the IDENTICAL decompressed output and exit state, call for call, as the real
    ASM routine — the byte-exact proof that the LZSS island is a recovery."""
    CALLS, BUDGET = 30, 4_000_000
    plain = _capture_unpack_output(False, CALLS, BUDGET)
    island = _capture_unpack_output(True, CALLS, BUDGET)
    assert len(plain) >= CALLS, f"only {len(plain)} _Unpack calls captured"
    assert len(island) == len(plain)
    for i, (p, k) in enumerate(zip(plain, island)):
        assert k[0] == p[0], f"call {i}: island output differs ({len(k[0])} vs {len(p[0])} bytes)"
        assert k[1] == p[1], f"call {i}: island exit state differs {k[1]} vs {p[1]}"


def test_install_refuses_wrong_code():
    m = runtime.create_machine()
    cs = m.seg_bases[hooks.RT_SEG_INDEX]
    m.mem.data[(cs << 4) + hooks.AFULDIV_OFF:
               (cs << 4) + hooks.AFULDIV_OFF + 16] = bytes(16)
    with pytest.raises(AssertionError):
        hooks.install(m)


# ---- the byte-memcpy island (seg2:3460) --------------------------------------
from dos_re.cpu import ZF as _ZF                       # noqa: E402


def _run_bytecopy(with_island, src_seg, src_off, dst_seg, dst_off, n, pattern):
    """Set up the loop's frame (src/dst huge pointers @bp-8/-6/-12/-10, SI=n)
    with `pattern` at the source, run to the loop exit, and report the copied
    region + exit registers/frame.  with_island hooks seg2:3460 (one step);
    otherwise the real ASM loop runs."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    cs2 = m.seg_bases[hooks.BYTECOPY_SEG_INDEX]
    if with_island:
        hooks.install(m)
    s = m.cpu.s
    src_lin = m.mem._xlat(src_seg, src_off)
    m.mem.data[src_lin:src_lin + n] = pattern
    s.cs, s.ip, s.bp, s.si, s.ax = cs2, hooks.BYTECOPY_OFF, 0xC000, n & 0xFFFF, 0x5500
    for off, v in ((-8, src_off), (-6, src_seg), (-12, dst_off), (-10, dst_seg)):
        m.mem.ww(s.ss, (s.bp + off) & 0xFFFF, v)
    if with_island:
        m.cpu.step()
    else:
        for _ in range(n * 15 + 200):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (cs2, hooks.BYTECOPY_EXIT):
                break
        else:
            raise AssertionError("ASM byte-copy loop did not reach its exit")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (cs2, hooks.BYTECOPY_EXIT)
    dst_lin = m.mem._xlat(dst_seg, dst_off)
    return bytes(m.mem.data[dst_lin:dst_lin + n]), dict(
        ax=s.ax, bx=s.bx, es=s.es, si=s.si, zf=m.cpu.get_flag(_ZF),
        src_off=m.mem.rw(s.ss, (s.bp - 8) & 0xFFFF),
        dst_off=m.mem.rw(s.ss, (s.bp - 12) & 0xFFFF))


# (src_seg, src_off, dst_seg, dst_off, n) — real-mode segments in scratch RAM.
# Includes the real 960-byte tile-row case, a 1-byte edge, and an OVERLAPPING
# forward copy (dst 16 bytes after src) that must smear exactly like the ASM.
_COPY_CASES = [
    (0x7000, 0x0004, 0x7100, 0x0000, 960),      # the observed tile-row copy
    (0x7000, 0x0000, 0x7100, 0x0000, 1),
    (0x7000, 0x0000, 0x7100, 0x0000, 300),
    (0x7000, 0x0010, 0x7000, 0x0000, 200),      # dst before src (no smear)
    (0x7000, 0x0000, 0x7000, 0x0010, 200),      # dst after src -> smears
]


@pytest.mark.parametrize("src_seg, src_off, dst_seg, dst_off, n", _COPY_CASES)
def test_bytecopy_island_matches_asm(src_seg, src_off, dst_seg, dst_off, n):
    pattern = bytes((i * 37 + 11) & 0xFF for i in range(n))
    asm = _run_bytecopy(False, src_seg, src_off, dst_seg, dst_off, n, pattern)
    isl = _run_bytecopy(True, src_seg, src_off, dst_seg, dst_off, n, pattern)
    assert isl[0] == asm[0], "copied bytes differ"
    assert isl[1] == asm[1], f"exit state differs: island {isl[1]} != asm {asm[1]}"


# ---- _Windows_MakeTable4x4 (seg4:4674) ---------------------------------------
def _run_maketable(with_island, count, tiles, table):
    """Set up a synthetic call — source tiles at DS:SI, the 4x32-word colour
    table at SS:0x1A56, dest band at ES:DI — run it (the ASM to the sentinel, or
    the island in one step), and return the dest band bytes + the exit state
    (which must show every register preserved)."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        hooks.install(m)
    s = m.cpu.s
    s.sp = 0xFF00                                # high, clear of the 0x1A56 table
    src_seg, src_off = 0x7000, 0x0000
    dst_seg, dst_off = 0x7100, 0x0000
    for i, t in enumerate(tiles):
        m.mem.wb(src_seg, (src_off + i) & 0xFFFF, t)
    for row in range(4):
        for t in range(32):
            m.mem.ww(s.ss, (0x1A56 + row * 0x40 + t * 2) & 0xFFFF, table[row][t])
    # marker registers so the oracle also proves full preservation
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0x1111, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp = 0x2222, 0x3333, 0x4444
    s.cs, s.ip = m.seg_bases[hooks.MAKETABLE4X4_SEG_INDEX], hooks.MAKETABLE4X4_OFF
    sp = s.sp
    for v in (count, dst_seg, dst_off, src_seg, src_off, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(count * 20 + 300):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _Windows_MakeTable4x4 did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    stride = 2 * count
    dst_lin = m.mem._xlat(dst_seg, dst_off)
    band = bytes(m.mem.data[dst_lin:dst_lin + 4 * stride])
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return band, regs


@pytest.mark.parametrize("count", [1, 4, 16, 128])
def test_maketable4x4_island_matches_asm(count):
    tiles = [(i * 7 + 3) & 0x0F for i in range(count)]
    # distinct per-row words so a per-row-lookup bug cannot hide
    table = [[((row << 12) | (t << 4) | (t ^ row)) & 0xFFFF for t in range(32)]
             for row in range(4)]
    asm = _run_maketable(False, count, tiles, table)
    isl = _run_maketable(True, count, tiles, table)
    assert isl[0] == asm[0], f"count={count}: band bytes differ"
    assert isl[1] == asm[1], f"count={count}: exit state differs {isl[1]} != {asm[1]}"


# ---- _WindowsMono_MakeTable4x4a/b (seg4:442C/44B9) — recovered/render.py -----
# Packs tile PAIRS (0x40 for "a", 0x20 for "b") into four `pairs`-strided output
# scanlines, high nibble from the even tile / low nibble from the odd, each read
# from a per-tile 8-byte SS pattern table selected by (mode & 7) at SS:0x26A0.
def _run_monomake4x4(with_island, off, pairs, mode, tiles, table_bytes):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00                                # high, clear of the 0x26A0 table
    src_seg, src_off = 0x7000, 0x0000
    dst_seg, dst_off = 0x7100, 0x0000
    for i, t in enumerate(tiles):
        m.mem.wb(src_seg, (src_off + i) & 0xFFFF, t)
    for i, b in enumerate(table_bytes):
        m.mem.wb(s.ss, (hooks.MONOMAKE4X4_TABLE_BASE + i) & 0xFFFF, b)

    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0x1111, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp = 0x2222, 0x3333, 0x4444
    s.cs, s.ip = m.seg_bases[hooks.MONOMAKE4X4_SEG_INDEX], off
    sp = s.sp
    # stack (high->low): mode, unused, dst_seg, dst_off, src_seg, src_off, ret
    for v in (mode, 0x0000, dst_seg, dst_off, src_seg, src_off, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(400_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _WindowsMono_MakeTable4x4 did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    dst_lin = m.mem._xlat(dst_seg, dst_off)
    out = bytes(m.mem.data[dst_lin:dst_lin + 4 * pairs])
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("half", ["a", "b"])
@pytest.mark.parametrize("mode", [0, 3, 7])
def test_monomake4x4_island_matches_asm(half, mode):
    off = hooks.MONOMAKE4X4A_OFF if half == "a" else hooks.MONOMAKE4X4B_OFF
    pairs = hooks.MONOMAKE4X4A_PAIRS if half == "a" else hooks.MONOMAKE4X4B_PAIRS
    tiles = [(i * 7 + 3) & 0xFF for i in range(2 * pairs)]         # full 0..255 range
    table_bytes = bytes((i * 5 + 9) & 0xFF for i in range(0x820))  # covers 256*8 + phase
    asm = _run_monomake4x4(False, off, pairs, mode, tiles, table_bytes)
    isl = _run_monomake4x4(True, off, pairs, mode, tiles, table_bytes)
    assert isl[0] == asm[0], f"{half} mode={mode}: output bytes differ"
    assert isl[1] == asm[1], f"{half} mode={mode}: exit state differs {isl[1]} != {asm[1]}"


# ---- _WindowsMono_MakeTable2x2a/b (seg4:4542/45DB) — recovered/render.py -----
# Two scanlines, FOUR tiles per byte (2-bit slots 0xC0/0x30/0x0C/0x03); count
# 0x20 (a) / 0x10 (b); scanline stride == count.
def _run_monomake2x2(with_island, off, count, mode, tiles, table_bytes):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    src_seg, src_off = 0x7000, 0x0000
    dst_seg, dst_off = 0x7100, 0x0000
    for i, t in enumerate(tiles):
        m.mem.wb(src_seg, (src_off + i) & 0xFFFF, t)
    for i, b in enumerate(table_bytes):
        m.mem.wb(s.ss, (hooks.MONOMAKE4X4_TABLE_BASE + i) & 0xFFFF, b)
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0x1111, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp = 0x2222, 0x3333, 0x4444
    s.cs, s.ip = m.seg_bases[hooks.MONOMAKE4X4_SEG_INDEX], off
    sp = s.sp
    for v in (mode, 0x0000, dst_seg, dst_off, src_seg, src_off, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(400_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _WindowsMono_MakeTable2x2 did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    dst_lin = m.mem._xlat(dst_seg, dst_off)
    out = bytes(m.mem.data[dst_lin:dst_lin + 2 * count])
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("half", ["a", "b"])
@pytest.mark.parametrize("mode", [0, 3, 7])
def test_monomake2x2_island_matches_asm(half, mode):
    off = hooks.MONOMAKE2X2A_OFF if half == "a" else hooks.MONOMAKE2X2B_OFF
    count = hooks.MONOMAKE2X2A_COUNT if half == "a" else hooks.MONOMAKE2X2B_COUNT
    tiles = [(i * 7 + 3) & 0xFF for i in range(4 * count)]
    table_bytes = bytes((i * 5 + 9) & 0xFF for i in range(0x820))
    asm = _run_monomake2x2(False, off, count, mode, tiles, table_bytes)
    isl = _run_monomake2x2(True, off, count, mode, tiles, table_bytes)
    assert isl[0] == asm[0], f"{half} mode={mode}: output bytes differ"
    assert isl[1] == asm[1], f"{half} mode={mode}: exit state differs {isl[1]} != {asm[1]}"


# ---- _Windows_MakeTable1x1 (seg4:46BB) ---------------------------------------
def _run_maketable1x1(with_island, count, tiles, table):
    """Synthetic call — source tiles at DS:SI, the XLAT table at SS:0x1B56,
    dest at ES:DI.  Returns the count>>1 output bytes + the (preserved) exit
    state."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        hooks.install(m)
    s = m.cpu.s
    s.sp = 0xFF00
    src_seg, src_off = 0x7000, 0x0000
    dst_seg, dst_off = 0x7100, 0x0000
    for i, t in enumerate(tiles):
        m.mem.wb(src_seg, (src_off + i) & 0xFFFF, t)
    for i in range(0x110):
        m.mem.wb(s.ss, (0x1B56 + i) & 0xFFFF, table[i])
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0x1111, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp = 0x2222, 0x3333, 0x4444
    s.cs, s.ip = m.seg_bases[hooks.MAKETABLE1X1_SEG_INDEX], hooks.MAKETABLE1X1_OFF
    sp = s.sp
    for v in (count, dst_seg, dst_off, src_seg, src_off, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(count * 20 + 300):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _Windows_MakeTable1x1 did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    dst_lin = m.mem._xlat(dst_seg, dst_off)
    out = bytes(m.mem.data[dst_lin:dst_lin + count // 2])
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("count", [2, 5, 16, 127, 128])
def test_maketable1x1_island_matches_asm(count):
    tiles = [(i * 5 + 1) & 0x0F for i in range(count)]
    table = [(i * 37 + 11) & 0xFF for i in range(0x110)]   # arbitrary XLAT table
    asm = _run_maketable1x1(False, count, tiles, table)
    isl = _run_maketable1x1(True, count, tiles, table)
    assert isl[0] == asm[0], f"count={count}: packed bytes differ"
    assert isl[1] == asm[1], f"count={count}: exit state differs {isl[1]} != {asm[1]}"


# -- the SIMONE PRNG family ---------------------------------------------------
#
# These oracles are stricter than the earlier ones: the SRand routines' last
# flag-writing instruction is observable (callers branch on nothing here, but
# byte-exact means byte-exact), so the comparison includes FLAGS, the LFSR
# seed word, and the freed-frame stack residue ([sp-2] saved BP, [sp-4]
# result scratch) that C callers can read back through uninitialised locals.

def _setup_srand(m, off, seed, args=()):
    s = m.cpu.s
    s.cs = m.seg_bases[hooks.SRAND_SEG_INDEX]
    s.ip = off
    s.ds = m.seg_bases[hooks.DG_SEG_INDEX]
    s.bx, s.si, s.di, s.bp = MARK["bx"], MARK["si"], MARK["di"], MARK["bp"]
    s.cx, s.dx, s.ax = 0x5555, 0x6666, 0x7777
    s.flags = 0x0AD7 & 0x0FFF                   # a busy flag pattern to disturb
    m.mem.ww(s.ds, hooks.SRAND_SEED_OFF, seed)
    sp = s.sp
    for v in (*reversed(args), SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp


def _srand_state(m):
    s = m.cpu.s
    residue = tuple(m.mem.rw(s.ss, (s.sp - 6 - 2 * i) & 0xFFFF) for i in (0, 1))
    return dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, cs=s.cs, ip=s.ip, ds=s.ds, es=s.es,
                flags=s.flags,
                seed=m.mem.rw(s.ds, hooks.SRAND_SEED_OFF),
                residue=residue)


def _run_srand(with_island, off, seed, args=()):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    _setup_srand(m, off, seed, args)
    for _ in range(100):
        m.cpu.step()
        if (m.cpu.s.cs & 0xFFFF, m.cpu.s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
            return _srand_state(m)
    raise AssertionError(f"routine at {off:04X} did not return to the sentinel")


SEEDS = [0x0000, 0x0001, 0x8000, 0x8001, 0xFFFF, 0x1BF5, 0x4444, 0xACE1]


@pytest.mark.parametrize("off,name", hooks.SRAND_MASK_OFFS,
                         ids=[n for _, n in hooks.SRAND_MASK_OFFS])
@pytest.mark.parametrize("seed", SEEDS)
def test_srand_pow2_islands_match_asm(off, name, seed):
    asm = _run_srand(False, off, seed)
    isl = _run_srand(True, off, seed)
    assert isl == asm, f"{name} seed={seed:#06x}: {isl} != {asm}"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("n", [1, 2, 3, 7, 100, 255, 0x7FFF, 0xFFFF])
def test_srand1_island_matches_asm(seed, n):
    asm = _run_srand(False, hooks.SRAND1_OFF, seed, (n,))
    isl = _run_srand(True, hooks.SRAND1_OFF, seed, (n,))
    assert isl == asm, f"seed={seed:#06x} n={n}: {isl} != {asm}"


@pytest.mark.parametrize("seed", [0x0000, 0x1234, 0xFFFF])
def test_srand_seed_accessors_match_asm(seed):
    for off, args in ((hooks.SETSRANDSEED_OFF, (seed,)),
                      (hooks.GETSRANDSEED_OFF, ()),
                      (hooks.SETRRANDSEED_OFF, ())):
        asm = _run_srand(False, off, 0x4321, args)
        isl = _run_srand(True, off, 0x4321, args)
        assert isl == asm, f"off={off:04X}: {isl} != {asm}"


@pytest.mark.parametrize("ticks", [0, 0x00123456, 0xFFFFFFFF])
def test_getrrandseed_island_matches_asm(ticks):
    def run(with_island):
        m = runtime.create_machine()
        m.cpu.trace_enabled = False
        if with_island:
            assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
        m.mem.ww(hooks.BIOS_TICK_SEG, 0, ticks & 0xFFFF)
        m.mem.ww(hooks.BIOS_TICK_SEG, 2, ticks >> 16)
        _setup_srand(m, hooks.GETRRANDSEED_OFF, 0x4321)
        for _ in range(100):
            m.cpu.step()
            if (m.cpu.s.cs & 0xFFFF, m.cpu.s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                return _srand_state(m)
        raise AssertionError("did not return")
    asm, isl = run(False), run(True)
    assert isl == asm
    assert isl["ax"] | (isl["dx"] << 16) == ticks


# ---- _win_IsWinOpen (seg7:C256) — recovered/window.py ------------------------
# The high byte of an object handle is a window-table slot; the window is open
# iff g_window_hwnd[slot] (DGROUP:0xBCA6) holds a live HWND that IsWindowVisible
# reports visible.  Three cases exercise every path: visible, hidden, empty slot.
_ISWINOPEN_FLAGMASK = 0x08C5          # CF|PF|ZF|SF|OF (AF is undefined for or/xor)


def _run_iswinopen(with_island, obj_handle, slot_kind):
    """Fill the slot table so obj_handle's slot maps to a 'visible'/'hidden'
    window or an empty (0) slot, run _win_IsWinOpen, return the exit state."""
    from win16.api.objects import Window
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    sysobj = m.api.services["system"]
    DS = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DS

    slot = (obj_handle >> 8) & 0xFF
    hwnd = 0
    if slot_kind != "empty":
        w = Window(wndclass=None, title="", style=0, x=0, y=0, w=0, h=0,
                   parent=0, menu=0, visible=(slot_kind == "visible"))
        sysobj.handles.add(w)
        hwnd = w.handle
    m.mem.ww(DS, (hooks.ISWINOPEN_HWND_TABLE_OFF + slot * 2) & 0xFFFF, hwnd)

    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp = 0x2222, 0x3333, 0x4444
    s.cs, s.ip = m.seg_bases[hooks.ISWINOPEN_SEG_INDEX], hooks.ISWINOPEN_OFF
    sp = 0xFE00
    for v in (obj_handle, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(4000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _win_IsWinOpen did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    return dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di, bp=s.bp,
                sp=s.sp, ds=s.ds, es=s.es, flags=s.flags & _ISWINOPEN_FLAGMASK)


@pytest.mark.parametrize("obj_handle,slot_kind,expect", [
    (0x0500, "visible", 1),          # open + visible  -> 1
    (0x0500, "hidden", 0),           # mapped but hidden -> 0
    (0x0600, "empty", 0),            # slot holds no HWND -> 0
    (0x1300, "visible", 1),          # a different slot, to move the table pointer
])
def test_iswinopen_island_matches_asm(obj_handle, slot_kind, expect):
    asm = _run_iswinopen(False, obj_handle, slot_kind)
    isl = _run_iswinopen(True, obj_handle, slot_kind)
    assert asm["ax"] == expect, f"ASM oracle itself disagrees: {asm['ax']} != {expect}"
    assert isl == asm, f"island != ASM for {slot_kind} @ {obj_handle:#06x}: {isl} != {asm}"


# ---- _win_GetObjRect (seg7:C2D2) — recovered/window.py ----------------------
# Two-level far-pointer walk to an object's stored RECT, copied to *lpRect, with
# right/bottom bumped when the DGROUP:0xBD0A inclusive-rects flag is set.  We lay
# out synthetic winrec + RECT structures in DGROUP and A/B the island vs ASM.
def _run_getobjrect(with_island, obj_handle, rect, flag):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    WINREC, SRC, LPRECT = 0x7000, 0x7100, 0x7200        # scratch offsets in DGROUP
    slot, obj = (obj_handle >> 8) & 0xFF, obj_handle & 0xFF

    # Seed the DGROUP-side tables through the same bridge view the island reads.
    from simant.bridge.dgroup_view import SelectorBackend, SimAntState
    st = SimAntState(SelectorBackend(m.mem, DG))
    st.window_records[slot].off = WINREC        # far-ptr table[slot] -> winrec
    st.window_records[slot].seg = DG
    arr = (WINREC + hooks.GETOBJRECT_OBJARR_OFF + obj * 4) & 0xFFFF
    m.mem.ww(DG, arr, SRC);              m.mem.ww(DG, (arr + 2) & 0xFFFF, DG)
    for i, v in enumerate(rect):
        m.mem.ww(DG, (SRC + i * 2) & 0xFFFF, v & 0xFFFF)
    for i in range(4):
        m.mem.ww(DG, (LPRECT + i * 2) & 0xFFFF, 0xEEEE)   # poison the output
    st.obj_rect_inclusive = flag

    s.ds = DG
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.GETOBJRECT_SEG_INDEX], hooks.GETOBJRECT_OFF
    sp = 0xFC00
    for v in (DG, LPRECT, obj_handle, SENT_CS, SENT_IP):     # lpRect far, objHandle, ret
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(4000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _win_GetObjRect did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    out = [m.mem.rw(DG, (LPRECT + i * 2) & 0xFFFF) for i in range(4)]
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("obj_handle,rect,flag", [
    (0x0503, [10, 20, 30, 40], 0),      # plain copy
    (0x0503, [10, 20, 30, 40], 1),      # inclusive -> exclusive (right/bottom +1)
    (0x1307, [1, 2, 3, 4], 0),          # different slot + object index
    (0x0A00, [0xFFFF, 5, 0xFFFF, 7], 1),  # +1 wraps 0xFFFF -> 0 (16-bit)
])
def test_getobjrect_island_matches_asm(obj_handle, rect, flag):
    asm_out, asm_regs = _run_getobjrect(False, obj_handle, rect, flag)
    isl_out, isl_regs = _run_getobjrect(True, obj_handle, rect, flag)
    assert isl_out == asm_out, f"rect out differs: {isl_out} != {asm_out}"
    assert isl_regs == asm_regs, f"exit regs differ: {isl_regs} != {asm_regs}"


# ---- _GenNestMap (seg4:4754) — recovered/render.py --------------------------
# The 64x64 nest-map generator (hottest routine in the demo).  A pusha/popa
# frame restores every register, so the observable state is the 4096-byte output
# buffer plus four DGROUP globals.  We lay out two synthetic source maps + a
# lookup table in DGROUP and A/B the full output against the ASM.
def _run_gennestmap(with_island, terrain_bytes, alt_bytes, table_bytes, mode):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    TERR, ALT, TAB, OUT = 0x6000, 0x7000, 0x7800, 0x8000       # DGROUP scratch
    for i in range(64 * 64):
        m.mem.wb(DG, (TERR + i) & 0xFFFF, terrain_bytes.get(i, 0x40))   # default -> col_c
        m.mem.wb(DG, (ALT + i) & 0xFFFF, alt_bytes.get(i, 0))
    for i in range(256):
        m.mem.wb(DG, (TAB + i) & 0xFFFF, table_bytes.get(i, 0))
    for i in range(64 * 64):
        m.mem.wb(DG, (OUT + i) & 0xFFFF, 0x11)                 # poison the output
    COL_A, COL_B, COL_C = 0xAA, 0xBB, 0xCC

    s.ds, s.es = DG, 0x9999
    s.ax, s.bx, s.cx, s.dx = 0x0A0A, 0x0B0B, 0x0C0C, 0x0D0D    # markers (must survive)
    s.si, s.di, s.bp = 0x5151, 0x6161, 0x7171
    s.cs, s.ip = m.seg_bases[hooks.GENNESTMAP_SEG_INDEX], hooks.GENNESTMAP_OFF
    sp = 0xF000
    for v in (mode, COL_C, COL_B, COL_A, TAB, ALT, TERR, DG, OUT, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(600_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _GenNestMap did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    out = bytes(m.mem.rb(DG, (OUT + i) & 0xFFFF) for i in range(64 * 64))
    globs = (m.mem.rw(DG, 0x1B78), m.mem.rb(DG, 0x1B7A),
             m.mem.rb(DG, 0x1B7B), m.mem.rb(DG, 0x1B7C))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, globs, regs


@pytest.mark.parametrize("mode", [0, 1])
def test_gennestmap_island_matches_asm(mode):
    # a spread of terrain values exercising every classification branch, plus
    # empties that route to the table (mode 0) or are left as poison (mode 1)
    terrain = {i: v for i, v in {
        0: 0xFE, 65: 0xFF, 130: 0x80, 195: 0x05, 260: 0x00,
        400: 0xFD, 401: 0x7F, 402: 0x00, 1000: 0x00, 4095: 0x81,
    }.items()}
    alt = {260: 0x0C, 402: 0xFC, 1000: 0x40}      # >>2 -> 3, 0x3F, 0x10
    table = {i: (i * 3 + 1) & 0xFF for i in range(64)}
    asm = _run_gennestmap(False, terrain, alt, table, mode)
    isl = _run_gennestmap(True, terrain, alt, table, mode)
    assert isl[0] == asm[0], "output map differs"
    assert isl[1] == asm[1], f"globals differ: {isl[1]} != {asm[1]}"
    assert isl[2] == asm[2], f"registers not preserved: {isl[2]} != {asm[2]}"


# ---- _XferTileColor (seg4:47DD) — recovered/render.py -----------------------
# A huge-pointer 4bpp tile-colour blit: `height` rows of `tile_w//2` bytes from
# a source tile into a padded DIB (advancing one stride per row).  pusha/popa
# preserves every register, so the observable state is the destination bytes.
def _run_xfertilecolor(with_island, args, src_bytes, dst_span=0x400):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    SRC, DST = 0x6000, 0x8000
    for i in range(dst_span):
        m.mem.wb(DG, (DST + i) & 0xFFFF, 0x11)             # poison the destination
    for i, b in enumerate(src_bytes):
        m.mem.wb(DG, (SRC + i) & 0xFFFF, b)

    s.ds, s.es = DG, 0x9999
    s.ax, s.bx, s.cx, s.dx = 0x0A0A, 0x0B0B, 0x0C0C, 0x0D0D
    s.si, s.di, s.bp = 0x5151, 0x6161, 0x7171
    s.cs, s.ip = m.seg_bases[hooks.XFERTILECOLOR_SEG_INDEX], hooks.XFERTILECOLOR_OFF
    # dst far, dst_x, top, height, tile_w, y_extent, map_w, src_tile, src far
    dst_x, top, height, tile_w, y_extent, map_w, src_tile = args
    words = [DST, DG, dst_x, top, height, tile_w, y_extent, map_w, src_tile, SRC, DG]
    sp = 0xF000
    for v in reversed(words):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    for v in (SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(400_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _XferTileColor did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    out = bytes(m.mem.rb(DG, (DST + i) & 0xFFFF) for i in range(dst_span))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("args", [
    # (dst_x, top, height, tile_w, y_extent, map_w, src_tile)
    (0, 0, 2, 4, 1, 2, 0),         # 2x2-byte block at origin, stride 4
    (0, 0, 4, 8, 1, 4, 0),         # taller/wider, stride 8
    (2, 0, 2, 4, 1, 2, 1),         # dst_x offset + a non-zero source tile
    (0, 1, 3, 4, 4, 2, 0),         # a non-zero start band ((y_extent-top-1)=2)
])
def test_xfertilecolor_island_matches_asm(args):
    src = bytes((i * 7 + 3) & 0xFF for i in range(256))   # deterministic tile stream
    asm = _run_xfertilecolor(False, args, src)
    isl = _run_xfertilecolor(True, args, src)
    assert isl[0] == asm[0], "destination bytes differ"
    assert isl[1] == asm[1], f"registers not preserved: {isl[1]} != {asm[1]}"


# ---- _XferTileMono (seg4:486C) — recovered/render.py -----------------------
# The 1bpp sibling of _XferTileColor: same ABI + huge-pointer walk, but eight
# pixels per byte and a bottom-up band walk.  tile_w must be a multiple of 8.
def _run_xfertilemono(with_island, args, src_bytes, dst_span=0x400):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    SRC, DST = 0x6000, 0x8000
    for i in range(dst_span):
        m.mem.wb(DG, (DST + i) & 0xFFFF, 0x11)             # poison the destination
    for i, b in enumerate(src_bytes):
        m.mem.wb(DG, (SRC + i) & 0xFFFF, b)

    s.ds, s.es = DG, 0x9999
    s.ax, s.bx, s.cx, s.dx = 0x0A0A, 0x0B0B, 0x0C0C, 0x0D0D
    s.si, s.di, s.bp = 0x5151, 0x6161, 0x7171
    s.cs, s.ip = m.seg_bases[hooks.XFERTILEMONO_SEG_INDEX], hooks.XFERTILEMONO_OFF
    dst_x, top, height, tile_w, y_extent, map_w, src_tile = args
    words = [DST, DG, dst_x, top, height, tile_w, y_extent, map_w, src_tile, SRC, DG]
    sp = 0xF000
    for v in reversed(words):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    for v in (SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(400_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _XferTileMono did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    out = bytes(m.mem.rb(DG, (DST + i) & 0xFFFF) for i in range(dst_span))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("args", [
    # (dst_x, top, height, tile_w, y_extent, map_w, src_tile)
    (0, 0, 2, 8, 1, 4, 0),         # 2 rows, 1 byte/row, stride 4
    (0, 0, 4, 16, 1, 2, 0),        # 2 bytes/row, taller block
    (8, 0, 2, 8, 1, 4, 1),         # dst_x offset (a byte) + non-zero source tile
    (0, 1, 3, 8, 4, 4, 0),         # non-zero start band
])
def test_xfertilemono_island_matches_asm(args):
    src = bytes((i * 7 + 3) & 0xFF for i in range(256))
    asm = _run_xfertilemono(False, args, src)
    isl = _run_xfertilemono(True, args, src)
    assert isl[0] == asm[0], "destination bytes differ"
    assert isl[1] == asm[1], f"registers not preserved: {isl[1]} != {asm[1]}"


# ---- _XferLifeTileMono (seg4:49B7) — recovered/render.py -------------------
# The transparent 1bpp blit: a second source plane (the mask, at +0x3000 for
# tiles 0..127) selects, per bit, whether to keep the destination or draw the
# source.  It reads the destination, so the DST fill and the mask both matter.
def _run_xferlifetilemono(with_island, args, src_bytes, dst_fill, dst_span=0x400):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    SRC, DST = 0x0000, 0xC000                    # SRC low so the +0x3000 mask fits
    for i in range(dst_span):
        m.mem.wb(DG, (DST + i) & 0xFFFF, dst_fill[i % len(dst_fill)])
    for i, b in enumerate(src_bytes):
        m.mem.wb(DG, (SRC + i) & 0xFFFF, b)

    s.ds, s.es = DG, 0x9999
    s.ax, s.bx, s.cx, s.dx = 0x0A0A, 0x0B0B, 0x0C0C, 0x0D0D
    s.si, s.di, s.bp = 0x5151, 0x6161, 0x7171
    s.cs, s.ip = m.seg_bases[hooks.XFERLIFETILEMONO_SEG_INDEX], hooks.XFERLIFETILEMONO_OFF
    dst_x, top, height, tile_w, y_extent, map_w, src_tile = args
    words = [DST, DG, dst_x, top, height, tile_w, y_extent, map_w, src_tile, SRC, DG]
    sp = 0xF000
    for v in reversed(words):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    for v in (SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(400_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _XferLifeTileMono did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    out = bytes(m.mem.rb(DG, (DST + i) & 0xFFFF) for i in range(dst_span))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("args", [
    (0, 0, 2, 8, 1, 4, 0),
    (0, 0, 4, 16, 1, 2, 0),
    (8, 0, 2, 8, 1, 4, 1),
    (0, 1, 3, 8, 4, 4, 0),
])
def test_xferlifetilemono_island_matches_asm(args):
    # data plane + a varied mask plane at +0x3000 (mixed keep/draw bits)
    src = bytearray(0x3200)
    for i in range(len(src)):
        src[i] = (i * 7 + 3) & 0xFF
    for i in range(0x3000, len(src)):
        src[i] = (i * 13 + 5) & 0xFF            # mask plane: exercises transparency
    dst_fill = bytes([0xA5, 0x5A, 0xF0, 0x0F])
    asm = _run_xferlifetilemono(False, args, bytes(src), dst_fill)
    isl = _run_xferlifetilemono(True, args, bytes(src), dst_fill)
    assert isl[0] == asm[0], "destination bytes differ"
    assert isl[1] == asm[1], f"registers not preserved: {isl[1]} != {asm[1]}"


# ---- _XferLifeTileColor (seg4:48FA) — recovered/render.py -------------------
# Same geometry as _XferTileColor but a transparent blend: sentinel 0xDD skips
# the byte, a 0xD 4bpp pixel index shows the destination through.  It reads the
# destination, so the pre-existing dest content (not just poison) matters.
def _run_xferlifetilecolor(with_island, args, src_bytes, dst_fill, dst_span=0x400):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    SRC, DST = 0x6000, 0x8000
    for i in range(dst_span):
        m.mem.wb(DG, (DST + i) & 0xFFFF, dst_fill[i % len(dst_fill)])   # real dest content
    for i, b in enumerate(src_bytes):
        m.mem.wb(DG, (SRC + i) & 0xFFFF, b)

    s.ds, s.es = DG, 0x9999
    s.ax, s.bx, s.cx, s.dx = 0x0A0A, 0x0B0B, 0x0C0C, 0x0D0D
    s.si, s.di, s.bp = 0x5151, 0x6161, 0x7171
    s.cs, s.ip = m.seg_bases[hooks.XFERLIFETILECOLOR_SEG_INDEX], hooks.XFERLIFETILECOLOR_OFF
    dst_x, top, height, tile_w, y_extent, map_w, src_tile = args
    words = [DST, DG, dst_x, top, height, tile_w, y_extent, map_w, src_tile, SRC, DG]
    sp = 0xF000
    for v in reversed(words):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    for v in (SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(400_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _XferLifeTileColor did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    out = bytes(m.mem.rb(DG, (DST + i) & 0xFFFF) for i in range(dst_span))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, regs


@pytest.mark.parametrize("args", [
    (0, 0, 2, 4, 1, 2, 0),
    (0, 0, 4, 8, 1, 4, 1),
    (2, 0, 3, 4, 4, 2, 0),
])
def test_xferlifetilecolor_island_matches_asm(args):
    # source with a spread of transparency: opaque, low-transparent (0x?D),
    # high-transparent (0xD?), fully transparent (0xDD)
    src = bytes([0x12, 0x3D, 0xD4, 0xDD, 0x56, 0x7D, 0xD8, 0x9A] * 32)
    dst_fill = bytes([0xF0, 0x0F, 0xAB, 0xCD])         # non-trivial existing dest
    asm = _run_xferlifetilecolor(False, args, src, dst_fill)
    isl = _run_xferlifetilecolor(True, args, src, dst_fill)
    assert isl[0] == asm[0], "blended destination differs"
    assert isl[1] == asm[1], f"registers not preserved: {isl[1]} != {asm[1]}"


# ---- _DrawChar (seg7:B033) — recovered/render.py ----------------------------
# Sub-byte-shifted OR-composite 1bpp glyph blit.  It does NOT pusha, so it
# clobbers ax/bx/cx/dx (values the island reproduces) while preserving
# si/di/ds/es/bp; it writes three scratch globals and reads per-row strides plus
# a partial-mask table.  The mask table is read via `xlatb` with a CS: override,
# so it comes from the CODE segment (already loaded from the binary at seg7:B02A)
# — NOT the glyph source segment.  We deliberately use DGROUP for the glyph
# source (whose 0xB02A is unrelated data), so an island that read the mask from
# the source segment would diverge from the ASM here (regression: the garbled
# "registered to" screen in demo_195527).
def _run_drawchar(with_island, width, height, x, y, glyph, src_stride, dst_stride,
                  dst_fill, span=0x80):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    SRC, DST = 0x6000, 0x7000
    for i in range(span):
        m.mem.wb(DG, (DST + i) & 0xFFFF, dst_fill[i % len(dst_fill)])
    for i, b in enumerate(glyph):
        m.mem.wb(DG, (SRC + i) & 0xFFFF, b)
    # Seed the blit's cached strides through the same bridge view the island reads.
    from simant.bridge.dgroup_view import (SelectorBackend, DrawCharGlobals,
                                           DRAWCHAR_GLOBALS_BASE)
    _g = DrawCharGlobals(SelectorBackend(m.mem, DG), DRAWCHAR_GLOBALS_BASE)
    _g.src_stride = src_stride
    _g.dst_stride = dst_stride

    s.ax, s.bx, s.cx, s.dx = 0xA0A0, 0xB0B0, 0xC0C0, 0xD0D0
    s.si, s.di, s.bp, s.es, s.ds = 0x1234, 0x5678, 0x9ABC, 0xDEF0, DG
    s.cs, s.ip = m.seg_bases[hooks.DRAWCHAR_SEG_INDEX], hooks.DRAWCHAR_OFF
    words = [SRC, DG, DST, DG, width, height, x, y]
    sp = 0xF000
    for v in reversed(words):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    for v in (SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(300_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _DrawChar did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    out = bytes(m.mem.rb(DG, (DST + i) & 0xFFFF) for i in range(span))
    globs = (m.mem.rw(DG, 0xB90E), m.mem.rw(DG, 0xB910), m.mem.rw(DG, 0xB918))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return out, globs, regs


@pytest.mark.parametrize("width,height,x,y", [
    (16, 1, 0, 0),        # byte-aligned, two full words
    (16, 3, 0, 0),        # multi-row (exercises the per-row strides)
    (12, 1, 4, 0),        # sub-byte x-shift + a partial edge column (12 & 7 = 4)
    (11, 2, 3, 1),        # odd width + x and y sub-bits
    (8, 2, 5, 0),         # single byte per row, shifted
])
def test_drawchar_island_matches_asm(width, height, x, y):
    glyph = bytes((i * 37 + 11) & 0xFF for i in range(64))
    dst_fill = bytes([0x80, 0x01, 0x42, 0x24])
    asm = _run_drawchar(False, width, height, x, y, glyph, 4, 5, dst_fill)
    isl = _run_drawchar(True, width, height, x, y, glyph, 4, 5, dst_fill)
    assert isl[0] == asm[0], "composited destination differs"
    assert isl[1] == asm[1], f"scratch globals differ: {isl[1]} != {asm[1]}"
    assert isl[2] == asm[2], f"exit registers differ: {isl[2]} != {asm[2]}"


# ---- _DoCalcTile (seg4:4A6B) — recovered/render.py --------------------------
# The map-cell tile resolver (demo #3 hot, 4 view modes).  pusha/popa preserves
# every register; the observable state is two DGROUP globals (CE96 byte graphic,
# CE7A word attribute).  We lay out the per-mode graphic/attribute maps, overlay
# layers, and season/phase globals, then A/B the outputs against the ASM.
def _run_docalctile(with_island, mode, tile_x, tile_y, *, sub=5, attr=0x00,
                    layer_texel=0x00, cc84=4, cf54=0x10, cf50=0x20, ce92=0x30):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    LAYER = 0x2000
    W = lambda off, v: m.mem.ww(DG, off & 0xFFFF, v & 0xFFFF)
    B = lambda off, v: m.mem.wb(DG, off & 0xFFFF, v & 0xFF)
    W(hooks.DOCALCTILE_MODE_G, mode)
    W(hooks.DOCALCTILE_SUB_G, sub)
    W(0xCF54, cf54); W(0xCF50, cf50); W(0xCC84, cc84); W(0xCE92, ce92)
    for i in range(5):
        W(hooks.DOCALCTILE_LAYER_TABLE + i * 4, LAYER)
        W(hooks.DOCALCTILE_LAYER_TABLE + i * 4 + 2, DG)
    # place the graphic + attribute + layer bytes for this cell
    layout = {0: (0x7F, 0x28E8, 0x68E8), 1: (0x7F, 0x28E8, 0x68E8),
              2: (0x3F, 0x48E8, 0x88E8), 3: (0x3F, 0x58E8, 0x98E8)}.get(mode)
    if layout:
        xmask, gfx, att = layout
        cell = ((tile_x & xmask) << 6) + tile_y
        B(gfx + cell, 0x42)
        B(att + cell, attr)
        lidx = (((tile_x & xmask) >> 1) << 5) + (tile_y >> 1)
        B(LAYER + lidx, layer_texel)
    B(hooks.DOCALCTILE_CE96, 0xEE); W(hooks.DOCALCTILE_CE7A, 0xEEEE)   # poison

    s.ax, s.bx, s.cx, s.dx = 0x0A0A, 0x0B0B, 0x0C0C, 0x0D0D
    s.si, s.di, s.bp, s.es, s.ds = 0x1111, 0x2222, 0x3333, 0x4444, DG
    s.cs, s.ip = m.seg_bases[hooks.GENDOCALCTILE_SEG_INDEX], hooks.DOCALCTILE_OFF
    sp = 0xF000
    for v in (tile_y, tile_x, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(500_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _DoCalcTile did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    outs = (m.mem.rb(DG, hooks.DOCALCTILE_CE96), m.mem.rw(DG, hooks.DOCALCTILE_CE7A))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return outs, regs


@pytest.mark.parametrize("kw", [
    dict(mode=4, tile_x=3, tile_y=5),                          # mode>=4 -> (0,0)
    dict(mode=0, tile_x=2, tile_y=3, attr=0x00),               # base, clear attr
    dict(mode=1, tile_x=2, tile_y=3, attr=0x55),               # mode 1 == mode 0
    dict(mode=0, tile_x=6, tile_y=9, attr=0xFF, cc84=4),       # animated FF, phase < 8
    dict(mode=0, tile_x=6, tile_y=9, attr=0xFF, cc84=8),       # animated FF, phase >= 8
    dict(mode=0, tile_x=6, tile_y=9, attr=0xFE),               # animated FE
    dict(mode=0, tile_x=4, tile_y=8, sub=0, layer_texel=0x35), # overlay layer hit
    dict(mode=0, tile_x=4, tile_y=8, sub=0, layer_texel=0x08), # overlay miss (<=0x10)
    dict(mode=2, tile_x=5, tile_y=7, attr=0x22),               # alternate map pair B
    dict(mode=3, tile_x=5, tile_y=7, attr=0xFF, cc84=2),       # alternate map pair C
])
def test_docalctile_island_matches_asm(kw):
    asm = _run_docalctile(False, **kw)
    isl = _run_docalctile(True, **kw)
    assert isl[0] == asm[0], f"(CE96, CE7A) differ: {isl[0]} != {asm[0]}"
    assert isl[1] == asm[1], f"registers not preserved: {isl[1]} != {asm[1]}"


# ---- _FlipWord / _FlipLong / _XFlipLong (seg4) — recovered/byteops.py -------
def _run_flip(off, args, farptr=None):
    """Drive a flip helper (ASM to the sentinel, or the island in one step) and
    return (exit registers, and for XFlipLong the swapped dword in memory)."""
    def one(with_island):
        m = runtime.create_machine()
        m.cpu.trace_enabled = False
        if with_island:
            assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
        s = m.cpu.s
        s.sp = 0xFF00
        FP_SEG, FP_OFF = 0x7000, 0x0040
        if farptr is not None:
            m.mem.ww(FP_SEG, FP_OFF, farptr[0])
            m.mem.ww(FP_SEG, (FP_OFF + 2) & 0xFFFF, farptr[1])
        s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
        s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
        s.cs, s.ip = m.seg_bases[hooks.FLIP_SEG_INDEX], off
        sp = s.sp
        for v in (*reversed(args), SENT_CS, SENT_IP):
            sp = (sp - 2) & 0xFFFF
            m.mem.ww(s.ss, sp, v & 0xFFFF)
        s.sp = sp
        if with_island:
            m.cpu.step()
        else:
            for _ in range(200):
                m.cpu.step()
                if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                    break
            else:
                raise AssertionError("ASM flip helper did not return")
        assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
        regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                    bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
        mem = None
        if farptr is not None:
            mem = (m.mem.rw(FP_SEG, FP_OFF), m.mem.rw(FP_SEG, (FP_OFF + 2) & 0xFFFF))
        return regs, mem
    return one(False), one(True)


@pytest.mark.parametrize("w", [0x0000, 0x00FF, 0xFF00, 0x1234, 0xABCD, 0xFFFF])
def test_flipword_island_matches_asm(w):
    asm, isl = _run_flip(hooks.FLIPWORD_OFF, [w])
    assert isl == asm, f"w={w:#06x}: {isl} != {asm}"
    assert asm[0]["ax"] == ((w << 8) | (w >> 8)) & 0xFFFF


@pytest.mark.parametrize("lo,hi", [(0x0000, 0x0000), (0x1234, 0xABCD),
                                   (0xFF00, 0x00FF), (0xFFFF, 0xFFFF), (0x0102, 0x0304)])
def test_fliplong_island_matches_asm(lo, hi):
    asm, isl = _run_flip(hooks.FLIPLONG_OFF, [lo, hi])
    assert isl == asm, f"lo={lo:#06x} hi={hi:#06x}: {isl} != {asm}"
    fw = lambda x: ((x << 8) | (x >> 8)) & 0xFFFF
    assert (asm[0]["ax"], asm[0]["dx"]) == (fw(hi), fw(lo))


@pytest.mark.parametrize("w0,w1", [(0x1234, 0xABCD), (0x0000, 0xFFFF), (0xDEAD, 0xBEEF)])
def test_xfliplong_island_matches_asm(w0, w1):
    # far-ptr arg -> the dword (w0 low, w1 high); the routine swaps the two words.
    asm, isl = _run_flip(hooks.XFLIPLONG_OFF, [0x0040, 0x7000], farptr=(w0, w1))
    assert isl == asm, f"({w0:#06x},{w1:#06x}): {isl} != {asm}"
    assert asm[1] == (w1, w0), "words not swapped in place"


# ---- _exchange (seg4:6E05) — recovered/byteops.py --------------------------
def _run_exchange(with_island, buf1, buf2, count):
    """Swap `count` bytes between two buffers (ASM to sentinel, or island in one
    step) and return (buf1_after, buf2_after, exit registers)."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    B1_SEG, B1_OFF = 0x7000, 0x0010
    B2_SEG, B2_OFF = 0x7100, 0x0020
    for i, b in enumerate(buf1):
        m.mem.wb(B1_SEG, (B1_OFF + i) & 0xFFFF, b)
    for i, b in enumerate(buf2):
        m.mem.wb(B2_SEG, (B2_OFF + i) & 0xFFFF, b)
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.FLIP_SEG_INDEX], hooks.EXCHANGE_OFF
    sp = s.sp
    # stack: buf1 far (off,seg), buf2 far (off,seg), count, ret
    for v in (count, B2_SEG, B2_OFF, B1_SEG, B1_OFF, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(20000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _exchange did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    n = max(len(buf1), len(buf2))
    b1 = bytes(m.mem.rb(B1_SEG, (B1_OFF + i) & 0xFFFF) for i in range(n))
    b2 = bytes(m.mem.rb(B2_SEG, (B2_OFF + i) & 0xFFFF) for i in range(n))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return b1, b2, regs


@pytest.mark.parametrize("count", [1, 4, 8, 16])
def test_exchange_island_matches_asm(count):
    buf1 = bytes((i * 7 + 3) & 0xFF for i in range(16))
    buf2 = bytes((0xF0 - i * 5) & 0xFF for i in range(16))
    asm = _run_exchange(False, buf1, buf2, count)
    isl = _run_exchange(True, buf1, buf2, count)
    assert isl == asm, f"count={count}: {isl} != {asm}"
    # the first `count` bytes are swapped, the rest untouched
    assert asm[0] == buf2[:count] + buf1[count:]
    assert asm[1] == buf1[:count] + buf2[count:]


# ---- _CopyChar / _CopyCharRep (seg4:6C62 / 6CAA) — recovered/render.py -----
def _run_copychar(with_island, off, x, y, width, glyph, rep=None):
    """Blit a glyph into a fresh DIB (ASM to sentinel, or island in one step) and
    return (DIB pixel bytes, DGROUP stride global, exit registers)."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    SRC_SEG, SRC_OFF = 0x7000, 0x0010
    DST_SEG, DST_OFF = 0x7100, 0x0000
    stride = width >> 3
    DIB_BYTES = 4 + stride * 64                     # header + generous pixel span
    for i, b in enumerate(glyph):
        m.mem.wb(SRC_SEG, (SRC_OFF + i) & 0xFFFF, b)
    m.mem.ww(DST_SEG, DST_OFF, width)               # DIB header width word
    m.mem.ww(DST_SEG, (DST_OFF + 2) & 0xFFFF, 0)
    for i in range(DIB_BYTES):                       # clear the pixel area
        m.mem.wb(DST_SEG, (DST_OFF + 4 + i) & 0xFFFF, 0)
    m.mem.ww(DG, hooks.COPYCHAR_STRIDE_G, 0)         # poison the stride scratch
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    entry = hooks.COPYCHAR_OFF if rep is None else hooks.COPYCHARREP_OFF
    s.cs, s.ip = m.seg_bases[hooks.COPYCHAR_SEG_INDEX], entry
    sp = s.sp
    # stack (low->high): src far (off,seg), x, y, dst far (off,seg), [rep], ret
    args = [SRC_OFF, SRC_SEG, x, y, DST_OFF, DST_SEG]
    if rep is not None:
        args.append(rep)
    for v in (*reversed(args), SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(20000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _CopyChar did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    pixels = bytes(m.mem.rb(DST_SEG, (DST_OFF + 4 + i) & 0xFFFF) for i in range(DIB_BYTES - 4))
    gstride = m.mem.rw(DG, hooks.COPYCHAR_STRIDE_G)
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return pixels, gstride, regs


@pytest.mark.parametrize("x,y,width", [(0, 0, 320), (3, 5, 320), (17, 9, 640), (1, 0, 64)])
def test_copychar_island_matches_asm(x, y, width):
    glyph = bytes((0x80 | (i * 0x13)) & 0xFF for i in range(16))
    asm = _run_copychar(False, None, x, y, width, glyph)
    isl = _run_copychar(True, None, x, y, width, glyph)
    assert isl == asm, f"x={x} y={y} w={width}: differs"


@pytest.mark.parametrize("x,y,width,rep", [(0, 0, 320, 1), (4, 2, 320, 5),
                                           (2, 3, 640, 8), (0, 1, 64, 3)])
def test_copycharrep_island_matches_asm(x, y, width, rep):
    glyph = bytes((0x40 + i * 3) & 0xFF for i in range(16))
    asm = _run_copychar(False, None, x, y, width, glyph, rep=rep)
    isl = _run_copychar(True, None, x, y, width, glyph, rep=rep)
    assert isl == asm, f"x={x} y={y} w={width} rep={rep}: differs"


# ---- _MoveTextToBalloon (seg4:6CF8) — recovered/render.py ------------------
def _run_movetext(with_island, x, y, dst_width, src_w, src_h, pixels):
    """Blit a source bitmap struct into a fresh DIB (ASM to sentinel, or island
    in one step); return (DIB pixel bytes, DGROUP stride global, exit regs)."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    STRUCT_SEG, STRUCT_OFF = 0x7000, 0x0010          # {width, height, far* pixels}
    PIX_SEG, PIX_OFF = 0x7000, 0x0080
    DST_SEG, DST_OFF = 0x7100, 0x0000
    dst_stride = dst_width >> 3
    DIB_BYTES = 4 + dst_stride * 128
    # source struct + packed pixels
    m.mem.ww(STRUCT_SEG, STRUCT_OFF, src_w)
    m.mem.ww(STRUCT_SEG, (STRUCT_OFF + 2) & 0xFFFF, src_h)
    m.mem.ww(STRUCT_SEG, (STRUCT_OFF + 4) & 0xFFFF, PIX_OFF)
    m.mem.ww(STRUCT_SEG, (STRUCT_OFF + 6) & 0xFFFF, PIX_SEG)
    for i, b in enumerate(pixels):
        m.mem.wb(PIX_SEG, (PIX_OFF + i) & 0xFFFF, b)
    m.mem.ww(DST_SEG, DST_OFF, dst_width)
    m.mem.ww(DST_SEG, (DST_OFF + 2) & 0xFFFF, 0)
    for i in range(DIB_BYTES):
        m.mem.wb(DST_SEG, (DST_OFF + 4 + i) & 0xFFFF, 0)
    m.mem.ww(DG, hooks.COPYCHAR_STRIDE_G, 0)
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.COPYCHAR_SEG_INDEX], hooks.MOVETEXTTOBALLOON_OFF
    sp = s.sp
    # stack (low->high): src struct far, dst far, x, y, ret
    args = [STRUCT_OFF, STRUCT_SEG, DST_OFF, DST_SEG, x, y]
    for v in (*reversed(args), SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(60000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _MoveTextToBalloon did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    dib = bytes(m.mem.rb(DST_SEG, (DST_OFF + 4 + i) & 0xFFFF) for i in range(DIB_BYTES - 4))
    gstride = m.mem.rw(DG, hooks.COPYCHAR_STRIDE_G)
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return dib, gstride, regs


@pytest.mark.parametrize("x,y,dst_w,src_w,src_h", [
    (0, 0, 320, 16, 8), (5, 3, 320, 24, 12), (2, 7, 640, 40, 10), (0, 0, 128, 8, 16),
])
def test_movetexttoballoon_island_matches_asm(x, y, dst_w, src_w, src_h):
    src_stride = (src_w + 7) >> 3
    pixels = bytes((i * 11 + 5) & 0xFF for i in range(src_stride * src_h))
    asm = _run_movetext(False, x, y, dst_w, src_w, src_h, pixels)
    isl = _run_movetext(True, x, y, dst_w, src_w, src_h, pixels)
    assert isl == asm, f"x={x} y={y} dst_w={dst_w} src={src_w}x{src_h}: differs"


# ---- _os_ClipLine (seg4:6E24) — recovered/geometry.py ----------------------
_CLIP_RET = 0x6FFE          # a seg4 offset the routine never reaches -> our marker


def _run_clipline(with_island, x0, y0, x1, y1, bound_a, bound_b, cx_in=0x5555):
    """Drive the near-call line clipper (ASM to a return marker, or island in one
    step); return (si, di, dx, bx, cx, cf, swap_global, ax)."""
    from dos_re.cpu import CF
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    seg4 = m.seg_bases[hooks.CLIPLINE_SEG_INDEX]
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    m.mem.ww(DG, hooks.CLIPLINE_BOUND_A_G, bound_a & 0xFFFF)
    m.mem.ww(DG, hooks.CLIPLINE_BOUND_B_G, bound_b & 0xFFFF)
    m.mem.ww(DG, hooks.CLIPLINE_SWAP_G, 0xEEEE)          # poison the swap scratch
    s.sp = 0xFF00
    s.ax, s.bp = 0xA1A1, 0x3333
    s.si, s.di = x0 & 0xFFFF, y0 & 0xFFFF
    s.dx, s.bx = x1 & 0xFFFF, y1 & 0xFFFF
    s.cx = cx_in & 0xFFFF
    s.cs, s.ip = seg4, hooks.CLIPLINE_OFF
    sp = (s.sp - 2) & 0xFFFF                              # near call: push return offset
    m.mem.ww(s.ss, sp, _CLIP_RET)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(20000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (seg4, _CLIP_RET):
                break
        else:
            raise AssertionError("ASM _os_ClipLine did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (seg4, _CLIP_RET)
    return (s.si, s.di, s.dx, s.bx, s.cx, (s.flags & CF), m.mem.rw(DG, hooks.CLIPLINE_SWAP_G), s.ax)


# rect [0,200] x [0,150]; lines exercising every trivial/crossing case
@pytest.mark.parametrize("x0,y0,x1,y1", [
    (10, 10, 100, 100),        # fully inside -> accept unchanged
    (300, 10, 400, 20),        # fully right -> reject
    (10, 300, 20, 400),        # fully above -> reject
    (-50, 10, -10, 20),        # fully left -> reject
    (10, -50, 20, -10),        # fully below -> reject
    (-50, 75, 250, 75),        # horizontal, crosses left+right
    (100, -50, 100, 250),      # vertical, crosses below+above
    (-50, -50, 250, 250),      # diagonal across the whole rect
    (100, 75, 300, 200),       # inside -> out top-right
    (0, 0, 200, 150),          # corner to corner (on the boundary)
    (250, 200, 10, 10),        # out -> in (reversed direction)
    (-20, 80, 80, -20),        # crosses left+below
])
def test_clipline_island_matches_asm(x0, y0, x1, y1):
    asm = _run_clipline(False, x0, y0, x1, y1, 200, 150)
    isl = _run_clipline(True, x0, y0, x1, y1, 200, 150)
    assert isl == asm, f"({x0},{y0})-({x1},{y1}): island {isl} != asm {asm}"


def test_clipline_island_fuzz():
    import random
    rng = random.Random(0xA17)          # deterministic
    for _ in range(150):
        ba, bb = rng.randint(1, 300), rng.randint(1, 300)
        pts = [rng.randint(-80, 380) for _ in range(4)]
        asm = _run_clipline(False, *pts, ba, bb)
        isl = _run_clipline(True, *pts, ba, bb)
        assert isl == asm, f"pts={pts} bounds=({ba},{bb}): island {isl} != asm {asm}"


# ---- _IsItFood (seg6:2D1A) — recovered/gameplay.py -------------------------
def _run_isitfood(with_island, tile, inside):
    """Query the food predicate (ASM to sentinel, or island in one step) with the
    world-state inside/outside flag set; return (ax, dx, es, preserved regs)."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    # point the world-state selector at DGROUP itself and set the inside flag
    m.mem.ww(DG, hooks.ISITFOOD_WORLD_SEG_G, DG)
    m.mem.ww(DG, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISITFOOD_SEG_INDEX], hooks.ISITFOOD_OFF
    sp = s.sp
    for v in (tile, SENT_CS, SENT_IP):              # one word arg + far return
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(200):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _IsItFood did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    return dict(ax=s.ax, dx=s.dx, es=s.es, bx=s.bx, cx=s.cx,
                si=s.si, di=s.di, bp=s.bp, sp=s.sp, ds=s.ds)


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("tile", [0x00, 0x17, 0x18, 0x20, 0x27, 0x28, 0x47, 0x48,
                                  0x4A, 0x4B, 0x4C, 0xFF])
def test_isitfood_island_matches_asm(tile, inside):
    asm = _run_isitfood(False, tile, inside)
    isl = _run_isitfood(True, tile, inside)
    assert isl == asm, f"tile={tile:#04x} inside={inside}: island {isl} != asm {asm}"
    # sanity: the recovered range
    from simant.recovered.gameplay import is_it_food
    assert asm["ax"] == is_it_food(tile, inside)


def _run_isthisfood(with_island, plane, tile, inside):
    """_IsThisFood(plane, tile): plane<=1 tail-calls _IsItFood(tile), so seed the
    inside/outside flag; plane>1 is the yard nest-food band."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    m.mem.ww(DG, hooks.ISITFOOD_WORLD_SEG_G, DG)
    m.mem.ww(DG, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISTHISFOOD_SEG_INDEX], hooks.ISTHISFOOD_OFF
    sp = s.sp
    for v in (tile, plane, SENT_CS, SENT_IP):       # plane@[bp+6], tile@[bp+8]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(200):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _IsThisFood did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    return dict(ax=s.ax, dx=s.dx, es=s.es, bx=s.bx, cx=s.cx,
                si=s.si, di=s.di, bp=s.bp, sp=s.sp, ds=s.ds)


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("plane,tile", [
    (0, 0x18), (1, 0x27), (1, 0x48), (0, 0x00),       # nest planes -> _IsItFood
    (2, 0x0F), (2, 0x10), (2, 0x13), (2, 0x14), (3, 0x11),  # yard band 0x10..0x13
])
def test_isthisfood_island_matches_asm(plane, tile, inside):
    asm = _run_isthisfood(False, plane, tile, inside)
    isl = _run_isthisfood(True, plane, tile, inside)
    assert isl == asm, f"(p={plane},t={tile:#x},in={inside}): {isl} != {asm}"
    from simant.recovered.gameplay import is_this_food
    assert asm["ax"] == is_this_food(plane, tile, inside)


# ---- _IsYellowAnt (seg5:5720) — recovered/gameplay.py ----------------------
def _run_isyellowant(with_island, val):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISYELLOWANT_SEG_INDEX], hooks.ISYELLOWANT_OFF
    sp = s.sp
    for v in (val, SENT_CS, SENT_IP):              # one word arg + far return
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(200):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _IsYellowAnt did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    return dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)


@pytest.mark.parametrize("val", [0x00, 0x01, 0x7F, 0xFD, 0xFE, 0xFF, 0x1FE, 0x1FF])
def test_isyellowant_island_matches_asm(val):
    asm = _run_isyellowant(False, val)
    isl = _run_isyellowant(True, val)
    assert isl == asm, f"val={val:#06x}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import is_yellow_ant
    assert asm["ax"] == is_yellow_ant(val)


# ---- _IsItDirt (seg5:1182) — recovered/gameplay.py -------------------------
def _run_isitdirt(with_island, val):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISITDIRT_SEG_INDEX], hooks.ISITDIRT_OFF
    sp = s.sp
    for v in (val, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(200):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _IsItDirt did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    return dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)


@pytest.mark.parametrize("val", [0x00, 0x1F, 0x20, 0x28, 0x2E, 0x2F, 0x48, 0xFFFF])
def test_isitdirt_island_matches_asm(val):
    asm = _run_isitdirt(False, val)
    isl = _run_isitdirt(True, val)
    assert isl == asm, f"val={val:#06x}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import is_it_dirt
    sx = val - 0x10000 if val & 0x8000 else val
    assert asm["ax"] == is_it_dirt(sx)


# ---- _InNestBounds (seg5:115C) — recovered/gameplay.py ---------------------
def _run_innestbounds(with_island, x, y):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.INNESTBOUNDS_SEG_INDEX], hooks.INNESTBOUNDS_OFF
    sp = s.sp
    for v in (y, x, SENT_CS, SENT_IP):             # args: x@[bp+6], y@[bp+8]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(200):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _InNestBounds did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    return dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)


@pytest.mark.parametrize("x,y", [
    (0, 1), (0x3F, 0x3F), (0x20, 0x20),            # in bounds
    (0, 0), (0x3F, 0), (-1, 0x20), (0x40, 0x20),   # x/y edge failures
    (0x20, 0x40), (0x20, -5), (0xFFFF, 0x20),      # out / negative-as-16bit
])
def test_innestbounds_island_matches_asm(x, y):
    asm = _run_innestbounds(False, x, y)
    isl = _run_innestbounds(True, x, y)
    assert isl == asm, f"({x},{y}): island {isl} != asm {asm}"


# ---- tile-classification predicate family (seg5) — recovered/gameplay.py ----
# _RIsItDirt / _IsItNFood / _IsThisEgg are single-word-arg predicates;
# _IsThisGrass / _IsThisPebble take (plane, tile).  All are pure and preserve
# every register but ax (result) and dx (the loaded arg residue).
def _pred_regs(s):
    return dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)


def _step_to_return(m, s, with_island, name, max_steps=200):
    if with_island:
        m.cpu.step()
    else:
        for _ in range(max_steps):                    # bigger for routines that
            m.cpu.step()                              # call sub-routines in a loop
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError(f"ASM {name} did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)


def _run_predicate(seg_index, off, name, with_island, args):
    """Drive a pure seg5 predicate.  `args` are the C args left-to-right (so
    arg0 lands at [bp+6]); they are pushed in reverse before the far-return."""
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[seg_index], off
    sp = s.sp
    for v in (*reversed(args), SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, name)
    return _pred_regs(s)


@pytest.mark.parametrize("val", [0x00, 0x1F, 0x20, 0x2F, 0x30, 0x4E, 0x4F, 0x60,
                                 0xFFFF])
def test_risitdirt_island_matches_asm(val):
    asm = _run_predicate(hooks.RISITDIRT_SEG_INDEX, hooks.RISITDIRT_OFF,
                         "_RIsItDirt", False, (val,))
    isl = _run_predicate(hooks.RISITDIRT_SEG_INDEX, hooks.RISITDIRT_OFF,
                         "_RIsItDirt", True, (val,))
    assert isl == asm, f"val={val:#06x}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import r_is_it_dirt
    sx = val - 0x10000 if val & 0x8000 else val
    assert asm["ax"] == r_is_it_dirt(sx)


@pytest.mark.parametrize("val", [0x00, 0x0F, 0x10, 0x12, 0x13, 0x14, 0xFFFF])
def test_isitnfood_island_matches_asm(val):
    asm = _run_predicate(hooks.ISITNFOOD_SEG_INDEX, hooks.ISITNFOOD_OFF,
                         "_IsItNFood", False, (val,))
    isl = _run_predicate(hooks.ISITNFOOD_SEG_INDEX, hooks.ISITNFOOD_OFF,
                         "_IsItNFood", True, (val,))
    assert isl == asm, f"val={val:#06x}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import is_it_nfood
    sx = val - 0x10000 if val & 0x8000 else val
    assert asm["ax"] == is_it_nfood(sx)


@pytest.mark.parametrize("val", [0x00, 0x01, 0x07, 0x08, 0x80, 0x81, 0x87, 0x88,
                                 0xFF00, 0xFF05])
def test_isthisegg_island_matches_asm(val):
    asm = _run_predicate(hooks.ISTHISEGG_SEG_INDEX, hooks.ISTHISEGG_OFF,
                         "_IsThisEgg", False, (val,))
    isl = _run_predicate(hooks.ISTHISEGG_SEG_INDEX, hooks.ISTHISEGG_OFF,
                         "_IsThisEgg", True, (val,))
    assert isl == asm, f"val={val:#06x}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import is_this_egg
    assert asm["ax"] == is_this_egg(val & 0xFF)


@pytest.mark.parametrize("plane,tile", [
    (0, 0x1D), (1, 0x1D), (2, 0x1B), (2, 0x1C), (2, 0x1F), (2, 0x20),
    (3, 0x1D), (-1, 0x1D),
])
def test_isthisgrass_island_matches_asm(plane, tile):
    asm = _run_predicate(hooks.ISTHISGRASS_SEG_INDEX, hooks.ISTHISGRASS_OFF,
                         "_IsThisGrass", False, (plane, tile))
    isl = _run_predicate(hooks.ISTHISGRASS_SEG_INDEX, hooks.ISTHISGRASS_OFF,
                         "_IsThisGrass", True, (plane, tile))
    assert isl == asm, f"({plane},{tile:#x}): island {isl} != asm {asm}"


@pytest.mark.parametrize("plane,tile", [
    (0, 0x30), (0, 0x52), (1, 0x50), (1, 0x51), (1, 0x53), (1, 0x54),
    (2, 0x2F), (2, 0x30), (2, 0x31), (2, 0x32), (-1, 0x52),
])
def test_isthispebble_island_matches_asm(plane, tile):
    asm = _run_predicate(hooks.ISTHISPEBBLE_SEG_INDEX, hooks.ISTHISPEBBLE_OFF,
                         "_IsThisPebble", False, (plane, tile))
    isl = _run_predicate(hooks.ISTHISPEBBLE_SEG_INDEX, hooks.ISTHISPEBBLE_OFF,
                         "_IsThisPebble", True, (plane, tile))
    assert isl == asm, f"({plane},{tile:#x}): island {isl} != asm {asm}"


@pytest.mark.parametrize("x,y", [
    (0, 0), (0x7F, 0x3F), (0x40, 0x20), (-1, 0x20), (0x80, 0x20),
    (0x20, -1), (0x20, 0x40), (0x7F, 0x40), (0xFFFF, 0x10),
])
def test_isvalida_island_matches_asm(x, y):
    asm = _run_predicate(hooks.ISVALIDA_SEG_INDEX, hooks.ISVALIDA_OFF,
                         "_IsValidA", False, (x, y))
    isl = _run_predicate(hooks.ISVALIDA_SEG_INDEX, hooks.ISVALIDA_OFF,
                         "_IsValidA", True, (x, y))
    assert isl == asm, f"({x},{y}): island {isl} != asm {asm}"


@pytest.mark.parametrize("x,y", [
    (0, 0), (0x3F, 0x3F), (0x20, 0x20), (-1, 0x20), (0x40, 0x20),
    (0x20, -1), (0x20, 0x40), (0x80, 0x10), (0x10, 0xFFFF),
])
def test_isvalidb_island_matches_asm(x, y):
    asm = _run_predicate(hooks.ISVALIDB_SEG_INDEX, hooks.ISVALIDB_OFF,
                         "_IsValidB", False, (x, y))
    isl = _run_predicate(hooks.ISVALIDB_SEG_INDEX, hooks.ISVALIDB_OFF,
                         "_IsValidB", True, (x, y))
    assert isl == asm, f"({x},{y}): island {isl} != asm {asm}"


# ---- world-state predicates: _IsLessThanHole / _IsSamePlane (seg5) ----------
# These read DGROUP globals, so the harness sets ds=DGROUP and seeds the
# world-state words (mirroring the _IsItFood seam).
def _run_islessthanhole(with_island, tile, inside):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    m.mem.ww(DG, hooks.ISLESSTHANHOLE_WORLD_SEG_G, DG)       # world selector -> DGROUP
    m.mem.ww(DG, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISLESSTHANHOLE_SEG_INDEX], hooks.ISLESSTHANHOLE_OFF
    sp = s.sp
    for v in (tile, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsLessThanHole")
    return _pred_regs(s)


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("tile", [0x00, 0x4F, 0x50, 0x51, 0x58, 0x59, 0x5A,
                                  0xFFFF])
def test_islessthanhole_island_matches_asm(tile, inside):
    asm = _run_islessthanhole(False, tile, inside)
    isl = _run_islessthanhole(True, tile, inside)
    assert isl == asm, f"tile={tile:#06x} inside={inside}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import is_less_than_hole
    sx = tile - 0x10000 if tile & 0x8000 else tile
    assert asm["ax"] == is_less_than_hole(sx, inside)


def _run_isnotbarrier(with_island, tile, inside):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    m.mem.ww(DG, hooks.ISLESSTHANHOLE_WORLD_SEG_G, DG)
    m.mem.ww(DG, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISNOTBARRIER_SEG_INDEX], hooks.ISNOTBARRIER_OFF
    sp = s.sp
    for v in (tile, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsNotBarrier")
    return _pred_regs(s)


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("tile", [0x00, 0x4F, 0x50, 0x51, 0x5E, 0x5F, 0x60,
                                  0xFFFF])
def test_isnotbarrier_island_matches_asm(tile, inside):
    asm = _run_isnotbarrier(False, tile, inside)
    isl = _run_isnotbarrier(True, tile, inside)
    assert isl == asm, f"tile={tile:#06x} inside={inside}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import is_not_barrier
    sx = tile - 0x10000 if tile & 0x8000 else tile
    assert asm["ax"] == is_not_barrier(sx, inside)


@pytest.mark.parametrize("x1,y1,x2,y2,d", [
    (5, 5, 5, 5, 0), (5, 5, 5, 3, 1), (5, 5, 5, 8, 5), (5, 5, 8, 3, 2),
    (5, 5, 8, 5, 3), (5, 5, 8, 8, 4), (5, 5, 2, 8, 6), (5, 5, 2, 5, 7),
    (5, 5, 2, 2, 8), (0, 0, 0, 0, 0), (-3, -3, -1, -5, 2),
])
def test_getdir_island_matches_asm(x1, y1, x2, y2, d):
    asm = _run_predicate(hooks.GETDIR_SEG_INDEX, hooks.GETDIR_OFF,
                         "_GetDir", False, (x1, y1, x2, y2))
    isl = _run_predicate(hooks.GETDIR_SEG_INDEX, hooks.GETDIR_OFF,
                         "_GetDir", True, (x1, y1, x2, y2))
    assert isl == asm, f"({x1},{y1})->({x2},{y2}): island {isl} != asm {asm}"
    from simant.recovered.gameplay import get_dir
    assert asm["ax"] == d == get_dir(x1, y1, x2, y2)


@pytest.mark.parametrize("x1,y1,x2,y2,d", [
    (5, 5, 5, 5, 0), (0, 0, 3, 4, 7), (10, 10, 7, 6, 7), (0, 0, 100, 0, 100),
    (-5, -5, 5, 5, 20), (0x7F, 0, 0, 0x3F, 0x7F + 0x3F), (3, 8, 3, 2, 6),
])
def test_sgetdis_island_matches_asm(x1, y1, x2, y2, d):
    asm = _run_predicate(hooks.SGETDIS_SEG_INDEX, hooks.SGETDIS_OFF,
                         "_SGetDis", False, (x1, y1, x2, y2))
    isl = _run_predicate(hooks.SGETDIS_SEG_INDEX, hooks.SGETDIS_OFF,
                         "_SGetDis", True, (x1, y1, x2, y2))
    assert isl == asm, f"({x1},{y1})->({x2},{y2}): island {isl} != asm {asm}"
    from simant.recovered.gameplay import s_get_dis
    assert asm["ax"] == d == s_get_dis(x1, y1, x2, y2)


@pytest.mark.parametrize("x1,y1,x2,y2,dist", [
    (0, 0, 3, 4, 25), (5, 5, 5, 5, 0), (5, 5, 8, 9, 25), (10, 10, 7, 6, 25),
    (0, 0, 100, 0, 10000), (0, 0, 0, 120, 14400), (-5, -5, 5, 5, 200),
    (0x7F, 0, 0, 0x3F, 0x7F * 0x7F + 0x3F * 0x3F),
])
def test_getdis_island_matches_asm(x1, y1, x2, y2, dist):
    asm = _run_predicate(hooks.GETDIS_SEG_INDEX, hooks.GETDIS_OFF,
                         "_GetDis", False, (x1, y1, x2, y2))
    isl = _run_predicate(hooks.GETDIS_SEG_INDEX, hooks.GETDIS_OFF,
                         "_GetDis", True, (x1, y1, x2, y2))
    # bx/cx hold the long-multiply helper's internal scratch (no caller reads
    # them; the contract is DX:AX + preserved SI/DI/BP/DS/ES) — as with __aFuldiv.
    contract = lambda r: {k: v for k, v in r.items() if k not in ("bx", "cx")}
    assert contract(isl) == contract(asm), f"({x1},{y1})->({x2},{y2}): {isl} != {asm}"
    got = asm["ax"] | (asm["dx"] << 16)               # long result in DX:AX
    from simant.recovered.gameplay import get_dis
    assert got == dist == get_dis(x1, y1, x2, y2)


# ---- __aFldiv (seg4:08D4) — signed 32-bit long division, no island ---------
# recovered/crt_math.py: not profiled hot (unlike __aFuldiv), recovered as
# plain composable source for the dig-subsystem routines that need it, not
# as a hooks.py performance island. Verified straight against the ASM (no
# with_island leg -- there's nothing installed to compare against).
def _split32(v):
    v &= 0xFFFFFFFF
    return v & 0xFFFF, (v >> 16) & 0xFFFF          # (lo, hi)


@pytest.mark.parametrize("dividend,divisor", [
    (100, 3), (-100, 3), (100, -3), (-100, -3),
    (0, 5), (7, 1), (-7, 1), (1, 7), (-1, 7),
    (0x12345678, 0x1000), (-0x12345678, 0x1000),           # divisor fits 16 bits
    (0x12345678, 0x123), (0x7FFFFFFF, 0x10001),             # divisor > 16 bits
    (-0x80000000, 1), (-0x80000000, -1),                    # INT_MIN edge cases
    (0x7FFFFFFF, 2), (-0x80000000, 0x7FFFFFFF),
])
def test_afldiv_matches_asm(dividend, divisor):
    from simant.recovered.crt_math import a_f_ldiv
    dividend_lo, dividend_hi = _split32(dividend)
    divisor_lo, divisor_hi = _split32(divisor)
    asm = _run_predicate(hooks.RT_SEG_INDEX, 0x08D4, "__aFldiv", False,
                         (dividend_lo, dividend_hi, divisor_lo, divisor_hi))
    got = asm["ax"] | (asm["dx"] << 16)
    expect = a_f_ldiv(dividend, divisor)
    assert got == expect, (
        f"dividend={dividend:#x} divisor={divisor:#x}: asm={got:#010x} "
        f"rec={expect:#010x}")


def _run_issameplane(with_island, plane, current):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    m.mem.ww(DG, hooks.ISSAMEPLANE_PLANE_G, current & 0xFFFF)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISSAMEPLANE_SEG_INDEX], hooks.ISSAMEPLANE_OFF
    sp = s.sp
    for v in (plane, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsSamePlane")
    return _pred_regs(s)


@pytest.mark.parametrize("plane,current", [
    (0, 1), (0, 0), (0, 2), (1, 1), (1, 2), (2, 2), (3, 3), (2, 3),
])
def test_issameplane_island_matches_asm(plane, current):
    asm = _run_issameplane(False, plane, current)
    isl = _run_issameplane(True, plane, current)
    assert isl == asm, f"plane={plane} current={current}: island {isl} != asm {asm}"
    from simant.recovered.gameplay import is_same_plane
    assert asm["ax"] == is_same_plane(plane, current)


# ---- _IsItHole (seg6:2CC0) — map + world-state hole predicate ---------------
# Seeds both the inside/outside flag and the plane-0 yard tile.
def _run_isithole(with_island, x, y, inside, tile):
    from simant.recovered.gameplay import map_cell_offset
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    m.mem.ww(DG, hooks.ISITFOOD_WORLD_SEG_G, DG)
    m.mem.ww(DG, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    off = map_cell_offset(0, x, y)
    if off is not None:
        m.mem.wb(DG, off & 0xFFFF, tile)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISITHOLE_SEG_INDEX], hooks.ISITHOLE_OFF
    sp = s.sp
    for v in (y, x, SENT_CS, SENT_IP):               # x@[bp+6], y@[bp+8]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsItHole")
    return _pred_regs(s)


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("x,y,tile", [
    (0x10, 0x10, 0x50),      # outside hole / inside not-hole
    (0x10, 0x10, 0x80),      # inside hole / outside not-hole
    (0x10, 0x10, 0x8F),      # inside upper edge
    (0x10, 0x10, 0x7F),      # below inside range
    (0x10, 0x10, 0x90),      # above inside range
    (0x00, 0x00, 0x50), (0x7F, 0x3F, 0x80),
    (0x80, 0x10, 0x50),      # x out of range (dx = x residue)
    (0x10, 0x40, 0x50),      # y out of range (dx = y residue)
])
def test_isithole_island_matches_asm(x, y, tile, inside):
    asm = _run_isithole(False, x, y, inside, tile)
    isl = _run_isithole(True, x, y, inside, tile)
    assert isl == asm, f"({x:#x},{y:#x},t={tile:#x},in={inside}): {isl} != {asm}"


# ---- _GetLife (seg5:6040) — life-grid accessor (empty cell -> 0xFFFF) --------
def _run_getlife(with_island, plane, x, y, seed):
    from simant.recovered.gameplay import life_cell_offset
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    off = life_cell_offset(plane, x, y)
    if off is not None:
        m.mem.wb(DG, off & 0xFFFF, seed)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.GETLIFE_SEG_INDEX], hooks.GETLIFE_OFF
    sp = s.sp
    for v in (y, x, plane, SENT_CS, SENT_IP):        # plane@[bp+6], x@[bp+8], y@[bp+0xa]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_GetLife")
    return _pred_regs(s)


@pytest.mark.parametrize("plane,x,y,seed", [
    (0, 0x10, 0x20, 0x42), (1, 0x7F, 0x3F, 0x01), (2, 0, 0, 0xFE),
    (3, 0x20, 0x10, 0x80),
    (0, 0x10, 0x20, 0x00),                            # empty cell -> 0xFFFF
    (2, 0x3F, 0x3F, 0x00),                            # empty nest cell
    (0, 0x80, 0x00, 0x42), (2, 0x40, 0, 0x42),        # coord-invalid
    (4, 0x10, 0x10, 0x42), (-1, 0x10, 0x10, 0x42),    # plane-invalid
])
def test_getlife_island_matches_asm(plane, x, y, seed):
    asm = _run_getlife(False, plane, x, y, seed)
    isl = _run_getlife(True, plane, x, y, seed)
    assert isl == asm, f"(p={plane},{x:#x},{y:#x},s={seed:#x}): {isl} != {asm}"
    from simant.recovered.gameplay import life_cell_offset, get_life_value
    expect = get_life_value(seed) if life_cell_offset(plane, x, y) is not None else 0xFFFF
    assert asm["ax"] == expect


# ---- _IsNotObstacle (seg5:94C6) — map + world-flag movement predicate --------
# Reads the plane map at DGROUP and the inside flag at the hardcoded world
# selector 0x5EF3 (the value DGROUP:[0xC320] holds), so seed both.
def _run_isnotobstacle(with_island, plane, x, y, inside, tile):
    from simant.recovered.gameplay import map_cell_offset
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    world = m.mem.rw(DG, hooks.ISITFOOD_WORLD_SEG_G)          # == 0x5EF3
    m.mem.ww(world, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    off = map_cell_offset(plane, x, y)
    if off is not None:
        m.mem.wb(DG, off & 0xFFFF, tile)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISNOTOBSTACLE_SEG_INDEX], hooks.ISNOTOBSTACLE_OFF
    sp = s.sp
    for v in (y, x, plane, SENT_CS, SENT_IP):        # plane@[bp+6], x@[bp+8], y@[bp+0xa]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsNotObstacle")
    return _pred_regs(s)


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("plane,x,y,tile", [
    (0, 0x10, 0x10, 0x53), (0, 0x10, 0x10, 0x5F), (1, 0x10, 0x10, 0x54),
    (1, 0x10, 0x10, 0x60), (0, 0x00, 0x00, 0x00),        # nest planes
    (2, 0x10, 0x10, 0x18), (2, 0x10, 0x10, 0x19),         # yard: <=0x18 clear / obstacle
    (2, 0x10, 0x10, 0x30), (3, 0x10, 0x10, 0x31),         # yard pebble
    (2, 0x10, 0x10, 0x40),                                 # yard obstacle
    (0, 0x80, 0x10, 0x00), (2, 0x40, 0x10, 0x00),         # coord-invalid
    (4, 0x10, 0x10, 0x00),                                 # plane-invalid
])
def test_isnotobstacle_island_matches_asm(plane, x, y, tile, inside):
    asm = _run_isnotobstacle(False, plane, x, y, inside, tile)
    isl = _run_isnotobstacle(True, plane, x, y, inside, tile)
    assert isl == asm, f"(p={plane},{x:#x},{y:#x},t={tile:#x},in={inside}): {isl} != {asm}"


@pytest.mark.parametrize("plane,x,y", [
    (0, 0x10, 0x10), (0, 0x7F, 0x3F), (0, 0x80, 0x10), (1, 0x10, 0x40),
    (2, 0x3F, 0x3F), (2, 0x40, 0x10), (3, 0x10, 0x10), (0, -1, 0x10),
])
def test_isvalidlocation_island_matches_asm(plane, x, y):
    asm = _run_predicate(hooks.ISVALIDLOC_SEG_INDEX, hooks.ISVALIDLOC_OFF,
                         "_IsValidLocation", False, (plane, x, y))
    isl = _run_predicate(hooks.ISVALIDLOC_SEG_INDEX, hooks.ISVALIDLOC_OFF,
                         "_IsValidLocation", True, (plane, x, y))
    assert isl == asm, f"(p={plane},{x},{y}): {isl} != {asm}"


def _run_isitdigable(with_island, plane, x, y, tile):
    from simant.recovered.gameplay import map_cell_offset
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    off = map_cell_offset(plane, x, y)
    if off is not None:
        m.mem.wb(DG, off & 0xFFFF, tile)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISITDIGABLE_SEG_INDEX], hooks.ISITDIGABLE_OFF
    sp = s.sp
    for v in (y, x, plane, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsItDigable")
    return _pred_regs(s)


@pytest.mark.parametrize("plane,x,y,tile", [
    (0, 0x10, 0x10, 0x20), (1, 0x10, 0x10, 0x1C),           # plane<2 -> 0
    (2, 0x10, 0x10, 0x20), (2, 0x10, 0x10, 0x2E),           # dirt
    (2, 0x10, 0x10, 0x1C), (3, 0x10, 0x10, 0x1F),           # grass
    (2, 0x10, 0x10, 0x1B), (2, 0x10, 0x10, 0x40),           # neither
    (2, 0x40, 0x10, 0x00), (2, 0x10, 0x40, 0x00),           # coord-invalid
    (4, 0x10, 0x10, 0x00), (5, 0x10, 0x10, 0x00),           # plane>3 (coords ok)
])
def test_isitdigable_island_matches_asm(plane, x, y, tile):
    asm = _run_isitdigable(False, plane, x, y, tile)
    isl = _run_isitdigable(True, plane, x, y, tile)
    assert isl == asm, f"(p={plane},{x:#x},{y:#x},t={tile:#x}): {isl} != {asm}"
    from simant.recovered.gameplay import is_it_digable
    if plane >= 2 and map_cell_offset_ok(plane, x, y):
        assert asm["ax"] == is_it_digable(plane, tile)


def map_cell_offset_ok(plane, x, y):
    from simant.recovered.gameplay import map_cell_offset
    return map_cell_offset(plane, x, y) is not None


# ---- _IsItAHole (seg5:9B4A) — hole on any plane (delegates _IsItHole) --------
def _run_isitahole(with_island, plane, x, y, inside, tile):
    from simant.recovered.gameplay import map_cell_offset
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    world = m.mem.rw(DG, hooks.ISITFOOD_WORLD_SEG_G)
    m.mem.ww(world, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    # plane<=1 reads the plane-0 map; plane>1 reads its own plane
    seed_plane = 0 if plane <= 1 else plane
    off = map_cell_offset(seed_plane, x, y)
    if off is not None:
        m.mem.wb(DG, off & 0xFFFF, tile)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISITAHOLE_SEG_INDEX], hooks.ISITAHOLE_OFF
    sp = s.sp
    for v in (y, x, plane, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsItAHole")
    return _pred_regs(s)


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("plane,x,y,tile", [
    (0, 0x10, 0x10, 0x80), (0, 0x10, 0x10, 0x50), (1, 0x10, 0x10, 0x00),
    (0, 0x80, 0x10, 0x00),                                  # nest: invalid coord
    (2, 0x10, 0x00, 0x18), (3, 0x10, 0x00, 0x18),           # yard hole (top row)
    (2, 0x10, 0x00, 0x19),                                  # yard non-hole
    (2, 0x10, 0x10, 0x18),                                  # yard y>0 -> 0
    (2, 0x10, -1, 0x18),                                    # yard y<0 invalid
    (2, 0x40, 0x00, 0x18),                                  # yard x invalid
    (4, 0x10, 0x00, 0x18),                                  # plane>3
])
def test_isitahole_island_matches_asm(plane, x, y, tile, inside):
    asm = _run_isitahole(False, plane, x, y, inside, tile)
    isl = _run_isitahole(True, plane, x, y, inside, tile)
    assert isl == asm, f"(p={plane},{x:#x},{y},t={tile:#x},in={inside}): {isl} != {asm}"


# ---- _GetBestDir (seg6:405E) — ant pathfinding, verified by RETURN VALUE ------
# A behaviour routine (7 interleaved sub-calls per iteration): reconstructed as
# source and checked against the ASM's return value, not as a full-residue island.
def _drive_getbestdir(plane, cur, tgt, inside, overrides):
    from simant.recovered.gameplay import (GET_BEST_DIR_DX, GET_BEST_DIR_DY,
        get_best_dir, life_cell_offset, map_cell_offset)
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    world = m.mem.rw(DG, hooks.ISITFOOD_WORLD_SEG_G)
    m.mem.ww(world, hooks.ISITFOOD_INSIDE_FLAG_OFF, 1 if inside else 0)
    cx, cy = cur
    # seed the 8 neighbours clear (map 0 / life 0), then apply overrides
    cells = {(cx + dx, cy + dy): (0, 0)
             for dx, dy in zip(GET_BEST_DIR_DX, GET_BEST_DIR_DY)}
    cells.update(overrides)
    for (px, py), (mt, lf) in cells.items():
        mo = map_cell_offset(plane, px, py)
        if mo is not None:
            m.mem.wb(DG, mo & 0xFFFF, mt & 0xFF)
            m.mem.wb(DG, life_cell_offset(plane, px, py) & 0xFFFF, lf & 0xFF)
    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[6], 0x405E                # _GetBestDir @ seg6:405E
    sp = s.sp
    for v in (tgt[1], tgt[0], cy, cx, plane, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    for _ in range(60000):                            # 8 dirs x up to 7 sub-calls
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
            break
    else:
        raise AssertionError("ASM _GetBestDir did not return")
    asm = s.ax & 0xFFFF

    def read_map(pl, x, y):
        o = map_cell_offset(pl, x, y)
        return m.mem.rb(DG, o & 0xFFFF) if o is not None else None

    def read_life(pl, x, y):
        o = life_cell_offset(pl, x, y)
        return m.mem.rb(DG, o & 0xFFFF) if o is not None else None

    py = get_best_dir(plane, cx, cy, tgt[0], tgt[1], read_map, read_life,
                      inside) & 0xFFFF
    return asm, py


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("plane,cur,tgt,overrides", [
    (2, (0x10, 0x10), (0x10, 0x10), {}),                     # already there -> -1
    (2, (0x10, 0x10), (0x18, 0x10), {}),                     # clear path east
    (2, (0x10, 0x10), (0x10, 0x04), {}),                     # clear path north
    (2, (0x10, 0x10), (0x18, 0x10), {(0x11, 0x10): (0x40, 0)}),  # direct dir is an obstacle
    (2, (0x10, 0x10), (0x18, 0x10), {(0x11, 0x10): (0x00, 5)}),  # direct dir occupied -> fallback
    (2, (0x10, 0x10), (0x18, 0x10), {(0x11, 0x10): (0x30, 0)}),  # direct dir is a pebble
    (0, (0x10, 0x10), (0x18, 0x10), {}),                     # nest plane, clear east
    (2, (0x01, 0x10), (0x00, 0x10), {}),                     # near the grid edge
])
def test_getbestdir_matches_asm_return(plane, cur, tgt, overrides, inside):
    asm, py = _drive_getbestdir(plane, cur, tgt, inside, overrides)
    assert asm == py, f"p={plane} cur={cur} tgt={tgt} ov={overrides} in={inside}: asm={asm:#x} py={py:#x}"


# ---- _IsClear3x3 (seg5:5AD2) — 3x3 block clear (9x _IsClearTile) --------------
def _run_isclear3x3(with_island, plane, x, y, blocked):
    from simant.recovered.gameplay import (CLEAR_3X3_DX, CLEAR_3X3_DY,
                                           life_cell_offset, map_cell_offset)
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    cells = [(x, y)] + [(x + dx, y + dy)
                        for dx, dy in zip(CLEAR_3X3_DX, CLEAR_3X3_DY)]
    for i, (cx, cy) in enumerate(cells):
        mo = map_cell_offset(plane, cx, cy)
        if mo is not None:                            # clear=map 0/life 0; block=high tile
            m.mem.wb(DG, mo & 0xFFFF, 0xFF if blocked == i else 0x00)
            m.mem.wb(DG, life_cell_offset(plane, cx, cy) & 0xFFFF, 0x00)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISCLEAR3X3_SEG_INDEX], hooks.ISCLEAR3X3_OFF
    sp = s.sp
    for v in (y, x, plane, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsClear3x3", max_steps=6000)
    return _pred_regs(s)


@pytest.mark.parametrize("plane,x,y,blocked", [
    (2, 0x10, 0x10, None),      # all 9 clear -> 1
    (2, 0x10, 0x10, 0),         # centre blocked
    (2, 0x10, 0x10, 1),         # first neighbour blocked
    (2, 0x10, 0x10, 5),         # a middle neighbour blocked
    (2, 0x10, 0x10, 8),         # last neighbour blocked
    (0, 0x10, 0x10, None),      # nest plane, all clear
    (0, 0x10, 0x10, 3),         # nest plane, a neighbour blocked
    (2, 0x00, 0x00, None),      # corner: some neighbours off-grid -> not clear
    (2, 0x3F, 0x3F, None),      # far corner
])
def test_isclear3x3_island_matches_asm(plane, x, y, blocked):
    asm = _run_isclear3x3(False, plane, x, y, blocked)
    isl = _run_isclear3x3(True, plane, x, y, blocked)
    assert isl == asm, f"(p={plane},{x:#x},{y:#x},blk={blocked}): {isl} != {asm}"


# ---- _IsClearTile (seg5:5B2C) — map passable + no blocking ant ---------------
def _run_iscleartile(with_island, plane, x, y, map_tile, life):
    from simant.recovered.gameplay import life_cell_offset, map_cell_offset
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    mo = map_cell_offset(plane, x, y)
    if mo is not None:
        m.mem.wb(DG, mo & 0xFFFF, map_tile)
        m.mem.wb(DG, life_cell_offset(plane, x, y) & 0xFFFF, life)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.ISCLEARTILE_SEG_INDEX], hooks.ISCLEARTILE_OFF
    sp = s.sp
    for v in (y, x, plane, SENT_CS, SENT_IP):        # plane@[bp+6], x@[bp+8], y@[bp+0xa]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_IsClearTile")
    return _pred_regs(s)


@pytest.mark.parametrize("life", [0x00, 0x05, 0xFE, 0xFF, 0x80])
@pytest.mark.parametrize("plane,x,y,map_tile", [
    (0, 0x10, 0x10, 0x0F), (0, 0x10, 0x10, 0x10), (1, 0x10, 0x10, 0x00),
    (2, 0x10, 0x10, 0x07), (2, 0x10, 0x10, 0x08), (3, 0x10, 0x10, 0x05),
    (0, 0x80, 0x10, 0x00), (2, 0x40, 0x10, 0x00), (4, 0x10, 0x10, 0x00),
])
def test_iscleartile_island_matches_asm(plane, x, y, map_tile, life):
    asm = _run_iscleartile(False, plane, x, y, map_tile, life)
    isl = _run_iscleartile(True, plane, x, y, map_tile, life)
    assert isl == asm, f"(p={plane},{x:#x},{y:#x},m={map_tile:#x},l={life:#x}): {isl} != {asm}"


# ---- _GetMap (seg5:60E2) — the map-cell accessor over the DGROUP planes ------
# Seeds the three plane arrays in DGROUP so a valid read returns a known byte;
# args are (plane, x, y) with plane@[bp+6].
def _run_getmap(with_island, plane, x, y, seed):
    from simant.recovered.gameplay import map_cell_offset
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    s.ds = DG
    off = map_cell_offset(plane, x, y)               # seed the target cell
    if off is not None:
        m.mem.wb(DG, off & 0xFFFF, seed)
    s.sp = 0xFF00
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.GETMAP_SEG_INDEX], hooks.GETMAP_OFF
    sp = s.sp
    for v in (y, x, plane, SENT_CS, SENT_IP):        # plane@[bp+6], x@[bp+8], y@[bp+0xa]
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    _step_to_return(m, s, with_island, "_GetMap")
    return _pred_regs(s)


@pytest.mark.parametrize("plane,x,y", [
    (0, 0, 0), (1, 0x7F, 0x3F), (1, 0x10, 0x20),     # valid yard planes
    (2, 0, 0), (2, 0x3F, 0x3F),                       # valid nest plane
    (3, 0x20, 0x10),                                  # valid plane 3
    (0, 0x80, 0x00), (0, 0x10, 0x40), (2, 0x40, 0),   # coord-invalid
    (4, 0x10, 0x10), (-1, 0x10, 0x10), (5, 0, 0),     # plane-invalid (coords ok)
])
def test_getmap_island_matches_asm(plane, x, y):
    seed = 0x6C
    asm = _run_getmap(False, plane, x, y, seed)
    isl = _run_getmap(True, plane, x, y, seed)
    assert isl == asm, f"(p={plane},{x:#x},{y:#x}): island {isl} != asm {asm}"
    from simant.recovered.gameplay import map_cell_offset
    expect = seed if map_cell_offset(plane, x, y) is not None else 0xFFFF
    assert asm["ax"] == expect


# ---- _CopyName (seg4:7438) — recovered/netbios.py --------------------------
def _run_copyname(with_island, src_bytes):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    s.sp = 0xFF00
    DST_SEG, DST_OFF = 0x7100, 0x0020
    SRC_SEG, SRC_OFF = 0x7000, 0x0010
    for i, b in enumerate(src_bytes):
        m.mem.wb(SRC_SEG, (SRC_OFF + i) & 0xFFFF, b)
    for i in range(0x10):                          # poison the dst field
        m.mem.wb(DST_SEG, (DST_OFF + i) & 0xFFFF, 0xEE)
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.COPYNAME_SEG_INDEX], hooks.COPYNAME_OFF
    sp = s.sp
    # stack: dst far (off,seg), src far (off,seg), ret
    for v in (SRC_SEG, SRC_OFF, DST_SEG, DST_OFF, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    if with_island:
        m.cpu.step()
    else:
        for _ in range(5000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _CopyName did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    dst = bytes(m.mem.rb(DST_SEG, (DST_OFF + i) & 0xFFFF) for i in range(0x10))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return dst, regs


@pytest.mark.parametrize("src", [
    b"HELLO\x00", b"\x00", b"EXACTLY16CHARS!!\x00",     # empty, short, exactly 16
    b"THIS_NAME_IS_WAY_TOO_LONG_TO_FIT\x00",            # over 16 -> clamped
    b"ABC DEF\x00",                                     # embedded space
])
def test_copyname_island_matches_asm(src):
    asm = _run_copyname(False, src)
    isl = _run_copyname(True, src)
    assert isl[0] == asm[0], f"src={src!r}: name field differs {isl[0]!r} != {asm[0]!r}"
    assert isl[1] == asm[1], f"src={src!r}: exit state differs {isl[1]} != {asm[1]}"


# ---- _GenOverMap (seg4:46E9) — recovered/render.py -------------------------
def _run_genovermap(with_island, mode):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    CX0, DX0, TBL1, TBL2 = 0x2000, 0x4000, 0x6000, 0x6100
    DST_SEG, DST_OFF = 0x7100, 0x0000
    # two 8 KB column-major source grids (some zeros to hit both branches)
    for i in range(0x2000):
        m.mem.wb(DG, (CX0 + i) & 0xFFFF, 0 if i % 5 == 0 else (i * 7 + 1) & 0xFF)
        m.mem.wb(DG, (DX0 + i) & 0xFFFF, (i * 11 + 3) & 0xFF)
    for j in range(0x100):
        m.mem.wb(DG, (TBL1 + j) & 0xFFFF, (j * 13 + 5) & 0xFF)
        m.mem.wb(DG, (TBL2 + j) & 0xFFFF, (j * 17 + 9) & 0xFF)
    for i in range(0x2000):
        m.mem.wb(DST_SEG, (DST_OFF + i) & 0xFFFF, 0xEE)   # poison dst
    m.mem.ww(DG, hooks.GENOVERMAP_TBL1_G, 0x1234)         # poison the scratch globals
    m.mem.ww(DG, hooks.GENOVERMAP_TBL2_G, 0x5678)

    s.ds = DG
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.GENOVERMAP_SEG_INDEX], hooks.GENOVERMAP_OFF
    s.sp = 0xFF00
    sp = s.sp
    # stack (high->low): mode, tbl2, tbl1, dx0, cx0, dst_seg, dst_off, ret
    for v in (mode, TBL2, TBL1, DX0, CX0, DST_SEG, DST_OFF, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(2_000_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _GenOverMap did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    dst_lin = m.mem._xlat(DST_SEG, DST_OFF)
    dst = bytes(m.mem.data[dst_lin:dst_lin + 0x2000])
    glob = (m.mem.rw(DG, hooks.GENOVERMAP_TBL1_G), m.mem.rw(DG, hooks.GENOVERMAP_TBL2_G))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return dst, glob, regs


@pytest.mark.parametrize("mode", [0, 1])
def test_genovermap_island_matches_asm(mode):
    asm = _run_genovermap(False, mode)
    isl = _run_genovermap(True, mode)
    assert isl[0] == asm[0], f"mode={mode}: overlay map bytes differ"
    assert isl[1] == asm[1], f"mode={mode}: scratch globals differ {isl[1]} != {asm[1]}"
    assert isl[2] == asm[2], f"mode={mode}: exit state differs {isl[2]} != {asm[2]}"


# ---- _GenNestMap (seg4:4754) — recovered/render.py ------------------------
def _run_gennestmap(with_island, mode):
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if with_island:
        assert hooks.install(m) == hooks.EXPECTED_ISLAND_COUNT
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    CX0, DX0, TBL = 0x2000, 0x3000, 0x6000
    VAL_FEFF, VAL_HIGH, VAL_ELSE = 0x11, 0x22, 0x33
    DST_SEG, DST_OFF = 0x7100, 0x0000
    # 4 KB primary grid cycling through 0 / 0xFE / 0xFF / bit7-set / bit7-clear
    cases = [0, 0xFE, 0xFF, 0x80, 0x81, 0x7F, 0x01, 0, 0x40, 0xC0]
    for i in range(0x1000):
        m.mem.wb(DG, (CX0 + i) & 0xFFFF, cases[i % len(cases)])
        m.mem.wb(DG, (DX0 + i) & 0xFFFF, (i * 11 + 3) & 0xFF)   # secondary layer
    for j in range(0x100):
        m.mem.wb(DG, (TBL + j) & 0xFFFF, (j * 13 + 5) & 0xFF)
    for i in range(0x1000):
        m.mem.wb(DST_SEG, (DST_OFF + i) & 0xFFFF, 0xEE)         # poison dst
    for a in (hooks.GENNEST_TABLE_GLOBAL, hooks.GENNEST_COLA_GLOBAL,
              hooks.GENNEST_COLB_GLOBAL, hooks.GENNEST_COLC_GLOBAL):
        m.mem.ww(DG, a, 0xABCD)                                 # poison scratch

    s.ds = DG
    s.ax, s.bx, s.cx, s.dx = 0xA1A1, 0xB1B1, 0xC1C1, 0xD1D1
    s.si, s.di, s.bp, s.es = 0x1111, 0x2222, 0x3333, 0x9999
    s.cs, s.ip = m.seg_bases[hooks.GENNESTMAP_SEG_INDEX], hooks.GENNESTMAP_OFF
    s.sp = 0xFF00
    sp = s.sp
    # stack (high->low): mode, val_else, val_high, val_feff, tbl, dx0, cx0, dst_seg, dst_off, ret
    for v in (mode, VAL_ELSE, VAL_HIGH, VAL_FEFF, TBL, DX0, CX0,
              DST_SEG, DST_OFF, SENT_CS, SENT_IP):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp

    if with_island:
        m.cpu.step()
    else:
        for _ in range(2_000_000):
            m.cpu.step()
            if (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP):
                break
        else:
            raise AssertionError("ASM _GenNestMap did not return")
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == (SENT_CS, SENT_IP)
    dst_lin = m.mem._xlat(DST_SEG, DST_OFF)
    dst = bytes(m.mem.data[dst_lin:dst_lin + 0x1000])
    glob = (m.mem.rw(DG, hooks.GENNEST_TABLE_GLOBAL), m.mem.rb(DG, hooks.GENNEST_COLA_GLOBAL),
            m.mem.rb(DG, hooks.GENNEST_COLB_GLOBAL), m.mem.rb(DG, hooks.GENNEST_COLC_GLOBAL))
    regs = dict(ax=s.ax, bx=s.bx, cx=s.cx, dx=s.dx, si=s.si, di=s.di,
                bp=s.bp, sp=s.sp, ds=s.ds, es=s.es)
    return dst, glob, regs


@pytest.mark.parametrize("mode", [0, 1])
def test_gennestmap_island_matches_asm(mode):
    asm = _run_gennestmap(False, mode)
    isl = _run_gennestmap(True, mode)
    assert isl[0] == asm[0], f"mode={mode}: nest map bytes differ"
    assert isl[1] == asm[1], f"mode={mode}: scratch globals differ {isl[1]} != {asm[1]}"
    assert isl[2] == asm[2], f"mode={mode}: exit state differs {isl[2]} != {asm[2]}"
