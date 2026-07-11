"""scripts/checkpoints.py: the deterministic checkpoint-digest trace comparison.

The end-to-end trace needs the real binary + a demo, so it lives as a script you
run.  What is asserted here (no assets): the harness imports, and its trace
comparison correctly finds the FIRST diverging checkpoint by kind (instr /
digest / length) — the logic that turns a saved baseline into a regression
oracle.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "checkpoints.py"


def _load():
    spec = importlib.util.spec_from_file_location("checkpoints", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cp(i, instr, digest):
    return {"i": i, "instr": instr, "digest": digest}


def test_imports_and_exposes_main():
    mod = _load()
    assert callable(mod.main) and callable(mod.compare_traces)


def test_identical_traces_match():
    ct = _load().compare_traces
    a = [_cp(0, 100, "aa"), _cp(1, 200, "bb")]
    assert ct(a, list(a)) == (None, "match")


def test_digest_divergence_pinpoints_first_bad_checkpoint():
    ct = _load().compare_traces
    a = [_cp(0, 100, "aa"), _cp(1, 200, "bb"), _cp(2, 300, "cc")]
    b = [_cp(0, 100, "aa"), _cp(1, 200, "XX"), _cp(2, 300, "cc")]
    assert ct(a, b) == (1, "digest")          # same distance, different state


def test_instr_divergence_ranks_before_digest():
    ct = _load().compare_traces
    a = [_cp(0, 100, "aa"), _cp(1, 200, "bb")]
    b = [_cp(0, 100, "aa"), _cp(1, 999, "ZZ")]
    assert ct(a, b) == (1, "instr")           # ran a different distance


def test_length_mismatch_after_matching_prefix():
    ct = _load().compare_traces
    a = [_cp(0, 100, "aa"), _cp(1, 200, "bb")]
    b = [_cp(0, 100, "aa")]
    assert ct(a, b) == (1, "length")
