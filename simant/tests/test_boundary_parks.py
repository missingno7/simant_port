"""Boundary parks are TRANSPARENT: parking changes when, never what.

A park (win16.interactive) unwinds the lifted Python chain at a fact-declared
head and resumes at the head's RESUME entry.  The claim that makes it safe is
that this is pure control flow — the same instructions execute, in the same
order, with the same state — so the deterministic demo, replayed on the
strict graph with parks ARMED AT EVERY HEAD, must reach the same instruction
count and the same digest as the park-free pin.

That is stronger than the whole-demo differential alone (which proves the
EMISSION with observers is byte-identical to the interpreted oracle); this
proves the PARK/RESUME path itself, including the frame driver the game lives
inside (the demo plays in-game, so MYTIMERFUNC's frame gate parks here).

The park costs are stripped to zero: real sleeps would only slow the test,
and the cost is a pacing policy, not semantics (win16.interactive
FRAME_GATE / PACING_SPIN).  Recipe/pins are single-sourced from
test_vmless_walls.py (the clean-room 45M-instruction prefix).
"""
import json
import sys

import pytest

from simant import vmless_boot as vb
from simant.tests.test_vmless_walls import (DEMO, PREFIX_DIGEST,
                                            PREFIX_END_INSTR, PREFIX_LIMIT)
from win16.demo import DemoDriver, DemoEnded
from win16.interactive import BoundaryParked
from win16.vmsnap import digest

pytestmark = pytest.mark.skipif(
    not (vb.BOOT_DIR / "manifest.json").exists()
    or not (vb.LIFT_DIR / "graph_manifest.json").exists()
    or not DEMO.exists(),
    reason="the boot image / lifted graph / demo is not generated")


def test_parks_are_transparent_over_the_demo_prefix(tmp_path):
    """The pinned prefix, replayed with EVERY head parking: identical
    instruction count AND digest to the park-free pin."""
    machine, _manifest, installed = vb.boot_strict(vb.BOOT_DIR,
                                                   lift_dir=vb.LIFT_DIR,
                                                   game_root=None)
    sys.setrecursionlimit(200_000)
    cpu = machine.cpu
    cpu.trace_enabled = False
    parks = {"n": 0, "heads": set()}

    def hook(cpu2, head_cs, head_ip, resume_ip):
        # The park, cost-free: re-point at the RESUME entry and unwind.
        parks["n"] += 1
        parks["heads"].add((head_cs, head_ip))
        cpu2.s.cs, cpu2.s.ip = head_cs & 0xFFFF, resume_ip & 0xFFFF
        raise BoundaryParked(head_cs, head_ip, resume_ip)

    cpu.boundary_hook = hook

    src = DEMO.read_text().splitlines()
    kept = [src[0]] + [ln for ln in src[1:]
                       if json.loads(ln).get("i", 0) <= PREFIX_LIMIT]
    demo = tmp_path / "prefix.jsonl"
    demo.write_text("\n".join(kept) + "\n")

    driver = DemoDriver(demo)
    driver.install(machine.api.services["system"])
    while True:
        try:
            cpu.run(2_000)
        except BoundaryParked:
            continue                # the CPU worker's yield: keep stepping
        except DemoEnded:
            break

    assert parks["n"] > 0, "no head parked -- are the observers emitted?"
    assert cpu.instruction_count == PREFIX_END_INSTR, (
        f"parking changed the instruction timeline: "
        f"{cpu.instruction_count:,} != {PREFIX_END_INSTR:,}")
    assert digest(machine) == PREFIX_DIGEST, \
        "parking changed the game-observable state"


def test_frame_gate_head_is_priced_by_the_generated_policy():
    """The graph ships its own park policy (scripts/liftemit.py generates it
    from the facts) and the sim frame driver's head is a frame gate — the
    pacing contract the interactive host reads."""
    from win16.interactive import FRAME_GATE, PACING_SPIN
    kinds = vb.boundary_park_kinds(vb.LIFT_DIR)
    assert kinds.get((0x0100, 0x25B6)) == FRAME_GATE, \
        "MYTIMERFUNC's frame-loop head must be priced frame_gate"
    assert set(kinds.values()) <= {FRAME_GATE, PACING_SPIN}
    # MYTIMERFUNC's timer DRAIN loop must NOT be a frame gate: waiting for
    # the next timer to be due would hand it a message every pass and the
    # drain would never end (see simant/facts/boundary_heads.txt).
    assert kinds.get((0x0100, 0x27BF), PACING_SPIN) == PACING_SPIN
