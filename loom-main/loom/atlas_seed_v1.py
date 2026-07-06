"""Seed atlas for baseline regional anchors."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .x_region_schema_v1 import XRegion, default_x_regions


@dataclass(frozen=True)
class SeedAtlasRegion:
    region_id: str
    label: str
    x_index: int
    anchors: tuple[str, ...]
    aliases: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_seed_atlas(regions: List[XRegion] | None = None) -> List[SeedAtlasRegion]:
    chosen = regions or default_x_regions()
    atlas: List[SeedAtlasRegion] = []
    for region in chosen:
        atlas.append(
            SeedAtlasRegion(
                region_id=region.id,
                label=region.label,
                x_index=region.index,
                anchors=tuple(region.keywords),
                aliases=tuple(region.aliases),
                notes=(f"x_index={region.index}", "seed-atlas-v1"),
            )
        )
    return atlas


def export_seed_tape(regions: List[SeedAtlasRegion] | None = None) -> List[Dict[str, Any]]:
    atlas = regions or build_seed_atlas()
    return [
        {
            "kind": "atlas_seed_region",
            "region_id": region.region_id,
            "label": region.label,
            "x_index": region.x_index,
            "anchors": list(region.anchors),
            "aliases": list(region.aliases),
            "notes": list(region.notes),
        }
        for region in atlas
    ]
