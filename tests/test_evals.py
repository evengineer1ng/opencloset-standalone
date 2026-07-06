from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import requests

from evals.loader import find_scenario_paths_by_ids, load_scenario, load_suite
from evals.reporting import write_suite_reports
from evals.schema import EvalTurnResult
from evals.runner import ApiHarnessEvalRunner


def test_load_scenario_supports_richer_e2e_fields():
    repo_root = Path(__file__).resolve().parents[1]
    scenario = load_scenario(repo_root / "evals" / "scenarios" / "code_add_small_feature.yaml", repo_root=str(repo_root))

    assert scenario.id == "code_add_small_feature"
    assert scenario.setup.use_temp_workspace is True
    assert scenario.expected_behavior
    assert scenario.required_observations
    assert scenario.scoring_rubric
    assert scenario.turns[0].content


def test_load_suite_and_resolve_scenarios():
    repo_root = Path(__file__).resolve().parents[1]
    suite = load_suite(repo_root / "evals" / "suites" / "e2e_basic.yaml", repo_root=str(repo_root))
    paths = find_scenario_paths_by_ids(suite.scenario_ids, repo_root=str(repo_root))

    assert suite.id == "e2e_basic"
    assert len(suite.scenario_ids) == 12
    assert len(paths) == 12


def test_load_coding_readiness_suite_and_resolve_scenarios():
    repo_root = Path(__file__).resolve().parents[1]
    suite = load_suite(repo_root / "evals" / "suites" / "coding_requests_ready.yaml", repo_root=str(repo_root))
    paths = find_scenario_paths_by_ids(suite.scenario_ids, repo_root=str(repo_root))

    assert suite.id == "coding_requests_ready"
    assert suite.scenario_ids == [
        "multi_turn_coding_patch",
        "code_add_small_feature",
        "coding_request_perf",
    ]
    assert len(paths) == 3


def test_write_suite_reports_creates_summary_trace_and_failures(tmp_path: Path):
    artifacts = [
        {
            "scenario": {"id": "simple_question"},
            "summary": {"status": "succeeded", "hard_pass": True, "failure_taxonomy": []},
            "judge": {"overall_score": 5, "recommended_patch_target": ""},
        },
        {
            "scenario": {"id": "debug_existing_bug_no_rewrite"},
            "summary": {
                "status": "failed",
                "hard_pass": False,
                "failure_taxonomy": ["rule_check_failed"],
                "final_text_preview": "failed preview",
            },
            "judge": {
                "overall_score": 2,
                "failure_category": "tool_discipline",
                "likely_root_cause": "did not inspect file first",
                "recommended_patch_target": "api/agent/runner.py",
                "minimal_fix_suggestion": "bias prompt toward read-before-edit",
            },
        },
    ]

    paths = write_suite_reports(reports_dir=tmp_path, suite_id="e2e_basic", artifacts=artifacts)

    summary_text = Path(paths["summary"]).read_text(encoding="utf-8")
    failures_text = Path(paths["failures"]).read_text(encoding="utf-8")
    trace_payload = json.loads(Path(paths["trace"]).read_text(encoding="utf-8"))

    assert "OpenCloset Eval Summary - e2e_basic" in summary_text
    assert "debug_existing_bug_no_rewrite" in failures_text
    assert trace_payload["suite_id"] == "e2e_basic"
    assert trace_payload["scenario_count"] == 2


def test_eval_runner_recovers_terminal_run_after_execute_timeout(tmp_path: Path):
    runner = ApiHarnessEvalRunner(
        api_base_url="http://127.0.0.1:5000/api",
        repo_root=str(tmp_path),
        out_dir=str(tmp_path / "runs"),
    )

    post_mock = Mock(side_effect=requests.ReadTimeout("execute timed out"))
    get_mock = Mock(
        side_effect=[
            {"run_id": "r1", "status": "running"},
            {"run_id": "r1", "status": "succeeded", "final_text": "done", "tool_results": []},
        ]
    )
    runner._post = post_mock  # type: ignore[method-assign]
    runner._get = get_mock  # type: ignore[method-assign]

    payload = runner._execute_run_with_recovery("s1", "r1")

    assert payload["status"] == "succeeded"
    assert payload["final_text"] == "done"
    assert get_mock.call_count == 2


