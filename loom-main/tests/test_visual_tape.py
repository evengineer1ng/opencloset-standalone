from pathlib import Path

import pytest

from oradio_engine.contract import NormalizedCandidate
from oradio_engine.visual_index import VisualIndex, visual_seed
from oradio_engine.visual_tape import (
    DEFAULT_VISUAL_FAMILIES,
    VISUAL_FAMILY_RULES,
    VisualTapeLog,
    build_visual_snapshot,
    candidate_to_visual_events,
    descriptor_visual_families,
    truth_to_visual_events,
)
from oradio_engine.visual_thumbnail import render_visual_frame, thumbnail_sidecar_path, write_visual_thumbnail


def _descriptor() -> dict:
    return {
        "oradio": "loomproof",
        "theme": "ribbon",
        "visual": {
            "base": {"mode": "builtin", "theme": "ribbon", "path": ""},
            "tape": {
                "seed": "loomproof:42",
                "accumulation": "causal",
                "families": ["color_drift", "breath", "particles", "ripples"],
            },
            "thumbnail": {"mode": "sidecar_png"},
        },
        "loom_notes": {"premise": "A causal ribbon remembers what happened."},
    }


def _candidate(priority: float = 0.7) -> NormalizedCandidate:
    return NormalizedCandidate(
        post_id="cand-1",
        source="oracle",
        title="Signal bends",
        body="The world changed shape.",
        priority=priority,
        ts="2026-06-13T12:00:00Z",
        type="event",
        tags=("signal",),
    )


def test_visual_snapshot_is_deterministic_for_same_seed_and_tape():
    descriptor = _descriptor()
    log = VisualTapeLog()
    families = descriptor_visual_families(descriptor)
    log.extend(candidate_to_visual_events(_candidate(), 3, families=families))
    index = VisualIndex(visual_seed(descriptor))

    a = build_visual_snapshot(log, index, 3, width=640, height=360)
    b = build_visual_snapshot(log, index, 3, width=640, height=360)

    assert a.total_energy == b.total_energy
    assert a.hue_shift == b.hue_shift
    assert a.particles == b.particles
    assert a.ripples == b.ripples


def test_every_default_family_has_a_real_rule():
    assert set(DEFAULT_VISUAL_FAMILIES) <= set(VISUAL_FAMILY_RULES)
    assert len(DEFAULT_VISUAL_FAMILIES) >= 12
    persistences = {VISUAL_FAMILY_RULES[name].persistence for name in DEFAULT_VISUAL_FAMILIES}
    energy_scales = {VISUAL_FAMILY_RULES[name].energy_scale for name in DEFAULT_VISUAL_FAMILIES}
    assert len(persistences) > 4
    assert len(energy_scales) > 4


def test_family_events_carry_family_specific_causal_payload():
    candidate = _candidate()
    events = candidate_to_visual_events(candidate, 5, families=DEFAULT_VISUAL_FAMILIES)
    assert {event.family for event in events} == set(DEFAULT_VISUAL_FAMILIES)
    bias_values = {round(float(event.payload["bias"]), 5) for event in events}
    spread_values = {round(float(event.payload["spread"]), 5) for event in events}
    assert len(bias_values) > 4
    assert len(spread_values) > 4


