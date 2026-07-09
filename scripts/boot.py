"""Boot SimAnt through the win16 layer and report the frontier.

    python scripts/boot.py [max_steps]

Loads the NE, runs it, and prints how far the interpreter got and what stopped
it (unimplemented API / opcode / DOS service) with CS:IP, the last trace lines,
and the API call log — the honest bring-up report for hardening the win16
layer.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import EXE_PATH, GAME_NAME, create_machine  # noqa: E402
from win16.api.system import Win16System  # noqa: E402


def main() -> None:
    max_steps = int(sys.argv[1]) if len(sys.argv) > 1 else 3_000_000
    if not EXE_PATH.exists():
        raise SystemExit(f"{EXE_PATH} not found")

    machine = create_machine()
    Win16System(machine)
    cpu = machine.cpu
    hdr = machine.exe.header
    print(f"[{GAME_NAME}] {EXE_PATH.name}: {hdr.segment_count} segs, entry "
          f"seg{hdr.entry_seg}:{hdr.entry_ip:04X}, modules "
          f"{', '.join(machine.exe.modules)}")
    print(f"[{GAME_NAME}] segment bases: {[f'{b:04X}' for b in machine.seg_bases[1:]]}, "
          f"osfixup sites {len(machine.osfixups)}")
    cpu.trace_enabled = True
    try:
        steps = cpu.run(max_steps)
        print(f"\n[{GAME_NAME}] ran {steps} steps without stopping; "
              f"at {cpu.s.cs:04X}:{cpu.s.ip:04X}")
    except Exception as exc:  # noqa: BLE001 — the probe reports everything
        print(f"\n[{GAME_NAME}] STOP after {cpu.instruction_count} instructions at "
              f"{cpu.s.cs:04X}:{cpu.s.ip:04X}\n    {type(exc).__name__}: {exc}")
    print("\nlast trace:")
    for line in cpu.trace[-16:]:
        print("   ", line)
    if machine.api.call_log:
        print("\nlast API calls:")
        for line in machine.api.call_log[-24:]:
            print("   ", line)


if __name__ == "__main__":
    main()
