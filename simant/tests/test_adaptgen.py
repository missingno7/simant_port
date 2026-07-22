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
    assert len(routed) == 201                      # the M2b routed corpus
    keys = [c["key"] for c in routed] + [k["key"] for k in kept]
    assert len(keys) == len(set(keys))             # one decision per entry


def test_every_routed_contract_is_complete(classified):
    routed, _ = classified
    for c in routed:
        assert c["ret"] in ("near", "far"), c
        assert c["result"] in ("none", "ax", "dxax", "tuple_ax_dx"), c
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
    ("5:9342", "args-incomplete"),             # _TileCanBeMovedOn: 8 named
                                               # args, 7 stack slots
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
    (the exact runtime path activate_generated_graph uses); returns the machine."""
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
    if contract["result"] in ("ax", "dxax", "tuple_ax_dx"):
        assert (b.ax & 0xFFFF) == (a.ax & 0xFFFF)
    if contract["result"] in ("dxax", "tuple_ax_dx"):
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


# --- 4. cont.250 — the DERIVED arg maps, proven per-call against the ASM -------
#
# scripts/argmapgen.py closes the `args-incomplete` contracts mechanically (the
# frame EXTENT falls out of the IR record's BP-relative operands).  What it can
# NOT derive is the assignment of names to slots — that is the MSC cdecl order
# the whole corpus runs on.  These tests are that assignment's proof: every
# contract closed in cont.250 is run against the ORIGINAL ASM from an identical
# pre-state over three arg vectors, one of them all-distinct, so a transposed
# pair changes the result register or the data segments and the A/B diverges.

#: the 35 contracts cont.250 closed (34 derived arg maps + _FlipLong's result
#: convention).  Pinned by key so a regression names the entry it broke.
CONT250_CLOSED = [
    "4:7356", "4:7360", "5:10CC", "5:1122", "5:115C", "5:1182", "5:1B06",
    "5:1D02", "5:26C4", "5:56BA", "5:56DA", "5:5720", "5:5EC8", "5:5EE4",
    "5:5F32", "5:5F64", "5:8C70", "5:94C6", "5:9C02", "5:9C26", "6:0A1C",
    "6:0A74", "6:1480", "6:28C0", "6:2CC0", "6:42B0", "6:6762", "6:8D3A",
    "6:943C", "6:947E", "6:94B6", "6:94F6", "6:9536", "6:9576", "7:2072",
]

#: arg vectors every closed contract is A/B'd over.  The first is ALL-DISTINCT
#: and ascending — the vector that discriminates arg ORDER.
AB_ARG_VECTORS = ((3, 5, 7, 9, 11, 13, 15, 17),
                  (1, 2, 3, 4, 5, 6, 7, 8),
                  (0, 0, 0, 0, 0, 0, 0, 0))


@pytest.mark.parametrize("key", CONT250_CLOSED)
def test_cont250_closed_contract_matches_asm(classified, adaptgen, tmp_path, key):
    c = _routed_contract(classified, key)
    near = c["ret"] == "near"
    for vec in AB_ARG_VECTORS:
        args = vec[:len(c["args"])]
        asm = _run_asm(key, args, near=near)
        adp = _run_adapter(adaptgen, c, args, near=near, tmp_path=tmp_path)
        _assert_contract_equal(asm, adp, c)


def test_no_derivable_arg_map_is_left_unclosed():
    """The committed facts are REPRODUCIBLE: re-deriving finds nothing new."""
    import subprocess
    import sys as _sys
    proc = subprocess.run(
        [_sys.executable, str(REPO_ROOT / "scripts" / "argmapgen.py"), "--check"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_argmapgen_refuses_an_unpinned_frame_extent():
    """A body that does not read every named arg leaves the extent unpinned —
    the map stays null rather than being guessed (5:9342 _TileCanBeMovedOn
    names 8 args but touches only 7 stack slots)."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("simant_argmapgen",
                                        REPO_ROOT / "scripts" / "argmapgen.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ir_doc = json.loads((REPO_ROOT / "artifacts" / "recovery_ir.json")
                        .read_text(encoding="utf-8"))
    map_doc = json.loads(
        (REPO_ROOT / "simant" / "facts" / "recovered_map.json")
        .read_text(encoding="utf-8"))
    _derived, refused = mod.plan(map_doc, ir_doc["functions"])
    by_key = {k: why for k, _s, why in refused}
    assert "frame extent mismatch" in by_key["5:9342"]
    # and a standing evidence-backed refusal is honoured, never re-derived
    assert "arg_map_refused" in by_key["5:5B2C"]
