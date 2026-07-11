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
    assert hooks.install(hk) == 27              # all islands, incl. the PRNG family
    isl = _run_island(hk, dividend, divisor)

    assert asm["ax"] | (asm["dx"] << 16) == (dividend // divisor) & 0xFFFFFFFF
    assert isl == asm, (
        f"{dividend:#x} // {divisor:#x}: island {isl} != asm {asm}")


def test_install_counts_and_verifies():
    m = runtime.create_machine()
    assert hooks.install(m) == 27
    assert runtime.install_hooks(runtime.create_machine()) == 27


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
        assert hooks.install(m) == 27
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
            assert hooks.install(m) == 27
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
        assert hooks.install(m) == 27
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
        assert hooks.install(m) == 27
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    WINREC, SRC, LPRECT = 0x7000, 0x7100, 0x7200        # scratch offsets in DGROUP
    slot, obj = (obj_handle >> 8) & 0xFF, obj_handle & 0xFF

    # far-ptr table[slot] -> winrec;  winrec+0x2C+obj*4 -> src RECT
    tab = (hooks.GETOBJRECT_WINTAB_OFF + slot * 4) & 0xFFFF
    m.mem.ww(DG, tab, WINREC);            m.mem.ww(DG, (tab + 2) & 0xFFFF, DG)
    arr = (WINREC + hooks.GETOBJRECT_OBJARR_OFF + obj * 4) & 0xFFFF
    m.mem.ww(DG, arr, SRC);              m.mem.ww(DG, (arr + 2) & 0xFFFF, DG)
    for i, v in enumerate(rect):
        m.mem.ww(DG, (SRC + i * 2) & 0xFFFF, v & 0xFFFF)
    for i in range(4):
        m.mem.ww(DG, (LPRECT + i * 2) & 0xFFFF, 0xEEEE)   # poison the output
    m.mem.ww(DG, hooks.GETOBJRECT_FLAG_OFF, flag)

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
        assert hooks.install(m) == 27
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
        assert hooks.install(m) == 27
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
        assert hooks.install(m) == 27
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
        assert hooks.install(m) == 27
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
        assert hooks.install(m) == 27
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
        assert hooks.install(m) == 27
    s = m.cpu.s
    DG = m.seg_bases[hooks.DG_SEG_INDEX]
    SRC, DST = 0x6000, 0x7000
    for i in range(span):
        m.mem.wb(DG, (DST + i) & 0xFFFF, dst_fill[i % len(dst_fill)])
    for i, b in enumerate(glyph):
        m.mem.wb(DG, (SRC + i) & 0xFFFF, b)
    m.mem.ww(DG, hooks.DRAWCHAR_G_SRCSTRIDE, src_stride)
    m.mem.ww(DG, hooks.DRAWCHAR_G_DSTSTRIDE, dst_stride)

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
        assert hooks.install(m) == 27
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
