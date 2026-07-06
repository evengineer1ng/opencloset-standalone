#!/usr/bin/env python3
"""Build the HuggingFace-ready loomspeech dataset folder (drag-drop into a new Dataset repo).

It writes an AudioFolder sample (wavs + metadata.jsonl), copies the bench tools, and writes the
dataset card. The point isn't a big static dump — it's a *generator-backed* benchmark: a taste to
audit + the encoder to mint unlimited more.

    python bench/build_dataset.py --out "C:/Users/evana/Downloads/loomspeech_dataset" --n 64
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soundfile as sf  # noqa: E402
from encode import builtin_corpus, encode_line  # noqa: E402

CARD = '''---
license: apache-2.0
task_categories:
- automatic-speech-recognition
language:
- en
tags:
- benchmark
- deterministic
- audio-to-text
- codec
pretty_name: loomspeech
size_categories:
- n<1K
---

# loomspeech — a decode benchmark

**Can a model listen to deterministically-encoded audio and recover the text it came from?**

loom is a ~10KB, model-free codec: it turns text into sung audio by mapping each **word to a fixed
musical motif** (1-3 notes on a pentatonic scale) sung on the word's own vowels. Same word -> same
sound, forever, on any machine. So `(text -> audio)` labels are **free and unlimited** — this repo
is a *generator-backed* benchmark, not a static dump.

## The task
Given a `.wav`, predict the source `text`. Metrics: exact-line accuracy and position-wise word
accuracy (`score.py`).

## What's here
- `data/` — a playable sample (`metadata.jsonl` + wavs: `file_name`, `text`, `motif`).
- `encode.py` — the encoder. Mint more: `python encode.py --builtin --n 5000 --out data`.
- `baseline.py` — a non-ML floor: pitch-only, vocabulary-aware (~43% word accuracy). Beat it.
- `score.py` — the scorer.

```python
from datasets import load_dataset
ds = load_dataset("asleepyhimiko/loomspeech")   # audio + text + motif
```

## Baseline & headroom
Pitch-only + known lexicon ~= **43% word / 20% exact-line.** It throws the **vowels** away (which
carry out-of-vocabulary words), assumes the lexicon, and dies under noise. A real model should beat
all three.

## The question
Near-100% decode proves a **lossless, model-free code** — meaning survives sound and returns.
Whether a recoverable code is a **language** (human-learnable, conventional, productive) is the open
question, not the claim. This benchmark measures the necessary condition.

- Hear it: <https://asleepyhimiko-loom.static.hf.space/booth.html> (set *pitch -> word = note*)
- Source: <https://github.com/evengineer1ng/loom>
'''


def main() -> None:
    ap = argparse.ArgumentParser(description="build the HF-ready loomspeech dataset folder")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=64)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sr", type=int, default=22050)
    args = ap.parse_args()

    root = os.path.expanduser(args.out)
    data = os.path.join(root, "data")
    os.makedirs(data, exist_ok=True)

    here = os.path.dirname(os.path.abspath(__file__))
    for f in ("encode.py", "score.py", "baseline.py"):
        shutil.copy(os.path.join(here, f), os.path.join(root, f))

    lines = builtin_corpus(args.n, args.seed)
    with open(os.path.join(data, "metadata.jsonl"), "w", encoding="utf-8") as meta:
        for i, line in enumerate(lines):
            audio, motifs = encode_line(line, sr=args.sr)
            fn = f"{i:05d}.wav"
            sf.write(os.path.join(data, fn), audio, args.sr)
            meta.write(json.dumps({"file_name": fn, "text": line, "motif": motifs}) + "\n")

    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
        f.write(CARD)

    print(f"dataset ready at: {root}")
    print(f"  root files: {sorted(os.listdir(root))}")
    print(f"  data/: {len(lines)} wavs + metadata.jsonl")
    print("upload: huggingface.co/new-dataset -> name 'loomspeech' -> drag this folder's contents in.")


if __name__ == "__main__":
    main()
