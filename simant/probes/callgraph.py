"""Static call-graph over the loaded SimAnt image — the bottom-up recovery queue.

Joins `win16.callgraph` (call-site extraction over the loaded machine) with
the segment-aware SIMANTW.SYM table: every named routine in the seven code
segments gets its callees resolved to names and a recovery classification:

    leaf      no calls at all — fits the existing A/B island oracle as-is
    api       calls only the OS API (thunk far-calls) — recoverable next;
              an island can service those through the Python API layer
    coupled   calls other game routines — recover after its callees (their
              proven-byte-exact Python stands in for the sub-calls)
    indirect  contains an indirect call — needs live evidence first

    python -m simant.probes.callgraph            # summary + the leaf queue
    python -m simant.probes.callgraph --seg 7    # one segment's full ledger

Triage-grade, like the profiler: spans can contain data the walker crosses
noisily (anomaly counts are reported per routine).  Always confirm against
the disassembly before recovering.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache

from .. import _env  # noqa: F401  (win16_re + dos_re on sys.path)
from .. import runtime
from .symbols import module_name, nearest_symbol, symbols_in_segment

from win16.callgraph import Call, calls_in_range

CODE_SEGS = (1, 2, 3, 4, 5, 6, 7)


# -- native-port role -------------------------------------------------------
# The eventual VM-less port RUNS the `core` simulation unchanged, REIMPLEMENTS
# `presentation` (rendering / windowing / the editor UI), and gets `runtime`
# (C-runtime / codec helpers) from Python.  So `core` is the real native-port
# denominator; a recovered presentation island is workbench scaffolding that a
# native backend discards.  Triage-grade, like `classification`: module default
# + a name-prefix override.
_PRESENTATION_PREFIXES = (
    "_win", "_font", "_db_", "_ch_", "_gr", "_hanim", "_ms", "_snd", "_clip",
    "_ed", "_Draw", "_Ribbon", "_Gauge", "_Balloon", "_Overlay", "_Mini",
    "_Bitmap", "_Mask", "_Icon", "_Cursor", "_DIB", "_Blt", "_Blit",
)
_TEXT_PRESENTATION = ("_Windows", "_Xfer", "_Draw", "_Copy", "_MoveText",
                      "_DoCalcTile", "_os_Clip", "_os_Blt")
_RUNTIME_PREFIXES = ("_output", "_fp", "_exit", "_Prof", "_NMSG", "_LD",
                     "_cintr", "_ctran", "_tran", "_str", "_mem")
_RUNTIME_NAMES = ("_Unpack", "_exchange", "_FlipWord", "_FlipLong",
                  "_XFlipLong", "bytecopy", "_CopyName")


def classify_domain(seg: int, name: str) -> str:
    """core | presentation | runtime — a routine's role in the native port."""
    if seg in (2, 3):                       # GR (graphics), ANTEDIT (editor UI)
        return "presentation"
    if name.startswith("__") or name.startswith(_RUNTIME_PREFIXES) \
            or name in _RUNTIME_NAMES:
        return "runtime"
    if seg == 4:                            # _TEXT: C runtime save its builders
        return "presentation" if name.startswith(_TEXT_PRESENTATION) else "runtime"
    if name.startswith(_PRESENTATION_PREFIXES):
        return "presentation"
    return "core"                           # the simulation (seg1/5/6/7)


@dataclass(frozen=True)
class Routine:
    seg: int
    off: int
    end: int
    name: str
    calls: tuple[Call, ...]
    anomalies: int

    @property
    def size(self) -> int:
        return self.end - self.off

    @property
    def domain(self) -> str:
        """Native-port role: core | presentation | runtime."""
        return classify_domain(self.seg, self.name)

    @property
    def classification(self) -> str:
        kinds = {c.kind for c in self.calls}
        if kinds & {"indirect_near", "indirect_far"}:
            return "indirect"
        if kinds & {"near", "far", "far_unmapped"}:
            return "coupled"
        if "api" in kinds:
            return "api"
        return "leaf"

    def callee_names(self) -> list[str]:
        out = []
        for c in self.calls:
            if c.kind == "near":
                out.append(nearest_symbol(c.seg, c.off))
            elif c.kind == "far":
                out.append(nearest_symbol(c.seg, c.off))
            elif c.kind == "api":
                out.append(c.api)
            elif c.kind == "far_unmapped":
                out.append(f"?far:{c.off:04X}")
            else:
                out.append("(indirect)")
        return out


@lru_cache(maxsize=1)
def build() -> list[Routine]:
    """Scan every named routine in the code segments of a freshly loaded
    machine (relocations applied; nothing executed)."""
    machine = runtime.create_machine()
    routines: list[Routine] = []
    for seg in CODE_SEGS:
        syms = symbols_in_segment(seg)
        code_len = machine.exe.segments[seg - 1].file_length
        for i, (off, name) in enumerate(syms):
            end = syms[i + 1][0] if i + 1 < len(syms) else code_len
            if end <= off:
                continue
            calls, anomalies = calls_in_range(machine, seg, off, end)
            routines.append(Routine(seg, off, end, name, tuple(calls),
                                    anomalies))
    return routines


def main(argv: list[str]) -> None:
    routines = build()
    if "--seg" in argv:
        seg = int(argv[argv.index("--seg") + 1])
        print(f"segment {seg} ({module_name(seg)}):")
        for r in (r for r in routines if r.seg == seg):
            noisy = f"  [{r.anomalies} anomalies]" if r.anomalies else ""
            print(f"  {r.off:04X}-{r.end:04X} {r.classification:8s} "
                  f"{r.name}{noisy}")
            for name in r.callee_names():
                print(f"           -> {name}")
        return

    by_class: dict[str, list[Routine]] = {}
    for r in routines:
        by_class.setdefault(r.classification, []).append(r)
    print(f"{len(routines)} routines across segments {CODE_SEGS}\n")
    print(f"{'seg':>4} {'module':16} {'leaf':>5} {'api':>5} "
          f"{'coupled':>8} {'indirect':>9}")
    for seg in CODE_SEGS:
        row = [r for r in routines if r.seg == seg]
        counts = {k: sum(1 for r in row if r.classification == k)
                  for k in ("leaf", "api", "coupled", "indirect")}
        print(f"{seg:>4} {module_name(seg):16} {counts['leaf']:>5} "
              f"{counts['api']:>5} {counts['coupled']:>8} "
              f"{counts['indirect']:>9}")

    # Native-port progress: recovered vs total, by role.  Only `core` counts
    # toward the VM-less endgame; presentation islands are workbench scaffolding.
    try:
        from .. import hooks
        recovered = {t[4] for t in hooks._ISLANDS}
    except Exception:                       # noqa: BLE001 — probe still useful
        recovered = set()
    print("\nnative-port progress (recovered / total, by role):")
    for dom in ("core", "presentation", "runtime"):
        row = [r for r in routines if r.domain == dom]
        rec = sum(1 for r in row if r.name in recovered)
        note = "  <- the native-port denominator" if dom == "core" else ""
        print(f"  {dom:12} {rec:3d} / {len(row):4d}{note}")

    print("\nleaf queue (smallest first — the next recoveries):")
    for r in sorted(by_class.get("leaf", []), key=lambda r: r.size)[:30]:
        noisy = f"  [{r.anomalies} anomalies]" if r.anomalies else ""
        dom = r.domain[0].upper()
        print(f"  [{dom}] {r.size:5d}B  seg{r.seg}:{r.off:04X}  "
              f"{module_name(r.seg)}!{r.name}{noisy}")


if __name__ == "__main__":
    main(sys.argv[1:])
