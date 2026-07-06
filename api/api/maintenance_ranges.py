from __future__ import annotations

from typing import Any

from api.api.maintenance_artifacts import (
    MICRO_SUMMARY_ARTIFACT,
    SEGMENT_SUMMARY_ARTIFACT,
    normalize_compaction_ranges,
)


def next_segment_range(
    segment_artifacts: list[dict[str, Any]],
    target_end_position: int,
    *,
    summary_message_limit: int,
) -> dict[str, int] | None:
    if target_end_position < 1:
        return None

    prefix_end = 0
    for artifact in sorted(
        segment_artifacts,
        key=lambda item: (int(item.get("start_position") or 0), int(item.get("end_position") or 0)),
    ):
        start_position = int(artifact.get("start_position") or 0)
        end_position = int(artifact.get("end_position") or 0)
        if start_position != prefix_end + 1 or end_position < start_position:
            break
        prefix_end = max(prefix_end, end_position)

    next_start = prefix_end + 1
    if next_start > target_end_position:
        return None

    next_end = min(target_end_position, next_start + summary_message_limit - 1)
    return {
        "start_position": next_start,
        "end_position": next_end,
    }


def build_compaction_ranges(
    summary_artifact: dict[str, Any] | None,
    segment_artifacts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    for segment_artifact in segment_artifacts or []:
        if segment_artifact.get("start_position") is None or segment_artifact.get("end_position") is None:
            continue
        ranges.append(
            {
                "artifact_type": SEGMENT_SUMMARY_ARTIFACT,
                "artifact_id": segment_artifact.get("id"),
                "start_position": int(segment_artifact["start_position"]),
                "end_position": int(segment_artifact["end_position"]),
            }
        )
    if summary_artifact and summary_artifact.get("start_position") is not None and summary_artifact.get("end_position") is not None:
        ranges.append(
            {
                "artifact_type": MICRO_SUMMARY_ARTIFACT,
                "artifact_id": summary_artifact.get("id"),
                "start_position": int(summary_artifact["start_position"]),
                "end_position": int(summary_artifact["end_position"]),
            }
        )
    return normalize_compaction_ranges(ranges)


def compaction_marker_matches(marker: dict[str, Any] | None, desired_ranges: list[dict[str, Any]]) -> bool:
    if not marker:
        return False
    metadata = marker.get("metadata") or {}
    current_ranges = normalize_compaction_ranges(metadata.get("covered_ranges") or [])
    return current_ranges == desired_ranges


def build_archive_safe_ranges(covered_ranges: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    archive_ranges: list[dict[str, Any]] = []
    for item in covered_ranges or []:
        source_ranges = item.get("source_ranges") or []
        if not source_ranges:
            continue
        if any(source_range.get("artifact_type") != SEGMENT_SUMMARY_ARTIFACT for source_range in source_ranges):
            continue
        archive_ranges.append(
            {
                "artifact_type": SEGMENT_SUMMARY_ARTIFACT,
                "start_position": int(item["start_position"]),
                "end_position": int(item["end_position"]),
                "source_ranges": [dict(source_range) for source_range in source_ranges],
            }
        )
    return normalize_compaction_ranges(archive_ranges)


def build_archive_candidate_content(archive_ranges: list[dict[str, Any]]) -> str:
    lines = ["Transcript archive candidate:"]
    for item in archive_ranges:
        lines.append(
            f"- Messages {item['start_position']}-{item['end_position']} are backed only by segment summaries"
        )
    return "\n".join(lines)
