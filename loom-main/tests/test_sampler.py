"""The deterministic sampler — pitch math verified by FFT (no ears needed for this part)."""

from __future__ import annotations

from sampler import fundamental_hz, note_to_hz, render_note, render_tape, synth_sample


def test_note_to_hz():
    assert abs(note_to_hz("A4") - 440.0) < 0.1
    assert abs(note_to_hz("C4") - 261.63) < 0.5
    assert abs(note_to_hz("C5") - 523.25) < 0.5


def test_rendered_note_pitch_matches_target():
    sample, sr = synth_sample(220.0)
    for name in ["C4", "E4", "G4", "A4", "C5"]:
        target = note_to_hz(name)
        measured = fundamental_hz(render_note(sample, 220.0, target, 0.5, sr), sr)
        assert abs(measured - target) / target < 0.04, (name, target, measured)


def test_render_tape_is_deterministic_and_right_length():
    sample, sr = synth_sample(220.0)
    tape = [{"note": "C4", "dur": 0.3}, {"note": "D4", "dur": 0.3}]
    a = render_tape(tape, sample, 220.0, sr, gap=0.0)
    b = render_tape(tape, sample, 220.0, sr, gap=0.0)
    assert (a == b).all()                              # same tape -> byte-identical audio
    assert abs(len(a) / sr - 0.6) < 0.05               # two 0.3s notes
