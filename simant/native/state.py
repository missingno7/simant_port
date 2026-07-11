"""``NativeGameState`` — SimAnt's memory, owned without a VM.

It *is* the game's address-space image (a ``bytearray`` exposed as ``.data`` —
exactly what the win16 VM's ``mem`` exposes), so every recovered function and
every ``simant/bridge`` adapter that already reads/writes VM memory runs over it
unchanged.  That is the migration's adapter swap: the recovered function is the
shared centre; today a VM ``mem`` is one adapter, a ``NativeGameState`` is
another — one implementation, two adapters, no second copy that can drift.

win16 is selector-based (not a flat DOS ``DS<<4``), so the state also mirrors the
VM's selector table (``sel_base``, RPL-masked — see dos_re ``Memory._xlat``) and
exposes the same ``rb/rw/wb/ww`` / ``_xlat`` interface — making it a drop-in for
the ``mem`` the recovered adapters take.  It records ``dgroup_base`` too, which
the flat bridge views add their offsets to.  Seeded today from a VM/snapshot (the
bootstrap); as islands take source-level state ownership, more of the per-frame
update runs over this image natively and the VM is needed only as the oracle.
"""
from __future__ import annotations

from ..bridge.dgroup_view import SimAntState


class NativeGameState:
    """The recovered game's memory image + selector table — a VM-free ``mem``.

    Exposes ``.data`` (so the flat ``mem``-shaped bridges index it unchanged),
    ``.dgroup_base`` (the flat DGROUP views' base), and the selector interface
    (``rb/rw/wb/ww`` / ``_xlat``) resolved exactly like the VM.  ``.view`` is the
    named-field :class:`SimAntState` over this image.
    """

    __slots__ = ("data", "dgroup_base", "sel_base", "sel_min", "view")

    def __init__(self, data: bytearray, dgroup_base: int,
                 sel_base: dict | None = None, sel_min: int = 0):
        if not isinstance(data, bytearray):
            data = bytearray(data)
        self.data = data
        self.dgroup_base = dgroup_base
        self.sel_base = dict(sel_base) if sel_base else {}
        self.sel_min = sel_min
        self.view = SimAntState(self, dgroup_base)

    # -- the mem interface (selector -> linear, exactly as dos_re Memory) ------
    def _xlat(self, seg: int, off: int) -> int:
        seg &= 0xFFFF
        if seg >= self.sel_min:
            base = self.sel_base.get(seg & 0xFFFC)      # RPL-masked descriptor
            if base is not None:
                return base + (off & 0xFFFF)
        return (seg << 4) + (off & 0xFFFF)

    def rb(self, seg: int, off: int) -> int:
        return self.data[self._xlat(seg, off)]

    def wb(self, seg: int, off: int, v: int) -> None:
        self.data[self._xlat(seg, off)] = v & 0xFF

    def rw(self, seg: int, off: int) -> int:
        a = self._xlat(seg, off)
        return self.data[a] | (self.data[a + 1] << 8)

    def ww(self, seg: int, off: int, v: int) -> None:
        a = self._xlat(seg, off)
        self.data[a] = v & 0xFF
        self.data[a + 1] = (v >> 8) & 0xFF

    # -- bootstrap ------------------------------------------------------------
    @classmethod
    def from_machine(cls, machine, dg_seg_index: int = 10) -> "NativeGameState":
        """Snapshot a live win16 machine's address space into a native state —
        the bootstrap seam (VM image -> owned image), copying DGROUP's linear
        base and the selector table so seg:off resolves identically."""
        mem = machine.mem
        base = mem._xlat(machine.seg_bases[dg_seg_index], 0)
        return cls(bytearray(mem.data), base,
                   sel_base=getattr(mem, "sel_base", None) or {},
                   sel_min=getattr(mem, "sel_min", 0))
