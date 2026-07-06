from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_suite_trace(artifacts: list[dict[str, Any]], *, suite_id: str) -> dict[str, Any]:
    return {
        "suite_id": suite_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "scenario_count": len(artifacts),
        "artifacts": artifacts,
    }


def build_suite_summary(artifacts: list[dict[str, Any]], *, suite_id: str) -> dict[str, Any]:
    total_score = 0.0
    total_dimensions = 0
    passed = 0
    failed = 0
    failure_modes: Counter[str] = Counter()
    patch_targets: Counter[str] = Counter()

    for artifact in artifacts:
        summary = artifact.get("summary") or {}
        judge = artifact.get("judge") or {}
        if summary.get("hard_pass"):
            passed += 1
        else:
            failed += 1
        for tag in summary.get("failure_taxonomy") or []:
            failure_modes[str(tag)] += 1
        if isinstance(judge.get("overall_score"), (int, float)):
            total_score += float(judge["overall_score"])
            total_dimensions += 1
        if judge.get("recommended_patch_target"):
            patch_targets[str(judge["recommended_patch_target"])] += 1

    average_score = round(total_score / total_dimensions, 2) if total_dimensions else None
    return {
        "suite_id": suite_id,
        "scenario_count": len(artifacts),
        "passed": passed,
        "failed": failed,
        "average_judge_score": average_score,
        "top_failure_modes": failure_modes.most_common(8),
        "top_patch_targets": patch_targets.most_common(8),
    }


def render_summary_markdown(summary: dict[str, Any], artifacts: list[dict[str, Any]]) -> str:
    lines = [
        f"# OpenCloset Eval Summary - {summary['suite_id']}",
        "",
        f"- Scenarios: {summary['scenario_count']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Average judge score: {summary.get('average_judge_score')}",
        "",
        "## Scenarios",
        "",
        "| Scenario | Status | Hard Pass | Judge | Failure Category | Patch Target |",
        "|---|---|---:|---:|---|---|",
    ]
    for artifact in artifacts:
        scenario = artifact.get("scenario") or {}
        summary_row = artifact.get("summary") or {}
        judge = artifact.get("judge") or {}
        lines.append(
            f"| {scenario.get('id')} | {summary_row.get('status')} | "
            f"{'yes' if summary_row.get('hard_pass') else 'no'} | "
            f"{judge.get('overall_score', '')} | "
            f"{judge.get('failure_category', '')} | "
            f"{judge.get('recommended_patch_target', '')} |"
        )
    lines.extend(["", "## Top Recurring Failure Modes", ""])
    for label, count in summary.get("top_failure_modes") or []:
        lines.append(f"- `{label}`: {count}")
    lines.extend(["", "## Highest-Priority Harness Fixes", ""])
    for label, count in summary.get("top_patch_targets") or []:
        lines.append(f"- `{label}`: {count} scenario(s)")
    return "\n".join(lines).rstrip() + "\n"


def render_failures_markdown(artifacts: list[dict[str, Any]]) -> str:
    lines = ["# OpenCloset Eval Failures", ""]
    failed_artifacts = [artifact for artifact in artifacts if not (artifact.get("summary") or {}).get("hard_pass")]
    if not failed_artifacts:
        lines.append("No failing scenarios.")
        return "\n".join(lines) + "\n"
    for artifact in failed_artifacts:
        scenario = artifact.get("scenario") or {}
        summary = artifact.get("summary") or {}
        judge = artifact.get("judge") or {}
        lines.extend(
            [
                f"## {scenario.get('id')}",
                "",
                f"- Status: {summary.get('status')}",
                f"- Failure category: {judge.get('failure_category', 'unknown')}",
                f"- Likely root cause: {judge.get('likely_root_cause', 'unknown')}",
                f"- Recommended patch target: {judge.get('recommended_patch_target', 'unknown')}",
                f"- Minimal fix suggestion: {judge.get('minimal_fix_suggestion', 'unknown')}",
                f"- Final text preview: {summary.get('final_text_preview', '')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_suite_reports(
    *,
    reports_dir: str | Path,
    suite_id: str,
    artifacts: list[dict[str, Any]],
) -> dict[str, str]:
    root = Path(reports_dir).resolve()
    _ensure_dir(root)
    stamp = _utc_stamp()
    summary = build_suite_summary(artifacts, suite_id=suite_id)
    trace = build_suite_trace(artifacts, suite_id=suite_id)
    failures_md = render_failures_markdown(artifacts)
    summary_md = render_summary_markdown(summary, artifacts)

    summary_path = root / f"{stamp}_summary.md"
    trace_path = root / f"{stamp}_trace.json"
    failures_path = root / f"{stamp}_failures.md"

    summary_path.write_text(summary_md, encoding="utf-8")
    trace_path.write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
    failures_path.write_text(failures_md, encoding="utf-8")
    return {
        "summary": str(summary_path),
        "trace": str(trace_path),
        "failures": str(failures_path),
    }
