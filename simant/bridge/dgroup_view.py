"""Byte-backed *typed views* over SimAnt's DGROUP — the layout bridge.

The concrete seam of the VM-less port (the win16 analogue of pre2's
``dgroup_view``).  Recovered logic operates on a *view* — ``s.rng_seed``,
``s.map_cols`` — and never sees a raw offset; this module is the ONLY place a
DGROUP offset for a migrated island is written down.

A view holds a **backend** (the ports-and-adapters seam) whose fields address
it in DGROUP OFFSETS.  The same view (and the same recovered logic) runs over
either backend:

* :class:`ByteBackend` — reads/writes straight through a flat address-space
  image at ``dgroup_base + offset``.  In the VM that image is ``mem.data`` with
  ``dgroup_base = seg_bases[DGROUP] << 4``; native it is ``NativeGameState.data``
  with a fixed base.  Byte-exact verification stays a plain memcmp of the DGROUP
  window against the ASM oracle, because the native state IS the memory image.
* :class:`OverlayBackend` — a read-through overlay: reads fall through to a base
  reader, writes ACCUMULATE a ``{offset: value}`` contract without mutating the
  base (for whole-routine transforms that return a write set).

Unlike pre2's DOS (a flat ``DS<<4`` segment), win16 is selector-based, so the
DGROUP linear base is not a constant — the caller supplies it (``mem`` adapters
pass ``seg_bases[10] << 4``; the native state passes its own base).  Within the
one ≤64 KB DGROUP segment, addressing is flat, so the same descriptors serve.
"""
from __future__ import annotations


# ---- backends ---------------------------------------------------------------

class ByteBackend:
    """Reads/writes go straight to a flat image at ``base + (off & 0xFFFF)``.

    `source` is anything exposing ``.data`` (a VM ``mem`` / a ``NativeGameState``)
    or a raw ``bytearray``; `base` is DGROUP's linear address in that image.
    """

    __slots__ = ("data", "base")

    def __init__(self, source, base: int):
        self.data = source.data if hasattr(source, "data") else source
        self.base = base

    def rb(self, off: int) -> int:
        return self.data[self.base + (off & 0xFFFF)]

    def wb(self, off: int, v: int) -> None:
        self.data[self.base + (off & 0xFFFF)] = v & 0xFF

    def rw(self, off: int) -> int:
        a = self.base + (off & 0xFFFF)
        return self.data[a] | (self.data[a + 1] << 8)

    def ww(self, off: int, v: int) -> None:
        a = self.base + (off & 0xFFFF)
        self.data[a] = v & 0xFF
        self.data[a + 1] = (v >> 8) & 0xFF


class SelectorBackend:
    """Reads/writes through a win16 VM ``mem`` at a fixed selector — the faithful
    hybrid-mode backend.  Unlike :class:`ByteBackend`'s flat indexing, it goes
    through ``mem.rb(seg, off)`` / ``mem.rw(seg, off)`` so selector translation
    (RPL masking, >64 KB huge blocks) matches the VM exactly.  Duck-typed on the
    ``mem`` object — it imports no VM, so the bridge stays VM-independent.

    For a plain ≤64 KB segment (DGROUP) at rest this is equivalent to a
    ``ByteBackend`` over ``mem.data`` at ``seg << 4``; the state-view test pins
    that equivalence.  Native runs use :class:`ByteBackend` over the owned image.
    """

    __slots__ = ("_mem", "seg")

    def __init__(self, mem, seg: int):
        self._mem = mem
        self.seg = seg & 0xFFFF

    def rb(self, off: int) -> int:
        return self._mem.rb(self.seg, off & 0xFFFF)

    def wb(self, off: int, v: int) -> None:
        self._mem.wb(self.seg, off & 0xFFFF, v & 0xFF)

    def rw(self, off: int) -> int:
        return self._mem.rw(self.seg, off & 0xFFFF)

    def ww(self, off: int, v: int) -> None:
        self._mem.ww(self.seg, off & 0xFFFF, v & 0xFFFF)


