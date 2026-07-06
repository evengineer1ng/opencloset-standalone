"""The corrected loop: the Index derives the CLAIM; reality's OUTCOME is observed + stored.

This is the fix for the closed-system bug — the earlier Index POC derived the outcome from a hash
(theory grading itself). Here the generator derives only confidence + what it predicts; the outcome
comes solely from an ObservationLog. A claim reality hasn't answered stays OPEN, never assumed.
Evidence outranks theory.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine.index import Index  # noqa: E402
from oradio_engine.observation import ObservationLog, grade  # noqa: E402


def claim_gen(seed, address):
    """Derive ONLY the hypothesis — confidence + predicted direction. NEVER the outcome."""
    _, n, _, i = address
    h = int(hashlib.sha256(f"{seed}:{n}:{i}".encode()).hexdigest(), 16)
    return {
        "confidence": round(0.5 + (h % 1000) / 2000.0, 4),
        "predicts": "up" if (h // 7) % 2 == 0 else "down",
    }


def _addresses():
    return [("t", n, "pred", i) for n in range(1, 6) for i in range(2)]  # 10 claims


def test_claims_are_derivable_outcomes_are_not():
    idx = Index("harbor", claim_gen)
    claim = idx.resolve(("t", 3, "pred", 1))
    assert set(claim) == {"confidence", "predicts"}, "the engine derives the claim..."
    assert "outcome" not in claim, "...but NOT the verdict — that's reality's to give"


def test_unanswered_claims_stay_open_not_assumed():
    idx = Index("harbor", claim_gen)
    addresses = _addresses()
    log = ObservationLog()  # reality has said nothing yet

    card = grade(idx, addresses, log)
    assert card["resolved"] == 0
    assert card["open"] == len(addresses), "no observation -> nothing graded, nothing assumed"
    assert card["hit_rate"] is None


def test_grade_joins_derived_claim_with_stored_evidence():
    idx = Index("harbor", claim_gen)
    addresses = _addresses()
    log = ObservationLog()

    # Reality answers only SOME of the claims (the rest remain open).
    answered = addresses[:6]
    for a in answered:
        # an external truth — here a coin we control, but the point is it is RECORDED, not derived
        log.record(a, "up" if a[1] % 2 == 0 else "down")

    card = grade(idx, addresses, log)
    assert card["resolved"] == 6
    assert card["open"] == len(addresses) - 6
    assert card["hits"] + card["misses"] == 6
    assert 0.0 <= card["hit_rate"] <= 1.0
    assert 0.0 <= card["brier"] <= 1.0


def test_evolutionary_funnel_is_survival_not_a_score_cliff():
    from oradio_engine.observation import evolutionary_funnel, frontier_threshold

    # 50 genomes with real, observed performance.
    perf = {("g", i): 0.90 + (int(hashlib.sha256(str(i).encode()).hexdigest(), 16) % 1000) / 10000
            for i in range(50)}
    history = evolutionary_funnel(perf, keep_fraction=0.5, seasons=5)

    counts = [r["survivors"] for r in history]
    bars = [r["bar"] for r in history]
    # graceful decline (~halving), NOT a cliff to 1
    assert counts[0] >= 20, f"first cut should keep ~half, not annihilate: {counts}"
    assert counts == sorted(counts, reverse=True), "survivors only ever shrink"
    # the standard RISES because the survivors get better — endogenous, not an absolute clock
    assert bars == sorted(bars), "the frontier rises each season"
    assert bars[-1] > bars[0], "the bar a survivor must clear is strictly higher by the end"
    # the bar is a member of the pool (a percentile of reality), not an abstract schedule
    assert frontier_threshold([0.1, 0.5, 0.9], keep_fraction=0.34) in (0.5, 0.9)


def test_evidence_is_recorded_once_reality_is_not_rerolled():
    log = ObservationLog()
    a = ("t", 1, "pred", 0)
    log.record(a, "up")
    log.record(a, "down")  # a second claim about the same moment must not overwrite the observed truth
    assert log.observed(a) == "up"
