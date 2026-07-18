"""argmapgen — derive the missing ``[bp+N]`` arg maps in recovered_map.json.

The ``args-incomplete`` contract class (docs/run_status.md cont.247/249): an
entry of ``simant/facts/recovered_map.json`` names its arguments but carries
``"bp": null`` for them, so ``scripts/adaptgen.py``/``scripts/overridegen.py``
cannot marshal the caller's stack frame and the entry stays on its generated
literal-lift body — the single biggest bucket keeping the manual corpus out of
the CPUless override graph.

This script DERIVES the map from the binary, never guesses it:

* the return kind is closed exactly as adaptgen closes it (recovered_map's
  ``ret``, else mechanically from the IR record's exits) — near args begin at
  ``[bp+4]``, far args at ``[bp+6]``;
* every instruction of the entry's IR record is decoded (``dos_re.lift.decode``)
  and its BP-relative memory operands collected: plain ``[bp+disp]``
  (mod=1/2, rm=6) and BP-INDEXED ``[bp+si+disp]``/``[bp+di+disp]`` (rm=2/3);
* a positive BP-indexed displacement means the frame is walked with a runtime
  index — the slot boundaries are not statically knowable, so the entry is
  REFUSED;
* the set of positive plain displacements must equal EXACTLY the contiguous
  word frame the entry's arity implies, ``{base, base+2, ..., base+2(k-1)}``.
  Any extra slot (a dword/far-pointer arg, an arg the map does not name), any
  missing slot (an arg the body never reads, so the frame extent is unpinned),
  or an odd/unaligned displacement REFUSES the entry.

That pins the frame EXTENT mechanically.  The assignment of names to slots is
the Microsoft C ``cdecl`` order the whole corpus already runs on (declaration
order ascending from ``base``) — not asserted here but PROVEN per entry by the
generated-adapter A/B oracle against the original ASM
(``simant/tests/test_adaptgen.py`` tier 3, and ``scripts/adaptverify.py``
per-call over a demo): a transposed pair changes the result register or the
data segments and the oracle diverges.  An entry that cannot be derived keeps
``"bp": null`` and is reported — a guessed offset is a silent
memory-corruption bug (docs/run_status.md cont.250).

    python scripts/argmapgen.py --dry-run     # report only
    python scripts/argmapgen.py               # write recovered_map.json
    python scripts/argmapgen.py --check       # re-derive; nonzero if the
                                              # committed map disagrees
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401

from dos_re.lift.decode import decode_one  # noqa: E402

DEFAULT_IR = REPO_ROOT / "artifacts" / "recovery_ir.json"
DEFAULT_MAP = REPO_ROOT / "simant" / "facts" / "recovered_map.json"

#: recorded in the entry so the derivation is auditable from the facts alone.
EVIDENCE = ("argmapgen: frame extent derived from the IR record's BP-relative "
            "operands (plain [bp+disp] set == the contiguous word frame the "
            "arity implies, no BP-indexed frame walk); cdecl declaration order "
            "ascending from [bp+{base}], proven per-call against the ASM by "
            "the generated-adapter A/B oracle")


class Refusal(RuntimeError):
    pass


def seg_para_bases(ir_functions: dict) -> dict[int, str]:
    bases: dict[int, str] = {}
    for key, rec in ir_functions.items():
        bases.setdefault(rec["ne_seg"], key.split(":")[0])
    return bases


def bp_operands(rec: dict) -> tuple[set[int], set[int]]:
    """(plain [bp+disp] displacements, BP-indexed [bp+si/di+disp] ones)."""
    plain: set[int] = set()
    indexed: set[int] = set()
    for blk in rec.get("blocks", ()):
        for inst in blk["instructions"]:
            raw = bytes.fromhex(inst["bytes"])
            d = decode_one(lambda i: raw[i] if i < len(raw) else 0, 0)
            if d.modrm is None or d.disp is None:
                continue
            mod, rm = d.modrm >> 6, d.modrm & 7
            if mod not in (1, 2):
                continue          # mod=0 has no disp; mod=0/rm=6 is absolute
            if rm == 6:
                plain.add(d.disp)
            elif rm in (2, 3):
                indexed.add(d.disp)
    return plain, indexed


def close_ret(entry: dict, rec: dict) -> str:
    stated = entry.get("ret")
    exits = set(rec.get("exits", ()))
    ir_ret = "far" if exits == {"retf"} else "near" if exits == {"ret"} else None
    if stated in ("near", "far"):
        if ir_ret is not None and ir_ret != stated:
            raise Refusal(f"ret conflict: map={stated} IR={ir_ret}")
        return stated
    if ir_ret is None:
        raise Refusal(f"ret-unclosable (IR exits {sorted(exits)})")
    return ir_ret


def derive(entry: dict, rec: dict) -> list[int]:
    """The ``[bp+N]`` offsets for ``entry``'s args, or raise ``Refusal``."""
    args = entry.get("args") or []
    if not args:
        raise Refusal("no args to derive")
    if entry.get("arg_map_refused"):
        # A standing, evidence-backed refusal: the entry's ABI slots do not
        # correspond to the recovered implementation's parameters at all, so
        # NO [bp+N] map closes it (writing one would be a false fact).
        raise Refusal(f"arg_map_refused: {entry['arg_map_refused']}")
    ret = close_ret(entry, rec)
    base = 6 if ret == "far" else 4
    plain, indexed = bp_operands(rec)
    if any(d >= base for d in indexed):
        raise Refusal(
            f"BP-indexed frame walk [bp+si/di+{sorted(d for d in indexed if d >= base)}]"
            " — slot boundaries not statically knowable")
    observed = sorted(d for d in plain if d >= base)
    expected = list(range(base, base + 2 * len(args), 2))
    if observed != expected:
        raise Refusal(f"frame extent mismatch: ret={ret} arity={len(args)} "
                      f"expected {expected} observed {observed}")
    return expected


