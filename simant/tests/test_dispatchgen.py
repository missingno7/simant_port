"""scripts/dispatchgen.py — mechanical switch-table derivation, pinned pure.

Synthetic IR records for the three proven MSC guard families (F1/F2/F3), the
fail-loud STOP behaviour for everything else, and the regeneration-stability
rule (a target whose census record EXISTS because a previous run declared it
must be re-emitted — the facts file is the source of those records)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "dispatchgen.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("simant_dispatchgen", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dispatchgen = _load_script()


def _rec(insts):
    return {"blocks": [{"instructions": insts}],
            "ne_seg": 1, "symbol": "_T", "liftable": True,
            "exits": sorted({i["kind"] for i in insts
                             if i["kind"] in ("jmp_ind", "ret", "retf")})}


def _i(ip, kind, bytes_, mnemonic="x", target=None):
    d = {"ip": f"{ip:04X}", "kind": kind, "bytes": bytes_, "mnemonic": mnemonic}
    if target is not None:
        d["target"] = f"{target:04X}"
    return d


def _table_fetch(table_off, words):
    data = {}
    for k, w in enumerate(words):
        data[table_off + 2 * k] = w & 0xFF
        data[table_off + 2 * k + 1] = (w >> 8) & 0xFF
    return lambda off: data.get(off, 0xFF)


def test_f1_cmp_ja_shl_xchg_proves_bound_and_reads_table():
    # cmp ax,2 / ja DEFAULT / shl ax,1 / xchg ax,bx / jmp cs:[bx+0200]
    insts = [
        _i(0x0100, "seq", "3d0200", "cmp"),
        _i(0x0103, "jcc", "7708", "ja", target=0x010D),
        _i(0x0105, "seq", "d1e0", "shift"),
        _i(0x0107, "seq", "93", "xchg ax,r16"),
        _i(0x0108, "jmp_ind", "2effa70002", "jmp rm16"),
        _i(0x010D, "ret", "c3", "ret"),
    ]
    rec = _rec(insts)
    got = dispatchgen.derive_site(rec, dispatchgen._inst_map(rec), 0x0108,
                                  _table_fetch(0x0200, [0x1111, 0x2222, 0x3333]))
    assert got["family"] == "F1"
    assert got["table"] == 0x0200 and got["slots"] == 3
    assert got["targets"] == [0x1111, 0x2222, 0x3333]


def test_f2_inverted_guard_jbe_jmp_default():
    # cmp ax,1 / jbe +3 / jmp DEFAULT / shl ax,1 / xchg ax,bx / jmp cs:[bx+0300]
    insts = [
        _i(0x0100, "seq", "3d0100", "cmp"),
        _i(0x0103, "jcc", "7603", "jbe", target=0x0108),
        _i(0x0105, "jmp", "e91000", "jmp", target=0x0118),
        _i(0x0108, "seq", "d1e0", "shift"),
        _i(0x010A, "seq", "93", "xchg ax,r16"),
        _i(0x010B, "jmp_ind", "2effa70003", "jmp rm16"),
        _i(0x0118, "ret", "c3", "ret"),
    ]
    rec = _rec(insts)
    got = dispatchgen.derive_site(rec, dispatchgen._inst_map(rec), 0x010B,
                                  _table_fetch(0x0300, [0xAAAA, 0xBBBB]))
    assert got["family"] == "F2" and got["slots"] == 2
    assert got["targets"] == [0xAAAA, 0xBBBB]


def test_f3_scaled_compare_without_shl_halves_the_bound():
    # cmp ax,4 (already a byte offset) / ja DEFAULT / xchg / jmp cs:[bx+0400]
    insts = [
        _i(0x0100, "seq", "3d0400", "cmp"),
        _i(0x0103, "jcc", "7706", "ja", target=0x010B),
        _i(0x0105, "seq", "93", "xchg ax,r16"),
        _i(0x0106, "jmp_ind", "2effa70004", "jmp rm16"),
        _i(0x010B, "ret", "c3", "ret"),
    ]
    rec = _rec(insts)
    got = dispatchgen.derive_site(rec, dispatchgen._inst_map(rec), 0x0106,
                                  _table_fetch(0x0400, [1, 2, 3]))
    assert got["family"] == "F3" and got["slots"] == 3   # 4/2 + 1


def test_f3_odd_scaled_bound_stops_never_guesses():
    insts = [
        _i(0x0100, "seq", "3d0300", "cmp"),
        _i(0x0103, "jcc", "7706", "ja", target=0x010B),
        _i(0x0105, "seq", "93", "xchg ax,r16"),
        _i(0x0106, "jmp_ind", "2effa70004", "jmp rm16"),
    ]
    rec = _rec(insts)
    with pytest.raises(dispatchgen.DispatchStop, match="odd"):
        dispatchgen.derive_site(rec, dispatchgen._inst_map(rec), 0x0106,
                                _table_fetch(0x0400, [1, 2]))


def test_non_table_jmp_ind_stops():
    insts = [_i(0x0100, "jmp_ind", "ffe3", "jmp rm16")]   # jmp bx
    rec = _rec(insts)
    with pytest.raises(dispatchgen.DispatchStop, match="non-table"):
        dispatchgen.derive_site(rec, dispatchgen._inst_map(rec), 0x0100,
                                lambda off: 0)


def test_render_facts_reemits_its_own_prior_entries():
    """Regeneration stability: a target whose census record has
    entry_origin=dispatch-fact came FROM this facts file and must be
    re-emitted; an independent census entry (.SYM) is comment-skipped."""
    derived = [{"entry": "0100:0100", "symbol": "_T", "ne_seg": 1,
                "cs": 0x0100, "site_ip": 0x0108, "family": "F1",
                "table": 0x0200, "bound_imm": 1, "guard_ip": "0100",
                "slots": 2, "targets": [0x1111, 0x2222]}]
    ir = {"functions": {
        "0100:1111": {"symbol": "case_1111", "entry_origin": "dispatch-fact"},
        "0100:2222": {"symbol": "_RealSym"},
    }}
    entries_txt, tables_txt, n_new = dispatchgen.render_facts(ir, derived)
    assert "1:1111  # via" in entries_txt          # re-emitted (own prior fact)
    assert "# 1:2222 is already census entry _RealSym" in entries_txt
    assert n_new == 1
    assert "1:0200+4" in tables_txt                # 2 slots = 4 bytes
