"""Receipt for the headline claim — "LLM -> code".

A LOCAL model AUTHORS a deterministic grammar from a natural-language persona; we then VALIDATE
the artifact (it parses, loads, and narrates a tape faithfully). If it passes, it's saved as a
usable .json the pure runtime can use forever — the model ran ONCE, at authoring time. This is the
compile-time/runtime split, demonstrated end to end, not asserted.

    python -m tools.author --model qwen3:8b "a calm BBC documentary narrator"

Honest: a local model may emit invalid JSON; we retry once and report failures plainly.
"""
from __future__ import annotations

import argparse
import json
import re

from oradio_engine.speech import Grammar

EXAMPLE = json.load(open("data/grammars/intern.json", encoding="utf-8"))
VERBS = "data/english/irregular_verbs.json"
SAMPLE = {"actor": "Hamilton", "action": "overtake", "object": "Norris", "valence": "hype", "_key": "1"}
UNIVERSE = {"Hamilton", "Norris", "Verstappen", "Leclerc", "Russell"}


def _gen(prompt, model):
    from llm_client import complete       # pluggable: local Ollama or any OpenAI-compatible
    return complete(prompt, model=model, temperature=0.3, num_predict=400)[0]


def author(persona, model):
    prompt = (
        f'Output ONLY a JSON narration grammar for this persona: "{persona}".\n'
        f'Copy this structure exactly, change only the string values to fit the persona:\n'
        f'{json.dumps(EXAMPLE, indent=2)}\n'
        f'Keep the key "form" EXACTLY as "{EXAMPLE["form"]}" and "tense" as "past". '
        f'"codas" maps a mood to a list of short ending strings. Output JSON only, no prose.'
    )
    raw = _gen(prompt, model)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON found in model output")
    return json.loads(m.group(0))


def validate(spec):
    """Compile + run the authored grammar; faithful narration of the sample?"""
    g = Grammar(spec, json.load(open(VERBS, encoding="utf-8")))
    line = g.line(SAMPLE, key="1")
    faithful = ("Hamilton" in line and "Norris" in line
                and not (UNIVERSE - {"Hamilton", "Norris"}) & set(re.findall(r"[A-Z][a-z]+", line)))
    grammatical = "overtook" in line.lower()          # correct past tense, not "overtake"
    return faithful and grammatical, line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("personas", nargs="*", default=["a calm BBC documentary narrator", "a hyped arcade announcer"])
    args = ap.parse_args()

    print(f"\n=== LLM -> code receipt · authoring grammars with {args.model} ===\n")
    for persona in args.personas:
        status = ok = line = None
        for attempt in (1, 2):
            try:
                spec = author(persona, args.model)
                ok, line = validate(spec)
                if ok:
                    slug = re.sub(r"[^a-z0-9]+", "_", persona.lower()).strip("_")[:30]
                    path = f"data/grammars/authored_{slug}.json"
                    json.dump(spec, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
                    status = f"COMPILED + faithful -> {path}"
                    break
                status = "parsed but narration not faithful"
            except Exception as exc:
                status = f"invalid (attempt {attempt}): {type(exc).__name__}: {str(exc)[:60]}"
        print(f'persona: "{persona}"')
        print(f"  {status}")
        if line:
            print(f"  sample: {line}")
        print()


if __name__ == "__main__":
    main()
