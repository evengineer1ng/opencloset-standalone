from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

from api.api.app import create_app


def _old_timestamp(minutes: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TestMaintenanceWorker:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.maintenance_worker.stop_background(timeout=0.1)
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_idle_session(self, content: str | list[str] = "hello world") -> tuple[str, str]:
        contents = [content] if isinstance(content, str) else list(content)
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "maintenance"},
        )
        session_id = session_resp.get_json()["id"]
        run_row = None
        for item in contents:
            self.client.post(f"/api/sessions/{session_id}/messages", json={"content": item})
            run_row = self.app.db.execute(
                "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (run_row["id"],))
        self.app.db.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (_old_timestamp(), session_id),
        )
        self.app.db.commit()
        return session_id, run_row["id"]

    def test_poll_once_creates_micro_summary_artifact_for_idle_session(self):
        session_id, _ = self._create_idle_session("summarize this exchange")
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.update_active_goal(session_id, "Keep progress stable")
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume the exchange", status="doing")

        result = self.app.maintenance_worker.poll_once()
        artifacts = self.app.maintenance.list_artifacts(session_id)
        artifact_types = {artifact["artifact_type"] for artifact in artifacts}

        assert result.checked_sessions == 1
        assert len(result.created_artifacts) == 3
        assert artifact_types == {"micro-summary", "compaction-marker", "handoff-candidate"}
        micro_summary = next(artifact for artifact in artifacts if artifact["artifact_type"] == "micro-summary")
        compaction_marker = next(artifact for artifact in artifacts if artifact["artifact_type"] == "compaction-marker")
        handoff_candidate = next(artifact for artifact in artifacts if artifact["artifact_type"] == "handoff-candidate")
        assert micro_summary["status"] == "valid"
        assert "Idle micro-summary:" in micro_summary["content"]
        assert "summarize this exchange" in micro_summary["content"]
        assert compaction_marker["metadata"]["covered_ranges"] == [
            {
                "artifact_type": "micro-summary",
                "start_position": 1,
                "end_position": 1,
                "source_ranges": [
                    {"artifact_type": "micro-summary", "artifact_id": micro_summary["id"], "start_position": 1, "end_position": 1},
                ],
            },
        ]
        assert handoff_candidate["status"] == "valid"
        assert "Prepared handoff candidate:" in handoff_candidate["content"]
        assert "Resume the exchange" in handoff_candidate["content"]
        assert "Idle micro-summary:" in handoff_candidate["content"]

    def test_poll_once_skips_session_with_active_run(self):
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "busy"},
        )
        session_id = session_resp.get_json()["id"]
        self.client.post(f"/api/sessions/{session_id}/messages", json={"content": "do work"})
        self.app.db.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (_old_timestamp(), session_id),
        )
        self.app.db.commit()

        result = self.app.maintenance_worker.poll_once()

        assert result.checked_sessions == 1
        assert self.app.maintenance.list_artifacts(session_id) == []

    def test_draft_artifact_does_not_replace_latest_valid_artifact_until_promoted(self):
        session_id, _ = self._create_idle_session("draft lifecycle target")
        valid_summary = self.app.maintenance.create_artifact(
            session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: original valid summary",
            start_position=1,
            end_position=1,
        )

        draft_summary = self.app.maintenance.create_draft_artifact(
            session_id,
            "micro-summary",
            metadata={"message_count": 2},
            start_position=1,
            end_position=2,
        )

        latest_valid_before = self.app.maintenance.get_latest_valid_artifact(session_id, "micro-summary")
        promoted_summary = self.app.maintenance.finalize_draft_artifact(
            draft_summary["id"],
            content="Idle micro-summary:\n- user: promoted summary",
            metadata={"message_count": 2},
            start_position=1,
            end_position=2,
        )
        artifacts = self.app.maintenance.list_artifacts(session_id, artifact_type="micro-summary")

        assert draft_summary["status"] == "draft"
        assert latest_valid_before["id"] == valid_summary["id"]
        assert promoted_summary["status"] == "valid"
        assert promoted_summary["id"] == draft_summary["id"]
        stale_original = next(artifact for artifact in artifacts if artifact["id"] == valid_summary["id"])
        assert stale_original["status"] == "stale"

    def test_segment_draft_promotion_only_stales_overlapping_valid_segments(self):
        session_id, _ = self._create_idle_session("segment draft target")
        first_segment = self.app.maintenance.create_artifact(
            session_id,
            "segment-summary",
            "Segment summary (messages 1-2)",
            start_position=1,
            end_position=2,
        )
        second_segment = self.app.maintenance.create_artifact(
            session_id,
            "segment-summary",
            "Segment summary (messages 7-8)",
            start_position=7,
            end_position=8,
        )
        draft_segment = self.app.maintenance.create_draft_artifact(
            session_id,
            "segment-summary",
            start_position=2,
            end_position=5,
        )

        promoted = self.app.maintenance.finalize_draft_artifact(
            draft_segment["id"],
            content="Segment summary (messages 2-5)",
            start_position=2,
            end_position=5,
        )
        artifacts = self.app.maintenance.list_artifacts(session_id, artifact_type="segment-summary")
        by_id = {artifact["id"]: artifact for artifact in artifacts}

        assert promoted["status"] == "valid"
        assert by_id[first_segment["id"]]["status"] == "stale"
        assert by_id[second_segment["id"]]["status"] == "valid"

    def test_poll_once_keeps_failed_micro_summary_as_draft(self, monkeypatch):
        session_id, _ = self._create_idle_session("failure leaves draft")

        def fail_summary(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(self.app.maintenance_worker, "_summarize_content", fail_summary)

        result = self.app.maintenance_worker.poll_once()
        summaries = self.app.maintenance.list_artifacts(session_id, artifact_type="micro-summary")

        assert not any(artifact.artifact_type == "micro-summary" for artifact in result.created_artifacts)
        assert len(summaries) == 1
        assert summaries[0]["status"] == "draft"
        assert self.app.maintenance.get_latest_valid_artifact(session_id, "micro-summary") is None

    def test_poll_once_stops_mid_pass_when_new_activity_arrives(self, monkeypatch):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")

        original_create_micro_summary = self.app.maintenance_worker._create_micro_summary

        def create_micro_summary_and_interrupt(target_session_id, end_position):
            artifact = original_create_micro_summary(target_session_id, end_position)
            self.client.post(f"/api/sessions/{target_session_id}/messages", json={"content": "fresh activity"})
            return artifact

        monkeypatch.setattr(
            self.app.maintenance_worker,
            "_create_micro_summary",
            create_micro_summary_and_interrupt,
        )

        result = self.app.maintenance_worker.poll_once()
        artifacts = self.app.maintenance.list_artifacts(session_id)
        artifact_types = {artifact["artifact_type"] for artifact in artifacts}

        assert any(artifact.artifact_type == "micro-summary" for artifact in result.created_artifacts)
        assert artifact_types == {"micro-summary"}
        assert self.app.run_manager.get_active_run(session_id) is not None

    def test_poll_once_skips_work_when_provider_is_saturated(self):
        session_id, _ = self._create_idle_session("saturated provider target")
        self.app.provider_saturation_checker = lambda: True

        result = self.app.maintenance_worker.poll_once()

        assert result.checked_sessions == 1
        assert result.created_artifacts == []
        assert self.app.maintenance.list_artifacts(session_id) == []

    def test_poll_once_stops_mid_pass_when_provider_becomes_saturated(self, monkeypatch):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")
        saturated = {"value": False}
        self.app.provider_saturation_checker = lambda: saturated["value"]

        original_create_micro_summary = self.app.maintenance_worker._create_micro_summary

        def create_micro_summary_then_saturate(target_session_id, end_position):
            artifact = original_create_micro_summary(target_session_id, end_position)
            saturated["value"] = True
            return artifact

        monkeypatch.setattr(
            self.app.maintenance_worker,
            "_create_micro_summary",
            create_micro_summary_then_saturate,
        )

        result = self.app.maintenance_worker.poll_once()
        artifacts = self.app.maintenance.list_artifacts(session_id)
        artifact_types = {artifact["artifact_type"] for artifact in artifacts}

        assert any(artifact.artifact_type == "micro-summary" for artifact in result.created_artifacts)
        assert artifact_types == {"micro-summary"}

    def test_new_valid_micro_summary_stales_previous_one(self):
        session_id, _ = self._create_idle_session("first summary target")
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Follow up later", status="doing")

        first_result = self.app.maintenance_worker.poll_once()
        first_summary = next(artifact for artifact in first_result.created_artifacts if artifact.artifact_type == "micro-summary")
        first_handoff = next(artifact for artifact in first_result.created_artifacts if artifact.artifact_type == "handoff-candidate")

        self.client.post(f"/api/sessions/{session_id}/messages", json={"content": "second summary target"})
        run_row = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (run_row["id"],))
        self.app.db.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ? AND position = (SELECT MAX(position) FROM messages WHERE session_id = ?)",
            (_old_timestamp(), session_id, session_id),
        )
        self.app.db.commit()

        second_result = self.app.maintenance_worker.poll_once()
        artifacts = self.app.maintenance.list_artifacts(session_id)
        valid_artifacts = {artifact["artifact_type"]: artifact for artifact in artifacts if artifact["status"] == "valid"}
        stale_artifacts = {
            artifact["artifact_type"]: artifact
            for artifact in artifacts
            if artifact["status"] == "stale"
        }

        assert len(second_result.created_artifacts) == 3
        assert valid_artifacts["micro-summary"]["end_position"] > first_summary.end_position
        assert valid_artifacts["handoff-candidate"]["end_position"] > first_handoff.end_position
        assert stale_artifacts["micro-summary"]["id"] == first_summary.id
        assert stale_artifacts["compaction-marker"]["end_position"] == 1
        assert stale_artifacts["handoff-candidate"]["id"] == first_handoff.id

    def test_poll_once_backfills_handoff_candidate_from_current_summary(self):
        session_id, _ = self._create_idle_session("ready for handoff")
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after rollover", status="doing")

        summary = self.app.maintenance.create_artifact(
            session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: ready for handoff",
            start_position=1,
            end_position=2,
        )

        result = self.app.maintenance_worker.poll_once()
        markers = self.app.maintenance.list_artifacts(session_id, artifact_type="compaction-marker")
        handoff_candidates = self.app.maintenance.list_artifacts(session_id, artifact_type="handoff-candidate")

        assert len(result.created_artifacts) == 2
        assert {artifact.artifact_type for artifact in result.created_artifacts} == {"compaction-marker", "handoff-candidate"}
        assert markers[0]["metadata"]["covered_ranges"] == [
            {
                "artifact_type": "micro-summary",
                "start_position": 1,
                "end_position": 2,
                "source_ranges": [
                    {"artifact_type": "micro-summary", "artifact_id": summary["id"], "start_position": 1, "end_position": 2},
                ],
            },
        ]
        assert handoff_candidates[0]["metadata"]["summary_artifact_id"] == summary["id"]
        assert "Resume after rollover" in handoff_candidates[0]["content"]

    def test_poll_once_creates_decision_tool_digest_for_idle_session(self):
        session_id, run_id = self._create_idle_session("use a tool")
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.update_active_goal(session_id, "Keep tool context")
        self.app.planning.add_plan_item(session_id, plan["id"], "Review tool output", status="doing")
        self.app.transcript.log_tool_invocation(
            session_id,
            run_id,
            "read",
            input_data={"path": "/tmp/test.txt"},
            output_data={"content": "hello"},
            status="completed",
        )
        self.app.db.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (_old_timestamp(), session_id),
        )
        self.app.db.commit()

        result = self.app.maintenance_worker.poll_once()
        artifacts = self.app.maintenance.list_artifacts(session_id)
        digest = next(artifact for artifact in artifacts if artifact["artifact_type"] == "decision-tool-digest")

        assert any(artifact.artifact_type == "decision-tool-digest" for artifact in result.created_artifacts)
        assert digest["status"] == "valid"
        assert "Decision/tool digest:" in digest["content"]
        assert "Active goal: Keep tool context" in digest["content"]
        assert "Next action: Review tool output" in digest["content"]
        assert "Tool read [completed]:" in digest["content"]
        assert "/tmp/test.txt" in digest["content"]

    def test_poll_once_creates_segment_summary_for_older_messages(self):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")

        result = self.app.maintenance_worker.poll_once()
        artifacts = self.app.maintenance.list_artifacts(session_id)
        segment_summary = next(artifact for artifact in artifacts if artifact["artifact_type"] == "segment-summary")
        compaction_marker = next(artifact for artifact in artifacts if artifact["artifact_type"] == "compaction-marker")
        micro_summary = next(artifact for artifact in artifacts if artifact["artifact_type"] == "micro-summary")
        archive_candidates = self.app.maintenance.list_artifacts(session_id, artifact_type="transcript-archive-candidate")
        transcript_ranges = self.app.transcript.list_ranges(session_id, range_type="archive-ready")

        assert len(result.created_artifacts) == 4
        assert micro_summary["start_position"] == 3
        assert segment_summary["start_position"] == 1
        assert segment_summary["end_position"] == 2
        assert compaction_marker["start_position"] == 1
        assert compaction_marker["end_position"] == 8
        assert compaction_marker["metadata"]["source_artifact_type"] == "segment-summary"
        assert compaction_marker["metadata"]["covered_ranges"] == [
            {
                "artifact_types": ["segment-summary", "micro-summary"],
                "start_position": 1,
                "end_position": 8,
                "source_ranges": [
                    {"artifact_type": "segment-summary", "artifact_id": segment_summary["id"], "start_position": 1, "end_position": 2},
                    {"artifact_type": "micro-summary", "artifact_id": micro_summary["id"], "start_position": 3, "end_position": 8},
                ],
            },
        ]
        assert archive_candidates == []
        assert "Segment summary (messages 1-2):" in segment_summary["content"]
        assert "message 1" in segment_summary["content"]
        assert "message 2" in segment_summary["content"]

    def test_poll_once_skips_current_segment_summary(self):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")

        first_result = self.app.maintenance_worker.poll_once()
        second_result = self.app.maintenance_worker.poll_once()

        assert len(first_result.created_artifacts) == 4
        assert second_result.created_artifacts == []

    def test_poll_once_backfills_compaction_marker_from_current_segment_summary(self):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")
        segment_summary = self.app.maintenance.create_artifact(
            session_id,
            "segment-summary",
            "Segment summary (messages 1-2):\n- user[1]: message 1\n- user[2]: message 2",
            start_position=1,
            end_position=2,
        )
        micro_summary = self.app.maintenance.create_artifact(
            session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: message 3",
            start_position=3,
            end_position=8,
        )

        result = self.app.maintenance_worker.poll_once()
        markers = self.app.maintenance.list_artifacts(session_id, artifact_type="compaction-marker")

        assert any(artifact.artifact_type == "compaction-marker" for artifact in result.created_artifacts)
        assert markers[0]["end_position"] == 8
        assert markers[0]["metadata"]["covered_ranges"] == [
            {
                "artifact_types": ["segment-summary", "micro-summary"],
                "start_position": 1,
                "end_position": 8,
                "source_ranges": [
                    {"artifact_type": "segment-summary", "artifact_id": segment_summary["id"], "start_position": 1, "end_position": 2},
                    {"artifact_type": "micro-summary", "artifact_id": micro_summary["id"], "start_position": 3, "end_position": 8},
                ],
            },
        ]

    def test_poll_once_produces_non_prefix_ranges_from_incremental_segments(self):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
            "message 9",
            "message 10",
            "message 11",
            "message 12",
            "message 13",
            "message 14",
            "message 15",
            "message 16",
            "message 17",
            "message 18",
            "message 19",
            "message 20",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")

        result = self.app.maintenance_worker.poll_once()
        segments = self.app.maintenance.get_valid_artifacts(session_id, "segment-summary")
        markers = self.app.maintenance.list_artifacts(session_id, artifact_type="compaction-marker")
        archive_candidates = self.app.maintenance.list_artifacts(session_id, artifact_type="transcript-archive-candidate")
        micro_summary = next(artifact for artifact in self.app.maintenance.list_artifacts(session_id) if artifact["artifact_type"] == "micro-summary")
        transcript_ranges = self.app.transcript.list_ranges(session_id, range_type="archive-ready", status="active")
        live_ranges = self.app.transcript.list_ranges(session_id, range_type="summary-covered", status="active")

        assert any(artifact.artifact_type == "segment-summary" for artifact in result.created_artifacts)
        assert len(segments) == 1
        assert segments[0]["start_position"] == 1
        assert segments[0]["end_position"] == 6
        assert micro_summary["start_position"] == 15
        assert micro_summary["end_position"] == 20
        assert markers[0]["metadata"]["covered_ranges"] == [
            {
                "artifact_type": "segment-summary",
                "start_position": 1,
                "end_position": 6,
                "source_ranges": [
                    {"artifact_type": "segment-summary", "artifact_id": segments[0]["id"], "start_position": 1, "end_position": 6},
                ],
            },
            {
                "artifact_type": "micro-summary",
                "start_position": 15,
                "end_position": 20,
                "source_ranges": [
                    {"artifact_type": "micro-summary", "artifact_id": micro_summary["id"], "start_position": 15, "end_position": 20},
                ],
            },
        ]
        archive_candidates = self.app.maintenance.list_artifacts(session_id, artifact_type="transcript-archive-candidate")
        message_states = self.app.transcript.list_message_states(
            session_id,
            state_type="archive-ready",
            status="active",
        )
        assert archive_candidates[0]["metadata"]["archive_ranges"] == [
            {
                "artifact_type": "segment-summary",
                "start_position": 1,
                "end_position": 6,
                "source_ranges": [
                    {"artifact_type": "segment-summary", "artifact_id": segments[0]["id"], "start_position": 1, "end_position": 6},
                ],
            },
        ]
        assert len(message_states) == 6
        assert message_states[0]["metadata"]["range_start"] == 1
        assert message_states[0]["metadata"]["range_end"] == 6
        assert live_ranges[0]["start_position"] == 1
        assert live_ranges[0]["end_position"] == 6
        assert live_ranges[1]["start_position"] == 15
        assert live_ranges[1]["end_position"] == 20

    def test_poll_once_accumulates_multiple_valid_segment_summaries(self):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
            "message 9",
            "message 10",
            "message 11",
            "message 12",
            "message 13",
            "message 14",
            "message 15",
            "message 16",
            "message 17",
            "message 18",
            "message 19",
            "message 20",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")

        first_result = self.app.maintenance_worker.poll_once()
        second_result = self.app.maintenance_worker.poll_once()
        segments = self.app.maintenance.get_valid_artifacts(session_id, "segment-summary")
        markers = self.app.maintenance.list_artifacts(session_id, artifact_type="compaction-marker")
        archive_candidates = self.app.maintenance.list_artifacts(session_id, artifact_type="transcript-archive-candidate")
        transcript_ranges = self.app.transcript.list_ranges(session_id, range_type="archive-ready")

        assert any(artifact.artifact_type == "segment-summary" for artifact in first_result.created_artifacts)
        assert any(artifact.artifact_type == "segment-summary" for artifact in second_result.created_artifacts)
        assert [(artifact["start_position"], artifact["end_position"]) for artifact in segments] == [(1, 6), (7, 12)]
        micro_summary = self.app.maintenance.get_latest_valid_artifact(session_id, "micro-summary")
        assert markers[0]["metadata"]["covered_ranges"] == [
            {
                "artifact_type": "segment-summary",
                "start_position": 1,
                "end_position": 12,
                "source_ranges": [
                    {"artifact_type": "segment-summary", "artifact_id": segments[0]["id"], "start_position": 1, "end_position": 6},
                    {"artifact_type": "segment-summary", "artifact_id": segments[1]["id"], "start_position": 7, "end_position": 12},
                ],
            },
            {
                "artifact_type": "micro-summary",
                "start_position": 15,
                "end_position": 20,
                "source_ranges": [
                    {"artifact_type": "micro-summary", "artifact_id": micro_summary["id"], "start_position": 15, "end_position": 20},
                ],
            },
        ]
        assert archive_candidates[0]["metadata"]["archive_ranges"] == [
            {
                "artifact_type": "segment-summary",
                "start_position": 1,
                "end_position": 12,
                "source_ranges": [
                    {"artifact_type": "segment-summary", "artifact_id": segments[0]["id"], "start_position": 1, "end_position": 6},
                    {"artifact_type": "segment-summary", "artifact_id": segments[1]["id"], "start_position": 7, "end_position": 12},
                ],
            },
        ]
        active_ranges = [item for item in transcript_ranges if item["status"] == "active"]
        stale_ranges = [item for item in transcript_ranges if item["status"] == "stale"]
        message_states = self.app.transcript.list_message_states(
            session_id,
            state_type="archive-ready",
        )
        active_message_states = [item for item in message_states if item["status"] == "active"]
        stale_message_states = [item for item in message_states if item["status"] == "stale"]
        assert len(active_ranges) == 1
        assert active_ranges[0]["start_position"] == 1
        assert active_ranges[0]["end_position"] == 12
        assert len(stale_ranges) == 1
        assert stale_ranges[0]["start_position"] == 1
        assert stale_ranges[0]["end_position"] == 6
        assert len(active_message_states) == 12
        assert len(stale_message_states) == 0

    def test_poll_once_preserves_semantically_current_compaction_marker(self):
        session_id, _ = self._create_idle_session([
            "message 1",
            "message 2",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
            "message 7",
            "message 8",
        ])
        plan = self.app.planning.get_plan(session_id)
        self.app.planning.add_plan_item(session_id, plan["id"], "Resume after summary", status="doing")
        segment_summary = self.app.maintenance.create_artifact(
            session_id,
            "segment-summary",
            "Segment summary (messages 1-2):\n- user[1]: message 1\n- user[2]: message 2",
            start_position=1,
            end_position=2,
        )
        micro_summary = self.app.maintenance.create_artifact(
            session_id,
            "micro-summary",
            "Idle micro-summary:\n- user: message 3",
            start_position=3,
            end_position=8,
        )
        self.app.maintenance.create_artifact(
            session_id,
            "compaction-marker",
            "Compaction marker for messages 1-8",
            metadata={
                "covered_ranges": [
                    {
                        "artifact_types": ["segment-summary", "micro-summary"],
                        "start_position": 1,
                        "end_position": 8,
                        "source_ranges": [
                            {"artifact_type": "segment-summary", "artifact_id": segment_summary["id"], "start_position": 1, "end_position": 2},
                            {"artifact_type": "micro-summary", "artifact_id": micro_summary["id"], "start_position": 3, "end_position": 8},
                        ],
                    },
                ],
            },
            start_position=1,
            end_position=8,
        )

        result = self.app.maintenance_worker.poll_once()
        markers = self.app.maintenance.list_artifacts(session_id, artifact_type="compaction-marker")

        assert not any(artifact.artifact_type == "compaction-marker" for artifact in result.created_artifacts)
        assert len(markers) == 1

    def test_poll_once_does_not_create_archive_candidate_for_micro_only_coverage(self):
        session_id, _ = self._create_idle_session("micro only")

        result = self.app.maintenance_worker.poll_once()
        archive_candidates = self.app.maintenance.list_artifacts(session_id, artifact_type="transcript-archive-candidate")
        transcript_ranges = self.app.transcript.list_ranges(session_id, range_type="archive-ready")

        assert not any(artifact.artifact_type == "transcript-archive-candidate" for artifact in result.created_artifacts)
        assert archive_candidates == []
        assert transcript_ranges == []

    def test_background_worker_is_cancellable(self):
        self.app.maintenance_worker.poll_interval_seconds = 0.05

        self.app.maintenance_worker.start_background()

        assert self.app.maintenance_worker._thread is not None
        assert self.app.maintenance_worker._thread.is_alive()

        self.app.maintenance_worker.stop_background(timeout=1.0)

        assert not self.app.maintenance_worker._thread.is_alive()


