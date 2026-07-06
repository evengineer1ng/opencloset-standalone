"""Render coordinate records into a deterministic loom-pixel plot plan."""

from __future__ import annotations

import math
import zlib
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List

from .coordinate_record_v1 import CoordinateRecord


@dataclass(frozen=True)
class LoomPixelCell:
    concept_id: str
    x_region: str
    y_overlay: str
    plot_x: int
    plot_y: int
    value: float
    color_seed: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _stable_seed(text: str) -> int:
    return zlib.crc32(str(text or "").encode("utf-8")) & 0xFFFFFFFF


def render_overlay_cells(
    records: Iterable[CoordinateRecord],
    *,
    overlay_id: str,
    region_count: int,
    width: int = 1024,
    height: int = 1024,
) -> List[LoomPixelCell]:
    chosen = [record for record in records if record.y_overlay == overlay_id]
    if not chosen:
        return []
    band_width = max(1, width // max(1, region_count))
    cells: List[LoomPixelCell] = []
    for record in sorted(chosen, key=lambda item: (item.x_index, item.concept_id)):
        seed = _stable_seed(record.concept_id + "|" + record.y_overlay)
        local = seed % max(1, band_width - 1)
        x = record.x_index * band_width + local
        y = max(0, min(height - 1, int(round((1.0 - record.value) * (height - 1)))))
        cells.append(
            LoomPixelCell(
                concept_id=record.concept_id,
                x_region=record.x_region,
                y_overlay=record.y_overlay,
                plot_x=x,
                plot_y=y,
                value=record.value,
                color_seed=seed,
            )
        )
    return cells
