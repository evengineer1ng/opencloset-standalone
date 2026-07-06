from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opencloset_runtime.booth_feed_baker import (
    build_booth_artifact,
    fetch_document_repo_items,
    fetch_sports_api_items,
    process_spec_queue,
)


def test_build_booth_artifact_supports_radio_os_style_feed_specs():
    spec = {
        "station": "Fixture Feed Digest",
        "feeds": {
            "news_wire": {
                "plugin": "rss",
                "enabled": True,
                "fixture_items": [
                    {
                        "id": "rss-1",
                        "title": "Launch window opens",
                        "summary": "Mission control confirms the window.",
                        "url": "https://example.com/launch",
                        "published_at": "2026-06-16T10:00:00Z",
                        "published_ts": 10,
                    }
                ],
            },
            "space_watch": {
                "plugin": "reddit",
                "enabled": True,
                "fixture_items": [
                    {
                        "id": "reddit-1",
                        "title": "New transfer orbit breakdown",
                        "body": "Detailed thread from the community.",
                        "subreddit": "space",
                        "url": "https://old.reddit.com/r/space/comments/abc123",
                        "published_at": "2026-06-16T10:05:00Z",
                        "published_ts": 15,
                        "score": 420,
                        "comments": 91,
                    }
                ],
            },
        },
    }

    artifact = build_booth_artifact(spec)

    assert artifact["tape_key"] == "Fixture Feed Digest"
    assert artifact["snapshot"]["item_count"] == 2
    assert artifact["snapshot"]["plugins"] == ["reddit", "rss"]
    assert artifact["tape"]["events"][0]["actor"] == "example.com"
    assert artifact["tape"]["events"][0]["action"] == "publish"
    assert artifact["tape"]["events"][1]["actor"] == "r/space"
    assert artifact["tape"]["events"][1]["action"] == "post"
    assert "same baked tape" in artifact["schema_hints"]["notes"][0].lower()
    assert 'TAPES["Fixture Feed Digest"]' in artifact["inline_js"]


def test_process_spec_queue_writes_artifacts_and_inline_js(tmp_path: Path):
    inbox = tmp_path / "inbox"
    out_dir = tmp_path / "artifacts"
    archive_dir = tmp_path / "archive"
    inbox.mkdir()
    spec_path = inbox / "sample.json"
    spec_path.write_text(
        json.dumps(
            {
                "station": "Queue Feed",
                "feeds": {
                    "news": {
                        "plugin": "rss",
                        "enabled": True,
                        "fixture_items": [
                            {
                                "id": "rss-1",
                                "title": "Incident resolved",
                                "summary": "Systems are stable again.",
                                "url": "https://example.com/outage",
                                "published_at": "2026-06-16T11:00:00Z",
                                "published_ts": 20,
                            }
                        ],
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = process_spec_queue(inbox, out_dir=out_dir, archive_dir=archive_dir)

    assert result["processed_count"] == 1
    artifact_path = Path(result["processed"][0]["artifact"])
    inline_path = Path(result["processed"][0]["inline_js"])
    assert artifact_path.exists()
    assert inline_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["tape_key"] == "Queue Feed"
    assert payload["snapshot"]["item_count"] == 1
    assert (archive_dir / "sample.json").exists()


def test_fetch_document_repo_items_reads_repo_files_deterministically(tmp_path: Path):
    repo = tmp_path / "lyrics"
    repo.mkdir()
    (repo / "verse1.txt").write_text("first line\nsecond line", encoding="utf-8")
    (repo / "hook.md").write_text("# Hook\nWe rise and repeat", encoding="utf-8")

    items = fetch_document_repo_items(
        "bars",
        {
            "plugin": "document_repo",
            "enabled": True,
            "repo_path": str(repo),
            "include_globs": ["*.txt", "*.md"],
            "max_files": 5,
            "actor": "the lyric archive",
        },
    )

    assert [item.object for item in items] == ["hook.md", "verse1.txt"]
    assert all(item.actor == "the lyric archive" for item in items)
    assert all(item.plugin == "document_repo" for item in items)


def test_fetch_sports_api_items_maps_results_into_booth_rows():
    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "events": [
                    {
                        "idEvent": "1",
                        "strEvent": "Knicks vs Pacers",
                        "strLeague": "NBA",
                        "strHomeTeam": "Knicks",
                        "strAwayTeam": "Pacers",
                        "intHomeScore": "108",
                        "intAwayScore": "101",
                        "strStatus": "FT",
                        "dateEvent": "2026-06-16",
                        "strSeason": "2026",
                    }
                ]
            }

    items = fetch_sports_api_items(
        "nba",
        {
            "plugin": "sports_api",
            "enabled": True,
            "provider": "thesportsdb",
            "endpoint": "eventspastleague",
            "params": {"id": "4387"},
            "limit": 3,
        },
        http_get=lambda *args, **kwargs: _Response(),
    )

    assert len(items) == 1
    assert items[0].actor == "NBA"
    assert items[0].action == "finish"
    assert "Knicks 108-101 Pacers" == items[0].object
