"""checkpoints — a deterministic checkpoint-digest trace of a demo replay.

Replays a demo and fingerprints the GAME-OBSERVABLE state
(win16.vmsnap.digest: memory + CPU + every window surface + clock) at fixed
instruction-count intervals.  Two replays of the SAME demo under the SAME
configuration MUST produce an identical trace — so a saved baseline is a
regression ORACLE: `--check` replays and reports the FIRST checkpoint whose
digest (or instruction count) diverges, pinpointing exactly where a change
altered behaviour, instead of only seeing a far-downstream crash.

    # record a baseline (the current code is the reference)
    python scripts/checkpoints.py cold_nohooks --interval 5000000 --save cold.trace
    # later, after a change, verify nothing drifted:
    python scripts/checkpoints.py cold_nohooks --interval 5000000 --check cold.trace

Notes
* Deterministic and comparable WITHIN one configuration.  A checkpoint is keyed
  by instruction count, which changes when islands are installed — so a baseline
  taken with `--hooks` must be checked with `--hooks`, and a no-hooks baseline
  checked without.  (Cross-config comparison is a different problem: v4 demos are
  instruction-keyed, so hooks and no-hooks reach the same game moments at
  different instruction counts — see the run_status journal.)
* `--hooks` installs the SimAnt islands (default off, matching a no-hooks demo);
  `--from-snapshot DIR` resumes a snapshot-anchored demo.
* `--vmless-graph DIR` installs the FULL VMless lifted graph (scripts/
  liftemit.py + scripts/liftlink.py output) via dos_re's
  `install_vmless_graph` — the DOS_RE 2.0 oracle-guided-convergence
  candidate.  The graph is emitted `count_instructions=True`, so its
  instruction-count timeline is the oracle's; combined with `--api-aligned`
  this makes an interpreted baseline directly comparable to a graph run.
* `--api-aligned` samples checkpoints at API-CALL BOUNDARIES (each import-
  thunk dispatch past the interval) instead of at step-chunk boundaries.
  Step-granular sampling points shift when hooks change work-per-step; API
  dispatches happen at IDENTICAL instruction counts in the oracle and a
  virtual-time-preserving graph, so the digests align exactly.
* Run under **pypy** for speed (a long replay); the digest itself is cheap.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simant.runtime import assets_present, create_machine, install_hooks  # noqa: E402
from win16.demo import DemoDriver, DemoDivergence, DemoEnded  # noqa: E402
from simant.runtime import resolve_demo  # noqa: E402
from win16.vmsnap import digest, load_snapshot  # noqa: E402

VERSION = 1


def compare_traces(base: list[dict], cur: list[dict]):
    """First diverging checkpoint of two traces: (index, kind) where kind is
    'instr' (ran a different distance), 'digest' (same distance, different game
    state) or 'length' (one trace has extra checkpoints); (None, 'match') when
    identical over their common prefix and equal length."""
    n = min(len(base), len(cur))
    for k in range(n):
        if base[k]["instr"] != cur[k]["instr"]:
            return k, "instr"
        if base[k]["digest"] != cur[k]["digest"]:
            return k, "digest"
    if len(base) != len(cur):
        return n, "length"
    return None, "match"


def _machine(args):
    if args.from_snapshot:
        m = load_snapshot(args.from_snapshot, create_machine)
    else:
        m = create_machine()
    m.cpu.trace_enabled = False
    if args.hooks:
        install_hooks(m)
    if args.vmless_graph:
        from dos_re.lift.install import install_vmless_graph
        installed = install_vmless_graph(m.cpu, args.vmless_graph)
        # A lifted call chain mirrors the guest stack on the Python stack
        # (lifted fn -> emulate_call -> step -> hook -> ...): recursive game
        # code (worldgen flood fills) legitimately nests thousands of frames.
        sys.setrecursionlimit(200_000)
        print(f"[checkpoints] VMless graph: {len(installed)} lifted modules "
              f"installed from {args.vmless_graph}")
    return m


def _trace(args) -> tuple[list[dict], str]:
    """Replay the demo, returning the checkpoint list and the end-status."""
    m = _machine(args)
    driver = DemoDriver(resolve_demo(args.demo))
    cpu = m.cpu
    sysobj = m.api.services["system"]
    checkpoints: list[dict] = []
    st = {"i": 0, "next": args.interval}

    def _maybe_cp():
        # Capture when the real instruction_count crosses the next interval.
        if cpu.instruction_count >= st["next"]:
            checkpoints.append({"i": st["i"], "instr": cpu.instruction_count,
                                "digest": digest(m)})
            st["i"] += 1
            st["next"] = (cpu.instruction_count // args.interval + 1) * args.interval

    driver.install(sysobj)
    if args.api_aligned:
        # API-BOUNDARY sampling: wrap every import-thunk dispatch so the
        # digest is taken at an API call whenever the interval has passed.
        # API calls happen at IDENTICAL instruction counts in the oracle and
        # in a count_instructions VMless graph (virtual-time preservation),
        # so baseline and candidate sample the SAME machine moments — unlike
        # step-chunk sampling, whose points shift with work-per-step.
        from win16.loader import THUNK_SEG

        def _wrap(fn):
            def wrapped(cpu2, _fn=fn):
                _maybe_cp()
                _fn(cpu2)
            wrapped.owns_time = getattr(fn, "owns_time", False)
            return wrapped
        for key, fn in list(cpu.replacement_hooks.items()):
            if key[0] == THUNK_SEG:
                cpu.replacement_hooks[key] = _wrap(fn)
    else:
        # The bulk of a session runs inside ONE long WndProc/TimerProc
        # callback, and cpu.run() can't return to checkpoint it — but win16's
        # call_far invokes yield_check every ~8192 instructions DURING a
        # callback.  DemoDriver.install sets yield_check (for its
        # instruction-keyed input); chain a checkpoint check after it so we
        # sample inside long callbacks too.
        driver_yield = sysobj.yield_check

        def _chained_yield():
            if driver_yield is not None:
                driver_yield()
            _maybe_cp()
        sysobj.yield_check = _chained_yield

    # cpu.run(n) counts STEPS: under the VMless graph one hooked step spans a
    # whole lifted function (thousands of instructions), so a 200k-step chunk
    # would overshoot an instruction budget by orders of magnitude.
    chunk = 2_000 if args.vmless_graph else 200_000
    status = "budget reached"
    while cpu.instruction_count < args.budget:
        try:
            cpu.run(chunk)
        except DemoEnded:
            status = "demo ended"
            break
        except DemoDivergence as exc:
            status = f"demo divergence: {exc}"
            break
        except Exception as exc:  # noqa: BLE001 — record where it stopped
            status = f"{type(exc).__name__}: {exc}"
            break
        if not args.api_aligned:
            _maybe_cp()                         # cover non-callback stretches too
    # A final checkpoint at the stop point, whatever it was.
    checkpoints.append({"i": st["i"], "instr": cpu.instruction_count,
                        "digest": digest(m), "final": True})
    return checkpoints, status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo", help="demo replayed as the deterministic drive")
    ap.add_argument("--interval", type=int, default=5_000_000,
                    help="instructions between checkpoints (default 5,000,000)")
    ap.add_argument("--budget", type=int, default=1_000_000_000, help="max instructions")
    ap.add_argument("--hooks", action="store_true", help="install the SimAnt islands")
    ap.add_argument("--vmless-graph", metavar="DIR", default=None,
                    help="install the full VMless lifted graph from DIR "
                         "(dos_re install_vmless_graph; the 2.0 candidate)")
    ap.add_argument("--api-aligned", action="store_true",
                    help="sample checkpoints at API-call boundaries so an "
                         "interpreted baseline and a virtual-time-preserving "
                         "graph run are directly comparable")
    ap.add_argument("--from-snapshot", metavar="DIR", help="resume a snapshot first")
    ap.add_argument("--save", metavar="FILE", help="write the checkpoint trace")
    ap.add_argument("--check", metavar="FILE", help="compare against a saved trace")
    args = ap.parse_args(argv)
    if not assets_present():
        raise SystemExit("assets/ANTWIN/SIMANTW.EXE not found")

    checkpoints, status = _trace(args)
    print(f"[checkpoints] {args.demo}: {len(checkpoints)} checkpoints "
          f"@ every {args.interval:,} instrs, hooks={args.hooks} ({status})")

    if args.check:
        base = json.loads(Path(args.check).read_text())
        if (base.get("interval") != args.interval
                or base.get("hooks") != args.hooks
                or base.get("api_aligned", False) != args.api_aligned):
            print(f"[checkpoints] WARNING: baseline was interval="
                  f"{base.get('interval')} hooks={base.get('hooks')} "
                  f"api_aligned={base.get('api_aligned', False)}, this run "
                  f"is interval={args.interval} hooks={args.hooks} "
                  f"api_aligned={args.api_aligned} — not comparable")
        # The trailing "final" checkpoint marks where each run STOPPED (budget
        # mechanics, error) — it is a stop-point diagnostic, not an aligned
        # sample; compare only the aligned interval checkpoints.
        bcp = [c for c in base["checkpoints"] if not c.get("final")]
        checkpoints = [c for c in checkpoints if not c.get("final")]
        k, kind = compare_traces(bcp, checkpoints)
        if k is None:
            print(f"[checkpoints] MATCH: all {len(bcp)} checkpoints identical "
                  f"to the baseline.")
            return 0
        if kind == "length":
            print(f"[checkpoints] first {k} checkpoints match, but the traces have "
                  f"different lengths (baseline {len(bcp)}, this run "
                  f"{len(checkpoints)}) — the replay ran a different distance.")
            return 1
        b, c = bcp[k], checkpoints[k]
        print(f"[checkpoints] DIVERGED at checkpoint {k} (~instr {c['instr']:,}), "
              f"{kind} mismatch:")
        print(f"    baseline: instr={b['instr']:,} digest={b['digest'][:16]}")
        print(f"    this run: instr={c['instr']:,} digest={c['digest'][:16]}")
        print("    -> a change altered behaviour at/just before this checkpoint.")
        return 1

    if args.save:
        Path(args.save).write_text(json.dumps(
            {"version": VERSION, "demo": Path(args.demo).name,
             "interval": args.interval, "hooks": args.hooks,
             "api_aligned": args.api_aligned,
             "vmless_graph": bool(args.vmless_graph),
             "checkpoints": checkpoints}, indent=0))
        print(f"[checkpoints] saved {len(checkpoints)} checkpoints to {args.save}")
        return 0

    for c in checkpoints:
        print(f"  cp {c['i']:>4}  instr={c['instr']:>13,}  {c['digest'][:24]}"
              f"{'  (final)' if c.get('final') else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
