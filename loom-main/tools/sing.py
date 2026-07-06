"""Sing a note-tape with a real (Freesound) or synth sample — the sampler scaffold, end to end.

  python -m tools.sing --synth --notes "C4 D4 E4 F4 G4 A4 B4 C5" --out transcripts/scale.wav
  python -m tools.sing --query "opera vowel ah sustained note" --notes "C4 E4 G4 C5"   # needs FREESOUND_API_KEY

Prints an FFT pitch-correctness check per note (the deterministic receipt). Timbre/realness is for
your ears + an ABX test; this proves the pitch math is right.
"""
from __future__ import annotations

import argparse

import soundfile as sf

from sampler import fundamental_hz, note_to_hz, render_note, render_tape, synth_sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="")
    ap.add_argument("--synth", action="store_true")
    ap.add_argument("--notes", default="C4 D4 E4 F4 G4 A4 B4 C5")
    ap.add_argument("--dur", type=float, default=0.5)
    ap.add_argument("--base", type=float, default=0.0, help="sample base pitch Hz (else estimated)")
    ap.add_argument("--out", default="transcripts/scale.wav")
    args = ap.parse_args()

    if args.synth or not args.query:
        sample, sr = synth_sample(220.0)
        base = 220.0
        print("sample: synth tone @220Hz (placeholder timbre — swap a Freesound sample in)")
    else:
        from freesound import fetch
        sample, sr = fetch(args.query)
        base = args.base or fundamental_hz(sample, sr)
        print(f"estimated sample base pitch: {base:.1f} Hz")

    notes = [{"note": n, "dur": args.dur} for n in args.notes.split()]
    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    sf.write(args.out, render_tape(notes, sample, base, sr), sr)

    print(f"\nwrote {args.out}\n\nFFT pitch check (target vs rendered):")
    ok = 0
    for nt in notes:
        tgt = note_to_hz(nt["note"])
        meas = fundamental_hz(render_note(sample, base, tgt, args.dur, sr), sr)
        good = abs(meas - tgt) / tgt < 0.04
        ok += good
        print(f"  {nt['note']:4} target {tgt:7.1f}Hz  rendered {meas:7.1f}Hz  {'ok' if good else 'OFF'}")
    print(f"\n{ok}/{len(notes)} notes pitch-correct")


if __name__ == "__main__":
    main()
