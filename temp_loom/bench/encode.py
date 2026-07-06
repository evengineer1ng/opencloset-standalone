#!/usr/bin/env python3
"""loomspeech benchmark — the ENCODER. Deterministic text -> sung audio.

Each word hashes to a short musical MOTIF (1-3 notes) on a fixed pentatonic scale, sung on the
word's own vowels. Same word -> same motif -> same sound, forever. That makes (text, audio) an
*unlimited, free, perfectly-labeled* dataset — no human, no licensing, no model.

THE TASK (see README): given the audio, recover the text. If a model can, the codec carries the
meaning losslessly — it is a language. Generate as much as you want:

    python bench/encode.py --builtin --n 2000 --out data/loomspeech
    python bench/encode.py --corpus my_lines.txt --out data/loomspeech

Writes <out>/*.wav + <out>/manifest.jsonl  ({id, text, wav, motif}).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
import voicesynth  # noqa: E402  (repo root)

try:
    import soundfile as sf
except ImportError:
    raise SystemExit("loomspeech encode needs soundfile:  pip install soundfile numpy")

SCALE_HZ = [220.00, 261.63, 293.66, 329.63, 392.00, 440.00, 523.25]  # A-minor pentatonic
VOWELS = set("aeiou")
# a small built-in lexicon so anyone can mint data with zero input.
WORDS = ("the a red fast car heart beats high low you move stop go win lose three dunk lap race "
         "night city loom echo brah turn brake speed lead fall rise hold open close left right up "
         "down now then fan rival crew pit fastest clean sharp gap apex throttle").split()


def word_motif(word: str) -> list:
    """A word -> a deterministic 1..3 note motif (degrees into SCALE_HZ). Clean uint32 hash —
    this is the canonical benchmark mapping (booth.html is its interactive cousin)."""
    h = 2166136261
    for c in word.lower():
        h = ((h ^ ord(c)) * 16777619) & 0xFFFFFFFF
    n = 1 + (len(word) % 3)
    out = []
    for _ in range(n):
        out.append(h % len(SCALE_HZ))
        h = (h * 16777619 + 2654435761) & 0xFFFFFFFF
    return out


def _words(text: str) -> list:
    return ["".join(c for c in w if c.isalpha()) for w in text.lower().split() if any(ch.isalpha() for ch in w)]


def encode_line(text: str, *, dur: float = 0.26, gap: float = 0.05, sr: int = 22050):
    notes, motifs = [], []
    for w in _words(text):
        deg = word_motif(w)
        motifs.append(deg)
        vs = [c for c in w if c in VOWELS] or ["a"]
        for i, d in enumerate(deg):
            notes.append({"hz": SCALE_HZ[d], "dur": dur, "vowel": vs[i % len(vs)]})
    audio = voicesynth.render_tape(notes, sr=sr, gap=gap) if notes else np.zeros(1, dtype=np.float32)
    return audio, motifs


def builtin_corpus(n: int, seed: int = 0) -> list:
    rng = random.Random(seed)
    return [" ".join(rng.choice(WORDS) for _ in range(rng.randint(2, 6))) for _ in range(n)]


def main() -> None:
    ap = argparse.ArgumentParser(description="loomspeech encoder — text to sung audio")
    ap.add_argument("--corpus", help="text file, one line per utterance")
    ap.add_argument("--builtin", action="store_true", help="mint random utterances from the built-in lexicon")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="data/loomspeech")
    ap.add_argument("--sr", type=int, default=22050)
    args = ap.parse_args()

    if args.corpus:
        lines = [ln.strip() for ln in open(args.corpus, encoding="utf-8") if ln.strip()]
    else:
        lines = builtin_corpus(args.n, args.seed)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "manifest.jsonl"), "w", encoding="utf-8") as man:
        for i, line in enumerate(lines):
            audio, motifs = encode_line(line, sr=args.sr)
            wav = f"{i:06d}.wav"
            sf.write(os.path.join(args.out, wav), audio, args.sr)
            man.write(json.dumps({"id": i, "text": line, "wav": wav, "motif": motifs}) + "\n")

    print(f"wrote {len(lines)} (text,audio) pairs -> {args.out}/  (sr={args.sr})")
    print("task: predict 'text' from 'wav'.  score:  python bench/score.py "
          f"{args.out}/manifest.jsonl preds.jsonl")


if __name__ == "__main__":
    main()