def test_truth_events_map_different_truth_fields_to_different_families():
    previous = {
        "oracle": {"tick": 9, "legitimacy": 42.0, "corruption": 28.0, "public_faith": 50.0, "decrees": 2, "events": 8},
        "harbor": {"tick": 9, "active_threads": 3},
        "house": {"tick": 9, "location": "front_door"},
        "league": {"tick": 9, "mode": "live", "recorded": 11, "cursors": {"a": 1}},
    }
    current = {
        "oracle": {"tick": 10, "legitimacy": 36.0, "corruption": 63.0, "public_faith": 44.0, "decrees": 5, "events": 14},
        "harbor": {"tick": 10, "active_threads": 7},
        "house": {"tick": 10, "location": "kitchen"},
        "league": {"tick": 10, "mode": "replay", "recorded": 19, "cursors": {"a": 1, "b": 2, "c": 3}},
    }
    events = truth_to_visual_events(current, previous, 10, families=DEFAULT_VISUAL_FAMILIES)
    by_family = {event.family: event for event in events}

    assert "glitch" in by_family
    assert by_family["glitch"].payload["field"] == "rupture"
    assert "veil" in by_family
    assert by_family["veil"].payload["field"] == "uncertainty"
    assert "orbitals" in by_family
    assert by_family["orbitals"].payload["field"] == "cycles"
    assert "scanlines" in by_family
    assert by_family["scanlines"].payload["field"] == "mediation"
    assert "ripples" in by_family
    assert by_family["ripples"].payload["field"] == "crossings"


def test_visual_tape_compounds_as_entries_accumulate():
    descriptor = _descriptor()
    log = VisualTapeLog()
    families = descriptor_visual_families(descriptor)
    index = VisualIndex(visual_seed(descriptor))

    first = _candidate(0.35)
    second = NormalizedCandidate(
        post_id="cand-2",
        source="oracle",
        title="Pressure climbs",
        body="The tape should remember this too.",
        priority=0.95,
        ts="2026-06-13T12:01:00Z",
        type="event",
        tags=("pressure",),
    )
    log.extend(candidate_to_visual_events(first, 1, families=families))
    early = build_visual_snapshot(log, index, 1, width=640, height=360)
    log.extend(candidate_to_visual_events(second, 2, families=families))
    late = build_visual_snapshot(log, index, 2, width=640, height=360)

    assert late.entries > early.entries
    assert late.total_energy > early.total_energy
    assert late.zoom >= early.zoom
    assert late.haze >= early.haze


def test_render_and_thumbnail_sidecar(tmp_path):
    descriptor = _descriptor()
    descriptor_path = tmp_path / "loomproof.oradio"
    descriptor_path.write_text("oradio: loomproof\n", encoding="utf-8")
    log = VisualTapeLog()
    log.extend(candidate_to_visual_events(_candidate(), 2, families=descriptor_visual_families(descriptor)))

    image, snapshot, meta = render_visual_frame(
        descriptor,
        descriptor_path,
        log,
        tick=2,
        size=(320, 180),
        phase=0.7,
    )
    assert image.size == (320, 180)
    assert snapshot.entries > 0
    assert meta["base"].startswith("builtin:")

    out = write_visual_thumbnail(descriptor, descriptor_path, log, tick=2, size=(320, 180))
    assert out == thumbnail_sidecar_path(descriptor_path)
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_uses_continuous_video_media_time(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")

    video_path = tmp_path / "loop.avi"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (48, 32))
    if not writer.isOpened():
        pytest.skip("OpenCV video writer unavailable on this machine")
    writer.write(np.full((32, 48, 3), (0, 0, 255), dtype=np.uint8))
    writer.write(np.full((32, 48, 3), (255, 0, 0), dtype=np.uint8))
    writer.release()

    descriptor = _descriptor()
    descriptor["theme"] = str(video_path)
    descriptor["visual"]["base"] = {"mode": "media", "theme": "ribbon", "path": str(video_path)}
    descriptor_path = tmp_path / "video.oradio"
    descriptor_path.write_text("oradio: video\n", encoding="utf-8")

    image_a, _snapshot_a, meta_a = render_visual_frame(
        descriptor,
        descriptor_path,
        VisualTapeLog(),
        tick=0,
        size=(96, 64),
        media_time=0.0,
    )
    image_b, _snapshot_b, meta_b = render_visual_frame(
        descriptor,
        descriptor_path,
        VisualTapeLog(),
        tick=0,
        size=(96, 64),
        media_time=0.25,
    )
    assert meta_a["base"].startswith("video:")
    assert meta_b["base"].startswith("video:")
    assert image_a.tobytes() != image_b.tobytes()
