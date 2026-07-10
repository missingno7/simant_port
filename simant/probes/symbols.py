"""Read SimAnt's shipped SIMANTW.SYM to name code addresses — segment-aware.

The file is a Microsoft MAPSYM table (the symdeb .SYM format):

    MAPDEF (at 0):  ppNextMap:2  bFlags:1  bReserved:1  pSegEntry:2
                    cConsts:2  pConstDef:2  cSegs:2  ppSegDef:2
                    cbMaxSym:1  cbModName:1  achModName[cbModName]
    SEGDEF chain (each at ppNextSeg*16, first at ppSegDef*16):
                    ppNextSeg:2  cSymbols:2  pSymDef:2  ...pad...
                    cbSegName:1 @ +20  achSegName[cbSegName] @ +21
    pSymDef is SEGDEF-relative and points at cSymbols WORDs, each the
    SEGDEF-relative offset of a SYMDEF:  wSymVal:2  cbSymName:1  achSymName.

SIMANTW.SYM's ten SEGDEFs are in NE-segment order (validated by the recovered
anchors in tests/test_symbols.py), and their names are the game's original
source modules — SIMANT_MODULE, GR_MODULE, ANTEDIT_MODULE, _TEXT,
SIMONE_MODULE, SIMANT1_MODULE, SIMTWO_MODULE + three data groups — i.e. the
.C file layout the recovered source should eventually mirror.

Lookups are per-segment (an offset never resolves into another segment's
table); `nearest_symbol` answers in symdeb style, `MODULE!_name+0xNN`.
The MAPDEF's absolute-constant table (cConsts) is not parsed — nothing has
needed it yet.
"""
from __future__ import annotations

import struct
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

_SYM_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "ANTWIN" \
    / "SIMANTW.SYM"


@lru_cache(maxsize=1)
def _segments() -> list[tuple[str, list[tuple[int, str]]]]:
    """Per NE segment (1-based order): (module name, sorted (offset, name))."""
    if not _SYM_PATH.exists():
        return []
    data = _SYM_PATH.read_bytes()
    cSegs, ppSegDef = struct.unpack_from("<HH", data, 10)
    segs: list[tuple[str, list[tuple[int, str]]]] = []
    pos = ppSegDef * 16
    for _ in range(cSegs):
        ppNextSeg, cSymbols, pSymDef = struct.unpack_from("<HHH", data, pos)
        cbName = data[pos + 20]
        segname = data[pos + 21:pos + 21 + cbName].decode("latin-1")
        syms: list[tuple[int, str]] = []
        for p in struct.unpack_from(f"<{cSymbols}H", data, pos + pSymDef):
            val = struct.unpack_from("<H", data, pos + p)[0]
            ln = data[pos + p + 2]
            syms.append((val, data[pos + p + 3:pos + p + 3 + ln].decode("latin-1")))
        syms.sort()
        segs.append((segname, syms))
        pos = ppNextSeg * 16
    return segs


def module_name(seg: int) -> str:
    """The source-module name of NE segment `seg` (1-based), '' if unknown."""
    segs = _segments()
    return segs[seg - 1][0] if 1 <= seg <= len(segs) else ""


def _short_module(name: str) -> str:
    return name[:-7] if name.endswith("_MODULE") else name


def nearest_symbol(seg: int, off: int) -> str:
    """The nearest preceding symbol to seg:off, as 'MODULE!_name+0xNN'.
    `seg` is the 1-based NE segment index (the profiler's bucket key)."""
    segs = _segments()
    if not segs:
        return "(no SIMANTW.SYM)"
    if not (1 <= seg <= len(segs)):
        return f"(seg {seg} not in SYM)"
    segname, syms = segs[seg - 1]
    mod = _short_module(segname)
    offs = [o for o, _ in syms]
    i = bisect_right(offs, off) - 1
    if i < 0:
        return f"{mod}!(before first symbol)"
    sym_off, name = syms[i]
    delta = off - sym_off
    return f"{mod}!{name}" + (f"+0x{delta:X}" if delta else "")


def symbols_in_segment(seg: int) -> list[tuple[int, str]]:
    """Every (offset, name) of NE segment `seg` (1-based), sorted by offset."""
    segs = _segments()
    return list(segs[seg - 1][1]) if 1 <= seg <= len(segs) else []


def symbols_in_range(seg: int, lo: int, hi: int) -> list[tuple[int, str]]:
    """Every (offset, name) in segment `seg` with lo <= offset < hi."""
    return [(o, n) for o, n in symbols_in_segment(seg) if lo <= o < hi]
