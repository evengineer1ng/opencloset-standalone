"""Sing a note-tape with the deterministic vocal synth — a voice from logic, no samples, no source.

  python -m tools.voice --notes "C4 D4 E4 F4 G4 A4 B4 C5" --vowel a --vibrato 0.02 --out transcripts/aah.wav

Prints an autocorrelation pitch-correctness check per note (the deterministic receipt). Whether it
sounds human is for your ears + an ABX test — this proves the logic puts the pitch where it should be.
"""
from __future__ import annotations

import argparse
import os

import soundfile as sf

import voicesynth
from sampler import note_to_hz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="C4 D4 E4 F4 G4 A4 B4 C5")
    ap.add_argument("--vowel", default="a", choices=list(voicesynth.FORMANTS))
    ap.add_argument("--vibrato", type=float, default=0.0, help="vibrato depth (0=none, ~0.02 operatic)")
    ap.add_argument("--dur", type=float, default=0.6)
    ap.add_argument("--out", default="transcripts/voice.wav")
    args = ap.parse_args()
    sr = 44100

    names = args.notes.split()
    notes = [{"hz": note_to_hz(n), "dur": args.dur, "vowel": args.vowel} for n in names]
    audio = voicesynth.render_tape(notes, vowel=args.vowel, vibrato_depth=args.vibrato, sr=sr)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    sf.write(args.out, audio, sr)

    print(f'wrote {args.out}  (vowel "{args.vowel}", vibrato {args.vibrato})\n\nspectral pitch check (harmonic at target?):')
    ok = 0
    for nm in names:
        tgt = note_to_hz(nm)
        note = voicesynth.sing_note(tgt, args.dur, vowel=args.vowel, vibrato_depth=args.vibrato, sr=sr)
        good = voicesynth.has_pitch(note, sr, tgt)
        ok += good
        print(f"  {nm:4} target {tgt:7.1f}Hz  peak@target: {'yes' if good else 'NO'}")
    print(f"\n{ok}/{len(names)} notes carry the right pitch")


if __name__ == "__main__":
    main()
