"""The deterministic event-detector — domain-agnostic continuous→discrete."""

from __future__ import annotations

from oradio_engine.detect import milestones, overtakes, running_extrema, threshold_crossings


def test_threshold_crossings_up_with_rearm():
    samples = [{"hr": 80}, {"hr": 119}, {"hr": 122}, {"hr": 121}, {"hr": 90}, {"hr": 125}]
    hits = threshold_crossings(samples, "hr", 120, direction="up", hysteresis=5)
    assert [i for i, _ in hits] == [2, 5]  # crosses up, drops below 115 to re-arm, crosses again


def test_running_extrema_new_fastest_lap():
    laps = [{"t": 92.0}, {"t": 91.4}, {"t": 91.6}, {"t": 90.9}]
    fastest = running_extrema(laps, "t", mode="min")
    assert [i for i, _ in fastest] == [0, 1, 3]  # each new best (lower) time


def test_overtakes_from_position_frames():
    # entity -> position (1 = leader). VER passes NOR between lap 1 and 2.
    frames = [{"VER": 2, "NOR": 1, "LEC": 3}, {"VER": 1, "NOR": 2, "LEC": 3}]
    evs = overtakes(frames)
    assert evs == [(1, "VER", "NOR")]


def test_overtakes_double_pass_is_two_events():
    frames = [{"A": 3, "B": 1, "C": 2}, {"A": 1, "B": 2, "C": 3}]
    evs = overtakes(frames)
    assert set((g, l) for _, g, l in evs) == {("A", "B"), ("A", "C")}


def test_milestones_every_step():
    samples = [{"d": 0}, {"d": 950}, {"d": 1010}, {"d": 1999}, {"d": 2100}]
    hits = milestones(samples, "d", 1000)
    assert [i for i, _ in hits] == [2, 4]  # crosses 1000, then 2000
