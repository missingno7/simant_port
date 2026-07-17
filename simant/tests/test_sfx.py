r"""SimAnt's digitized sound effects — the game's own contract, pinned.

`GR!_myBeginSound` (2:98B0) opens the wave device and hands `GR!_MciOutWave`
(2:959C) the effect: MciOutWave decodes its 4-bit delta-PCM into a buffer and
waveOutWrite's it.  That decode runs in GUEST code — the platform only plays
back what the game declares and writes (win16_re owns the device model; see
its tests/test_waveout.py) — so what these pin is the GAME's side:

  * the wave format it declares (a PCMWAVEFORMAT literal in seg 2), and
  * the audio that actually reaches the device over a real demo drive,
    cross-checked against artifacts/sounds_wav/ — 57 effects extracted
    independently of this decode (corroboration, not ground truth; their
    provenance turns out to be the game's own sndPlaySound RIFF, which the
    fallback test below reproduces byte for byte).

The wave path is the PRIMARY one: once waveOutOpen succeeds the game never
reaches its sndPlaySound fallback, which it takes only when no device exists.
Both are driven here.

These replay on the STRICT lifted graph (the boot image, as
test_vmless_walls.py does) — the interpreter needs ~70 s to reach the first
effect at ~41M instructions, the graph ~17 s — and stop as soon as enough
effects have been captured.
"""
import glob
import hashlib
import io
import os
import wave
from pathlib import Path

import pytest

from simant import runtime
from simant.tests.test_vmless_walls import DATA_DIR, DEMO
from win16.audio import riff_with_consistent_size
from win16.demo import DemoDriver, DemoEnded

REPO_ROOT = Path(__file__).resolve().parents[2]
SOUNDS_WAV = REPO_ROOT / "artifacts" / "sounds_wav"

#: The PCMWAVEFORMAT the game builds at 2:9D72..9D99 right before waveOutOpen:
#: {wFormatTag=1 (PCM), nChannels=1, nSamplesPerSec=4096, nAvgBytesPerSec=4096,
#: nBlockAlign=1, wBitsPerSample=8} — stored as eight WORD literals.  4096 Hz
#: mono 8-bit is what the disassembly PROVES; the extracted WAVs corroborate
#: it but do not define it.
FORMAT_WORDS_AT_2_9D72 = (1, 1, 0x1000, 0x0000, 0x1000, 0x0000, 1, 8)
DECLARED_RATE, DECLARED_BITS, DECLARED_CHANNELS = 4096, 8, 1

#: The extracted reference effects are, byte for byte, what the game's OWN
#: sndPlaySound RIFF builder emits (proven below, 7/7).  The waveOut path emits
#: exactly this many samples MORE for the same effect — observed for every
#: effect, in both directions of the comparison.  Both paths apply the same
#: `(len - 0x10) * 2` formula (identical bytes at 2:95FB, 2:9A25 and 2:9E17),
#: so they take the resource's length from accessors that differ by 3 input
#: bytes.  Which accessor is which is not pinned down here — the audible
#: content is identical either way (6 samples is 1.5 ms at 4096 Hz).
WAVE_TAIL_EXTRA = 6

import simant.vmless_boot as vb  # noqa: E402

_have_strict = ((vb.BOOT_DIR / "manifest.json").exists()
                and (vb.LIFT_DIR / "graph_manifest.json").exists())

pytestmark = pytest.mark.skipif(
    not (_have_strict and DEMO.exists() and DATA_DIR.exists()
         and SOUNDS_WAV.exists()),
    reason="needs the boot image + lifted graph + cold_nohooks demo + "
           "assets + the extracted reference effects")


class _Enough(Exception):
    """Stop the drive: we have all the effects the test needs."""


class _Capture:
    """The host sink: records what would have been played, plays nothing."""

    def __init__(self, want=3):
        self.pcm = []
        self.wav = []
        self.want = want

    def _maybe_stop(self, seq):
        if len({len(x) for x in seq}) >= self.want:
            raise _Enough()

    def play_pcm(self, pcm, *, rate, bits, channels):
        self.pcm.append((bytes(pcm), rate, bits, channels))
        self._maybe_stop([p for p, _r, _b, _c in self.pcm])

    def stop_pcm(self):
        pass

    def play_wav(self, data, *, loop=False):
        self.wav.append(bytes(data))
        self._maybe_stop(self.wav)

    def stop_wav(self):
        pass


def _drive(*, refuse_device=False, want=3):
    """Replay cold_nohooks on the strict graph, capturing the audio sink,
    until `want` distinct effects have been seen (or the demo ends)."""
    import sys
    machine, _manifest, _installed = vb.boot_strict(vb.BOOT_DIR,
                                                    game_root=DATA_DIR)
    cap = _Capture(want)
    machine.api.services["sound_backend"] = cap
    if refuse_device:
        # The machine with no wave hardware: the game's OWN fallback runs.
        machine.api.named_procs[("MMSYSTEM", "waveOutOpen")].handler = \
            lambda ctx: 2                       # MMSYSERR_BADDEVICEID
    sysobj = machine.api.services["system"]
    sys.setrecursionlimit(200_000)
    DemoDriver(DEMO).install(sysobj)
    try:
        while True:
            machine.cpu.run(2_000)
    except (_Enough, DemoEnded):
        pass
    return machine, sysobj, cap


@pytest.fixture(scope="module")
def played():
    """One strict drive, shared by every assertion about the primary path."""
    return _drive()


@pytest.fixture(scope="module")
def fell_back():
    """One strict drive with the device refused — the fallback path."""
    return _drive(refuse_device=True)


