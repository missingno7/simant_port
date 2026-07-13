"""The state-view seam: one recovered implementation, two backends (VM + native).

Proves the ``simant/bridge`` layout bridge reads/writes SimAnt's DGROUP as named
source-level fields, and that the SAME view + SAME recovered logic runs over
both a live win16 VM image and an owned :class:`NativeGameState` — the core of
the VM-less port (the win16 analogue of pre2's state-view layer).
"""
import pytest

from simant import hooks, runtime
from simant.bridge.dgroup_view import SimAntState
from simant.native.state import NativeGameState
from simant.recovered.simone import srand_step

pytestmark = pytest.mark.skipif(not runtime.assets_present(),
                                reason="simant assets not present")

DG = 10                                     # DGROUP segment index
RNG_OFF = 0xCBF2                             # _SRand seed (cross-check vs the raw read)


def _machine():
    m = runtime.create_machine()
    m.cpu.trace_enabled = False
    return m


def _dg_base(m):
    return m.seg_bases[DG] << 4


def test_view_matches_raw_selector_read():
    # The flat ByteBackend over mem.data at DGROUP's linear base must agree with
    # the VM's own selector-based read — the bridge is a faithful re-addressing.
    m = _machine()
    s = SimAntState(m.mem, _dg_base(m))
    assert s.rng_seed == m.mem.rw(m.seg_bases[DG], RNG_OFF)
    # a write through the view lands where the VM sees it
    s.rng_seed = 0x1234
    assert m.mem.rw(m.seg_bases[DG], RNG_OFF) == 0x1234
    s.map_cols = 40
    assert m.mem.rw(m.seg_bases[DG], 0xCC80) == 40


def test_native_state_mirrors_the_vm_image():
    m = _machine()
    vm = SimAntState(m.mem, _dg_base(m))
    vm.rng_seed = 0xBEEF
    vm.map_cols, vm.map_rows = 31, 23

    native = NativeGameState.from_machine(m)          # bootstrap: VM image -> owned
    assert native.view.rng_seed == 0xBEEF
    assert (native.view.map_cols, native.view.map_rows) == (31, 23)

    # The native image is independent: mutating it does not touch the VM.
    native.view.rng_seed = 0x0001
    assert vm.rng_seed == 0xBEEF


def test_map_planes_read_the_same_cells_the_asm_addresses():
    # The named map-plane views must index the exact bytes _GetMap's
    # map_cell_offset points at — the layout bridge is a faithful re-addressing
    # of the tile grid, for every plane.
    from simant.recovered.gameplay import map_cell_offset
    m = _machine()
    s = SimAntState(m.mem, _dg_base(m))
    seg = m.seg_bases[DG]
    for plane, x, y, val in [(0, 0x10, 0x20, 0x42), (1, 0x7F, 0x3F, 0x9A),
                             (2, 0, 0, 0x55), (3, 0x20, 0x10, 0xC3)]:
        off = map_cell_offset(plane, x, y)
        m.mem.wb(seg, off, val)                        # poke via the raw selector
        assert s.map_planes[plane][(x << 6) + y] == val   # the view sees it


def test_world_grids_migrate_to_an_owned_native_state():
    # Write a tile + a life value through the VM view, bootstrap the owned image,
    # and read them back natively — the biggest game structure (the map) is
    # ownable with no VM, and the owned copy is independent.
    m = _machine()
    vm = SimAntState(m.mem, _dg_base(m))
    vm.map_nest[5] = 0x77                              # plane 2, index 5
    vm.life_yard[0x100] = 0x21

    native = NativeGameState.from_machine(m)
    assert native.view.map_nest[5] == 0x77
    assert native.view.life_yard[0x100] == 0x21

    native.view.map_nest[5] = 0x00                     # owned image is independent
    assert vm.map_nest[5] == 0x77


@pytest.mark.parametrize("seed", [0x0001, 0x8000, 0xBEEF, 0xFFFF, 0x1BF5])
def test_recovered_prng_runs_over_both_backends(seed):
    # The recovered srand_step is the shared centre; drive it through the view on
    # a VM backend and a native backend — identical results, no second copy.
    m = _machine()
    vm = SimAntState(m.mem, _dg_base(m))
    native = NativeGameState.from_machine(m)

    vm.rng_seed = seed
    native.view.rng_seed = seed

    for _ in range(50):
        vm.rng_seed = srand_step(vm.rng_seed)
        native.view.rng_seed = srand_step(native.view.rng_seed)
        assert vm.rng_seed == native.view.rng_seed
