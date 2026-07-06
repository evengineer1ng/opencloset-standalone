from __future__ import annotations

import os
import tempfile

from api.api.app import create_app


def _make_client():
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    db_path = handle.name
    handle.close()
    app = create_app(db_path=db_path, start_background_workers=False)
    client = app.test_client()
    return app, client, db_path


def test_episode_playback_generates_real_queue():
    app, client, db_path = _make_client()
    try:
        event = {
            "event_type": "player.start",
            "device_id": "living-room-kodi",
            "occurred_at": "2026-05-07T12:00:00Z",
            "player": {
                "item_type": "episode",
                "media_item_id": "tmdb:tv:1399:s01e01",
                "title": "Game of Thrones",
                "season_number": 1,
                "episode_number": 1,
                "runtime_minutes": 55,
                "position_seconds": 1200,
                "is_paused": False,
            },
            "osd": {"visible": True},
        }
        response = client.post("/api/playback/events", json=event)
        assert response.status_code == 200

        current_response = client.get("/api/playback/current")
        assert current_response.status_code == 200
        current = current_response.get_json()["current"]
        assert current["session"]["safe_through_seconds"] == 1110
        assert current["active_thread_id"].endswith("tmdb:tv:1399:s01e01")

        queue_response = client.post(
            "/api/queue/generate",
            json={
                "profile_id": "default",
                "device_id": "living-room-kodi",
                "time_budget_minutes": 120,
                "prioritize_current_series": True,
            },
        )
        assert queue_response.status_code == 200
        queue = queue_response.get_json()["queue"]
        assert queue["name"] == "Continue Game of Thrones"
        assert len(queue["items"]) == 2
        assert queue["items"][0]["episode"] == 2
        assert queue["items"][0]["tmdb"] == "1399"
    finally:
        app.db.close()
        os.unlink(db_path)


def test_usher_settings_round_trip_and_chat_route():
    app, client, db_path = _make_client()
    try:
        put_response = client.put(
            "/api/usher/settings",
            json={
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "base_url": "http://127.0.0.1:9000",
                "timeout_seconds": 15,
            },
        )
        assert put_response.status_code == 200

        get_response = client.get("/api/usher/settings")
        assert get_response.status_code == 200
        settings = get_response.get_json()
        assert settings["provider"] == "openai"
        assert settings["base_url"] == "http://127.0.0.1:9000"

        chat_response = client.post("/api/chat/messages", json={"message": "queue tonight"})
        assert chat_response.status_code == 200
        answer = chat_response.get_json()["answer"]["text"]
        assert answer
    finally:
        app.db.close()
        os.unlink(db_path)