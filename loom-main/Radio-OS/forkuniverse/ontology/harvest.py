from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import requests

from .models import ConceptRecord

CONCEPTNET_API = "https://api.conceptnet.io"
FREE_DICTIONARY_API = "https://api.dictionaryapi.dev/api/v2/entries/en"

_RELATION_TO_BUCKET = {
    "Causes": "creates_events",
    "HasSubevent": "creates_events",
    "Desires": "intensifies_with",
    "MotivatedByGoal": "intensifies_with",
    "CausesDesire": "creates_predictions",
    "HasPrerequisite": "decays_with",
    "Antonym": "failure_modes",
    "RelatedTo": "tags",
}

_CATEGORY_HINTS = {
    "love": "relationship_force",
    "lust": "relationship_force",
    "death": "life_cycle",
    "grief": "desire_force",
    "debt": "obligation",
    "hunger": "scarcity_force",
    "illness": "body_state",
    "ambition": "status_force",
    "rivalry": "status_force",
    "rumor": "memory_force",
    "status": "status_force",
    "shame": "desire_force",
    "faith": "belief_force",
    "promise": "obligation",
    "secret": "memory_force",
    "contract": "obligation",
    "crime": "institutional_force",
    "inheritance": "resource_pressure",
    "migration": "life_cycle",
    "weather": "environmental_force",
    "betrayal": "relationship_force",
}


@dataclass
class HarvestSignals:
    word: str
    definitions: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    antonyms: List[str] = field(default_factory=list)
    causes: List[str] = field(default_factory=list)
    subevents: List[str] = field(default_factory=list)
    desires: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    used_for: List[str] = field(default_factory=list)

    def unique(self) -> "HarvestSignals":
        for attr in (
            "definitions",
            "examples",
            "synonyms",
            "antonyms",
            "causes",
            "subevents",
            "desires",
            "related",
            "locations",
            "used_for",
        ):
            values = getattr(self, attr)
            seen = set()
            unique_values = []
            for value in values:
                normalized = value.strip().lower()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                unique_values.append(value.strip())
            setattr(self, attr, unique_values)
        return self


def _safe_get(url: str, *, timeout: float = 10.0) -> Optional[Any]:
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


def fetch_dictionary_entries(word: str) -> Optional[Any]:
    return _safe_get(f"{FREE_DICTIONARY_API}/{word}")


def fetch_conceptnet_edges(word: str, *, limit: int = 50) -> Optional[Any]:
    return _safe_get(f"{CONCEPTNET_API}/c/en/{word}?limit={limit}")


def extract_dictionary_signals(word: str, payload: Any) -> HarvestSignals:
    signals = HarvestSignals(word=word)
    if not isinstance(payload, list):
        return signals

    for entry in payload:
        for meaning in entry.get("meanings", []):
            for definition in meaning.get("definitions", []):
                text = definition.get("definition")
                if text:
                    signals.definitions.append(text)
                example = definition.get("example")
                if example:
                    signals.examples.append(example)
                signals.synonyms.extend(definition.get("synonyms") or [])
                signals.antonyms.extend(definition.get("antonyms") or [])
            signals.synonyms.extend(meaning.get("synonyms") or [])
            signals.antonyms.extend(meaning.get("antonyms") or [])

    return signals.unique()


def _concept_label(node: Dict[str, Any]) -> Optional[str]:
    label = node.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    term = node.get("term")
    if isinstance(term, str) and term.startswith("/c/en/"):
        return term.split("/c/en/", 1)[1].replace("_", " ").strip()
    return None


