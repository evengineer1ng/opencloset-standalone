"""CLI entry point for the hockey data pipeline."""

import json
import logging
from pathlib import Path

import click

from .config import settings
from .fetcher import NHLAPIFetcher
from . import parsers
from . import loader as loader_mod

logger = logging.getLogger(__name__)


@click.group()
@click.option("--log-level", default="INFO", help="Logging level.")
@click.option("--local", is_flag=True, default=False, help="Use SQLite local DB instead of PostgreSQL.")
def cli(log_level: str, local: bool):
    """Hockey Talent ID Engine - Data Pipeline."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if local:
        settings.mode = "local"
        click.echo("Running in LOCAL mode (SQLite)")


@cli.command()
@click.option("--local", is_flag=True, default=False, help="Use sample data files instead of API.")
def bootstrap(local: bool):
    """Bootstrap: load seasons, teams, then all players and games for 5 years."""
    if local:
        settings.mode = "local"
        click.echo("Bootstrapping with local sample data...")
        _bootstrap_local()
        return

    with loader_mod.get_connection() as conn:
        loader_mod.init_db_schema(conn)

    seasons = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    _load_seasons(seasons)
    _load_teams()
    for season in seasons:
        click.echo(f"\n{'=' * 60}")
        click.echo(f"Season: {season}")
        click.echo("=" * 60)
        _load_season(season)


def _bootstrap_local():
    """Bootstrap using sample data files (no API needed)."""
    data_dir = Path(settings.data_dir)

    # Initialize schema first
    with loader_mod.get_connection() as conn:
        loader_mod.init_db_schema(conn)
    click.echo("Schema initialized.")

    seasons = _local_sample_seasons(data_dir)
    _load_seasons(seasons)

    # Load sample players
    players_file = data_dir / "sample_players.json"
    if players_file.exists():
        with open(players_file) as f:
            raw_players = json.load(f)
        players = [_normalize_local_player(player) for player in raw_players]
        click.echo(f"Loading {len(players)} sample players...")
        with loader_mod.get_connection() as conn:
            for p in players:
                loader_mod.upsert_player(conn, p)
        click.echo(f"Loaded {len(players)} players.")

    # Load sample games
    games_file = data_dir / "sample_games.json"
    if games_file.exists():
        with open(games_file) as f:
            raw_games = json.load(f)
        games_payload = raw_games.get("games", raw_games) if isinstance(raw_games, dict) else raw_games
        games = [_normalize_local_game(game) for game in games_payload]
        click.echo(f"Loading {len(games)} sample games...")
        with loader_mod.get_connection() as conn:
            for g in games:
                loader_mod.upsert_game(conn, g)
        click.echo(f"Loaded {len(games)} games.")

    click.echo("\nBootstrap complete (local mode).")


@cli.command()
@click.argument("season")
@click.option("--local", is_flag=True, default=False, help="Use sample data files instead of API.")
def ingest_season(season: str, local: bool):
    """Ingest a single season (players, games, events, stats)."""
    if local:
        settings.mode = "local"
        click.echo(f"Ingesting season {season} with local data...")
        _bootstrap_local()
        return
    with loader_mod.get_connection() as conn:
        loader_mod.init_db_schema(conn)
    _ensure_season(season)
    _load_season(season)


@cli.command()
@click.argument("game-id", type=int)
@click.option("--local", is_flag=True, default=False, help="Use sample data files instead of API.")
def ingest_game(game_id: int, local: bool):
    """Ingest a single game by NHL game ID."""
    if local:
        settings.mode = "local"
        click.echo("Local mode: game ingestion uses sample data.")
        return
    with loader_mod.get_connection() as conn:
        loader_mod.init_db_schema(conn)
    with NHLAPIFetcher() as fetcher:
        game_data = fetcher.get(f"game/game-info/{game_id}")
        game = parsers.parse_game_info(game_data)
        events_data = fetcher.get(f"game/event/{game_id}")
        events = parsers.parse_events(events_data)

        with loader_mod.get_connection() as conn:
            game_pk = loader_mod.upsert_game(conn, game)
            for ev in events:
                ev.setdefault("game_id", game_pk)
                loader_mod.upsert_game_event(conn, ev)

        click.echo(f"Game {game_id} ingested: {len(events)} events.")


@cli.command()
@click.argument("season")
def evaluate(season: str):
    """Run trait evaluation for a season."""
    season_id = _get_season_id(season)
    click.echo(f"Evaluating season {season} (id={season_id})...")
    # TODO: implement evaluation logic
    click.echo("Evaluation placeholder â not yet implemented.")


# ---- Internal helpers ----


def _load_seasons(seasons: list[str]):
    """Create season records."""
    with loader_mod.get_connection() as conn:
        for label in seasons:
            loader_mod.upsert_season(conn, label)
    click.echo(f"Loaded {len(seasons)} seasons.")


def _load_teams():
    """Load teams from NHL API."""
    with NHLAPIFetcher() as fetcher:
        teams_data = fetcher.get("team-stats")
    teams = parsers.parse_teams(teams_data)
    with loader_mod.get_connection() as conn:
        for t in teams:
            loader_mod.upsert_team(conn, t)
    click.echo(f"Loaded {len(teams)} teams.")


def _load_season(season: str):
    """Load all players and games for a season."""
    # Load players
    with NHLAPIFetcher() as fetcher:
        players_data = fetcher.get(f"player-stats/{season}/regular-season/summary")
    players = parsers.parse_players(players_data)
    with loader_mod.get_connection() as conn:
        for p in players:
            loader_mod.upsert_player(conn, p)
    click.echo(f"  Players: {len(players)}")

    # Load games
    with NHLAPIFetcher() as fetcher:
        schedule_data = fetcher.get(f"schedule/{season}")
    games = parsers.parse_schedule(schedule_data)
    with loader_mod.get_connection() as conn:
        for g in games:
            loader_mod.upsert_game(conn, g)
    click.echo(f"  Games: {len(games)}")


def _ensure_season(season: str):
    """Ensure season record exists."""
    with loader_mod.get_connection() as conn:
        loader_mod.upsert_season(conn, season)


def _get_season_id(season: str) -> str:
    """Get UUID for a season label."""
    with loader_mod.get_connection() as conn:
        if settings.mode == "local":
            row = conn.execute(
                "SELECT id FROM seasons WHERE season_year = ?",
                [season],
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM seasons WHERE season_year = %s",
                [season],
            ).fetchone()
    if not row:
        raise click.ClickException(f"Season '{season}' not found. Run bootstrap first.")
    if isinstance(row, dict) or hasattr(row, "keys"):
        return str(row["id"])
    return str(row[0])


def _local_sample_seasons(data_dir: Path) -> list[str]:
    games_file = data_dir / "sample_games.json"
    if not games_file.exists():
        return ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

    with open(games_file) as f:
        raw_games = json.load(f)

    games_payload = raw_games.get("games", raw_games) if isinstance(raw_games, dict) else raw_games
    seasons = sorted({
        _normalize_season_year(str(game.get("season") or game.get("seasonYear") or "")).strip()
        for game in games_payload
        if isinstance(game, dict) and (game.get("season") or game.get("seasonYear"))
    })
    return seasons or ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]


def _normalize_local_player(raw_player: dict) -> dict:
    team = raw_player.get("team") or {}
    return {
        "nhl_id": raw_player.get("id"),
        "name": raw_player.get("fullName") or "Unknown",
        "position": raw_player.get("position"),
        "birthdate": raw_player.get("birthDate"),
        "height_cm": _height_to_cm(raw_player.get("height")),
        "weight_kg": _weight_to_kg(raw_player.get("weight")),
        "nationality": raw_player.get("birthCountry"),
        "draft_year": raw_player.get("draftYear"),
        "draft_round": raw_player.get("draftRound"),
        "draft_overall": raw_player.get("draftOverall"),
        "is_active": not bool(raw_player.get("retired", False)),
        "team_nhl_id": team.get("id"),
    }


def _normalize_local_game(raw_game: dict) -> dict:
    teams = raw_game.get("teams") or {}
    away = teams.get("away") or {}
    home = teams.get("home") or {}
    linescore = raw_game.get("linescore") or {}
    return {
        "nhl_game_id": str(raw_game.get("gamePk") or raw_game.get("id") or ""),
        "season_year": _normalize_season_year(str(raw_game.get("season") or raw_game.get("seasonYear") or "")),
        "game_date": str(raw_game.get("gameDate") or "").split("T", 1)[0] or None,
        "home_team_id": home.get("id"),
        "away_team_id": away.get("id"),
        "home_score": linescore.get("totalHome"),
        "away_score": linescore.get("totalAway"),
    }


def _normalize_season_year(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[6:8]}"
    return value


def _height_to_cm(raw_value: str | None) -> float | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    normalized = value.replace("'", "-").replace('"', "")
    parts = [part for part in normalized.split("-") if part]
    try:
        feet = int(parts[0])
        inches = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    return round((feet * 12 + inches) * 2.54, 2)


def _weight_to_kg(raw_value: int | float | str | None) -> float | None:
    if raw_value in (None, ""):
        return None
    try:
        pounds = float(raw_value)
    except (TypeError, ValueError):
        return None
    return round(pounds * 0.45359237, 2)


if __name__ == "__main__":
    cli()
