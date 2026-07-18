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
def ir_funcs():
    return json.loads(IR.read_text(encoding="utf-8"))["functions"]


@pytest.fixture(scope="module")
def routed(ir_funcs):
    mp = json.loads(MAP.read_text(encoding="utf-8"))
    facts = adaptgen.load_adapter_facts(FACTS)
    r, _kept = adaptgen.classify(mp, ir_funcs, facts)
    assert r, "no routable manual entries -- the contract source regressed"
    memo: dict = {}
    for c in r:
        c["virtual_time"] = overridegen.virtual_time_of(c, ir_funcs, memo)
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
        "virtual_time": {"kind": "island", "reason": "synthetic"},
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


# --------------------------------------------------------------------------
# VIRTUAL-TIME contracts (cont.248) -- what makes an override gate-admissible.
#
# A composed override returns a `cost` in the compat channel and the caller
# accumulates it, so an override that does not reproduce the ORIGINAL's
# per-invocation instruction count shifts every downstream platform effect and
# desyncs the instruction-count-keyed demo.  overridegen derives that count
# mechanically from the recovery IR CFG; these tests pin the derivation itself
# (against hand-computed CFGs) and pin that only an EXACT contract is offered
# to the gate.
# --------------------------------------------------------------------------

def _synth_ir(blocks):
    """A one-function IR doc from (leader, [(ip, kind, bytes, target)]) blocks."""
    out = []
    for leader, insts in blocks:
        ii = []
        for ip, kind, nbytes, target in insts:
            d = {"ip": f"{ip:04X}", "kind": kind, "mnemonic": kind,
                 "bytes": "90" * nbytes}
            if target is not None:
                d["target"] = f"{target:04X}"
            ii.append(d)
        out.append({"leader": f"{leader:04X}", "instructions": ii})
    return {"blocks": out}


def test_static_cost_counts_the_return_instruction():
    """A straight-line body's cost is its instruction count INCLUDING the ret --
    the same total dos_re's generated twin accumulates (`_cost += count` at the
    RET), which is what a composing caller adds."""
    funcs = {"1000:0000": _synth_ir([(0x0000, [
        (0x0000, "seq", 1, None), (0x0001, "seq", 1, None),
        (0x0002, "retf", 1, None)])])}
    assert overridegen.static_cost("1000:0000", funcs) == 3


def test_equal_branch_arms_are_still_a_constant():
    """A branch whose arms have EQUAL length still costs a constant -- the cost
    is path-INdependent even though the body is not single-path."""
    funcs = {"1000:0000": _synth_ir([
        (0x0000, [(0x0000, "jcc", 2, 0x0004)]),
        (0x0002, [(0x0002, "seq", 1, None), (0x0003, "ret", 1, None)]),
        (0x0004, [(0x0004, "seq", 1, None), (0x0005, "ret", 1, None)]),
    ])}
    assert overridegen.static_cost("1000:0000", funcs) == 3


@pytest.mark.parametrize("blocks,reason", [
    # unequal arms: 1 vs 2 instructions after the jcc
    ([(0x0000, [(0x0000, "jcc", 2, 0x0003)]),
      (0x0002, [(0x0002, "ret", 1, None)]),
      (0x0003, [(0x0003, "seq", 1, None), (0x0004, "ret", 1, None)])],
     "path-dependent"),
    # a back edge: the trip count is data
    ([(0x0000, [(0x0000, "seq", 1, None), (0x0001, "jcc", 2, 0x0000)]),
      (0x0003, [(0x0003, "ret", 1, None)])],
     "loop"),
])
def test_a_path_dependent_body_is_refused_never_approximated(blocks, reason):
    """We never GUESS a cost: a body whose per-invocation count depends on the
    executed path is reported, and stays on its instruction-exact generated
    body."""
    funcs = {"1000:0000": _synth_ir(blocks)}
    with pytest.raises(overridegen.NotStatic) as exc:
        overridegen.static_cost("1000:0000", funcs)
    assert str(exc.value) == reason


def test_a_platform_far_call_is_not_static():
    """The 0060 import thunk dispatches through plat.farcall, whose cost is
    dynamic -- a body reaching one can never carry a static contract."""
    funcs = {"1000:0000": _synth_ir([(0x0000, [
        (0x0000, "call_far", 5, None), (0x0005, "retf", 1, None)])])}
    funcs["1000:0000"]["blocks"][0]["instructions"][0]["far_target"] = \
        ["0060", "0018"]
    with pytest.raises(overridegen.NotStatic) as exc:
        overridegen.static_cost("1000:0000", funcs)
    assert str(exc.value) == "platform-farcall"


def test_a_static_call_adds_the_callee_cost():
    """A call into a statically-costed callee keeps the caller static: the cost
    is the call + the callee's own total + the rest, exactly as the interpreter
    would count it."""
    funcs = {
        "1000:0000": _synth_ir([(0x0000, [
            (0x0000, "call", 3, 0x0100), (0x0003, "ret", 1, None)])]),
        "1000:0100": _synth_ir([(0x0100, [
            (0x0100, "seq", 1, None), (0x0101, "ret", 1, None)])]),
    }
    assert overridegen.static_cost("1000:0100", funcs) == 2
    assert overridegen.static_cost("1000:0000", funcs) == 1 + 2 + 1


def test_the_emitted_body_returns_its_declared_cost(routed):
    """The contract and the body agree: whatever virtual_time declares is
    exactly what the body returns in the compat channel.  A drift between the
    two is invisible to state differentials and would silently shift the
    timeline."""
    for c in routed:
        vt = c["virtual_time"]
        cost = vt.get("cost", 1)
        assert f"'cost': {cost}}}" in overridegen.emit_body(c), c["key"]
        assert overridegen.contract_of(c)["virtual_time"] == vt
        if vt["kind"] == "island":
            assert cost == 1                     # one dispatch step
        else:
            assert vt["kind"] == "static" and cost >= 1


def test_only_exact_contracts_are_offered_to_the_gate(routed, ir_funcs):
    """The gate-admissible set is exactly the overrides whose cost is a derived
    constant -- and it is non-empty (the seam is proven by RUNNING overrides,
    not just by the mechanism)."""
    exact = [c for c in routed if c["virtual_time"]["kind"] != "island"]
    assert exact, "no override carries an exact virtual-time contract"
    for c in exact:
        # re-derive independently: the declared cost IS the IR-derived count
        assert c["virtual_time"]["cost"] == overridegen.static_cost(
            c["para_key"], ir_funcs), c["key"]
