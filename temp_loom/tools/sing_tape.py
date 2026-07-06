"""Sing a tape in opera style — narration (a grammar) -> a deterministic melody -> the vocal synth.

  python -m tools.sing_tape --nba --voice town_crier --vibrato 0.035 --out transcripts/finals_opera.wav

The whole stack, ~free: a tape -> town-crier text -> each line mapped to an operatic phrase (a
pentatonic arch, cadence to the tonic) -> sung by voicesynth (vowels + vibrato). No model, no GPU.

Honest: it sings VOWELS following the words' rhythm + the melody; it does NOT yet enunciate
consonants (diction is the next rung). The town-crier text is the subtitle; the audio is the vocalise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time

import numpy as np
import soundfile as sf

import voicesynth
from oradio_engine.speech import Grammar
from sampler import note_to_hz

SCALE = ["A3", "C4", "D4", "E4", "G4", "A4", "C5"]   # A-minor pentatonic — always consonant


def nba_roles(path, limit=14):
    plays = json.load(open(path, encoding="utf-8")).get("plays", [])
    rows = []
    for p in plays:
        m = re.match(r"^(.+?) (makes|misses) (.+)$", p.get("text") or "")
        if not m or m.group(2) != "makes":
            continue
        actor = m.group(1).split(" (")[0].strip()
        rest = m.group(3).lower()
        obj = ("three" if "three" in rest else "dunk" if "dunk" in rest
               else "layup" if "layup" in rest else "free throw" if "free throw" in rest else "shot")
        rows.append({"actor": actor, "action": "make", "object": obj, "valence": "hype",
                     "lap": f"Q{(p.get('period') or {}).get('number')}", "priority": 0.8})
        if len(rows) >= limit:
            break
    return rows


def melody(n):
    if n <= 1:
        return [SCALE[0]]
    out = [SCALE[round((len(SCALE) - 1) * (1 - abs(2 * (i / (n - 1)) - 1)))] for i in range(n)]
    out[-1] = SCALE[0]                                # cadence
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nba", action="store_true")
    ap.add_argument("--tape", default="")
    ap.add_argument("--voice", default="town_crier")
    ap.add_argument("--vowel", default="a")
    ap.add_argument("--vibrato", type=float, default=0.035)
    ap.add_argument("--wordsec", type=float, default=0.32)
    ap.add_argument("--out", default="transcripts/finals_opera.wav")
    args = ap.parse_args()
    sr = 44100

    t0 = time.perf_counter()
    rows = nba_roles("data/finals_pbp_401859967.json") if args.nba else json.load(open(args.tape, encoding="utf-8"))
    grammar = Grammar.from_file(f"data/grammars/{args.voice}.json", verbs="data/english/irregular_verbs.json")
    lines = [grammar.line(r, key=str(i)) for i, r in enumerate(rows)]

    audio = []
    for line in lines:
        for nm in melody(len(re.findall(r"[A-Za-z']+", line))):
            audio.append(voicesynth.sing_note(note_to_hz(nm), args.wordsec, vowel=args.vowel,
                                              vibrato_depth=args.vibrato, sr=sr))
        audio.append(np.zeros(int(0.18 * sr), dtype=np.float32))
    out = np.concatenate(audio).astype(np.float32)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    sf.write(args.out, out, sr)
    dt = time.perf_counter() - t0

    print(f'=== sung in opera ({args.voice}, vibrato {args.vibrato}) -> {args.out} ===\n')
    for ln in lines:
        print("  " + ln)
    print(f"\n{len(out)/sr:.1f}s of audio, rendered in {dt*1000:.0f} ms")
    print("cost: $0 — deterministic synth + grammar, no model, no GPU, no API. CPU only.")


if __name__ == "__main__":
    main()
