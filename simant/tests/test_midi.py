"""MMSYSTEM MCI music path — SimAnt's real .mid soundtrack.

SimAnt's music engine LoadLibrary's mmsystem.dll, resolves midiOutGetNumDevs +
mciSendCommand, and plays sound\\NAME.mid through the MCI sequencer.  These
drive the MCI layer the way the game does and assert the deterministic model:
the .mid element resolves to the real asset, the event log is exact, and a
host backend is driven open->play->close.
"""
from types import SimpleNamespace

from simant import runtime
from win16.api.system import Win16System
from win16.api import mmsystem


def _machine():
    m = runtime.create_machine()
    Win16System(m)
    m.cpu.trace_enabled = False
    return m


def _ctx(m, args):
    from win16.api.core import CallContext
    return CallContext(m.cpu, m.api, "MMSYSTEM", 0, "mciSendCommand", tuple(args))


def _call_mci(m, dev, msg, flags, parm):
    # invoke the registered mciSendCommand handler directly with decoded args
    entry = m.api.named_procs[("MMSYSTEM", "mciSendCommand")]
    return entry.handler(_ctx(m, [dev, msg, flags, parm]))


def _write_open_parms(m, seg, off, element_ptr):
    # MCI_OPEN_PARMS: element (LPCSTR) at +12
    m.mem.ww(seg, off + 12, element_ptr & 0xFFFF)
    m.mem.ww(seg, off + 14, (element_ptr >> 16) & 0xFFFF)


def test_mmsystem_dll_is_a_provided_file():
    m = _machine()
    assert "MMSYSTEM.DLL" in m.api.provided_dlls
    # the existence probe (file_open) must succeed for the game's loader
    sysobj = m.api.services["system"]
    h = sysobj.file_open("C:\\WINDOWS\\SYSTEM\\mmsystem.dll")
    assert h >= 0


def test_midiOutGetNumDevs_reports_a_device():
    m = _machine()
    entry = m.api.named_procs[("MMSYSTEM", "midiOutGetNumDevs")]
    assert entry.handler(_ctx(m, [])) == 1


def test_mci_open_resolves_the_mid_and_drives_the_backend():
    m = _machine()
    dg = m.seg_bases[10]
    # stage the element string "C:\sound\gamethme.mid" in DGROUP
    element = "C:\\sound\\gamethme.mid"
    m.mem.load(dg, 0x6000, element.encode("latin-1") + b"\x00")
    m.mem.load(dg, 0x6100, b"\x00" * 32)                 # the MCI_OPEN_PARMS
    _write_open_parms(m, dg, 0x6100, (dg << 16) | 0x6000)

    events = []
    m.api.services["music_backend"] = SimpleNamespace(
        open=lambda d, p: events.append(("open", d, p)),
        play=lambda d: events.append(("play", d)),
        stop=lambda d: events.append(("stop", d)),
        close=lambda d: events.append(("close", d)),
        is_playing=lambda d: False)

    MCI_OPEN, MCI_PLAY, MCI_CLOSE = 0x0803, 0x0806, 0x0804
    dev = 0
    assert _call_mci(m, dev, MCI_OPEN, 0x200, (dg << 16) | 0x6100) == 0
    # the open assigned a device id into the parms (+4) and to the backend
    dev = m.mem.rw(dg, 0x6104)
    assert dev == 1
    assert _call_mci(m, dev, MCI_PLAY, 0x4, 0) == 0
    assert _call_mci(m, dev, MCI_CLOSE, 0, 0) == 0

    # backend was driven with the real resolved .mid host path
    kinds = [e[0] for e in events]
    assert kinds == ["open", "play", "close"]
    open_evt = events[0]
    assert open_evt[1] == 1
    assert open_evt[2] is not None and open_evt[2].upper().endswith("GAMETHME.MID")

    # deterministic log records the (clock, event, args) timeline
    log = m.api.services["mci_log"]
    assert [e[1] for e in log] == ["open", "play", "close"]
    assert log[0][2] == (1, element)


def test_mci_element_resolves_to_real_asset_file():
    m = _machine()
    sysobj = m.api.services["system"]
    host = sysobj.resolve_host_path("C:\\sound\\gamethme.mid")
    assert host is not None
    assert host.name.upper() == "GAMETHME.MID"
    assert host.is_file()
