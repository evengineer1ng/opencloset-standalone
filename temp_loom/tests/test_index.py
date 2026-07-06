"""The Index — derivable addressing. Predictions become coordinates; the gate rises; a tiny
index addresses an exponential space and collapses any deep address back to the seed.

Named for the EP *Index* that preceded *.D.O.G.* — the album is the existence proof.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import EvidenceService  # noqa: E402
from oradio_engine.index import Address, Index, funnel, gate, lineage  # noqa: E402


# --------------------------------------------------------------------------- #
# A universe's predictions as a pure function of (seed, address) — never stored.
# --------------------------------------------------------------------------- #
def pred_gen(seed, address: Address):
    _, n, _, i = address  # ("t", N, "pred", i)
    h = int(hashlib.sha256(f"{seed}:{n}:{i}".encode()).hexdigest(), 16)
    confidence = round(0.5 + (h % 1000) / 2000.0, 4)         # 0.5 .. 1.0
    outcome = "hit" if (h // 1000) % 2 == 0 else "miss"
    return {"prediction_id": f"{seed}:{n}:{i}", "confidence": confidence,
            "status": "resolved", "resolution_outcome": outcome}


def _pred_addresses():
    return [("t", n, "pred", i) for n in range(1, 11) for i in range(3)]


def _derive_scorecard(index, addresses):
    rows = [index.resolve(a) for a in addresses]  # a WALK, nothing stored
    hits = sum(1 for r in rows if r["resolution_outcome"] == "hit")
    misses = sum(1 for r in rows if r["resolution_outcome"] == "miss")
    n = hits + misses
    brier = sum((r["confidence"] - (1.0 if r["resolution_outcome"] == "hit" else 0.0)) ** 2
                for r in rows) / n
    return {"hits": hits, "misses": misses, "hit_rate": hits / n, "brier": round(brier, 4)}


def test_resolve_is_deterministic_and_stores_nothing():
    idx = Index("harbor", pred_gen)
    a = ("t", 144, "pred", 3)
    assert idx.resolve(a) == idx.resolve(a)            # same coordinate -> same element
    assert idx._cache == {}, "nothing stored — predictions are derived, not kept"


def test_predictions_as_coordinates_equal_the_evidence_service():
    # The un-indexed way: feed the rows into EvidenceService (stores a growing dict).
    idx = Index("harbor", pred_gen)
    addresses = _pred_addresses()
    ev = EvidenceService()
    ev.ingest("harbor", [idx.resolve(a) for a in addresses], tick=10)
    stored = ev.scorecard()

    # The indexed way: derive the scorecard by WALKING addresses — store only the generator.
    derived = _derive_scorecard(idx, addresses)

    assert derived["hits"] == stored["hits"]
    assert derived["misses"] == stored["misses"]
    assert abs(derived["brier"] - stored["brier_score"]) < 1e-9
    # The whole point: same answer, but the Index kept ONE generator, not 30 rows.
    assert idx._cache == {}


def test_gate_rises_toward_but_never_one():
    assert gate(0, 0) == 0.9
    assert gate(1, 0) == 0.99
    assert abs(gate(2, 0) - 0.999) < 1e-12
    assert gate(0, 50) == 0.99           # gains a nine over time
    assert gate(0, 0) < gate(1, 0) < gate(2, 0) < 1.0


def test_funnel_surfaces_fewer_as_the_standard_rises():
    def score_gen(seed, address):
        h = int(hashlib.sha256(f"{seed}:{address}".encode()).hexdigest(), 16)
        return {"score": 0.90 + (h % 100) / 1000.0}   # 0.90 .. 0.999

    idx = Index("u", score_gen)
    cands = [("c", i) for i in range(300)]
    early = funnel(idx, cands, lambda el: el["score"], level=0, t=0)    # bar 0.900
    late = funnel(idx, cands, lambda el: el["score"], level=0, t=100)   # bar 0.999
    assert len(early) > 0
    assert len(late) < len(early), "the ever-tightening standard surfaces fewer over time"


# --------------------------------------------------------------------------- #
# Scaling / the album homage: a tiny index addresses an exponential space, resolves any
# deep address in O(1), and collapses it back to the seed in O(depth).
# --------------------------------------------------------------------------- #
EXP = 5  # ~English avg word length: each acrostic layer is ~5x the one above


def acro_parent(address: Address):
    _, layer, _, pos = address
    return None if layer == 0 else ("layer", layer - 1, "pos", pos // EXP)


def acro_gen(seed, address: Address):
    _, layer, _, pos = address
    return {"layer": layer, "pos": pos,
            "token": hashlib.sha256(f"{seed}:{layer}:{pos}".encode()).hexdigest()[:6]}


def _count(seed, layer):
    return len(seed) * EXP ** layer


def test_tiny_index_addresses_an_exponential_space_lazily():
    idx = Index("dog", acro_gen)
    assert _count("dog", 0) == 3
    assert _count("dog", 10) == 3 * 5 ** 10   # ~29 million positions...

    before = idx.calls
    idx.resolve(("layer", 10, "pos", 29_000_000))  # ...resolved with ONE derivation
    assert idx.calls - before == 1, "O(1) resolve, not O(N) — store the rule, derive the point"


def test_lineage_collapses_any_deep_address_back_to_the_seed():
    path = lineage(("layer", 10, "pos", 12_345), acro_parent)
    assert len(path) == 11                       # layers 10..0, O(depth)
    assert path[-1] == ("layer", 0, "pos", 0)    # reaches the seed — like reading 'dog' 6 levels down
