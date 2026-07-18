"""The carrier-free CPUless OVERRIDE bodies (scripts/overridegen.py).

The unified override-graph seam (docs/run_status.md cont.247): every hand-
recovered body in ``simant/recovered/`` whose ABI contract is mechanically
closed becomes a DIRECT CPUless override at its own address --

    implementation = manual_overrides.get(addr, generated[addr])

The override body is the CPUless-idiom twin of the PROVEN ``scripts/adaptgen.py``
CPU-carrier adapter: same contract source (recovered_map.json, through the SHARED
:func:`adaptgen.classify`), same ``[bp+N]`` stack-arg math, same view bindings,
same result convention -- but it marshals off the CPUless caller's EXPLICIT state
(the mem image + the register bundle) instead of a ``cpu`` object, so it reaches
no ``dos_re.cpu``/``cpu.s`` and passes ``lint_cpuless``.

These tests pin that equivalence (a marshalling drift between the two is exactly
the class of bug the byte-exact gate would otherwise have to find), and pin the
dos_re override CONTRACT against the body it describes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import simant._env  # noqa: E402,F401
import win16  # noqa: E402,F401

import adaptgen  # noqa: E402
import overridegen  # noqa: E402

IR = REPO_ROOT / "artifacts" / "recovery_ir.json"
MAP = REPO_ROOT / "simant" / "facts" / "recovered_map.json"
FACTS = REPO_ROOT / "simant" / "facts" / "adapter_facts.json"

pytestmark = pytest.mark.skipif(
    not IR.is_file(), reason="recovery IR not built (scripts/irgen.py)")


@pytest.fixture(scope="module")
def routed():
    ir = json.loads(IR.read_text(encoding="utf-8"))
    mp = json.loads(MAP.read_text(encoding="utf-8"))
    facts = adaptgen.load_adapter_facts(FACTS)
    r, _kept = adaptgen.classify(mp, ir["functions"], facts)
    assert r, "no routable manual entries -- the contract source regressed"
    return r


class _Mem:
    """A minimal duck-typed mem image (the CPUless convention's ``mem``)."""

    def __init__(self, size=1 << 20):
        self.data = bytearray(size)

    def _lin(self, seg, off):
        return ((seg << 4) + (off & 0xFFFF)) % len(self.data)

    def rb(self, seg, off):
        return self.data[self._lin(seg, off)]

    def wb(self, seg, off, v):
        self.data[self._lin(seg, off)] = v & 0xFF

    def rw(self, seg, off):
        a = self._lin(seg, off)
        return self.data[a] | (self.data[a + 1] << 8)

    def ww(self, seg, off, v):
        a = self._lin(seg, off)
        self.data[a] = v & 0xFF
        self.data[a + 1] = (v >> 8) & 0xFF


def _load_body(src: str, stem: str, tmp_path: Path):
    path = tmp_path / f"{stem}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, stem)


def test_override_set_is_exactly_adaptgen_routed(routed):
    """The override set IS adaptgen's routed set -- one classifier, so the
    carrier-free and carrier-bound flavors can never disagree on WHICH entries
    have a mechanically closed contract."""
    contracts = {c["para_key"]: overridegen.contract_of(c) for c in routed}
    assert len(contracts) == len(routed)
    for c in routed:
        got = contracts[c["para_key"]]
        assert got["ret_kind"] == c["ret"]
        assert got["name"] == f"func_{c['para_key'].replace(':', '_').lower()}"
        # the result convention maps 1:1 onto the CPUless output set
        assert got["outputs"] == {"ax": ["ax"], "dxax": ["ax", "dx"],
                                  "none": []}[c["result"]]


def test_contract_inputs_match_the_body_signature(routed, tmp_path):
    """Every register the dos_re contract declares as an input is a keyword the
    emitted body accepts -- a mismatch would make the composed caller pass an
    argument the body cannot receive (a TypeError deep in the graph)."""
    import inspect
    for c in routed[:40]:
        stem = f"func_{c['para_key'].replace(':', '_').lower()}"
        fn = _load_body(overridegen.emit_body(c), stem, tmp_path)
        params = set(inspect.signature(fn).parameters) - {"mem"}
        assert set(overridegen.contract_of(c)["inputs"]) == params, c["key"]


def test_body_reads_stack_args_at_the_callee_entry_frame(tmp_path):
    """``[bp+N]`` at the historical hook entry is ``[sp+N-2]`` at CPUless callee
    entry (ss:sp points at the return frame, before the prologue's ``push bp``).

    This is THE marshalling invariant the whole override seam rests on -- it is
    the same arithmetic scripts/adaptgen.py proved against the ASM oracle, so it
    is pinned here directly rather than trusted.
    """
    # a synthetic far entry taking two words at [bp+6] and [bp+8]
    seen = {}

    def _impl(view, x, y):
        seen["args"] = (x, y)
        return 0x4321

    c = {
        "key": "5:0000", "para_key": "2F99:0000", "symbol": "_Synthetic",
        "impl": "simant.tests.test_cpuless_override._IMPL",
        "ret": "far", "views": ["dgroup"],
        "args": [("x", 6), ("y", 8)], "result": "ax",
    }
    src = overridegen.emit_body(c).replace(
        "from simant.tests.test_cpuless_override import _IMPL as _impl",
        "_impl = None")
    fn = _load_body(src, "func_2f99_0000", tmp_path)
    fn.__globals__["_impl"] = _impl

    mem = _Mem()
    ss, sp, ds = 0x3000, 0x0100, 0x2000
    # the caller pushed y then x, then the far return frame (off, cs):
    #   [sp+0]=ret off  [sp+2]=ret cs  [sp+4]=x  [sp+6]=y
    mem.ww(ss, sp, 0xBEEF)          # return offset
    mem.ww(ss, sp + 2, 0x1234)      # return cs
    mem.ww(ss, sp + 4, 0x0007)      # x  ([bp+6] -> [sp+4])
    mem.ww(ss, sp + 6, 0xFFFE)      # y  ([bp+8] -> [sp+6]), negative
    out, compat = fn(mem, ds=ds, ss=ss, sp=sp)

    assert seen["args"] == (7, -2), "stack args mis-marshalled"   # y sign-extends
    assert out == {"ax": 0x4321}
    assert compat == {"flags": 0, "fmask": 0, "cost": 1}


def test_every_override_body_compiles(routed, tmp_path):
    """Every emitted override body is syntactically valid Python and defines the
    contract's function name (the name dos_re's composed callers call)."""
    for c in routed:
        stem = f"func_{c['para_key'].replace(':', '_').lower()}"
        src = overridegen.emit_body(c)
        compile(src, stem, "exec")
        assert f"def {stem}(mem" in src
        # the CODE (everything past the module docstring; the docstring itself
        # documents the wall) must never touch a cpu carrier.
        code = src.split("from __future__ import annotations", 1)[1]
        assert "cpu" not in code, \
            f"{c['key']}: an override body must never mention a cpu carrier"


def test_override_bodies_reach_no_cpu_carrier(routed):
    """The carrier-free wall, at the source level: an override body imports only
    the pure recovered corpus (simant.recovered / simant.bridge) -- never
    dos_re.cpu, never simant.lifted."""
    for c in routed:
        src = overridegen.emit_body(c)
        for line in src.splitlines():
            if line.startswith(("import ", "from ")):
                assert "dos_re" not in line and "simant.lifted" not in line, \
                    f"{c['key']}: {line}"
