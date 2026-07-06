from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from api.agent.runner import RunExecutionResult, SessionAgentRunner
from api.api.events import StreamEvent


@runtime_checkable
class ExecutionRuntime(Protocol):
    def execute_run(self, session_id: str, run_id: str) -> RunExecutionResult:
        ...

    def request_interrupt(self, run_id: str) -> bool:
        ...


class LocalExecutionRuntime:
    """Compatibility wrapper for the current local SessionAgentRunner."""

    def __init__(self, app, runner: SessionAgentRunner) -> None:
        self.app = app
        self.runner = runner

    def execute_run(self, session_id: str, run_id: str) -> RunExecutionResult:
        delegated_result = self._maybe_execute_delegated_run(session_id, run_id)
        if delegated_result is not None:
            return delegated_result
        return self.runner.execute_run(session_id, run_id)

    def request_interrupt(self, run_id: str) -> bool:
        return self.runner.request_interrupt(run_id)

    def _maybe_execute_delegated_run(self, session_id: str, run_id: str) -> RunExecutionResult | None:
        session = self.app.execution_support.get_session(session_id)
        run = self.app.run_manager.get_run(run_id)
        if not session or not run:
            return None
        classification = self._classify_auto_delegation(session_id, run_id)
        if classification is None:
            return None

        instruction = classification.instruction
        try:
            task = self.app.delegations.create_task(
                session_id,
                task_type=classification.task_type,
                instruction=instruction,
                title=classification.title,
                budget=classification.budget or None,
                metadata={
                    "auto_delegated": True,
                    "source_run_id": run_id,
                    "source_message_id": classification.message_id,
                    "request_origin": "chat_runtime",
                    "goal": classification.title,
                },
            )
        except ValueError as exc:
            if self._is_unavailable_delegation_error(exc):
                return None
            raise

        substrate = self.app.delegations.get_substrate(task.get("substrate_id"))
        worker_name = str((substrate or {}).get("label") or task.get("worker_name") or task.get("substrate_id") or "delegated worker")

        self.app.run_manager.start_run(run_id)
        self.app.run_manager.stream_tool_use(
            run_id,
            "delegate_task",
            {
                "delegation_id": task["id"],
                "task_type": task["task_type"],
                "substrate_id": task.get("substrate_id"),
                "authority_mode": task.get("authority_mode"),
            },
        )
        self.app.run_manager.emit_stream_event(
            run_id,
            StreamEvent.provider_notice(
                "delegation_selected",
                f"{classification.task_type}:{task.get('substrate_id') or 'unknown'}",
            ),
        )

        try:
            completed_task = self.app.delegation_worker.execute_task(task["id"]) or self.app.delegations.get_task(task["id"]) or task
        except Exception as exc:
            self.app.run_manager.fail_run(run_id, str(exc) or type(exc).__name__)
            return RunExecutionResult(
                run_id=run_id,
                session_id=session_id,
                status="failed",
                finish_reason="delegation_failed",
                text="",
                final_text="",
                transient_text="",
                provider_route={"delegation_substrate_id": task.get("substrate_id"), "delegated": True},
                tool_results=[],
                input_tokens=0,
                output_tokens=0,
                interrupted=False,
                error=str(exc) or type(exc).__name__,
            )

        payload = completed_task.get("result_payload") if isinstance(completed_task.get("result_payload"), dict) else {}
        result_text = str(completed_task.get("result_text") or "").strip()
        summary = str(completed_task.get("result_summary") or payload.get("summary") or result_text).strip()
        status = str(completed_task.get("status") or "completed").strip().lower()
        finish_reason = "delegation_completed"
        tool_status = "success"
        error_text = ""
        run_status = "succeeded"
        exit_status = str(payload.get("exit_status") or "").strip().lower()
        if status != "completed" or exit_status in {"failed", "blocked", "partial"}:
            tool_status = "failed" if exit_status == "failed" or status == "failed" else "blocked"
            error_text = str(completed_task.get("error") or payload.get("summary") or "Delegated task did not complete successfully.").strip()
            finish_reason = "delegation_failed" if tool_status == "failed" else "delegation_blocked"
            run_status = "failed" if tool_status == "failed" else "blocked"
        final_text = self._render_delegated_reply(
            task_type=str(completed_task.get("task_type") or classification.task_type),
            worker_name=worker_name,
            payload=payload,
            fallback_summary=summary or "The delegated worker returned without a summary.",
            error=error_text or str(completed_task.get("error") or "").strip(),
        )

        self.app.run_manager.stream_tool_result(
            run_id,
            task["id"],
            "delegate_task",
            tool_status,
            final_text if tool_status == "success" else "",
            error_text or None,
            finish_reason if tool_status != "success" else None,
        )
        self.app.run_manager.persist_tool_result(
            session_id=session_id,
            run_id=run_id,
            tool_name="delegate_task",
            input_data={
                "delegation_id": task["id"],
                "task_type": completed_task.get("task_type"),
                "substrate_id": completed_task.get("substrate_id"),
            },
            output_data={
                "worker_name": worker_name,
                "summary": summary,
                "result_payload": payload,
            },
            status=tool_status,
            error=error_text or None,
        )
        self.app.run_manager.emit_stream_event(
            run_id,
            StreamEvent.worker_report(
                worker_name=worker_name,
                workspace_id=str(session.get("workspace_id") or ""),
                summary=summary or final_text,
                payload={
                    "delegation_id": task["id"],
                    "task_type": completed_task.get("task_type"),
                    "substrate_id": completed_task.get("substrate_id"),
                    "result_payload": payload,
                },
            ),
        )

        transcript_persisted = False
        transient_text = final_text
        if final_text:
            self.app.run_manager.stream_text_delta(run_id, final_text)
            self.app.run_manager.persist_assistant_message(
                session_id=session_id,
                run_id=run_id,
                content=final_text,
                token_estimate=max(1, len(final_text) // 3),
                persistent=True,
            )
            transcript_persisted = True
        self.app.run_manager.emit_stream_event(
            run_id,
            StreamEvent.assistant_final(
                status=run_status,
                finish_reason=finish_reason,
                final_text=final_text if transcript_persisted else "",
                transient_text=transient_text,
                transcript_persisted=transcript_persisted,
            ),
        )

        if run_status == "succeeded":
            self.app.run_manager.succeed_run(run_id)
        elif run_status == "blocked":
            self.app.run_manager.block_run(run_id, error_text or summary or "delegation blocked", finish_reason)
        else:
            self.app.run_manager.fail_run(run_id, error_text or summary or "delegation failed", finish_reason)

        return RunExecutionResult(
            run_id=run_id,
            session_id=session_id,
            status=run_status,
            finish_reason=finish_reason,
            text=final_text if transcript_persisted else "",
            final_text=final_text if transcript_persisted else "",
            transient_text=transient_text,
            provider_route={
                "delegation_substrate_id": completed_task.get("substrate_id"),
                "delegated": True,
                "worker_name": worker_name,
                "task_type": completed_task.get("task_type"),
            },
            tool_results=[
                {
                    "tool_id": task["id"],
                    "tool_name": "delegate_task",
                    "status": tool_status,
                    "content": final_text if tool_status == "success" else "",
                    "error": error_text or None,
                    "error_code": finish_reason if tool_status != "success" else None,
                }
            ],
            input_tokens=int(completed_task.get("input_tokens") or 0),
            output_tokens=int(completed_task.get("output_tokens") or 0),
            interrupted=False,
            error=error_text,
        )

    @staticmethod
    def _is_unavailable_delegation_error(exc: ValueError) -> bool:
        message = str(exc or "").strip().lower()
        return (
            "delegation substrate is not available" in message
            or "unknown delegation substrate" in message
        )

    def _classify_auto_delegation(self, session_id: str, run_id: str) -> "_AutoDelegationDecision | None":
        # HARD KILL-SWITCH: delegation (copilot/codex/claude_code CLIs) is OFF unless explicitly
        # enabled via env. The UI still POSTs mode="auto" on session-create, so the per-session
        # policy can't be trusted to keep it off — this guarantees the native loop runs end to end.
        import os
        if os.environ.get("OPENCLOSET_ENABLE_DELEGATION", "").strip().lower() not in {"1", "true", "yes", "on"}:
            return None
        policy = self.app.delegations.get_policy(session_id) or {}
        if str(policy.get("mode") or "manual").strip().lower() != "auto":
            return None

        row = self.app.db.execute(
            """
            SELECT id, role, content
            FROM messages
            WHERE run_id = ?
            ORDER BY position DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if not row or str(row["role"] or "") != "user":
            return None
        text = str(row["content"] or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if any(phrase in lowered for phrase in ("don't delegate", "do not delegate", "no delegation", "answer locally")):
            return None

        task_type = self._infer_delegation_task_type(text)
        if not task_type:
            return None
        route_policy = dict((policy.get("task_routes") or {}).get(task_type) or {})
        if not bool(route_policy.get("auto_delegate", False)):
            return None

        return _AutoDelegationDecision(
            task_type=task_type,
            title=self._build_delegation_title(task_type, text),
            instruction=text,
            message_id=str(row["id"]),
            budget=dict(route_policy.get("budget") or {}),
        )

    @staticmethod
    def _infer_delegation_task_type(text: str) -> str | None:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return None
        if lowered.endswith("?") and not re.search(r"\b(run|test|fix|implement|edit|patch|inspect|debug|trace|check|verify)\b", lowered):
            return None

        verify_hits = len(re.findall(r"\b(test|tests|pytest|verify|verification|validate|validation|check|checks|build|compile|lint|rerun|run)\b", lowered))
        implement_hits = len(re.findall(r"\b(fix|implement|build|create|write|edit|update|patch|refactor|change|add|remove|wire|hook|integrate)\b", lowered))
        inspect_hits = len(re.findall(r"\b(inspect|review|analyze|debug|investigate|trace|look into|find|search|grep|read|scan|explore)\b", lowered))
        code_context_hits = len(re.findall(r"\b(repo|repository|codebase|code|file|files|directory|function|class|module|runtime|backend|frontend|ui|api|bug|error|failure|log|logs|stack|tooling|command)\b", lowered))

        if verify_hits and (code_context_hits or "test" in lowered or "build" in lowered):
            return "verify"
        if implement_hits and (code_context_hits or "fix " in lowered or lowered.startswith(("fix", "implement", "build", "create", "write", "patch", "update"))):
            return "implement"
        if inspect_hits and code_context_hits:
            return "inspect"
        return None

    @staticmethod
    def _build_delegation_title(task_type: str, text: str) -> str:
        compact = " ".join(str(text or "").split())
        preview = compact[:72].rstrip()
        if len(compact) > 72:
            preview += "..."
        return f"{task_type.title()}: {preview}" if preview else task_type.title()

    @staticmethod
    def _render_delegated_reply(
        *,
        task_type: str,
        worker_name: str,
        payload: dict,
        fallback_summary: str,
        error: str = "",
    ) -> str:
        summary = str(payload.get("summary") or fallback_summary).strip() or fallback_summary
        patch_summary = str(payload.get("patch_summary") or "").strip()
        files = [str(item).strip() for item in payload.get("files_touched", []) if str(item).strip()]
        tests_passed = [str(item).strip() for item in payload.get("tests_passed", []) if str(item).strip()]
        tests_run = [str(item).strip() for item in payload.get("tests_run", []) if str(item).strip()]
        open_questions = [str(item).strip() for item in payload.get("open_questions", []) if str(item).strip()]
        risks = [str(item).strip() for item in payload.get("risks", []) if str(item).strip()]
        exit_status = str(payload.get("exit_status") or "").strip().lower()

        if error and exit_status == "partial":
            return f"I delegated that to {worker_name}, but it stopped before finishing. {summary or error}".strip()

        if error and exit_status in {"failed", "blocked"}:
            return f"I delegated that to {worker_name}, but it {exit_status}. {summary or error} {error}".strip()

        sentences = [f"I delegated that to {worker_name}. {summary}".strip()]
        if patch_summary:
            sentences.append(patch_summary)
        if files:
            sentences.append("Files touched: " + ", ".join(files[:6]) + ("." if len(files) <= 6 else ", and more."))
        if tests_passed:
            sentences.append("Tests passed: " + ", ".join(tests_passed[:4]) + ".")
        elif tests_run:
            sentences.append("Tests run: " + ", ".join(tests_run[:4]) + ".")
        if open_questions:
            sentences.append("Open questions: " + "; ".join(open_questions[:3]) + ".")
        if risks:
            sentences.append("Risks: " + "; ".join(risks[:3]) + ".")
        return " ".join(sentence.strip() for sentence in sentences if sentence.strip()).strip()


@dataclass(frozen=True)
class _AutoDelegationDecision:
    task_type: str
    title: str
    instruction: str
    message_id: str
    budget: dict


class ClawExecutionRuntime:
    def __init__(
        self,
        app,
        execution_support,
        *,
        command: str = "openclaw",
        use_local_mode: bool = True,
        timeout_seconds: float = 1800.0,
    ) -> None:
        self.app = app
        self.execution_support = execution_support
        self.command = self._resolve_command(command)
        self.use_local_mode = use_local_mode
        self.timeout_seconds = timeout_seconds
        self._active_processes: dict[str, subprocess.Popen[str]] = {}
        self._interrupt_requests: set[str] = set()
        self._lock = threading.Lock()

    def execute_run(self, session_id: str, run_id: str) -> RunExecutionResult:
        session = self.execution_support.get_session(session_id)
        run = self.app.run_manager.get_run(run_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        if not run or run["session_id"] != session_id:
            raise ValueError(f"Run not found: {run_id}")
        if run["status"] not in ("queued", "running"):
            raise ValueError(f"Run {run_id} is {run['status']}, not executable")

        input_envelope = self.execution_support.load_run_input_envelope(run_id)
        route = self.execution_support.resolve_substrate_route(session, run_id, input_envelope=input_envelope)
        self.app.event_logger.log(session_id, "provider_routed", route.to_payload(), run_id)
        self.app.run_manager.start_run(run_id)

        prompt_text = self.execution_support.build_claw_runtime_prompt(session, run_id, input_envelope=input_envelope)
        command = self._build_command(session_id, route, prompt_text)

        parsed_payloads: list[dict] = []
        assistant_chunks: list[str] = []
        final_text = ""
        finish_reason = "completed"
        input_tokens = 0
        output_tokens = 0
        tool_results: list[dict] = []
        pending_tool_inputs: dict[str, dict] = {}
        persisted_tool_ids: set[str] = set()
        raw_lines: list[str] = []
        transcript_persisted = False
        error_text = ""
        status_hint: str | None = None

        process = subprocess.Popen(
            command,
            cwd=self.app.config.get("WORKSPACE_ROOT"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        with self._lock:
            self._active_processes[run_id] = process

        try:
            assert process.stdout is not None
            for line in process.stdout:
                stripped = line.rstrip("\r\n")
                if stripped:
                    raw_lines.append(stripped)
                payload = self._try_parse_json_line(stripped)
                if payload is None:
                    if stripped:
                        self.app.run_manager.emit_stream_event(run_id, StreamEvent.provider_notice("claw_cli_output", stripped))
                    continue

                parsed_payloads.append(payload)
                event_type = str(payload.get("type") or payload.get("event") or payload.get("state") or "").strip()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

                if event_type in {"stream.assistant_delta", "assistant_delta", "text_delta"}:
                    text = str(data.get("text") or "")
                    if text:
                        assistant_chunks.append(text)
                        self.app.run_manager.stream_text_delta(run_id, text)
                    continue

                if event_type in {"stream.tool_call", "tool_call"}:
                    tool_name = str(data.get("tool_name") or data.get("name") or "tool")
                    input_data = data.get("input") if isinstance(data.get("input"), dict) else {}
                    tool_id = str(data.get("tool_id") or data.get("id") or tool_name)
                    pending_tool_inputs[tool_id] = input_data
                    self.app.run_manager.stream_tool_use(run_id, tool_name, input_data)
                    continue

                if event_type in {"stream.tool_result", "tool_result"}:
                    tool_id = str(data.get("tool_id") or data.get("id") or uuid.uuid4().hex)
                    tool_name = str(data.get("tool_name") or data.get("name") or "tool")
                    status = str(data.get("status") or "completed")
                    content = str(data.get("content") or "")
                    error = str(data.get("error") or "") or None
                    error_code = str(data.get("error_code") or "") or None
                    self.app.run_manager.stream_tool_result(run_id, tool_id, tool_name, status, content, error, error_code)
                    if tool_id not in persisted_tool_ids:
                        self.app.run_manager.persist_tool_result(
                            session_id=session_id,
                            run_id=run_id,
                            tool_name=tool_name,
                            input_data=pending_tool_inputs.get(tool_id) or {},
                            output_data={"content": content} if content else None,
                            status=status,
                            error=error,
                        )
                        persisted_tool_ids.add(tool_id)
                    tool_results.append(
                        {
                            "tool_id": tool_id,
                            "tool_name": tool_name,
                            "status": status,
                            "content": content,
                            "error": error,
                            "error_code": error_code,
                        }
                    )
                    continue

                if event_type in {"stream.usage", "usage"}:
                    input_tokens = int(data.get("input_tokens") or input_tokens or 0)
                    output_tokens = int(data.get("output_tokens") or output_tokens or 0)
                    self.app.run_manager.stream_usage(run_id, input_tokens, output_tokens)
                    continue

                extracted_text = self._extract_final_text(payload)
                if extracted_text:
                    final_text = extracted_text
                extracted_status = self._extract_status(payload)
                if extracted_status:
                    status_hint = extracted_status
                extracted_finish_reason = self._extract_finish_reason(payload)
                if extracted_finish_reason:
                    finish_reason = extracted_finish_reason
                extracted_error = self._extract_error(payload)
                if extracted_error:
                    error_text = extracted_error

            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            with self._lock:
                self._interrupt_requests.add(run_id)
            try:
                process.kill()
            except Exception:
                pass
            error_text = f"OpenClaw agent timed out after {self.timeout_seconds:.0f}s"
            finish_reason = "claw_timeout"
            status_hint = "failed"
        finally:
            with self._lock:
                self._active_processes.pop(run_id, None)

        raw_output = "\n".join(raw_lines).strip()
        if not parsed_payloads and raw_output:
            payload = self._try_parse_json_document(raw_output)
            if payload is not None:
                parsed_payloads.append(payload)
                extracted_text = self._extract_final_text(payload)
                if extracted_text:
                    final_text = extracted_text
                extracted_status = self._extract_status(payload)
                if extracted_status:
                    status_hint = extracted_status
                extracted_finish_reason = self._extract_finish_reason(payload)
                if extracted_finish_reason:
                    finish_reason = extracted_finish_reason
                extracted_error = self._extract_error(payload)
                if extracted_error:
                    error_text = extracted_error
        transient_text = "".join(assistant_chunks).strip() or final_text or raw_output
        final_text = final_text or transient_text

        interrupted = False
        with self._lock:
            interrupted = run_id in self._interrupt_requests
            self._interrupt_requests.discard(run_id)

        if interrupted:
            self.app.run_manager.interrupt_run(run_id)
            return RunExecutionResult(
                run_id=run_id,
                session_id=session_id,
                status="interrupted",
                finish_reason="interrupted",
                text="",
                final_text="",
                transient_text=transient_text,
                provider_route=route.to_payload(),
                tool_results=tool_results,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                interrupted=True,
                error="",
            )

        if not assistant_chunks and final_text:
            self.app.run_manager.stream_text_delta(run_id, final_text)

        terminal_status = status_hint or ("succeeded" if process.returncode == 0 else "failed")
        if terminal_status not in {"succeeded", "failed", "blocked", "interrupted"}:
            terminal_status = "succeeded" if process.returncode == 0 else "failed"

        if terminal_status == "succeeded" and final_text:
            self.app.run_manager.persist_assistant_message(
                session_id=session_id,
                run_id=run_id,
                content=final_text,
                token_estimate=max(1, len(final_text) // 3),
                persistent=True,
            )
            transcript_persisted = True

        if final_text or transient_text:
            self.app.run_manager.emit_stream_event(
                run_id,
                StreamEvent.assistant_final(
                    status=terminal_status,
                    finish_reason=finish_reason,
                    final_text=final_text if transcript_persisted else "",
                    transient_text=transient_text,
                    transcript_persisted=transcript_persisted,
                ),
            )

        if terminal_status == "succeeded":
            self.app.run_manager.succeed_run(run_id)
        elif terminal_status == "blocked":
            self.app.run_manager.block_run(run_id, error_text or finish_reason, finish_reason or "blocked")
        else:
            self.app.run_manager.fail_run(run_id, error_text or raw_output or f"openclaw exited with code {process.returncode}")

        final_run = self.app.run_manager.get_run(run_id)
        return RunExecutionResult(
            run_id=run_id,
            session_id=session_id,
            status=final_run["status"] if final_run else terminal_status,
            finish_reason=finish_reason,
            text=final_text if transcript_persisted else "",
            final_text=final_text if transcript_persisted else "",
            transient_text=transient_text,
            provider_route=route.to_payload(),
            tool_results=tool_results,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            interrupted=False,
            error=error_text,
        )

    def request_interrupt(self, run_id: str) -> bool:
        with self._lock:
            process = self._active_processes.get(run_id)
            if process is None:
                return False
            self._interrupt_requests.add(run_id)

        try:
            process.terminate()
            try:
                process.wait(timeout=1)
            except Exception:
                process.kill()
        except Exception:
            return False
        return True

    def _build_command(self, session_id: str, route, prompt_text: str) -> list[str]:
        command = [self.command, "agent", "--json", "--session-id", self._claw_session_key(session_id), "--message", prompt_text]
        if self.use_local_mode:
            command.append("--local")
        model_arg = self._build_model_arg(str(route.resolved_provider), str(route.resolved_model))
        if model_arg:
            command.extend(["--model", model_arg])
        if self.timeout_seconds > 0:
            command.extend(["--timeout", str(int(self.timeout_seconds))])
        return command

    @staticmethod
    def _claw_session_key(session_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencloset-session:{session_id}"))

    @staticmethod
    def _resolve_command(command: str) -> str:
        normalized = str(command or "openclaw").strip() or "openclaw"
        if os.name != "nt":
            return normalized
        lowered = normalized.lower()
        if lowered.endswith("openclaw.cmd") or lowered.endswith("openclaw.exe"):
            return normalized
        if lowered.endswith("openclaw.ps1"):
            cmd_candidate = normalized[:-4] + ".cmd"
            if os.path.exists(cmd_candidate):
                return cmd_candidate
            return normalized
        if lowered != "openclaw":
            return normalized
        cmd_path = shutil.which("openclaw.cmd")
        if cmd_path:
            return cmd_path
        return normalized

    @staticmethod
    def _build_model_arg(provider: str, model: str) -> str:
        model = str(model or "").strip()
        provider = str(provider or "").strip()
        if not model:
            return ""
        if "/" in model or not provider:
            return model
        return f"{provider}/{model}"

    @staticmethod
    def _try_parse_json_line(line: str) -> dict | None:
        candidate = str(line or "").strip()
        if not candidate or candidate[0] not in "[{":
            return None
        return ClawExecutionRuntime._try_parse_json_document(candidate)

    @staticmethod
    def _try_parse_json_document(text: str) -> dict | None:
        candidate = str(text or "").strip()
        if not candidate or candidate[0] not in "[{":
            return None
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _extract_status(payload: dict) -> str:
        for key in ("status", "state", "result"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                lowered = value.strip().lower()
                if lowered in {"succeeded", "failed", "blocked", "interrupted"}:
                    return lowered
        return ""

    @staticmethod
    def _extract_finish_reason(payload: dict) -> str:
        for key in ("finish_reason", "finishReason", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("finish_reason", "finishReason", "reason"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _extract_error(payload: dict) -> str:
        for key in ("error", "message", "errorMessage"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip() and key != "message":
                return value.strip()
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("error", "message", "errorMessage"):
                value = data.get(key)
                if isinstance(value, str) and value.strip() and key != "message":
                    return value.strip()
        return ""

    @classmethod
    def _extract_final_text(cls, payload: dict) -> str:
        candidates = [
            payload.get("final_text"),
            payload.get("finalText"),
            payload.get("text"),
            payload.get("content"),
        ]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([
                data.get("final_text"),
                data.get("finalText"),
                data.get("text"),
                data.get("content"),
            ])
        message = payload.get("message")
        if isinstance(message, dict):
            candidates.extend([message.get("text"), message.get("content")])
        result = payload.get("result")
        if isinstance(result, dict):
            candidates.extend([
                result.get("text"),
                result.get("content"),
                result.get("final_text"),
                result.get("finalAssistantVisibleText"),
                result.get("finalAssistantRawText"),
            ])
            payloads = result.get("payloads")
            if isinstance(payloads, list):
                for entry in payloads:
                    if isinstance(entry, dict):
                        candidates.extend([entry.get("text"), entry.get("content")])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""