class OverlayBackend:
    """Read-through overlay: reads fall through to ``base_rb(offset)`` unless
    already written; writes accumulate a ``{offset: byte}`` contract and never
    touch the base — for a contract-returning island whose whole-routine
    transform must stay a pure function of its inputs."""

    __slots__ = ("_base_rb", "writes")

    def __init__(self, base_rb):
        self._base_rb = base_rb          # base_rb(off) -> the ORIGINAL DGROUP byte
        self.writes: dict[int, int] = {}

    def rb(self, off: int) -> int:
        o = off & 0xFFFF
        return self.writes[o] if o in self.writes else self._base_rb(o)

    def wb(self, off: int, v: int) -> None:
        self.writes[off & 0xFFFF] = v & 0xFF

    def rw(self, off: int) -> int:
        return self.rb(off) | (self.rb((off + 1) & 0xFFFF) << 8)

    def ww(self, off: int, v: int) -> None:
        self.wb(off, v)
        self.wb((off + 1) & 0xFFFF, v >> 8)


# ---- field descriptors (offset RELATIVE to the view's base) -----------------

class _U8:
    """An 8-bit field."""
    def __init__(self, off: int):
        self.off = off

    def __get__(self, o, owner=None):
        return self if o is None else o._backend.rb(o._base + self.off)

    def __set__(self, o, v: int):
        o._backend.wb(o._base + self.off, v)


class _S8:
    """A signed 8-bit field (-0x80..0x7F)."""
    def __init__(self, off: int):
        self.off = off

    def __get__(self, o, owner=None):
        if o is None:
            return self
        v = o._backend.rb(o._base + self.off)
        return v - 0x100 if v & 0x80 else v

    def __set__(self, o, v: int):
        o._backend.wb(o._base + self.off, v)


class _U16:
    """A little-endian 16-bit field."""
    def __init__(self, off: int):
        self.off = off

    def __get__(self, o, owner=None):
        return self if o is None else o._backend.rw(o._base + self.off)

    def __set__(self, o, v: int):
        o._backend.ww(o._base + self.off, v)


class _S16:
    """A signed little-endian 16-bit field (-0x8000..0x7FFF)."""
    def __init__(self, off: int):
        self.off = off

    def __get__(self, o, owner=None):
        if o is None:
            return self
        v = o._backend.rw(o._base + self.off)
        return v - 0x10000 if v & 0x8000 else v

    def __set__(self, o, v: int):
        o._backend.ww(o._base + self.off, v)


class _U16Array:
    """A contiguous array of little-endian words; ``view.field[i]`` reads/writes."""
    def __init__(self, off: int, length: int):
        self.off = off
        self.length = length

    def __get__(self, o, owner=None):
        if o is None:
            return self
        return _U16ArrayView(o._backend, o._base + self.off, self.length)


class _U16ArrayView:
    __slots__ = ("_backend", "_base", "length")

    def __init__(self, backend, base: int, length: int):
        self._backend = backend
        self._base = base
        self.length = length

    def __getitem__(self, i: int) -> int:
        return self._backend.rw(self._base + i * 2)

    def __setitem__(self, i: int, v: int) -> None:
        self._backend.ww(self._base + i * 2, v)

    def __len__(self) -> int:
        return self.length


class _Bytes:
    """A raw byte grid (a map / life plane); ``view.field[i]`` reads/writes a
    byte at ``base + i``.  ``length`` is optional (informational — the plane's
    addressable span); indexing is not bounds-checked, matching the ASM."""
    def __init__(self, off: int, length: int | None = None):
        self.off = off
        self.length = length

    def __get__(self, o, owner=None):
        if o is None:
            return self
        return _BytesView(o._backend, o._base + self.off, self.length)


class _BytesView:
    __slots__ = ("_backend", "_base", "length")

    def __init__(self, backend, base: int, length):
        self._backend = backend
        self._base = base
        self.length = length

    def __getitem__(self, i: int) -> int:
        return self._backend.rb(self._base + i)

    def __setitem__(self, i: int, v: int) -> None:
        self._backend.wb(self._base + i, v)


