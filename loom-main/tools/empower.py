"""Find the smallest LLM the deterministic scaffold genuinely empowers.

The mirror does the facts (faithful by construction). A tiny model is asked ONLY to add flair to a
fact-true line; a GUARD rejects any output that invents a number/score, a turn/position/team, a
wrong sport, or mangles/drops the names — falling back to the mirror. So output is factually safe
regardless of model. The honest metric is then the FALLBACK rate: how often the model's color was
unusable. Low fallback + coherent samples = genuinely empowered. High fallback = safe but pointless
(you just got the mirror back).

Coherence a regex can't measure — read transcripts/empower_samples_*.txt with your own eyes.

    python -m tools.empower --models smollm2:135m,tinyllama:1.1b,phi3:3.8b --n 12
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request

from oradio_engine.speech import Grammar, roles_from_tags

OLLAMA = "http://127.0.0.1:11434/api/generate"
TEAMS = ["mercedes", "ferrari", "red bull", "mclaren", "aston martin", "racing point",
         "alpine", "williams", "haas", "alphatauri", "sauber", "racing bulls"]
WRONG_SPORT = ["soccer", "nba", "nascar", "basketball", "football", "hockey", "baseball", "tennis"]


def llm(prompt, model, n_predict=32):
    body = {"model": model, "prompt": prompt, "stream": False, "think": False,
            "options": {"temperature": 0.6, "num_predict": n_predict}}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req, timeout=120))
    return re.sub(r"(?is)<think>.*?</think>", "", resp.get("response") or "").strip()


def unfaithful(text, actor, obj, drivers):
    low = text.lower()
    if not text:
        return True
    if actor and actor.lower() not in low:
        return True                                            # dropped the subject
    if obj and obj[:1].isupper() and obj.lower() not in low:
        return True                                            # dropped/mangled the named rival
    return bool(
        re.search(r"\b\d+\s*[-–]\s*\d+\b", text)          # invented score "4-2"
        or re.search(r"\b\d+\s*(minute|minutes|second|seconds|goal|goals|point|points)\b", low)
        or re.search(r"\bturn \d+", low)
        or re.search(r"\b(p\d|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(place|position)\b", low)
        or any(tm in low for tm in TEAMS)
        or any(w in low for w in WRONG_SPORT)
        or any(d.lower() in low for d in drivers if d not in (actor, obj))
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="smollm2:135m,tinyllama:1.1b,phi3:3.8b")
    ap.add_argument("--n", type=int, default=12)
    args = ap.parse_args()

    rows = json.load(open("data/f1_barcelona_2026.json", encoding="utf-8"))
    structured = [r for r in rows if any(t.startswith("action:") for t in r["tags"])][:args.n]
    drivers = set()
    for r in rows:
        for t in r["tags"]:
            if t.startswith("actor:"):
                drivers.add(t.split(":", 1)[1])
            if t.startswith("object:") and t.split(":", 1)[1][:1].isupper():
                drivers.add(t.split(":", 1)[1])
    G = Grammar.from_file("data/grammars/intern.json", verbs="data/english/irregular_verbs.json")

    n = len(structured)
    print(f"\n=== smallest-LLM empowerment ladder · {n} events (guard = factual safety, not coherence) ===\n")
    print(f"{'model':16} {'bare faithful':>14} {'color kept (usable)':>20} {'fell back to mirror':>20}")
    print("-" * 74)

    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        bare_ok = 0; fell = 0; samples = []
        for r in structured:
            roles = roles_from_tags(r["tags"])
            actor, obj = roles.get("actor", ""), roles.get("object", "")
            det_line = G.line(roles, key=r["tags"][0])
            lap = next((t.split(":", 1)[1] for t in r["tags"] if t.startswith("lap:")), "")
            try:
                bare = llm(f"F1 commentator, one short sentence. Event: lap {lap} {actor} {roles.get('action','')} {obj}", model)
                if not unfaithful(bare, actor, obj, drivers):
                    bare_ok += 1
                prompt = (f"Rewrite with vivid flair. Keep the name(s) {actor}"
                          f"{(' and ' + obj) if obj and obj[:1].isupper() else ''}. "
                          f"Do NOT add positions, lap numbers, scores, teams, other sports, or other drivers. "
                          f"One sentence.\nLine: {det_line}")
                colored = llm(prompt, model)
            except Exception:
                bare, colored = "(error)", ""
            if unfaithful(colored, actor, obj, drivers):
                final = det_line; fell += 1
            else:
                final = colored
            samples.append((det_line, bare, final))
        kept = n - fell
        print(f"{model:16} {f'{100*bare_ok//n}% ({bare_ok}/{n})':>14} {f'{100*kept//n}% ({kept}/{n})':>20} {f'{100*fell//n}% ({fell}/{n})':>20}")
        fn = f"transcripts/empower_samples_{model.replace(':','_').replace('/','_')}.txt"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(f"# {model}: MIRROR | BARE (unscaffolded) | FINAL (mirror+guarded color)\n\n")
            for det, bare, final in samples:
                f.write(f"  MIRROR: {det}\n  BARE  : {bare}\n  FINAL : {final}\n\n")

    print("\nverdict = the smallest model with HIGH color-kept AND coherent samples (read the files).")


if __name__ == "__main__":
    main()
