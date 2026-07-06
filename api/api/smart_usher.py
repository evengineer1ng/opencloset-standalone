from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request

from api.db.schema import new_id


_MEDIA_ITEM_RE = re.compile(
    r"^(?P<source>tmdb|imdb|kodi):(?P<kind>[^:]+):(?P<identifier>[^:]+?)(?::s(?P<season>\d+)e(?P<episode>\d+))?$"
)

_SETTINGS_KEY = "usher_settings"
_PLAYBACK_KEY = "playback"
_QUEUE_KEY = "last_queue"
_DEFAULT_SETTINGS = {
    "provider": "llama_cpp",
    "model": "",
    "base_url": "http://127.0.0.1:8080",
    "timeout_seconds": 30.0,
}


def register_smart_usher_routes(app: Flask) -> None:
    _ensure_tables(app)

    @app.route("/api/usher/settings", methods=["GET"])
    def get_usher_settings():
        return jsonify(_load_state(app, _SETTINGS_KEY, _DEFAULT_SETTINGS))

    @app.route("/api/usher/settings", methods=["PUT"])
    def put_usher_settings():
        data = request.get_json(silent=True) or {}
        settings = _load_state(app, _SETTINGS_KEY, _DEFAULT_SETTINGS)

        provider = str(data.get("provider", settings["provider"]) or "").strip()
        model = str(data.get("model", settings["model"]) or "").strip()
        base_url = str(data.get("base_url", settings["base_url"]) or "").strip()
        timeout_seconds = data.get("timeout_seconds", settings["timeout_seconds"])

        if not provider:
            return jsonify({"error": "provider is required"}), 400
        if not base_url:
            return jsonify({"error": "base_url is required"}), 400
        try:
            timeout_value = float(timeout_seconds)
        except (TypeError, ValueError):
            return jsonify({"error": "timeout_seconds must be numeric"}), 400
        if timeout_value <= 0:
            return jsonify({"error": "timeout_seconds must be greater than zero"}), 400

        next_settings = {
            "provider": provider,
            "model": model,
            "base_url": base_url.rstrip("/"),
            "timeout_seconds": timeout_value,
        }
        _store_state(app, _SETTINGS_KEY, next_settings)
        return jsonify(next_settings)

    @app.route("/api/playback/current", methods=["GET"])
    def get_current_playback():
        playback = _load_state(app, _PLAYBACK_KEY, {})
        return jsonify({"current": _present_current_state(app, playback)})

    @app.route("/api/playback/events", methods=["POST"])
    def post_playback_event():
        data = request.get_json(silent=True) or {}
        event_type = str(data.get("event_type") or "").strip()
        if not event_type:
            return jsonify({"error": "event_type is required"}), 400

        playback = _record_playback_event(app, data)
        return jsonify({"ok": True, "current": _present_current_state(app, playback)})

    @app.route("/api/chat/messages", methods=["POST"])
    def post_chat_message():
        data = request.get_json(silent=True) or {}
        message = str(data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message is required"}), 400

        playback = _load_state(app, _PLAYBACK_KEY, {})
        current = _present_current_state(app, playback)
        lowered = message.lower()

        if any(term in lowered for term in ("queue", "tonight", "up next", "binge", "next episode")):
            queue = _generate_queue(app, data, playback)
            text = _summarize_queue(queue)
        else:
            title = (current.get("media_item") or {}).get("title") or "nothing yet"
            if current.get("chat_visible_allowed"):
                text = (
                    f"You are on {title}. I can keep this spoiler-safe, answer questions about the current watch, "
                    "or queue the next block immediately."
                )
            else:
                text = (
                    "Playback state is not live yet. I can still generate a queue from the last known watch context "
                    "once Kodi sends playback events."
                )

        return jsonify({
            "answer": {"text": text, "citations": []},
            "guard": {"blocked_sources": []},
        })

    @app.route("/api/queue/generate", methods=["POST"])
    def post_generate_queue():
        data = request.get_json(silent=True) or {}
        playback = _load_state(app, _PLAYBACK_KEY, {})
        queue = _generate_queue(app, data, playback)
        return jsonify({"queue": queue})

    @app.route("/api/kodi/playlist/push", methods=["POST"])
    def post_push_playlist():
        data = request.get_json(silent=True) or {}
        queue_id = str(data.get("queue_id") or "").strip()
        queue = _load_state(app, _QUEUE_KEY, {"items": []})
        if not queue_id:
            return jsonify({"error": "queue_id is required"}), 400
        if queue.get("queue_id") != queue_id:
            return jsonify({"error": "queue not found"}), 404

        items = queue.get("items") or []
        return jsonify({
            "queue_id": queue_id,
            "device_id": str(data.get("device_id") or "").strip(),
            "pushed_count": 0,
            "skipped_count": len(items),
            "mode": "backend",
            "note": "Direct playlist injection is handled in Kodi via the addon Umbrella path.",
        })


def _ensure_tables(app: Flask) -> None:
    app.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS smart_usher_state (
            state_key       TEXT PRIMARY KEY,
            state_json      TEXT NOT NULL DEFAULT '{}',
            updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS smart_usher_events (
            id              TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            profile_id      TEXT NOT NULL DEFAULT 'default',
            device_id       TEXT NOT NULL DEFAULT '',
            occurred_at     TEXT NOT NULL,
            payload_json    TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_smart_usher_events_device
            ON smart_usher_events(device_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_smart_usher_events_profile
            ON smart_usher_events(profile_id, occurred_at DESC);
        """
    )
    app.db.commit()


def _load_state(app: Flask, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = app.db.execute(
        "SELECT state_json FROM smart_usher_state WHERE state_key = ?",
        (key,),
    ).fetchone()
    if not row:
        return deepcopy(default)
    try:
        loaded = json.loads(row["state_json"])
    except (TypeError, json.JSONDecodeError):
        return deepcopy(default)
    if not isinstance(loaded, dict):
        return deepcopy(default)
    return loaded


def _store_state(app: Flask, key: str, value: dict[str, Any]) -> None:
    app.db.execute(
        """
        INSERT INTO smart_usher_state (state_key, state_json, updated_at)
        VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT(state_key) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (key, json.dumps(value),),
    )
    app.db.commit()


def _record_playback_event(app: Flask, payload: dict[str, Any]) -> dict[str, Any]:
    previous = _load_state(app, _PLAYBACK_KEY, {})
    event_type = str(payload.get("event_type") or "player.progress").strip()
    profile_id = str(payload.get("profile_id") or previous.get("profile_id") or "default")
    device_id = str(payload.get("device_id") or previous.get("device_id") or "")
    occurred_at = str(payload.get("occurred_at") or _now_iso())

    incoming_player = payload.get("player") or {}
    previous_player = previous.get("player") or {}
    normalized_player = {
        "item_type": str(incoming_player.get("item_type") or previous_player.get("item_type") or "movie"),
        "media_item_id": str(incoming_player.get("media_item_id") or previous_player.get("media_item_id") or ""),
        "title": str(incoming_player.get("title") or previous_player.get("title") or "Smart Usher"),
        "show_title": str(incoming_player.get("show_title") or previous_player.get("show_title") or incoming_player.get("title") or previous_player.get("show_title") or ""),
        "season_number": _as_int(incoming_player.get("season_number"), previous_player.get("season_number")),
        "episode_number": _as_int(incoming_player.get("episode_number"), previous_player.get("episode_number")),
        "runtime_minutes": _as_int(incoming_player.get("runtime_minutes"), previous_player.get("runtime_minutes")),
        "position_seconds": _as_float(incoming_player.get("position_seconds"), previous_player.get("position_seconds")),
        "is_paused": _as_bool(incoming_player.get("is_paused"), previous_player.get("is_paused")),
    }
    normalized_player["ids"] = _extract_ids(normalized_player["media_item_id"])

    playback = {
        "profile_id": profile_id,
        "device_id": device_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "playback_active": event_type != "player.stop" and bool(normalized_player["media_item_id"]),
        "player": normalized_player,
        "osd": payload.get("osd") or previous.get("osd") or {"visible": False},
        "media_item": _build_media_item(normalized_player),
    }

    app.db.execute(
        """
        INSERT INTO smart_usher_events (id, event_type, profile_id, device_id, occurred_at, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (new_id(), event_type, profile_id, device_id, occurred_at, json.dumps(payload)),
    )
    app.db.commit()
    _store_state(app, _PLAYBACK_KEY, playback)
    return playback


def _present_current_state(app: Flask, playback: dict[str, Any]) -> dict[str, Any]:
    player = playback.get("player") or {}
    queue = _load_state(app, _QUEUE_KEY, {"items": []})
    position_seconds = _as_float(player.get("position_seconds"), 0.0)
    current = {
        "media_item": playback.get("media_item") or _build_media_item(player),
        "session": {
            "profile_id": str(playback.get("profile_id") or "default"),
            "device_id": str(playback.get("device_id") or ""),
            "current_position_seconds": position_seconds,
            "safe_through_seconds": max(int(position_seconds) - 90, 0),
            "playback_active": bool(playback.get("playback_active")),
            "is_paused": bool(player.get("is_paused")),
        },
        "queue": queue,
        "chat_visible_allowed": bool(player.get("media_item_id")),
    }
    thread_id = _build_thread_id(
        str(playback.get("profile_id") or "default"),
        str(playback.get("device_id") or ""),
        str(player.get("media_item_id") or ""),
    )
    if thread_id:
        current["active_thread_id"] = thread_id
    return current


def _generate_queue(app: Flask, request_payload: dict[str, Any], playback: dict[str, Any]) -> dict[str, Any]:
    player = playback.get("player") or {}
    existing_queue = _load_state(app, _QUEUE_KEY, {"items": []})
    time_budget_minutes = max(_as_int(request_payload.get("time_budget_minutes"), 120), 30)
    prioritize_current_series = bool(request_payload.get("prioritize_current_series", True))

    if player.get("item_type") == "episode" and prioritize_current_series and player.get("media_item_id"):
        queue = _build_episode_queue(playback, time_budget_minutes)
    elif player.get("media_item_id"):
        queue = _build_single_item_queue(playback)
    elif existing_queue.get("items"):
        queue = dict(existing_queue)
        queue["queue_id"] = new_id()
        queue["created_at"] = _now_iso()
    else:
        queue = {
            "queue_id": new_id(),
            "name": "Tonight Queue",
            "created_at": _now_iso(),
            "items": [],
            "summary": "No playback context is available yet.",
        }

    _store_state(app, _QUEUE_KEY, queue)
    return queue


def _build_episode_queue(playback: dict[str, Any], time_budget_minutes: int) -> dict[str, Any]:
    player = playback.get("player") or {}
    ids = dict(player.get("ids") or {})
    runtime_minutes = max(_as_int(player.get("runtime_minutes"), 45), 20)
    season_number = max(_as_int(player.get("season_number"), 1), 1)
    episode_number = max(_as_int(player.get("episode_number"), 0), 0)
    item_count = min(max(time_budget_minutes // runtime_minutes, 1), 4)
    show_title = str(player.get("show_title") or player.get("title") or "Current Series")

    items = []
    for offset in range(1, item_count + 1):
        next_episode = episode_number + offset if episode_number else offset
        items.append({
            "item_type": "episode",
            "media_type": "episode",
            "title": f"{show_title} S{season_number:02d}E{next_episode:02d}",
            "show_title": show_title,
            "tvshowtitle": show_title,
            "season": season_number,
            "episode": next_episode,
            "tmdb": ids.get("tmdb", ""),
            "imdb": ids.get("imdb", ""),
            "tvdb": ids.get("tvdb", ""),
            "ids": ids,
        })

    return {
        "queue_id": new_id(),
        "name": f"Continue {show_title}",
        "created_at": _now_iso(),
        "summary": f"Queued the next {len(items)} episode(s) from the current series.",
        "items": items,
    }


def _build_single_item_queue(playback: dict[str, Any]) -> dict[str, Any]:
    player = playback.get("player") or {}
    ids = dict(player.get("ids") or {})
    item_type = str(player.get("item_type") or "movie")
    title = str(player.get("title") or "Tonight Pick")
    item = {
        "item_type": item_type,
        "media_type": item_type,
        "title": title,
        "tmdb": ids.get("tmdb", ""),
        "imdb": ids.get("imdb", ""),
        "tvdb": ids.get("tvdb", ""),
        "ids": ids,
    }
    return {
        "queue_id": new_id(),
        "name": f"Tonight Queue: {title}",
        "created_at": _now_iso(),
        "summary": "Queued the current title because no episodic continuation context was available.",
        "items": [item],
    }


def _build_media_item(player: dict[str, Any]) -> dict[str, Any]:
    if not player:
        return {}
    media_item = {
        "title": str(player.get("title") or "Smart Usher"),
        "item_type": str(player.get("item_type") or "movie"),
        "media_item_id": str(player.get("media_item_id") or ""),
        "season_number": _as_int(player.get("season_number"), None),
        "episode_number": _as_int(player.get("episode_number"), None),
    }
    ids = dict(player.get("ids") or {})
    media_item["ids"] = ids
    if ids.get("tmdb"):
        media_item["tmdb"] = ids["tmdb"]
    if ids.get("imdb"):
        media_item["imdb"] = ids["imdb"]
    if ids.get("tvdb"):
        media_item["tvdb"] = ids["tvdb"]
    return media_item


def _extract_ids(media_item_id: str) -> dict[str, str]:
    match = _MEDIA_ITEM_RE.match(str(media_item_id or ""))
    if not match:
        return {}
    source = match.group("source")
    identifier = match.group("identifier")
    if source == "tmdb":
        return {"tmdb": identifier}
    if source == "imdb":
        return {"imdb": identifier}
    return {}


def _build_thread_id(profile_id: str, device_id: str, media_item_id: str) -> str:
    if not media_item_id:
        return ""
    return f"{profile_id}:{device_id}:{media_item_id}"


def _summarize_queue(queue: dict[str, Any]) -> str:
    items = queue.get("items") or []
    if not items:
        return "I do not have enough playback context yet to build a queue. Start something in Kodi and I can take it from there."
    return f"Built {len(items)} item(s) for {queue.get('name') or 'Tonight Queue'}."


def _as_int(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool | None = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")