def _reference_frames():
    """The independently-extracted effects, keyed by their raw sample bytes."""
    out = {}
    for f in sorted(glob.glob(str(SOUNDS_WAV / "*.wav"))):
        w = wave.open(f)
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == \
            (DECLARED_RATE, 1, 1)
        out[w.readframes(w.getnframes())] = os.path.basename(f)
    return out


def test_the_game_declares_4096hz_mono_8bit():
    """The format literal the game writes before waveOutOpen, read straight
    out of the code segment — eight `mov word ptr [bx+N], imm16` stores."""
    from win16 import ne
    exe = ne.parse_ne(runtime.EXE_PATH)
    seg2 = bytes(exe.segment_bytes(exe.segments[1]))
    got, pos = [], 0x9D72
    for _ in range(8):
        assert seg2[pos] == 0xC7, f"not a mov imm16 at 2:{pos:04X}"
        if seg2[pos + 1] == 0x07:                       # [bx], imm16
            got.append(int.from_bytes(seg2[pos + 2:pos + 4], "little"))
            pos += 4
        else:                                            # [bx+disp8], imm16
            assert seg2[pos + 1] == 0x47, f"unexpected modrm at 2:{pos:04X}"
            got.append(int.from_bytes(seg2[pos + 3:pos + 5], "little"))
            pos += 5
    assert tuple(got) == FORMAT_WORDS_AT_2_9D72


def test_effects_reach_the_device_in_the_declared_format(played):
    _machine, _sysobj, cap = played
    assert cap.pcm, "no PCM reached the device — the SFX path is silent"
    assert not cap.wav, "the fallback ran even though the device opened"
    for pcm, rate, bits, channels in cap.pcm:
        assert (rate, bits, channels) == (DECLARED_RATE, DECLARED_BITS,
                                          DECLARED_CHANNELS)
        assert len(set(pcm)) > 1, "a buffer of constant samples is silence"


def test_every_effect_played_is_a_real_simant_sound(played):
    """Each buffer the game decodes and writes STARTS WITH an independently
    extracted SimAnt effect, byte for byte, and runs exactly
    WAVE_TAIL_EXTRA samples longer — so the guest decode and the format model
    are both right (a wrong seed, nibble order or sample sign would diverge on
    the very first samples, not agree for thousands)."""
    _machine, _sysobj, cap = played
    refs = _reference_frames()
    distinct = {hashlib.sha1(p).digest(): p for p, _r, _b, _c in cap.pcm}
    assert len(distinct) >= 3
    for pcm in distinct.values():
        hit = [nm for frames, nm in refs.items()
               if pcm.startswith(frames)
               and len(pcm) - len(frames) == WAVE_TAIL_EXTRA]
        assert hit, (f"a {len(pcm)}-byte buffer matches no extracted effect — "
                     f"the decode or the format model is wrong")


def test_the_wave_log_records_the_device_traffic(played):
    _machine, _sysobj, cap = played
    machine = _machine
    log = machine.api.services["sound_log"]
    assert [e[1] for e in log[:2]] == ["wave_open", "wave_write"]
    assert log[0][2][1:] == (DECLARED_RATE, DECLARED_BITS, DECLARED_CHANNELS)


def test_without_a_device_the_game_falls_back_to_sndplaysound(fell_back):
    """The fallback is real code, not a theory: refuse waveOutOpen and the
    game builds a complete in-memory RIFF and hands it to sndPlaySound, which
    it resolves BY NAME through GetProcAddress (an mmsystem that registers it
    only by ordinal hands back NULL and the effects vanish silently)."""
    _machine, _sysobj, cap = fell_back
    assert cap.wav, "waveOutOpen was refused but no sndPlaySound image arrived"
    assert not cap.pcm, "the wave path ran though the device was refused"

    refs = _reference_frames()
    for img in cap.wav:
        assert img[:4] == b"RIFF"
        w = wave.open(io.BytesIO(riff_with_consistent_size(img)))
        assert (w.getframerate(), w.getsampwidth() * 8, w.getnchannels()) == \
            (DECLARED_RATE, DECLARED_BITS, DECLARED_CHANNELS)
        frames = w.readframes(w.getnframes())
        assert frames in refs, \
            f"the {w.getnframes()}-frame RIFF is not an extracted effect"


def test_the_games_riff_under_declares_its_size(fell_back):
    """SimAnt's RIFF builder writes `RIFF size = data chunk + its header`,
    leaving out the "WAVE" tag and the whole "fmt " chunk — 28 bytes short.
    Windows' MMIO reader walks the chunks and plays the lot, so a reader that
    trusted the field would clip the tail off every effect."""
    _machine, _sysobj, cap = fell_back
    img = cap.wav[0]
    declared = int.from_bytes(img[4:8], "little")
    assert img[36:40] == b"data"
    data_size = int.from_bytes(img[40:44], "little")
    assert declared == data_size + 8            # the game's arithmetic
    assert len(img) == 44 + data_size           # ...and the true length
    assert len(img) - 8 - declared == 28        # exactly WAVE + the fmt chunk


def test_the_completion_ledger_balances(played):
    """Every buffer written either finished — its MM_WOM_DONE delivered on the
    virtual clock — or was abandoned by a waveOutReset.  Nothing is stranded
    in flight and nothing leaks into the message queue."""
    from win16.api.mmsystem import MM_WOM_DONE
    machine, sysobj, _cap = played
    log = machine.api.services["sound_log"]
    assert sum(1 for e in log if e[1] == "wave_write") > 0
    for dev in machine.api.services["wave_state"]["devices"].values():
        assert all(isinstance(d, tuple) for d in dev["pending"])
    assert [m for m in sysobj.msg_queue if m[1] == MM_WOM_DONE] == []
