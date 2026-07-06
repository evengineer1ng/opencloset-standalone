from __future__ import annotations

from api.agent.governor import RunGovernor
from api.tools.executor import ExecutionStatus, ToolCall, ToolResult


def _exec_call(call_id: str, command: str) -> ToolCall:
    return ToolCall(call_id=call_id, name="exec", arguments={"command": command})


def _failure(call_id: str, content: str, *, error: str = "exec exited with code 1") -> ToolResult:
    return ToolResult(
        call_id=call_id,
        tool_name="exec",
        status=ExecutionStatus.EXECUTION_ERROR,
        content=content,
        error=error,
    )


class TestRunGovernor:
    def test_duplicate_sqlite_utils_failures_trigger_capsule_and_block_strategy(self):
        governor = RunGovernor(max_attempts_per_subgoal=4, duplicate_failure_threshold=2, freeze_after_attempts=3)
        call = _exec_call("c1", 'sqlite-utils "D:\\openclaw\\opencloset\\hockey\\hockey_lab.sqlite" tables')
        result = _failure("c1", 'Error: near "tables": syntax error')

        first = governor.observe_batch({"c1": call}, [result])
        second = governor.observe_batch({"c1": call}, [result])

        assert first.inject_capsule is False
        assert second.inject_capsule is True
        assert second.capsule is not None
        assert second.capsule.subgoal == "inspect_sqlite_db"
        assert "sqlite_utils_cli" in second.capsule.blocked_strategies
        assert "Python's built-in sqlite3 module" in second.capsule.next_safe_action

        allowed, blocked = governor.filter_calls([call])
        assert allowed == []
        assert len(blocked) == 1
        assert "blocked repeated failure strategy" in (blocked[0].error or "").lower()

    def test_freeze_mutation_blocks_write_after_repeated_failures(self):
        governor = RunGovernor(max_attempts_per_subgoal=4, duplicate_failure_threshold=2, freeze_after_attempts=3)
        call = _exec_call("c1", "sqlite3 hockey_lab.sqlite .tables")
        result = _failure("c1", "sqlite3 is not recognized as an internal or external command")

        governor.observe_batch({"c1": call}, [result])
        governor.observe_batch({"c1": call}, [result])
        third = governor.observe_batch({"c1": call}, [result])

        assert third.inject_capsule is True
        assert third.capsule is not None
        assert third.capsule.freeze_mutation is True

        write_call = ToolCall(call_id="w1", name="write", arguments={"path": "x.txt", "content": "hello"})
        allowed, blocked = governor.filter_calls([write_call])
        assert allowed == []
        assert len(blocked) == 1
        assert "froze mutation" in (blocked[0].error or "").lower()

    def test_snapshot_preserves_discoveries_and_blocked_strategies(self):
        governor = RunGovernor()
        call = _exec_call("c1", "git push origin master")
        result = _failure(
            "c1",
            "Exit code: 1\nOutput:\nHost key verification failed.\nfatal: Could not read from remote repository.\n",
        )

        governor.observe_batch({"c1": call}, [result])
        governor.observe_batch({"c1": call}, [result])
        snapshot = governor.snapshot()

        assert snapshot["subgoal_attempts"]["git_push_remote"] == 2
        assert "git_push" in snapshot["blocked_strategies"]["git_push_remote"]
        assert any("host key trust" in fact.lower() for fact in snapshot["facts"]["git_push_remote"])

    def test_missing_command_probe_recovers_with_direct_answer_instead_of_install(self):
        governor = RunGovernor()
        call = _exec_call("c1", "definitely_not_a_real_command_xyz --version")
        result = _failure(
            "c1",
            "",
            error="Error executing command: [WinError 2] The system cannot find the file specified",
        )

        decision = governor.observe_recoverable_failures(
            {"c1": call},
            [result],
            last_user_prompt="First verify whether `definitely_not_a_real_command_xyz --version` exists. Then recover and answer in one sentence that the command is unavailable and no broader fix should be attempted.",
        )

        assert "command is unavailable" in decision.message_content
        assert "no broader fix should be attempted" in decision.message_content
        assert "Install the needed CLI" not in decision.message_content

    def test_repo_execute_prompt_gets_direct_route_hint_for_pending_action(self):
        governor = RunGovernor(max_pending_action_attempts=1)

        decision = governor.observe_pending_action(
            last_text="Let me search for the route handler.",
            last_user_prompt="Find the file and function that handle the synchronous POST /api/sessions/<session_id>/runs/<run_id>/execute path, then answer with the file path and function name only.",
        )

        assert "api/api/routes.py" in decision.message_content
        assert "execute_run" in decision.message_content
