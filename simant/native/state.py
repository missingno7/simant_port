"""``NativeGameState`` — SimAnt's memory, owned without a VM.

It *is* the game's address-space image (a ``bytearray`` exposed as ``.data`` —
exactly what the win16 VM's ``mem`` exposes), so every recovered function and
every ``simant/bridge`` adapter that already reads/writes VM memory runs over it
unchanged.  That is the migration's adapter swap: the recovered function is the
shared centre; today a VM ``mem`` is one adapter, a ``NativeGameState`` is
another — one implementation, two adapters, no second copy that can drift.

win16 is selector-based (not a flat DOS ``DS<<4``), so the state also records
``dgroup_base`` — DGROUP's linear address in the image — which the bridge views
add their offsets to.  Seeded today from a VM/snapshot (the bootstrap); as
islands take source-level state ownership, more of the per-frame update runs
over this image natively and the VM is needed only as the verify oracle.
"""
from __future__ import annotations

from ..bridge.dgroup_view import SimAntState


class NativeGameState:
    """The recovered game's memory image.  Exposes ``.data`` (so the existing
    ``mem``-shaped bridges index ``.data`` with no change) and ``.dgroup_base``.
    ``.view`` is the named-field :class:`SimAntState` over this image."""

    __slots__ = ("data", "dgroup_base", "view")

    def __init__(self, data: bytearray, dgroup_base: int):
        if not isinstance(data, bytearray):
            data = bytearray(data)
        self.data = data
        self.dgroup_base = dgroup_base
        self.view = SimAntState(self, dgroup_base)

    @classmethod
    def from_machine(cls, machine, dg_seg_index: int = 10) -> "NativeGameState":
        """Snapshot a live win16 machine's address space into a native state —
        the bootstrap seam (VM image -> owned image), copying DGROUP's linear
        base from the machine's segment table."""
        base = machine.seg_bases[dg_seg_index] << 4
        return cls(bytearray(machine.mem.data), base)
