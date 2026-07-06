from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from api.tools.executor import ExecutionStatus, ToolCall as ExecutorToolCall, ToolResult


FAILURE_CATEGORIES = {
    "command_not_found",
    "bad_subcommand",
    "bad_argument_order",
    "quoting_error",
    "path_error",
    "permission_error",
    "timeout",
    "empty_result",
    "semantic_unexpected_output",
    "mutation_failed",
    "unknown",
}


@dataclass
class FailureRecord:
    call_id: str
    tool_name: str
    category: str
    subgoal: str
    strategy: str
    signature: str
    detail: str
    command: str = ""
    facts: list[str] = field(default_factory=list)


@dataclass
class RecoveryCapsule:
    subgoal: str
    attempt_count: int
    max_attempts: int
    failure_family: str
    blocked_strategies: list[str]
    facts: list[str]
    next_safe_action: str
    recovery_note: str
    freeze_mutation: bool = False


@dataclass
class FailurePivot:
    tool_name: str
    repeated_pattern: str
    attempt_count: int
    pivot_hint: str


@dataclass
class GovernorDecision:
    inject_capsule: bool = False
    capsule: RecoveryCapsule | None = None
    failure_pivot: FailurePivot | None = None
    message_role: str | None = None
    message_kind: str = "text"
    message_content: str = ""
    terminal_reason: str | None = None
    terminal_error: str = ""
    terminal_payload: dict[str, Any] = field(default_factory=dict)


