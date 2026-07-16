"""scripts/liftemit.py + scripts/liftlink.py — the VMless-graph pipeline, pinned.

Two tiers:

* the SYMBOLIC NAMING POLICY is pure and pinned without assets: .SYM identity
  becomes module/function stems (``SIMONE_MODULE!_SRand1`` ->
  ``simone_srand1``), deterministically, with address suffixes on collisions
  and identifier hygiene on hostile names ($-prefixed CRT symbols, keywords);
* a MINIATURE of the emit half runs on real code (a two-entry IR): symbolic
  modules + ``graph_manifest.json`` land in the emit dir, dos_re's
  ``install_vmless_graph`` resolves the manifest and registers the hooks
  under their symbolic names, and the miniature corpus holds the VMless wall
  (no ``interp_one`` call site).

The full-corpus emit/link/convergence gates are the runners' job
(docs/run_status.md cont.222); these tests pin the conventions cheaply.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from simant.runtime import assets_present

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"simant_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the naming policy (pure — no assets) ----------------------------------------

def _rec(module, symbol, entry="2F99:0100", ne_seg=5, liftable=True):
    return {"module": module, "symbol": symbol, "entry": entry,
            "ne_seg": ne_seg, "liftable": liftable}


def test_stem_policy_symbolic_examples():
    liftemit = _load_script("liftemit")
    assert liftemit.stem_for(_rec("SIMONE_MODULE", "_SRand1")) == "simone_srand1"
    assert liftemit.stem_for(_rec("_TEXT", "__aFlmul", ne_seg=4)) == "text_aflmul"
    assert liftemit.stem_for(_rec("_TEXT", "$I10_OUTPUT", ne_seg=4)) == "text_i10_output"
    assert liftemit.stem_for(_rec("SIMANT1_MODULE", "_DoRedInitiator",
                                  ne_seg=6)) == "simant1_doredinitiator"


def test_stem_collisions_get_address_suffixes_deterministically():
    liftemit = _load_script("liftemit")
    functions = {
        "0E99:0100": _rec("GR_MODULE", "_MEM_Free", "0E99:0100", 2),
        "0E99:0200": _rec("GR_MODULE", "_mem_free", "0E99:0200", 2),
        "0E99:0300": _rec("GR_MODULE", "_mem_Lock", "0E99:0300", 2),
    }
    naming = liftemit.build_naming(functions)
    # Both claimants of "gr_mem_free" carry their address; the unique one not.
    assert naming.stem(0x0E99, 0x0100) == "gr_mem_free_0100"
    assert naming.stem(0x0E99, 0x0200) == "gr_mem_free_0200"
    assert naming.stem(0x0E99, 0x0300) == "gr_mem_lock"
    # Deterministic under re-run and insertion order.
    again = liftemit.build_naming(dict(reversed(list(functions.items()))))
    assert again.mapping == naming.mapping


def test_stems_are_identifiers_for_the_whole_real_corpus():
    # The real SIMANTW corpus (if the IR artifact is present): every liftable
    # record gets a unique, valid-identifier stem — GraphNaming validates on
    # construction, so building it IS the assertion.
    ir = REPO_ROOT / "artifacts" / "recovery_ir.json"
    if not ir.exists():
        pytest.skip("artifacts/recovery_ir.json not generated")
    liftemit = _load_script("liftemit")
    doc = json.loads(ir.read_text(encoding="utf-8"))
    naming = liftemit.build_naming(doc["functions"])
    n_liftable = sum(1 for r in doc["functions"].values() if r.get("liftable"))
    assert len(naming.mapping) == n_liftable


# --- the miniature emit (real code) ----------------------------------------------

@pytest.mark.skipif(not assets_present(), reason="SimAnt assets not present")
def test_miniature_graph_emits_symbolically_and_installs(tmp_path):
    from simant.runtime import create_machine
    from win16.irgen import build_ir

    irgen = _load_script("irgen")
    liftemit = _load_script("liftemit")
    dosre_liftemit = liftemit.load_dosre_tool("liftemit")

    machine = create_machine()
    machine.cpu.trace_enabled = False
    _entries, names, _ = irgen.sym_corpus()
    sample = [(5, 0x158A), (2, 0x0000)]          # _SRand1 + _SetHelpCursor
    doc = build_ir(machine, sample, machine_factory=None,
                   names={k: names[k] for k in sample},
                   symbols="SIMANTW.SYM sha1=test")

    naming = liftemit.build_naming(doc["functions"])
    naming.save(tmp_path)
    for entry in sorted(doc["functions"]):
        status, detail = dosre_liftemit.emit_entry_from_ir(
            doc["functions"][entry], tmp_path, None,
            stem=naming.stem_of(entry))
        assert status == "ok", (entry, status, detail)

    assert (tmp_path / "simone_srand1.py").is_file()
    assert (tmp_path / "graph_manifest.json").is_file()
    # The miniature corpus holds the VMless wall.
    assert dosre_liftemit.vmless_wall_report(tmp_path) == {}

    class FakeCPU:
        def __init__(self):
            self.replacement_hooks = {}
            self.hook_names = {}

    from dos_re.lift.install import install_vmless_graph
    cpu = FakeCPU()
    installed = install_vmless_graph(cpu, tmp_path)
    assert installed[(0x2F99, 0x158A)] == "simone_srand1.py"
    assert cpu.hook_names[(0x2F99, 0x158A)] == "simone_srand1"
    # Provenance stays inside the module: the paragraph-base ENTRY constant.
    src = (tmp_path / "simone_srand1.py").read_text(encoding="utf-8")
    assert "ENTRY = (0x2F99, 0x158A)" in src
