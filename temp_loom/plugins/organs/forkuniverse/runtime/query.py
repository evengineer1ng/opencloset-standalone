from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from forkuniverse.compiler.models import CompiledWorldPackage
from forkuniverse.engine.world_core import UniverseState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class UniverseQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str
    mode: Literal["broadcast_digest", "active_listen", "state_snapshot"] = "broadcast_digest"
    since: Literal["last_radio_check", "last_compute", "explicit"] = "last_radio_check"
    since_timestamp: Optional[str] = None
    max_events: int = Field(default=8, ge=1, le=100)
    heat_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    include_threads: bool = True
    include_resolved_predictions: bool = True
    include_new_predictions: bool = True
    now_timestamp: Optional[str] = None


class TruthComputationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str
    mode: str
    universe_time: str
    elapsed_sim_time: str
    headline: str
    events: List[Dict[str, Any]]
    threads: List[Dict[str, Any]]
    resolved_predictions: List[Dict[str, Any]]
    new_predictions: List[Dict[str, Any]]
    heat: float
    query_metadata: Dict[str, Any]


def _elapsed_seconds_for_query(
    package: CompiledWorldPackage,
    request: UniverseQueryRequest,
) -> float:
    now_dt = _parse_dt(request.now_timestamp) or _utc_now()
    if request.since == "explicit" and request.since_timestamp:
        since_dt = _parse_dt(request.since_timestamp)
    else:
        memory_rows = package.world_tables.get("memory_records", [])
        latest_check = None
        for row in memory_rows:
            if row.get("memory_tier") == "system":
                latest_check = row.get("summary")
        since_dt = _parse_dt(latest_check) if latest_check else None
    if since_dt is None:
        return 0.0
    return max(0.0, (now_dt - since_dt).total_seconds())


def _sim_seconds(elapsed_real_seconds: float, package: CompiledWorldPackage) -> float:
    ratio = float(package.time_policy.get("world_seconds_per_real_second") or 60.0)
    return elapsed_real_seconds * ratio


