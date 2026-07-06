"""Deterministic sampler — render a note-tape into audio from ONE recorded sample. No ML.

The loom thesis in sound: borrowed timbre (a real sample, captured once) + deterministic
arrangement (pitch-shift + time + envelope + concat) = play ANY melody, byte-identically. A song
is a tape; a voice/instrument is a sample. Endpoint deps (numpy + soundfile), never the pure core.

POC limitation: pitch-shift is by resampling, which shifts formants too ("chipmunk" on big jumps);
a formant-preserving phase vocoder is the real-engine upgrade.
"""
from __future__ import annotations

import re

import numpy as np

A4 = 440.0
_SEMI = {"C": -9, "C#": -8, "D": -7, "D#": -6, "E": -5, "F": -4, "F#": -3, "G": -2, "G#": -1,
         "A": 0, "A#": 1, "B": 2}


def note_to_hz(name: str) -> float:
    m = re.match(r"([A-G]#?)(-?\d)", name.strip())
    if not m:
        raise ValueError(f"bad note {name!r}")
    semis = _SEMI[m.group(1)] + (int(m.group(2)) - 4) * 12
    return A4 * 2 ** (semis / 12.0)


def synth_sample(hz: float = 220.0, sr: int = 44100, secs: float = 1.0):
    """A harmonic tone to prove the pipeline before a real sample is dropped in the same slot."""
    t = np.arange(int(sr * secs)) / sr
    x = sum(np.sin(2 * np.pi * hz * k * t) / k for k in range(1, 9))
    return (0.2 * x).astype(np.float32), sr


def fundamental_hz(audio: np.ndarray, sr: int) -> float:
    """FFT peak — used to estimate a sample's pitch and to VERIFY rendered pitch."""
    a = audio[: int(sr * 0.5)] if len(audio) > sr * 0.5 else audio
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    spec[0] = 0
    return float(np.argmax(spec) * sr / len(a))


def _pitch_shift(sample: np.ndarray, ratio: float) -> np.ndarray:
    """Resample so playback is `ratio`x faster -> pitch * ratio."""
    n = len(sample)
    idx = np.arange(0, n, ratio)
    return np.interp(idx, np.arange(n), sample).astype(np.float32)


def render_note(sample, base_hz, target_hz, dur_s, sr) -> np.ndarray:
    shifted = _pitch_shift(sample, target_hz / base_hz)
    need = max(1, int(dur_s * sr))
    if len(shifted) < need:
        shifted = np.tile(shifted, int(np.ceil(need / len(shifted))))
    note = shifted[:need].astype(np.float32).copy()
    a = min(int(0.012 * sr), need // 4) or 1               # attack/release to kill clicks
    env = np.ones(need, dtype=np.float32)
    env[:a] = np.linspace(0, 1, a)
    env[-a:] = np.linspace(1, 0, a)
    return note * env


def render_tape(notes, sample, base_hz, sr=44100, gap=0.03) -> np.ndarray:
    """notes: list of {"note": "C4", "dur": 0.5}. Returns mono float32 audio."""
    out = []
    silence = np.zeros(int(gap * sr), dtype=np.float32)
    for nt in notes:
        out.append(render_note(sample, base_hz, note_to_hz(nt["note"]), float(nt.get("dur", 0.5)), sr))
        out.append(silence)
    return np.concatenate(out).astype(np.float32) if out else np.zeros(1, dtype=np.float32)