def test_eval_runner_synthesizes_failed_payload_when_run_never_resolves(tmp_path: Path):
    runner = ApiHarnessEvalRunner(
        api_base_url="http://127.0.0.1:5000/api",
        repo_root=str(tmp_path),
        out_dir=str(tmp_path / "runs"),
    )

    post_mock = Mock(side_effect=[requests.ReadTimeout("execute timed out"), {"status": "interrupt_requested"}])
    get_mock = Mock(
        side_effect=lambda *_args, **_kwargs: {
            "run_id": "r1",
            "session_id": "s1",
            "status": "running",
            "input_tokens": 11,
            "output_tokens": 3,
        }
    )
    runner._post = post_mock  # type: ignore[method-assign]
    runner._get = get_mock  # type: ignore[method-assign]

    payload = runner._wait_for_terminal_run(
        "s1",
        "r1",
        reason="execute_read_timeout",
        timeout_seconds=0.0,
        poll_interval_seconds=0.0,
    )

    assert payload["status"] == "failed"
    assert payload["finish_reason"] == "eval_recovery_timeout"
    assert "Interrupt was requested" in payload["error"]
    assert payload["input_tokens"] == 11
    assert payload["output_tokens"] == 3


def test_extract_turn_observations_supports_stream_tool_call_variants(tmp_path: Path):
    runner = ApiHarnessEvalRunner(
        api_base_url="http://127.0.0.1:5000/api",
        repo_root=str(tmp_path),
        out_dir=str(tmp_path / "runs"),
    )

    observations = runner._extract_turn_observations(
        [
            {"type": "tool_called", "data": {"tool_id": "read", "input": {"path": str(tmp_path / "task_app.py")}}},
            {"type": "stream.tool_call", "data": {"tool_name": "edit", "input": {"path": str(tmp_path / "task_app.py")}}},
            {"type": "stream.tool_call", "data": {"tool_name": "exec", "input": {"command": "python -m pytest"}}},
        ],
        [],
        temp_workspace=str(tmp_path),
    )

    assert str(tmp_path / "task_app.py") in observations["files_read"]
    assert str(tmp_path / "task_app.py") in observations["files_written"]
    assert "python -m pytest" in observations["commands"]


def test_extract_turn_observations_recovers_llamacpp_xml_fallback_and_edit_results(tmp_path: Path):
    runner = ApiHarnessEvalRunner(
        api_base_url="http://127.0.0.1:5000/api",
        repo_root=str(tmp_path),
        out_dir=str(tmp_path / "runs"),
    )

    target = str(tmp_path / "task_app.py")
    observations = runner._extract_turn_observations(
        [
            {"type": "assistant_delta", "data": {"text": f"<read>{target}</read>"}},
            {
                "type": "assistant_delta",
                "data": {
                    "text": (
                        f'<tool_call name="edit">{{"path":"{target.replace("\\", "\\\\")}",'
                        '"edits":[{"oldText":"x","newText":"y"}]}}</tool_call>'
                    )
                },
            },
            {"type": "assistant_delta", "data": {"text": "<exec>python -m pytest</exec>"}},
        ],
        [
            {
                "tool_name": "edit",
                "content": f"+1 -1 task_app.py\nApplied 1 edit(s) to {target}",
            }
        ],
        temp_workspace=str(tmp_path),
    )

    assert target in observations["files_read"]
    assert target in observations["files_written"]
    assert "python -m pytest" in observations["commands"]
    assert observations["touched_temp_workspace"] is True


def test_run_checks_supports_observed_command_contains(tmp_path: Path):
    runner = ApiHarnessEvalRunner(
        api_base_url="http://127.0.0.1:5000/api",
        repo_root=str(tmp_path),
        out_dir=str(tmp_path / "runs"),
    )

    scenario = load_scenario(
        Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "coding_request_perf.yaml",
        repo_root=str(Path(__file__).resolve().parents[1]),
    )

    checks = runner._run_checks(
        scenario,
        [
            EvalTurnResult(
                turn_index=1,
                role="user",
                content="",
                run_id="r1",
                message_id="m1",
                status="succeeded",
                finish_reason="completed",
                final_text="helper and tests are done",
                transient_text="",
                error="",
                latency_ms=1200,
                input_tokens=0,
                output_tokens=0,
                observations={
                    "files_read": [
                        str(tmp_path / "task_app.py"),
                        str(tmp_path / "tests" / "test_task_app.py"),
                    ],
                    "files_written": [str(tmp_path / "task_app.py")],
                    "commands": ["python -m pytest -q"],
                    "runtime_event_types": [],
                },
            )
        ],
        {str(tmp_path / "task_app.py"): None},
        runtime_vars={"repo_root": str(tmp_path), "temp_root": str(tmp_path), "temp_workspace": str(tmp_path)},
    )

    command_checks = [item for item in checks if item.type == "observed_command_contains"]
    assert command_checks
    assert command_checks[0].passed is True