def _format_universe_time(sim_seconds: float) -> str:
    total_days = int(sim_seconds // 86400)
    year = 1 + (total_days // 360)
    day_of_year = 1 + (total_days % 360)
    season_names = ["Spring", "Summer", "Autumn", "Winter"]
    season = season_names[((day_of_year - 1) // 90) % 4]
    day_in_season = 1 + ((day_of_year - 1) % 90)
    return f"Year {year}, {season}, Day {day_in_season}"


def _format_elapsed_sim_time(sim_seconds: float) -> str:
    if sim_seconds < 60:
        return f"{int(sim_seconds)} seconds"
    if sim_seconds < 3600:
        return f"{int(sim_seconds // 60)} minutes"
    if sim_seconds < 86400:
        return f"{int(sim_seconds // 3600)} hours"
    return f"{int(sim_seconds // 86400)} days"


def _event_from_thread(thread: Dict[str, Any], mode: str) -> Dict[str, Any]:
    heat = float(thread.get("heat", 0.0))
    urgency = float(thread.get("urgency", 0.0))
    status = thread.get("status", "active")
    title = thread.get("title", "Unnamed thread")
    if mode == "active_listen":
        summary = f"{title} is still in motion."
    else:
        summary = f"{title} remains one of the most important unresolved developments."
    return {
        "event_type": "thread_status",
        "title": title,
        "summary": summary,
        "heat": heat,
        "urgency": urgency,
        "status": status,
    }


def _prediction_views(package: CompiledWorldPackage, include: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not include:
        return [], []
    resolved: List[Dict[str, Any]] = []
    new: List[Dict[str, Any]] = []
    for row in package.world_tables.get("predictions", []):
        if row.get("status") == "resolved":
            resolved.append(row)
        else:
            new.append(row)
    return resolved, new


def _compute_truth_live(
    package: CompiledWorldPackage,
    request: UniverseQueryRequest,
    state: UniverseState,
) -> TruthComputationResult:
    """Drive the live engine: convert elapsed real time into owed ticks, advance
    the authoritative ``UniverseState``, and surface the resulting delta.

    Unlike the static path, this reads the engine's actual evolving threads,
    events, and prediction settlements rather than the compiler's seeded tables.
    """
    elapsed_real_seconds = _elapsed_seconds_for_query(package, request)
    sim_seconds = _sim_seconds(elapsed_real_seconds, package)

    from_tick = state.time.tick
    delta = state.compute_absence(elapsed_real_seconds)

    events = sorted(
        delta.new_events,
        key=lambda row: abs(float(row.get("pressure_delta", 0.0))),
        reverse=True,
    )[: request.max_events]

    threads = [
        row
        for row in delta.thread_deltas
        if float(row.get("heat", 0.0)) >= request.heat_threshold
    ][: request.max_events]

    resolved_predictions = (
        delta.settled_predictions[: request.max_events]
        if request.include_resolved_predictions
        else []
    )
    new_predictions = (
        delta.opened_predictions[: request.max_events]
        if request.include_new_predictions
        else []
    )

    headline = delta.headline
    if request.mode == "active_listen":
        headline = f"Active thread: {headline}"
    elif request.mode == "broadcast_digest":
        headline = f"Breaking from {package.package_identity.universe_title}: {headline}"

    world_seconds_per_tick = (
        state.time.world_seconds_per_real_second * state.time.real_seconds_per_tick
    )

    query_metadata = {
        "elapsed_real_seconds": elapsed_real_seconds,
        "computed_world_seconds": sim_seconds,
        "query_driven": True,
        "live_engine": True,
        "execution_model": package.time_policy.get("execution_model", "on_demand"),
        "from_tick": from_tick,
        "to_tick": state.time.tick,
        "ticks_advanced": state.time.tick - from_tick,
        "prediction_scorecard": delta.prediction_scorecard,
    }

    return TruthComputationResult(
        universe_id=request.universe_id,
        mode=request.mode,
        universe_time=_format_universe_time(state.time.tick * world_seconds_per_tick),
        elapsed_sim_time=_format_elapsed_sim_time(sim_seconds),
        headline=headline,
        events=events,
        threads=threads if request.include_threads else [],
        resolved_predictions=resolved_predictions,
        new_predictions=new_predictions,
        heat=round(delta.heat, 3),
        query_metadata=query_metadata,
    )


def compute_truth(
    package: CompiledWorldPackage,
    request: UniverseQueryRequest,
    state: Optional[UniverseState] = None,
) -> TruthComputationResult:
    if state is not None:
        return _compute_truth_live(package, request, state)

    elapsed_real_seconds = _elapsed_seconds_for_query(package, request)
    sim_seconds = _sim_seconds(elapsed_real_seconds, package)

    threads = package.world_tables.get("story_threads", [])
    filtered_threads = [
        thread for thread in threads if float(thread.get("heat", 0.0)) >= request.heat_threshold
    ]
    filtered_threads.sort(
        key=lambda row: (float(row.get("heat", 0.0)), float(row.get("urgency", 0.0))),
        reverse=True,
    )
    filtered_threads = filtered_threads[: request.max_events]

    events = [_event_from_thread(thread, request.mode) for thread in filtered_threads]
    resolved_predictions, new_predictions = _prediction_views(
        package,
        request.include_resolved_predictions or request.include_new_predictions,
    )
    if not request.include_resolved_predictions:
        resolved_predictions = []
    if not request.include_new_predictions:
        new_predictions = []

    if filtered_threads:
        headline = filtered_threads[0].get("title", "No current headline")
        heat = max(float(thread.get("heat", 0.0)) for thread in filtered_threads)
    else:
        headline = "No major developments cleared the current broadcast threshold."
        heat = 0.0

    if request.mode == "active_listen" and filtered_threads:
        headline = f"Active thread: {headline}"
    elif request.mode == "broadcast_digest" and filtered_threads:
        headline = f"Breaking from {package.package_identity.universe_title}: {headline}"

    query_metadata = {
        "elapsed_real_seconds": elapsed_real_seconds,
        "computed_world_seconds": sim_seconds,
        "query_driven": bool(package.time_policy.get("query_driven", False)),
        "execution_model": package.time_policy.get("execution_model", "on_demand"),
    }

    return TruthComputationResult(
        universe_id=request.universe_id,
        mode=request.mode,
        universe_time=_format_universe_time(sim_seconds),
        elapsed_sim_time=_format_elapsed_sim_time(sim_seconds),
        headline=headline,
        events=events[: request.max_events],
        threads=filtered_threads if request.include_threads else [],
        resolved_predictions=resolved_predictions[: request.max_events],
        new_predictions=new_predictions[: request.max_events],
        heat=round(heat, 3),
        query_metadata=query_metadata,
    )
