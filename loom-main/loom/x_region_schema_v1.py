"""Stable X-axis semantic region schema for coordinate-mapped loompixels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class XRegion:
    id: str
    label: str
    index: int
    keywords: tuple[str, ...]
    aliases: tuple[str, ...] = ()


def default_x_regions() -> List[XRegion]:
    return [
        XRegion("identity", "Identity", 0, ("person", "people", "name", "profile", "reputation", "biography", "individual"), ("self", "character")),
        XRegion("place", "Place", 1, ("city", "country", "island", "state", "barbados", "river", "mountain", "geography", "territory", "province"), ("location", "geo")),
        XRegion("organism", "Organism", 2, ("cat", "dog", "animal", "plant", "health", "species", "taxa", "organism", "disease"), ("life", "biology")),
        XRegion("event", "Event", 3, ("war", "election", "festival", "ceremony", "incident", "finals", "history", "timeline"), ("history",)),
        XRegion("competition", "Competition", 4, ("league", "tournament", "season", "race", "match", "nba", "playoff", "sport", "athlete"), ("sports",)),
        XRegion("governance", "Governance", 5, ("government", "law", "policy", "president", "minister", "court", "military", "administration"), ("politics",)),
        XRegion("artifact", "Artifact", 6, ("engine", "device", "machine", "tool", "weapon", "book", "technology", "computing", "transportation", "communications"), ("technology",)),
        XRegion("science", "Science", 7, ("physics", "chemistry", "math", "equation", "theory", "experiment", "taxonomy", "spectrum"), ("research",)),
        XRegion("culture", "Culture", 8, ("music", "film", "art", "poem", "religion", "myth", "language", "custom"), ("media",)),
        XRegion("survival", "Survival", 9, ("food", "disease", "shelter", "medicine", "safety", "climate", "healthcare", "nutrition"), ("risk",)),
        XRegion("exploration", "Exploration", 10, ("voyage", "mission", "expedition", "space", "discovery", "oracle", "journey"), ("travel",)),
        XRegion("relation", "Relation", 11, ("family", "marriage", "alliance", "network", "union", "friendship", "kinship"), ("social",)),
        XRegion("control", "Control", 12, ("system", "command", "management", "driver", "controller", "regulation", "issues"), ("operations",)),
        XRegion("commerce", "Commerce", 13, ("trade", "market", "company", "price", "economy", "finance", "industry"), ("business",)),
        XRegion("media", "Media", 14, ("broadcast", "newspaper", "website", "television", "radio", "publication", "journal"), ("press",)),
        XRegion("context", "Context", 15, ("overview", "background", "index", "list", "disambiguation", "context", "outline", "stub"), ("meta",)),
    ]


def region_map(regions: List[XRegion] | None = None) -> Dict[str, XRegion]:
    chosen = regions or default_x_regions()
    mapping: Dict[str, XRegion] = {}
    for region in chosen:
        mapping[region.id] = region
        mapping[region.label.lower()] = region
        for alias in region.aliases:
            mapping[alias.lower()] = region
    return mapping


def region_ids(regions: List[XRegion] | None = None) -> List[str]:
    return [region.id for region in (regions or default_x_regions())]
