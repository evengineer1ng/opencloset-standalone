import json
from pathlib import Path

from forkuniverse.ontology.harvest import (
    concept_record_from_signals,
    extract_conceptnet_signals,
    extract_dictionary_signals,
    merge_signals,
)
from forkuniverse.ontology.models import ConceptRecord, ConceptRegistry
from forkuniverse.ontology.registry import merge_registries, write_concept_registry
from tools.forkuniverse_harvest import load_seed_words


def test_extract_dictionary_signals_collects_definitions_and_synonyms():
    payload = [
        {
            "meanings": [
                {
                    "partOfSpeech": "noun",
                    "definitions": [
                        {
                            "definition": "A state of owing money.",
                            "example": "Debt kept him awake at night.",
                            "synonyms": ["liability"],
                            "antonyms": ["solvency"],
                        }
                    ],
                    "synonyms": ["obligation"],
                    "antonyms": [],
                }
            ]
        }
    ]

    signals = extract_dictionary_signals("debt", payload)

    assert "A state of owing money." in signals.definitions
    assert "liability" in signals.synonyms
    assert "obligation" in signals.synonyms
    assert "solvency" in signals.antonyms


def test_extract_conceptnet_signals_collects_relations():
    payload = {
        "edges": [
            {
                "rel": {"label": "Causes"},
                "start": {"label": "debt"},
                "end": {"label": "stress"},
            },
            {
                "rel": {"label": "HasSubevent"},
                "start": {"label": "debt"},
                "end": {"label": "repayment"},
            },
            {
                "rel": {"label": "Desires"},
                "start": {"label": "debt"},
                "end": {"label": "relief"},
            },
        ]
    }

    signals = extract_conceptnet_signals("debt", payload)

    assert "stress" in signals.causes
    assert "repayment" in signals.subevents
    assert "relief" in signals.desires


def test_concept_record_from_signals_builds_useful_stub():
    dictionary_signals = extract_dictionary_signals(
        "love",
        [
            {
                "meanings": [
                    {
                        "definitions": [
                            {
                                "definition": "An intense feeling of deep affection.",
                                "synonyms": ["adoration"],
                            }
                        ],
                        "synonyms": ["attachment"],
                    }
                ]
            }
        ],
    )
    conceptnet_signals = extract_conceptnet_signals(
        "love",
        {
            "edges": [
                {
                    "rel": {"label": "Causes"},
                    "start": {"label": "love"},
                    "end": {"label": "sacrifice"},
                },
                {
                    "rel": {"label": "Desires"},
                    "start": {"label": "love"},
                    "end": {"label": "proximity"},
                },
            ]
        },
    )

    merged = merge_signals(dictionary_signals, conceptnet_signals)
    concept = concept_record_from_signals("love", merged)

    assert concept.concept_id == "love"
    assert concept.category == "relationship_force"
    assert "sacrifice" in concept.creates_events
    assert "proximity" in concept.intensifies_with


def test_merge_registries_adds_new_concepts_without_overwriting():
    base = ConceptRegistry(
        registry_id="test_registry",
        concepts=[
            ConceptRecord(
                concept_id="love",
                label="Love",
                category="relationship_force",
                description="Base concept",
                affects=[],
                creates_events=[],
                creates_threads=[],
                creates_predictions=[],
                decays_with=[],
                intensifies_with=[],
                resolution_modes=[],
                failure_modes=[],
                radio_surfaces=[],
                default_coefficients={},
                tags=[],
            )
        ],
    )
    new_concept = ConceptRecord(
        concept_id="debt",
        label="Debt",
        category="obligation",
        description="Debt concept",
        affects=[],
        creates_events=[],
        creates_threads=[],
        creates_predictions=[],
        decays_with=[],
        intensifies_with=[],
        resolution_modes=[],
        failure_modes=[],
        radio_surfaces=[],
        default_coefficients={},
        tags=[],
    )
    replacement_love = ConceptRecord(
        concept_id="love",
        label="Love",
        category="relationship_force",
        description="Replacement concept",
        affects=[],
        creates_events=[],
        creates_threads=[],
        creates_predictions=[],
        decays_with=[],
        intensifies_with=[],
        resolution_modes=[],
        failure_modes=[],
        radio_surfaces=[],
        default_coefficients={},
        tags=[],
    )

    merged = merge_registries(base, [new_concept, replacement_love], replace_existing=False)
    concept_map = {concept.concept_id: concept for concept in merged.concepts}

    assert concept_map["love"].description == "Base concept"
    assert concept_map["debt"].description == "Debt concept"


def test_load_seed_words_skips_comments_and_blanks(tmp_path: Path):
    seed_file = tmp_path / "concepts.txt"
    seed_file.write_text("# comment\nlove\n\n debt \n", encoding="utf-8")

    words = load_seed_words(seed_file)

    assert words == ["love", "debt"]


def test_write_concept_registry_round_trips_json(tmp_path: Path):
    registry = ConceptRegistry(
        registry_id="roundtrip_registry",
        concepts=[
            ConceptRecord(
                concept_id="rumor",
                label="Rumor",
                category="memory_force",
                description="Rumor concept",
                affects=[],
                creates_events=[],
                creates_threads=[],
                creates_predictions=[],
                decays_with=[],
                intensifies_with=[],
                resolution_modes=[],
                failure_modes=[],
                radio_surfaces=[],
                default_coefficients={},
                tags=[],
            )
        ],
    )
    output = tmp_path / "registry.json"

    write_concept_registry(registry, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["registry_id"] == "roundtrip_registry"
    assert payload["concepts"][0]["concept_id"] == "rumor"
