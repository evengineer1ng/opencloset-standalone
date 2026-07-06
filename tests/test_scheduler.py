from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from api.api.app import create_app
from api.api.scheduler import (
    EligibilityContext,
    EligibilityGate,
    IdleTier,
    SchedulerArbiter,
    SchedulerJobManager,
    SchedulerRunner,
    detect_idle_tier,
    score_candidate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ago(seconds: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _future(seconds: float) -> str:
    ts = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class FakeCandidate:
    id: str
    type: str
    source: str = "worker"
    workspace_id: str = "ws1"
    session_id: str = "sess1"
    priority: int = 50
    urgency: int = 50
    compute_cost: int = 1
    interruptibility: str = "high"
    cooldown: int = 0
    expires_at: str | None = None
    prerequisites: list = None
    mutability_level: str = "read_only"
    foreground_blocking: bool = False
    title: str = "Test"
    summary: str = "Test summary"
    metadata: dict = None

    def __post_init__(self):
        if self.prerequisites is None:
            self.prerequisites = []
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "priority": self.priority,
            "urgency": self.urgency,
            "compute_cost": self.compute_cost,
            "interruptibility": self.interruptibility,
            "cooldown": self.cooldown,
            "expires_at": self.expires_at,
            "prerequisites": list(self.prerequisites),
            "mutability_level": self.mutability_level,
            "foreground_blocking": self.foreground_blocking,
            "title": self.title,
            "summary": self.summary,
            "metadata": dict(self.metadata),
        }


class FakeProducer:
    def __init__(self, candidates: list[FakeCandidate]) -> None:
        self._candidates = candidates

    def collect_candidates(self, workspace_id: str) -> list[FakeCandidate]:
        return [c for c in self._candidates if c.workspace_id == workspace_id]


# ---------------------------------------------------------------------------
# Idle tier detection
# ---------------------------------------------------------------------------

class TestDetectIdleTier:
    def test_none_returns_extended(self):
        assert detect_idle_tier(None) == IdleTier.EXTENDED

    def test_invalid_string_returns_extended(self):
        assert detect_idle_tier("not-a-date") == IdleTier.EXTENDED

    def test_hot_tier_recent_activity(self):
        assert detect_idle_tier(_ago(2)) == IdleTier.HOT

    def test_brief_tier_15s_idle(self):
        assert detect_idle_tier(_ago(15)) == IdleTier.BRIEF

    def test_medium_tier_3min_idle(self):
        assert detect_idle_tier(_ago(180)) == IdleTier.MEDIUM

    def test_long_tier_15min_idle(self):
        assert detect_idle_tier(_ago(900)) == IdleTier.LONG

    def test_extended_tier_2hr_idle(self):
        assert detect_idle_tier(_ago(7200)) == IdleTier.EXTENDED

    def test_boundary_brief_lower(self):
        assert detect_idle_tier(_ago(10.5)) == IdleTier.BRIEF

    def test_boundary_medium_lower(self):
        assert detect_idle_tier(_ago(121)) == IdleTier.MEDIUM


# ---------------------------------------------------------------------------
# Eligibility gate
# ---------------------------------------------------------------------------

class TestEligibilityGate:
    def setup_method(self):
        self.gate = EligibilityGate()
        self.ctx = EligibilityContext(
            idle_tier=IdleTier.MEDIUM,
            active_run_session_ids=set(),
            now=datetime.now(timezone.utc),
            job_last_run={},
        )

    def _ctx(self, **kwargs) -> EligibilityContext:
        base = {
            "idle_tier": IdleTier.MEDIUM,
            "active_run_session_ids": set(),
            "now": datetime.now(timezone.utc),
            "job_last_run": {},
        }
        base.update(kwargs)
        return EligibilityContext(**base)

    def test_basic_eligible(self):
        c = FakeCandidate(id="c1", type="backlog_review")
        assert self.gate.is_eligible(c, ctx=self.ctx) is True

    def test_foreground_blocking_requires_medium_tier(self):
        c = FakeCandidate(id="c1", type="backlog_review", foreground_blocking=True)
        ctx_hot = self._ctx(idle_tier=IdleTier.HOT)
        ctx_brief = self._ctx(idle_tier=IdleTier.BRIEF)
        ctx_medium = self._ctx(idle_tier=IdleTier.MEDIUM)
        assert self.gate.is_eligible(c, ctx=ctx_hot) is False
        assert self.gate.is_eligible(c, ctx=ctx_brief) is False
        assert self.gate.is_eligible(c, ctx=ctx_medium) is True

    def test_active_run_blocks_session(self):
        c = FakeCandidate(id="c1", type="backlog_review", session_id="sess_a")
        ctx = self._ctx(active_run_session_ids={"sess_a"})
        assert self.gate.is_eligible(c, ctx=ctx) is False

    def test_active_run_does_not_block_other_session(self):
        c = FakeCandidate(id="c1", type="backlog_review", session_id="sess_b")
        ctx = self._ctx(active_run_session_ids={"sess_a"})
        assert self.gate.is_eligible(c, ctx=ctx) is True

    def test_cooldown_blocks_recently_run_type(self):
        c = FakeCandidate(id="c1", type="handoff_review", cooldown=300)
        last_run = datetime.now(timezone.utc) - timedelta(seconds=100)
        ctx = self._ctx(job_last_run={"handoff_review": last_run})
        assert self.gate.is_eligible(c, ctx=ctx) is False

    def test_cooldown_passes_after_elapsed(self):
        c = FakeCandidate(id="c1", type="handoff_review", cooldown=300)
        last_run = datetime.now(timezone.utc) - timedelta(seconds=400)
        ctx = self._ctx(job_last_run={"handoff_review": last_run})
        assert self.gate.is_eligible(c, ctx=ctx) is True

    def test_zero_cooldown_always_passes(self):
        c = FakeCandidate(id="c1", type="handoff_review", cooldown=0)
        last_run = datetime.now(timezone.utc) - timedelta(seconds=1)
        ctx = self._ctx(job_last_run={"handoff_review": last_run})
        assert self.gate.is_eligible(c, ctx=ctx) is True

    def test_metadata_min_idle_tier_blocks_reflective_candidate(self):
        c = FakeCandidate(id="c1", type="fresh_eyes_thread_pull", metadata={"min_idle_tier": IdleTier.LONG})
        ctx_medium = self._ctx(idle_tier=IdleTier.MEDIUM)
        ctx_long = self._ctx(idle_tier=IdleTier.LONG)
        assert self.gate.is_eligible(c, ctx=ctx_medium) is False
        assert self.gate.is_eligible(c, ctx=ctx_long) is True


# ---------------------------------------------------------------------------
# Score candidate
# ---------------------------------------------------------------------------

class TestScoreCandidate:
    def test_higher_urgency_scores_higher(self):
        low = FakeCandidate(id="low", type="t", urgency=10, priority=50)
        high = FakeCandidate(id="high", type="t", urgency=90, priority=50)
        assert score_candidate(high, IdleTier.MEDIUM) > score_candidate(low, IdleTier.MEDIUM)

    def test_higher_priority_scores_higher(self):
        low = FakeCandidate(id="low", type="t", urgency=50, priority=10)
        high = FakeCandidate(id="high", type="t", urgency=50, priority=90)
        assert score_candidate(high, IdleTier.MEDIUM) > score_candidate(low, IdleTier.MEDIUM)

    def test_higher_idle_tier_scores_higher_all_else_equal(self):
        c = FakeCandidate(id="c", type="t", urgency=50, priority=50)
        s0 = score_candidate(c, IdleTier.HOT)
        s3 = score_candidate(c, IdleTier.LONG)
        assert s3 > s0

    def test_higher_compute_cost_scores_lower(self):
        cheap = FakeCandidate(id="cheap", type="t", compute_cost=1)
        expensive = FakeCandidate(id="exp", type="t", compute_cost=90)
        assert score_candidate(cheap, IdleTier.MEDIUM) > score_candidate(expensive, IdleTier.MEDIUM)

    def test_low_interruptibility_scores_lower(self):
        easy = FakeCandidate(id="easy", type="t", interruptibility="high")
        hard = FakeCandidate(id="hard", type="t", interruptibility="low")
        assert score_candidate(easy, IdleTier.MEDIUM) > score_candidate(hard, IdleTier.MEDIUM)

    def test_unknown_interruptibility_treated_as_medium(self):
        c_med = FakeCandidate(id="med", type="t", interruptibility="medium")
        c_unk = FakeCandidate(id="unk", type="t", interruptibility="unknown_value")
        assert score_candidate(c_med, IdleTier.MEDIUM) == score_candidate(c_unk, IdleTier.MEDIUM)

    def test_score_is_deterministic(self):
        c = FakeCandidate(id="c1", type="t", urgency=70, priority=60, compute_cost=5)
        s1 = score_candidate(c, IdleTier.LONG)
        s2 = score_candidate(c, IdleTier.LONG)
        assert s1 == s2


# ---------------------------------------------------------------------------
# SchedulerJobManager
# ---------------------------------------------------------------------------

class TestSchedulerJobManager:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.jobs: SchedulerJobManager = self.app.scheduler_jobs

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _make_workspace(self) -> str:
        resp = self.app.test_client().post("/api/workspaces", json={"name": "Test WS"})
        return resp.get_json()["id"]

    def test_create_and_get_job(self):
        ws_id = self._make_workspace()
        job = self.jobs.create_job(
            job_type="handoff_review",
            workspace_id=ws_id,
            cooldown_seconds=300,
        )
        assert job["job_type"] == "handoff_review"
        assert job["workspace_id"] == ws_id
        assert job["status"] == "scheduled"
        assert job["cooldown_seconds"] == 300

        fetched = self.jobs.get_job(job["id"])
        assert fetched["id"] == job["id"]

    def test_list_jobs_filtered_by_workspace(self):
        ws_id = self._make_workspace()
        self.jobs.create_job(job_type="backlog_review", workspace_id=ws_id)
        self.jobs.create_job(job_type="context_review", workspace_id=ws_id)
        jobs = self.jobs.list_jobs(workspace_id=ws_id)
        assert len(jobs) == 2

    def test_list_due_jobs_returns_no_future_job(self):
        ws_id = self._make_workspace()
        self.jobs.create_job(
            job_type="backlog_review",
            workspace_id=ws_id,
            next_run_at=_future(3600),
        )
        due = self.jobs.list_due_jobs()
        assert all(j["job_type"] != "backlog_review" for j in due)

    def test_list_due_jobs_returns_null_next_run(self):
        ws_id = self._make_workspace()
        job = self.jobs.create_job(job_type="backlog_review", workspace_id=ws_id)
        due = self.jobs.list_due_jobs()
        assert any(j["id"] == job["id"] for j in due)

    def test_list_due_jobs_returns_past_due(self):
        ws_id = self._make_workspace()
        job = self.jobs.create_job(
            job_type="backlog_review",
            workspace_id=ws_id,
            next_run_at=_ago(60),
        )
        due = self.jobs.list_due_jobs()
        assert any(j["id"] == job["id"] for j in due)

    def test_record_run_sets_last_run_at(self):
        ws_id = self._make_workspace()
        job = self.jobs.create_job(job_type="handoff_review", workspace_id=ws_id, cooldown_seconds=60)
        self.jobs.record_run(job["id"], next_run_at=_future(60), status="scheduled")
        updated = self.jobs.get_job(job["id"])
        assert updated["last_run_at"] is not None
        assert updated["next_run_at"] is not None

    def test_get_last_run_times_groups_by_type(self):
        ws_id = self._make_workspace()
        job1 = self.jobs.create_job(job_type="handoff_review", workspace_id=ws_id)
        job2 = self.jobs.create_job(job_type="handoff_review", workspace_id=ws_id)
        self.jobs.record_run(job1["id"])
        self.jobs.record_run(job2["id"])
        last_runs = self.jobs.get_last_run_times()
        assert "handoff_review" in last_runs
        assert isinstance(last_runs["handoff_review"], datetime)

    def test_disable_job(self):
        ws_id = self._make_workspace()
        job = self.jobs.create_job(job_type="backlog_review", workspace_id=ws_id)
        self.jobs.disable_job(job["id"])
        updated = self.jobs.get_job(job["id"])
        assert updated["status"] == "disabled"

    def test_get_nonexistent_job_returns_none(self):
        assert self.jobs.get_job("does-not-exist") is None

    def test_status_filter_in_list(self):
        ws_id = self._make_workspace()
        job = self.jobs.create_job(job_type="backlog_review", workspace_id=ws_id)
        self.jobs.disable_job(job["id"])
        scheduled = self.jobs.list_jobs(workspace_id=ws_id, status="scheduled")
        disabled = self.jobs.list_jobs(workspace_id=ws_id, status="disabled")
        assert len(scheduled) == 0
        assert len(disabled) == 1

    def test_record_candidate_execution_updates_last_run_time(self):
        ws_id = self._make_workspace()
        recorded = self.jobs.record_candidate_execution(
            job_type="fresh_eyes_thread_pull",
            workspace_id=ws_id,
            session_id="sess1",
            source="pastime",
            cooldown_seconds=3600,
            metadata={"candidate_id": "cand1"},
        )
        assert recorded is not None
        assert recorded["status"] == "completed"
        last_runs = self.jobs.get_last_run_times()
        assert "fresh_eyes_thread_pull" in last_runs


# ---------------------------------------------------------------------------
# SchedulerArbiter
# ---------------------------------------------------------------------------

class TestSchedulerArbiter:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.arbiter: SchedulerArbiter = self.app.scheduler_arbiter

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _make_workspace(self) -> str:
        resp = self.app.test_client().post("/api/workspaces", json={"name": "Test WS"})
        return resp.get_json()["id"]

    def test_empty_producers_returns_none(self):
        ws_id = self._make_workspace()
        # Remove default producer temporarily
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        selected, ranked = self.arbiter.select_work(ws_id, idle_tier=IdleTier.MEDIUM)
        assert selected is None
        assert ranked == []
        self.arbiter._producers.extend(saved)

    def test_single_producer_candidate_selected(self):
        ws_id = self._make_workspace()
        c = FakeCandidate(id="c1", type="backlog_review", workspace_id=ws_id, urgency=80, priority=70)
        producer = FakeProducer([c])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(producer)
        selected, ranked = self.arbiter.select_work(ws_id, idle_tier=IdleTier.MEDIUM)
        assert selected is c
        assert len(ranked) == 1
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)

    def test_highest_scored_candidate_selected(self):
        ws_id = self._make_workspace()
        low = FakeCandidate(id="low", type="backlog_review", workspace_id=ws_id, urgency=10, priority=10)
        high = FakeCandidate(id="high", type="backlog_review", workspace_id=ws_id, urgency=90, priority=90)
        producer = FakeProducer([low, high])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(producer)
        selected, ranked = self.arbiter.select_work(ws_id, idle_tier=IdleTier.MEDIUM)
        assert selected is high
        assert ranked[0] is high
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)

    def test_foreground_blocking_candidate_gated_at_hot_tier(self):
        ws_id = self._make_workspace()
        c = FakeCandidate(id="fb", type="context_review", workspace_id=ws_id, foreground_blocking=True, urgency=100, priority=100)
        producer = FakeProducer([c])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(producer)
        selected_hot, _ = self.arbiter.select_work(ws_id, idle_tier=IdleTier.HOT)
        selected_medium, _ = self.arbiter.select_work(ws_id, idle_tier=IdleTier.MEDIUM)
        assert selected_hot is None
        assert selected_medium is c
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)

    def test_get_arbiter_snapshot_structure(self):
        ws_id = self._make_workspace()
        snapshot = self.arbiter.get_arbiter_snapshot(ws_id, idle_tier=IdleTier.BRIEF)
        assert "workspace_id" in snapshot
        assert "idle_tier" in snapshot
        assert "eligible_count" in snapshot
        assert "selected" in snapshot
        assert "ranked" in snapshot
        assert snapshot["idle_tier"] == IdleTier.BRIEF

    def test_register_multiple_producers(self):
        ws_id = self._make_workspace()
        c1 = FakeCandidate(id="c1", type="backlog_review", workspace_id=ws_id, urgency=30, priority=30)
        c2 = FakeCandidate(id="c2", type="context_review", workspace_id=ws_id, urgency=80, priority=80)
        p1 = FakeProducer([c1])
        p2 = FakeProducer([c2])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(p1)
        self.arbiter.register_producer(p2)
        selected, ranked = self.arbiter.select_work(ws_id, idle_tier=IdleTier.MEDIUM)
        assert len(ranked) == 2
        assert selected is c2  # c2 has higher urgency/priority
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)

    def test_disabled_pastime_candidate_is_filtered(self):
        ws_id = self._make_workspace()
        pastimes = self.app.workspaces.list_workspace_pastimes(ws_id)
        fresh_eyes = next(item for item in pastimes if item["key"] == "fresh-eyes-thread-pulling")
        self.app.workspaces.update_workspace_pastime(ws_id, fresh_eyes["id"], status="paused")
        candidate = FakeCandidate(
            id="fresh-eyes",
            type="fresh_eyes_thread_pull",
            workspace_id=ws_id,
            urgency=90,
            priority=90,
            metadata={"pastime_key": "fresh-eyes-thread-pulling", "min_idle_tier": IdleTier.LONG},
        )
        producer = FakeProducer([candidate])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(producer)
        selected, ranked = self.arbiter.select_work(ws_id, idle_tier=IdleTier.EXTENDED)
        assert selected is None
        assert ranked == []
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)

    def test_paused_attention_profile_blocks_all_candidates(self):
        ws_id = self._make_workspace()
        self.app.workspaces.update_workspace_attention_profile(ws_id, mode="paused", max_idle_budget=0)
        candidate = FakeCandidate(
            id="backlog",
            type="backlog_review",
            workspace_id=ws_id,
            urgency=90,
            priority=90,
            metadata={"pastime_key": "backlog-review"},
        )
        producer = FakeProducer([candidate])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(producer)
        selected, ranked = self.arbiter.select_work(ws_id, idle_tier=IdleTier.MEDIUM)
        assert selected is None
        assert ranked == []
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)

    def test_attention_allowed_pastime_types_filter_reflective_candidate(self):
        ws_id = self._make_workspace()
        self.app.workspaces.update_workspace_attention_profile(
            ws_id,
            mode="warm",
            allowed_pastime_types=["operational"],
            max_idle_budget=10,
        )
        candidate = FakeCandidate(
            id="fresh-eyes",
            type="fresh_eyes_thread_pull",
            workspace_id=ws_id,
            urgency=90,
            priority=90,
            compute_cost=6,
            metadata={"pastime_key": "fresh-eyes-thread-pulling", "min_idle_tier": IdleTier.LONG},
        )
        producer = FakeProducer([candidate])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(producer)
        selected, ranked = self.arbiter.select_work(ws_id, idle_tier=IdleTier.EXTENDED)
        assert selected is None
        assert ranked == []
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)

    def test_attention_mode_changes_candidate_ranking(self):
        ws_id = self._make_workspace()
        operational = FakeCandidate(
            id="backlog",
            type="backlog_review",
            workspace_id=ws_id,
            urgency=60,
            priority=60,
            compute_cost=1,
            metadata={"pastime_key": "backlog-review"},
        )
        reflective = FakeCandidate(
            id="fresh-eyes",
            type="fresh_eyes_thread_pull",
            workspace_id=ws_id,
            urgency=60,
            priority=60,
            compute_cost=6,
            metadata={"pastime_key": "fresh-eyes-thread-pulling", "min_idle_tier": IdleTier.LONG},
        )
        producer = FakeProducer([operational, reflective])
        saved = self.arbiter._producers[:]
        self.arbiter._producers.clear()
        self.arbiter.register_producer(producer)

        self.app.workspaces.update_workspace_attention_profile(
            ws_id,
            mode="active",
            baseline_priority=80,
            current_attention_level=90,
            max_idle_budget=10,
            allowed_pastime_types=["operational", "reflective"],
        )
        active_selected, _ = self.arbiter.select_work(ws_id, idle_tier=IdleTier.EXTENDED)

        self.app.workspaces.update_workspace_attention_profile(
            ws_id,
            mode="background",
            baseline_priority=20,
            current_attention_level=20,
            max_idle_budget=10,
            allowed_pastime_types=["operational", "reflective"],
        )
        background_selected, _ = self.arbiter.select_work(ws_id, idle_tier=IdleTier.EXTENDED)

        assert active_selected is reflective
        assert background_selected is operational
        self.arbiter._producers.clear()
        self.arbiter._producers.extend(saved)


