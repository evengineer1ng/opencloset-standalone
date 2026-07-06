"""The evidence / calibration service — graded against synthetic claims (exact scoring)
and against ForkUniverse's REAL predictions (the loop end-to-end).

This is ATL's foundational contribution generalized: any organ's predictions become
gradable evidence, scored on resolution. Proven locally without ATL's runtime.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import (  # noqa: E402
    Clock,
    EvidenceService,
    FederationEngine,
)
from oradio_engine.shims.forkuniverse_shim import ForkUniverseOrgan  # noqa: E402

CREATION = dict(
    universe_title="Pressure Harbor",
    premise="A port city where love, debt, rumor, and grief reshape every alliance.",
    setting_kind="haunted_port_city",
    time_period="modern",
    story_mode="continuous",
    world_scale="district",
    starting_population=24,
    seed_mode="custom",
    ontology_domains=["love", "debt", "rumor", "grief"],
)


def test_exact_scoring_on_synthetic_claims():
    ev = EvidenceService()
    # Two confident hits, one confident miss.
    ev.ingest("t", [
        {"prediction_id": "p1", "confidence": 1.0, "status": "open"},
        {"prediction_id": "p2", "confidence": 0.0, "status": "open"},
        {"prediction_id": "p3", "confidence": 1.0, "status": "open"},
    ], tick=1)
    assert ev.open_count == 3 and ev.resolved_count == 0

    ev.ingest("t", [
        {"prediction_id": "p1", "confidence": 1.0, "resolution_outcome": "hit"},
        {"prediction_id": "p2", "confidence": 0.0, "resolution_outcome": "hit"},   # surprise
        {"prediction_id": "p3", "confidence": 1.0, "resolution_outcome": "miss"},  # overconfident
    ], tick=5)
    card = ev.scorecard()
    assert ev.open_count == 0 and card["resolved"] == 3
    assert card["hits"] == 2 and card["misses"] == 1
    assert abs(card["hit_rate"] - 2 / 3) < 1e-9
    # Brier = mean[(1-1)^2, (0-1)^2, (1-0)^2] = (0 + 1 + 1)/3
    assert abs(card["brier_score"] - (2 / 3)) < 1e-4


def test_idempotent_on_resurfaced_predictions():
    ev = EvidenceService()
    row_open = {"prediction_id": "p", "confidence": 0.8, "status": "open"}
    ev.ingest("t", [row_open], tick=1)
    ev.ingest("t", [row_open], tick=2)  # re-surfaced while still open
    assert ev.open_count == 1
    resolved = {"prediction_id": "p", "confidence": 0.8, "resolution_outcome": "hit"}
    ev.ingest("t", [resolved], tick=3)
    ev.ingest("t", [resolved], tick=4)  # re-surfaced after resolution
    assert ev.open_count == 0 and ev.resolved_count == 1


def test_evidence_wired_into_federation_with_real_forkuniverse():
    ev = EvidenceService()
    eng = FederationEngine(clock=Clock(), evidence=ev)
    eng.register(ForkUniverseOrgan.from_request(
        "harbor", world_ticks_per_clock_tick=12, custom_seed="evidence-1", **CREATION))
    eng.run(steps=12)  # 144 world ticks — predictions open and settle

    card = ev.scorecard()
    assert card["open"] + card["resolved"] > 0, "the world should make predictions"
    assert card["resolved"] > 0, "some predictions should have settled by tick 144"
    if card["graded"]:
        assert 0.0 <= card["hit_rate"] <= 1.0
        assert 0.0 <= card["brier_score"] <= 1.0
    assert "harbor" in ev.by_source()
