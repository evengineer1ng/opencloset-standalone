"""Emit a concept-grading tape for an external LLM baseline pass."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from .atlas_seed_v1 import SeedAtlasRegion, build_seed_atlas
from .thesaurus_bridge_v1 import score_with_thesaurus

# When the thesaurus gives a concept no real signal, offer a DIVERSE menu instead of the first
# regions by index (which always led with identity and stacked abstract titles into it).
# 'context' is the legitimate catch-all for meta/abstract/list/disambiguation titles; identity is
# placed LAST so it is never the lazy default — only chosen when it genuinely fits a person/name.
DIVERSE_FALLBACK = (
    "context", "science", "culture", "artifact", "governance", "event", "place",
    "relation", "commerce", "exploration", "competition", "media", "control",
    "organism", "survival", "identity",
)


def _tiebreak(concept_id: str, region_id: str) -> int:
    """Deterministic, unbiased tie-break for equal-scoring regions (not index order)."""
    return int(hashlib.sha1((concept_id + "|" + region_id).encode("utf-8")).hexdigest()[:8], 16)


@dataclass(frozen=True)
class LlmBaselineTapeRow:
    concept_id: str
    title: str
    namespace: str
    candidate_regions: List[str]
    anchor_context: Dict[str, List[str]]
    prompt_tags: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "title": self.title,
            "namespace": self.namespace,
            "candidate_regions": list(self.candidate_regions),
            "anchor_context": {key: list(value) for key, value in self.anchor_context.items()},
            "prompt_tags": list(self.prompt_tags),
        }


def build_llm_baseline_tape(
    source_rows: Iterable[Dict[str, Any]],
    *,
    atlas: List[SeedAtlasRegion] | None = None,
    top_n: int = 6,
) -> List[Dict[str, Any]]:
    chosen = atlas or build_seed_atlas()
    region_by_id = {region.region_id: region for region in chosen}
    all_ids = list(region_by_id.keys())
    rows: List[Dict[str, Any]] = []
    for source_row in source_rows:
        title = str(source_row.get("title", ""))
        namespace = str(source_row.get("namespace", ""))
        concept_id = str(source_row.get("id") or source_row.get("normalized_title") or title)
        bridge = score_with_thesaurus(title + " " + namespace, atlas=chosen)
        scores = bridge.region_scores
        # rank by real signal, with a deterministic UNBIASED tie-break (not -x_index, which
        # always favored identity@0 and stacked abstract titles into it).
        ranked = sorted(
            all_ids,
            key=lambda rid: (scores.get(rid, 0.0), _tiebreak(concept_id, rid)),
            reverse=True,
        )
        candidate_regions = [rid for rid in ranked if scores.get(rid, 0.0) > 0.0][:top_n]
        # weak/no signal -> pad from the diverse menu so the model gets a real choice
        for rid in DIVERSE_FALLBACK:
            if len(candidate_regions) >= top_n:
                break
            if rid not in candidate_regions and rid in region_by_id:
                candidate_regions.append(rid)
        # guarantee the legitimate catch-all is always available
        if "context" in region_by_id and "context" not in candidate_regions:
            candidate_regions[-1] = "context"
        anchor_context = {
            rid: list(region_by_id[rid].anchors[:5])
            for rid in candidate_regions if rid in region_by_id
        }
        row = LlmBaselineTapeRow(
            concept_id=concept_id,
            title=title,
            namespace=namespace,
            candidate_regions=candidate_regions,
            anchor_context=anchor_context,
            prompt_tags=list(source_row.get("tags", []))[:8],
        )
        rows.append(
            {
                "kind": "baseline_grading_request",
                "instruction": "Grade candidate semantic regions for this concept. Return region_scores, primary_region, optional secondary_regions, and short reason tags only.",
                "payload": row.to_dict(),
            }
        )
    return rows