def plan(map_doc: dict, ir_functions: dict):
    """(derived, refused) over every entry with an incomplete arg map."""
    bases = seg_para_bases(ir_functions)
    derived, refused = [], []
    for entry in map_doc["functions"]:
        key = entry.get("key")
        args = entry.get("args") or []
        if key is None or not args:
            continue
        if not any(a.get("bp") is None for a in args):
            continue
        seg_s, off_s = key.split(":")
        rec = ir_functions.get(f"{bases.get(int(seg_s))}:{off_s}")
        if rec is None:
            refused.append((key, entry["symbol"], "no IR record"))
            continue
        try:
            bps = derive(entry, rec)
        except Refusal as exc:
            refused.append((key, entry["symbol"], str(exc)))
            continue
        derived.append((key, entry["symbol"], bps, entry))
    return derived, refused


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="re-derive against the committed map; nonzero exit if "
                         "any derivable entry is still unclosed or disagrees")
    args = ap.parse_args(argv)

    ir_doc = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    map_path = Path(args.map)
    map_doc = json.loads(map_path.read_text(encoding="utf-8"))
    derived, refused = plan(map_doc, ir_doc["functions"])

    if args.check:
        # Everything derivable must already be closed in the committed facts.
        stale = [(k, s, b) for k, s, b, _e in derived]
        if stale:
            for k, s, b in stale:
                print(f"UNCLOSED {k} {s}: derivable as {b}")
            return 1
        print("argmapgen --check: no derivable arg map is left unclosed")
        return 0

    for key, sym, bps, entry in derived:
        base = bps[0]
        for a, bp in zip(entry["args"], bps):
            a["bp"] = bp
        entry["arg_map_evidence"] = EVIDENCE.format(base=base)
        print(f"derived {key:8s} {sym:26s} {[ (a['name'], a['bp']) for a in entry['args'] ]}")
    print(f"\nargmapgen: {len(derived)} arg map(s) derived, "
          f"{len(refused)} refused")
    for key, sym, why in refused:
        print(f"  REFUSED {key:8s} {sym:26s} {why}")
    if not args.dry_run and derived:
        map_path.write_text(json.dumps(map_doc, indent=1) + "\n",
                            encoding="utf-8")
        print(f"\nwrote {map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