# ---- views ------------------------------------------------------------------

def _coerce_backend(source, base: int):
    """A backend passes through; anything else (NativeGameState / VM ``mem`` /
    raw ``bytearray``) is wrapped in a :class:`ByteBackend` at ``base``."""
    if isinstance(source, (ByteBackend, SelectorBackend, OverlayBackend)):
        return source
    return ByteBackend(source, base)


class StructView:
    """A view over one fixed-layout struct at a DGROUP ``base`` offset; its field
    descriptors add their own (relative) offset to ``base``."""

    __slots__ = ("_backend", "_base")

    def __init__(self, backend, base: int = 0):
        self._backend = backend
        self._base = base


class StructArray:
    """Descriptor for a fixed-stride array of structs; ``view.field[i]`` binds
    ``struct_cls`` to ``base + i*stride``."""
    def __init__(self, off: int, stride: int, length: int, struct_cls):
        self.off = off
        self.stride = stride
        self.length = length
        self.struct_cls = struct_cls

    def __get__(self, o, owner=None):
        if o is None:
            return self
        return _StructArrayView(o._backend, o._base + self.off, self.stride,
                                self.length, self.struct_cls)


class _StructArrayView:
    __slots__ = ("_backend", "_base", "stride", "length", "cls")

    def __init__(self, backend, base, stride, length, cls):
        self._backend = backend
        self._base = base
        self.stride = stride
        self.length = length
        self.cls = cls

    def __getitem__(self, i: int):
        if i < 0:
            i += self.length
        return self.cls(self._backend, self._base + i * self.stride)

    def __len__(self) -> int:
        return self.length

    def __iter__(self):
        for i in range(self.length):
            yield self[i]


class DgroupView(StructView):
    """Whole-DGROUP view (base 0, so fields are ABSOLUTE DGROUP offsets).  Wrap a
    ``NativeGameState`` / VM ``mem`` / raw ``bytearray`` in a :class:`ByteBackend`
    at ``dgroup_base``, or pass a backend directly (``dgroup_base`` ignored)."""

    def __init__(self, source, dgroup_base: int = 0):
        super().__init__(_coerce_backend(source, dgroup_base), 0)


#: base of the _Unpack (LZSS) decoder's resumable state block in DGROUP.
UNPACK_STATE_BASE = 0xB7C0


class UnpackState(StructView):
    """The _Unpack LZSS decompressor's resumable state (base 0xB7C0) — the
    decoder is called in chunks and parks all of this between calls.  Named as a
    struct so the island reads like the decoder it drives, not scattered offsets.
    """
    win_seg = _U16(0x00)        # 0xB7C0 — 4 KB sliding-window selector
    thresh = _U16(0x02)         # 0xB7C2 — match-length threshold
    src_off = _U16(0x04)        # 0xB7C4 — compressed-input cursor
    src_seg = _U16(0x06)        # 0xB7C6 — compressed-input selector
    in_rem = _S16(0x08)         # 0xB7C8 — signed input-bytes-remaining counter
    r = _U16(0x0A)              # 0xB7CA — window write position
    flags = _U16(0x0C)          # 0xB7CC — flag-bit buffer
    dx = _U16(0x0E)             # 0xB7CE — decoder scratch (dx)
    cx = _U16(0x10)             # 0xB7D0 — decoder scratch (cx)
    match_rem = _U16(0x12)      # 0xB7D2 — bytes left in the current match copy
    resume = _U16(0x14)         # 0xB7D4 — resume/return code (0 = fresh call)


#: base of the _DrawChar blit's cached scratch block in DGROUP.
DRAWCHAR_GLOBALS_BASE = 0xB90E


