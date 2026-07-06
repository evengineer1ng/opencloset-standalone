"""The outgress keystone: deterministic fill is canonical, the LLM can only re-phrase under a
verified grounding cage, and starved slots become structured synthesis-feedback gaps."""

from __future__ import annotations

from oradio_engine.outgress import (
    HOUSE_VOCAB,
    content_tokens,
    ground_check,
    render,
    select_template,
    summarize_gaps,
    write_gaps,
)


# A "define" ruling where Atlas has the neighborhood but not the address (the albedo case):
# top hit is a generic concept, status partial, an unresolved note, no admitted finding.
ALBEDO_RULING = {
    "query": "what is albedo?",
    "focus": "albedo",
    "transform": "define",
    "status": "partial",
    "confidence": 0.94,
    "admitted_findings": [],
    "related_material": ["Aluminium (science, score 0.482)"],
    "unresolved_points": [
        "The current Atlas material establishes related source material, but not yet a precise definition statement."
    ],
    "citations": [],
    "top_hit": {"title": "Albedo", "region": "science", "score": 0.929, "object": "Albedo is a science concept"},
}

# A "rank" ruling where Atlas actually has the address (who-won style): admitted + citation.
RACE_RULING = {
    "query": "who won the race?",
    "focus": "won race",
    "transform": "rank",
    "status": "supported",
    "confidence": 0.91,
    "admitted_findings": ["Atlas retrieved 'NASCAR Championship' as the strongest match (sport, score 0.910)."],
    "related_material": [],
    "unresolved_points": [],
    "citations": ["atlas:won-race#lap1:row0"],
    "top_hit": {"title": "NASCAR Championship", "region": "sport", "score": 0.910, "object": "NASCAR Championship is a competition"},
}


def test_deterministic_answer_is_always_produced_and_grounded():
    # No smoother => pure deterministic render. It must be non-empty (no more empty-answer
    # failure mode) and contain only allowed vocabulary by construction.
    res = render("what is albedo?", ALBEDO_RULING)
    assert res.answer
    assert res.answer == res.deterministic
    assert res.smoothed is False
    # the focus must surface in the rendered text
    assert "albedo" in res.answer.lower()


def test_define_partial_selects_define_template_and_flags_missing_address():
    res = render("what is albedo?", ALBEDO_RULING)
    assert res.template_id == "define.partial"
    kinds = {g["kind"] for g in res.gaps}
    # generic-concept define => a missing_address gap (the actionable atlas-recall signal)
    assert "missing_address" in kinds
    # the define.partial narrative is fully satisfied (top_title + unresolved present), so it
    # is NOT starved — the honest signal here is missing_address, not blank-field noise.
    assert res.starved_slots == []


def test_supported_summary_with_empty_unresolved_is_not_a_gap():
    # An empty `unresolved` on a *supported* answer is correct, not starvation.
    res = render("who won the race?", RACE_RULING)
    assert "unresolved" not in res.starved_slots


def test_define_wanting_a_supported_answer_starves_on_admitted():
    # If the ideal is define.supported (needs admitted+citations) but they're empty, the forced
    # downgrade records exactly what synthesis lacked.
    ruling = dict(ALBEDO_RULING, status="supported")
    res = render("what is albedo?", ruling)
    assert res.ideal_template_id == "define.supported"
    assert res.template_id != "define.supported"
    assert "admitted" in res.starved_slots
    assert any(g["kind"] == "template_downgrade" for g in res.gaps)


def test_rich_ruling_uses_rich_template_no_missing_address():
    res = render("who won the race?", RACE_RULING)
    # rank+supported has admitted + top_title, so the rich rank template applies
    assert res.template_id == "rank.supported"
    assert "missing_address" not in {g["kind"] for g in res.gaps}
    assert "NASCAR" in res.answer


def test_smoother_invention_is_rejected_falls_back_to_deterministic():
    # A smoother that injects a fact ("reflectivity") not in the source must be rejected.
    def lying_smoother(text, allowed):
        return ("Albedo is the reflectivity ratio of a surface.", {"model": "x", "latency_ms": 1,
                                                                     "prompt_tokens": 1, "completion_tokens": 1})

    res = render("what is albedo?", ALBEDO_RULING, smoother=lying_smoother)
    assert res.smoothed is False
    assert res.answer == res.deterministic
    assert res.grounding["ok"] is False
    assert "reflectivity" in res.grounding["invented"]
    assert res.error == "smoother_invented_tokens"


def test_smoother_grounded_rephrase_is_accepted():
    # A rephrase using only allowed words is accepted.
    def faithful_smoother(text, allowed):
        # reorder the deterministic text — every content word is already in `allowed`
        return (text, {"model": "x", "latency_ms": 1, "prompt_tokens": 1, "completion_tokens": 1})

    res = render("who won the race?", RACE_RULING, smoother=faithful_smoother)
    assert res.smoothed is True
    assert res.grounding["ok"] is True


def test_smoother_reasoning_leak_is_rejected():
    def leaky_smoother(text, allowed):
        return ("<think>let me reason</think> Atlas supports the answer.",
                {"model": "x", "latency_ms": 1, "prompt_tokens": 1, "completion_tokens": 1})

    res = render("who won the race?", RACE_RULING, smoother=leaky_smoother)
    # <think> is stripped; the remainder is grounded, so this one actually smooths fine —
    # assert the strip happened and no reasoning leaked into the answer.
    assert "<think>" not in res.answer
    assert "reason" not in res.answer.lower()


def test_ground_check_basics():
    allowed = content_tokens("albedo science concept") | HOUSE_VOCAB
    ok, invented = ground_check("albedo is a science concept", allowed)
    assert ok and not invented
    ok2, invented2 = ground_check("albedo is plutonium", allowed)
    assert not ok2 and "plutonium" in invented2


def test_gap_ledger_roundtrip_and_summary(tmp_path):
    ledger = tmp_path / "gaps.jsonl"
    res = render("what is albedo?", ALBEDO_RULING)
    write_gaps(ledger, res.gaps)
    summary = summarize_gaps(ledger)
    assert summary["total"] == len(res.gaps)
    assert summary["by_transform"].get("define") == len(res.gaps)
    assert "missing_address" in summary["by_kind"]


def test_unsupported_status_declines_cleanly():
    ruling = {"focus": "blorptangle", "transform": "summary", "status": "unsupported",
              "confidence": 0.0, "admitted_findings": [], "related_material": [],
              "unresolved_points": [], "citations": [], "top_hit": {}}
    res = render("what is a blorptangle?", ruling)
    assert res.template_id == "any.unsupported"
    assert "declines" in res.answer.lower() or "no admitted" in res.answer.lower()
