"""Populate the first baseline map from Wikipedia index rows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .atlas_seed_v1 import SeedAtlasRegion, build_seed_atlas
from .baseline_prior_generator_v1 import BaselinePrior, build_baseline_prior
from .llm_baseline_tape_v1 import build_llm_baseline_tape
from .loom_pixel_render_v1 import LoomPixelCell, render_overlay_cells
from .placement_receipt_v1 import PlacementReceipt
from .placement_solver_v1 import PlacementResult, solve_placement
from .wikipedia_index_loombit import infer_snapshot_from_path, iter_index_rows
from .x_region_schema_v1 import XRegion, default_x_regions
from .y_overlay_schema_v1 import YOverlay, default_y_overlays


@dataclass(frozen=True)
class BaselinePopulationItem:
    source_row: Dict[str, Any]
    llm_tape_row: Dict[str, Any]
    llm_label: Dict[str, Any]
    prior: BaselinePrior
    placement: PlacementResult


@dataclass(frozen=True)
class BaselinePopulationBatch:
    snapshot: str
    source_rows: List[Dict[str, Any]]
    llm_tape: List[Dict[str, Any]]
    items: List[BaselinePopulationItem]
    overlay_cells: Dict[str, List[LoomPixelCell]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot": self.snapshot,
            "source_rows": list(self.source_rows),
            "llm_tape": list(self.llm_tape),
            "items": [
                {
                    "source_row": item.source_row,
                    "llm_tape_row": item.llm_tape_row,
                    "llm_label": item.llm_label,
                    "prior": {
                        "concept_id": item.prior.concept_id,
                        "title": item.prior.title,
                        "primary_region": item.prior.primary_region,
                        "region_scores": dict(item.prior.region_scores),
                        "llm_weight": item.prior.llm_weight,
                        "dictionary_weight": item.prior.dictionary_weight,
                        "notes": list(item.prior.notes),
                    },
                    "placement_receipt": item.placement.receipt.to_dict(),
                    "record_count": len(item.placement.records),
                }
                for item in self.items
            ],
            "overlay_cells": {
                overlay_id: [cell.to_dict() for cell in cells]
                for overlay_id, cells in self.overlay_cells.items()
            },
        }


def _default_llm_label(tape_row: Dict[str, Any], regions: List[XRegion]) -> Dict[str, Any]:
    payload = tape_row.get("payload", {})
    candidates = list(payload.get("candidate_regions", []))
    primary = candidates[0] if candidates else regions[0].id
    scores = {region.id: 0.0 for region in regions}
    if primary in scores:
        scores[primary] = 1.0
    return {
        "primary_region": primary,
        "region_scores": scores,
        "secondary_regions": candidates[1:3],
        "reason_tags": list(payload.get("prompt_tags", []))[:4],
        "confidence": 0.5,
    }


def normalize_llm_labels(
    llm_labels: Dict[str, Dict[str, Any]] | None,
    llm_tape: List[Dict[str, Any]],
    *,
    regions: List[XRegion] | None = None,
) -> Dict[str, Dict[str, Any]]:
    chosen_regions = regions or default_x_regions()
    provided = llm_labels or {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for tape_row in llm_tape:
        payload = tape_row.get("payload", {})
        concept_id = str(payload.get("concept_id", ""))
        label = provided.get(concept_id)
        normalized[concept_id] = label if isinstance(label, dict) else _default_llm_label(tape_row, chosen_regions)
    return normalized


def populate_baseline_from_rows(
    source_rows: Iterable[Dict[str, Any]],
    *,
    llm_labels: Dict[str, Dict[str, Any]] | None = None,
    atlas: List[SeedAtlasRegion] | None = None,
    regions: List[XRegion] | None = None,
    overlays: List[YOverlay] | None = None,
    dictionary_weight: float = 0.33,
    thesaurus_weight: float = 0.33,
    llm_weight: float = 0.34,
    width: int = 1024,
    height: int = 1024,
) -> BaselinePopulationBatch:
    chosen_rows = list(source_rows)
    chosen_regions = regions or default_x_regions()
    chosen_atlas = atlas or build_seed_atlas(chosen_regions)
    chosen_overlays = overlays or default_y_overlays()
    llm_tape = build_llm_baseline_tape(chosen_rows, atlas=chosen_atlas)
    normalized_labels = normalize_llm_labels(llm_labels, llm_tape, regions=chosen_regions)
    items: List[BaselinePopulationItem] = []
    all_records = []
    for source_row, tape_row in zip(chosen_rows, llm_tape):
        concept_id = str(tape_row.get("payload", {}).get("concept_id", ""))
        label = normalized_labels[concept_id]
        prior = build_baseline_prior(
            source_row,
            llm_prior=label,
            llm_weight=llm_weight,
            dictionary_weight=dictionary_weight,
            thesaurus_weight=thesaurus_weight,
            regions=chosen_regions,
            atlas=chosen_atlas,
        )
        placement = solve_placement(source_row, prior, regions=chosen_regions, overlays=chosen_overlays)
        items.append(
            BaselinePopulationItem(
                source_row=source_row,
                llm_tape_row=tape_row,
                llm_label=label,
                prior=prior,
                placement=placement,
            )
        )
        all_records.extend(placement.records)
    overlay_cells = {
        overlay.id: render_overlay_cells(
            all_records,
            overlay_id=overlay.id,
            region_count=len(chosen_regions),
            width=width,
            height=height,
        )
        for overlay in chosen_overlays
    }
    snapshot = str(chosen_rows[0].get("source_snapshot", "")) if chosen_rows else ""
    return BaselinePopulationBatch(
        snapshot=snapshot,
        source_rows=chosen_rows,
        llm_tape=llm_tape,
        items=items,
        overlay_cells=overlay_cells,
    )


def populate_baseline_from_wikipedia_index(
    source_path: str | Path,
    *,
    max_entries: int = 128,
    llm_labels: Dict[str, Dict[str, Any]] | None = None,
    atlas: List[SeedAtlasRegion] | None = None,
    regions: List[XRegion] | None = None,
    overlays: List[YOverlay] | None = None,
    dictionary_weight: float = 0.33,
    thesaurus_weight: float = 0.33,
    llm_weight: float = 0.34,
    width: int = 1024,
    height: int = 1024,
) -> BaselinePopulationBatch:
    snapshot = infer_snapshot_from_path(source_path)
    rows = list(iter_index_rows(source_path, snapshot=snapshot, max_entries=max_entries))
    return populate_baseline_from_rows(
        rows,
        llm_labels=llm_labels,
        atlas=atlas,
        regions=regions,
        overlays=overlays,
        dictionary_weight=dictionary_weight,
        thesaurus_weight=thesaurus_weight,
        llm_weight=llm_weight,
        width=width,
        height=height,
    )
