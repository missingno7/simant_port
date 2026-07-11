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
