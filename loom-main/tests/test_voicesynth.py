"""Deterministic vocal synth — pitch (autocorrelation) and formant timbre, verified by logic."""

from __future__ import annotations

import numpy as np

from voicesynth import has_pitch, sing_note


def test_sung_pitch_present_at_target():
    for hz in [220.0, 330.0, 440.0, 523.25]:
        assert has_pitch(sing_note(hz, 0.5, vowel="a"), 44100, hz), hz


def test_vowel_changes_timbre():
    sr = 44100

    def centroid(x):
        S = np.abs(np.fft.rfft(x)); fr = np.fft.rfftfreq(len(x), 1 / sr)
        return float((fr * S).sum() / (S.sum() or 1.0))

    bright = centroid(sing_note(220.0, 0.5, vowel="i"))   # high formants
    dark = centroid(sing_note(220.0, 0.5, vowel="u"))     # low formants
    assert bright > dark                                   # formant table actually shapes timbre


def test_vibrato_modulates_pitch_but_not_center():
    sr = 44100
    flat = sing_note(440.0, 0.6, vowel="a", vibrato_depth=0.0)
    vib = sing_note(440.0, 0.6, vowel="a", vibrato_depth=0.03)
    assert (flat != vib).any()                             # vibrato changed the waveform
    assert has_pitch(vib, sr, 440.0, tol=0.06)             # ...still centered on the right pitch


def test_deterministic():
    assert (sing_note(440.0, 0.3, vowel="a") == sing_note(440.0, 0.3, vowel="a")).all()
