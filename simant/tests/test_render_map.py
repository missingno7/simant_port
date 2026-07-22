"""render_map taxonomy + parsing helpers (scripts/render_map.py).

The full map is an Atlas query (integration-tested by building the Atlas);
here we pin the pure pieces so the rendering taxonomy can't silently rot:
every categorised API is a real win16 GDI/USER handler, and the boundary-label
parse is correct.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "render_map", REPO_ROOT / "scripts" / "render_map.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_boundary_label_parse():
    rm = _load()
    assert rm._api_name("api:GDI.1:SetBkColor") == "SetBkColor"
    assert rm._api_name("api:USER.39:BeginPaint") == "BeginPaint"
    assert rm._api_name("proc:MMSYSTEM.mciSendCommand") is None
    assert rm._api_name("") is None


def test_every_categorised_api_is_a_real_win16_handler():
    from simant.runtime import assets_present
    if not assets_present():
        import pytest
        pytest.skip("needs assets for the registry")
    import simant.runtime  # noqa: F401 — puts win16 on the path
    from win16.api.surface import build_registry, WINFLAGS_NO_FPU
    reg = build_registry(winflags=WINFLAGS_NO_FPU)
    implemented = {getattr(e, "label", "").rsplit(":", 1)[-1]
                   for e in reg.entries.values()}
    implemented |= {getattr(e, "name", "") for e in reg.entries.values()}
    rm = _load()
    missing = sorted(api for cat in rm.CATEGORIES.values() for api in cat
                     if api not in implemented)
    assert not missing, f"render_map categorises non-existent APIs: {missing}"


def test_categories_are_disjoint_enough_and_nonempty():
    rm = _load()
    assert set(rm.CATEGORIES) >= {"dc-lifecycle", "invalidation", "blit",
                                  "gdi-object", "primitive", "scroll",
                                  "palette"}
    for cat, apis in rm.CATEGORIES.items():
        assert apis, f"empty category {cat}"
