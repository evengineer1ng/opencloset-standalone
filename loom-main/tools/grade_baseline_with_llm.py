#!/usr/bin/env python3
"""Grade the baseline coordinate-map tape with a local LLM (llama.cpp :9080).

Produces a concept_id -> label map for `populate_baseline_map.py --labels-json`.
The LLM only NAMES the neighborhood (region) per concept; Loom still assigns the address.
Egress stays deterministic: these labels are advisory priors, blended with dictionary+thesaurus.

    python loom-main/tools/grade_baseline_with_llm.py \
        loom-main/outputs/baseline-population-sample/llm_baseline_tape.json \
        -o loom-main/outputs/baseline-population-sample/llm_labels_27b.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request

# stdout is often redirected to a cp1252 file on Windows; Wikipedia titles can carry non-cp1252
# characters (e.g. transliteration marks). Force UTF-8 so a print never kills the run.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SYS = (
    "You are a strict semantic-region grader. Given a TITLE and a small set of CANDIDATE regions "
    "(each with example anchor words), score every candidate 0.0-1.0 for how well it fits the "
    "title's meaning, choose the single best primary_region (MUST be one of the candidates), and "
    "list up to 2 secondary_regions from the candidates. "
    "Choose 'identity' ONLY for an actual person, name, or biography. Do not default to identity. "
    "Choose 'context' ONLY for genuinely meta titles: lists, indexes, disambiguation pages, "
    "overviews, outlines, or stubs with no single subject. A single concrete topic is NEVER "
    "'context' -- it always has a real region. Examples: Albedo->science, Anarchism->governance, "
    "Bankruptcy->commerce, Calligraphy->culture, Arachnophobia->survival, Submarine->artifact, "
    "Cheetah->organism. Pick the most specific region that fits the subject. "
    "Output ONLY a compact JSON object, no prose, no markdown:\n"
    '{"primary_region":"<candidate>","region_scores":{"<candidate>":0.0},'
    '"secondary_regions":["<candidate>"]}'
)


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _extract_json(text: str):
    text = re.sub(r"```[a-zA-Z]*", "", text or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def grade_one(url: str, model: str, row: dict, timeout: float):
    p = row["payload"]
    cands = p["candidate_regions"]
    user = ("title: " + p.get("title", "")
            + "\ncandidate_regions: " + json.dumps(cands)
            + "\nanchors: " + json.dumps(p.get("anchor_context", {}))
            + "\ntags: " + json.dumps(p.get("prompt_tags", []))
            + " /no_think")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        "temperature": 0.2, "max_tokens": 320, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        resp = _post(url, body, timeout)
        text = resp["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - report any transport/parse failure as a row error
        return None, "http_error:" + str(exc)[:90]
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return None, "no_json:" + (text or "")[:60].replace("\n", " ")
    scores = {r: float(obj.get("region_scores", {}).get(r, 0.0) or 0.0) for r in cands}
    prim = obj.get("primary_region")
    if prim not in cands:
        prim = max(scores, key=scores.get) if any(scores.values()) else cands[0]
    sec = [r for r in (obj.get("secondary_regions") or []) if r in cands and r != prim][:2]
    return {"primary_region": prim, "region_scores": scores, "secondary_regions": sec}, None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("tape")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:9080/v1/chat/completions")
    ap.add_argument("--model", default="qwen3.6-27b")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args(argv)

    rows = json.load(open(args.tape, encoding="utf-8"))
    if args.max:
        rows = rows[:args.max]
    labels, hist, errs, t0 = {}, {}, 0, time.time()

    def _save():
        json.dump(labels, open(args.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    try:
        for i, row in enumerate(rows):
            cid = row["payload"]["concept_id"]
            lab, err = grade_one(args.url, args.model, row, args.timeout)
            title = row["payload"].get("title", "")
            if err:
                errs += 1
                print(f"[{i+1}/{len(rows)}] ERR {err}  ({title})", flush=True)
                continue
            labels[cid] = lab
            hist[lab["primary_region"]] = hist.get(lab["primary_region"], 0) + 1
            print(f"[{i+1}/{len(rows)}] {title:<30} -> {lab['primary_region']}", flush=True)
            _save()  # incremental: a crash never wipes prior work
    finally:
        _save()
    print(f"\nwrote {len(labels)} labels ({errs} errors) in {time.time()-t0:.0f}s -> {args.out}")
    print("primary_region distribution:", dict(sorted(hist.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
