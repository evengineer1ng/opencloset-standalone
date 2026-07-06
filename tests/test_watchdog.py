from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from api.api.app import create_app
from api.api.rollover import RolloverConflictError
from api.api.watchdog import SessionWatchdog, WatchdogPollResult, build_argument_parser, main


class TestSessionWatchdog:
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

    def test_poll_once_rolls_over_threshold_session(self):
        self.app.config["ENABLE_ROLLOVER_GUARD"] = True
        self.app.planning.rollover_guard_enabled = True
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "watch me"},
        )
        session_id = resp.get_json()["id"]

        self.app.planning.update_context_guard(session_id, 60000, rollover_threshold=58000)
        self.app.planning.set_status(session_id, "paused")

        result = self.app.watchdog.poll_once()

        assert result.checked_sessions == 1
        assert len(result.triggered_rollovers) == 1

        successor = result.triggered_rollovers[0]
        old_session = self.client.get(f"/api/sessions/{session_id}").get_json()
        new_session = self.client.get(f"/api/sessions/{successor.id}").get_json()

        assert old_session["status"] == "rolled-over"
        assert old_session["rolled_over_to"] == successor.id
        assert new_session["status"] == "active"
        assert successor.active_plan is not None
        assert successor.active_plan["handoff"]["source_session_id"] == session_id

    def test_poll_once_skips_session_with_active_run(self):
        self.app.config["ENABLE_ROLLOVER_GUARD"] = True
        self.app.planning.rollover_guard_enabled = True
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "busy"},
        )
        session_id = resp.get_json()["id"]

        self.app.planning.update_context_guard(session_id, 60000, rollover_threshold=58000)
        self.app.db.execute(
            "INSERT INTO runs (id, session_id, status, turn_number) VALUES ('run-1', ?, 'queued', 1)",
            (session_id,),
        )
        self.app.db.commit()

        result = self.app.watchdog.poll_once()
        session = self.client.get(f"/api/sessions/{session_id}").get_json()

        assert result.checked_sessions == 1
        assert result.triggered_rollovers == []
        assert session["status"] == "active"

    def test_poll_once_skips_rollover_conflict(self):
        self.app.config["ENABLE_ROLLOVER_GUARD"] = True
        self.app.planning.rollover_guard_enabled = True
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "conflict"},
        )
        session_id = resp.get_json()["id"]

        self.app.planning.update_context_guard(session_id, 60000, rollover_threshold=58000)

        with patch("api.api.watchdog.create_rollover_successor", side_effect=RolloverConflictError("already rolling")):
            result = self.app.watchdog.poll_once()

        session = self.client.get(f"/api/sessions/{session_id}").get_json()
        assert result.checked_sessions == 1
        assert result.triggered_rollovers == []
        assert session["status"] == "active"

    def test_poll_once_skips_threshold_session_when_guard_disabled(self):
        self.app.config["ENABLE_ROLLOVER_GUARD"] = False
        self.app.planning.rollover_guard_enabled = False
        resp = self.client.post(
            "/api/sessions",
            json={"model": "test-model", "label": "watch me later"},
        )
        session_id = resp.get_json()["id"]

        self.app.planning.update_context_guard(session_id, 60000, rollover_threshold=58000)
        self.app.planning.set_status(session_id, "paused")

        result = self.app.watchdog.poll_once()

        assert result.checked_sessions == 1
        assert result.triggered_rollovers == []


def test_build_argument_parser_defaults():
    parser = build_argument_parser()
    args = parser.parse_args([])

    assert args.db_path is None
    assert args.poll_interval_seconds == 30.0
    assert args.once is False


def test_run_forever_polls_until_interrupted(monkeypatch):
    watchdog = SessionWatchdog(app=object(), poll_interval_seconds=0.01)
    calls: list[str] = []

    def stop_after_one_poll():
        calls.append("poll")
        raise RuntimeError("stop")

    monkeypatch.setattr(watchdog, "poll_once", stop_after_one_poll)

    with pytest.raises(RuntimeError, match="stop"):
        watchdog.run_forever()

    assert calls == ["poll"]


def test_background_worker_is_cancellable(tmp_path):
    app = create_app(db_path=str(tmp_path / "watchdog.db"))
    try:
        app.watchdog.poll_interval_seconds = 0.05
        app.watchdog.start_background()

        assert app.watchdog._thread is not None
        assert app.watchdog._thread.is_alive()

        app.watchdog.stop_background(timeout=1.0)

        assert not app.watchdog._thread.is_alive()
    finally:
        app.close()


def test_create_app_auto_starts_watchdog_for_flask_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")
    app = create_app(db_path=str(tmp_path / "watchdog-cli.db"))
    try:
        assert app.watchdog._thread is not None
        assert app.watchdog._thread.is_alive()
    finally:
        app.close()

    assert not app.watchdog._thread.is_alive()


def test_main_once_closes_app(monkeypatch):
    closed = {"value": False}

    class _FakeApp:
        def close(self):
            closed["value"] = True

    class _FakeWatchdog:
        def __init__(self, app, *, poll_interval_seconds):
            self.app = app
            self.poll_interval_seconds = poll_interval_seconds

        def poll_once(self):
            return WatchdogPollResult(checked_sessions=2, triggered_rollovers=[])

    monkeypatch.setattr(
        "api.api.app.create_app",
        lambda db_path=None, start_background_workers=False: _FakeApp(),
    )
    monkeypatch.setattr("api.api.watchdog.SessionWatchdog", _FakeWatchdog)

    result = main(["--once", "--poll-interval", "12"])

    assert result == 0
    assert closed["value"] is True


def test_main_run_forever_closes_app(monkeypatch):
    closed = {"value": False}
    run_forever_called = {"value": False}

    class _FakeApp:
        def close(self):
            closed["value"] = True

    class _FakeWatchdog:
        def __init__(self, app, *, poll_interval_seconds):
            self.app = app
            self.poll_interval_seconds = poll_interval_seconds

        def run_forever(self):
            run_forever_called["value"] = True

    monkeypatch.setattr(
        "api.api.app.create_app",
        lambda db_path=None, start_background_workers=False: _FakeApp(),
    )
    monkeypatch.setattr("api.api.watchdog.SessionWatchdog", _FakeWatchdog)

    result = main([])

    assert result == 0
    assert run_forever_called["value"] is True
    assert closed["value"] is True
