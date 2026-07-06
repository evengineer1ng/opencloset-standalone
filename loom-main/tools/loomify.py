"""loom, headless — the booth without a GUI. Idea OR tape -> threaded narration -> spoken.

  python -m tools.loomify --tape data/f1_barcelona_2026.json --voice town_crier --speak
  python -m tools.loomify --idea "a haunted office printer" --depth 2 --speak

Authors a tape from an idea via the pluggable LLM (llm_client — local or any OpenAI-compatible),
or loads an existing tape; runs the deterministic pipeline (threads), narrates in a vetted voice,
and speaks it cross-platform (speech_out: Windows/Mac/Linux/Android-Termux).

KEY: only --idea (authoring) touches an LLM. --tape playback is pure Python + KB files + TTS — no
GPU, no model — so it runs anywhere, phone included. The model thinks once; the tape replays free.
"""
from __future__ import annotations

import argparse
import json
import re

from oradio_engine.antenna import load_tape
from oradio_engine.speech import Grammar, roles_from_tags
from oradio_engine.thread import narrate_salient

SCHEMA = """Output ONLY a JSON object: {"tape": [...rows...]}.
A row = {"tags": ["actor:NAME","action:VERB","object:THING","magnitude:NUMBER","unit:UNIT","valence:hype|alarm|calm"],
         "priority": 0.7}. Rules:
- actor and action REQUIRED. action MUST be a plain present-tense verb lemma (spike, drop, jam, open,
  fail, whisper) — the engine conjugates it. Do NOT past-tense it.
- object/magnitude/unit/valence optional. valence is exactly hype/alarm/calm if present.
- Reuse the SAME few actor names across rows so a thread forms. 7 rows telling a tiny story.
Output JSON only, no prose."""


def author_tape(idea, feedback=""):
    from llm_client import complete
    raw, _ = complete(f"{SCHEMA}\n\nDOMAIN IDEA: \"{idea}\"\n{feedback}", num_predict=700, temperature=0.4)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in model output")
    return json.loads(m.group(0))["tape"]


def _rows_to_events(rows):
    out = []
    for r in rows:
        e = roles_from_tags(r.get("tags", []))
        e["lap"] = next((t.split(":", 1)[1] for t in r.get("tags", []) if t.startswith("lap:")), "")
        e["priority"] = r.get("priority", 0.7)
        out.append(e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idea", default="")
    ap.add_argument("--tape", default="")
    ap.add_argument("--voice", default="intern")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--flavour", default="both")
    ap.add_argument("--salience", type=float, default=0.0)
    ap.add_argument("--rules", default="")
    ap.add_argument("--speak", action="store_true")
    args = ap.parse_args()

    if args.tape:
        events = load_tape(args.tape)
        print(f'=== loom · tape={args.tape} · voice={args.voice} ===\n')
    elif args.idea:
        print(f'=== loom · idea="{args.idea}" · voice={args.voice} ===\n')
        tape, feedback = None, ""
        for attempt in (1, 2, 3):
            try:
                tape = author_tape(args.idea, feedback); break
            except Exception as exc:
                feedback = f"Previous output failed: {exc}. Output STRICT JSON only."
                print(f"  (author attempt {attempt} rejected: {type(exc).__name__})")
        if not tape:
            raise SystemExit("could not author a tape in 3 tries")
        events = _rows_to_events(tape)
    else:
        raise SystemExit("give --idea \"...\" or --tape path")

    grammar = Grammar.from_file(f"data/grammars/{args.voice}.json", verbs="data/english/irregular_verbs.json")
    rules = json.load(open(args.rules, encoding="utf-8")) if args.rules else None
    stories = narrate_salient(events, grammar, depth=args.depth, rules=rules,
                              min_priority=args.salience, flavour=args.flavour)

    speak = None
    if args.speak:
        from speech_out import backend, say
        speak = say
        print(f"[speaking via {backend()}]\n")
    for lap, line in stories:
        print((f"[lap {lap}] " if lap else "") + line)
        if speak:
            speak(line)


if __name__ == "__main__":
    main()