class DrawCharGlobals(StructView):
    """The _DrawChar glyph-blit's cached DGROUP scratch (base 0xB90E): the
    source/dest selectors it stores and the per-scanline strides it reads back,
    plus the cached word count.  Bound relative to the base so the offsets read
    as a struct, not scattered absolutes."""
    src_seg = _U16(0x00)        # 0xB90E — glyph source selector (written)
    dst_seg = _U16(0x02)        # 0xB910 — dest selector (written)
    src_stride = _U16(0x04)     # 0xB912 — source scanline stride (read)
    dst_stride = _U16(0x06)     # 0xB914 — dest scanline stride (read)
    words = _U16(0x0A)          # 0xB918 — cached full-word span (written)


class FarPtr(StructView):
    """A 16:16 far pointer (offset then selector) — a reusable 4-byte record."""
    off = _U16(0x00)
    seg = _U16(0x02)


# The world is stored as three map planes and three life planes packed in
# DGROUP, each a byte grid addressed column-major with an x-stride of 64
# (index = (x << 6) + y).  Planes 0 and 1 share the wide "yard" grid (128x64);
# planes 2 and 3 are the 64x64 nest grids.  These bases are the layout ("WHERE")
# for _GetMap / _GetLife and their callers; recovered logic imports them.
MAP_PLANE_BASE = {0: 0x28E8, 1: 0x28E8, 2: 0x48E8, 3: 0x58E8}
LIFE_PLANE_BASE = {0: 0x68E8, 1: 0x68E8, 2: 0x88E8, 3: 0x98E8}
_YARD_SPAN = 0x80 * 0x40                     # 128x64 bytes
_NEST_SPAN = 0x40 * 0x40                     # 64x64 bytes


class SimAntState(DgroupView):
    """SimAnt's DGROUP as named source-level fields — the human-readable state
    the recovered logic reads/writes.  Grows one verified field at a time as
    islands migrate off raw offsets.  Every offset here is cross-checked against
    the SIMANTW.SYM symbol and the recovered routines that use it.
    """
    #: the simulation LFSR seed stepped by every _SRand* call (SIMONE_MODULE).
    rng_seed = _U16(0xCBF2)
    #: the selected music/sound device (Sound Mode digit from SIMANT.CFG).
    music_device = _S16(0xB91C)
    #: the edit/map grid dimensions the tile renderers stride by.
    map_cols = _U16(0xCC80)
    map_rows = _U16(0xCD7A)
    #: nonzero while songs are enabled (gates the MMSYSTEM music path).
    songs_on = _U16(0x0AF6)

    # -- window management (SIMTWO_MODULE) --  the object handle's HIGH byte is a
    # window-table slot, so both tables are addressed by slot 0..255 (the live
    # window count is smaller; the length is the addressable range).
    #: slot -> HWND (_win_IsWinOpen's `g_window_hwnd`).
    window_hwnd = _U16Array(0xBCA6, 256)
    #: slot -> far pointer to the window record (_win_GetObjRect's table).
    window_records = StructArray(0xCE9A, 4, 256, FarPtr)
    #: nonzero => stored object rects are inclusive (bump right/bottom by one).
    obj_rect_inclusive = _U16(0xBD0A)

    # -- the world grids (SIMONE_MODULE) --  the tile map and the life grid, as
    # named byte planes.  `map_planes[p]` / `life_planes[p]` index by plane; the
    # recovered map/life accessors compute (x<<6)+y and read through these.
    map_yard = _Bytes(MAP_PLANE_BASE[0], _YARD_SPAN)     # planes 0, 1
    map_nest = _Bytes(MAP_PLANE_BASE[2], _NEST_SPAN)     # plane 2
    map_plane3 = _Bytes(MAP_PLANE_BASE[3], _NEST_SPAN)   # plane 3
    life_yard = _Bytes(LIFE_PLANE_BASE[0], _YARD_SPAN)
    life_nest = _Bytes(LIFE_PLANE_BASE[2], _NEST_SPAN)
    life_plane3 = _Bytes(LIFE_PLANE_BASE[3], _NEST_SPAN)

    @property
    def map_planes(self):
        return (self.map_yard, self.map_yard, self.map_nest, self.map_plane3)

    @property
    def life_planes(self):
        return (self.life_yard, self.life_yard, self.life_nest, self.life_plane3)
