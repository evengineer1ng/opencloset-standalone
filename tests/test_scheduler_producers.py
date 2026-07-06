from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from api.api.app import create_app
from api.api.scheduler_producers import MaintenanceCandidateProducer, WatchdogCandidateProducer


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ago(seconds: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex


class _AppFixture:
    """Base class providing a throwaway app + db per test method."""

    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.client = self.app.test_client()

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _make_workspace(self) -> str:
        resp = self.client.post("/api/workspaces", json={"name": "Test WS"})
        return resp.get_json()["id"]

    def _make_session(self, workspace_id: str, label: str = "Test Session") -> str:
        sess_id = _new_id()
        self.app.db.execute(
            """INSERT INTO sessions (id, label, model, provider, status, workspace_id)
               VALUES (?, ?, 'test-model', 'llamacpp', 'active', ?)""",
            (sess_id, label, workspace_id),
        )
        self.app.db.commit()
        return sess_id

    def _add_message(self, session_id: str, role: str = "user", content: str = "hello", created_at: str | None = None) -> dict:
        msg_id = _new_id()
        run_id = _new_id()
        ts = created_at or _now_str()
        # Insert a stub run so the FK is satisfied
        self.app.db.execute(
            "INSERT INTO runs (id, session_id, status, turn_number) VALUES (?, ?, 'succeeded', 1)",
            (run_id, session_id),
        )
        self.app.db.execute(
            """INSERT INTO messages (id, session_id, run_id, role, content, position, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (msg_id, session_id, run_id, role, content, ts),
        )
        self.app.db.commit()
        return {"id": msg_id, "position": 1}


# ---------------------------------------------------------------------------
# MaintenanceCandidateProducer
# ---------------------------------------------------------------------------

class TestMaintenanceCandidateProducer(_AppFixture):

    def _producer(self, idle_threshold: float = 5.0) -> MaintenanceCandidateProducer:
        return MaintenanceCandidateProducer(self.app, idle_threshold_seconds=idle_threshold)

    def test_no_sessions_returns_empty(self):
        ws_id = self._make_workspace()
        candidates = self._producer().collect_candidates(ws_id)
        assert candidates == []

    def test_session_with_no_messages_skipped(self):
        ws_id = self._make_workspace()
        self._make_session(ws_id)
        candidates = self._producer().collect_candidates(ws_id)
        assert candidates == []

    def test_recent_message_below_threshold_returns_empty(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        # Message created right now — within idle threshold
        self._add_message(sess_id, created_at=_now_str())
        candidates = self._producer(idle_threshold=3600.0).collect_candidates(ws_id)
        assert candidates == []

    def test_stale_session_produces_candidate(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        # Message created 120s ago — well past threshold
        self._add_message(sess_id, created_at=_ago(120))
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.type == "maintenance_needed"
        assert c.session_id == sess_id
        assert c.workspace_id == ws_id
        assert c.source == "maintenance"
        assert c.foreground_blocking is False

    def test_candidate_id_contains_session_id(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        self._add_message(sess_id, created_at=_ago(120))
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert sess_id in candidates[0].id

    def test_urgency_scales_with_elapsed_time(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        # 60 minutes elapsed → urgency = min(90, 40+60) = 100 → capped at 90
        self._add_message(sess_id, created_at=_ago(3600))
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert candidates[0].urgency == 90

    def test_active_run_skips_session(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        self._add_message(sess_id, created_at=_ago(120))
        # Mock an active run for this session
        self.app.run_manager.get_active_run = lambda sid: {"id": "run1"} if sid == sess_id else None
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert candidates == []

    def test_up_to_date_summary_skips_candidate(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        msg = self._add_message(sess_id, created_at=_ago(120))
        # Inject a fake up-to-date maintenance artifact
        original = self.app.maintenance.get_latest_valid_artifact
        def fake_get_latest(session_id, artifact_type):
            if session_id == sess_id:
                return {"end_position": msg.get("position", 1)}
            return None
        self.app.maintenance.get_latest_valid_artifact = fake_get_latest
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert candidates == []
        self.app.maintenance.get_latest_valid_artifact = original

    def test_multiple_sessions_multiple_candidates(self):
        ws_id = self._make_workspace()
        sess1 = self._make_session(ws_id, label="S1")
        sess2 = self._make_session(ws_id, label="S2")
        self._add_message(sess1, created_at=_ago(120))
        self._add_message(sess2, created_at=_ago(120))
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert len(candidates) == 2
        session_ids = {c.session_id for c in candidates}
        assert sess1 in session_ids
        assert sess2 in session_ids

    def test_priority_is_45(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        self._add_message(sess_id, created_at=_ago(120))
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert candidates[0].priority == 45

    def test_compute_cost_is_20(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        self._add_message(sess_id, created_at=_ago(120))
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert candidates[0].compute_cost == 20

    def test_cooldown_is_120(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        self._add_message(sess_id, created_at=_ago(120))
        candidates = self._producer(idle_threshold=10.0).collect_candidates(ws_id)
        assert candidates[0].cooldown == 120


# ---------------------------------------------------------------------------
# WatchdogCandidateProducer
# ---------------------------------------------------------------------------

class TestWatchdogCandidateProducer(_AppFixture):

    def _producer(self) -> WatchdogCandidateProducer:
        return WatchdogCandidateProducer(self.app)

    def test_no_sessions_returns_empty(self):
        ws_id = self._make_workspace()
        candidates = self._producer().collect_candidates(ws_id)
        assert candidates == []

    def test_session_without_rollover_need_returns_empty(self):
        ws_id = self._make_workspace()
        self._make_session(ws_id)
        # By default planning.should_rollover returns False for a fresh session
        candidates = self._producer().collect_candidates(ws_id)
        assert candidates == []

    def test_session_needing_rollover_produces_candidate(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        # Force should_rollover to return True
        original = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: sid == sess_id
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original
        assert len(candidates) == 1
        c = candidates[0]
        assert c.type == "rollover_needed"
        assert c.session_id == sess_id
        assert c.workspace_id == ws_id

    def test_candidate_id_contains_session_id(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        original = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: True
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original
        assert sess_id in candidates[0].id

    def test_foreground_blocking_is_true(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        original = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: True
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original
        assert candidates[0].foreground_blocking is True

    def test_priority_urgency_are_95(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        original = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: True
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original
        c = candidates[0]
        assert c.priority == 95
        assert c.urgency == 95

    def test_compute_cost_is_30(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        original = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: True
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original
        assert candidates[0].compute_cost == 30

    def test_active_run_skips_session(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        original_rollover = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: True
        original_run = self.app.run_manager.get_active_run
        self.app.run_manager.get_active_run = lambda sid: {"id": "run1"}
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original_rollover
        self.app.run_manager.get_active_run = original_run
        assert candidates == []

    def test_multiple_rollover_sessions_multiple_candidates(self):
        ws_id = self._make_workspace()
        sess1 = self._make_session(ws_id, label="S1")
        sess2 = self._make_session(ws_id, label="S2")
        original = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: sid in {sess1, sess2}
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original
        assert len(candidates) == 2

    def test_source_is_maintenance(self):
        ws_id = self._make_workspace()
        sess_id = self._make_session(ws_id)
        original = self.app.planning.should_rollover
        self.app.planning.should_rollover = lambda sid: True
        candidates = self._producer().collect_candidates(ws_id)
        self.app.planning.should_rollover = original
        assert candidates[0].source == "maintenance"
