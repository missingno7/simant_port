"""KERNEL dynamic loading + runtime GetProcAddress thunk minting.

SimAnt's MIDI music engine LoadLibrary's mmsystem.dll and GetProcAddress-es its
exports at runtime, then CALLS the resolved far pointers directly.  This drives
those APIs the way the game does (far calls into the real import thunks) and
proves the whole chain: LoadLibrary -> GetProcAddress -> a minted callable
thunk -> Python dispatch.  Phase 1 keeps midiOutGetNumDevs at 0 devices (the
game keeps its SOUND.DRV fallback), so this also pins the non-breaking contract.
"""
from simant import runtime
from win16.api.system import Win16System
from win16.callback import call_far

THUNK = 0x60

# Real Win16 KERNEL ordinals SimAnt imports for dynamic loading.
K_LOADLIBRARY = 95
K_GETPROCADDRESS = 50


def _machine():
    m = runtime.create_machine()
    Win16System(m)
    m.cpu.trace_enabled = False
    return m


def _put(m, off, s):
    dg = m.seg_bases[10]
    m.mem.load(dg, off, s.encode("latin-1") + b"\x00")
    return (dg << 16) | off


def _call_slot(m, ordinal, args):
    off = m.api.slots[("KERNEL", ordinal)]
    return call_far(m.cpu, THUNK, THUNK, off, args, max_steps=500_000)


def _call_farptr(m, farptr, args):
    seg, off = (farptr >> 16) & 0xFFFF, farptr & 0xFFFF
    return call_far(m.cpu, THUNK, seg, off, args, max_steps=500_000)


def test_loadlibrary_provides_mmsystem_and_rejects_others():
    m = _machine()
    mmsys = _put(m, 0x7000, "mmsystem.dll")
    sndblst = _put(m, 0x7020, "SNDBLST.DLL")

    ax, _ = _call_slot(m, K_LOADLIBRARY, [(mmsys >> 16), mmsys & 0xFFFF])
    assert ax >= 32                                   # provided -> real HINSTANCE

    ax2, _ = _call_slot(m, K_LOADLIBRARY, [(sndblst >> 16), sndblst & 0xFFFF])
    assert ax2 < 32                                   # not provided -> error, game falls back


def test_getprocaddress_mints_a_callable_thunk_that_dispatches():
    m = _machine()
    mmsys = _put(m, 0x7000, "mmsystem.dll")
    midi = _put(m, 0x7040, "midiOutGetNumDevs")
    mci = _put(m, 0x7060, "mciSendCommand")

    hinst, _ = _call_slot(m, K_LOADLIBRARY, [(mmsys >> 16), mmsys & 0xFFFF])

    # midiOutGetNumDevs is implemented -> a non-null far pointer into the thunk seg.
    ax, dx = _call_slot(m, K_GETPROCADDRESS, [hinst, (midi >> 16), midi & 0xFFFF])
    proc = (dx << 16) | ax
    assert proc != 0
    assert (proc >> 16) == THUNK                      # a minted thunk slot

    # Calling it dispatches to our handler; the MIDI Mapper reports one device.
    ax, _ = _call_farptr(m, proc, [])
    assert ax == 1

    # mciSendCommand is also implemented -> a callable thunk (not NULL).
    ax, dx = _call_slot(m, K_GETPROCADDRESS, [hinst, (mci >> 16), mci & 0xFFFF])
    assert (dx << 16 | ax) != 0

    # An export we do NOT implement resolves to NULL.
    nope = _put(m, 0x7080, "midiInGetNumDevs")
    ax, dx = _call_slot(m, K_GETPROCADDRESS, [hinst, (nope >> 16), nope & 0xFFFF])
    assert (dx << 16 | ax) == 0

    # Minting is idempotent per name (same far pointer on re-resolve).
    ax2, dx2 = _call_slot(m, K_GETPROCADDRESS, [hinst, (midi >> 16), midi & 0xFFFF])
    assert (dx2 << 16 | ax2) == proc
