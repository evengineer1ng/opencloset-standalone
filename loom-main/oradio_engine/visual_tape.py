"""Append-only visual causes for the Loom player."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from oradio_engine.contract import NormalizedCandidate

from oradio_engine.visual_index import VisualIndex

Address = Tuple[Any, ...]

DEFAULT_VISUAL_FAMILIES = (
    "color_drift",
    "breath",
    "particles",
    "ripples",
    "bloom",
    "scanlines",
    "veil",
    "orbitals",
    "glitch",
    "embers",
    "grain",
    "prisms",
)


def _hash_unit(*parts: Any) -> float:
    raw = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).digest()
    return int.from_bytes(raw[:4], "big") / 0xFFFFFFFF


def _tag_hits(candidate: NormalizedCandidate, *wanted: str) -> float:
    tags = {str(tag).lower() for tag in candidate.tags}
    return sum(1.0 for item in wanted if item.lower() in tags)


def _source_is(candidate: NormalizedCandidate, *wanted: str) -> float:
    source = str(candidate.source).lower()
    return 1.0 if source in {item.lower() for item in wanted} else 0.0


def _type_is(candidate: NormalizedCandidate, *wanted: str) -> float:
    ctype = str(candidate.type).lower()
    return 1.0 if ctype in {item.lower() for item in wanted} else 0.0


@dataclass(frozen=True)
class VisualFamilyRule:
    name: str
    energy_scale: float
    persistence: float
    hue_scale: float
    source_bias: Tuple[str, ...] = ()
    type_bias: Tuple[str, ...] = ()
    tag_bias: Tuple[str, ...] = ()
    text_bias: Tuple[str, ...] = ()

    def intake(self, candidate: NormalizedCandidate) -> Dict[str, float]:
        priority = max(0.05, float(candidate.priority))
        text = f"{candidate.title} {candidate.body}".lower()
        bias = 0.92 + (0.16 * _hash_unit(self.name, "bias"))
        bias += 0.22 * _source_is(candidate, *self.source_bias)
        bias += 0.18 * _type_is(candidate, *self.type_bias)
        bias += 0.14 * _tag_hits(candidate, *self.tag_bias)
        bias += 0.09 * sum(1.0 for item in self.text_bias if item.lower() in text)
        energy = max(0.03, priority * self.energy_scale * bias)
        direction = (_hash_unit(self.name, candidate.post_id, "direction") * 2.0) - 1.0
        spread = 0.2 + (0.8 * _hash_unit(self.name, candidate.post_id, "spread"))
        pulse = 0.15 + (0.85 * _hash_unit(self.name, candidate.post_id, "pulse"))
        return {
            "energy": energy,
            "direction": direction,
            "spread": spread,
            "pulse": pulse,
            "bias": bias,
        }

    def accumulate(self, event: "VisualTapeEvent", tick: int) -> float:
        age = max(0, int(tick) - int(event.tick))
        fade = self.persistence ** age
        pulse = float(event.payload.get("pulse") or 0.5)
        spread = float(event.payload.get("spread") or 0.5)
        return max(0.0, event.energy * fade * (0.78 + (0.22 * pulse)) * (0.84 + (0.16 * spread)))


VISUAL_FAMILY_RULES: Dict[str, VisualFamilyRule] = {
    "color_drift": VisualFamilyRule(
        "color_drift", energy_scale=0.95, persistence=0.986, hue_scale=1.25,
        type_bias=("event", "frame"), tag_bias=("signal", "mood", "heat"), text_bias=("glow", "shift", "turn", "tone"),
    ),
    "breath": VisualFamilyRule(
        "breath", energy_scale=0.72, persistence=0.993, hue_scale=0.28,
        source_bias=("moco", "ring_telemetry"), tag_bias=("body", "presence", "heartbeat", "sleep"), text_bias=("breath", "pulse", "room"),
    ),
    "particles": VisualFamilyRule(
        "particles", energy_scale=0.84, persistence=0.974, hue_scale=0.45,
        type_bias=("frame", "event"), tag_bias=("motion", "signal", "weather"), text_bias=("scatter", "dust", "spray"),
    ),
    "ripples": VisualFamilyRule(
        "ripples", energy_scale=0.88, persistence=0.969, hue_scale=0.50,
        source_bias=("simulated_spatial_array", "video_capture_sim"), tag_bias=("arrival", "impact", "wave"), text_bias=("arrived", "crossed", "echo"),
    ),
    "bloom": VisualFamilyRule(
        "bloom", energy_scale=0.90, persistence=0.981, hue_scale=0.82,
        tag_bias=("wonder", "hope", "signal"), text_bias=("flare", "shine", "rise", "open"),
    ),
    "scanlines": VisualFamilyRule(
        "scanlines", energy_scale=0.61, persistence=0.992, hue_scale=0.08,
        source_bias=("video_capture_sim", "pc_telemetry"), type_bias=("frame",), tag_bias=("broadcast", "monitor", "screen"), text_bias=("frame", "capture", "watch"),
    ),
    "veil": VisualFamilyRule(
        "veil", energy_scale=0.74, persistence=0.995, hue_scale=0.42,
        tag_bias=("memory", "fog", "dream", "melancholic"), text_bias=("veil", "mist", "shroud", "hidden"),
    ),
    "orbitals": VisualFamilyRule(
        "orbitals", energy_scale=0.67, persistence=0.989, hue_scale=0.66,
        source_bias=("oracle", "atl_league"), tag_bias=("cycle", "pattern", "thread"), text_bias=("orbit", "cycle", "loop"),
    ),
    "glitch": VisualFamilyRule(
        "glitch", energy_scale=0.92, persistence=0.944, hue_scale=0.18,
        source_bias=("pc_telemetry", "video_capture_sim"), tag_bias=("error", "spike", "rupture", "pressure"), text_bias=("break", "fault", "spike", "tear"),
    ),
    "embers": VisualFamilyRule(
        "embers", energy_scale=0.80, persistence=0.977, hue_scale=0.72,
        tag_bias=("heat", "warning", "conflict"), text_bias=("burn", "ember", "fire", "ash"),
    ),
    "grain": VisualFamilyRule(
        "grain", energy_scale=0.56, persistence=0.998, hue_scale=0.06,
        source_bias=("moco", "ring_telemetry", "pc_telemetry"), tag_bias=("background", "texture", "ambient"), text_bias=("ambient", "static", "noise"),
    ),
    "prisms": VisualFamilyRule(
        "prisms", energy_scale=0.78, persistence=0.982, hue_scale=1.05,
        tag_bias=("wonder", "vision", "refraction"), text_bias=("prism", "split", "spectrum", "shimmer"),
    ),
}


@dataclass(frozen=True)
class VisualTapeEvent:
    tick: int
    family: str
    source: str
    address: Address
    energy: float
    hue_hint: float
    lineage: Tuple[str, ...] = ()
    payload: Dict[str, Any] = field(default_factory=dict)
    seq: int = 0


@dataclass
class VisualTapeSnapshot:
    tick: int
    entries: int
    total_energy: float
    hue_shift: float
    family_energy: Dict[str, float]
    breath: float
    haze: float
    zoom: float
    bloom: float
    veil: float
    scanline_alpha: float
    grain: float
    glitch: float
    prism: float
    particles: List[Dict[str, float]]
    ripples: List[Dict[str, float]]
    orbitals: List[Dict[str, float]]
    lineage: List[Tuple[str, ...]]


class VisualTapeLog:
    """Append-only visual event log."""

    def __init__(self) -> None:
        self._events: List[VisualTapeEvent] = []
        self._next_seq = 1

    def append(self, event: VisualTapeEvent) -> VisualTapeEvent:
        stamped = VisualTapeEvent(
            tick=int(event.tick),
            family=event.family,
            source=event.source,
            address=tuple(event.address),
            energy=float(event.energy),
            hue_hint=float(event.hue_hint),
            lineage=tuple(event.lineage),
            payload=dict(event.payload),
            seq=self._next_seq,
        )
        self._next_seq += 1
        self._events.append(stamped)
        return stamped

    def extend(self, events: Iterable[VisualTapeEvent]) -> List[VisualTapeEvent]:
        return [self.append(event) for event in events]

    def clear(self) -> None:
        self._events.clear()
        self._next_seq = 1

    def through(self, tick: int) -> List[VisualTapeEvent]:
        return [event for event in self._events if event.tick <= tick]

    def recent(self, limit: int = 6) -> List[VisualTapeEvent]:
        return self._events[-limit:]

    def __len__(self) -> int:
        return len(self._events)


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except Exception:
        return 0.0


def _delta(prev: Dict[str, Any], cur: Dict[str, Any], key: str) -> float:
    return _to_float(cur.get(key)) - _to_float(prev.get(key))


def descriptor_visual_families(descriptor: Dict[str, Any]) -> List[str]:
    visual = descriptor.get("visual") if isinstance(descriptor.get("visual"), dict) else {}
    tape = visual.get("tape") if isinstance(visual.get("tape"), dict) else {}
    families = tape.get("families")
    if isinstance(families, list):
        cleaned = [str(item).strip() for item in families if str(item).strip()]
        if cleaned:
            return cleaned
    return list(DEFAULT_VISUAL_FAMILIES)


def candidate_to_visual_events(
    candidate: NormalizedCandidate,
    tick: int,
    *,
    families: Sequence[str],
) -> List[VisualTapeEvent]:
    base_lineage = (
        f"candidate:{candidate.post_id}",
        f"source:{candidate.source}",
        f"type:{candidate.type}",
        f"tick:{tick}",
    )
    events: List[VisualTapeEvent] = []
    for family in families:
        rule = VISUAL_FAMILY_RULES.get(family)
        if rule is None:
            continue
        intake = rule.intake(candidate)
        hue_hint = ((_hash_unit(candidate.post_id, candidate.source, candidate.type, family) * 2.0) - 1.0) * rule.hue_scale
        events.append(
            VisualTapeEvent(
                tick=tick,
                family=family,
                source=candidate.source,
                address=("t", tick, "candidate", candidate.post_id, "family", family),
                energy=float(intake["energy"]),
                hue_hint=hue_hint,
                lineage=base_lineage,
                payload={
                    "title": candidate.title,
                    "priority": candidate.priority,
                    "body": candidate.body,
                    "direction": float(intake["direction"]),
                    "spread": float(intake["spread"]),
                    "pulse": float(intake["pulse"]),
                    "bias": float(intake["bias"]),
                    "rule": family,
                },
            )
        )
    return events


def truth_to_visual_events(
    truth: Dict[str, Dict[str, Any]],
    previous_truth: Dict[str, Dict[str, Any]],
    tick: int,
    *,
    families: Sequence[str],
) -> List[VisualTapeEvent]:
    events: List[VisualTapeEvent] = []
    active = set(families)
    for organ_name, current in truth.items():
        if not isinstance(current, dict):
            continue
        previous = previous_truth.get(organ_name, {}) if isinstance(previous_truth.get(organ_name), dict) else {}
        lineage_base = (
            f"truth:{organ_name}",
            f"tick:{tick}",
        )

        thread_delta = _delta(previous, current, "active_threads")
        record_delta = _delta(previous, current, "recorded")
        event_delta = _delta(previous, current, "events")
        decree_delta = _delta(previous, current, "decrees")
        health = _to_float(current.get("health"))
        legitimacy = _to_float(current.get("legitimacy"))
        corruption = _to_float(current.get("corruption"))
        faith = _to_float(current.get("public_faith"))
        leagues = _to_float(current.get("leagues"))
        teams = _to_float(current.get("teams"))
        tier = str(current.get("tier") or "")
        era = str(current.get("era") or "")
        location = str(current.get("location") or "")
        cursors = current.get("cursors") if isinstance(current.get("cursors"), dict) else {}
        cursor_count = float(len(cursors))

        family_truth: Dict[str, Tuple[float, Dict[str, Any]]] = {
            "glitch": (
                max(0.0, abs(thread_delta) * 0.30 + max(0.0, corruption - legitimacy) * 0.02 + cursor_count * 0.05),
                {"organ": organ_name, "field": "rupture", "thread_delta": thread_delta, "corruption": corruption, "legitimacy": legitimacy, "cursor_count": cursor_count},
            ),
            "veil": (
                max(0.0, max(0.0, 50.0 - faith) * 0.012 + (0.18 if not location and not era else 0.0)),
                {"organ": organ_name, "field": "uncertainty", "faith": faith, "location": location, "era": era},
            ),
            "orbitals": (
                max(0.0, decree_delta * 0.18 + leagues * 0.02 + teams * 0.004 + (0.22 if era else 0.0)),
                {"organ": organ_name, "field": "cycles", "decrees": current.get("decrees"), "leagues": leagues, "teams": teams, "era": era},
            ),
            "embers": (
                max(0.0, event_delta * 0.12 + max(0.0, corruption) * 0.01 + (0.18 if "ember" in tier.lower() else 0.0)),
                {"organ": organ_name, "field": "aftermath", "event_delta": event_delta, "corruption": corruption, "tier": tier},
            ),
            "grain": (
                max(0.0, record_delta * 0.03 + cursor_count * 0.03 + (0.10 if current.get("mode") == "live" else 0.0)),
                {"organ": organ_name, "field": "ambient_texture", "record_delta": record_delta, "cursor_count": cursor_count, "mode": current.get("mode")},
            ),
            "prisms": (
                max(0.0, abs(health - legitimacy) * 0.01 + abs(faith - legitimacy) * 0.01 + max(0.0, thread_delta) * 0.08),
                {"organ": organ_name, "field": "multiplicity", "health": health, "legitimacy": legitimacy, "faith": faith, "thread_delta": thread_delta},
            ),
            "scanlines": (
                max(0.0, cursor_count * 0.06 + (0.12 if current.get("mode") == "replay" else 0.0)),
                {"organ": organ_name, "field": "mediation", "cursor_count": cursor_count, "mode": current.get("mode")},
            ),
            "ripples": (
                max(0.0, thread_delta * 0.24 + (0.16 if location and location != str(previous.get("location") or "") else 0.0)),
                {"organ": organ_name, "field": "crossings", "thread_delta": thread_delta, "location": location, "previous_location": previous.get("location")},
            ),
            "breath": (
                max(0.0, (0.14 if location else 0.0) + (0.18 if current.get("tick") != previous.get("tick") else 0.0) + max(0.0, 1.0 if tier else 0.0) * 0.04),
                {"organ": organ_name, "field": "organism_rhythm", "location": location, "tier": tier, "tick": current.get("tick")},
            ),
            "bloom": (
                max(0.0, max(0.0, legitimacy) * 0.006 + max(0.0, faith) * 0.004 + max(0.0, thread_delta) * 0.10),
                {"organ": organ_name, "field": "revelation", "legitimacy": legitimacy, "faith": faith, "thread_delta": thread_delta},
            ),
            "particles": (
                max(0.0, event_delta * 0.10 + thread_delta * 0.06 + teams * 0.002),
                {"organ": organ_name, "field": "distributed_motion", "event_delta": event_delta, "thread_delta": thread_delta, "teams": teams},
            ),
            "color_drift": (
                max(0.0, abs(health - faith) * 0.01 + abs(legitimacy - corruption) * 0.01 + max(0.0, leagues) * 0.003),
                {"organ": organ_name, "field": "tone_shift", "health": health, "faith": faith, "legitimacy": legitimacy, "corruption": corruption},
            ),
        }

        for family, (energy, payload) in family_truth.items():
            if family not in active:
                continue
            rule = VISUAL_FAMILY_RULES.get(family)
            if rule is None or energy <= 0.03:
                continue
            payload = dict(payload)
            payload["rule"] = family
            payload["pulse"] = 0.35 + (0.65 * _hash_unit("truth", family, organ_name, tick, payload.get("field")))
            payload["spread"] = 0.25 + (0.75 * _hash_unit("truth", family, organ_name, "spread"))
            direction = (_hash_unit("truth", family, organ_name, "direction", tick) * 2.0) - 1.0
            events.append(
                VisualTapeEvent(
                    tick=tick,
                    family=family,
                    source=f"truth:{organ_name}",
                    address=("t", tick, "truth", organ_name, "family", family),
                    energy=energy,
                    hue_hint=direction * rule.hue_scale,
                    lineage=lineage_base + (f"field:{payload['field']}",),
                    payload=payload,
                )
            )
    return events


def build_visual_snapshot(
    log: VisualTapeLog,
    index: VisualIndex,
    tick: int,
    *,
    width: int,
    height: int,
) -> VisualTapeSnapshot:
    events = log.through(tick)
    weighted_events: List[Tuple[VisualTapeEvent, float]] = []
    for event in events:
        rule = VISUAL_FAMILY_RULES.get(event.family)
        if rule is None:
            continue
        weighted_events.append((event, rule.accumulate(event, tick)))

    total_energy = sum(weight for _event, weight in weighted_events)
    family_energy: Dict[str, float] = {}
    for event, weight in weighted_events:
        family_energy[event.family] = family_energy.get(event.family, 0.0) + weight
    energy_scale = min(1.0, total_energy / 18.0) if events else 0.0
    hue_shift = 0.0
    if total_energy:
        hue_shift = max(
            -0.9,
            min(0.9, sum(event.hue_hint * weight for event, weight in weighted_events) / total_energy),
        )
    breath_energy = family_energy.get("breath", 0.0)
    veil_energy = family_energy.get("veil", 0.0)
    bloom_energy = family_energy.get("bloom", 0.0)
    particle_energy = family_energy.get("particles", 0.0) + family_energy.get("embers", 0.0)
    ripple_energy = family_energy.get("ripples", 0.0)
    orbital_energy = family_energy.get("orbitals", 0.0)
    glitch_energy = family_energy.get("glitch", 0.0)
    prism_energy = family_energy.get("prisms", 0.0)
    grain_energy = family_energy.get("grain", 0.0)
    scanline_energy = family_energy.get("scanlines", 0.0)
    zoom_energy = breath_energy + (0.35 * family_energy.get("color_drift", 0.0)) + (0.4 * bloom_energy)

    breath = 0.16 + min(0.84, breath_energy / 12.0 + total_energy / 80.0)
    haze = min(0.42, veil_energy / 18.0 + total_energy / 120.0)
    zoom = 1.0 + min(0.11, zoom_energy / 70.0)
    bloom = min(0.7, bloom_energy / 14.0)
    veil = min(0.55, veil_energy / 14.0)
    scanline_alpha = min(0.3, scanline_energy / 24.0)
    grain = min(0.32, grain_energy / 18.0)
    glitch = min(0.35, glitch_energy / 16.0)
    prism = min(0.45, prism_energy / 18.0)

    particle_count = min(56, 8 + len(weighted_events) // 2 + int(particle_energy * 3))
    particles: List[Dict[str, float]] = []
    for idx in range(particle_count):
        point = index.particle(tick // 2, idx)
        warm = 0.35 + (0.65 * _hash_unit("warm", tick, idx))
        particles.append(
            {
                "x": point["u"] * width,
                "y": point["v"] * height,
                "r": 2.0 + (8.0 * point["w"] * max(0.25, breath)),
                "alpha": 0.10 + (0.34 * point["z"] * max(0.18, energy_scale)),
                "warm": warm,
            }
        )

    ripples: List[Dict[str, float]] = []
    for event in [item for item, _weight in weighted_events if item.family == "ripples"][-8:]:
        point = index.ripple(event.tick, event.seq)
        ripples.append(
            {
                "cx": point["u"] * width,
                "cy": point["v"] * height,
                "radius": 30.0 + (max(width, height) * 0.18 * point["w"]) + (event.energy * 26.0) + (ripple_energy * 4.0),
                "alpha": 0.12 + (0.22 * min(1.0, event.energy)),
            }
        )

    orbital_count = min(18, int(orbital_energy * 3.5))
    orbitals: List[Dict[str, float]] = []
    for idx in range(orbital_count):
        point = index.resolve(("t", tick // 2, "orbital", idx))
        orbitals.append(
            {
                "cx": width * (0.25 + (0.5 * point["u"])),
                "cy": height * (0.25 + (0.5 * point["v"])),
                "radius": 18.0 + (point["w"] * max(width, height) * 0.12),
                "thickness": 1.0 + (2.0 * point["z"]),
                "alpha": 0.08 + (0.18 * min(1.0, orbital_energy / 4.0)),
            }
        )

    lineage = [event.lineage for event, _weight in weighted_events[-4:]]
    return VisualTapeSnapshot(
        tick=tick,
        entries=len(events),
        total_energy=total_energy,
        hue_shift=hue_shift,
        family_energy=family_energy,
        breath=breath,
        haze=haze,
        zoom=zoom,
        bloom=bloom,
        veil=veil,
        scanline_alpha=scanline_alpha,
        grain=grain,
        glitch=glitch,
        prism=prism,
        particles=particles,
        ripples=ripples,
        orbitals=orbitals,
        lineage=lineage,
    )