class TestMaintenanceRoutes:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.db.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _wrap_db_with_session_error(self):
        real_db = self.app.db

        class _DbProxy:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, params=()):
                if "SELECT id FROM sessions WHERE id = ?" in sql:
                    raise sqlite3.InterfaceError("bad parameter or other API misuse")
                return self._inner.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        self.app.db = _DbProxy(real_db)

    def test_list_artifacts_route(self):
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "artifacts"},
        )
        session_id = session_resp.get_json()["id"]
        self.app.maintenance.create_artifact(session_id, "micro-summary", "content", start_position=1, end_position=1)

        resp = self.client.get(f"/api/sessions/{session_id}/artifacts")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["session_id"] == session_id
        assert len(data["artifacts"]) == 1

    def test_list_transcript_ranges_route(self):
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "ranges"},
        )
        session_id = session_resp.get_json()["id"]
        self.app.transcript.sync_archive_ready_ranges(
            session_id,
            [
                {
                    "start_position": 1,
                    "end_position": 3,
                    "source_ranges": [
                        {"artifact_type": "segment-summary", "start_position": 1, "end_position": 3},
                    ],
                },
            ],
            source_artifact_id="artifact-1",
        )

        resp = self.client.get(f"/api/sessions/{session_id}/transcript-ranges?range_type=archive-ready&status=active")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["session_id"] == session_id
        assert len(data["ranges"]) == 1
        assert data["ranges"][0]["range_type"] == "archive-ready"
        assert data["ranges"][0]["metadata"]["source_artifact_id"] == "artifact-1"

    def test_list_transcript_message_states_route(self):
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "message states"},
        )
        session_id = session_resp.get_json()["id"]
        run_resp = self.client.post(
            f"/api/sessions/{session_id}/messages",
            json={"content": "first"},
        )
        assert run_resp.status_code == 201
        queued_run = self.app.db.execute(
            "SELECT id FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        self.app.db.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (queued_run["id"],))
        self.app.db.commit()
        self.app.transcript.sync_summary_covered_ranges(
            session_id,
            [
                {
                    "artifact_type": "micro-summary",
                    "start_position": 1,
                    "end_position": 1,
                    "source_ranges": [
                        {"artifact_type": "micro-summary", "start_position": 1, "end_position": 1},
                    ],
                },
            ],
            source_artifact_id="artifact-2",
        )

        resp = self.client.get(
            f"/api/sessions/{session_id}/transcript-message-states?state_type=summary-covered&status=active"
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["session_id"] == session_id
        assert len(data["message_states"]) == 1
        assert data["message_states"][0]["state_type"] == "summary-covered"
        assert data["message_states"][0]["metadata"]["range_start"] == 1

    def test_list_artifacts_route_not_found(self):
        resp = self.client.get("/api/sessions/missing/artifacts")
        assert resp.status_code == 404

    def test_list_transcript_ranges_route_not_found(self):
        resp = self.client.get("/api/sessions/missing/transcript-ranges")
        assert resp.status_code == 404

    def test_list_transcript_message_states_route_not_found(self):
        resp = self.client.get("/api/sessions/missing/transcript-message-states")
        assert resp.status_code == 404

    def test_artifacts_route_invalid_session_id_returns_structured_sqlite_error_details(self):
        session_resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "artifacts error"},
        )
        session_id = session_resp.get_json()["id"]
        self._wrap_db_with_session_error()

        resp = self.client.get(f"/api/sessions/{session_id}/artifacts")

        assert resp.status_code == 400
        payload = resp.get_json()
        assert payload["error"] == "invalid session_id parameter"
        assert payload["detail"]["route"] == "list_artifacts"
        assert payload["detail"]["exception_type"] == "InterfaceError"
