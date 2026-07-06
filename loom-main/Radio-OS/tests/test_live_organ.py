"""The push/live contract + intake tape — the foundational seam ATL and MoCo share.

Proves the determinism boundary locally (no real feed): a live organ RECORDS once, and
replaying its tape reproduces the bus byte-for-byte. This is what lets ATL (push league
events) and MoCo (live motion-intent) be honest about live-vs-deterministic.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Clock, Determinism, FederationEngine, SimulationOrgan  # noqa: E402
from oradio_engine.live import IntakeTape, LiveFeedOrgan, LiveSource  # noqa: E402


class ScriptedSource:
    """Stands in for ATL's REST poll / MoCo's classifier stream: one batch per poll."""

    def __init__(self, batches):
        self._batches = list(batches)
        self._i = 0

    def poll(self):
        if self._i >= len(self._batches):
            return []
        batch = self._batches[self._i]
        self._i += 1
        return batch


def _batches():
    # e.g. ATL: standings deltas; MoCo: classified intents. Shaped for normalize_event.
    return [
        [{"title": "promotion", "body": "timmy +2", "priority": 0.6, "type": "standings"}],
        [],
        [{"title": "right_trigger", "body": "accelerate", "priority": 0.8, "type": "intent"},
         {"title": "crossover", "body": "left dribble", "priority": 0.5, "type": "intent"}],
        [{"title": "drawdown", "body": "dany -1.4%", "priority": 0.7, "type": "risk"}],
    ]


def test_live_organ_satisfies_contract_and_is_live():
    organ = LiveFeedOrgan("feed", source=ScriptedSource(_batches()))
    assert isinstance(organ, SimulationOrgan)
    assert organ.identity().determinism is Determinism.LIVE


def test_record_then_replay_is_byte_identical():
    # RECORD: drive a live organ through the federation; it records to its tape.
    rec_eng = FederationEngine(clock=Clock())
    rec_eng.register(LiveFeedOrgan("feed", source=ScriptedSource(_batches())))
    recorded_bus = [c.as_dict() for c in rec_eng.run(steps=4)]
    organ = next(iter(rec_eng.organs.values()))
    tape = organ.tape
    assert len(tape.entries) == 4, "4 observations recorded across the batches"

    # REPLAY: a fresh organ built from the tape reproduces the bus exactly.
    rep_eng = FederationEngine(clock=Clock())
    rep_eng.register(LiveFeedOrgan.replay_from("feed", tape))
    replayed_bus = [c.as_dict() for c in rep_eng.run(steps=4)]

    assert replayed_bus == recorded_bus, "replaying the tape must reproduce the live bus"


def test_tape_serialization_round_trips():
    eng = FederationEngine(clock=Clock())
    eng.register(LiveFeedOrgan("feed", source=ScriptedSource(_batches())))
    eng.run(steps=4)
    tape = next(iter(eng.organs.values())).tape

    restored = IntakeTape.from_list(tape.to_list())
    rep_eng = FederationEngine(clock=Clock())
    rep_eng.register(LiveFeedOrgan.replay_from("feed", restored))
    out = [c.body for c in rep_eng.run(steps=4)]
    live_again = FederationEngine(clock=Clock())
    live_again.register(LiveFeedOrgan("feed", source=ScriptedSource(_batches())))
    assert out == [c.body for c in live_again.run(steps=4)], "serialized tape replays identically"


def test_live_organ_marks_federation_not_fully_deterministic():
    eng = FederationEngine(clock=Clock())
    eng.register(LiveFeedOrgan("feed", source=ScriptedSource(_batches())))
    assert not eng.is_fully_deterministic, "a LIVE organ means the federation isn't pure-deterministic"


def test_mixed_pull_and_push_federation():
    """A deterministic pull reference organ + a live push organ on one clock/bus."""
    from tests.test_oradio_engine import TideOrgan  # reuse the reference pull organ

    eng = FederationEngine(clock=Clock())
    eng.register(TideOrgan("tide", 1))
    eng.register(LiveFeedOrgan("feed", source=ScriptedSource(_batches())))
    eng.run(steps=4)
    sources = {c.source for c in eng.bus}
    assert "feed" in sources
    assert not eng.is_fully_deterministic  # the live organ taints purity (correctly)