# ---------------------------------------------------------------------------
# SchedulerRunner
# ---------------------------------------------------------------------------

class TestSchedulerRunner:
    def setup_method(self):
        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._db_file.name
        self._db_file.close()
        self.app = create_app(db_path=self.db_path)
        self.runner: SchedulerRunner = self.app.scheduler_runner
        self.jobs: SchedulerJobManager = self.app.scheduler_jobs

    def teardown_method(self):
        try:
            self.app.close()
        except Exception:
            pass
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _make_workspace(self) -> str:
        resp = self.app.test_client().post("/api/workspaces", json={"name": "Test WS"})
        return resp.get_json()["id"]

    def test_no_due_jobs_returns_empty(self):
        results = self.runner.run_due()
        assert results == []

    def test_no_handler_registered_skips_job(self):
        ws_id = self._make_workspace()
        self.jobs.create_job(job_type="unhandled_type", workspace_id=ws_id)
        results = self.runner.run_due()
        assert results == []

    def test_handler_called_for_due_job(self):
        ws_id = self._make_workspace()
        called = []
        def handler(job):
            called.append(job["id"])
            return {"ran": True}
        self.runner.register_handler("test_job", handler)
        job = self.jobs.create_job(job_type="test_job", workspace_id=ws_id)
        results = self.runner.run_due()
        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert results[0]["job_id"] == job["id"]
        assert job["id"] in called

    def test_handler_failure_marks_job_failed(self):
        ws_id = self._make_workspace()
        def bad_handler(job):
            raise RuntimeError("intentional failure")
        self.runner.register_handler("bad_job", bad_handler)
        job = self.jobs.create_job(job_type="bad_job", workspace_id=ws_id)
        results = self.runner.run_due()
        assert results[0]["status"] == "error"
        updated = self.jobs.get_job(job["id"])
        assert updated["status"] == "failed"

    def test_future_job_not_run(self):
        ws_id = self._make_workspace()
        ran = []
        self.runner.register_handler("future_job", lambda j: ran.append(j["id"]))
        self.jobs.create_job(
            job_type="future_job",
            workspace_id=ws_id,
            next_run_at=_future(3600),
        )
        self.runner.run_due()
        assert ran == []

    def test_cooldown_job_rescheduled_after_run(self):
        ws_id = self._make_workspace()
        self.runner.register_handler("cooldown_job", lambda j: {"ok": True})
        job = self.jobs.create_job(
            job_type="cooldown_job",
            workspace_id=ws_id,
            cooldown_seconds=600,
        )
        self.runner.run_due()
        updated = self.jobs.get_job(job["id"])
        assert updated["status"] == "scheduled"
        assert updated["last_run_at"] is not None
        assert updated["next_run_at"] is not None

    def test_one_shot_job_completed_after_run(self):
        ws_id = self._make_workspace()
        self.runner.register_handler("one_shot", lambda j: None)
        job = self.jobs.create_job(
            job_type="one_shot",
            workspace_id=ws_id,
            cooldown_seconds=0,
        )
        self.runner.run_due()
        updated = self.jobs.get_job(job["id"])
        assert updated["status"] == "completed"


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