class RunGovernor:
    """Deterministic execution governor for tool retries and recovery."""

    def __init__(
        self,
        *,
        max_attempts_per_subgoal: int = 4,
        duplicate_failure_threshold: int = 2,
        freeze_after_attempts: int = 3,
        max_pending_action_attempts: int = 2,
        repeated_intent_threshold: int = 2,
        max_recovery_attempts: int = 5,
    ) -> None:
        self.max_attempts_per_subgoal = max(1, max_attempts_per_subgoal)
        self.duplicate_failure_threshold = max(1, duplicate_failure_threshold)
        self.freeze_after_attempts = max(1, freeze_after_attempts)
        self.max_pending_action_attempts = max(0, max_pending_action_attempts)
        self.repeated_intent_threshold = max(2, repeated_intent_threshold)
        self.max_recovery_attempts = max(0, max_recovery_attempts)
        self._attempts_by_subgoal: dict[str, int] = {}
        self._signature_attempts: dict[str, int] = {}
        self._blocked_strategies_by_subgoal: dict[str, set[str]] = {}
        self._facts_by_subgoal: dict[str, list[str]] = {}
        self._capsule_counts: dict[str, int] = {}
        self._freeze_subgoals: set[str] = set()
        self._pending_action_attempts = 0
        self._last_pending_action_signature = ""
        self._pending_action_signature_repeats = 0
        self._recovery_attempts_by_subgoal: dict[str, int] = {}

    def filter_calls(self, calls: list[ExecutorToolCall]) -> tuple[list[ExecutorToolCall], list[ToolResult]]:
        allowed: list[ExecutorToolCall] = []
        blocked: list[ToolResult] = []
        for call in calls:
            subgoal = self._derive_subgoal(call)
            strategy = self._derive_strategy(call)
            if strategy in self._blocked_strategies_by_subgoal.get(subgoal, set()):
                blocked.append(
                    ToolResult(
                        call_id=call.call_id,
                        tool_name=call.name,
                        status=ExecutionStatus.VALIDATION_FAILED,
                        content="",
                        error=(
                            f"RunGovernor blocked repeated failure strategy '{strategy}' for subgoal '{subgoal}'. "
                            "Switch methods and follow the latest recovery capsule instead of retrying the same command family."
                        ),
                    )
                )
                continue
            if self._freeze_subgoals and self._call_mutates_state(call):
                blocked.append(
                    ToolResult(
                        call_id=call.call_id,
                        tool_name=call.name,
                        status=ExecutionStatus.VALIDATION_FAILED,
                        content="",
                        error=(
                            f"RunGovernor froze mutation for subgoal '{subgoal}' during recovery. "
                            "Use inspection/help/fallback steps only until the recovery capsule is satisfied."
                        ),
                    )
                )
                continue
            allowed.append(call)
        return allowed, blocked

    def observe_batch(
        self,
        calls_by_id: dict[str, ExecutorToolCall],
        batch_results: list[ToolResult],
    ) -> GovernorDecision:
        failure_records = [
            self._classify_failure(calls_by_id.get(result.call_id), result)
            for result in batch_results
            if self._is_failure(result)
        ]
        if not failure_records:
            return GovernorDecision()

        primary = failure_records[-1]
        subgoal = primary.subgoal
        self._attempts_by_subgoal[subgoal] = self._attempts_by_subgoal.get(subgoal, 0) + 1
        attempts = self._attempts_by_subgoal[subgoal]
        self._signature_attempts[primary.signature] = self._signature_attempts.get(primary.signature, 0) + 1
        signature_attempts = self._signature_attempts[primary.signature]

        facts = self._facts_by_subgoal.setdefault(subgoal, [])
        for fact in primary.facts:
            if fact not in facts:
                facts.append(fact)

        inject_capsule = False
        if signature_attempts >= self.duplicate_failure_threshold:
            self._blocked_strategies_by_subgoal.setdefault(subgoal, set()).add(primary.strategy)
            inject_capsule = True
        if attempts >= self.freeze_after_attempts:
            self._freeze_subgoals.add(subgoal)
            inject_capsule = True

        if not inject_capsule:
            return GovernorDecision()

        capsule = self._build_capsule(primary, attempts)
        capsule_key = f"{subgoal}:{primary.strategy}:{attempts}"
        if self._capsule_counts.get(capsule_key):
            return GovernorDecision()
        self._capsule_counts[capsule_key] = 1
        return GovernorDecision(
            inject_capsule=True,
            capsule=capsule,
            failure_pivot=FailurePivot(
                tool_name=primary.tool_name,
                repeated_pattern=self._repeated_pattern(primary),
                attempt_count=attempts,
                pivot_hint=capsule.next_safe_action,
            ),
        )

    def render_capsule(self, capsule: RecoveryCapsule) -> str:
        blocked = ", ".join(capsule.blocked_strategies) if capsule.blocked_strategies else "(none)"
        facts = "\n".join(f"- {item}" for item in capsule.facts) if capsule.facts else "- none yet"
        freeze_line = "yes" if capsule.freeze_mutation else "no"
        return (
            "Run Governor Recovery Capsule\n"
            f"Subgoal: {capsule.subgoal}\n"
            f"Attempts: {capsule.attempt_count}/{capsule.max_attempts}\n"
            f"Failure family: {capsule.failure_family}\n"
            f"Mutation frozen: {freeze_line}\n"
            f"Blocked strategies: {blocked}\n"
            "Facts:\n"
            f"{facts}\n"
            f"Next safe action: {capsule.next_safe_action}\n"
            f"Recovery note: {capsule.recovery_note}"
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "subgoal_attempts": dict(self._attempts_by_subgoal),
            "blocked_strategies": {key: sorted(values) for key, values in self._blocked_strategies_by_subgoal.items()},
            "facts": {key: list(values) for key, values in self._facts_by_subgoal.items()},
            "frozen_subgoals": sorted(self._freeze_subgoals),
            "pending_action_attempts": self._pending_action_attempts,
            "pending_action_signature": self._last_pending_action_signature,
            "pending_action_signature_repeats": self._pending_action_signature_repeats,
            "recovery_attempts": dict(self._recovery_attempts_by_subgoal),
        }

    def observe_pending_action(
        self,
        *,
        last_text: str = "",
        invalid_xml_fallback: bool = False,
        last_user_prompt: str = "",
    ) -> GovernorDecision:
        self._pending_action_attempts += 1
        intent_signature = self._normalize_pending_action_signature(last_text)
        if intent_signature and intent_signature == self._last_pending_action_signature:
            self._pending_action_signature_repeats += 1
        elif intent_signature:
            self._last_pending_action_signature = intent_signature
            self._pending_action_signature_repeats = 1
        else:
            self._last_pending_action_signature = ""
            self._pending_action_signature_repeats = 0

        if intent_signature and self._pending_action_signature_repeats >= self.repeated_intent_threshold:
            next_required_action = (
                "Stop restating the same planned action. Either emit the concrete next tool call now, or state the exact blocker or missing input instead of repeating intent."
            )
            blocker_message = (
                f"Blocked: run kept restating the same planned action without acting on it. Intent signature: {intent_signature}. "
                f"Repeated {self._pending_action_signature_repeats} times. Last text: {last_text or 'Unavailable.'} "
                f"Next required action: {next_required_action}"
            )
            return GovernorDecision(
                terminal_reason="repeated_intent_blocked",
                terminal_error=blocker_message,
                terminal_payload={
                    "intent_signature": intent_signature,
                    "repeat_count": self._pending_action_signature_repeats,
                    "last_text": last_text,
                    "next_required_action": next_required_action,
                },
            )

        if self._pending_action_attempts <= self.max_pending_action_attempts:
            route_hint = self._pending_action_hint_for_prompt(last_user_prompt)
            content = (
                "You described an action but did not generate a usable tool call. "
                "Emit exactly one real tool call for the immediate next action and nothing else.\n"
                "If you use XML fallback, use exactly one real block such as <exec>REAL_COMMAND</exec>.\n"
                "Do not narrate, summarize, or emit placeholders.\n"
                "Do not use XML attributes like command= or workdir=."
            )
            if invalid_xml_fallback:
                content += "\nThe last XML attempt was unusable. Use a real executable command body only."
            if route_hint:
                content += f"\n{route_hint}"
            return GovernorDecision(
                message_role="user",
                message_kind="text",
                message_content=content,
            )
        return GovernorDecision(
            terminal_reason="pending_action_giveup",
            terminal_payload={
                "retries": self._pending_action_attempts,
                "last_text": last_text,
            },
        )

    def reset_pending_action(self) -> None:
        self._pending_action_attempts = 0
        self._last_pending_action_signature = ""
        self._pending_action_signature_repeats = 0

    def _normalize_pending_action_signature(self, text: str) -> str:
        normalized = (text or "").lower()
        if not normalized.strip():
            return ""

        action_groups = [
            ("push", ("push", "pushing")),
            ("commit", ("commit", "committing")),
            ("check", ("check", "checking", "verify", "verifying", "confirm", "confirming")),
            ("inspect", ("inspect", "inspecting", "read", "reading", "search", "searching", "look", "looking", "debug", "debugging")),
            ("build", ("build", "building")),
            ("fix", ("fix", "fixing", "repair", "repairing")),
            ("run", ("run", "running", "test", "testing", "pytest", "lint", "building")),
            ("add", ("add", "adding")),
        ]
        for root, forms in action_groups:
            if any(re.search(rf"\b{re.escape(form)}\b", normalized) for form in forms):
                return root

        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", normalized)
            if token not in {
                "i", "im", "i'll", "ill", "will", "we", "were", "we'll", "well", "let", "me",
                "now", "to", "the", "a", "an", "again", "current", "state", "try", "trying",
                "going", "good", "status", "confirmed", "before", "after",
            }
        ]
        return " ".join(tokens[:2])

    def observe_recoverable_failures(
        self,
        calls_by_id: dict[str, ExecutorToolCall],
        batch_results: list[ToolResult],
        *,
        last_user_prompt: str = "",
    ) -> GovernorDecision:
        failure_records = [
            self._classify_failure(calls_by_id.get(result.call_id), result)
            for result in batch_results
            if self._is_recoverable_failure(result)
        ]
        if not failure_records:
            return GovernorDecision()

        primary = failure_records[-1]
        subgoal = primary.subgoal
        self._recovery_attempts_by_subgoal[subgoal] = self._recovery_attempts_by_subgoal.get(subgoal, 0) + 1
        attempts = self._recovery_attempts_by_subgoal[subgoal]
        if attempts > self.max_recovery_attempts:
            return GovernorDecision()

        return GovernorDecision(
            message_role="user",
            message_kind="text",
            message_content=self._recovery_prompt_for_record(primary, attempts, last_user_prompt=last_user_prompt),
        )

    def reset_recovery_attempts(self) -> None:
        self._recovery_attempts_by_subgoal.clear()

    def _is_failure(self, result: ToolResult) -> bool:
        status = result.status.value if hasattr(result.status, "value") else result.status
        return status not in {"success", "ask_pending"}

    def _is_recoverable_failure(self, result: ToolResult) -> bool:
        category = self._classify_category(result, self._detail(result))
        return category not in {"permission_error", "timeout"}

    def _classify_failure(self, call: ExecutorToolCall | None, result: ToolResult) -> FailureRecord:
        detail = self._detail(result)
        category = self._classify_category(result, detail)
        subgoal = self._derive_subgoal(call)
        strategy = self._derive_strategy(call)
        command = self._command(call)
        signature = self._semantic_signature(category, subgoal, strategy, detail, command)
        facts = self._facts_for(category, subgoal, strategy, detail, command)
        return FailureRecord(
            call_id=result.call_id,
            tool_name=result.tool_name,
            category=category,
            subgoal=subgoal,
            strategy=strategy,
            signature=signature,
            detail=detail,
            command=command,
            facts=facts,
        )

    def _detail(self, result: ToolResult) -> str:
        return "\n".join(
            part for part in (str(result.content or "").strip(), str(result.error or "").strip()) if part
        ).strip()

    def _classify_category(self, result: ToolResult, detail: str) -> str:
        lowered = detail.lower()
        status = result.status.value if hasattr(result.status, "value") else result.status
        if status == ExecutionStatus.PERMISSION_DENIED:
            return "permission_error"
        if "timed out" in lowered or "timeout" in lowered:
            return "timeout"
        if any(
            token in lowered
            for token in (
                "[winerror 2]",
                "the system cannot find the file specified",
                "is not recognized as",
                "command not found",
                "no module named",
                "not installed",
                "executable file not found",
            )
        ):
            return "command_not_found"
        if any(token in lowered for token in ("unknown command", "invalid choice", "no such command", "bad command")):
            return "bad_subcommand"
        if any(token in lowered for token in ("unexpected argument", "got unexpected extra argument", "unrecognized arguments", "too few arguments", "argument order")):
            return "bad_argument_order"
        if any(token in lowered for token in ("syntax error", "unterminated", "unclosed quotation", "quoted string", "near \"", "near '")):
            return "quoting_error"
        if any(token in lowered for token in ("path", "does not exist", "unable to open database", "no such table", "no such file")):
            return "path_error"
        if any(token in lowered for token in ("nothing to commit", "merge conflict", "failed to push some refs", "mutation failed")):
            return "mutation_failed"
        if not detail:
            return "empty_result"
        if status == ExecutionStatus.EXECUTION_ERROR:
            return "semantic_unexpected_output"
        return "unknown"

    def _derive_subgoal(self, call: ExecutorToolCall | None) -> str:
        if call is None:
            return "unknown"
        if call.name != "exec":
            return call.name
        command = self._command(call).lower()
        if any(token in command for token in ("sqlite-utils", "sqlite_utils", "sqlite3")):
            return "inspect_sqlite_db"
        if command.startswith("git push"):
            return "git_push_remote"
        if command.startswith("gh auth"):
            return "github_cli_auth"
        if command.startswith("git "):
            return "git_repo_operation"
        return f"exec:{command.split(' ', 1)[0] if command else 'unknown'}"

    def _derive_strategy(self, call: ExecutorToolCall | None) -> str:
        if call is None:
            return "unknown"
        if call.name != "exec":
            return call.name
        command = self._command(call).lower()
        if command.startswith("sqlite3"):
            return "native_sqlite3_cli"
        if "sqlite-utils" in command or "sqlite_utils" in command:
            return "sqlite_utils_cli"
        if "python" in command and "sqlite3" in command:
            return "python_sqlite3"
        if command.startswith("gh auth"):
            return "gh_auth"
        if command.startswith("git push"):
            return "git_push"
        if command.endswith("--help") or " help" in command:
            return "help_inspection"
        return "generic_exec"

    def _semantic_signature(
        self,
        category: str,
        subgoal: str,
        strategy: str,
        detail: str,
        command: str,
    ) -> str:
        lowered = detail.lower()
        if "near \"tables\"" in lowered or "near 'tables'" in lowered:
            marker = "near_tables_syntax"
        elif "host key verification failed" in lowered:
            marker = "host_key_verification_failed"
        elif "permission denied (publickey)" in lowered:
            marker = "permission_denied_publickey"
        elif "no module named" in lowered:
            marker = "python_module_missing"
        elif "is not recognized as" in lowered:
            marker = "windows_command_missing"
        elif "unknown command" in lowered:
            marker = "unknown_command"
        elif "unrecognized arguments" in lowered:
            marker = "unrecognized_arguments"
        elif "unable to open database" in lowered:
            marker = "unable_to_open_database"
        else:
            marker = category
        command_root = command.strip().split(" ", 1)[0].lower() if command else "unknown"
        return f"{subgoal}|{strategy}|{command_root}|{marker}"

    def _facts_for(self, category: str, subgoal: str, strategy: str, detail: str, command: str) -> list[str]:
        lowered = detail.lower()
        facts: list[str] = []
        if subgoal == "inspect_sqlite_db":
            if strategy == "native_sqlite3_cli" and category == "command_not_found":
                facts.append("sqlite3 CLI appears missing or unavailable on PATH.")
            if strategy == "sqlite_utils_cli" and ("near \"tables\"" in lowered or "near 'tables'" in lowered):
                facts.append("sqlite-utils table-listing attempt produced SQL-style syntax failure.")
            if "sqlite-utils" in command.lower():
                facts.append("sqlite-utils strategy has already been attempted.")
        if category == "command_not_found" and command:
            facts.append(f"Executable or entrypoint missing for command root '{command.split(' ', 1)[0]}'.")
        if category == "permission_error":
            facts.append("Permission or policy blocked the attempted action.")
        if "host key verification failed" in lowered:
            facts.append("Remote SSH host key trust is not established for github.com.")
        if "permission denied (publickey)" in lowered:
            facts.append("GitHub SSH authentication is failing with the current key setup.")
        if "failed to push some refs" in lowered:
            facts.append("Remote rejected the push; local and remote history may have diverged.")
        return facts

    def _build_capsule(self, record: FailureRecord, attempts: int) -> RecoveryCapsule:
        blocked_strategies = sorted(self._blocked_strategies_by_subgoal.get(record.subgoal, set()))
        facts = list(self._facts_by_subgoal.get(record.subgoal, []))
        next_safe_action = self._next_safe_action(record, blocked_strategies, attempts)
        recovery_note = self._recovery_note(record, blocked_strategies)
        return RecoveryCapsule(
            subgoal=record.subgoal,
            attempt_count=attempts,
            max_attempts=self.max_attempts_per_subgoal,
            failure_family=record.category,
            blocked_strategies=blocked_strategies,
            facts=facts,
            next_safe_action=next_safe_action,
            recovery_note=recovery_note,
            freeze_mutation=record.subgoal in self._freeze_subgoals,
        )

    def _next_safe_action(self, record: FailureRecord, blocked_strategies: list[str], attempts: int) -> str:
        if record.subgoal == "inspect_sqlite_db":
            if "python_sqlite3" not in blocked_strategies and attempts >= 2:
                return "Switch method. Use Python's built-in sqlite3 module to verify the DB path and inspect sqlite_master instead of retrying another sqlite-utils variant."
            if "help_inspection" not in blocked_strategies:
                return "Inspect the installed CLI help for the exact syntax or confirm the environment before issuing another database command."
        if record.subgoal == "git_push_remote":
            return "Do not vary the push command blindly. Inspect the exact remote/auth error and resolve that blocker before retrying the same push."
        if record.category == "command_not_found":
            return "Stop improvising command variants. Verify the executable/help surface or switch to a known built-in fallback method."
        return "Inspect help/environment evidence first, then issue one corrected command using a different strategy family."

    def _recovery_note(self, record: FailureRecord, blocked_strategies: list[str]) -> str:
        blocked = ", ".join(blocked_strategies) if blocked_strategies else "none yet"
        return (
            f"Previous attempts failed around strategy '{record.strategy}'. "
            f"Blocked strategies for this subgoal: {blocked}. "
            "Do not repeat blocked command families. Treat this capsule as guidance, not an exact command prescription."
        )

    def _repeated_pattern(self, record: FailureRecord) -> str:
        return record.signature.replace("|", ":")

    def _recovery_prompt_for_record(self, record: FailureRecord, attempts: int, *, last_user_prompt: str = "") -> str:
        detail = record.detail
        detail_lower = detail.lower()
        prompt_lower = (last_user_prompt or "").lower()
        detail_limit = 400 + (attempts - 1) * 200
        detail_excerpt = detail[:detail_limit]

        if record.category == "command_not_found":
            if any(
                phrase in prompt_lower
                for phrase in (
                    "verify whether",
                    "whether `",
                    "whether '",
                    "whether \"",
                    "whether ",
                    "exists",
                    "unavailable",
                    "no broader fix",
                )
            ):
                return (
                    "The last exec call confirmed the requested command is unavailable on this system. "
                    "Do not broaden into installation, PATH edits, or replacement scripts. "
                    "Answer the user directly in one sentence. "
                    "Use the word 'unavailable' verbatim and the phrase 'no broader fix should be attempted' verbatim."
                )
            if "sqlite" in detail_lower:
                return (
                    "The last exec call failed because the sqlite CLI is missing or not on PATH. "
                    "Install or expose the real sqlite command first, then retry the query. "
                    "Do not replace this with a one-off Python sqlite script. "
                    "Execute the next corrective tool call now; do not narrate. "
                    "Suggested command: winget install --exact --id SQLite.SQLite"
                )
            return (
                f"The last {record.tool_name} call failed because the requested executable is missing or not on PATH. "
                "Install the needed CLI or fix PATH first, then retry the original command. "
                "Do not replace a standard CLI task with an ad hoc Python script unless the user explicitly asked for that workaround. "
                "Execute the next corrective tool call now; do not narrate.\n"
                f"Error detail:\n{detail_excerpt}"
            )

        if "host key verification failed" in detail_lower:
            if attempts >= 2:
                return (
                    "The same SSH host-key failure happened again after an attempted fix. "
                    "Do not repeat the same push. Verify SSH connectivity directly before another push attempt. "
                    "Execute the next corrective tool call now; do not narrate. Suggested command: ssh -vT git@github.com"
                )
            return (
                "The last exec call failed because github.com is not trusted in known_hosts yet. "
                "Fix that first, then retry the same git push. Execute the next corrective tool call now; do not narrate. "
                "Suggested command: ssh-keyscan github.com >> ~/.ssh/known_hosts"
            )

        if "malformed tool arguments" in detail_lower or "json parse failed" in detail_lower:
            return (
                f"The last {record.tool_name} call failed because its arguments were malformed. "
                "Re-ground on the tool schema and emit one corrected call. "
                "Do not repeat the same malformed argument shape.\n"
                f"Error detail:\n{detail_excerpt}"
            )

        if "missing required parameter" in detail_lower:
            if record.tool_name == "edit":
                return (
                    "The last edit call used the wrong argument shape. "
                    "Retry edit with the required schema exactly: "
                    '{"path":"...","edits":[{"oldText":"...","newText":"..."}]}. '
                    "Use one corrected edit call now; do not narrate or switch tools unless edit fails again for a new reason.\n"
                    f"Error detail:\n{detail_excerpt}"
                )
            return (
                f"The last {record.tool_name} call omitted a required parameter. "
                "Retry the same tool with a corrected argument object that satisfies the schema. "
                "Use one corrected tool call now; do not narrate.\n"
                f"Error detail:\n{detail_excerpt}"
            )

        if record.category == "bad_subcommand":
            return (
                "The last command used the wrong subcommand or CLI surface. "
                "Inspect the installed help output first, then issue one corrected command. "
                "Do not repeat the same command family without re-grounding.\n"
                f"Error detail:\n{detail_excerpt}"
            )

        if record.category in {"bad_argument_order", "quoting_error"}:
            return (
                "The last command shape was malformed. "
                "Re-ground from the help output or a simpler command form before retrying. "
                "Do not repeat the same failing syntax pattern.\n"
                f"Error detail:\n{detail_excerpt}"
            )

        if record.category == "path_error":
            if "/api/sessions/" in prompt_lower and "/runs/" in prompt_lower and "/execute" in prompt_lower:
                return (
                    "The last search drifted into a bad path or caller-only search. "
                    "Stop broad grep retries and inspect the likely route file directly. "
                    "Answer from the concrete server handler in api/api/routes.py, specifically the execute_run route."
                )
            return (
                "The last command failed around path or target resolution. "
                "Verify the path and inspect the environment before retrying with a new command form.\n"
                f"Error detail:\n{detail_excerpt}"
            )

        return (
            f"The last {record.tool_name} call failed and needs a different strategy. "
            "Inspect help/environment evidence first, then issue one corrected command. "
            "Do not repeat the same failing command family.\n"
            f"Error detail:\n{detail_excerpt}"
        )

    def _pending_action_hint_for_prompt(self, prompt: str) -> str:
        normalized = (prompt or "").lower()
        if "/api/sessions/" in normalized and "/runs/" in normalized and "/execute" in normalized:
            return (
                "This request is about the concrete API execute route. "
                "Inspect the likely route file directly before more broad searching. "
                "Preferred next step: open api/api/routes.py and locate the execute_run handler."
            )
        if (
            "research" in normalized
            or "summarize" in normalized
            or "source" in normalized
            or "sources" in normalized
        ):
            return (
                "This is a research-and-synthesis request, not an open-ended exploration task. "
                "If you already inspected relevant local sources, stop searching and answer now with the requested findings. "
                "Name the concrete capabilities or gap, and cite the file paths you used."
            )
        if (
            "continue" in normalized
            and "plan" in normalized
        ) or "what should we do first" in normalized:
            return (
                "This is a continuity request, not a fresh discovery task. "
                "Carry forward the plan you already established and answer the user's direct question now. "
                "State the first move plainly instead of reopening the repo tour."
            )
        return ""

    def _call_mutates_state(self, call: ExecutorToolCall) -> bool:
        if call.name in {"write", "edit", "apply_patch"}:
            return True
        if call.name != "exec":
            return False
        command = self._command(call).lower()
        return bool(re.search(r"^\s*(git\s+(add|commit|amend|merge|rebase|reset|restore|checkout|switch|stash)|rm\b|move\b|mv\b)", command))

    def _command(self, call: ExecutorToolCall | None) -> str:
        if call is None:
            return ""
        command = call.arguments.get("command")
        return str(command or "").strip() if isinstance(command, str) else ""
