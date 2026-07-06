"""Deterministic routing over a loombit index tree."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .loombit import ExternalDictionary, decode_loombit, load_external_dictionary


@dataclass
class RouteHint:
    gradient_bucket: str = ""
    aim_tokens: List[str] = field(default_factory=list)
    preferred_paths: List[str] = field(default_factory=list)
    confidence: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RouteEntry:
    id: str
    path: str
    score: float
    depth: int
    class_name: str
    topic: str
    summary: str
    bucket: str = ""
    gradient: str = ""
    tags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    lineage: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean_tokens(text: str) -> List[str]:
    out: List[str] = []
    seen = set()
    token = []
    for ch in str(text or "").lower():
        if ch.isalnum():
            token.append(ch)
            continue
        if token:
            item = "".join(token)
            if item not in seen:
                seen.add(item)
                out.append(item)
            token = []
    if token:
        item = "".join(token)
        if item not in seen:
            out.append(item)
    return out


def _entry_text(entry: Dict[str, Any]) -> str:
    parts = [
        entry.get("id"),
        entry.get("path"),
        entry.get("class"),
        entry.get("topic"),
        entry.get("summary"),
        entry.get("bucket"),
        entry.get("gradient"),
        " ".join(entry.get("tags") or []),
    ]
    return " ".join(str(part or "") for part in parts if part)


def _score_entry(entry: Dict[str, Any], query_tokens: Sequence[str], hint: RouteHint) -> RouteEntry:
    text = _entry_text(entry)
    tokens = set(_clean_tokens(text))
    score = 0.0
    reasons: List[str] = []

    query_matches = [token for token in query_tokens if token in tokens]
    if query_matches:
        score += min(0.55, 0.18 * len(query_matches))
        reasons.append("query:" + ",".join(query_matches[:4]))

    aim_matches = [token for token in hint.aim_tokens if token in tokens]
    if aim_matches:
        score += min(0.30, 0.12 * len(aim_matches))
        reasons.append("aim:" + ",".join(aim_matches[:4]))

    bucket = str(entry.get("bucket") or "")
    gradient = str(entry.get("gradient") or "")
    path = str(entry.get("path") or "")
    if hint.gradient_bucket:
        lowered_bucket = hint.gradient_bucket.lower()
        if lowered_bucket and lowered_bucket in bucket.lower():
            score += 0.22
            reasons.append(f"bucket:{bucket}")
        elif lowered_bucket and lowered_bucket in gradient.lower():
            score += 0.14
            reasons.append(f"gradient:{gradient}")
    if hint.preferred_paths:
        lowered_path = path.lower()
        for pref in hint.preferred_paths:
            pref_lower = str(pref or "").lower()
            if pref_lower and pref_lower in lowered_path:
                score += 0.22
                reasons.append(f"path:{pref}")
                break

    if str(entry.get("topic") or "").strip():
        score += 0.04
    if str(entry.get("summary") or "").strip():
        score += 0.03
    if str(entry.get("class") or "").strip().endswith("index"):
        score += 0.02

    return RouteEntry(
        id=str(entry.get("id") or ""),
        path=path,
        score=round(min(1.0, score), 3),
        depth=0,
        class_name=str(entry.get("class") or ""),
        topic=str(entry.get("topic") or ""),
        summary=str(entry.get("summary") or ""),
        bucket=bucket,
        gradient=gradient,
        tags=list(entry.get("tags") or []),
        reasons=reasons,
        lineage=[],
    )


def _load_index_payload(index_path: str | Path, external_dictionary: ExternalDictionary | None) -> Dict[str, Any]:
    decoded = decode_loombit(Path(index_path).read_bytes(), external_dictionary=external_dictionary)
    payload = decoded.get("payload", {})
    if not isinstance(payload, dict) or str(payload.get("kind") or "") != "loombit_index":
        raise ValueError(f"{index_path} is not a loombit_index artifact")
    return payload


def route_index(
    index_path: str | Path,
    *,
    query: str,
    hint: RouteHint | None = None,
    dict_path: str | Path | None = None,
    max_depth: int = 2,
    top_k: int = 8,
) -> Dict[str, Any]:
    external = load_external_dictionary(dict_path) if dict_path else None
    query_tokens = _clean_tokens(query)
    route_hint = hint or RouteHint()
    root = Path(index_path)
    visited = set()
    ranked: List[RouteEntry] = []

    def walk(current_path: Path, depth: int, lineage: List[str]) -> None:
        if depth > max_depth:
            return
        key = str(current_path.resolve())
        if key in visited or not current_path.exists():
            return
        visited.add(key)
        payload = _load_index_payload(current_path, external)
        entries = payload.get("entries") or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            scored = _score_entry(entry, query_tokens, route_hint)
            scored.depth = depth
            scored.lineage = lineage + [str(payload.get("title") or current_path.name)]
            ranked.append(scored)
            child_path = str(entry.get("path") or "")
            child_class = str(entry.get("class") or "")
            if depth < max_depth and child_path.lower().endswith(".loombit") and child_class.endswith("index"):
                next_path = (current_path.parent / child_path).resolve()
                if next_path.exists():
                    try:
                        walk(next_path, depth + 1, scored.lineage)
                    except ValueError:
                        pass

    walk(root, 0, [])
    ranked.sort(key=lambda item: (-item.score, item.depth, item.path, item.id))
    return {
        "index_path": str(root),
        "query": query,
        "hint": route_hint.as_dict(),
        "ranked": [item.as_dict() for item in ranked[:top_k]],
        "visited_indexes": len(visited),
    }


def summarize_index(
    index_path: str | Path,
    *,
    dict_path: str | Path | None = None,
    max_entries: int = 12,
) -> Dict[str, Any]:
    external = load_external_dictionary(dict_path) if dict_path else None
    payload = _load_index_payload(index_path, external)
    entries = payload.get("entries") or []
    preview = []
    for entry in entries[:max_entries]:
        if not isinstance(entry, dict):
            continue
        preview.append(
            {
                "id": str(entry.get("id") or ""),
                "path": str(entry.get("path") or ""),
                "class": str(entry.get("class") or ""),
                "topic": str(entry.get("topic") or ""),
                "summary": str(entry.get("summary") or ""),
                "bucket": str(entry.get("bucket") or ""),
                "gradient": str(entry.get("gradient") or ""),
                "tags": list(entry.get("tags") or []),
            }
        )
    return {
        "title": str(payload.get("title") or ""),
        "level": int(payload.get("level") or 0),
        "branching": int(payload.get("branching") or 0),
        "entry_count": int(payload.get("entry_count") or len(entries)),
        "entries": preview,
    }
