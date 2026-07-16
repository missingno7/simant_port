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


# -- MIDI Mapper level filtering against the real soundtrack -------------------
#
# Every SimAnt .mid is dual-format per the Windows 3.x MIDI Mapper authoring
# guideline: the extended-level arrangement on channels 1-10 (percussion on
# 10) is mirrored note-for-note on the base-level channels 13-16 (percussion
# on 16).  Under the original MIDI Mapper only ONE level reached the synth;
# a raw General MIDI rendering plays both — the melody doubles and channel
# 16's percussion mirror (program change 126 = GM "Applause", a noise patch)
# re-triggers on every drum hit: the owner-reported constant "shhh" riding
# over the music.  win16.audio.MidiBackend now applies the extended-level
# Mapper view; this pins it against the shipped GAMETHME.MID.

def _walk_smf(data):
    """-> [(track, abs_ticks, status, data1, data2|None)] channel events."""
    assert data[:4] == b"MThd"
    ntrks = int.from_bytes(data[10:12], "big")
    pos = 8 + int.from_bytes(data[4:8], "big")
    out = []

    def vlq(p):
        v = 0
        while True:
            b = data[p]
            p += 1
            v = (v << 7) | (b & 0x7F)
            if not b & 0x80:
                return v, p

    for trk in range(ntrks):
        assert data[pos:pos + 4] == b"MTrk"
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        p, end = pos + 8, pos + 8 + length
        pos = end
        t, running = 0, None
        while p < end:
            dt, p = vlq(p)
            t += dt
            b = data[p]
            if b == 0xFF:
                ln, q = vlq(p + 2)
                p = q + ln
                running = None
            elif b in (0xF0, 0xF7):
                ln, q = vlq(p + 1)
                p = q + ln
                running = None
            else:
                if b & 0x80:
                    running = b
                    p += 1
                if running & 0xF0 in (0xC0, 0xD0):
                    out.append((trk, t, running, data[p], None))
                    p += 1
                else:
                    out.append((trk, t, running, data[p], data[p + 1]))
                    p += 2
    return out


def _note_on_counts(events):
    from collections import Counter
    return dict(Counter(st & 0x0F for _, _, st, _, d2 in events
                        if st & 0xF0 == 0x90 and d2))


def test_gamethme_mapper_filter_strips_the_base_level_mirror():
    data = (runtime.ASSETS / "ANTWIN" / "SOUND" / "GAMETHME.MID").read_bytes()
    from win16.audio import EXTENDED_LEVEL_CHANNELS, midi_keep_channels

    before = _walk_smf(data)
    # the shipped file really is dual-format: base level (12-15 0-based)
    # mirrors the extended level (2,3,4,9) note-on for note-on
    assert _note_on_counts(before) == {2: 346, 3: 182, 4: 98, 9: 456,
                                       12: 346, 13: 182, 14: 98, 15: 456}
    ext_perc = [(t, d1, d2) for _, t, st, d1, d2 in before
                if st == 0x99 and d2]
    base_perc = [(t, d1, d2) for _, t, st, d1, d2 in before
                 if st == 0x9F and d2]
    assert len(base_perc) == len(ext_perc) == 456
    # the hiss source: the base percussion mirror carries GM program 126
    # ("Applause") — a melodic noise patch on a modern GM synth
    assert (0xCF, 126) in {(st, d1) for _, _, st, d1, _ in before}

    filtered = midi_keep_channels(data, EXTENDED_LEVEL_CHANNELS)
    after = _walk_smf(filtered)
    # extended level intact, base level mirror (and its Applause hits) gone
    assert _note_on_counts(after) == {2: 346, 3: 182, 4: 98, 9: 456}
    assert all(st & 0x0F not in (12, 13, 14, 15) for _, _, st, _, _ in after)
    # surviving events keep their absolute times, exactly
    kept_before = [(trk, t, st, d1, d2) for trk, t, st, d1, d2 in before
                   if st & 0x0F in EXTENDED_LEVEL_CHANNELS]
    assert after == kept_before