def extract_conceptnet_signals(word: str, payload: Any) -> HarvestSignals:
    signals = HarvestSignals(word=word)
    if not isinstance(payload, dict):
        return signals

    for edge in payload.get("edges", []):
        rel = ((edge.get("rel") or {}).get("label") or "").strip()
        start = _concept_label(edge.get("start") or {})
        end = _concept_label(edge.get("end") or {})
        other = end if (start or "").lower() == word.lower() else start
        if not other:
            continue

        if rel == "Causes":
            signals.causes.append(other)
        elif rel == "HasSubevent":
            signals.subevents.append(other)
        elif rel in {"Desires", "MotivatedByGoal", "CausesDesire"}:
            signals.desires.append(other)
        elif rel == "RelatedTo":
            signals.related.append(other)
        elif rel == "AtLocation":
            signals.locations.append(other)
        elif rel == "UsedFor":
            signals.used_for.append(other)

    return signals.unique()


def merge_signals(*signal_sets: HarvestSignals) -> HarvestSignals:
    if not signal_sets:
        return HarvestSignals(word="")

    merged = HarvestSignals(word=signal_sets[0].word)
    for signals in signal_sets:
        for attr in (
            "definitions",
            "examples",
            "synonyms",
            "antonyms",
            "causes",
            "subevents",
            "desires",
            "related",
            "locations",
            "used_for",
        ):
            getattr(merged, attr).extend(getattr(signals, attr))
    return merged.unique()


def infer_category(word: str, signals: HarvestSignals) -> str:
    if word.lower() in _CATEGORY_HINTS:
        return _CATEGORY_HINTS[word.lower()]
    definition_blob = " ".join(signals.definitions).lower()
    if "emotion" in definition_blob or "feeling" in definition_blob:
        return "desire_force"
    if "duty" in definition_blob or "obligation" in definition_blob:
        return "obligation"
    if "disease" in definition_blob or "condition" in definition_blob:
        return "body_state"
    return "simulation_concept"


def _pick(items: Iterable[str], limit: int = 5) -> List[str]:
    out: List[str] = []
    for item in items:
        text = item.strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def concept_record_from_signals(word: str, signals: HarvestSignals) -> ConceptRecord:
    category = infer_category(word, signals)
    description = signals.definitions[0] if signals.definitions else f"{word.title()} as a ForkUniverse simulation concept."
    affects = _pick(signals.related + signals.synonyms)
    creates_events = _pick(signals.subevents + signals.causes)
    creates_threads = _pick(signals.subevents + signals.desires + signals.causes)
    creates_predictions = _pick(signals.causes + signals.desires + signals.related)
    decays_with = _pick(signals.antonyms + signals.used_for)
    intensifies_with = _pick(signals.desires + signals.related + signals.locations)
    resolution_modes = _pick(signals.antonyms + signals.used_for + signals.related)
    failure_modes = _pick(signals.antonyms + signals.causes)
    radio_surfaces = _pick(["open_thread", "prediction", "breaking_development"] + signals.related)
    tags = _pick(signals.synonyms + signals.related + signals.locations)

    return ConceptRecord(
        concept_id=word.lower().replace(" ", "_"),
        label=word.title(),
        category=category,
        description=description[:500],
        affects=affects,
        creates_events=creates_events,
        creates_threads=creates_threads,
        creates_predictions=creates_predictions,
        decays_with=decays_with,
        intensifies_with=intensifies_with,
        resolution_modes=resolution_modes,
        failure_modes=failure_modes,
        radio_surfaces=radio_surfaces,
        default_coefficients={
            "thread_heat_bias": 0.2 if creates_threads else 0.0,
            "prediction_spawn_bias": 0.15 if creates_predictions else 0.0,
        },
        tags=tags,
    )


def harvest_concept(word: str) -> ConceptRecord:
    dictionary_payload = fetch_dictionary_entries(word)
    conceptnet_payload = fetch_conceptnet_edges(word)
    dictionary_signals = extract_dictionary_signals(word, dictionary_payload)
    conceptnet_signals = extract_conceptnet_signals(word, conceptnet_payload)
    merged = merge_signals(dictionary_signals, conceptnet_signals)
    return concept_record_from_signals(word, merged)
