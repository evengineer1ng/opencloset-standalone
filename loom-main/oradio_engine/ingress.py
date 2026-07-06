"""Ingress translator/judge seam for Loom queries.

This keeps nondeterministic interpretation at the boundary and turns it into a
deterministic machine-intent brief before retrieval/synthesis runs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, Iterable, List, Sequence

from . import query_codec_impl as impl
from loom.loombit_route import RouteHint, route_index


@dataclass
class IntentCandidate:
    source: str
    query: str
    normalized_query: str
    transform: str
    focus: str
    entities: List[str] = field(default_factory=list)
    time_scope: str = ""
    constraints: List[str] = field(default_factory=list)
    ambiguities: List[str] = field(default_factory=list)
    retrieval_plan: List[str] = field(default_factory=list)
    gradient_bucket: str = ""
    aim_tokens: List[str] = field(default_factory=list)
    preferred_paths: List[str] = field(default_factory=list)
    confidence: float = 0.0
    dropped_terms: List[str] = field(default_factory=list)
    guessed_terms: List[str] = field(default_factory=list)
    alternates: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class JudgedIntent:
    original_query: str
    accepted_query: str
    accepted_candidate: IntentCandidate
    candidates: List[IntentCandidate]
    arbitration: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "original_query": self.original_query,
            "accepted_query": self.accepted_query,
            "accepted_candidate": self.accepted_candidate.as_dict(),
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "arbitration": self.arbitration,
        }


def _clean_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _clean_list(values: Any, *, limit: int = 12, item_limit: int = 120) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        parts = re.split(r"[,\n|;]+", values)
    elif isinstance(values, Iterable):
        parts = list(values)
    else:
        parts = [values]
    out: List[str] = []
    seen = set()
    for raw in parts:
        item = _clean_text(raw, item_limit)
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _cap_phrase_entities(query: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for match in re.finditer(r"\b(?:[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+)*)\b", str(query or "")):
        phrase = _clean_text(match.group(0), 120)
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(phrase)
    return out


def _quoted_entities(query: str) -> List[str]:
    out: List[str] = []
    for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', str(query or "")):
        picked = match.group(1) or match.group(2) or ""
        cleaned = _clean_text(picked, 120)
        if cleaned:
            out.append(cleaned)
    return _clean_list(out)


def _time_scope(query: str) -> str:
    q = str(query or "")
    lowered = q.lower()
    patterns = [
        (r"\btoday\b", "today"),
        (r"\byesterday\b", "yesterday"),
        (r"\btomorrow\b", "tomorrow"),
        (r"\bafter\b", "after"),
        (r"\bbefore\b", "before"),
        (r"\bfirst\b", "first"),
        (r"\blast\b", "last"),
        (r"\bnext\b", "next"),
        (r"\blap\s+\d+\b", lambda m: m.group(0)),
        (r"\b\d{4}-\d{2}-\d{2}\b", lambda m: m.group(0)),
    ]
    for pattern, label in patterns:
        match = re.search(pattern, lowered, re.I)
        if match:
            return label(match) if callable(label) else label
    return ""


def _human_time_scope(query: str) -> str:
    return _time_scope(query)


def _sanitize_candidate(query: str, candidate: IntentCandidate) -> IntentCandidate:
    human_scope = _human_time_scope(query)
    sanitized = IntentCandidate(
        source=candidate.source,
        query=candidate.query,
        normalized_query=candidate.normalized_query,
        transform=candidate.transform,
        focus=candidate.focus,
        entities=list(candidate.entities),
        time_scope=candidate.time_scope,
        constraints=list(candidate.constraints),
        ambiguities=list(candidate.ambiguities),
        retrieval_plan=list(candidate.retrieval_plan),
        gradient_bucket=candidate.gradient_bucket,
        aim_tokens=list(candidate.aim_tokens),
        preferred_paths=list(candidate.preferred_paths),
        confidence=candidate.confidence,
        dropped_terms=list(candidate.dropped_terms),
        guessed_terms=list(candidate.guessed_terms),
        alternates=list(candidate.alternates),
        meta=dict(candidate.meta),
    )
    removed: List[str] = []
    if sanitized.time_scope and sanitized.time_scope != human_scope:
        removed.append(f"time_scope:{sanitized.time_scope}")
        sanitized.time_scope = human_scope
    lowered_constraints = []
    for item in sanitized.constraints:
        token = str(item or "").strip()
        if token.lower() in {"latest", "today", "current", "recent", "newest"} and token.lower() not in str(query or "").lower():
            removed.append(f"constraint:{token}")
            continue
        lowered_constraints.append(token)
    sanitized.constraints = lowered_constraints
    if removed:
        meta = dict(sanitized.meta)
        meta["sanitized_removed"] = removed
        sanitized.meta = meta
    return sanitized


def _constraints(query: str) -> List[str]:
    q = str(query or "")
    found = []
    for pattern, label in [
        (r"\bonly\b", "only"),
        (r"\bexact(?:ly)?\b", "exact"),
        (r"\bfrom the tape\b", "from_tape"),
        (r"\bdeterministic\b", "deterministic"),
        (r"\bwithout search\b", "without_search"),
        (r"\bwith search\b", "with_search"),
    ]:
        if re.search(pattern, q, re.I):
            found.append(label)
    return _clean_list(found, limit=8, item_limit=40)


def _loss_report(query: str, normalized_query: str) -> List[str]:
    original_terms = set(impl.question_tokens(query))
    normalized_terms = set(impl.question_tokens(normalized_query))
    dropped = sorted(original_terms - normalized_terms)
    return dropped[:12]


def _normalize_human_query(query: str) -> str:
    text = " " + _clean_text(_extract_question_clause(query), 240) + " "
    replacements = [
        r"\bokay\b",
        r"\bok\b",
        r"\bplease\b",
        r"\bjust\b",
        r"\bactually\b",
        r"\breally\b",
        r"\bi know this might sound dumb but\b",
        r"\bthis might sound dumb but\b",
        r"\bi was wondering\b",
        r"\bcan you tell me\b",
        r"\bdo you know\b",
        r"\bended up\b",
        r"\bfrom the tape only\b",
        r"\bfrom the tape\b",
        r"\bonly\b",
    ]
    for pattern in replacements:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"\s*[,;:]+\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^\s*who\s+winning\b", "who won", text, flags=re.I)
    text = re.sub(r"^\s*who\s+win\b", "who won", text, flags=re.I)
    text = re.sub(r"\bin the end\b", " ", text, flags=re.I)
    text = re.sub(r"\s+\?", "?", text)
    return _clean_text(text, 240)


def _default_plan(transform: str, focus: str, entities: Sequence[str], time_scope: str) -> List[str]:
    subject = focus or (entities[0] if entities else "query")
    plan = [f"focus:{subject}"]
    if entities:
        plan.append("match_entities")
    if transform in {"rank", "count", "time", "sequence", "causal", "actor_action", "define"}:
        plan.append(f"transform:{transform}")
    else:
        plan.append("transform:summary")
    if time_scope:
        plan.append(f"time_scope:{time_scope}")
    plan.append("score_evidence")
    return plan


def _compose_machine_query(candidate: IntentCandidate) -> str:
    focus = candidate.focus or (candidate.entities[0] if candidate.entities else candidate.query)
    normalized = candidate.normalized_query or candidate.query
    if re.match(r"^(who|what|when|where|why|how)\b", normalized, re.I):
        base = normalized
    elif candidate.transform == "define":
        base = f"define {focus}"
    elif candidate.transform == "actor_action":
        base = f"what did {focus} do"
    elif candidate.transform == "causal":
        base = f"why {focus}"
    elif candidate.transform == "sequence":
        base = f"how did {focus} happen"
    elif candidate.transform == "time":
        base = f"when {focus}"
    elif candidate.transform == "count":
        base = f"count {focus}"
    elif candidate.transform == "rank":
        base = f"rank {focus}"
    else:
        base = normalized
    skip_time_scope = candidate.time_scope.lower() == "after" and re.match(r"^who\s+won\b", base, re.I)
    if candidate.time_scope and candidate.time_scope.lower() not in base.lower() and not skip_time_scope:
        base += f" {candidate.time_scope}"
    return _clean_text(base, 240)


def _extract_question_clause(query: str) -> str:
    text = _clean_text(query, 240)
    matches = list(re.finditer(r"\b(who|what|when|where|why|how)\b", text, re.I))
    if matches:
        return text[matches[-1].start():]
    return text


def build_heuristic_candidate(query: str) -> IntentCandidate:
    normalized_query = _normalize_human_query(query)
    focus = impl.query_focus_phrase(normalized_query) or _clean_text(normalized_query, 120)
    transform = impl.infer_transform(normalized_query)
    entities = _clean_list(_quoted_entities(normalized_query) + _cap_phrase_entities(normalized_query))
    time_scope = _time_scope(query)
    constraints = _constraints(query)
    retrieval_plan = _default_plan(transform, focus, entities, time_scope)
    return IntentCandidate(
        source="heuristic",
        query=query,
        normalized_query=normalized_query,
        transform=transform,
        focus=focus,
        entities=entities,
        time_scope=time_scope,
        constraints=constraints,
        ambiguities=[],
        retrieval_plan=retrieval_plan,
        gradient_bucket="",
        aim_tokens=impl.question_tokens(focus)[:6],
        preferred_paths=[],
        confidence=0.55,
        dropped_terms=_loss_report(query, normalized_query),
        guessed_terms=[],
        alternates=[],
        meta={"builder": "heuristic_v1"},
    )


def normalize_translator_candidates(translator_payload: Any, query: str) -> List[IntentCandidate]:
    if translator_payload is None:
        return []
    if isinstance(translator_payload, dict) and isinstance(translator_payload.get("candidates"), list):
        payloads = translator_payload.get("candidates") or []
    elif isinstance(translator_payload, list):
        payloads = translator_payload
    else:
        payloads = [translator_payload]

    candidates: List[IntentCandidate] = []
    for index, raw in enumerate(payloads):
        item = raw if isinstance(raw, dict) else {}
        transform = _clean_text(item.get("transform") or item.get("intent") or impl.infer_transform(query), 40).lower() or "summary"
        focus = _clean_text(item.get("focus") or item.get("subject") or item.get("query_intent") or impl.query_focus_phrase(query), 120)
        normalized_query = _clean_text(item.get("normalized_query") or item.get("canonical_query") or item.get("query") or query, 240)
        candidate = IntentCandidate(
            source=_clean_text(item.get("source") or item.get("translator") or f"translator:{index}", 60),
            query=query,
            normalized_query=normalized_query,
            transform=transform,
            focus=focus,
            entities=_clean_list(item.get("entities") or item.get("entity_mentions")),
            time_scope=_clean_text(item.get("time_scope") or item.get("timeframe"), 60),
            constraints=_clean_list(item.get("constraints")),
            ambiguities=_clean_list(item.get("ambiguities"), limit=8),
            retrieval_plan=_clean_list(item.get("retrieval_plan") or item.get("plan"), limit=12),
            gradient_bucket=_clean_text(item.get("gradient_bucket") or item.get("bucket"), 80),
            aim_tokens=_clean_list(item.get("aim_tokens"), limit=8, item_limit=40),
            preferred_paths=_clean_list(item.get("preferred_paths"), limit=8, item_limit=120),
            confidence=impl.clamp01(item.get("confidence", 0.5)),
            dropped_terms=_clean_list(item.get("dropped_terms") or item.get("loss_report")),
            guessed_terms=_clean_list(item.get("guessed_terms")),
            alternates=list(item.get("alternates") or [])[:6],
            meta={"raw": item},
        )
        if not candidate.retrieval_plan:
            candidate.retrieval_plan = _default_plan(candidate.transform, candidate.focus, candidate.entities, candidate.time_scope)
        candidates.append(_sanitize_candidate(query, candidate))
    return candidates


def _coverage_score(terms: Sequence[str], rows: Sequence[impl.Row]) -> float:
    if not terms:
        return 0.0
    vocab = impl.tape_vocabulary(rows)
    if not vocab:
        return 0.0
    scores = []
    for term in terms:
        token_scores = [impl.best_token_affinity(token, vocab)[0] for token in impl.question_tokens(term)]
        scores.append(sum(token_scores) / max(1, len(token_scores)) if token_scores else 0.0)
    return round(sum(scores) / max(1, len(scores)), 3)


def _preservation_score(query: str, candidate: IntentCandidate) -> float:
    original_terms = set(impl.question_tokens(query))
    proposed_terms = set(impl.question_tokens(candidate.normalized_query))
    proposed_terms.update(impl.question_tokens(candidate.focus))
    for entity in candidate.entities:
        proposed_terms.update(impl.question_tokens(entity))
    if not original_terms:
        return 0.0
    kept = len(original_terms & proposed_terms) / max(1, len(original_terms))
    return round(kept, 3)


def _candidate_score(query: str, rows: Sequence[impl.Row], candidate: IntentCandidate) -> Dict[str, float]:
    expected_transform = impl.infer_transform(query)
    transform_match = 1.0 if candidate.transform == expected_transform else 0.55
    focus_coverage = _coverage_score([candidate.focus], rows)
    entity_coverage = _coverage_score(candidate.entities, rows) if candidate.entities else 0.0
    preservation = _preservation_score(query, candidate)
    plan_score = min(1.0, len(candidate.retrieval_plan) / 4.0)
    confidence_score = impl.clamp01(candidate.confidence)
    ambiguity_penalty = min(0.25, len(candidate.ambiguities) * 0.04)
    dropped_penalty = min(0.20, len(candidate.dropped_terms) * 0.03)
    guessed_penalty = min(0.18, len(candidate.guessed_terms) * 0.04)
    total = impl.clamp01(
        focus_coverage * 0.34
        + entity_coverage * 0.18
        + preservation * 0.18
        + transform_match * 0.14
        + plan_score * 0.08
        + confidence_score * 0.08
        - ambiguity_penalty
        - dropped_penalty
        - guessed_penalty
    )
    return {
        "total": round(total, 3),
        "focus_coverage": focus_coverage,
        "entity_coverage": entity_coverage,
        "preservation": preservation,
        "transform_match": round(transform_match, 3),
        "plan_score": round(plan_score, 3),
        "translator_confidence": round(confidence_score, 3),
        "ambiguity_penalty": round(ambiguity_penalty, 3),
        "dropped_penalty": round(dropped_penalty, 3),
        "guessed_penalty": round(guessed_penalty, 3),
    }


def judge_intent(
    query: str,
    rows: Sequence[impl.Row],
    translator_payload: Any = None,
    *,
    loombit_index: str | None = None,
    loombit_dict: str | None = None,
) -> JudgedIntent:
    return judge_intent_with_routing(
        query,
        rows,
        translator_payload=translator_payload,
        loombit_index=loombit_index,
        loombit_dict=loombit_dict,
    )


def judge_intent_with_routing(
    query: str,
    rows: Sequence[impl.Row],
    translator_payload: Any = None,
    *,
    loombit_index: str | None = None,
    loombit_dict: str | None = None,
) -> JudgedIntent:
    candidates = [build_heuristic_candidate(query)]
    candidates.extend(normalize_translator_candidates(translator_payload, query))

    scored = []
    for candidate in candidates:
        machine_query = _compose_machine_query(candidate)
        scores = _candidate_score(query, rows, candidate)
        route_meta = {}
        if loombit_index:
            routed = route_index(
                loombit_index,
                query=machine_query,
                dict_path=loombit_dict,
                hint=RouteHint(
                    gradient_bucket=candidate.gradient_bucket,
                    aim_tokens=list(candidate.aim_tokens),
                    preferred_paths=list(candidate.preferred_paths),
                    confidence=candidate.confidence,
                ),
                max_depth=2,
                top_k=5,
            )
            top = (routed.get("ranked") or [])
            top_score = float((top[0] or {}).get("score") if top else 0.0)
            scores["route_score"] = round(top_score, 3)
            scores["total"] = round(impl.clamp01(scores["total"] * 0.82 + top_score * 0.18), 3)
            route_meta = routed
        scored.append({
            "candidate": candidate,
            "machine_query": machine_query,
            "scores": scores,
            "route": route_meta,
        })

    scored.sort(
        key=lambda item: (
            -item["scores"]["total"],
            item["candidate"].source != "heuristic",
            item["machine_query"],
        )
    )
    winner = scored[0]
    return JudgedIntent(
        original_query=query,
        accepted_query=winner["machine_query"],
        accepted_candidate=winner["candidate"],
        candidates=candidates,
        arbitration={
            "winner_source": winner["candidate"].source,
            "winner_scores": winner["scores"],
            "scored_candidates": [
                {
                    "source": item["candidate"].source,
                    "machine_query": item["machine_query"],
                    "scores": item["scores"],
                    "route": item["route"],
                }
                for item in scored
            ],
            "route": winner["route"],
        },
    )
