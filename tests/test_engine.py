from __future__ import annotations

import os
import tempfile

from api.agent.engine import ConversationRuntime, Message
from api.api.app import create_app


class TestConversationRuntimeToolTracking:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)

        self.app.db.execute(
            "INSERT INTO sessions (id, label, model, provider, status, context_window, token_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("session-1", "test", "test-model", "llamacpp", "active", 32768, 0),
        )
        self.app.db.commit()

        self.runtime = ConversationRuntime(
            session_id="session-1",
            model="test-model",
            provider="llamacpp",
            context_window=32768,
            db_conn=self.app.db,
            transcript_manager=self.app.transcript,
            event_logger=self.app.event_logger,
        )

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_complete_tool_call_clears_run_tracking(self):
        self.runtime.begin_run()
        self.runtime.register_tool_call("tool-1")

        self.runtime.complete_tool_call("tool-1")

        assert self.runtime.active_tools == []
        assert self.runtime.current_run is not None
        assert self.runtime.current_run.tool_calls == []

    def test_interrupt_run_only_synthesizes_unfinished_tools(self):
        self.runtime.begin_run()
        self.runtime.register_tool_call("tool-finished")
        self.runtime.register_tool_call("tool-pending")
        self.runtime.complete_tool_call("tool-finished")

        self.runtime.interrupt_run()

        tool_messages = [msg for msg in self.runtime.messages if msg.role == "tool"]
        assert len(tool_messages) == 1
        assert "tool-pending" in tool_messages[0].content
        assert "tool-finished" not in tool_messages[0].content

    def test_nonpersistent_user_message_stays_out_of_transcript(self):
        self.runtime.begin_run()

        msg = Message(
            role="user",
            content="internal coaching",
            token_estimate=3,
            persistent=False,
        )
        self.runtime.add_message(msg)

        transcript_rows = self.app.db.execute(
            "SELECT content FROM messages WHERE session_id = ? ORDER BY position ASC",
            (self.runtime.session_id,),
        ).fetchall()

        assert [row["content"] for row in transcript_rows] == []
        assert self.runtime.messages[-1].content == "internal coaching"