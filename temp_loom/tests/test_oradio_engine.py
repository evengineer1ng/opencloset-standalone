"""Contract + federation tests for oradio_engine.

Proves the spine the real organ shims will plug into:
  - the five-verb contract is satisfiable,
  - a shared clock advances sovereign organs together,
  - their output lands on one normalized bus,
  - benchmark axis #1 (Reproducibility): a deterministic federation replays
    byte-identical, and a different seed diverges,
  - the live/deterministic line is explicit (can_compute_future),
  - cross-organ ripple is OFF by default and changes output when enabled (axis #4 surface).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from oradio_engine import (  # noqa: E402
    Clock,
    Determinism,
    FederationEngine,
    NormalizedCandidate,
    OrganIdentity,
    SimulationOrgan,
    TickDelta,
    normalize_event,
)


# --------------------------------------------------------------------------- #
# Reference organs (the role real shims will play). A tiny deterministic
# "tide" world: each tick it folds its seed+tick into a pressure value and
# emits an event when pressure crosses a threshold. Pure function of (seed, tick).
# --------------------------------------------------------------------------- #
class TideOrgan:
    def __init__(self, name: str, seed: int) -> None:
        self._name = name
        self._seed = seed
        self._tick = 0
        self._pressure = 0.0
        self._nudge = 0.0  # external input accumulator (for ripple tests)

    def identity(self) -> OrganIdentity:
        return OrganIdentity(self._name, Determinism.DETERMINISTIC, self._seed)

    def _pressure_at(self, tick: int) -> float:
        # deterministic, namespace-keyed (mirrors SeededRNG (seed, ns, tick) discipline)
        import hashlib

        h = hashlib.sha256(f"{self._seed}:tide:{tick}".encode()).hexdigest()
        return (int(h[:8], 16) % 1000) / 1000.0

    def advance(self, to_tick: int) -> TickDelta:
        frm = self._tick
        events = []
        for t in range(frm + 1, to_tick + 1):
            p = self._pressure_at(t) + self._nudge
            self._pressure = p
            if p >= 0.7:
                events.append(
                    {"title": f"{self._name} surge", "heat": round(p, 3), "ts": float(t)}
                )
        self._tick = to_tick
        return TickDelta(
            from_tick=frm, to_tick=to_tick, events=events, heat=self._pressure,
            headline=f"{self._name} @ {self._pressure:.2f}",
        )

    def observe(self, delta: TickDelta):
        return [
            normalize_event(self._name, delta.to_tick, i, e)
            for i, e in enumerate(delta.events)
        ]

    def read_truth(self):
        return {"tick": self._tick, "pressure": round(self._pressure, 3), "seed": self._seed}

    def apply_input(self, event):
        self._nudge += float(event.get("nudge", 0.0))


class LiveOrgan(TideOrgan):
    def identity(self) -> OrganIdentity:
        return OrganIdentity(self._name, Determinism.LIVE, None)


def _build(seed_a=1, seed_b=2):
    eng = FederationEngine(clock=Clock())
    eng.register(TideOrgan("alpha", seed_a))
    eng.register(TideOrgan("beta", seed_b))
    return eng


# --------------------------------------------------------------------------- #
def test_organs_satisfy_protocol():
    assert isinstance(TideOrgan("x", 1), SimulationOrgan)


def test_shared_clock_advances_all_organs():
    eng = _build()
    eng.tick(5)
    assert eng.clock.tick == 5
    assert all(o.read_truth()["tick"] == 5 for o in eng.organs.values())


def test_bus_collects_normalized_candidates():
    eng = _build()
    eng.run(steps=30)
    assert eng.bus, "expected some surges on the bus over 30 ticks"
    c = eng.bus[0]
    assert isinstance(c, NormalizedCandidate)
    assert c.source in ("alpha", "beta")
    assert set(c.as_dict()) == {
        "post_id", "source", "title", "body", "priority", "ts", "type", "tags"
    }


def test_reproducibility_axis_1():
    a = _build(1, 2)
    b = _build(1, 2)
    out_a = [c.as_dict() for c in a.run(steps=50)]
    out_b = [c.as_dict() for c in b.run(steps=50)]
    assert out_a == out_b, "deterministic federation must replay byte-identical"


def test_different_seed_diverges():
    a = _build(1, 2)
    c = _build(7, 9)
    out_a = [c.post_id for c in a.run(steps=50)]
    out_c = [c.post_id for c in c.run(steps=50)]
    assert out_a != out_c, "distinct seeds must produce distinct worlds (emergence floor)"


def test_determinism_class_gates_future():
    assert TideOrgan("d", 1).identity().can_compute_future is True
    assert LiveOrgan("l", 1).identity().can_compute_future is False
    eng = _build()
    assert eng.is_fully_deterministic
    eng.register(LiveOrgan("gamma", 3))
    assert not eng.is_fully_deterministic


def test_ripple_off_by_default_and_changes_output_when_on():
    # OFF: baseline
    base = _build()
    base_out = [c.post_id for c in base.run(steps=40)]

    # ON: every surge nudges the *other* organ's pressure up, creating more surges
    def ripple(target_name, cand):
        return {"nudge": 0.05} if cand.title.endswith("surge") else None

    rippled = _build()
    rippled.ripple = ripple
    rippled_out = [c.post_id for c in rippled.run(steps=40)]

    assert len(rippled_out) >= len(base_out)
    assert rippled_out != base_out, "ripple must be observable (the axis-4 test surface)"
