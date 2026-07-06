#!/usr/bin/env python3
"""A non-ML baseline decoder for loomspeech — pitch-only, vocabulary-aware (the FLOOR).

Reads each audio file, splits it into note segments by silence, detects each note's pitch (FFT),
snaps to a scale degree, then greedily segments the degree sequence into words using the known
lexicon. It ignores vowels and timbre entirely — so the vowel signal (which carries out-of-
vocabulary words) is thrown away. The open challenge: beat this with a model that needs no
lexicon, survives noise, and recovers words this baseline can't.

    python bench/encode.py --builtin --n 300 --out data/ls
    python bench/baseline.py data/ls/manifest.jsonl data/ls > preds.jsonl
    python bench/score.py data/ls/manifest.jsonl preds.jsonl
"""
import json
import os
import sys

import numpy as np

try:
    import soundfile as sf
except ImportError:
    raise SystemExit("needs soundfile: pip install soundfile numpy")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from encode import SCALE_HZ, WORDS, word_motif  # noqa: E402

VOCAB = {}
for _w in WORDS:
    VOCAB.setdefault(tuple(word_motif(_w)), _w)   # motif -> word (first wins on collision)


def segments(audio, sr, thr=0.02, min_s=0.05):
    env = np.abs(audio).astype(np.float64)
    win = max(1, int(0.006 * sr))
    env = np.convolve(env, np.ones(win) / win, "same")
    voiced = env > thr
    segs, i, n = [], 0, len(audio)
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if j - i >= int(min_s * sr):
                segs.append((i, j))
            i = j
        else:
            i += 1
    return segs


def degree(seg, sr):
    a = seg * np.hanning(len(seg))
    spec = np.abs(np.fft.rfft(a))
    fr = np.fft.rfftfreq(len(a), 1.0 / sr)
    band = (fr >= 200) & (fr <= 560)
    if not band.any() or not spec[band].size:
        return 0
    f = fr[band][int(np.argmax(spec[band]))]
    return min(range(len(SCALE_HZ)), key=lambda d: abs(SCALE_HZ[d] - f))


def decode(audio, sr):
    degs = [degree(audio[a:b], sr) for a, b in segments(audio, sr)]
    out, i = [], 0
    while i < len(degs):
        hit = None
        for length in (3, 2, 1):
            t = tuple(degs[i:i + length])
            if len(t) == length and t in VOCAB:
                hit = (VOCAB[t], length)
                break
        if hit:
            out.append(hit[0])
            i += hit[1]
        else:
            i += 1
    return " ".join(out)


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: baseline.py <manifest.jsonl> <data_dir>  > preds.jsonl")
    manifest, root = sys.argv[1], sys.argv[2]
    for line in open(manifest, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        audio, sr = sf.read(os.path.join(root, d["wav"]))
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        print(json.dumps({"id": d["id"], "text": decode(np.asarray(audio, dtype=np.float64), sr)}))


if __name__ == "__main__":
    main()
