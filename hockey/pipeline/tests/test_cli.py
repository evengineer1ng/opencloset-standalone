from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

import pipeline.cli as cli_module
from pipeline import loader as loader_mod
from pipeline.cli import cli
from pipeline.config import settings


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses

    def get(self, endpoint, params=None):
        return self.responses[endpoint]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def test_bootstrap_local_loads_sample_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    (data_dir / "sample_players.json").write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "fullName": "Player One",
                    "position": "C",
                    "height": "6'1\"",
                    "weight": 200,
                    "birthDate": "1999-01-01",
                    "birthCountry": "CAN",
                },
                {
                    "id": 2,
                    "fullName": "Player Two",
                    "position": "D",
                    "height": "5'11\"",
                    "weight": 185,
                    "birthDate": "2000-02-02",
                    "birthCountry": "USA",
                },
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "sample_games.json").write_text(
        json.dumps(
            {
                "games": [
                    {
                        "gamePk": 2023020001,
                        "gameDate": "2023-10-10T23:00:00Z",
                        "season": "20232024",
                        "teams": {
                            "away": {"id": 1, "name": "Away"},
                            "home": {"id": 2, "name": "Home"},
                        },
                        "linescore": {"totalAway": 2, "totalHome": 4},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "hockey_lab.sqlite"
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "local_db_path", str(db_path))
    monkeypatch.setattr(settings, "mode", "local")

    runner = CliRunner()
    result = runner.invoke(cli, ["bootstrap", "--local"])

    assert result.exit_code == 0, result.output
    assert db_path.exists()

    conn = sqlite3.connect(str(db_path))
    try:
        player_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        season_count = conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0]
        game_count = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    finally:
        conn.close()

    assert player_count == 2
    assert season_count == 1
    assert game_count == 1


def test_bootstrap_non_local_loads_api_backed_data_into_current_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "hockey_lab.sqlite"
    responses = {
        "team-stats": {
            "data": [
                {"id": 1, "name": "Boston Bruins", "abbreviation": "BOS", "cityName": "Boston"},
                {"id": 2, "name": "Colorado Avalanche", "abbreviation": "COL", "cityName": "Denver"},
            ]
        },
        "player-stats/2020-21/regular-season/summary": {
            "data": [
                {
                    "id": 8478402,
                    "fullName": "Nathan MacKinnon",
                    "position": {"abbreviation": "C"},
                    "birthDate": "1995-09-01",
                    "height": "6-0",
                    "weight": "200",
                    "nationality": {"name": "CAN"},
                }
            ]
        },
        "player-stats/2021-22/regular-season/summary": {"data": []},
        "player-stats/2022-23/regular-season/summary": {"data": []},
        "player-stats/2023-24/regular-season/summary": {"data": []},
        "player-stats/2024-25/regular-season/summary": {"data": []},
        "schedule/2020-21": {
            "games": [
                {
                    "gamePk": 2023020001,
                    "gameDate": "2023-10-10T23:00:00Z",
                    "season": "20232024",
                    "teams": {
                        "away": {"id": 1, "name": "Boston Bruins"},
                        "home": {"id": 2, "name": "Colorado Avalanche"},
                    },
                    "linescore": {"totalAway": 2, "totalHome": 4},
                }
            ]
        },
        "schedule/2021-22": {"games": []},
        "schedule/2022-23": {"games": []},
        "schedule/2023-24": {"games": []},
        "schedule/2024-25": {"games": []},
    }

    monkeypatch.setattr(settings, "local_db_path", str(db_path))
    monkeypatch.setattr(settings, "mode", "local")
    monkeypatch.setattr(cli_module, "NHLAPIFetcher", lambda: FakeFetcher(responses))

    runner = CliRunner()
    result = runner.invoke(cli, ["bootstrap"])

    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(str(db_path))
    try:
        season_count = conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0]
        team_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        player_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        game_count = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    finally:
        conn.close()

    assert season_count == 5
    assert team_count == 2
    assert player_count == 1
    assert game_count == 1


def test_ingest_game_non_local_loads_events_with_player_resolution(tmp_path, monkeypatch):
    db_path = tmp_path / "hockey_lab.sqlite"
    monkeypatch.setattr(settings, "local_db_path", str(db_path))
    monkeypatch.setattr(settings, "mode", "local")

    with loader_mod.get_connection() as conn:
        loader_mod.init_db_schema(conn)
        loader_mod.upsert_player(
            conn,
            {
                "nhl_id": 8478402,
                "name": "Nathan MacKinnon",
                "position": "C",
                "birthdate": "1995-09-01",
                "height_cm": 183,
                "weight_kg": 91,
                "nationality": "CAN",
                "draft_year": None,
                "draft_round": None,
                "draft_overall": None,
                "is_active": True,
            },
        )

    responses = {
        "game/game-info/2024020001": {
            "id": 2024020001,
            "gameDate": "2024-10-10T23:00:00Z",
            "season": "20242025",
            "homeTeam": {"id": 1, "name": "Boston Bruins"},
            "awayTeam": {"id": 2, "name": "Colorado Avalanche"},
            "homeScore": 4,
            "awayScore": 2,
            "status": {"abstractState": "FINAL"},
        },
        "game/event/2024020001": {
            "id": 2024020001,
            "events": [
                {
                    "type": "GOAL",
                    "period": {"number": 1},
                    "periodTime": "10:22",
                    "team": {"id": 2},
                    "players": [{"id": 8478402}],
                    "description": "Nathan MacKinnon scored",
                    "coordinates": {"x": 12, "y": -5},
                }
            ],
        },
    }
    monkeypatch.setattr(cli_module, "NHLAPIFetcher", lambda: FakeFetcher(responses))

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest-game", "2024020001"])

    assert result.exit_code == 0, result.output

    conn = sqlite3.connect(str(db_path))
    try:
        game_count = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        event_row = conn.execute("SELECT event_type, details FROM events").fetchone()
    finally:
        conn.close()

    assert game_count == 1
    assert event_count == 1
    assert event_row[0] == "GOAL"
    assert "Nathan MacKinnon scored" in event_row[1]