class TestSchedulerRoutes:
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

    def test_snapshot_404_for_unknown_workspace(self):
        resp = self.client.get("/api/workspaces/does-not-exist/scheduler/snapshot")
        assert resp.status_code == 404

    def test_snapshot_returns_valid_structure(self):
        ws_id = self._make_workspace()
        resp = self.client.get(
            f"/api/workspaces/{ws_id}/scheduler/snapshot",
            query_string={"idle_tier": "2"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["workspace_id"] == ws_id
        assert data["idle_tier"] == 2
        assert "eligible_count" in data
        assert "selected" in data
        assert "ranked" in data

    def test_create_and_list_jobs(self):
        ws_id = self._make_workspace()
        create_resp = self.client.post(
            f"/api/workspaces/{ws_id}/scheduler/jobs",
            json={"job_type": "handoff_review", "cooldown_seconds": 300},
        )
        assert create_resp.status_code == 201
        job = create_resp.get_json()
        assert job["job_type"] == "handoff_review"
        assert job["cooldown_seconds"] == 300

        list_resp = self.client.get(f"/api/workspaces/{ws_id}/scheduler/jobs")
        assert list_resp.status_code == 200
        jobs = list_resp.get_json()["jobs"]
        assert any(j["id"] == job["id"] for j in jobs)

    def test_create_job_requires_job_type(self):
        ws_id = self._make_workspace()
        resp = self.client.post(
            f"/api/workspaces/{ws_id}/scheduler/jobs",
            json={"cooldown_seconds": 60},
        )
        assert resp.status_code == 400

    def test_get_job_route(self):
        ws_id = self._make_workspace()
        create_resp = self.client.post(
            f"/api/workspaces/{ws_id}/scheduler/jobs",
            json={"job_type": "backlog_review"},
        )
        job_id = create_resp.get_json()["id"]
        get_resp = self.client.get(f"/api/workspaces/{ws_id}/scheduler/jobs/{job_id}")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["id"] == job_id

    def test_get_job_404_for_wrong_workspace(self):
        ws_id = self._make_workspace()
        ws2_id = self._make_workspace()
        create_resp = self.client.post(
            f"/api/workspaces/{ws_id}/scheduler/jobs",
            json={"job_type": "backlog_review"},
        )
        job_id = create_resp.get_json()["id"]
        resp = self.client.get(f"/api/workspaces/{ws2_id}/scheduler/jobs/{job_id}")
        assert resp.status_code == 404

    def test_disable_job_route(self):
        ws_id = self._make_workspace()
        job = self.client.post(
            f"/api/workspaces/{ws_id}/scheduler/jobs",
            json={"job_type": "context_review"},
        ).get_json()
        resp = self.client.post(
            f"/api/workspaces/{ws_id}/scheduler/jobs/{job['id']}/disable"
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "disabled"

    def test_list_jobs_status_filter(self):
        ws_id = self._make_workspace()
        job = self.client.post(
            f"/api/workspaces/{ws_id}/scheduler/jobs",
            json={"job_type": "backlog_review"},
        ).get_json()
        self.client.post(f"/api/workspaces/{ws_id}/scheduler/jobs/{job['id']}/disable")
        resp = self.client.get(
            f"/api/workspaces/{ws_id}/scheduler/jobs",
            query_string={"status": "disabled"},
        )
        assert resp.status_code == 200
        jobs = resp.get_json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["status"] == "disabled"


# ---------------------------------------------------------------------------
# Stale signal expiry (integration via workspace_runtime)
# ---------------------------------------------------------------------------

class TestStaleSignalExpiry:
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

    def test_cleanup_stale_signals_resolves_orphaned_open_signal(self):
        ws_id = self._make_workspace()
        runtime = self.app.workspace_runtime
        # Manually insert an open signal with a source_key that has no active candidate.
        from api.api.workspace_runtime import _now
        from api.db.schema import new_id
        now = _now()
        signal_id = new_id()
        self.app.db.execute(
            """INSERT INTO workspace_signals
               (id, workspace_id, session_id, worker_name, signal_type, source_key,
                title, summary, status, priority, metadata, created_at, updated_at)
               VALUES (?, ?, NULL, 'handoff-review-clerk', 'handoff_ready',
                       'handoff-review:orphan-session',
                       'Orphan Signal', 'No active candidate', 'open', 50, '{}', ?, ?)""",
            (signal_id, ws_id, now, now),
        )
        self.app.db.commit()
        # Run cleanup with empty active source keys.
        count = runtime._cleanup_stale_signals(ws_id, active_source_keys=set())
        assert count >= 1
        # Signal should now be resolved.
        row = self.app.db.execute(
            "SELECT status FROM workspace_signals WHERE id = ?", (signal_id,)
        ).fetchone()
        assert row["status"] == "resolved"

    def test_cleanup_preserves_escalated_signals(self):
        ws_id = self._make_workspace()
        runtime = self.app.workspace_runtime
        from api.api.workspace_runtime import _now
        from api.db.schema import new_id
        now = _now()
        signal_id = new_id()
        self.app.db.execute(
            """INSERT INTO workspace_signals
               (id, workspace_id, session_id, worker_name, signal_type, source_key,
                title, summary, status, priority, metadata, created_at, updated_at)
               VALUES (?, ?, NULL, 'handoff-review-clerk', 'handoff_ready',
                       'handoff-review:escalated-session',
                       'Escalated Signal', 'CLO is reviewing', 'escalated', 90, '{}', ?, ?)""",
            (signal_id, ws_id, now, now),
        )
        self.app.db.commit()
        count = runtime._cleanup_stale_signals(ws_id, active_source_keys=set())
        # Escalated signals should NOT be auto-resolved by cleanup.
        row = self.app.db.execute(
            "SELECT status FROM workspace_signals WHERE id = ?", (signal_id,)
        ).fetchone()
        assert row["status"] == "escalated"
