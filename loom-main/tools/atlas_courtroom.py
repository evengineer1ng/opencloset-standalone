#!/usr/bin/env python3
"""Terminal Atlas + LLM courtroom harness.

Atlas remains the authority layer. The LLM may normalize ingress and explain
the ruling back to the user, but the final answer is restricted to Atlas-
admitted claims.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import time
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_client import default_model, provider
from oradio_engine.atlas_retrieval import AtlasRetriever
from oradio_engine.ollama_ingress import generate_candidates
from oradio_engine.outgress import render as render_outgress, write_gaps, summarize_gaps
from oradio_engine.query_codec import export_packet, query_tapes_via_ingress, render_evidence, render_meaning
from loom.loombit_route import summarize_index


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 180) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def openai_mode() -> str:
    return os.environ.get("LOOM_LLM_OPENAI_MODE", "auto").strip().lower()


def complete_with_usage(
    prompt: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.0,
    num_predict: int = 700,
    think: bool = False,
) -> Dict[str, Any]:
    chosen_model = model or default_model()
    started = time.perf_counter()
    kind = provider()
    if kind in ("openai", "openai-compatible"):
        base = os.environ.get("LOOM_LLM_BASE", "https://api.openai.com/v1").rstrip("/")
        key = os.environ.get("LOOM_LLM_KEY") or os.environ.get("OPENAI_API_KEY", "")
        mode = openai_mode()
        body = None
        text = ""
        if mode in {"auto", "chat"}:
            try:
                body = _post(
                    base + "/chat/completions",
                    {
                        "model": chosen_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": num_predict,
                    },
                    {"Authorization": f"Bearer {key}"},
                )
                choice = body.get("choices", [{}])[0]
                message = choice.get("message", {}) or {}
                text = str(message.get("content") or message.get("reasoning_content") or "")
            except Exception:
                if mode == "chat":
                    raise
        if not text and mode in {"auto", "completion"}:
            body = _post(
                base + "/completions",
                {
                    "model": chosen_model,
                    "prompt": prompt,
                    "temperature": temperature,
                    "max_tokens": num_predict,
                },
                {"Authorization": f"Bearer {key}"},
            )
            choice = body.get("choices", [{}])[0]
            text = str(choice.get("text") or "")
        usage = body.get("usage", {}) or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
    else:
        url = os.environ.get("LOOM_LLM_ENDPOINT", "http://127.0.0.1:11434/api/generate")
        body = _post(
            url,
            {
                "model": chosen_model,
                "prompt": prompt,
                "stream": False,
                "think": think,
                "options": {"temperature": temperature, "num_predict": num_predict},
            },
            {},
        )
        text = str(body.get("response") or "")
        prompt_tokens = int(body.get("prompt_eval_count") or 0)
        completion_tokens = int(body.get("eval_count") or 0)
    clean = re.sub(r"(?is)<think>.*?</think>", "", text).strip()
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "text": clean,
        "model": chosen_model,
        "provider": kind,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
    }


def maybe_parse_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def normalize_text(text: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]", " ", str(text or "").lower())
    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def tokenize(text: str) -> List[str]:
    return [token for token in normalize_text(text).split() if token]


def sentence_claims(text: str) -> List[str]:
    out: List[str] = []
    for part in re.split(r"(?<=[.!?])\s+|\n+", str(text or "").strip()):
        claim = part.strip(" -\t\r\n")
        if claim:
            out.append(claim)
    return out


def lexical_overlap_score(a: str, b: str) -> float:
    aa = set(tokenize(a))
    bb = set(tokenize(b))
    if not aa or not bb:
        return 0.0
    inter = len(aa & bb)
    union = len(aa | bb)
    return inter / union if union else 0.0


def best_support(claim: str, admitted_claims: Sequence[str]) -> Tuple[float, str]:
    best_score = 0.0
    best_claim = ""
    for admitted in admitted_claims:
        score = lexical_overlap_score(claim, admitted)
        if normalize_text(claim) and normalize_text(claim) in normalize_text(admitted):
            score = max(score, 0.96)
        if normalize_text(admitted) and normalize_text(admitted) in normalize_text(claim):
            score = max(score, 0.88)
        if score > best_score:
            best_score = score
            best_claim = admitted
    return best_score, best_claim


def classify_status(confidence: float, evidence_count: int, meaning_text: str) -> str:
    lowered = normalize_text(meaning_text)
    if evidence_count <= 0:
        return "unsupported"
    if "cannot justify a direct answer" in lowered or "no cited evidence" in lowered:
        return "unsupported"
    if confidence >= 0.75:
        return "supported"
    if confidence >= 0.55:
        return "partial"
    return "inconclusive"


def _packet_focus(packet: Dict[str, Any]) -> str:
    question_tape = packet.get("question_tape") or []
    if question_tape:
        return str((question_tape[0] or {}).get("focus") or "").strip()
    ingress = ((packet.get("meta") or {}).get("ingress")) or {}
    accepted = (ingress.get("accepted_candidate") or {})
    return str(accepted.get("focus") or "").strip()


def _packet_transform(packet: Dict[str, Any]) -> str:
    question_tape = packet.get("question_tape") or []
    if question_tape:
        return str((question_tape[0] or {}).get("transform") or "summary").strip() or "summary"
    ingress = ((packet.get("meta") or {}).get("ingress")) or {}
    accepted = (ingress.get("accepted_candidate") or {})
    return str(accepted.get("transform") or "summary").strip() or "summary"


def _atlas_hits(packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for row in packet.get("source_rows") or []:
        if str((row or {}).get("kind") or "") != "atlas_hit":
            continue
        raw = (row or {}).get("raw") or {}
        hits.append(
            {
                "title": str(raw.get("atlas_title") or row.get("actor") or "").strip(),
                "region": str(raw.get("atlas_region") or row.get("thread") or "").strip(),
                "score": float(raw.get("atlas_score") or row.get("priority") or 0.0),
                "ref": str(row.get("ref") or "").strip(),
                "object": str(row.get("object") or "").strip(),
                "tags": list(row.get("tags") or []),
            }
        )
    return hits


def _is_generic_concept_hit(hit: Dict[str, Any]) -> bool:
    object_text = normalize_text(hit.get("object") or "")
    return object_text.endswith("concept")


def build_judge_ruling(query: str, packet: Dict[str, Any]) -> Dict[str, Any]:
    focus = _packet_focus(packet) or query
    transform = _packet_transform(packet)
    hits = _atlas_hits(packet)
    confidence = float((((packet.get("meta") or {}).get("confidence") or {}).get("total")) or 0.0)
    top = hits[0] if hits else {}
    second = hits[1] if len(hits) > 1 else {}
    focus_key = normalize_text(focus)
    exact_focus_hits = [hit for hit in hits if normalize_text(hit["title"]) == focus_key]
    exact_focus = exact_focus_hits[0] if exact_focus_hits else top
    regions = [hit["region"] for hit in hits if hit.get("region")]
    region_count = len(set(regions))
    close_competition = bool(top and second and abs(float(top.get("score") or 0.0) - float(second.get("score") or 0.0)) <= 0.12)

    admitted: List[str] = []
    related: List[str] = []
    unresolved: List[str] = []

    if exact_focus:
        admitted.append(
            f"Atlas retrieved '{exact_focus['title']}' as the strongest directly named material match "
            f"({exact_focus['region']}, score {exact_focus['score']:.3f})."
        )
    if transform == "define":
        if exact_focus and _is_generic_concept_hit(exact_focus):
            unresolved.append(
                "The current Atlas material establishes real related source material, but not yet a precise definition statement."
            )
        if close_competition or region_count > 1:
            unresolved.append(
                "Nearby Atlas material branches across multiple related titles and regions, so the court should treat this as a cluster, not a settled one-line definition."
            )
    elif not hits:
        unresolved.append("Atlas did not retrieve source material for this query.")

    for hit in hits[1:5]:
        related.append(f"{hit['title']} ({hit['region']}, score {hit['score']:.3f})")

    if not hits:
        status = "unsupported"
    elif transform == "define" and unresolved:
        status = "partial"
    elif confidence >= 0.75 and not unresolved:
        status = "supported"
    elif confidence >= 0.55:
        status = "partial"
    else:
        status = "inconclusive"

    return {
        "query": query,
        "focus": focus,
        "transform": transform,
        "status": status,
        "confidence": round(confidence, 3),
        "admitted_findings": admitted,
        "related_material": related,
        "unresolved_points": unresolved,
        "citations": [hit.get("ref") for hit in hits[:5] if hit.get("ref")],
        "top_hit": exact_focus or top,
    }


def ruling_allowed_claims(ruling: Dict[str, Any]) -> List[str]:
    claims: List[str] = []
    for key in ("admitted_findings", "unresolved_points"):
        for item in ruling.get(key) or []:
            text = str(item).strip()
            if text:
                claims.append(text)
    for item in ruling.get("related_material") or []:
        text = str(item).strip()
        if text:
            claims.append(f"Related Atlas material: {text}.")
    return claims


def render_ruling(ruling: Dict[str, Any]) -> str:
    lines = [
        f"status: {ruling['status']}",
        f"confidence: {ruling['confidence']:.3f}",
        f"transform: {ruling['transform']}",
        f"focus: {ruling['focus']}",
    ]
    admitted = ruling.get("admitted_findings") or []
    related = ruling.get("related_material") or []
    unresolved = ruling.get("unresolved_points") or []
    if admitted:
        lines.append("")
        lines.append("admitted_findings:")
        for item in admitted:
            lines.append(f"- {item}")
    if related:
        lines.append("")
        lines.append("related_material:")
        for item in related:
            lines.append(f"- {item}")
    if unresolved:
        lines.append("")
        lines.append("unresolved_points:")
        for item in unresolved:
            lines.append(f"- {item}")
    if ruling.get("citations"):
        lines.append("")
        lines.append("citations:")
        for item in ruling["citations"]:
            lines.append(f"- {item}")
    return "\n".join(lines)


def extract_admitted_claims(packet: Dict[str, Any]) -> List[str]:
    admitted: List[str] = []
    answer_tape = packet.get("answer_tape") or []
    if answer_tape:
        claim = str((answer_tape[0] or {}).get("claim") or "").strip()
        if claim:
            admitted.append(claim)
    for item in packet.get("evidence_citations") or []:
        claim = str((item or {}).get("claim_glob") or "").strip()
        if claim:
            admitted.append(claim)
    meaning = str(((packet.get("meta") or {}).get("meaning_text")) or "").strip()
    if meaning:
        for claim in sentence_claims(meaning):
            if "[" in claim:
                admitted.append(claim)
    deduped: List[str] = []
    seen = set()
    for claim in admitted:
        key = normalize_text(claim)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(claim)
    return deduped


def compare_claim_sets(answer_text: str, admitted_claims: Sequence[str]) -> Dict[str, Any]:
    claims = sentence_claims(answer_text)
    unsupported: List[Dict[str, Any]] = []
    supported: List[Dict[str, Any]] = []
    for claim in claims:
        score, support = best_support(claim, admitted_claims)
        bucket = {"claim": claim, "support": support, "score": round(score, 3)}
        if score >= 0.45:
            supported.append(bucket)
        else:
            unsupported.append(bucket)
    total = len(claims)
    return {
        "claims": claims,
        "supported_claims": supported,
        "unsupported_claims": unsupported,
        "unsupported_claim_count": len(unsupported),
        "unsupported_claim_rate": round((len(unsupported) / total), 3) if total else 0.0,
        "supported_claim_count": len(supported),
    }


def agreement_score(raw_answer: str, atlas_claims: Sequence[str]) -> float:
    raw_claims = sentence_claims(raw_answer)
    if not raw_claims or not atlas_claims:
        return 0.0
    scores = [best_support(claim, atlas_claims)[0] for claim in raw_claims]
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def extract_self_confidence(payload: Dict[str, Any], text: str) -> float:
    value = payload.get("confidence")
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    match = re.search(r"\bconfidence\b[^0-9]{0,12}([01](?:\.\d+)?)", str(text or ""), re.I)
    if match:
        return max(0.0, min(1.0, float(match.group(1))))
    return 0.0


def render_panel(title: str, body: str) -> str:
    return f"\n{title}\n{'=' * len(title)}\n{body.strip() if body.strip() else '(empty)'}\n"


def read_queries(path: Path) -> List[str]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    queries: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if cleaned:
            queries.append(cleaned)
    return queries


@dataclass
class CourtroomConfig:
    tapes: Optional[Path]
    atlas_dir: Optional[Path]
    loombit_index: Optional[Path]
    loombit_dict: Optional[Path]
    enable_search: bool
    target_confidence: float
    ingress_model: str
    raw_model: str
    outgress_model: str
    max_appeals: int = 1


class CourtroomHarness:
    def __init__(self, config: CourtroomConfig) -> None:
        self.config = config
        self._atlas: Optional[AtlasRetriever] = None

    def _atlas_client(self) -> AtlasRetriever:
        if self.config.atlas_dir is None:
            raise RuntimeError("Atlas directory is not configured")
        if self._atlas is None:
            self._atlas = AtlasRetriever(self.config.atlas_dir)
        return self._atlas

    def ingress_payload(self, query: str) -> Any:
        if not self.config.ingress_model:
            return None
        summary = None
        if self.config.loombit_index:
            summary = summarize_index(str(self.config.loombit_index), dict_path=str(self.config.loombit_dict) if self.config.loombit_dict else None)
        return generate_candidates(query, model=self.config.ingress_model, index_summary=summary)

    def atlas_retrieve(self, query: str) -> Dict[str, Any]:
        if self.config.atlas_dir is None:
            return {"hits": [], "relations": {}}
        started = time.perf_counter()
        payload = self._atlas_client().retrieve(query)
        payload["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return payload

    def packet_for_query(self, query: str, translator_payload: Any, atlas_payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        started = time.perf_counter()
        tapes_input: str | Path | Sequence[Any]
        tapes_input = self.config.tapes if self.config.tapes is not None else []
        packet = export_packet(
            query_tapes_via_ingress(
                tapes_input,
                query,
                translator_payload=translator_payload,
                enable_search=self.config.enable_search,
                loombit_index=str(self.config.loombit_index) if self.config.loombit_index else None,
                loombit_dict=str(self.config.loombit_dict) if self.config.loombit_dict else None,
                atlas_payload=atlas_payload,
            )
        )
        return packet, int((time.perf_counter() - started) * 1000)

    def raw_llm_answer(self, query: str) -> Dict[str, Any]:
        prompt = (
            "Answer the user's question directly.\n"
            "Return ONLY JSON with keys: answer, confidence, assumptions, claims.\n"
            "Use confidence as a number from 0 to 1.\n"
            "Use claims as short factual strings.\n"
            f"User query: {query}"
        )
        usage = complete_with_usage(prompt, model=self.config.raw_model, temperature=0.0, num_predict=700)
        payload = maybe_parse_json_object(usage["text"])
        answer = str(payload.get("answer") or usage["text"]).strip()
        claims = payload.get("claims")
        if not isinstance(claims, list):
            claims = sentence_claims(answer)
        assumptions = payload.get("assumptions")
        if isinstance(assumptions, list):
            cleaned_assumptions = [str(item).strip() for item in assumptions if str(item).strip()]
        elif assumptions:
            cleaned_assumptions = [str(assumptions).strip()]
        else:
            cleaned_assumptions = []
        return {
            "answer": answer,
            "confidence": extract_self_confidence(payload, usage["text"]),
            "assumptions": cleaned_assumptions,
            "claims": [str(item).strip() for item in claims if str(item).strip()],
            "usage": usage,
            "raw_payload": payload,
        }

    def appeal_ticket(self, query: str, packet: Dict[str, Any], raw_answer: Dict[str, Any]) -> Dict[str, Any]:
        ruling = build_judge_ruling(query, packet)
        unsupported = compare_claim_sets(raw_answer["answer"], ruling_allowed_claims(ruling))["unsupported_claims"]
        prompt = (
            "You are preparing one narrow appeal to a deterministic Atlas engine.\n"
            "Do not write a freeform answer. Propose one narrow challenge to the current ruling.\n"
            "Return ONLY JSON with keys: reason, suggested_search_query, proposed_claim, needed_evidence.\n"
            f"Original user query: {query}\n"
            f"Current ruling: {json.dumps(ruling, ensure_ascii=False)}\n"
            f"Raw LLM claims: {json.dumps(raw_answer.get('claims') or [], ensure_ascii=False)}\n"
            f"Claims not currently admitted: {json.dumps(unsupported[:4], ensure_ascii=False)}\n"
        )
        usage = complete_with_usage(prompt, model=self.config.raw_model, temperature=0.0, num_predict=260)
        payload = maybe_parse_json_object(usage["text"])
        payload["_usage"] = usage
        return payload

    def _zero_usage(self) -> Dict[str, Any]:
        return {
            "text": "",
            "model": self.config.outgress_model,
            "provider": provider(),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
        }

    def outgress_answer(self, query: str, ruling: Dict[str, Any]) -> Dict[str, Any]:
        """Fill-only outgress: a deterministic template render is the canonical answer; the LLM
        may only re-phrase it, and any content word it introduces that is not in the allowed
        vocabulary is rejected (the deterministic text ships instead). Starved slots and forced
        downgrades are returned as gap records — the feedback signal for synthesis."""

        def smoother(deterministic: str, allowed: List[str]) -> Tuple[str, Dict[str, Any]]:
            prompt = (
                "Re-phrase the court's ruling below so it reads naturally for the user.\n"
                "STRICT RULE: you may ONLY use words that already appear in the ruling text or "
                "in this allowed vocabulary. Do not introduce any new noun, name, number, or "
                "fact. You are smoothing language, not adding information.\n"
                "Keep it to 1-3 sentences. Return ONLY JSON: {\"final_response\": \"...\"}.\n"
                f"Allowed vocabulary: {', '.join(allowed)}\n"
                f"Ruling text: {deterministic}\n"
            )
            usage = complete_with_usage(prompt, model=self.config.outgress_model,
                                        temperature=0.0, num_predict=300)
            payload = maybe_parse_json_object(usage["text"])
            text = str(payload.get("final_response") or "").strip()
            return text, usage

        result = render_outgress(query, ruling, smoother=smoother)
        usage = result.usage or self._zero_usage()
        return {
            "answer": result.answer,
            "deterministic": result.deterministic,
            "template_id": result.template_id,
            "ideal_template_id": result.ideal_template_id,
            "smoothed": result.smoothed,
            "grounding": result.grounding,
            "starved_slots": result.starved_slots,
            "gaps": result.gaps,
            "usage": usage,
            "raw_payload": {"final_response": result.answer},
            "error": result.error,
        }

    def maybe_run_appeal(self, query: str, packet: Dict[str, Any], raw_answer: Dict[str, Any]) -> Dict[str, Any]:
        atlas_conf = float((((packet.get("meta") or {}).get("confidence") or {}).get("total")) or 0.0)
        llm_conf = float(raw_answer.get("confidence") or 0.0)
        triggered = atlas_conf < self.config.target_confidence and llm_conf >= atlas_conf + 0.15
        result = {
            "triggered": triggered,
            "accepted": False,
            "improved_confidence": False,
            "ticket": {},
            "decision": "not_triggered",
            "packet": packet,
        }
        if not triggered or self.config.max_appeals <= 0:
            return result
        ticket = self.appeal_ticket(query, packet, raw_answer)
        result["ticket"] = ticket
        suggested_query = str(ticket.get("suggested_search_query") or "").strip()
        if not suggested_query:
            result["decision"] = "rejected_empty_query"
            return result
        atlas_payload = self.atlas_retrieve(suggested_query)
        if not atlas_payload.get("hits"):
            result["decision"] = "rejected_no_hits"
            return result
        appealed_packet, _ = self.packet_for_query(query, self.ingress_payload(query), atlas_payload)
        before_conf = float((((packet.get("meta") or {}).get("confidence") or {}).get("total")) or 0.0)
        after_conf = float((((appealed_packet.get("meta") or {}).get("confidence") or {}).get("total")) or 0.0)
        result["accepted"] = True
        result["packet"] = appealed_packet
        result["improved_confidence"] = after_conf > before_conf + 0.02
        result["decision"] = "accepted" if result["improved_confidence"] else "partially_accepted"
        return result

    def run_query(self, query: str) -> Dict[str, Any]:
        started = time.perf_counter()
        translator_payload = self.ingress_payload(query)
        atlas_payload = self.atlas_retrieve(query)
        packet, ruling_latency_ms = self.packet_for_query(query, translator_payload, atlas_payload)
        atlas_latency_ms = int(atlas_payload.get("latency_ms") or 0) + ruling_latency_ms
        judge_ruling = build_judge_ruling(query, packet)
        allowed_claims = ruling_allowed_claims(judge_ruling)
        atlas_conf = float((((packet.get("meta") or {}).get("confidence") or {}).get("total")) or 0.0)
        relation_conf = float((((packet.get("meta") or {}).get("confidence") or {}).get("relation")) or 0.0)
        meaning_conf = float((((packet.get("meta") or {}).get("confidence") or {}).get("meaning")) or 0.0)
        status = judge_ruling["status"]

        raw_answer = self.raw_llm_answer(query)
        comparison = compare_claim_sets(raw_answer["answer"], allowed_claims)
        comparison["agreement_score"] = agreement_score(raw_answer["answer"], judge_ruling.get("admitted_findings") or allowed_claims)
        comparison["atlas_confidence"] = round(atlas_conf, 3)
        comparison["llm_self_confidence"] = round(float(raw_answer.get("confidence") or 0.0), 3)

        appeal = self.maybe_run_appeal(query, packet, raw_answer)
        final_packet = appeal["packet"]
        final_ruling = build_judge_ruling(query, final_packet)
        final_allowed = ruling_allowed_claims(final_ruling)
        final_status = final_ruling["status"]
        outgress = self.outgress_answer(query, final_ruling)
        final_compare = compare_claim_sets(outgress["answer"], final_allowed) if outgress["answer"] else {
            "claims": [],
            "supported_claims": [],
            "unsupported_claims": [],
            "unsupported_claim_count": 0,
            "unsupported_claim_rate": 0.0,
            "supported_claim_count": 0,
        }
        total_latency_ms = int((time.perf_counter() - started) * 1000)

        return {
            "query": query,
            "translator_payload": translator_payload,
            "atlas_packet": packet,
            "judge_ruling": judge_ruling,
            "final_packet": final_packet,
            "final_ruling": final_ruling,
            "raw_llm": raw_answer,
            "comparison": comparison,
            "appeal": appeal,
            "outgress": outgress,
            "final_compare": final_compare,
            "status": status,
            "final_status": final_status,
            "metrics": {
                "timestamp": now_iso(),
                "query": query,
                "ingress_model": self.config.ingress_model or "heuristic",
                "outgress_model": outgress["usage"]["model"],
                "atlas_latency_ms": atlas_latency_ms,
                "raw_llm_latency_ms": int(raw_answer["usage"]["latency_ms"]),
                "outgress_latency_ms": int(outgress["usage"]["latency_ms"]),
                "total_latency_ms": total_latency_ms,
                "llm_prompt_tokens": int(raw_answer["usage"]["prompt_tokens"]) + int(outgress["usage"]["prompt_tokens"]),
                "llm_completion_tokens": int(raw_answer["usage"]["completion_tokens"]) + int(outgress["usage"]["completion_tokens"]),
                "atlas_confidence": round(atlas_conf, 3),
                "llm_self_confidence": round(float(raw_answer.get("confidence") or 0.0), 3),
                "agreement_score": comparison["agreement_score"],
                "unsupported_claim_count": comparison["unsupported_claim_count"],
                "admitted_claim_count": len(final_allowed),
                "rejected_claim_count": comparison["unsupported_claim_count"],
                "inconclusive_claim_count": 1 if final_status == "inconclusive" else 0,
                "appeal_triggered": bool(appeal["triggered"]),
                "appeal_accepted": bool(appeal["accepted"]),
                "appeal_improved_confidence": bool(appeal["improved_confidence"]),
                "final_status": final_status,
                "citation_count": len(final_packet.get("evidence_citations") or []),
                "source_count": len(final_packet.get("source_rows") or []),
                "answer_character_count": len(outgress["answer"]),
                "outgress_error": outgress.get("error") or "",
                "outgress_template_id": outgress.get("template_id") or "",
                "outgress_ideal_template_id": outgress.get("ideal_template_id") or "",
                "outgress_smoothed": bool(outgress.get("smoothed")),
                "outgress_grounded": bool((outgress.get("grounding") or {}).get("ok", True)),
                "outgress_invented_token_count": len((outgress.get("grounding") or {}).get("invented") or []),
                "starved_slot_count": len(outgress.get("starved_slots") or []),
                "synthesis_gap_count": len(outgress.get("gaps") or []),
                "time_to_first_ruling_ms": atlas_latency_ms,
                "time_to_final_answer_ms": total_latency_ms,
                "unsupported_claim_rate": comparison["unsupported_claim_rate"],
                "correction_rate": 1 if normalize_text(raw_answer["answer"]) != normalize_text(outgress["answer"]) else 0,
                "appeal_rate": 1 if appeal["triggered"] else 0,
                "appeal_success_rate": 1 if appeal["improved_confidence"] else 0,
                "final_admitted_claim_confidence": round(float(final_ruling.get("confidence") or 0.0), 3),
                "relation_score": round(relation_conf, 3),
                "meaning_score": round(meaning_conf, 3),
                "helpfulness_score": None,
                "baseline_delta": {
                    "unsupported_claim_rate_delta": round(comparison["unsupported_claim_rate"] - final_compare["unsupported_claim_rate"], 3),
                    "prompt_token_delta": int(raw_answer["usage"]["prompt_tokens"]) - int(outgress["usage"]["prompt_tokens"]),
                    "completion_token_delta": int(raw_answer["usage"]["completion_tokens"]) - int(outgress["usage"]["completion_tokens"]),
                },
            },
        }


def print_session(result: Dict[str, Any]) -> None:
    packet = result["atlas_packet"]
    judge_ruling = result["judge_ruling"]
    final_packet = result["final_packet"]
    final_ruling = result["final_ruling"]
    raw = result["raw_llm"]
    comparison = result["comparison"]
    appeal = result["appeal"]
    outgress = result["outgress"]
    ingress = ((packet.get("meta") or {}).get("ingress")) or {}
    raw_lines = [
        f"confidence: {raw.get('confidence', 0):.3f}",
        raw["answer"],
    ]
    comp_lines = [
        f"agreement_score: {comparison['agreement_score']}",
        f"unsupported_claim_count: {comparison['unsupported_claim_count']}",
        f"unsupported_claim_rate: {comparison['unsupported_claim_rate']}",
    ]
    if comparison["supported_claims"]:
        comp_lines.append("claims_both_support:")
        for item in comparison["supported_claims"][:6]:
            comp_lines.append(f"- {item['claim']} -> {item['support']} ({item['score']})")
    if comparison["unsupported_claims"]:
        comp_lines.append("claims_atlas_rejects_or_cannot_support:")
        for item in comparison["unsupported_claims"][:6]:
            comp_lines.append(f"- {item['claim']} ({item['score']})")
    appeal_lines = [f"decision: {appeal['decision']}"]
    if appeal.get("ticket"):
        appeal_lines.append(json.dumps(appeal["ticket"], ensure_ascii=False, indent=2))
    grounding = outgress.get("grounding") or {}
    final_lines = [
        f"final_status: {result['final_status']}",
        f"template: {outgress.get('template_id')}"
        + (f" (ideal {outgress.get('ideal_template_id')})" if outgress.get('ideal_template_id') != outgress.get('template_id') else "")
        + f"  smoothed: {outgress.get('smoothed')}  grounded: {grounding.get('ok', True)}",
    ]
    if grounding.get("invented"):
        final_lines.append(f"rejected_invented_tokens: {', '.join(grounding['invented'])}")
    if outgress.get("error"):
        final_lines.append(f"note: {outgress['error']} (fell back to deterministic render)")
    final_lines.append("")
    final_lines.append(outgress["answer"])

    print(render_panel("USER QUERY", result["query"]))
    print(render_panel("INGRESS TICKET", json.dumps(ingress, ensure_ascii=False, indent=2)))
    print(render_panel("ATLAS RULING", render_ruling(judge_ruling)))
    print(render_panel("PURE EVIDENCE", render_evidence(packet)))
    print(render_panel("RAW LLM ANSWER", "\n".join(raw_lines)))
    print(render_panel("COMPARISON", "\n".join(comp_lines)))
    if appeal["triggered"]:
        print(render_panel("APPEAL RESULT", "\n".join(appeal_lines)))
    print(render_panel("FINAL RESPONSE", "\n".join(final_lines)))
    gaps = outgress.get("gaps") or []
    if gaps:
        gap_lines = [f"- [{g.get('kind')}] {g.get('slot') or g.get('detail') or ''}".rstrip() for g in gaps]
        print(render_panel("SYNTHESIS GAPS (feedback)", "\n".join(gap_lines)))
    print(render_panel("FINAL RULING", render_ruling(final_ruling)))
    if final_packet is not packet:
        print(render_panel("FINAL EVIDENCE", render_evidence(final_packet)))


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Atlas + LLM courtroom harness")
    ap.add_argument("--tapes", default="", help="Optional tape folder or file for additional local context")
    ap.add_argument("--atlas-dir", default="", help="Atlas embedding pack directory")
    ap.add_argument("--query", default="", help="One-shot query; omit for terminal chat")
    ap.add_argument("--benchmark-queries", default="", help="Text or JSON file of queries for benchmark mode")
    ap.add_argument("--log-jsonl", default=str(ROOT / "courtroom_logs" / "atlas_courtroom.jsonl"), help="Per-query metrics log path")
    ap.add_argument("--gaps-jsonl", default=str(ROOT / "courtroom_logs" / "synthesis_gaps.jsonl"), help="Synthesis-feedback gap ledger path")
    ap.add_argument("--gaps-summary", action="store_true", help="Print the rolled-up gap backlog and exit")
    ap.add_argument("--artifacts-dir", default=str(ROOT / "courtroom_logs" / "runs"), help="Where to write saved outputs")
    ap.add_argument("--ingress-model", default="", help="Optional Ollama ingress model for ticket-writing")
    ap.add_argument("--raw-model", default="", help="Raw LLM comparison model")
    ap.add_argument("--outgress-model", default="", help="Atlas-guided final answer model")
    ap.add_argument("--loombit-index", default="", help="Optional loombit index for ingress routing hints")
    ap.add_argument("--loombit-dict", default="", help="Optional loombit dictionary path")
    ap.add_argument("--no-search", action="store_true", help="Disable confidence-repair search")
    ap.add_argument("--target-confidence", type=float, default=0.72, help="Appeal trigger threshold")
    return ap


def save_artifacts(artifacts_dir: Path, slug: str, payload: Dict[str, Any]) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    target = artifacts_dir / f"{slug}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "query"


def run_benchmark(harness: CourtroomHarness, queries: Sequence[str], log_jsonl: Path, artifacts_dir: Path, gaps_jsonl: Path) -> int:
    for idx, query in enumerate(queries, start=1):
        result = harness.run_query(query)
        raw_only = {
            "mode": "raw_llm_only",
            "query": query,
            "answer": result["raw_llm"]["answer"],
            "confidence": result["raw_llm"]["confidence"],
            "usage": result["raw_llm"]["usage"],
            "unsupported_claims_vs_atlas": result["comparison"],
        }
        atlas_only = {
            "mode": "atlas_only",
            "query": query,
            "status": result["status"],
            "packet": result["atlas_packet"],
        }
        guided = {
            "mode": "atlas_guided",
            "query": query,
            "status": result["final_status"],
            "answer": result["outgress"]["answer"],
            "metrics": result["metrics"],
            "comparison": result["final_compare"],
        }
        bundle = {
            "timestamp": now_iso(),
            "query": query,
            "raw_llm_only": raw_only,
            "atlas_only": atlas_only,
            "atlas_guided": guided,
        }
        append_jsonl(log_jsonl, {"benchmark": True, **result["metrics"]})
        write_gaps(gaps_jsonl, result["outgress"].get("gaps") or [])
        save_artifacts(artifacts_dir, f"{idx:03d}-{slugify(query)}", bundle)
        print(f"[{idx}/{len(queries)}] {query}")
        print(f"  raw unsupported rate: {result['comparison']['unsupported_claim_rate']}")
        print(f"  guided unsupported rate: {result['final_compare']['unsupported_claim_rate']}")
        print(f"  final status: {result['final_status']}  template: {result['outgress'].get('template_id')}"
              f"  smoothed: {result['outgress'].get('smoothed')}  gaps: {len(result['outgress'].get('gaps') or [])}")
    return 0


def build_config(args: argparse.Namespace) -> CourtroomConfig:
    return CourtroomConfig(
        tapes=Path(args.tapes) if args.tapes else None,
        atlas_dir=Path(args.atlas_dir) if args.atlas_dir else None,
        loombit_index=Path(args.loombit_index) if args.loombit_index else None,
        loombit_dict=Path(args.loombit_dict) if args.loombit_dict else None,
        enable_search=not args.no_search,
        target_confidence=float(args.target_confidence),
        ingress_model=str(args.ingress_model or "").strip(),
        raw_model=str(args.raw_model or default_model()).strip(),
        outgress_model=str(args.outgress_model or args.raw_model or default_model()).strip(),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    log_jsonl = Path(args.log_jsonl)
    gaps_jsonl = Path(args.gaps_jsonl)
    artifacts_dir = Path(args.artifacts_dir)

    if args.gaps_summary:
        print(json.dumps(summarize_gaps(gaps_jsonl), ensure_ascii=False, indent=2))
        return 0

    harness = CourtroomHarness(build_config(args))

    if args.benchmark_queries:
        queries = read_queries(Path(args.benchmark_queries))
        return run_benchmark(harness, queries, log_jsonl, artifacts_dir, gaps_jsonl)

    if args.query:
        result = harness.run_query(args.query)
        print_session(result)
        append_jsonl(log_jsonl, result["metrics"])
        write_gaps(gaps_jsonl, result["outgress"].get("gaps") or [])
        save_artifacts(artifacts_dir, slugify(args.query), result)
        return 0

    while True:
        try:
            query = input("\nquery> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 130
        if not query:
            continue
        if query.lower() in {"quit", "exit", ":q"}:
            return 0
        result = harness.run_query(query)
        print_session(result)
        append_jsonl(log_jsonl, result["metrics"])
        write_gaps(gaps_jsonl, result["outgress"].get("gaps") or [])
        save_artifacts(artifacts_dir, slugify(query), result)


if __name__ == "__main__":
    raise SystemExit(main())
