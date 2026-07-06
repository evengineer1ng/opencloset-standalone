#!/usr/bin/env python3
"""
oradio_tape_synth_v2.py

Deterministic O-Radio tape synthesis chatbot.

Purpose:
- Load 1..N Loom/O-Radio tape files from a folder.
- Answer a user query from the tapes with rough but real synthesis.
- Separate MEANING from EVIDENCE.
- Use inline footnote citations in meaning.
- Keep a running session tape.
- Optionally use DuckDuckGo to repair low-confidence concepts.
- Search results become tape rows; search never answers directly.

No LLMs. No embeddings. No hidden reasoning.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# -----------------------------
# Config
# -----------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "target_confidence": 0.74,
    "word_confidence_threshold": 0.62,
    "relation_confidence_threshold": 0.58,
    "meaning_confidence_threshold": 0.62,
    "evidence_confidence_threshold": 0.56,
    "max_search_rounds": 5,
    "max_results_per_search": 5,
    "fetch_timeout_seconds": 9,
    "max_native_evidence": 8,
    "max_total_evidence": 12,
}

# Deliberately aggressive. This app should search "OpenAI", not "any".
FILLERS = {
    "the","a","an","of","to","for","is","are","was","were","did","does","do",
    "can","could","would","should","and","or","if","it","this","that","today",
    "now","me","my","we","our","you","your","in","on","at","by","with","from",
    "as","be","been","being","into","about","what","who","when","where","why",
    "how","which","anything","something","please","tell","ask","show","explain",
    "vs","versus","compare","than","there","their","his","her","they","them",
    "any","all","some","thing","things","stuff","one","ones","these","those",
    "here","there","over","under","through","around","like","just","really",
    "very","much","more","less","kind","sort","maybe","perhaps","query",
    "question","answer","source","tape","tapes","loaded","current","folder",
}

# Query intent hints. Earlier entries win.
TRANSFORM_HINTS = [
    ("why", "causal"),
    ("because", "causal"),
    ("cause", "causal"),
    ("how", "sequence"),
    ("after", "sequence"),
    ("before", "sequence"),
    ("next", "sequence"),
    ("then", "sequence"),
    ("when", "time"),
    ("lap", "time"),
    ("tick", "time"),
    ("count", "count"),
    ("many", "count"),
    ("number", "count"),
    ("most", "rank"),
    ("highest", "rank"),
    ("biggest", "rank"),
    ("top", "rank"),
    ("leader", "rank"),
    ("best", "rank"),
    ("worst", "rank"),
    ("good", "evaluation"),
    ("bad", "evaluation"),
    ("important", "evaluation"),
    ("noteworthy", "evaluation"),
    ("coefficient", "interpretive"),
    ("factor", "interpretive"),
    ("weight", "interpretive"),
    ("recap", "summary"),
    ("summary", "summary"),
    ("summarize", "summary"),
    ("overview", "summary"),
    ("happened", "summary"),
]

VERBS_PAST = {
    "make": "made", "win": "won", "lose": "lost", "draw": "drew", "steal": "stole",
    "split": "split", "build": "built", "lead": "led", "run": "ran", "go": "went",
    "come": "came", "rise": "rose", "fall": "fell", "write": "wrote",
    "publish": "published", "return": "returned", "observe": "observed",
    "observed": "observed", "say": "said", "emit": "emitted", "start_mixtape": "started mixtape",
    "play": "played", "pause": "paused", "adopt": "adopted", "establish": "established",
    "enable": "enabled", "connect": "connected", "standardize": "standardized",
}

CONTROL_ACTORS = {"booth", "player", "recorder"}
CONTROL_ACTIONS = {"play", "pause", "start_mixtape", "save", "load", "emit"}


# -----------------------------
# Data classes
# -----------------------------

@dataclasses.dataclass
class Row:
    tape_id: str
    row_index: int
    actor: str
    action: str
    object: str
    lap: int
    priority: float
    valence: str
    source: str = ""
    source_domain: str = ""
    headline: str = ""
    thread: str = ""
    topic: str = ""
    time: str = ""
    kind: str = "event"
    source_kind: str = "source"  # source | search | session | metadata
    raw: Dict[str, Any] = dataclasses.field(default_factory=dict)
    tags: List[str] = dataclasses.field(default_factory=list)

    def text(self) -> str:
        parts = [
            self.actor, self.action, self.object, self.headline, self.thread, self.topic,
            self.source_domain, self.source, " ".join(self.tags)
        ]
        return " ".join(str(p) for p in parts if p)

    def content_actor(self) -> str:
        return str(self.raw.get("content_actor") or self.actor or "").strip()

    def content_action(self) -> str:
        return str(self.raw.get("content_action") or self.action or "").strip()

    def content_object(self) -> str:
        return str(self.raw.get("content_object") or self.raw.get("content_text") or self.object or self.headline or "").strip()

    def is_control(self) -> bool:
        return self.actor.lower() in CONTROL_ACTORS and self.action.lower() in CONTROL_ACTIONS

    def clause(self) -> str:
        # Content view: unwrap "booth played X" into "X" when available.
        if self.is_control() and self.content_object():
            return clean_text(self.content_object(), 240)
        actor = self.content_actor() or self.source_domain or "source"
        action = VERBS_PAST.get(self.content_action().lower(), regular_past(self.content_action()))
        obj = self.content_object()
        return " ".join(x for x in [actor, action, obj] if x).strip()

    def ref_id(self) -> str:
        return f"{self.tape_id}#lap{self.lap}:row{self.row_index}"


@dataclasses.dataclass
class EvidenceHit:
    row: Row
    score: float
    token_score: float
    priority_score: float
    chronology_score: float
    source_score: float
    matched_terms: List[str]
    native: bool

    @property
    def confidence(self) -> float:
        return self.score


@dataclasses.dataclass
class ConfidenceGraph:
    total: float
    words: List[Dict[str, Any]]
    relation: float
    meaning: float
    evidence: List[EvidenceHit]

    def weakest_nodes(self, cfg: Dict[str, Any], searched: set[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []

        for w in self.words:
            if w["confidence"] < cfg["word_confidence_threshold"]:
                key = f"word:{w['item']}"
                if key not in searched:
                    items.append({
                        "type": "word",
                        "item": w["item"],
                        "confidence": w["confidence"],
                        "gap": 1 - w["confidence"],
                        "search_query": w["item"],
                        "reason": "low word confidence",
                    })

        if self.relation < cfg["relation_confidence_threshold"]:
            key = "relation:query"
            if key not in searched:
                items.append({
                    "type": "relation",
                    "item": "query_to_source",
                    "confidence": self.relation,
                    "gap": 1 - self.relation,
                    "search_query": "",
                    "reason": "low query-to-source relation confidence",
                })

        if self.meaning < cfg["meaning_confidence_threshold"]:
            key = "meaning:query"
            if key not in searched:
                items.append({
                    "type": "meaning",
                    "item": "answer_shape",
                    "confidence": self.meaning,
                    "gap": 1 - self.meaning,
                    "search_query": "",
                    "reason": "low answer meaning confidence",
                })

        weak_evidence = [
            e for e in self.evidence[:5]
            if e.confidence < cfg["evidence_confidence_threshold"]
            and e.row.kind != "search_failure"
            and "search failed for" not in lower(e.row.object)
        ][:2]
        for e in weak_evidence:
            q = " ".join([e.row.headline, e.row.content_object(), e.row.content_actor(), e.row.content_action()]).strip()[:180]
            key = f"evidence:{stable_id(q)}"
            if q and key not in searched:
                items.append({
                    "type": "evidence",
                    "item": q,
                    "confidence": e.confidence,
                    "gap": 1 - e.confidence,
                    "search_query": q,
                    "reason": "low evidence confidence",
                })

        items.sort(key=lambda x: (-x["gap"], x["type"], x["item"]))
        return items


# -----------------------------
# Utilities
# -----------------------------

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clamp01(n: Any) -> float:
    try:
        x = float(n)
    except Exception:
        return 0.0
    if not math.isfinite(x):
        return 0.0
    return max(0.0, min(1.0, x))


def stable_id(*parts: Any) -> str:
    text = "\n".join(str(p) for p in parts if p is not None)
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def clean_text(x: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(x or "")).strip()
    return text[:limit]


def lower(x: Any) -> str:
    return str(x or "").lower()


def regular_past(v: str) -> str:
    v = str(v or "emit").strip().lower()
    if not v:
        return "emitted"
    if v.endswith("e"):
        return v + "d"
    if len(v) > 2 and v.endswith("y") and v[-2] not in "aeiou":
        return v[:-1] + "ied"
    return v + "ed"


def split_alpha_num(text: Any) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", lower(text)) if t]


def unique(seq: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in seq:
        t = lower(item).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def quoted_terms(query: str) -> List[str]:
    return [m.group(1).strip().lower() for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'', query) for m in [m] if (m.group(1) or m.group(2))]


def query_focus_phrase(query: str) -> str:
    q = re.sub(r"[?!.]+$", "", query.strip())
    patterns = [
        r"^\s*what\s+is\s+(.+)$",
        r"^\s*who\s+is\s+(.+)$",
        r"^\s*what\s+did\s+(.+?)\s+do\s*$",
        r"^\s*how\s+did\s+(.+?)\s+happen\s*$",
        r"^\s*why\s+(.+)$",
    ]
    for pat in patterns:
        m = re.match(pat, q, re.I)
        if m:
            return clean_text(m.group(1), 120)
    toks = question_tokens(q)
    return " ".join(toks[:5])


def question_tokens(query: str) -> List[str]:
    qterms = set(quoted_terms(query))
    out = []
    for t in split_alpha_num(query):
        if t in FILLERS and t not in qterms:
            continue
        if len(t) <= 1 and t not in qterms:
            continue
        # Do not use generic pronouns/determiners as debt nodes.
        out.append(t)
    return unique(out)


def char_trigrams(token: str) -> List[str]:
    token = lower(token)
    if len(token) < 3:
        return [token] if token else []
    return [token[i:i+3] for i in range(len(token) - 2)]


def token_affinity(left: str, right: str) -> float:
    a = lower(left).strip()
    b = lower(right).strip()
    if not a or not b:
        return 0.0
    # Critical bug fix: "openai" must not match "a", "open", or "opened".
    # Fuzzy matching is intentionally conservative; search should repair unknown terms.
    if min(len(a), len(b)) < 3:
        return 1.0 if a == b else 0.0
    if a == b:
        return 1.0

    min_len = min(len(a), len(b))
    max_len = max(len(a), len(b))
    length_ratio = min_len / max_len if max_len else 0.0

    # Substring is only high confidence when it is basically the same lexical unit,
    # e.g. protocol/protocols, not open/openai.
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a) and length_ratio >= 0.82:
        return 0.92

    prefix = 0
    while prefix < len(a) and prefix < len(b) and a[prefix] == b[prefix]:
        prefix += 1
    prefix_score = 0.0
    if prefix >= 5 and (prefix / max_len) >= 0.80:
        prefix_score = min(0.86, 0.45 + (prefix / max_len) * 0.4)

    ag = set(char_trigrams(a))
    bg = set(char_trigrams(b))
    union = ag | bg
    inter = ag & bg
    tri = len(inter) / len(union) if union else 0.0
    tri_score = tri if tri >= 0.58 else 0.0

    return max(prefix_score, tri_score)


def best_token_affinity(token: str, candidates: Sequence[str]) -> Tuple[float, str]:
    best = 0.0
    best_c = ""
    for c in candidates:
        score = token_affinity(token, c)
        if score > best:
            best = score
            best_c = c
    return clamp01(best), best_c


def domain_of(raw: Any) -> str:
    s = str(raw or "")
    try:
        u = urllib.parse.urlparse(s)
        if u.hostname:
            return re.sub(r"^www\.", "", u.hostname.lower())
    except Exception:
        pass
    m = re.search(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", s, re.I)
    return re.sub(r"^www\.", "", m.group(1).lower()) if m else ""


def tag_value(row: Dict[str, Any], key: str) -> str:
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    prefix = key + ":"
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(prefix):
            return tag[len(prefix):]
    return ""


def extract_yamlish_value(text: str, key: str) -> str:
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text)
    return m.group(1).strip() if m else ""


# -----------------------------
# Loading tapes
# -----------------------------

def normalize_row(item: Any, i: int, tape_id: str, source_kind: str = "source") -> Row:
    if isinstance(item, str):
        return Row(
            tape_id=tape_id, row_index=i, actor="line", action="say",
            object=clean_text(item, 1000), lap=i + 1, priority=0.5, valence="calm",
            kind="line", raw={"text": item, "_source_kind": source_kind}, source_kind=source_kind
        )

    row = item if isinstance(item, dict) else {"object": item}
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}

    actor = clean_text(
        tag_value(row, "actor") or row.get("content_actor") or row.get("actor") or raw.get("actor") or row.get("source") or "tape",
        180
    )
    action = clean_text(
        tag_value(row, "action") or row.get("content_action") or row.get("action") or raw.get("action") or row.get("kind") or "emit",
        80
    )
    obj = clean_text(
        tag_value(row, "object") or row.get("content_object") or row.get("content_text") or row.get("object") or raw.get("object") or row.get("title") or row.get("text") or row.get("body") or row.get("message") or "",
        1000
    )
    source = clean_text(tag_value(row, "source") or row.get("source") or row.get("url") or raw.get("link") or raw.get("source") or "", 500)
    source_domain = clean_text(row.get("source_domain") or domain_of(source), 120)
    headline = clean_text(row.get("headline") or raw.get("title") or row.get("title") or "", 500)
    thread = clean_text(tag_value(row, "thread") or row.get("thread") or "", 200)
    topic = clean_text(tag_value(row, "topic") or row.get("topic") or "", 100)
    time_value = clean_text(tag_value(row, "time") or row.get("time") or raw.get("published") or "", 100)
    kind = clean_text(row.get("kind") or tag_value(row, "kind") or source_kind, 100)
    tags = [str(t) for t in row.get("tags", [])] if isinstance(row.get("tags"), list) else []
    valence = clean_text(tag_value(row, "valence") or row.get("valence") or "calm", 40)
    priority = clamp01(row.get("priority", raw.get("priority", 0.5)))
    lap = int(float(row.get("lap", i + 1) or i + 1))

    raw_copy = dict(row)
    raw_copy["_source_kind"] = source_kind

    return Row(
        tape_id=tape_id,
        row_index=i,
        actor=actor,
        action=action,
        object=obj,
        lap=lap,
        priority=priority,
        valence=valence,
        source=source,
        source_domain=source_domain,
        headline=headline,
        thread=thread,
        topic=topic,
        time=time_value,
        kind=kind,
        source_kind=source_kind,
        raw=raw_copy,
        tags=tags,
    )


def parse_json_or_ndjson(path: Path) -> List[Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError(f"{path} JSON root is not a list")
        return data
    rows = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def parse_text_metadata_tape(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows: List[Dict[str, Any]] = []
    universe = extract_yamlish_value(text, "universe") or extract_yamlish_value(text, "oradio") or path.stem
    intent = extract_yamlish_value(text, "intent") or universe
    rows.append({
        "actor": path.name,
        "action": "define",
        "object": universe,
        "valence": "calm",
        "lap": 1,
        "priority": 0.7,
        "source": str(path),
        "kind": "metadata",
        "thread": f"file:{path.stem}",
        "evidence": "loaded metadata file",
    })
    if intent and intent != universe:
        rows.append({
            "actor": path.name,
            "action": "intend",
            "object": intent,
            "valence": "calm",
            "lap": 2,
            "priority": 0.65,
            "source": str(path),
            "kind": "metadata",
            "thread": f"file:{path.stem}",
            "evidence": "intent field",
        })
    for n, line in enumerate(text.splitlines(), start=3):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if any(k in line for k in ("plugin:", "path:", "from:", "to:", "transform:", "name:", "connection:")):
            rows.append({
                "actor": path.name,
                "action": "declare",
                "object": line,
                "valence": "calm",
                "lap": n,
                "priority": 0.45,
                "source": str(path),
                "kind": "metadata",
                "thread": f"file:{path.stem}",
            })
    return rows


def load_tape_file(path: Path) -> List[Row]:
    tape_id = path.name
    suffixes = "".join(path.suffixes).lower()
    if path.suffix.lower() in {".oradio", ".loom"}:
        items = parse_text_metadata_tape(path)
    elif path.suffix.lower() in {".json", ".ndjson", ".jsonl"} or ".tape" in suffixes:
        items = parse_json_or_ndjson(path)
    else:
        return []
    return [normalize_row(item, i, tape_id, "source") for i, item in enumerate(items)]


def load_tapes(folder_or_file: Path) -> List[Row]:
    if folder_or_file.is_file():
        paths = [folder_or_file]
    else:
        pats = ["*.tape.json", "*.tape.ndjson", "*.json", "*.ndjson", "*.jsonl", "*.oradio", "*.loom"]
        seen = set()
        paths = []
        for pat in pats:
            for p in sorted(folder_or_file.glob(pat)):
                if p not in seen:
                    seen.add(p)
                    paths.append(p)
    rows: List[Row] = []
    for path in paths:
        try:
            loaded = load_tape_file(path)
            rows.extend(loaded)
        except Exception as e:
            sys.stderr.write(f"[warn] could not load {path}: {e}\n")
    return rows


# -----------------------------
# Confidence and retrieval
# -----------------------------

def event_tokens(row: Row) -> List[str]:
    # Failed searches are telemetry, not semantic evidence. Do not let the failed query text
    # make unknown concepts look "known".
    if row.kind == "search_failure" or "search failed for" in lower(row.object):
        return ["search_failure", "duckduckgo"]
    return unique(split_alpha_num(row.text()))


def tape_vocabulary(rows: Sequence[Row]) -> List[str]:
    toks: List[str] = []
    for r in rows:
        # Control rows are allowed, but content view text dominates.
        toks.extend(event_tokens(r))
    return unique(toks)


def infer_transform(query: str) -> str:
    q = lower(query)
    if re.match(r"^\s*what\s+is\b", q):
        return "define"
    if re.match(r"^\s*what\s+did\b", q):
        return "actor_action"
    if "coefficient" in q or "factor" in q or "weight" in q:
        return "interpretive"
    toks = set(question_tokens(query))
    for hint, transform in TRANSFORM_HINTS:
        if hint in toks or re.search(rf"\b{re.escape(hint)}\b", q):
            return transform
    return "summary"


def score_words(query: str, rows: Sequence[Row]) -> List[Dict[str, Any]]:
    toks = question_tokens(query)
    vocab = tape_vocabulary(rows)
    out: List[Dict[str, Any]] = []
    for t in toks:
        score, match = best_token_affinity(t, vocab)
        out.append({
            "type": "word",
            "item": t,
            "confidence": round(score, 3),
            "gap": round(1 - score, 3),
            "match": match,
            "reason": "covered by tape vocabulary" if score >= DEFAULT_CONFIG["word_confidence_threshold"] else "foreign or weakly covered by tape vocabulary",
        })
    return out


def score_relation(query: str, rows: Sequence[Row]) -> float:
    toks = question_tokens(query)
    if not toks:
        return 0.0
    word_scores = [w["confidence"] for w in score_words(query, rows)]
    avg = sum(word_scores) / max(1, len(word_scores))
    direct = sum(1 for s in word_scores if s >= 0.92) / max(1, len(word_scores))
    row_hits = 0
    for row in rows:
        rt = event_tokens(row)
        if any(best_token_affinity(t, rt)[0] >= 0.72 for t in toks):
            row_hits += 1
    row_coverage = min(1.0, row_hits / max(3, min(len(rows), len(toks) * 3))) if rows else 0
    return round(clamp01(avg * 0.52 + direct * 0.18 + row_coverage * 0.30), 3)


def score_evidence(query: str, rows: Sequence[Row]) -> List[EvidenceHit]:
    toks = question_tokens(query)
    hits: List[EvidenceHit] = []
    if not rows:
        return hits
    for row in rows:
        rt = event_tokens(row)
        matched = []
        scores = []
        for t in toks:
            s, match = best_token_affinity(t, rt)
            scores.append(s)
            if s >= 0.72:
                matched.append(f"{t}->{match}" if match and match != t else t)
        lexical = sum(scores) / max(1, len(scores)) if toks else 0.2
        priority = clamp01(row.priority)
        chronology = 0.12 if row.time or any(tag.startswith("chronology:") for tag in row.tags) else 0.0
        source_score = 0.08 if row.source_kind == "search" or row.kind == "search_result" else 0.0
        control_penalty = -0.18 if row.is_control() and not row.raw.get("content_actor") else 0.0
        is_search_failure = row.kind == "search_failure" or "search failed for" in lower(row.object)
        score = clamp01(lexical * 0.70 + priority * 0.18 + chronology + source_score + control_penalty)
        if is_search_failure:
            score = min(score, 0.12)
            matched = []
        # Do not let high-priority but lexically unrelated rows become fake evidence.
        if lexical < 0.15 and not is_search_failure:
            continue
        if score > 0.05:
            hits.append(EvidenceHit(
                row=row,
                score=round(score, 3),
                token_score=round(lexical, 3),
                priority_score=round(priority, 3),
                chronology_score=round(chronology, 3),
                source_score=round(source_score, 3),
                matched_terms=matched,
                native=(row.source_kind != "search" and row.kind != "search_result"),
            ))
    hits.sort(key=lambda e: (-e.score, not e.native, e.row.tape_id, e.row.lap, e.row.row_index))
    return hits


def score_meaning(query: str, rows: Sequence[Row]) -> float:
    relation = score_relation(query, rows)
    transform = infer_transform(query)
    words = score_words(query, rows)
    anchors = any(w["confidence"] >= 0.82 for w in words)
    token_mass = min(1, len(question_tokens(query)) / 4)
    transform_bonus = 0.24 if transform not in {"describe", "summary"} else 0.10
    return round(clamp01(relation * 0.50 + transform_bonus + (0.18 if anchors else 0.05) + token_mass * 0.10), 3)


def compute_confidence(query: str, rows: Sequence[Row]) -> ConfidenceGraph:
    words = score_words(query, rows)
    relation = score_relation(query, rows)
    meaning = score_meaning(query, rows)
    evidence = score_evidence(query, rows)

    word_avg = sum(w["confidence"] for w in words) / max(1, len(words)) if words else 0.35
    top = evidence[:4]
    evidence_avg = sum(e.confidence for e in top) / max(1, len(top)) if top else 0.0
    total = clamp01(word_avg * 0.28 + relation * 0.24 + meaning * 0.24 + evidence_avg * 0.24)
    return ConfidenceGraph(round(total, 3), words, relation, meaning, evidence)


def has_critical_low_word(conf: ConfidenceGraph, cfg: Dict[str, Any]) -> bool:
    return any(w["confidence"] < cfg["word_confidence_threshold"] for w in conf.words)


# -----------------------------
# DuckDuckGo search -> tape
# -----------------------------

def fetch_url(url: str, timeout: int = 9) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 LoomOradioSynth/0.2",
            "Accept": "application/json,text/html,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def ddg_instant_answer(query: str, timeout: int = 9) -> List[Dict[str, Any]]:
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    })
    text = fetch_url(url, timeout=timeout)
    data = json.loads(text)
    results: List[Dict[str, Any]] = []
    if data.get("AbstractText"):
        results.append({
            "title": data.get("Heading") or query,
            "snippet": data.get("AbstractText"),
            "url": data.get("AbstractURL") or f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}",
            "domain": domain_of(data.get("AbstractURL") or ""),
        })
    for item in data.get("RelatedTopics", [])[:12]:
        if isinstance(item, dict) and item.get("Text"):
            first_url = item.get("FirstURL") or ""
            results.append({
                "title": item.get("Text", "").split(" - ")[0][:120],
                "snippet": item.get("Text"),
                "url": first_url,
                "domain": domain_of(first_url) or "duckduckgo.com",
            })
        elif isinstance(item, dict) and isinstance(item.get("Topics"), list):
            for sub in item["Topics"][:3]:
                if isinstance(sub, dict) and sub.get("Text"):
                    first_url = sub.get("FirstURL") or ""
                    results.append({
                        "title": sub.get("Text", "").split(" - ")[0][:120],
                        "snippet": sub.get("Text"),
                        "url": first_url,
                        "domain": domain_of(first_url) or "duckduckgo.com",
                    })
    return results


def ddg_search_tape(query: str, round_no: int, item_type: str, cfg: Dict[str, Any]) -> List[Row]:
    try:
        results = ddg_instant_answer(query, timeout=int(cfg["fetch_timeout_seconds"]))
    except Exception as e:
        item = {
            "actor": "duckduckgo",
            "action": "return",
            "object": f"search failed for {query}: {e}",
            "lap": round_no * 100 + 1,
            "priority": 0.2,
            "valence": "alarm",
            "source": f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}",
            "source_domain": "duckduckgo.com",
            "kind": "search_failure",
            "thread": f"search:{query}",
            "search_query": query,
        }
        return [normalize_row(item, 0, f"search:{stable_id(query, round_no)}", "search")]

    if not results:
        item = {
            "actor": "duckduckgo",
            "action": "return",
            "object": f"no instant-answer result for {query}",
            "lap": round_no * 100 + 1,
            "priority": 0.15,
            "valence": "calm",
            "source": f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}",
            "source_domain": "duckduckgo.com",
            "kind": "search_result",
            "thread": f"search:{query}",
            "search_query": query,
        }
        return [normalize_row(item, 0, f"search:{stable_id(query, round_no)}", "search")]

    rows: List[Row] = []
    for i, r in enumerate(results[: int(cfg["max_results_per_search"])]):
        obj = " - ".join(x for x in [r.get("title"), r.get("snippet")] if x)
        item = {
            "actor": r.get("domain") or "duckduckgo",
            "action": "return",
            "object": clean_text(obj, 1000),
            "lap": round_no * 100 + i + 1,
            "priority": clamp01(0.78 - i * 0.08),
            "valence": "calm",
            "source": r.get("url") or f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}",
            "source_domain": r.get("domain") or domain_of(r.get("url")) or "duckduckgo.com",
            "headline": r.get("title") or "",
            "kind": "search_result",
            "thread": f"search:{query}",
            "search_query": query,
            "claims": [{"subject": query, "predicate": "returned", "object": clean_text(obj, 240)}],
            "tags": [
                "actor:duckduckgo",
                "action:return",
                "kind:search_result",
                f"query:{query}",
                f"thread:search:{query}",
                "source:duckduckgo",
                f"repair_type:{item_type}",
            ],
        }
        rows.append(normalize_row(item, i, f"search:{stable_id(query, round_no)}", "search"))
    return rows


def acquire_confidence(query: str, rows: List[Row], cfg: Dict[str, Any], enable_search: bool) -> Tuple[List[Row], ConfidenceGraph, List[Dict[str, Any]], List[Row]]:
    working = list(rows)
    searched: set[str] = set()
    search_log: List[Dict[str, Any]] = []
    search_rows_all: List[Row] = []

    conf = compute_confidence(query, working)
    if not enable_search:
        return working, conf, search_log, search_rows_all

    for round_no in range(1, int(cfg["max_search_rounds"]) + 1):
        # Original protocol: low individual concepts can force search even if total looks high.
        if conf.total >= cfg["target_confidence"] and not has_critical_low_word(conf, cfg):
            break

        debt = conf.weakest_nodes(cfg, searched)
        if not debt:
            break

        node = debt[0]
        if node["type"] == "relation":
            search_query = query
        elif node["type"] == "meaning":
            search_query = f"{infer_transform(query)} meaning {query}"
        else:
            search_query = node["search_query"]

        key = f"{node['type']}:{node['item']}"
        searched.add(key)

        before = conf.total
        search_rows = ddg_search_tape(search_query, round_no, node["type"], cfg)
        working.extend(search_rows)
        search_rows_all.extend(search_rows)
        conf = compute_confidence(query, working)

        search_log.append({
            "round": round_no,
            "searched": search_query,
            "debt_type": node["type"],
            "debt_item": node["item"],
            "before_node_confidence": round(float(node["confidence"]), 3),
            "before_total_confidence": round(before, 3),
            "after_total_confidence": round(conf.total, 3),
            "rows_added": len(search_rows),
        })

    return working, conf, search_log, search_rows_all


# -----------------------------
# Synthesis
# -----------------------------

def context_trail(row: Row, all_rows: Sequence[Row], radius: int = 1) -> List[Row]:
    same_tape = [r for r in all_rows if r.tape_id == row.tape_id]
    same_tape.sort(key=lambda r: (r.lap, r.row_index))
    idx = next((i for i, r in enumerate(same_tape) if r.row_index == row.row_index and r.lap == row.lap), -1)
    if idx < 0:
        return [row]
    start = max(0, idx - radius)
    end = min(len(same_tape), idx + radius + 1)
    return same_tape[start:end]


def short_ref(row: Row) -> str:
    src = f" source={row.source_domain or row.source}" if (row.source_domain or row.source) else ""
    return f"{row.tape_id} lap {row.lap} row {row.row_index}{src}"


def focus_hits(hits: Sequence[EvidenceHit], native: Optional[bool] = None, n: int = 5) -> List[EvidenceHit]:
    filtered = [h for h in hits if native is None or h.native == native]
    return filtered[:n]


def best_source_phrase(hit: EvidenceHit) -> str:
    r = hit.row
    if r.source_domain:
        return r.source_domain
    if r.tape_id:
        return r.tape_id
    return "the tape"


def strongest_subject(hits: Sequence[EvidenceHit], fallback: str) -> str:
    if not hits:
        return fallback
    r = hits[0].row
    if r.is_control() and r.content_object():
        return fallback
    return r.content_actor() or r.thread or r.topic or fallback


def make_definition_clause(subject: str, hits: Sequence[EvidenceHit]) -> str:
    if not hits:
        return f"{subject} is not sufficiently represented in the loaded tapes"
    native = [h for h in focus_hits(hits, native=True, n=5) if h.token_score >= 0.5]
    acquired = [h for h in focus_hits(hits, native=False, n=5) if h.token_score >= 0.5 and h.row.kind != "search_failure"]
    failed_search = [h for h in focus_hits(hits, native=False, n=5) if h.row.kind == "search_failure"]
    if native:
        top = native[0].row
        return f"{subject} appears natively in the loaded tapes through {top.clause()}"
    if acquired:
        top = acquired[0].row
        return f"{subject} is not native to the loaded source tapes; acquired search tape frames it through {top.clause()}"
    if failed_search:
        return f"{subject} is not native to the loaded source tapes, and the attempted search tape did not return usable context"
    return f"{subject} is only weakly represented by the available tape rows"


def make_actor_action_clause(subject: str, hits: Sequence[EvidenceHit]) -> str:
    if not hits:
        return f"I cannot find what {subject} did in the loaded tapes"
    actions = []
    for h in hits[:5]:
        c = h.row.clause()
        if c not in actions:
            actions.append(c)
    return f"{subject} shows up through " + "; ".join(actions[:4])


def make_sequence_clause(subject: str, hits: Sequence[EvidenceHit]) -> str:
    if not hits:
        return "the sequence cannot be justified from the available rows"
    ordered = sorted(hits[:6], key=lambda h: (h.row.lap, h.row.row_index))
    start = ordered[0].row.clause()
    finish = ordered[-1].row.clause()
    return f"the trace runs from {start} toward {finish}"


def make_rank_clause(subject: str, hits: Sequence[EvidenceHit], all_rows: Sequence[Row]) -> str:
    # Rank by actor frequency in relevant hits, fallback to source-wide actor frequency.
    counts: Dict[str, int] = {}
    for h in hits[:20]:
        a = h.row.content_actor() or h.row.actor
        if a and a.lower() not in CONTROL_ACTORS:
            counts[a] = counts.get(a, 0) + 1
    if not counts:
        for r in all_rows:
            a = r.content_actor() or r.actor
            if a and a.lower() not in CONTROL_ACTORS:
                counts[a] = counts.get(a, 0) + 1
    if not counts:
        return "no non-control actor rises clearly to the top"
    leader, count = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return f"{leader} is on top by repeated relevant appearances, with {count} supporting rows in the current view"


def make_interpretive_clause(subject: str, hits: Sequence[EvidenceHit], query: str) -> str:
    q = lower(query)
    if "coefficient" in q:
        return (
            "if 'coefficient' means a measurable multiplier or weighting factor, "
            "the tape's closest equivalent is not one single number but a bundle of weights: "
            "lexical match, row priority, chronology, recurrence, and evidence strength"
        )
    if hits:
        return f"the interpretive answer is anchored by {hits[0].row.clause()}, then widened by the surrounding evidence trail"
    return "the interpretive answer remains under-supported because the available tapes do not provide enough anchors"


def evidence_clause(hits: Sequence[EvidenceHit]) -> str:
    native = focus_hits(hits, native=True, n=3)
    acquired = focus_hits(hits, native=False, n=3)
    if native and acquired:
        return "native tape rows provide the local anchor, while search tape repairs missing outside context"
    if native:
        return "the support is native to the loaded tape folder"
    if acquired:
        return "the support is externally acquired from DuckDuckGo search tape"
    return "there is no strong evidence support"


def relation_clause(conf: ConfidenceGraph, hits: Sequence[EvidenceHit]) -> str:
    if conf.relation >= 0.8:
        return "the query is strongly related to the current tape set"
    if conf.relation >= 0.55:
        return "the query is partially related to the current tape set"
    return "the query is weakly related to the native tape set"


def implication_clause(transform: str, subject: str, hits: Sequence[EvidenceHit]) -> str:
    if transform == "define":
        return "so the answer should be read as a bounded identity, not an exhaustive definition"
    if transform == "sequence":
        return "so the answer is chronological rather than causal proof"
    if transform == "causal":
        return "so the answer can describe supported pressure and sequence, but not hidden cause"
    if transform == "rank":
        return "so the answer is a ranking over the loaded tape view"
    if transform == "interpretive":
        return "so the answer is a computed analogy between the query language and the tape telemetry"
    if transform == "summary":
        return "so the answer compresses the strongest visible thread"
    return "so the answer stays bounded by the cited rows"


def boundary_clause(hits: Sequence[EvidenceHit], query: str) -> str:
    native = focus_hits(hits, native=True, n=2)
    acquired = focus_hits(hits, native=False, n=2)
    if acquired and not native:
        return "This is external context, not something proven native to the loaded source tapes"
    if acquired and native:
        return "This combines native tape evidence with acquired search context"
    if native:
        return "This is bounded to the loaded tape folder"
    return "This remains low-evidence and should be treated as unresolved"


def confidence_clause(conf: ConfidenceGraph) -> str:
    label = (
        "high" if conf.total >= 0.84 else
        "sufficient" if conf.total >= DEFAULT_CONFIG["target_confidence"] else
        "partial" if conf.total >= 0.55 else
        "low"
    )
    return f"confidence: {label} ({conf.total:.2f}); relation={conf.relation:.2f}; meaning={conf.meaning:.2f}"


def build_answer_form(query: str, rows: Sequence[Row], conf: ConfidenceGraph) -> Dict[str, Any]:
    transform = infer_transform(query)
    subject = query_focus_phrase(query) or strongest_subject(conf.evidence, "the query")
    hits = conf.evidence[:12]

    if transform == "define":
        claim = make_definition_clause(subject, hits)
    elif transform == "actor_action":
        claim = make_actor_action_clause(subject, hits)
    elif transform in {"sequence", "causal", "time"}:
        claim = make_sequence_clause(subject, hits)
    elif transform == "rank":
        claim = make_rank_clause(subject, hits, rows)
    elif transform == "interpretive":
        claim = make_interpretive_clause(subject, hits, query)
    elif transform == "count":
        claim = f"{len(hits[:10])} high-signal rows currently carry the answer to {subject}"
    else:
        if hits:
            claim = f"{subject} is best summarized by {hits[0].row.clause()}"
        else:
            claim = f"I cannot justify a direct answer to {subject} from the current tapes"

    return {
        "transform": transform,
        "subject": subject,
        "claim": claim,
        "evidence_clause": evidence_clause(hits),
        "relation_clause": relation_clause(conf, hits),
        "implication_clause": implication_clause(transform, subject, hits),
        "boundary_clause": boundary_clause(hits, query),
        "confidence_clause": confidence_clause(conf),
        "hits": hits,
    }


def choose_support_sets(form: Dict[str, Any]) -> List[Tuple[str, List[EvidenceHit]]]:
    hits: List[EvidenceHit] = form["hits"]
    native = focus_hits(hits, native=True, n=4)
    acquired = focus_hits(hits, native=False, n=4)
    sets: List[Tuple[str, List[EvidenceHit]]] = []

    sets.append((form["claim"], hits[:4]))
    if form["evidence_clause"]:
        if native and acquired:
            sets.append((form["evidence_clause"], native[:2] + acquired[:2]))
        elif native:
            sets.append((form["evidence_clause"], native[:3]))
        elif acquired:
            sets.append((form["evidence_clause"], acquired[:3]))
    if form["boundary_clause"] and form["boundary_clause"] != form["evidence_clause"]:
        basis = acquired[:2] if acquired else native[:2]
        if basis:
            sets.append((form["boundary_clause"], basis))
    if form["transform"] == "interpretive":
        # Cite telemetry itself using top rows; this is where the rough app is allowed to say how it computed.
        sets.append(("The measurable factors are lexical match, priority, chronology, and evidence strength", hits[:5]))
    return [(text, hs) for text, hs in sets if text and hs]


def assemble_meaning(form: Dict[str, Any], support_sets: List[Tuple[str, List[EvidenceHit]]]) -> Tuple[str, List[Dict[str, Any]]]:
    footnotes: List[Dict[str, Any]] = []
    ref_to_id: Dict[str, int] = {}
    parts: List[str] = []

    for text, hs in support_sets:
        key = "|".join(h.row.ref_id() for h in hs)
        if key in ref_to_id:
            n = ref_to_id[key]
        else:
            n = len(footnotes) + 1
            ref_to_id[key] = n
            footnotes.append({"id": n, "claim_glob": text, "support": hs})
        parts.append(f"{text} [{n}]")

    # Add one uncited sentence if it is pure boundary logic already cited elsewhere? Keep it cited if possible.
    if not parts:
        parts.append(form["claim"])

    meaning = " ".join(parts)
    # Add relation/implication as prose, but only cite if already covered by first footnote.
    if footnotes:
        meaning += f" {form['implication_clause']} [{footnotes[0]['id']}]."
    else:
        meaning += f" {form['implication_clause']}."
    meaning += f"\n\n{form['confidence_clause']}."
    return meaning, footnotes


def synthesize(query: str, rows: Sequence[Row], conf: ConfidenceGraph) -> Dict[str, Any]:
    form = build_answer_form(query, rows, conf)
    support_sets = choose_support_sets(form)
    meaning, footnotes = assemble_meaning(form, support_sets)

    evidence_lines: List[str] = []
    evidence_json: List[Dict[str, Any]] = []

    for f in footnotes:
        n = f["id"]
        support: List[EvidenceHit] = f["support"]
        evidence_lines.append(f"[{n}] {f['claim_glob']}")
        trail_items = []
        for h in support:
            row = h.row
            trail = context_trail(row, rows, radius=1)
            native_label = "native" if h.native else "acquired/search"
            evidence_lines.append(
                f"  - {short_ref(row)} | {native_label} | score={h.score:.2f} token={h.token_score:.2f} priority={h.priority_score:.2f} chrono={h.chronology_score:.2f} source_bonus={h.source_score:.2f} | {row.clause()}"
            )
            if h.matched_terms:
                evidence_lines.append(f"    matched: {', '.join(h.matched_terms)}")
            if row.source:
                evidence_lines.append(f"    source: {row.source}")
            evidence_lines.append("    trail:")
            trail_desc = []
            for tr in trail:
                evidence_lines.append(f"      • {tr.tape_id} lap {tr.lap}: {tr.clause()}")
                trail_desc.append({
                    "ref": tr.ref_id(),
                    "lap": tr.lap,
                    "actor": tr.actor,
                    "action": tr.action,
                    "object": tr.object,
                    "source": tr.source,
                    "source_domain": tr.source_domain,
                    "source_kind": tr.source_kind,
                })
            trail_items.append({
                "evidence_ref": row.ref_id(),
                "score": h.score,
                "native": h.native,
                "matched_terms": h.matched_terms,
                "row": dataclasses.asdict(row),
                "trail": trail_desc,
            })
        evidence_json.append({"id": n, "claim_glob": f["claim_glob"], "trail": trail_items})

    diagnostics = {
        "answer_form": {k: v for k, v in form.items() if k != "hits"},
        "words": conf.words,
        "top_evidence": [
            {
                "ref": e.row.ref_id(),
                "score": e.score,
                "native": e.native,
                "matched_terms": e.matched_terms,
                "clause": e.row.clause(),
            }
            for e in conf.evidence[:10]
        ],
    }

    return {
        "meaning": meaning,
        "evidence_text": "\n".join(evidence_lines) if evidence_lines else "No cited evidence.",
        "evidence": evidence_json,
        "confidence": {
            "total": conf.total,
            "relation": conf.relation,
            "meaning": conf.meaning,
            "words": conf.words,
            "top_evidence": diagnostics["top_evidence"],
        },
        "diagnostics": diagnostics,
    }


# -----------------------------
# Conversation tape I/O
# -----------------------------

def row_to_tape_dict(row: Row) -> Dict[str, Any]:
    return dataclasses.asdict(row)


def append_ndjson(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def make_chat_records(query: str, result: Dict[str, Any], search_log: List[Dict[str, Any]], search_rows: Sequence[Row], turn: int) -> List[Dict[str, Any]]:
    base_time = now_iso()
    records: List[Dict[str, Any]] = [
        {
            "actor": "user",
            "action": "ask",
            "object": query,
            "lap": turn * 1000 + 1,
            "time": base_time,
            "kind": "chat_query",
            "priority": 0.7,
            "valence": "calm",
        }
    ]

    for i, sr in enumerate(search_rows):
        rec = row_to_tape_dict(sr)
        rec["lap"] = turn * 1000 + 2 + i
        rec["kind"] = "search_tape_row"
        records.append(rec)

    records.append({
        "actor": "oradio_synth",
        "action": "synthesize_meaning",
        "object": result["meaning"],
        "lap": turn * 1000 + 900,
        "time": base_time,
        "kind": "assistant_meaning",
        "priority": clamp01(result["confidence"]["total"]),
        "valence": "calm",
        "confidence": result["confidence"],
        "diagnostics": result.get("diagnostics", {}),
        "search_log": search_log,
    })
    records.append({
        "actor": "oradio_synth",
        "action": "cite_evidence",
        "object": result["evidence_text"],
        "lap": turn * 1000 + 901,
        "time": base_time,
        "kind": "assistant_evidence",
        "priority": clamp01(result["confidence"]["total"]),
        "valence": "calm",
        "evidence": result["evidence"],
    })
    return records


# -----------------------------
# Main orchestration
# -----------------------------

def answer_once(query: str, source_rows: List[Row], cfg: Dict[str, Any], enable_search: bool) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Row], ConfidenceGraph, List[Row]]:
    working, conf, search_log, search_rows = acquire_confidence(query, source_rows, cfg, enable_search=enable_search)
    result = synthesize(query, working, conf)
    result["search_log"] = search_log
    result["rows_loaded"] = len(source_rows)
    result["rows_after_search"] = len(working)
    return result, search_log, search_rows, conf, working


def print_response(result: Dict[str, Any]) -> None:
    print("\nMEANING\n" + "-" * 72)
    print(result["meaning"])
    print("\nEVIDENCE\n" + "-" * 72)
    print(result["evidence_text"])
    if result.get("search_log"):
        print("\nSEARCH / CONFIDENCE REPAIR\n" + "-" * 72)
        for item in result["search_log"]:
            print(
                f"round {item['round']}: searched {item['searched']!r} "
                f"({item['debt_type']} {item['before_node_confidence']:.2f}); "
                f"total {item['before_total_confidence']:.2f} -> {item['after_total_confidence']:.2f}; "
                f"rows +{item['rows_added']}"
            )


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="O-Radio deterministic tape synthesis chatbot v2")
    ap.add_argument("--tapes", required=True, help="Folder or single file containing tape JSON/NDJSON/.oradio/.loom")
    ap.add_argument("--query", help="One-shot query. If omitted, starts an interactive chat loop.")
    ap.add_argument("--search", action="store_true", help="Enable DuckDuckGo confidence repair")
    ap.add_argument("--session", default="", help="Conversation tape NDJSON path")
    ap.add_argument("--outdir", default="", help="Directory for per-turn JSON outputs")
    ap.add_argument("--write-search-tape", action="store_true", help="Write acquired search rows to JSON")
    ap.add_argument("--write-answer-tape", action="store_true", help="Write final answer records to JSON")
    ap.add_argument("--target-confidence", type=float, default=DEFAULT_CONFIG["target_confidence"])
    ap.add_argument("--max-search-rounds", type=int, default=DEFAULT_CONFIG["max_search_rounds"])
    ap.add_argument("--max-results-per-search", type=int, default=DEFAULT_CONFIG["max_results_per_search"])
    return ap


def run() -> int:
    args = build_arg_parser().parse_args()
    cfg = dict(DEFAULT_CONFIG)
    cfg["target_confidence"] = args.target_confidence
    cfg["max_search_rounds"] = args.max_search_rounds
    cfg["max_results_per_search"] = args.max_results_per_search

    tape_path = Path(args.tapes)
    rows = load_tapes(tape_path)
    if not rows:
        print(f"No tape rows loaded from {tape_path}", file=sys.stderr)
        return 2

    session = Path(args.session) if args.session else Path("oradio_chat_session.ndjson")
    outdir = Path(args.outdir) if args.outdir else Path("oradio_outputs")

    print(f"loaded {len(rows)} rows from {tape_path}")
    print(f"search={'on' if args.search else 'off'} | session={session}")

    def handle(q: str, turn_no: int) -> None:
        result, search_log, search_rows, _conf, _working = answer_once(q, rows, cfg, enable_search=args.search)
        print_response(result)
        records = make_chat_records(q, result, search_log, search_rows, turn_no)
        append_ndjson(session, records)
        turn_id = stable_id(q, turn_no, time.time())
        out_path = outdir / f"turn_{turn_no:04d}_{turn_id}.json"
        save_json(out_path, {
            "query": q,
            "result": result,
            "chat_records": records,
        })
        if args.write_search_tape and search_rows:
            save_json(outdir / f"turn_{turn_no:04d}_{turn_id}.search.tape.json", [row_to_tape_dict(r) for r in search_rows])
        if args.write_answer_tape:
            save_json(outdir / f"turn_{turn_no:04d}_{turn_id}.answer.tape.json", records)
        print(f"\n[written] session += {len(records)} rows; output={out_path}")

    if args.query:
        handle(args.query, 1)
        return 0

    print("\nType a query. Commands: /quit, /search on, /search off, /confidence 0.8")
    turn = 0
    while True:
        try:
            q = input("\noradio> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q in {"/q", "/quit", "/exit"}:
            break
        if q == "/search on":
            args.search = True
            print("search on")
            continue
        if q == "/search off":
            args.search = False
            print("search off")
            continue
        if q.startswith("/confidence "):
            try:
                cfg["target_confidence"] = float(q.split(None, 1)[1])
                print(f"target confidence={cfg['target_confidence']}")
            except Exception:
                print("usage: /confidence 0.74")
            continue
        turn += 1
        handle(q, turn)

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
