from __future__ import annotations

from oradio_engine.answer_synthesis import (
    build_concept_citations,
    build_evidence_citations,
    build_route_trace,
    build_synthesis_plan,
)


def test_build_route_and_citations_and_template():
    ingress = {
        "accepted_query": "who won the race",
        "accepted_candidate": {
            "focus": "race winner",
            "entities": ["Leclerc"],
            "aim_tokens": ["winner", "race"],
            "preferred_paths": ["sports"],
        },
        "arbitration": {
            "winner_scores": {"total": 0.77},
            "route": {
                "ranked": [
                    {
                        "id": "sports-basketball",
                        "path": "shards/title-sp.index.loombit",
                        "score": 0.81,
                        "topic": "basketball finals winner",
                        "summary": "winner trail",
                        "class_name": "wikipedia_shard_index",
                        "bucket": "sports",
                        "gradient": "sports/basketball",
                        "reasons": ["query:winner", "path:sports"],
                        "lineage": ["root"],
                    }
                ]
            },
        },
    }
    result = {
        "meaning": "Leclerc is on top [1].",
        "evidence": [
            {
                "id": 1,
                "claim_glob": "Leclerc is on top",
                "trail": [
                    {
                        "evidence_ref": "f1#lap16:row2",
                        "trail": [{"ref": "f1#lap16:row2", "lap": 16}],
                    }
                ],
            }
        ],
    }
    answer_form = {
        "transform": "rank",
        "subject": "the race",
        "claim": "Leclerc is on top",
        "relation_clause": "the query is strongly related",
        "evidence_clause": "the support is native",
        "implication_clause": "so the answer is a ranking",
        "boundary_clause": "This is bounded to the loaded tape folder",
    }
    route_trace = build_route_trace(ingress)
    concept = build_concept_citations(ingress, route_trace)
    evidence = build_evidence_citations(result)
    synthesis = build_synthesis_plan(
        answer_form=answer_form,
        result=result,
        ingress=ingress,
        render_voice="",
        confidence_total=0.77,
        concept_citations=concept,
        evidence_citations=evidence,
    )
    assert route_trace
    assert concept
    assert evidence[0].citation_id == "evidence:1"
    assert synthesis.template_id == "ranked_answer"
    assert synthesis.renderer_voice == "town_crier"
