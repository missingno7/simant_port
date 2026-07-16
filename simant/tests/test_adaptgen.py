"""M2b adapter routing (scripts/adaptgen.py) — policy + generated-adapter A/B.

Three tiers:

1. ROUTING POLICY pinned pure: the classifier's routed/kept split over the real
   recovered_map + recovery IR — gated entries stay literal, presentation-
   effect subtrees stay literal, contract conflicts fail loud.
2. EMISSION shape: the generated marshalling for each ABI template path
   (near/far frame pop, [bp+N] -> [sp+N-2], AX/none results).
3. GENERATED-ADAPTER A/B oracle: for entries covering the ABI shape matrix,
   run the ORIGINAL ASM and the generated adapter from an identical machine
   state and require identical results — the ABI contract (return frame,
   callee-saved registers, result registers) plus byte-identical data
   segments (DGROUP sim band, SIMANT_DATA_GROUP, PACK).  This is the same
   oracle shape the islands' tests use, applied to GENERATED marshalling.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import simant.hooks as hooks
from simant import runtime

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "adaptgen.py"

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="SimAnt assets not present")

SENT_CS, SENT_IP = 0xDEAD, 0xBEEF


def _load_script():
    spec = importlib.util.spec_from_file_location("simant_adaptgen", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def adaptgen():
    return _load_script()


@pytest.fixture(scope="module")
def classified(adaptgen):
    ir_doc = json.loads((REPO_ROOT / "artifacts" / "recovery_ir.json")
                        .read_text(encoding="utf-8"))
    map_doc = json.loads(
        (REPO_ROOT / "simant" / "facts" / "recovered_map.json")
        .read_text(encoding="utf-8"))
    facts_doc = adaptgen.load_adapter_facts(
        REPO_ROOT / "simant" / "facts" / "adapter_facts.json")
    routed, kept = adaptgen.classify(map_doc, ir_doc["functions"], facts_doc)
    return routed, kept


# --- 1. routing policy --------------------------------------------------------

def test_routed_and_kept_partition_the_matched_corpus(classified):
    routed, kept = classified
    assert len(routed) + len(kept) == 309          # the inventory's matched set
    assert len(routed) == 166                      # the M2b routed corpus
    keys = [c["key"] for c in routed] + [k["key"] for k in kept]
    assert len(keys) == len(set(keys))             # one decision per entry


def test_every_routed_contract_is_complete(classified):
    routed, _ = classified
    for c in routed:
        assert c["ret"] in ("near", "far"), c
        assert c["result"] in ("none", "ax", "dxax"), c
        base = 4 if c["ret"] == "near" else 6
        bps = [bp for _n, bp in c["args"]]
        assert bps == list(range(base, base + 2 * len(bps), 2)), c
        assert all(v in ("dgroup", "simant_data_group", "pack")
                   for v in c["views"]), c


@pytest.mark.parametrize("key,reason_prefix", [
    ("6:1E42", "status:proven-gated"),         # _DoForageAnt — gate policy (a)
    ("6:0B76", "presentation-effects"),        # _DoRestAnt fires _RestBalloons
    ("5:617A", "views:view"),                  # _SetMap zaps the map redraw
    ("4:08D4", "callee-cleans"),               # __aFldiv (ret far 8, dwords)
    ("7:C2D2", "callback-injected"),           # _win_GetObjRect render tier
    ("5:1122", "args-incomplete"),             # _GetDis island-only marshalling
    ("7:65CE", "fact-excluded"),               # _PlaceBlackQueen live divergence
])
def test_kept_literal_reasons(classified, key, reason_prefix):
    _, kept = classified
    entry = next((k for k in kept if k["key"] == key), None)
    assert entry is not None, f"{key} was routed but must stay literal"
    assert any(r.startswith(reason_prefix) for r in entry["reasons"]), entry


def test_ret_conflict_between_map_and_ir_fails_loud(adaptgen):
    entry = {"key": "5:0000", "symbol": "_X", "ret": "near"}
    rec = {"exits": ["retf"]}
    with pytest.raises(adaptgen.ContractError):
        adaptgen.close_ret(entry, rec)


# --- 2. emission shape ---------------------------------------------------------

def _contract(**kw):
    base = dict(key="5:0ACC", para_key="2F99:0ACC", cs=0x2F99, ip=0x0ACC,
                symbol="_X", impl="simant.recovered.gameplay.place_drop",
                ret="far", views=["dgroup"], args=[("slot", 6)],
                result="none", signature="c8040000", facts_used=[])
    base.update(kw)
    return base


def test_far_adapter_pops_the_far_frame_and_reads_bp6_at_sp4(adaptgen):
    src = adaptgen.emit_adapter(_contract(result="ax"), "stem_x")
    assert "ret_cs = m.rw(ss, (sp + 2) & 0xFFFF)" in src
    assert "(sp + 4) & 0xFFFF))   # slot=[bp+6]" in src
    assert "s.sp = (sp + 4) & 0xFFFF" in src
    assert "s.cs = ret_cs" in src
    assert "s.ax = _impl(" in src
    assert "AUTOGENERATED" in src


def test_near_adapter_pops_only_ip_and_reads_bp4_at_sp2(adaptgen):
    src = adaptgen.emit_adapter(
        _contract(ret="near", args=[("slot", 4)]), "stem_x")
    assert "ret_cs" not in src
    assert "(sp + 2) & 0xFFFF))   # slot=[bp+4]" in src
    assert "s.sp = (sp + 2) & 0xFFFF" in src
    assert "s.ip = ret_ip" in src


# --- 3. generated-adapter A/B oracle -------------------------------------------

DGROUP_SIZE = 0x10000
SIM_HI = 0xE000                    # sim state band; the stack lives above


def _run_asm(entry_key, args, *, near, seed_fn=None):
    """Interpret the ORIGINAL routine to the sentinel; returns the machine."""
    seg, off = entry_key.split(":")
    seg, off = int(seg), int(off, 16)
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if seed_fn is not None:
        seed_fn(m)
    s = m.cpu.s
    s.ds = m.seg_bases[hooks.DG_SEG_INDEX]
    s.sp = 0xFF00
    s.cs, s.ip = m.seg_bases[seg], off
    sp = s.sp
    tail = (SENT_IP,) if near else (SENT_CS, SENT_IP)
    for v in (*reversed(args), *tail):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    target = (s.cs & 0xFFFF, SENT_IP) if near else (SENT_CS, SENT_IP)
    for _ in range(300_000):
        m.cpu.step()
        if (s.cs & 0xFFFF, s.ip & 0xFFFF) == target:
            break
    else:
        raise AssertionError(f"ASM {entry_key} did not return")
    return m


def _run_adapter(adaptgen, contract, args, *, near, seed_fn=None, tmp_path):
    """Emit the adapter, install it as the replacement hook, dispatch ONE step
    (the exact runtime path install_vmless_graph uses); returns the machine."""
    src = adaptgen.emit_adapter(contract, "gen_adapter_under_test")
    path = tmp_path / "gen_adapter_under_test.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = mod.gen_adapter_under_test

    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    if seed_fn is not None:
        seed_fn(m)
    s = m.cpu.s
    s.ds = m.seg_bases[hooks.DG_SEG_INDEX]
    s.sp = 0xFF00
    s.cs, s.ip = contract["cs"], contract["ip"]
    sp = s.sp
    tail = (SENT_IP,) if near else (SENT_CS, SENT_IP)
    for v in (*reversed(args), *tail):
        sp = (sp - 2) & 0xFFFF
        m.mem.ww(s.ss, sp, v & 0xFFFF)
    s.sp = sp
    m.cpu.replacement_hooks[(contract["cs"], contract["ip"])] = fn
    m.cpu.step()                       # one dispatch runs the whole adapter
    target = (contract["cs"], SENT_IP) if near else (SENT_CS, SENT_IP)
    assert (s.cs & 0xFFFF, s.ip & 0xFFFF) == target
    return m


def _assert_contract_equal(asm, adp, contract):
    a, b = asm.cpu.s, adp.cpu.s
    assert (b.sp & 0xFFFF) == (a.sp & 0xFFFF)
    for reg in ("si", "di", "bp", "ds", "ss"):
        assert (getattr(b, reg) & 0xFFFF) == (getattr(a, reg) & 0xFFFF), reg
    if contract["result"] in ("ax", "dxax"):
        assert (b.ax & 0xFFFF) == (a.ax & 0xFFFF)
    if contract["result"] == "dxax":
        assert (b.dx & 0xFFFF) == (a.dx & 0xFFFF)
    for seg_i, hi in ((hooks.DG_SEG_INDEX, SIM_HI),
                      (hooks.SIMANT_DATA_GROUP_SEG_INDEX, DGROUP_SIZE),
                      (hooks.PACK_SEG_INDEX, DGROUP_SIZE)):
        asm_bytes = bytes(asm.mem.block(asm.seg_bases[seg_i], 0, hi))
        adp_bytes = bytes(adp.mem.block(adp.seg_bases[seg_i], 0, hi))
        assert asm_bytes == adp_bytes, f"seg{seg_i} state differs"


def _routed_contract(classified, key):
    routed, _ = classified
    c = next((c for c in routed if c["key"] == key), None)
    assert c is not None, f"{key} is not in the routed set"
    return c


def _seed_rng(value):
    def seed(m):
        m.mem.ww(m.seg_bases[hooks.DG_SEG_INDEX], 0xCBF2, value)
    return seed


@pytest.mark.parametrize("key,args,seed", [
    ("5:14A4", (128,), _seed_rng(0x3131)),   # _SGRand   far, arg, AX, dgroup
    ("5:14CC", (8,),   _seed_rng(0xBEEF)),   # _SGSRand  far, arg, AX (sign path)
    ("5:2A16", (),     None),                # _CompactListA  far, no args, pack+sdg
    ("5:30E8", (),     None),                # _ClearListB    far, no args
    ("5:1CBA", (3,),   None),                # _CanBeHouseHole far, scalar-only, AX
    ("7:01CC", (),     None),                # _GstrB    far, no args, AX
])
def test_generated_far_adapters_match_asm(classified, adaptgen, tmp_path,
                                          key, args, seed):
    c = _routed_contract(classified, key)
    asm = _run_asm(key, args, near=False, seed_fn=seed)
    adp = _run_adapter(adaptgen, c, args, near=False, seed_fn=seed,
                       tmp_path=tmp_path)
    _assert_contract_equal(asm, adp, c)


@pytest.mark.parametrize("key,args,seed", [
    ("6:92AA", (), None),                    # _ColonySmellBN near, no args
    ("6:9306", (), None),                    # _ColonySmellBT near, no args
    ("6:95B6", (0, 5, 5), None),             # _DecTSmell near, 3 args
    ("6:2A22", (2,), _seed_rng(0x1234)),     # _RandTurn near, arg, AX
])
def test_generated_near_adapters_match_asm(classified, adaptgen, tmp_path,
                                           key, args, seed):
    c = _routed_contract(classified, key)
    asm = _run_asm(key, args, near=True, seed_fn=seed)
    adp = _run_adapter(adaptgen, c, args, near=True, seed_fn=seed,
                       tmp_path=tmp_path)
    _assert_contract_equal(asm, adp, c)
