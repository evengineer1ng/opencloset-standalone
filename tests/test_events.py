from api.api.events import (
    EVENT_ACTION_PROGRESS_BLOCKED,
    EVENT_ASSISTANT_FINAL,
    EVENT_PENDING_ACTION_GIVEUP,
    EVENT_PROMPT_UNANSWERED,
    EVENT_REPEATED_INTENT_BLOCKED,
    EVENT_PROVIDER_STREAM_TIMEOUT,
    EVENT_SUBPROCESS_KILLED,
    EVENT_TOOL_FAILURE_PIVOT,
    EVENT_TOOL_RESULT,
    StreamEvent,
)


class TestStreamEvent:
    def test_assistant_final_event_shape(self):
        event = StreamEvent.assistant_final(
            status="blocked",
            finish_reason="prompt_unanswered",
            final_text="",
            transient_text="I need to inspect the code before answering.",
            transcript_persisted=False,
        )

        assert event.type == EVENT_ASSISTANT_FINAL
        assert event.data == {
            "status": "blocked",
            "finish_reason": "prompt_unanswered",
            "final_text": "",
            "transient_text": "I need to inspect the code before answering.",
            "transcript_persisted": False,
        }

    def test_tool_result_includes_error_code_when_present(self):
        event = StreamEvent.tool_result(
            "call_1",
            "exec",
            "execution_error",
            "Exit code: 1\nOutput:\nboom",
            error="exit 1:\nboom",
            error_code="exec.exit_nonzero",
        )

        assert event.type == EVENT_TOOL_RESULT
        assert event.data["tool_id"] == "call_1"
        assert event.data["error_code"] == "exec.exit_nonzero"

    def test_provider_stream_timeout_event_shape(self):
        event = StreamEvent.provider_stream_timeout(130.0, 120.0, last_event_type="assistant_delta")

        assert event.type == EVENT_PROVIDER_STREAM_TIMEOUT
        assert event.data == {
            "elapsed_s": 130.0,
            "threshold_s": 120.0,
            "last_event_type": "assistant_delta",
        }

    def test_subprocess_killed_event_shape(self):
        event = StreamEvent.subprocess_killed("sess_1", 4242, "python hang.py", "interrupt")

        assert event.type == EVENT_SUBPROCESS_KILLED
        assert event.data["session_id"] == "sess_1"
        assert event.data["pid"] == 4242
        assert event.data["reason"] == "interrupt"

    def test_tool_failure_pivot_event_shape(self):
        event = StreamEvent.tool_failure_pivot("exec", "sqlite_sql_syntax_error", 3, "Use python -c import sqlite3")

        assert event.type == EVENT_TOOL_FAILURE_PIVOT
        assert event.data["tool_name"] == "exec"
        assert event.data["repeated_pattern"] == "sqlite_sql_syntax_error"
        assert event.data["attempt_count"] == 3

    def test_pending_action_giveup_event_shape(self):
        event = StreamEvent.pending_action_giveup(2, "Now pushing to origin.")

        assert event.type == EVENT_PENDING_ACTION_GIVEUP
        assert event.data == {
            "retries": 2,
            "last_text": "Now pushing to origin.",
        }

    def test_repeated_intent_blocked_event_shape(self):
        event = StreamEvent.repeated_intent_blocked(
            message="Blocked: run kept restating the same planned action without acting on it.",
            intent_signature="push",
            repeat_count=2,
            last_text="Trying to push again.",
            next_required_action="Stop restating the same planned action.",
        )

        assert event.type == EVENT_REPEATED_INTENT_BLOCKED
        assert event.data["intent_signature"] == "push"
        assert event.data["repeat_count"] == 2
        assert event.data["last_text"] == "Trying to push again."

    def test_action_progress_blocked_event_shape(self):
        event = StreamEvent.action_progress_blocked(
            message="Blocked: implementation run exhausted discovery budget.",
            discovery_steps=5,
            discovery_budget=5,
            files_read=2,
            symbols_found=4,
            files_modified=0,
            tests_run=0,
            artifacts_created=0,
            evidence=["file_search on app.py -> success", "read_file on api/api/app.py -> success"],
            next_required_action="Either patch the located title hook or declare the missing seam explicitly.",
        )

        assert event.type == EVENT_ACTION_PROGRESS_BLOCKED
        assert event.data["discovery_steps"] == 5
        assert event.data["files_read"] == 2
        assert event.data["symbols_found"] == 4
        assert event.data["next_required_action"].startswith("Either patch")

    def test_prompt_unanswered_event_shape(self):
        event = StreamEvent.prompt_unanswered(
            message="Blocked: run ended without a clear answer to the last user prompt.",
            reason="transient_progress_only",
            last_user_prompt="fix the report",
            assistant_text="I need to inspect the code before answering.",
            action_mode=True,
            files_modified=0,
            tests_run=0,
            artifacts_created=0,
            next_required_action="Answer the user's last prompt directly.",
        )

        assert event.type == EVENT_PROMPT_UNANSWERED
        assert event.data["reason"] == "transient_progress_only"
        assert event.data["action_mode"] is True
        assert event.data["last_user_prompt"] == "fix the report"
        assert event.data["assistant_text"].startswith("I need to inspect")