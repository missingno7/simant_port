"""build_vmless_boot_image — SIMANTW's data-only boot image (dos_re_2.0 §1a').

The BUILD-time half of the EXE-independence wall: consumes the original
executable HERE (the only place the strict-VMless pipeline ever reads it),
boots the machine through the normal NE loader to the canonical
post-relocation entry state (instruction zero — an NE loader does all its
work at load time), and captures it as a data-only boot image under
``artifacts/vmless_boot/``:

    memory.bin        the 4MB machine image, recovered code POISONED (every
                      byte the recovery IR decoded as an instruction is
                      zeroed, minus the declared code_as_data jump tables)
    state.json        CPU state + allocator + metadata (vmsnap format)
    system.pickle     the Win16System object graph
    program.pickle    the EXE-free program identity (header + resources;
                      raw bytes stripped)
    manifest.json     provenance, segment map, API slot table, poison
                      accounting, code_as_data, per-segment regions,
                      post-poison memory hash

Everything here regenerates from the EXE + IR + facts + toolchain; the output
is disposable (gitignored).  Verify with scripts/audit_vmless_boot_image.py;
boot with scripts/play_vmless.py.

    python scripts/build_vmless_boot_image.py [--ir artifacts/recovery_ir.json]
                                              [--out artifacts/vmless_boot]
                                              [--no-poison]
"""
from __future__ import annotations

import argparse
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

DEFAULT_IR = REPO_ROOT / "artifacts" / "recovery_ir.json"
DEFAULT_OUT = REPO_ROOT / "artifacts" / "vmless_boot"
FACTS_DIR = REPO_ROOT / "simant" / "facts"


def code_as_data_ranges(seg_bases) -> list[tuple[int, int]]:
    """The code_as_data facts (generated jump tables + hand-verified data
    tables; NE_SEG:HEX_OFF+HEX_LEN per line) as linear (start, length)."""
    ranges: list[tuple[int, int]] = []
    for fname in ("code_as_data.txt", "code_as_data_manual.txt"):
        path = FACTS_DIR / fname
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            seg_s, rest = line.split(":", 1)
            off_s, len_s = rest.split("+", 1)
            para = seg_bases[int(seg_s, 10)]
            ranges.append(((para << 4) + int(off_s, 16), int(len_s, 16)))
    return ranges


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ir", default=str(DEFAULT_IR))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-poison", action="store_true",
                    help="keep the recovered code bytes (DIAGNOSTIC images "
                         "only -- the audit and the strict runner refuse an "
                         "unpoisoned image)")
    args = ap.parse_args(argv)

    from simant.runtime import GAME_NAME, create_machine
    from win16.bootimage import build_boot_image

    machine = create_machine()          # the ONE EXE consumption, build time
    ranges = code_as_data_ranges(machine.seg_bases)

    manifest = build_boot_image(
        machine, args.out,
        ir_path=args.ir,
        code_as_data=ranges,
        game=GAME_NAME,
        note="canonical post-relocation NE entry state (instruction 0)",
        poison=not args.no_poison,
    )
    p = manifest["poison"]
    print(f"boot image -> {args.out}")
    print(f"  source: {manifest['source_exe']['name']} sha256 "
          f"{manifest['source_exe']['sha256'][:16]}...")
    print(f"  poison: {p['poisoned_bytes']} bytes in {p['poisoned_runs']} runs "
          f"(enabled={p['enabled']}), code bytes present after: "
          f"{p['code_bytes_present_after']}")
    print(f"  code_as_data: {len(manifest['code_as_data']['ranges'])} ranges "
          f"(the derived jump tables)")
    print(f"  memory sha256: {manifest['memory_sha256'][:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
