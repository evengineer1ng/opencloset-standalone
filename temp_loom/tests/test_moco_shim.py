"""MoCo LIVE 'sense' organ — polls the runtime telemetry snapshot, emits on intent change.

Tested with scripted telemetry payloads (the shape MoCo's RuntimeTelemetryWriter produces)
and with real snapshot files on disk. No mediapipe needed — only MoCo's own runtime needs it.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import Clock, Determinism, FederationEngine, SimulationOrgan  # noqa: E402
from oradio_engine.shims.moco_shim import make_moco_organ  # noqa: E402


def _payload(label, action=None, score=0.9, buttons=None):
    return {
        "recognition": {"committed_label": label, "top_label": label, "top_score": score},
        "output": {"active_action": action, "axes": {"ls_x": 0.0, "ls_y": 0.0}, "active_buttons": buttons or []},
        "updated_at": 1.0,
    }


class _ScriptedReader:
    """Stands in for the live telemetry file: returns the next snapshot per poll."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._i = 0

    def __call__(self):
        if self._i >= len(self._frames):
            return self._frames[-1] if self._frames else None  # snapshot persists (overwritten in place)
        f = self._frames[self._i]
        self._i += 1
        return f


def _frames():
    # idle, then a crossover, held (repeat = no new event), then a right_trigger.
    return [
        None,
        _payload("crossover", action="left_stick", score=0.82),
        _payload("crossover", action="left_stick", score=0.83),   # same intent -> no event
        _payload("right_trigger", action="accelerate", score=0.91),
    ]


def test_moco_organ_is_live_and_satisfies_contract():
    organ = make_moco_organ("moco", reader=_ScriptedReader(_frames()))
    assert isinstance(organ, SimulationOrgan)
    assert organ.identity().determinism is Determinism.LIVE


def test_emits_only_on_intent_change():
    eng = FederationEngine(clock=Clock())
    eng.register(make_moco_organ("moco", reader=_ScriptedReader(_frames())))
    eng.run(steps=4)
    beats = [c for c in eng.bus if c.source == "moco"]
    # 4 polls -> idle(none), crossover(emit), crossover(same, skip), right_trigger(emit) = 2
    assert len(beats) == 2
    assert [c.title for c in beats] == ["crossover", "right_trigger"]
    assert beats[1].priority == 0.91  # classifier score becomes candidate confidence


def test_record_then_replay_byte_identical():
    rec = FederationEngine(clock=Clock())
    rec.register(make_moco_organ("moco", reader=_ScriptedReader(_frames())))
    recorded = [c.as_dict() for c in rec.run(steps=4)]
    tape = next(iter(rec.organs.values())).tape

    from oradio_engine.live import LiveFeedOrgan
    rep = FederationEngine(clock=Clock())
    rep.register(LiveFeedOrgan.replay_from("moco", tape))
    replayed = [c.as_dict() for c in rep.run(steps=4)]
    assert replayed == recorded, "live motion is recorded once and replays deterministically"


def test_reads_real_telemetry_file(tmp_path):
    # Simulate MoCo's RuntimeTelemetryWriter: a JSON snapshot overwritten in place.
    path = tmp_path / "ui_runtime.json"
    organ = make_moco_organ("moco", telemetry_path=str(path))

    path.write_text(json.dumps(_payload("spin_move", action="dribble", score=0.77)), encoding="utf-8")
    d1 = organ.advance(1)
    assert len(d1.events) == 1 and d1.events[0]["title"] == "spin_move"

    # same snapshot -> no new event
    d2 = organ.advance(2)
    assert d2.events == []

    path.write_text(json.dumps(_payload("euro_step", action="dribble", score=0.66)), encoding="utf-8")
    d3 = organ.advance(3)
    assert len(d3.events) == 1 and d3.events[0]["title"] == "euro_step"
