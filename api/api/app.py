# OpenCloset REST API — Flask app factory

from __future__ import annotations

import os
import sys
import weakref
from flask import Flask

from api.db.schema import init_db


def _patch_windows_path() -> None:
    """Append common Windows tool directories to PATH if they exist but are missing.

    Flask is often started from a terminal that doesn't carry the full system PATH,
    so exec subprocesses can't find tools like gh, git, or ssh even though they're
    installed. Patching here means every exec call in the session can find them
    without needing full hardcoded paths.
    """
    if sys.platform != "win32":
        return
    candidates = [
        r"C:\Program Files\GitHub CLI",
        r"C:\Program Files\Git\cmd",
        r"C:\Program Files\Git\usr\bin",
        r"C:\Windows\System32\OpenSSH",
        r"C:\Program Files\PowerShell\7",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links"),
        r"C:\sqlite",
    ]
    current = os.environ.get("PATH", "")
    additions = [p for p in candidates if os.path.isdir(p) and p not in current]
    if additions:
        os.environ["PATH"] = os.pathsep.join(additions) + os.pathsep + current


_patch_windows_path()


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = int(raw.strip())
    return value if value > 0 else None


def create_app(db_path: str | None = None, *, start_background_workers: bool | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        db_path: Path to SQLite database. Defaults to project root/opencloset.db.
        start_background_workers: Whether to start background pollers on app creation.
            When None, the factory auto-enables them for Flask CLI server runs or
            when OPENCLOSET_START_BACKGROUND_WORKERS is set.
    """
    app = Flask(__name__)

    try:
        from flask_sock import Sock
    except ImportError:
        Sock = None

    app.runtime_sock = Sock(app) if Sock is not None else None
    app.config["RUNTIME_WEBSOCKETS_ENABLED"] = app.runtime_sock is not None

    env_background_override = _env_flag("OPENCLOSET_START_BACKGROUND_WORKERS")
    if start_background_workers is None:
        if env_background_override is not None:
            start_background_workers = env_background_override
        else:
            start_background_workers = os.environ.get("FLASK_RUN_FROM_CLI") == "true"

    # -- DB setup --
    if db_path is None:
        db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "opencloset.db"
        )
        db_path = os.path.abspath(db_path)

    app.config["DB_PATH"] = db_path
    app.config.json_sort_keys = False

    # Initialize DB at app creation time for simplicity.
    # For production you'd use appcontext/requestctx patterns,
    # but the harness doesn't need high concurrency.
    app.db = init_db(db_path)

    # -- Shared process store for exec/process tools and UI process controls --
    from api.tools.process import ProcessStore
    app.process_store = ProcessStore()

    # -- Register routes --
    from api.api.routes import register_routes
    register_routes(app)
    from api.api.smart_usher import register_smart_usher_routes
    register_smart_usher_routes(app)

    # -- Transient Windows --
    from api.api.windows import TransientWindowManager, register_window_routes
    app.windows = TransientWindowManager(app.db)
    register_window_routes(app)

    # -- Streaming (SSE) --
    from api.api.streaming import EventQueueStore, register_streaming
    app.event_store = EventQueueStore()
    register_streaming(app, app.event_store)
    app.runtime_event_store = EventQueueStore()

    # -- Transcript Manager (single authoritative writer) --
    from api.api.transcript_manager import TranscriptManager
    app.transcript = TranscriptManager(app.db)

    # -- Per-run input envelopes (attachments, captures, modality hints) --
    from api.api.run_inputs import RunInputManager
    app.run_inputs = RunInputManager(app.db)

    # -- Event Logger (lifecycle audit trail) --
    from api.api.event_logger import init_events_table, EventLogger
    init_events_table(app.db)
    app.event_logger = EventLogger(app.db)

    # -- Run lifecycle manager --
    from api.api.run_lifecycle import RunManager
    app.run_manager = RunManager(app.db, app.event_store, app.transcript, app.event_logger)
    app.run_manager.reconcile_stale_running_runs()

    def _emit_subprocess_killed(handle, reason: str) -> None:
        from api.api.events import StreamEvent

        event = StreamEvent.subprocess_killed(
            handle.session_id,
            int(handle.pid or 0),
            handle.command,
            reason,
        )
        owner_session_id = str(getattr(handle, "owner_session_id", "") or "")
        owner_run_id = str(getattr(handle, "owner_run_id", "") or "")
        if not owner_session_id:
            return

        active_run = app.run_manager.get_active_run(owner_session_id)
        if active_run and owner_run_id and active_run.get("id") == owner_run_id:
            app.run_manager.emit_stream_event(owner_run_id, event)
            return

        app.event_logger.log_stream_event(owner_session_id, owner_run_id, event)

    app.process_store.set_termination_callback(_emit_subprocess_killed)

    app.config.setdefault(
        "EXECUTION_SUBSTRATE",
        str(os.environ.get("OPENCLOSET_EXECUTION_SUBSTRATE", "local") or "local").strip().lower(),
    )

    # -- Agent runner (single queued run execution) --
    from api.agent.runner import SessionAgentRunner
    from api.api.execution_runtime import LocalExecutionRuntime
    from api.api.execution_support import ExecutionSupportService

    app.agent_runner = SessionAgentRunner(app)
    app.execution_support = ExecutionSupportService(app)

    app.execution_runtime = LocalExecutionRuntime(app, app.agent_runner)

    # -- Runtime diagnostics (deterministic transient error windows) --
    from api.api.runtime_diagnostics import RuntimeDiagnosticsManager
    app.runtime_diagnostics = RuntimeDiagnosticsManager(
        app.db,
        app.event_logger,
        app.windows,
        execution_support=app.execution_support,
    )

    # -- Clo Queue (serial cross-session dispatch) --
    from api.api.clo_queue import CloQueueManager, CloQueueWorker, register_clo_queue_routes
    app.clo_queue = CloQueueManager(app)
    app.clo_queue_worker = CloQueueWorker(
        app,
        poll_interval_seconds=float(os.environ.get("OPENCLOSET_CLO_QUEUE_POLL_INTERVAL_SECONDS", "2")),
    )
    register_clo_queue_routes(app)

    app.config.setdefault("LLAMACPP_SERVER_URL", os.environ.get("OPENCLOSET_LLAMACPP_URL", "http://127.0.0.1:8080"))
    app.config.setdefault(
        "LLAMACPP_CACHE_PROMPT",
        _env_flag("OPENCLOSET_LLAMACPP_CACHE_PROMPT") if _env_flag("OPENCLOSET_LLAMACPP_CACHE_PROMPT") is not None else True,
    )
    app.config.setdefault("LLAMACPP_N_CACHE_REUSE", int(os.environ.get("OPENCLOSET_LLAMACPP_N_CACHE_REUSE", "256")))
    app.config.setdefault("LLAMACPP_REASONING_FORMAT", os.environ.get("OPENCLOSET_LLAMACPP_REASONING_FORMAT", "none"))
    app.config.setdefault(
        "LLAMACPP_ENABLE_THINKING",
        _env_flag("OPENCLOSET_LLAMACPP_ENABLE_THINKING") if _env_flag("OPENCLOSET_LLAMACPP_ENABLE_THINKING") is not None else False,
    )
    app.config.setdefault("LLAMACPP_REASONING_BUDGET", int(os.environ.get("OPENCLOSET_LLAMACPP_REASONING_BUDGET", "0")))
    app.config.setdefault("OLLAMA_SERVER_URL", os.environ.get("OPENCLOSET_OLLAMA_URL", "http://127.0.0.1:11434"))
    app.config.setdefault("OPENAI_BASE_URL", os.environ.get("OPENCLOSET_OPENAI_BASE_URL", "https://api.openai.com/v1"))
    app.config.setdefault("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    app.config.setdefault("OPENAI_MODEL", os.environ.get("OPENCLOSET_OPENAI_MODEL", "gpt-4.1-mini"))
    app.config.setdefault("PROVIDER_TIMEOUT_SECONDS", float(os.environ.get("OPENCLOSET_PROVIDER_TIMEOUT_SECONDS", "1800")))
    provider_stream_idle_timeout_raw = os.environ.get("OPENCLOSET_PROVIDER_STREAM_IDLE_TIMEOUT_SECONDS", "120")
    app.config.setdefault(
        "PROVIDER_STREAM_IDLE_TIMEOUT_SECONDS",
        float(provider_stream_idle_timeout_raw) if provider_stream_idle_timeout_raw not in (None, "") else None,
    )
    app.config.setdefault("WORKSPACE_ROOT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
    app.config.setdefault(
        "DEFAULT_TOOL_ALLOWLIST",
        [
            "list_dir",
            "read",
            "write",
            "edit",
            "exec",
            "process",
            "memory_search",
            "plan_get_active",
            "plan_list_stored",
            "plan_add_item",
            "plan_set_status",
            "plan_create",
            "plan_activate",
            "plan_reorder",
            "plan_archive",
            "plan_list_proposals",
            "plan_propose_change",
            "plan_accept_proposal",
            "plan_reject_proposal",
        ],
    )
    app.config.setdefault(
        "RUNTIME_BASELINE_TOOL_ALLOWLIST",
        [
            "list_dir",
            "plan_get_active",
            "plan_list_stored",
            "plan_list_proposals",
        ],
    )
    app.config.setdefault("LOOP_MAX_TOOL_CALLS_PER_TURN", int(os.environ.get("OPENCLOSET_LOOP_MAX_TOOL_CALLS_PER_TURN", "4")))
    app.config.setdefault("LOOP_MAX_TURNS", _env_optional_int("OPENCLOSET_LOOP_MAX_TURNS"))
    app.config.setdefault("CLO_QUEUE_MAX_TURNS", _env_optional_int("OPENCLOSET_CLO_QUEUE_MAX_TURNS"))
    app.config.setdefault("LOOP_TEMPERATURE", float(os.environ.get("OPENCLOSET_LOOP_TEMPERATURE", "0.2")))
    app.config.setdefault("LOOP_MAX_TOKENS", int(os.environ.get("OPENCLOSET_LOOP_MAX_TOKENS", "4096")))
    app.config.setdefault(
        "LOOP_MAX_CONSECUTIVE_FAILURE_BATCHES",
        int(os.environ.get("OPENCLOSET_LOOP_MAX_CONSECUTIVE_FAILURE_BATCHES", "3")),
    )
    app.config.setdefault("DELEGATION_MAX_TOKENS", int(os.environ.get("OPENCLOSET_DELEGATION_MAX_TOKENS", "768")))
    app.config.setdefault(
        "DELEGATION_POLL_INTERVAL_SECONDS",
        float(os.environ.get("OPENCLOSET_DELEGATION_POLL_INTERVAL_SECONDS", "2")),
    )
    app.config.setdefault(
        "ENABLE_ROLLOVER_GUARD",
        _env_flag("OPENCLOSET_ENABLE_ROLLOVER_GUARD") if _env_flag("OPENCLOSET_ENABLE_ROLLOVER_GUARD") is not None else False,
    )
    app.config.setdefault("LOOP_PAUSE_THRESHOLD_PCT", float(os.environ.get("OPENCLOSET_LOOP_PAUSE_THRESHOLD_PCT", "88.5")))
    app.config.setdefault("WATCHDOG_POLL_INTERVAL_SECONDS", float(os.environ.get("OPENCLOSET_WATCHDOG_POLL_INTERVAL_SECONDS", "30")))
    app.config.setdefault("MAINTENANCE_POLL_INTERVAL_SECONDS", float(os.environ.get("OPENCLOSET_MAINTENANCE_POLL_INTERVAL_SECONDS", "30")))
    app.config.setdefault("WORKSPACE_RUNTIME_POLL_INTERVAL_SECONDS", float(os.environ.get("OPENCLOSET_WORKSPACE_RUNTIME_POLL_INTERVAL_SECONDS", "30")))
    app.config.setdefault("SCHEDULER_POLL_INTERVAL_SECONDS", float(os.environ.get("OPENCLOSET_SCHEDULER_POLL_INTERVAL_SECONDS", "15")))
    app.config.setdefault("AGENT_CHANNEL_TICK_INTERVAL_SECONDS", int(os.environ.get("OPENCLOSET_AGENT_CHANNEL_TICK_INTERVAL_SECONDS", "300")))
    app.config.setdefault("RUNTIME_STREAM_HISTORY_LIMIT", int(os.environ.get("OPENCLOSET_RUNTIME_STREAM_HISTORY_LIMIT", "200")))
    app.config.setdefault("MAINTENANCE_IDLE_THRESHOLD_SECONDS", float(os.environ.get("OPENCLOSET_MAINTENANCE_IDLE_THRESHOLD_SECONDS", "15")))
    app.config.setdefault("MAINTENANCE_SUMMARY_MESSAGE_LIMIT", int(os.environ.get("OPENCLOSET_MAINTENANCE_SUMMARY_MESSAGE_LIMIT", "6")))
    app.config.setdefault(
        "MEMORY_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "memory")),
    )
    app.config.setdefault(
        "UPLOAD_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "uploads")),
    )
    app.config.setdefault("MEMORY_EMBEDDING_MODEL", os.environ.get("OPENCLOSET_MEMORY_EMBEDDING_MODEL", ""))
    app.config.setdefault(
        "MEMORY_EMBEDDING_TIMEOUT_SECONDS",
        float(os.environ.get("OPENCLOSET_MEMORY_EMBEDDING_TIMEOUT_SECONDS", "30")),
    )

    # -- Planning --
    from api.api.planning import init_planning_table, PlanningManager, register_planning_routes
    init_planning_table(app.db)
    app.planning = PlanningManager(app.db, app.event_logger)
    app.planning.rollover_guard_enabled = bool(app.config["ENABLE_ROLLOVER_GUARD"])
    register_planning_routes(app)

    # -- Delegated read-only worker execution --
    from api.api.delegation import DelegationManager, DelegationWorker, register_delegation_routes
    app.delegations = DelegationManager(app)
    app.delegation_worker = DelegationWorker(
        app,
        poll_interval_seconds=float(app.config["DELEGATION_POLL_INTERVAL_SECONDS"]),
    )
    register_delegation_routes(app)

    # -- Workspaces + Build Projects --
    from api.api.workspaces import WorkspaceManager, register_workspace_routes
    app.workspaces = WorkspaceManager(app.db, app.event_logger, upload_root=app.config["UPLOAD_ROOT"])
    app.planning.workspaces = app.workspaces
    register_workspace_routes(app)

    # -- File-backed memory baseline --
    from api.api.memory import MemoryManager, OllamaEmbeddingClient, register_memory_routes
    memory_embedder = None
    if app.config["MEMORY_EMBEDDING_MODEL"]:
        memory_embedder = OllamaEmbeddingClient(
            base_url=app.config["OLLAMA_SERVER_URL"],
            model_name=app.config["MEMORY_EMBEDDING_MODEL"],
            timeout=float(app.config["MEMORY_EMBEDDING_TIMEOUT_SECONDS"]),
        )
    app.memory = MemoryManager(app.config["MEMORY_ROOT"], embedder=memory_embedder)
    register_memory_routes(app)

    # -- Durable behavior feedback + scoped policy patches --
    from api.api.behavior import BehaviorManager, init_behavior_tables, register_behavior_routes
    init_behavior_tables(app.db)
    app.behavior = BehaviorManager(app.db)
    register_behavior_routes(app)

    # -- Durable binary session attachments --
    from api.api.session_attachments import SessionAttachmentManager
    app.session_attachments = SessionAttachmentManager(app)

    # -- Maintenance artifacts + worker --
    from api.api.maintenance import (
        MaintenanceManager,
        SessionMaintenanceWorker,
        init_maintenance_table,
        register_maintenance_routes,
    )
    init_maintenance_table(app.db)
    app.maintenance = MaintenanceManager(app.db)
    app.maintenance_worker = SessionMaintenanceWorker(
        app,
        poll_interval_seconds=float(app.config["MAINTENANCE_POLL_INTERVAL_SECONDS"]),
        idle_threshold_seconds=float(app.config["MAINTENANCE_IDLE_THRESHOLD_SECONDS"]),
        summary_message_limit=int(app.config["MAINTENANCE_SUMMARY_MESSAGE_LIMIT"]),
    )
    register_maintenance_routes(app)

    # -- Operational workspace runtime (minimal scheduler + ambient worker seam) --
    from api.api.workspace_runtime import (
        WorkspaceRuntimeManager,
        WorkspaceRuntimeWorker,
        init_workspace_runtime_table,
        register_workspace_runtime_routes,
    )
    init_workspace_runtime_table(app.db)
    app.workspace_runtime = WorkspaceRuntimeManager(
        app.db,
        workspaces=app.workspaces,
        planning=app.planning,
        maintenance=app.maintenance,
        run_manager=app.run_manager,
        event_logger=app.event_logger,
    )
    app.workspace_runtime_worker = WorkspaceRuntimeWorker(
        app,
        poll_interval_seconds=float(app.config["WORKSPACE_RUNTIME_POLL_INTERVAL_SECONDS"]),
    )
    register_workspace_runtime_routes(app)

    # -- Domain harness registry for headless runtime channels --
    from api.api.agent_harnesses import AgentHarnessRegistry, EveOpsHarness, PokemonHarness
    app.agent_harnesses = AgentHarnessRegistry()
    app.agent_harnesses.register("eve", EveOpsHarness())
    app.agent_harnesses.register("pokemon", PokemonHarness())

    # -- Headless runtime / agent channels --
    from api.api.agent_channels import AgentChannelManager, register_agent_channel_routes
    app.agent_channels = AgentChannelManager(app, app.runtime_event_store, harness_registry=app.agent_harnesses)
    register_agent_channel_routes(app)

    # -- Pokemon bridge sidecar and schema routes --
    from api.api.pokemon_bridge import PokemonBridgeManager, register_pokemon_bridge_routes
    app.pokemon_bridge = PokemonBridgeManager(app)
    register_pokemon_bridge_routes(app)

    # -- Watchdog (external poller uses the same service object) --
    from api.api.watchdog import SessionWatchdog
    app.watchdog = SessionWatchdog(
        app,
        poll_interval_seconds=float(app.config["WATCHDOG_POLL_INTERVAL_SECONDS"]),
    )

    # -- Scheduler: unified arbiter + job persistence + cron runner --
    from api.api.scheduler import (
        SchedulerArbiter,
        SchedulerJobManager,
        SchedulerRunner,
        SchedulerWorker,
        init_scheduler_table,
        register_scheduler_routes,
    )
    init_scheduler_table(app.db)
    app.scheduler_jobs = SchedulerJobManager(app.db)
    app.scheduler_arbiter = SchedulerArbiter(app.db, job_manager=app.scheduler_jobs)
    from api.api.reflective_pastimes import ReflectivePastimeManager
    app.reflective_pastimes = ReflectivePastimeManager(
        app.db,
        workspaces=app.workspaces,
        planning=app.planning,
        event_logger=app.event_logger,
    )
    app.scheduler_runner = SchedulerRunner(app.db, job_manager=app.scheduler_jobs)
    app.scheduler_worker = SchedulerWorker(
        app,
        poll_interval_seconds=float(app.config["SCHEDULER_POLL_INTERVAL_SECONDS"]),
    )
    app.scheduler_runner.register_handler("agent_channel_tick", app.agent_channels.run_scheduled_tick)
    # Candidate producers — workspace runtime, reflective pastimes, maintenance, watchdog.
    from api.api.scheduler_producers import MaintenanceCandidateProducer, WatchdogCandidateProducer
    app.maintenance_producer = MaintenanceCandidateProducer(
        app,
        idle_threshold_seconds=float(app.config["MAINTENANCE_IDLE_THRESHOLD_SECONDS"]),
    )
    app.watchdog_producer = WatchdogCandidateProducer(app)
    app.scheduler_arbiter.register_producer(app.workspace_runtime)
    app.scheduler_arbiter.register_producer(app.reflective_pastimes)
    app.scheduler_arbiter.register_producer(app.maintenance_producer)
    app.scheduler_arbiter.register_producer(app.watchdog_producer)
    app.workspace_runtime.arbiter = app.scheduler_arbiter
    app.workspace_runtime.reflective_pastimes = app.reflective_pastimes
    register_scheduler_routes(app)

    # -- Briefing manager ("when user returns" workspace digest) --
    from api.api.briefing import WorkspaceBriefingManager, register_briefing_routes
    app.briefing = WorkspaceBriefingManager(
        app.db,
        workspace_runtime=app.workspace_runtime,
        planning=app.planning,
        workspaces=app.workspaces,
        event_logger=app.event_logger,
    )
    # Register workspace_briefing as a cron-runnable job type.
    def _briefing_job_handler(job: dict) -> dict:
        ws_id = job.get("workspace_id")
        if not ws_id:
            return {"skipped": "no workspace_id"}
        evidence = app.briefing.generate_briefing(ws_id)
        return {"evidence_id": evidence.get("id") if isinstance(evidence, dict) else str(evidence)}
    app.scheduler_runner.register_handler("workspace_briefing", _briefing_job_handler)
    register_briefing_routes(app)

    if start_background_workers:
        for worker_name in ("maintenance_worker", "workspace_runtime_worker", "watchdog", "scheduler_worker", "clo_queue_worker", "delegation_worker"):
            worker = getattr(app, worker_name, None)
            start_background = getattr(worker, "start_background", None)
            if callable(start_background):
                start_background()

    close_state = {"closed": False}

    def _close_app() -> None:
        if close_state["closed"]:
            return
        close_state["closed"] = True

        for worker_name in ("maintenance_worker", "workspace_runtime_worker", "watchdog", "scheduler_worker", "clo_queue_worker", "delegation_worker"):
            worker = getattr(app, worker_name, None)
            stop_background = getattr(worker, "stop_background", None)
            if callable(stop_background):
                try:
                    stop_background(timeout=0.1)
                except Exception:
                    pass

        memory_manager = getattr(app, "memory", None)
        close_memory = getattr(memory_manager, "close", None)
        if callable(close_memory):
            try:
                close_memory()
            except Exception:
                pass

        db = getattr(app, "db", None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    app.close = _close_app
    weakref.finalize(app, _close_app)

    from flask import jsonify as _jsonify

    @app.errorhandler(Exception)
    def _handle_exception(exc):
        import traceback
        traceback.print_exc()
        return _jsonify({"error": str(exc) or type(exc).__name__}), 500

    @app.errorhandler(404)
    def _handle_404(exc):
        return _jsonify({"error": "not found"}), 404

    return app
