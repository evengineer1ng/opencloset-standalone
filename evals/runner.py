from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any
import uuid

import requests
from requests import ReadTimeout

from evals import SCHEMA_VERSION
from evals.schema import EvalCheckResult, EvalRunArtifact, EvalScenario, EvalTurnResult, dataclass_to_dict


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned or "eval"


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False), encoding="utf-8")


def _json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


_XML_TOOL_CALL_RE = re.compile(
    r"<tool_call\s+name=\"(?P<name>[a-zA-Z0-9_:-]+)\">\s*(?P<body>\{.*?\})\s*</tool_call>",
    re.DOTALL,
)
_XML_PAIRED_TOOL_RE = re.compile(
    r"<(?P<name>[a-zA-Z0-9_:-]+)>\s*(?P<body>.*?)\s*</(?P=name)>",
    re.DOTALL,
)
_EDIT_RESULT_PATH_RE = re.compile(r"Applied \d+ edit\(s\) to (?P<path>.+)$", re.MULTILINE)
_WRITE_RESULT_PATH_RE = re.compile(r"^Wrote file:\s*(?P<path>.+)$", re.MULTILINE)


def _run_shell_check(command: str, *, workdir: str | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output.strip()


def _git_commit(repo_root: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        return (completed.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _deep_expand_runtime_vars(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for key, replacement in variables.items():
            result = result.replace(f"${{{key}}}", replacement)
        return result
    if isinstance(value, list):
        return [_deep_expand_runtime_vars(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _deep_expand_runtime_vars(item, variables) for key, item in value.items()}
    return value


class _SseCollector:
    def __init__(self, api_base_url: str, session_id: str, run_id: str, *, enabled: bool = True) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.session_id = session_id
        self.run_id = run_id
        self.enabled = enabled
        self.events: list[dict[str, Any]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._error: str | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, name=f"eval-sse-{self.run_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        url = f"{self.api_base_url}/sessions/{self.session_id}/runs/{self.run_id}/stream"
        try:
            with requests.get(url, stream=True, timeout=(5, 300)) as response:
                response.raise_for_status()
                event_type = "message"
                data_lines: list[str] = []
                for raw_line in response.iter_lines(decode_unicode=True):
                    if self._stop.is_set():
                        break
                    if raw_line is None:
                        continue
                    line = str(raw_line)
                    if not line:
                        if data_lines:
                            data_text = "\n".join(data_lines)
                            try:
                                data = json.loads(data_text)
                            except json.JSONDecodeError:
                                data = {"raw": data_text}
                            self.events.append({"type": event_type, "data": data})
                        event_type = "message"
                        data_lines = []
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip() or "message"
                    elif line.startswith("data:"):
                        data_lines.append(line.split(":", 1)[1].strip())
        except Exception as exc:
            self._error = str(exc)


class ApiHarnessEvalRunner:
    def __init__(self, *, api_base_url: str, repo_root: str, out_dir: str) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.repo_root = str(Path(repo_root).resolve())
        self.out_dir = Path(out_dir).resolve()
        self.http = requests.Session()

    def run_scenario(
        self,
        scenario: EvalScenario,
        *,
        stream: bool = True,
        keep_session: bool = False,
        profile_overrides: dict[str, str] | None = None,
        judge_override: bool | None = None,
        judge_provider: str | None = None,
        judge_model: str | None = None,
    ) -> EvalRunArtifact:
        profile = dataclass_to_dict(scenario.profile)
        if profile_overrides:
            for key, value in profile_overrides.items():
                if value:
                    profile[key] = value
        profile["provider"], profile["model"] = self._resolve_profile_provider_model(
            str(profile.get("provider") or ""),
            str(profile.get("model") or ""),
        )

        artifact_id = uuid.uuid4().hex
        created_at = _utc_now()
        session_id = ""
        seeded_files: dict[str, str | None] = {}
        turn_results: list[EvalTurnResult] = []
        messages: list[dict[str, Any]] = []
        session_events: list[dict[str, Any]] = []
        trace_context = self._prepare_scenario_workspace(scenario, artifact_id=artifact_id)
        runtime_vars = trace_context["runtime_vars"]

        expanded_setup = _deep_expand_runtime_vars(dataclass_to_dict(scenario.setup), runtime_vars)
        expanded_tool_policy = expanded_setup.get("tool_policy") or {}
        if not expanded_tool_policy:
            expanded_tool_policy = {
                "enabled_tools": [],
                "allow_destructive_tools": [],
                "allowed_paths": [runtime_vars["repo_root"], runtime_vars["temp_workspace"]],
            }

        for file_seed in scenario.setup.files:
            path = Path(_deep_expand_runtime_vars(file_seed.path, runtime_vars))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_deep_expand_runtime_vars(file_seed.content, runtime_vars), encoding="utf-8")

        tracked_paths = self._tracked_paths_from_rules(scenario, runtime_vars=runtime_vars)
        for path in tracked_paths:
            seeded_files[path] = _hash_file(Path(path))

        session_payload = {
            "label": f"eval:{scenario.id}",
            "model": profile["model"],
            "provider": profile["provider"],
            "context_window": int(expanded_setup.get("context_window") or scenario.setup.context_window),
            "workspace_id": expanded_setup.get("workspace_id"),
            "build_project_id": expanded_setup.get("build_project_id"),
            "tool_policy": expanded_tool_policy,
            "delegation_policy": {"mode": "manual"},
        }
        session_data = self._post("/sessions", session_payload, expected=201)
        session_id = str(session_data["id"])

        try:
            for turn_index, turn in enumerate(scenario.turns, start=1):
                turn_content = _deep_expand_runtime_vars(turn.content, runtime_vars)
                turn_metadata = _deep_expand_runtime_vars(turn.metadata or {}, runtime_vars)
                message_payload = {
                    "content": turn_content,
                    "role": turn.role,
                    "metadata": turn_metadata,
                }
                message_data = self._post(f"/sessions/{session_id}/messages", message_payload, expected=201)
                run_id = str(message_data["run_id"])
                collector = _SseCollector(self.api_base_url, session_id, run_id, enabled=stream)
                collector.start()
                started = time.perf_counter()
                execute_data = self._execute_run_with_recovery(session_id, run_id)
                latency_ms = int((time.perf_counter() - started) * 1000)
                collector.stop()
                run_events = self._get(f"/sessions/{session_id}/runs/{run_id}/events").get("events", [])
                observations = self._extract_turn_observations(
                    run_events,
                    execute_data.get("tool_results") or [],
                    temp_workspace=runtime_vars["temp_workspace"],
                )
                turn_results.append(
                    EvalTurnResult(
                        turn_index=turn_index,
                        role=turn.role,
                        content=turn_content,
                        run_id=run_id,
                        message_id=str(message_data["message_id"]),
                        status=str(execute_data.get("status") or ""),
                        finish_reason=str(execute_data.get("finish_reason") or ""),
                        final_text=str(execute_data.get("final_text") or ""),
                        transient_text=str(execute_data.get("transient_text") or ""),
                        error=str(execute_data.get("error") or ""),
                        latency_ms=latency_ms,
                        input_tokens=int(execute_data.get("input_tokens") or 0),
                        output_tokens=int(execute_data.get("output_tokens") or 0),
                        tool_results=list(execute_data.get("tool_results") or []),
                        provider_route=execute_data.get("provider_route"),
                        run_events=list(run_events),
                        stream_events=list(collector.events),
                        observations=observations,
                    )
                )

            messages = self._get(f"/sessions/{session_id}/messages?limit=5000").get("messages", [])
            session_events = self._get(f"/sessions/{session_id}/events?limit=5000").get("events", [])
            checks = self._run_checks(scenario, turn_results, seeded_files, runtime_vars=runtime_vars)
            artifact = self._build_artifact(
                artifact_id=artifact_id,
                created_at=created_at,
                scenario=scenario,
                session_id=session_id,
                profile=profile,
                turn_results=turn_results,
                messages=messages,
                session_events=session_events,
                checks=checks,
                trace_context=trace_context,
            )
            judge_result, self_critique_result = self._score_with_evaluators(
                scenario,
                artifact,
                profile=profile,
                enabled_override=judge_override,
                judge_provider=judge_provider,
                judge_model=judge_model,
            )
            artifact.judge = judge_result
            artifact.self_critique = self_critique_result
        finally:
            if session_id and not keep_session:
                self._delete_session(session_id)
            cleanup_path = trace_context.get("cleanup_temp_root")
            if cleanup_path:
                shutil.rmtree(cleanup_path, ignore_errors=True)

        self._persist_artifact(artifact)
        return artifact

    def _prepare_scenario_workspace(self, scenario: EvalScenario, *, artifact_id: str) -> dict[str, Any]:
        temp_root = Path(
            scenario.setup.temp_workspace_root
            or (self.out_dir.parent / "tmp")
        ).resolve() / f"{_slug(scenario.id)}-{artifact_id[:8]}"
        temp_workspace = temp_root / scenario.setup.temp_workspace_name
        temp_workspace.mkdir(parents=True, exist_ok=True)

        fixture_dir = None
        if scenario.setup.fixture_dir:
            fixture_dir = Path(_deep_expand_runtime_vars(scenario.setup.fixture_dir, {"repo_root": self.repo_root})).resolve()
            if fixture_dir.exists():
                shutil.copytree(fixture_dir, temp_workspace, dirs_exist_ok=True)

        runtime_vars = {
            "repo_root": self.repo_root,
            "temp_root": str(temp_root),
            "temp_workspace": str(temp_workspace),
        }
        return {
            "runtime_vars": runtime_vars,
            "fixture_dir": str(fixture_dir) if fixture_dir else None,
            "cleanup_temp_root": str(temp_root),
        }

    def _tracked_paths_from_rules(
        self,
        scenario: EvalScenario,
        *,
        runtime_vars: dict[str, str],
    ) -> list[str]:
        paths: list[str] = []
        for rule in scenario.checks.rules:
            if rule.path and rule.type == "expected_file_changed":
                candidate = _deep_expand_runtime_vars(str(rule.path), runtime_vars)
                if candidate not in paths:
                    paths.append(candidate)
        return paths

    def _extract_turn_observations(
        self,
        run_events: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        *,
        temp_workspace: str,
    ) -> dict[str, Any]:
        files_read: list[str] = []
        files_written: list[str] = []
        commands: list[str] = []
        runtime_event_types: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        assistant_fragments: list[str] = []

        for event in run_events:
            event_type = str(event.get("type") or "")
            runtime_event_types.append(event_type)
            data = event.get("data") or {}
            if event_type in {"assistant_delta", "stream.assistant_delta"}:
                text = str(data.get("text") or "")
                if text:
                    assistant_fragments.append(text)
            if event_type in {"tool_call", "tool_called", "stream.tool_call"}:
                tool_name = str(data.get("tool_name") or data.get("name") or data.get("tool_id") or "")
                tool_input = data.get("input") or {}
                tool_calls.append({"tool_name": tool_name, "input": tool_input})
                if tool_name == "read":
                    path = str(tool_input.get("path") or "").strip()
                    if path:
                        files_read.append(path)
                elif tool_name == "read_file":
                    path = str(tool_input.get("filePath") or tool_input.get("path") or "").strip()
                    if path:
                        files_read.append(path)
                elif tool_name in {"write", "edit"}:
                    path = str(tool_input.get("path") or "").strip()
                    if path:
                        files_written.append(path)
                elif tool_name == "exec":
                    command = str(tool_input.get("command") or "").strip()
                    if command:
                        commands.append(command)

        if assistant_fragments:
            for tool_name, tool_input in self._extract_tool_calls_from_text("".join(assistant_fragments)):
                tool_calls.append({"tool_name": tool_name, "input": tool_input})
                if tool_name in {"read", "read_file"}:
                    path = str(tool_input.get("path") or tool_input.get("filePath") or "").strip()
                    if path:
                        files_read.append(path)
                elif tool_name in {"write", "edit"}:
                    path = str(tool_input.get("path") or "").strip()
                    if path:
                        files_written.append(path)
                elif tool_name == "exec":
                    command = str(tool_input.get("command") or "").strip()
                    if command:
                        commands.append(command)

        for result in tool_results:
            tool_name = str(result.get("tool_name") or result.get("name") or "")
            content = str(result.get("content") or "")
            if tool_name == "edit":
                match = _EDIT_RESULT_PATH_RE.search(content)
                if match:
                    files_written.append(match.group("path").strip())
            elif tool_name == "write":
                match = _WRITE_RESULT_PATH_RE.search(content)
                if match:
                    files_written.append(match.group("path").strip())

        return {
            "tool_calls": tool_calls,
            "tool_result_count": len(tool_results),
            "files_read": sorted(set(files_read)),
            "files_written": sorted(set(files_written)),
            "commands": commands,
            "runtime_event_types": runtime_event_types,
            "touched_temp_workspace": any(
                item.startswith(temp_workspace) for item in files_read + files_written
            ),
        }

    def _extract_tool_calls_from_text(self, text: str) -> list[tuple[str, dict[str, Any]]]:
        extracted: list[tuple[str, dict[str, Any]]] = []
        seen: set[tuple[str, str]] = set()

        for match in _XML_TOOL_CALL_RE.finditer(text):
            tool_name = str(match.group("name") or "").strip()
            body = str(match.group("body") or "").strip()
            try:
                tool_input = json.loads(body)
            except json.JSONDecodeError:
                continue
            signature = (tool_name, json.dumps(tool_input, sort_keys=True, ensure_ascii=False))
            if signature in seen:
                continue
            seen.add(signature)
            extracted.append((tool_name, tool_input))

        for match in _XML_PAIRED_TOOL_RE.finditer(text):
            tool_name = str(match.group("name") or "").strip()
            if tool_name in {"tool_call", "think", "invoke", "transient-window"}:
                continue
            body = str(match.group("body") or "").strip()
            if not body:
                continue
            if tool_name in {"read", "list_dir"}:
                tool_input = {"path": body}
            elif tool_name == "read_file":
                tool_input = {"filePath": body}
            elif tool_name == "exec":
                tool_input = {"command": body}
            else:
                continue
            signature = (tool_name, json.dumps(tool_input, sort_keys=True, ensure_ascii=False))
            if signature in seen:
                continue
            seen.add(signature)
            extracted.append((tool_name, tool_input))

        return extracted

    def _run_checks(
        self,
        scenario: EvalScenario,
        turn_results: list[EvalTurnResult],
        before_hashes: dict[str, str | None],
        *,
        runtime_vars: dict[str, str],
    ) -> list[EvalCheckResult]:
        results: list[EvalCheckResult] = []
        final_turn = turn_results[-1] if turn_results else None
        all_tool_names = {
            str(result.get("tool_name") or result.get("name") or "")
            for turn in turn_results
            for result in turn.tool_results
        }
        all_shell_text = "\n".join(
            json.dumps(result, ensure_ascii=False)
            for turn in turn_results
            for result in turn.tool_results
        )

        forbidden_tool_names = list(scenario.forbidden.get("tool_names") or [])
        forbidden_shell_patterns = list(scenario.forbidden.get("shell_patterns") or [])
        if forbidden_tool_names or forbidden_shell_patterns:
            passed = not any(name in all_tool_names for name in forbidden_tool_names)
            if passed and forbidden_shell_patterns:
                passed = not any(pattern.lower() in all_shell_text.lower() for pattern in forbidden_shell_patterns)
            results.append(
                EvalCheckResult(
                    type="scenario_forbidden_actions",
                    passed=passed,
                    details="No forbidden actions detected" if passed else "Forbidden tool or shell pattern detected",
                )
            )

        for rule in scenario.checks.rules:
            turn = turn_results[(rule.turn - 1)] if rule.turn and 0 < rule.turn <= len(turn_results) else final_turn
            text = turn.final_text if turn else ""
            if rule.type == "status_is":
                actual = turn.status if turn else ""
                passed = actual == str(rule.value)
                results.append(EvalCheckResult(rule.type, passed, f"expected {rule.value}, got {actual}", rule.turn))
            elif rule.type == "final_text_contains":
                needle = str(rule.value or "")
                passed = needle in text
                results.append(EvalCheckResult(rule.type, passed, f"expected final text to contain {needle!r}", rule.turn))
            elif rule.type == "final_text_not_contains":
                needle = str(rule.value or "")
                passed = needle not in text
                results.append(EvalCheckResult(rule.type, passed, f"expected final text to avoid {needle!r}", rule.turn))
            elif rule.type == "transient_text_contains":
                haystack = turn.transient_text if turn else ""
                needle = str(rule.value or "")
                passed = needle in haystack
                results.append(EvalCheckResult(rule.type, passed, f"expected transient text to contain {needle!r}", rule.turn))
            elif rule.type == "expected_file_changed":
                path = Path(_deep_expand_runtime_vars(str(rule.path or ""), runtime_vars))
                before_hash = before_hashes.get(str(path))
                after_hash = _hash_file(path)
                passed = before_hash != after_hash and after_hash is not None
                results.append(EvalCheckResult(rule.type, passed, f"expected file hash to change for {path}", rule.turn))
            elif rule.type == "command_exit_zero":
                workdir = _deep_expand_runtime_vars(str(rule.workdir or self.repo_root), runtime_vars)
                command = _deep_expand_runtime_vars(str(rule.command or ""), runtime_vars)
                code, output = _run_shell_check(command, workdir=workdir)
                passed = code == 0
                details = f"exit={code}"
                if output:
                    details += f" output={output[:220]}"
                results.append(EvalCheckResult(rule.type, passed, details, rule.turn))
            elif rule.type == "max_latency_ms":
                actual = turn.latency_ms if turn else 0
                limit = int(rule.value or 0)
                passed = actual <= limit
                results.append(EvalCheckResult(rule.type, passed, f"latency {actual}ms <= {limit}ms", rule.turn))
            elif rule.type == "no_forbidden_actions":
                passed = not any(name in all_tool_names for name in rule.forbidden_tool_names)
                if passed and rule.forbidden_shell_patterns:
                    passed = not any(pattern.lower() in all_shell_text.lower() for pattern in rule.forbidden_shell_patterns)
                results.append(EvalCheckResult(rule.type, passed, "forbidden action scan complete", rule.turn))
            elif rule.type == "tool_name_used":
                tool_name = str(rule.value or "")
                passed = tool_name in all_tool_names
                results.append(EvalCheckResult(rule.type, passed, f"expected tool {tool_name!r} to be used", rule.turn))
            elif rule.type == "tool_name_not_used":
                tool_name = str(rule.value or "")
                passed = tool_name not in all_tool_names
                results.append(EvalCheckResult(rule.type, passed, f"expected tool {tool_name!r} to stay unused", rule.turn))
            elif rule.type == "observed_file_read_contains":
                needle = str(rule.value or "")
                observed = (turn.observations.get("files_read") if turn else []) or []
                passed = any(needle in item for item in observed)
                results.append(EvalCheckResult(rule.type, passed, f"expected observed read path containing {needle!r}", rule.turn))
            elif rule.type == "observed_file_written_contains":
                needle = str(rule.value or "")
                observed = (turn.observations.get("files_written") if turn else []) or []
                passed = any(needle in item for item in observed)
                results.append(EvalCheckResult(rule.type, passed, f"expected observed write path containing {needle!r}", rule.turn))
            elif rule.type == "observed_command_contains":
                needle = str(rule.value or "")
                observed = (turn.observations.get("commands") if turn else []) or []
                passed = any(needle in item for item in observed)
                results.append(EvalCheckResult(rule.type, passed, f"expected observed command containing {needle!r}", rule.turn))
            elif rule.type == "runtime_event_contains":
                needle = str(rule.value or "")
                observed = (turn.observations.get("runtime_event_types") if turn else []) or []
                passed = needle in observed
                results.append(EvalCheckResult(rule.type, passed, f"expected runtime event {needle!r}", rule.turn))
            else:
                results.append(EvalCheckResult(rule.type, False, "unknown rule type", rule.turn))
        return results

    def _build_artifact(
        self,
        *,
        artifact_id: str,
        created_at: str,
        scenario: EvalScenario,
        session_id: str,
        profile: dict[str, Any],
        turn_results: list[EvalTurnResult],
        messages: list[dict[str, Any]],
        session_events: list[dict[str, Any]],
        checks: list[EvalCheckResult],
        trace_context: dict[str, Any],
    ) -> EvalRunArtifact:
        final_turn = turn_results[-1] if turn_results else None
        hard_pass = all(check.passed for check in checks) if checks else True
        total_input_tokens = sum(turn.input_tokens for turn in turn_results)
        total_output_tokens = sum(turn.output_tokens for turn in turn_results)
        total_latency_ms = sum(turn.latency_ms for turn in turn_results)
        tool_call_count = sum(len(turn.tool_results) for turn in turn_results)

        all_files_read = sorted({path for turn in turn_results for path in turn.observations.get("files_read", [])})
        all_files_written = sorted({path for turn in turn_results for path in turn.observations.get("files_written", [])})
        all_commands = [cmd for turn in turn_results for cmd in turn.observations.get("commands", [])]
        all_event_types = [event_type for turn in turn_results for event_type in turn.observations.get("runtime_event_types", [])]

        baseline = {
            "git_commit": _git_commit(self.repo_root),
            "harness_profile": profile.get("harness_profile") or "default",
            "provider": profile.get("provider") or "",
            "model": profile.get("model") or "",
            "scenario_id": scenario.id,
            "category": scenario.category,
        }
        artifact_date = datetime.now(timezone.utc).strftime("%Y%m%d")
        artifact_dir = self.out_dir / artifact_date / _slug(scenario.id)
        artifact_path = artifact_dir / f"{artifact_id}.json"
        replay_path = artifact_dir / f"{artifact_id}.replay.json"

        summary = {
            "status": final_turn.status if final_turn else "not-run",
            "finish_reason": final_turn.finish_reason if final_turn else "",
            "hard_pass": hard_pass,
            "check_pass_count": sum(1 for check in checks if check.passed),
            "check_fail_count": sum(1 for check in checks if not check.passed),
            "final_text_preview": (final_turn.final_text[:280] if final_turn and final_turn.final_text else ""),
            "failure_taxonomy": self._failure_taxonomy(turn_results, checks),
        }
        metrics = {
            "turn_count": len(turn_results),
            "latency_ms_total": total_latency_ms,
            "latency_ms_avg": int(total_latency_ms / len(turn_results)) if turn_results else 0,
            "input_tokens_total": total_input_tokens,
            "output_tokens_total": total_output_tokens,
            "tool_call_count": tool_call_count,
            "context_window": scenario.setup.context_window,
            "max_turns_requested": scenario.max_turns,
        }
        replay = {
            "scenario_path": scenario.path,
            "session_id": session_id,
            "turns": [
                {"role": turn.role, "content": turn.content, "run_id": turn.run_id, "tool_results": turn.tool_results}
                for turn in turn_results
            ],
            "messages": messages,
        }
        trace = {
            "temp_workspace": trace_context["runtime_vars"]["temp_workspace"],
            "fixture_dir": trace_context.get("fixture_dir"),
            "observed_files_read": all_files_read,
            "observed_files_written": all_files_written,
            "observed_commands": all_commands,
            "runtime_event_types": all_event_types,
            "session_events": session_events,
        }
        return EvalRunArtifact(
            schema_version=SCHEMA_VERSION,
            artifact_id=artifact_id,
            created_at=created_at,
            api_base_url=self.api_base_url,
            scenario={
                "id": scenario.id,
                "title": scenario.title,
                "category": scenario.category,
                "goal": scenario.goal,
                "tags": scenario.tags,
                "path": scenario.path,
                "notes_path": scenario.notes_path,
                "suite_ids": scenario.suite_ids,
                "expected_behavior": scenario.expected_behavior,
                "forbidden_behavior": scenario.forbidden_behavior,
                "required_observations": scenario.required_observations,
                "max_turns": scenario.max_turns,
                "scoring_rubric": [asdict(item) for item in scenario.scoring_rubric],
            },
            profile=profile,
            baseline=baseline,
            session={"id": session_id},
            summary=summary,
            turns=[asdict(item) for item in turn_results],
            checks=[asdict(item) for item in checks],
            metrics=metrics,
            judge={"enabled": scenario.judge.enabled, "status": "not_implemented", "model": scenario.judge.model, "provider": scenario.judge.provider},
            self_critique={"status": "not_implemented"},
            replay=replay,
            paths={"artifact": str(artifact_path), "replay": str(replay_path)},
            trace=trace,
            messages=messages,
            session_events=session_events,
        )

    def _failure_taxonomy(self, turn_results: list[EvalTurnResult], checks: list[EvalCheckResult]) -> list[str]:
        tags: list[str] = []
        if any(turn.status == "blocked" for turn in turn_results):
            tags.append("run_blocked")
        if any(turn.error for turn in turn_results):
            tags.append("run_error")
        if any(not check.passed for check in checks):
            tags.append("rule_check_failed")
        if any(turn.latency_ms > 30000 for turn in turn_results):
            tags.append("slow_turn")
        if any("tool_call" in turn.observations.get("runtime_event_types", []) for turn in turn_results):
            tags.append("tool_use_present")
        return sorted(set(tags))

    def _score_with_evaluators(
        self,
        scenario: EvalScenario,
        artifact: EvalRunArtifact,
        *,
        profile: dict[str, Any],
        enabled_override: bool | None = None,
        judge_provider: str | None = None,
        judge_model: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        enabled = scenario.judge.enabled if enabled_override is None else enabled_override
        if not enabled:
            disabled_judge = {
                "enabled": False,
                "status": "disabled",
                "model": judge_model or scenario.judge.model or profile.get("model") or "",
                "provider": judge_provider or scenario.judge.provider or profile.get("provider") or "",
            }
            disabled_self = {
                "enabled": False,
                "status": "disabled",
                "model": judge_model or scenario.judge.model or profile.get("model") or "",
                "provider": judge_provider or scenario.judge.provider or profile.get("provider") or "",
            }
            return disabled_judge, disabled_self

        evaluator_model = judge_model or scenario.judge.model or str(profile.get("model") or "")
        evaluator_provider = judge_provider or scenario.judge.provider or str(profile.get("provider") or "llamacpp")
        evaluator_provider, evaluator_model = self._resolve_profile_provider_model(
            str(evaluator_provider or ""),
            str(evaluator_model or ""),
        )
        rubric = [item.name for item in scenario.scoring_rubric] or scenario.judge.rubric or [
            "intent_capture",
            "context_use",
            "tool_discipline",
            "completion",
            "output_quality",
            "runtime_stability",
            "user_experience",
        ]
        evaluator_session_id = ""
        try:
            session_payload = {
                "label": f"judge:{scenario.id}",
                "model": evaluator_model,
                "provider": evaluator_provider,
                "context_window": min(int(scenario.setup.context_window or 32768), 32768),
                "tool_policy": {
                    "enabled_tools": [],
                    "allow_destructive_tools": [],
                    "allowed_paths": [self.repo_root],
                },
                "delegation_policy": {"mode": "manual"},
            }
            session_data = self._post("/sessions", session_payload, expected=201)
            evaluator_session_id = str(session_data["id"])

            judge_raw_text, judge_parsed, judge_run_id = self._run_evaluator_prompt(
                evaluator_session_id,
                self._build_judge_prompt(artifact, scenario=scenario, rubric=rubric),
                metadata={"eval_judge": True},
            )
            judge_result = {
                "enabled": True,
                "status": "scored" if judge_parsed is not None else "parse_failed",
                "model": evaluator_model,
                "provider": evaluator_provider,
                "rubric": rubric,
                "run_id": judge_run_id,
                "session_id": evaluator_session_id,
                "raw_text": judge_raw_text,
                "parsed": judge_parsed,
            }
            if isinstance(judge_parsed, dict):
                judge_result.update(
                    {
                        "overall_score": judge_parsed.get("overall_score"),
                        "dimension_scores": judge_parsed.get("dimension_scores") or {},
                        "verdict": judge_parsed.get("verdict"),
                        "strengths": judge_parsed.get("strengths") or [],
                        "weaknesses": judge_parsed.get("weaknesses") or [],
                        "rationale": judge_parsed.get("rationale") or "",
                        "failure_category": judge_parsed.get("failure_category") or "",
                        "likely_root_cause": judge_parsed.get("likely_root_cause") or "",
                        "recommended_patch_target": judge_parsed.get("recommended_patch_target") or "",
                        "minimal_fix_suggestion": judge_parsed.get("minimal_fix_suggestion") or "",
                    }
                )

            self_raw_text, self_parsed, self_run_id = self._run_evaluator_prompt(
                evaluator_session_id,
                self._build_self_critique_prompt(artifact),
                metadata={"eval_self_critique": True},
            )
            self_result = {
                "enabled": True,
                "status": "scored" if self_parsed is not None else "parse_failed",
                "model": evaluator_model,
                "provider": evaluator_provider,
                "run_id": self_run_id,
                "session_id": evaluator_session_id,
                "raw_text": self_raw_text,
                "parsed": self_parsed,
            }
            if isinstance(self_parsed, dict):
                self_result.update(
                    {
                        "overall_score": self_parsed.get("overall_score"),
                        "confidence": self_parsed.get("confidence"),
                        "verdict": self_parsed.get("verdict"),
                        "strengths": self_parsed.get("strengths") or [],
                        "mistakes": self_parsed.get("mistakes") or [],
                        "next_improvements": self_parsed.get("next_improvements") or [],
                        "rationale": self_parsed.get("rationale") or "",
                    }
                )
            return judge_result, self_result
        except Exception as exc:
            error_judge = {
                "enabled": True,
                "status": "judge_error",
                "model": evaluator_model,
                "provider": evaluator_provider,
                "rubric": rubric,
                "error": str(exc),
            }
            error_self = {
                "enabled": True,
                "status": "self_critique_error",
                "model": evaluator_model,
                "provider": evaluator_provider,
                "error": str(exc),
            }
            return error_judge, error_self
        finally:
            if evaluator_session_id:
                self._delete_session(evaluator_session_id)

    def _run_evaluator_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        metadata: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None, str]:
        message_data = self._post(
            f"/sessions/{session_id}/messages",
            {"content": prompt, "role": "user", "metadata": metadata},
            expected=201,
        )
        run_id = str(message_data["run_id"])
        execute_data = self._post(f"/sessions/{session_id}/runs/{run_id}/execute", None, expected=200)
        raw_text = str(execute_data.get("final_text") or execute_data.get("text") or execute_data.get("transient_text") or "")
        parsed = self._extract_json_object(raw_text)
        return raw_text, parsed, run_id

    def _build_judge_prompt(self, artifact: EvalRunArtifact, *, scenario: EvalScenario, rubric: list[str]) -> str:
        payload = {
            "scenario": artifact.scenario,
            "summary": artifact.summary,
            "metrics": artifact.metrics,
            "checks": artifact.checks,
            "trace": artifact.trace,
            "turns": [
                {
                    "turn_index": turn.get("turn_index"),
                    "user_content": turn.get("content"),
                    "status": turn.get("status"),
                    "finish_reason": turn.get("finish_reason"),
                    "final_text": turn.get("final_text"),
                    "transient_text": turn.get("transient_text"),
                    "error": turn.get("error"),
                    "latency_ms": turn.get("latency_ms"),
                    "tool_count": len(turn.get("tool_results") or []),
                    "observations": turn.get("observations") or {},
                }
                for turn in artifact.turns
            ],
        }
        rubric_text = ", ".join(rubric)
        expected = "\n".join(f"- {item}" for item in scenario.expected_behavior) or "- none provided"
        forbidden = "\n".join(f"- {item}" for item in scenario.forbidden_behavior) or "- none provided"
        observations = "\n".join(f"- {item}" for item in scenario.required_observations) or "- none provided"
        return (
            "You are evaluating OpenCloset as a product harness, not benchmarking raw model intelligence.\n"
            "Judge prompt shaping, tool discipline, continuity, execution honesty, and final answer quality.\n"
            "Do not create transient windows.\n"
            "Return JSON only. No markdown, no XML, no prose outside the JSON object.\n"
            "Use this exact shape:\n"
            "{"
            "\"overall_score\": number, "
            "\"dimension_scores\": {\"<dimension>\": number}, "
            "\"verdict\": \"pass|mixed|fail\", "
            "\"strengths\": [string], "
            "\"weaknesses\": [string], "
            "\"failure_category\": string, "
            "\"likely_root_cause\": string, "
            "\"recommended_patch_target\": string, "
            "\"minimal_fix_suggestion\": string, "
            "\"rationale\": string"
            "}\n"
            f"Rubric dimensions: {rubric_text}\n"
            "Automatic hard-fail behaviors to punish heavily if present:\n"
            "- rewrites existing working code without inspection\n"
            "- claims work was done when it was not\n"
            "- ignores explicit user constraint\n"
            "- stalls without final answer\n"
            "- tool loop with no progress\n"
            "- loses active objective\n"
            "- fabricates file contents or runtime state\n"
            "- excessive pre-action narration after deciding to act\n"
            "Expected behavior:\n"
            f"{expected}\n"
            "Forbidden behavior:\n"
            f"{forbidden}\n"
            "Required observations:\n"
            f"{observations}\n"
            "Payload:\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    def _build_self_critique_prompt(self, artifact: EvalRunArtifact) -> str:
        payload = {
            "scenario": artifact.scenario,
            "summary": artifact.summary,
            "metrics": artifact.metrics,
            "checks": artifact.checks,
            "trace": artifact.trace,
            "turns": artifact.turns,
        }
        return (
            "You are performing a machine-readable self-critique of one OpenCloset eval run.\n"
            "Focus on harness behavior, not general flattery.\n"
            "Do not create transient windows.\n"
            "Return JSON only with this exact shape:\n"
            "{"
            "\"overall_score\": number, "
            "\"confidence\": number, "
            "\"verdict\": \"pass|mixed|fail\", "
            "\"strengths\": [string], "
            "\"mistakes\": [string], "
            "\"next_improvements\": [string], "
            "\"rationale\": string"
            "}\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        candidate = (text or "").strip()
        if not candidate:
            return None
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    def _persist_artifact(self, artifact: EvalRunArtifact) -> None:
        artifact_payload = dataclass_to_dict(artifact)
        artifact_path = Path(artifact.paths["artifact"])
        replay_path = Path(artifact.paths["replay"])
        _json_dump(artifact_path, artifact_payload)
        _json_dump(replay_path, artifact_payload["replay"])

    def _resolve_profile_provider_model(self, provider: str, model: str) -> tuple[str, str]:
        normalized_provider = provider.strip() or "llamacpp"
        normalized_model = model.strip()
        if normalized_model:
            return normalized_provider, normalized_model
        try:
            providers = list((self._get("/providers") or {}).get("providers") or [])
        except Exception:
            providers = []
        if normalized_provider and normalized_provider != "auto":
            for row in providers:
                if str(row.get("id") or "") == normalized_provider and str(row.get("model_name") or "").strip():
                    return normalized_provider, str(row.get("model_name") or "").strip()
        for preferred in ("llamacpp", "ollama", "openai"):
            for row in providers:
                if (
                    str(row.get("id") or "") == preferred
                    and bool(row.get("enabled"))
                    and str(row.get("model_name") or "").strip()
                ):
                    return preferred, str(row.get("model_name") or "").strip()
        return normalized_provider, normalized_model

    def _post(self, path: str, payload: dict[str, Any] | None, *, expected: int) -> dict[str, Any]:
        last_response = None
        for attempt in range(8):
            response = self.http.post(f"{self.api_base_url}{path}", json=payload, timeout=600)
            last_response = response
            if response.status_code == expected:
                return response.json()
            if response.status_code >= 500 and "database is locked" in response.text.lower() and attempt < 7:
                time.sleep(min(4.0, 0.5 * (attempt + 1)))
                continue
            break
        raise RuntimeError(f"POST {path} -> {last_response.status_code}: {last_response.text[:400]}")

    def _execute_run_with_recovery(self, session_id: str, run_id: str) -> dict[str, Any]:
        path = f"/sessions/{session_id}/runs/{run_id}/execute"
        try:
            return self._post(path, None, expected=200)
        except ReadTimeout:
            return self._wait_for_terminal_run(session_id, run_id, reason="execute_read_timeout")
        except requests.RequestException as exc:
            if isinstance(exc, requests.Timeout):
                return self._wait_for_terminal_run(session_id, run_id, reason=type(exc).__name__)
            raise

    def _wait_for_terminal_run(
        self,
        session_id: str,
        run_id: str,
        *,
        reason: str,
        timeout_seconds: float = 660.0,
        poll_interval_seconds: float = 2.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_run_payload: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            run_payload = self._get(f"/sessions/{session_id}/runs/{run_id}")
            last_run_payload = run_payload
            if str(run_payload.get("status") or "") not in {"queued", "running"}:
                recovered = dict(run_payload)
                recovered.setdefault("recovered_after", reason)
                return recovered
            time.sleep(poll_interval_seconds)
        self._request_interrupt_best_effort(session_id, run_id)
        interrupt_deadline = time.monotonic() + 20.0
        while time.monotonic() < interrupt_deadline:
            run_payload = self._get(f"/sessions/{session_id}/runs/{run_id}")
            last_run_payload = run_payload
            if str(run_payload.get("status") or "") not in {"queued", "running", "interrupt_requested"}:
                recovered = dict(run_payload)
                recovered.setdefault("recovered_after", f"{reason}_after_interrupt")
                return recovered
            time.sleep(1.0)

        recovered = dict(last_run_payload or {"run_id": run_id, "session_id": session_id})
        preview = json.dumps(recovered, ensure_ascii=False)[:400]
        recovered["status"] = "failed"
        recovered["finish_reason"] = "eval_recovery_timeout"
        recovered["error"] = (
            f"Timed out waiting for terminal run state after {reason}: {preview}. "
            "Interrupt was requested, but the run never resolved to a terminal state."
        )
        recovered.setdefault("final_text", "")
        recovered.setdefault("transient_text", "")
        recovered.setdefault("tool_results", [])
        recovered.setdefault("input_tokens", int((last_run_payload or {}).get("input_tokens") or 0))
        recovered.setdefault("output_tokens", int((last_run_payload or {}).get("output_tokens") or 0))
        recovered["recovered_after"] = f"{reason}_timed_out"
        return recovered

    def _request_interrupt_best_effort(self, session_id: str, run_id: str) -> None:
        try:
            self._post(f"/sessions/{session_id}/runs/{run_id}/interrupt", None, expected=200)
        except Exception:
            return

    def _get(self, path: str) -> dict[str, Any]:
        response = self.http.get(f"{self.api_base_url}{path}", timeout=120)
        response.raise_for_status()
        return response.json()

    def _delete_session(self, session_id: str) -> None:
        for attempt in range(8):
            try:
                response = self.http.delete(f"{self.api_base_url}/sessions/{session_id}", timeout=30)
            except Exception:
                return
            if response.status_code < 400 or response.status_code == 404:
                return
            if response.status_code >= 500 and "database is locked" in response.text.lower() and attempt < 7:
                time.sleep(min(4.0, 0.5 * (attempt + 1)))
                continue
            return


def load_artifact(path: str | Path) -> dict[str, Any]:
    return _json_load(Path(path))


def find_matching_artifacts(
    out_dir: str | Path,
    *,
    scenario_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    harness_profile: str | None = None,
) -> list[dict[str, Any]]:
    root = Path(out_dir)
    artifacts: list[dict[str, Any]] = []
    for path in root.rglob("*.json"):
        if path.name.endswith(".replay.json"):
            continue
        try:
            artifact = _json_load(path)
        except Exception:
            continue
        if artifact.get("schema_version") != SCHEMA_VERSION:
            continue
        baseline = artifact.get("baseline") or {}
        scenario = artifact.get("scenario") or {}
        if scenario_id and scenario.get("id") != scenario_id:
            continue
        if provider and baseline.get("provider") != provider:
            continue
        if model and baseline.get("model") != model:
            continue
        if harness_profile and baseline.get("harness_profile") != harness_profile:
            continue
        artifacts.append(artifact)
    artifacts.sort(key=lambda item: str(item.get("created_at") or ""))
    return artifacts


def compare_artifacts(artifact_a: dict[str, Any], artifact_b: dict[str, Any]) -> dict[str, Any]:
    metrics_a = artifact_a.get("metrics") or {}
    metrics_b = artifact_b.get("metrics") or {}
    summary_a = artifact_a.get("summary") or {}
    summary_b = artifact_b.get("summary") or {}
    judge_a = artifact_a.get("judge") or {}
    judge_b = artifact_b.get("judge") or {}
    self_a = artifact_a.get("self_critique") or {}
    self_b = artifact_b.get("self_critique") or {}

    check_map_a = {f"{item.get('type')}:{item.get('turn')}": item for item in artifact_a.get("checks") or []}
    check_map_b = {f"{item.get('type')}:{item.get('turn')}": item for item in artifact_b.get("checks") or []}
    changed_checks: list[dict[str, Any]] = []
    for key in sorted(set(check_map_a) | set(check_map_b)):
        left = check_map_a.get(key)
        right = check_map_b.get(key)
        if left != right:
            changed_checks.append({"check": key, "a": left, "b": right})

    def _delta(name: str) -> dict[str, Any]:
        left = metrics_a.get(name, 0)
        right = metrics_b.get(name, 0)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return {"a": left, "b": right, "delta": right - left}
        return {"a": left, "b": right}

    return {
        "artifact_a": artifact_a.get("paths", {}).get("artifact"),
        "artifact_b": artifact_b.get("paths", {}).get("artifact"),
        "scenario_id": (artifact_b.get("scenario") or {}).get("id") or (artifact_a.get("scenario") or {}).get("id"),
        "baseline_key": artifact_b.get("baseline") or artifact_a.get("baseline") or {},
        "status": {
            "a": summary_a.get("status"),
            "b": summary_b.get("status"),
            "changed": summary_a.get("status") != summary_b.get("status"),
        },
        "hard_pass": {
            "a": summary_a.get("hard_pass"),
            "b": summary_b.get("hard_pass"),
            "changed": summary_a.get("hard_pass") != summary_b.get("hard_pass"),
        },
        "judge_overall_score": {
            "a": judge_a.get("overall_score"),
            "b": judge_b.get("overall_score"),
            "delta": (
                (judge_b.get("overall_score") - judge_a.get("overall_score"))
                if isinstance(judge_a.get("overall_score"), (int, float)) and isinstance(judge_b.get("overall_score"), (int, float))
                else None
            ),
        },
        "self_critique_overall_score": {
            "a": self_a.get("overall_score"),
            "b": self_b.get("overall_score"),
            "delta": (
                (self_b.get("overall_score") - self_a.get("overall_score"))
                if isinstance(self_a.get("overall_score"), (int, float)) and isinstance(self_b.get("overall_score"), (int, float))
                else None
            ),
        },
        "metrics": {
            "latency_ms_total": _delta("latency_ms_total"),
            "latency_ms_avg": _delta("latency_ms_avg"),
            "input_tokens_total": _delta("input_tokens_total"),
            "output_tokens_total": _delta("output_tokens_total"),
            "tool_call_count": _delta("tool_call_count"),
        },
        "check_summary": {
            "a_pass_count": summary_a.get("check_pass_count"),
            "b_pass_count": summary_b.get("check_pass_count"),
            "a_fail_count": summary_a.get("check_fail_count"),
            "b_fail_count": summary_b.get("check_fail_count"),
            "changed_checks": changed_checks,
        },
        "failure_taxonomy": {
            "a": summary_a.get("failure_taxonomy") or [],
            "b": summary_b.get("failure_taxonomy") or [],
        },
        "patch_target": {
            "a": judge_a.get("recommended_patch_target"),
            "b": judge_b.get("recommended_patch_target"),
        },
    }


def compare_suite_artifacts(
    out_dir: str | Path,
    *,
    scenario_ids: list[str],
    provider: str | None = None,
    model: str | None = None,
    harness_profile: str | None = None,
) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for scenario_id in scenario_ids:
        matches = find_matching_artifacts(
            out_dir,
            scenario_id=scenario_id,
            provider=provider,
            model=model,
            harness_profile=harness_profile,
        )
        if len(matches) < 2:
            skipped.append({"scenario_id": scenario_id, "reason": "need at least two matching artifacts"})
            continue
        comparisons.append(compare_artifacts(matches[-2], matches[-1]))

    def _num(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) else None

    latency_deltas = [_num(item.get("metrics", {}).get("latency_ms_total", {}).get("delta")) for item in comparisons]
    latency_deltas = [item for item in latency_deltas if item is not None]
    judge_deltas = [_num(item.get("judge_overall_score", {}).get("delta")) for item in comparisons]
    judge_deltas = [item for item in judge_deltas if item is not None]
    self_deltas = [_num(item.get("self_critique_overall_score", {}).get("delta")) for item in comparisons]
    self_deltas = [item for item in self_deltas if item is not None]

    hard_pass_improved = 0
    hard_pass_regressed = 0
    status_changed = 0
    for item in comparisons:
        hard = item.get("hard_pass") or {}
        status = item.get("status") or {}
        if hard.get("a") is False and hard.get("b") is True:
            hard_pass_improved += 1
        if hard.get("a") is True and hard.get("b") is False:
            hard_pass_regressed += 1
        if status.get("changed"):
            status_changed += 1

    return {
        "suite_size": len(scenario_ids),
        "compared_count": len(comparisons),
        "skipped_count": len(skipped),
        "summary": {
            "hard_pass_improved": hard_pass_improved,
            "hard_pass_regressed": hard_pass_regressed,
            "status_changed": status_changed,
            "avg_latency_delta_ms": round(sum(latency_deltas) / len(latency_deltas), 2) if latency_deltas else None,
            "avg_judge_score_delta": round(sum(judge_deltas) / len(judge_deltas), 2) if judge_deltas else None,
            "avg_self_critique_score_delta": round(sum(self_deltas) / len(self_deltas), 2) if self_deltas else None,
        },
        "comparisons": comparisons,
        "skipped": skipped,
    }
