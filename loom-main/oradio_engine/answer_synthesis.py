"""Deterministic answer-template and citation scaffolding."""

from __future__ import annotations

from typing import Any, Dict, List

from .packet import ConceptCitation, EvidenceCitation, RouteTraceStep, SynthesisPlan


TEMPLATE_BY_TRANSFORM = {
    "summary": "synthesis_summary",
    "rank": "ranked_answer",
    "define": "definition",
    "actor_action": "direct_answer",
    "sequence": "chronology",
    "time": "chronology",
    "causal": "cause_because",
    "count": "count",
    "interpretive": "comparison",
}


def _template_id(answer_form: Dict[str, Any], confidence_total: float) -> str:
    transform = str(answer_form.get("transform") or "summary")
    if confidence_total < 0.35:
        return "route_not_answerable"
    if confidence_total < 0.55:
        return "uncertainty_boundary"
    return TEMPLATE_BY_TRANSFORM.get(transform, "direct_answer")


def _renderer_voice(answer_form: Dict[str, Any], render_voice: str) -> str:
    if render_voice:
        return render_voice
    transform = str(answer_form.get("transform") or "")
    if transform in {"causal", "interpretive"}:
        return "philosopher"
    if transform in {"rank", "count", "time", "sequence"}:
        return "town_crier"
    return "clerk"


def build_route_trace(ingress: Dict[str, Any] | None) -> List[RouteTraceStep]:
    if not ingress:
        return []
    route = ((ingress.get("arbitration") or {}).get("route") or {})
    ranked = route.get("ranked") or []
    steps: List[RouteTraceStep] = []
    for idx, item in enumerate(ranked[:8], start=1):
        path = str(item.get("path") or "")
        class_name = str(item.get("class_name") or item.get("class") or "")
        step_kind = "route_pack" if class_name == "lbpack" else ("route_shard" if class_name.endswith("index") else "route_leaf")
        label = str(item.get("topic") or item.get("summary") or path or item.get("id") or f"route-{idx}")
        steps.append(
            RouteTraceStep(
                step_kind=step_kind,
                ref=str(item.get("id") or path or f"route-{idx}"),
                label=label,
                score=float(item.get("score") or 0.0),
                meta={
                    "path": path,
                    "bucket": str(item.get("bucket") or ""),
                    "gradient": str(item.get("gradient") or ""),
                    "reasons": list(item.get("reasons") or []),
                    "lineage": list(item.get("lineage") or []),
                },
            )
        )
    return steps


def build_concept_citations(ingress: Dict[str, Any] | None, route_trace: List[RouteTraceStep]) -> List[ConceptCitation]:
    if not ingress:
        return []
    citations: List[ConceptCitation] = []
    candidate = ingress.get("accepted_candidate") or {}
    route_refs = [step.ref for step in route_trace[:3]]
    label = str(candidate.get("focus") or candidate.get("normalized_query") or ingress.get("accepted_query") or "query")
    reason = "ingress focus + route alignment"
    citations.append(
        ConceptCitation(
            citation_id="concept:focus",
            label=label,
            reason=reason,
            score=float((ingress.get("arbitration") or {}).get("winner_scores", {}).get("total") or 0.0),
            route_refs=route_refs,
            meta={
                "entities": list(candidate.get("entities") or []),
                "aim_tokens": list(candidate.get("aim_tokens") or []),
                "preferred_paths": list(candidate.get("preferred_paths") or []),
            },
        )
    )
    for idx, step in enumerate(route_trace[:3], start=1):
        citations.append(
            ConceptCitation(
                citation_id=f"concept:route:{idx}",
                label=step.label,
                reason=", ".join(step.meta.get("reasons") or []) or "route match",
                score=step.score,
                route_refs=[step.ref],
                meta={"path": step.meta.get("path", ""), "lineage": step.meta.get("lineage", [])},
            )
        )
    return citations


def build_evidence_citations(result: Dict[str, Any]) -> List[EvidenceCitation]:
    evidence = result.get("evidence") or []
    out: List[EvidenceCitation] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        trails = item.get("trail") or []
        refs: List[str] = []
        for trail in trails:
            if not isinstance(trail, dict):
                continue
            ref = str(trail.get("evidence_ref") or "")
            if ref:
                refs.append(ref)
        out.append(
            EvidenceCitation(
                citation_id=f"evidence:{item.get('id')}",
                claim_glob=str(item.get("claim_glob") or ""),
                evidence_refs=refs,
                trail=trails,
                meta={"footnote_id": item.get("id")},
            )
        )
    return out


def build_synthesis_plan(
    *,
    answer_form: Dict[str, Any],
    result: Dict[str, Any],
    ingress: Dict[str, Any] | None,
    render_voice: str,
    confidence_total: float,
    concept_citations: List[ConceptCitation],
    evidence_citations: List[EvidenceCitation],
) -> SynthesisPlan:
    template_id = _template_id(answer_form, confidence_total)
    renderer_voice = _renderer_voice(answer_form, render_voice)
    filled_slots = {
        "subject": str(answer_form.get("subject") or ""),
        "claim": str(answer_form.get("claim") or ""),
        "relation_clause": str(answer_form.get("relation_clause") or ""),
        "evidence_clause": str(answer_form.get("evidence_clause") or ""),
        "implication_clause": str(answer_form.get("implication_clause") or ""),
        "meaning_text": str(result.get("meaning") or ""),
    }
    bindings = {
        "concept": [item.citation_id for item in concept_citations],
        "evidence": [item.citation_id for item in evidence_citations],
    }
    return SynthesisPlan(
        template_id=template_id,
        renderer_voice=renderer_voice,
        filled_slots=filled_slots,
        boundary_statement=str(answer_form.get("boundary_clause") or ""),
        citation_bindings=bindings,
        meta={
            "accepted_query": str((ingress or {}).get("accepted_query") or ""),
            "concept_count": len(concept_citations),
            "evidence_count": len(evidence_citations),
        },
    )
