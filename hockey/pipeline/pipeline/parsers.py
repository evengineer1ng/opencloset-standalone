"""Parse NHL API JSON responses into schema-compatible dicts."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_player(data: dict[str, Any]) -> dict[str, Any]:
    """Parse a player record from NHL API response."""
    position = data.get("position")
    if isinstance(position, dict):
        position_value = position.get("abbreviation", "???")
    else:
        position_value = position or "???"

    nationality = data.get("nationality")
    if isinstance(nationality, dict):
        nationality_value = nationality.get("name")
    else:
        nationality_value = nationality or data.get("birthCountry")

    return {
        "nhl_id": data["id"],
        "name": data.get("fullName", data.get("name", "Unknown")),
        "position": position_value,
        "birthdate": data.get("birthDate") or data.get("birthdate"),
        "height_cm": _parse_height(data.get("height")),
        "weight_kg": _parse_weight(data.get("weight")),
        "nationality": nationality_value,
        "draft_year": data.get("draftYear"),
        "draft_round": data.get("draftRound"),
        "draft_overall": data.get("draftOverall"),
        "is_active": data.get("isCurrentNHLPlayer", True),
    }


def parse_team(data: dict[str, Any]) -> dict[str, Any]:
    """Parse a team record from NHL API response."""
    name = data.get("name", "")
    if isinstance(name, dict):
        name = name.get("default") or name.get("fr") or ""

    return {
        "nhl_id": data["id"],
        "name": name,
        "abbreviation": data.get("abbreviation") or data.get("triCode", ""),
        "city": data.get("cityName") or data.get("placeName"),
    }


def parse_season(data: dict[str, Any]) -> dict[str, Any]:
    """Parse a season record from NHL API response."""
    return {
        "label": data.get("season", data.get("label", "")),
        "start_date": data.get("startDate"),
        "end_date": data.get("endDate"),
    }


def parse_game(data: dict[str, Any]) -> dict[str, Any]:
    """Parse a game record from NHL API response."""
    pk_sk = data.get("gamePk") or data.get("id")

    teams = data.get("teams") or {}
    home_team = data.get("homeTeam") or teams.get("home", {})
    away_team = data.get("awayTeam") or teams.get("away", {})
    linescore = data.get("linescore") or {}
    home_score = data.get("homeScore")
    away_score = data.get("awayScore")
    if home_score is None:
        home_score = linescore.get("totalHome")
    if away_score is None:
        away_score = linescore.get("totalAway")

    return {
        "nhl_game_id": pk_sk,
        "season_year": _normalize_season_year(str(data.get("season") or data.get("seasonYear") or "")),
        "game_date": data.get("gameDate", "").split("T")[0] if data.get("gameDate") else None,
        "home_team_id": home_team.get("id"),
        "away_team_id": away_team.get("id"),
        "home_score": home_score,
        "away_score": away_score,
        "status": data.get("status", {}).get("abstractState", "FINAL"),
    }


def parse_game_event(data: dict[str, Any], game_id: str) -> dict[str, Any]:
    """Parse a game event into the loader's event shape."""
    event = data
    period = event.get("period")
    if isinstance(period, dict):
        period_number = period.get("number", 0)
    else:
        period_number = period or 0

    details = {
        "description": event.get("description", ""),
        "team_nhl_id": (event.get("team") or {}).get("id"),
        "coordinates_x": (event.get("coordinates") or {}).get("x"),
        "coordinates_y": (event.get("coordinates") or {}).get("y"),
        "players": event.get("players", []),
    }

    players = event.get("players", [])
    primary_player = next((player.get("id") for player in players if player.get("id") is not None), None)

    return {
        "game_id": game_id,
        "player_nhl_id": primary_player,
        "period": period_number,
        "time_on_ice": event.get("periodTime", "00:00"),
        "event_type": event.get("type", "UNKNOWN"),
        "strength": event.get("strength") or event.get("situationCode"),
        "details": details,
    }


def parse_players(data: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = data.get("data") if isinstance(data, dict) else data
    if not isinstance(payload, list):
        payload = []
    return [parse_player(item) for item in payload if isinstance(item, dict) and item.get("id") is not None]


def parse_teams(data: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: Any = data
    if isinstance(data, dict):
        payload = data.get("data") or data.get("teams") or data.get("teamStats") or []
    if not isinstance(payload, list):
        payload = []
    return [parse_team(item) for item in payload if isinstance(item, dict) and item.get("id") is not None]


def parse_schedule(data: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    if isinstance(data, list):
        games = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        if isinstance(data.get("games"), list):
            games = [item for item in data["games"] if isinstance(item, dict)]
        elif isinstance(data.get("gameWeek"), list):
            for week in data["gameWeek"]:
                if not isinstance(week, dict):
                    continue
                weekly_games = week.get("games") or []
                games.extend(item for item in weekly_games if isinstance(item, dict))
    return [parse_game(item) for item in games]


def parse_game_info(data: dict[str, Any]) -> dict[str, Any]:
    return parse_game(data)


def parse_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events = data.get("plays") or data.get("events") or []
    if not isinstance(raw_events, list):
        raw_events = []
    game_id = str(data.get("gamePk") or data.get("id") or "")
    return [parse_game_event(item, game_id) for item in raw_events if isinstance(item, dict)]


def parse_player_stats(data: dict[str, Any]) -> dict[str, Any]:
    """Parse player stats from NHL API response."""
    return {
        "player_nhl_id": data.get("playerId"),
        "season_id": data.get("season"),
        "team_nhl_id": data.get("team", {}).get("id"),
        "games_played": data.get("gamesPlayed", 0),
        "goals": data.get("goals", 0),
        "assists": data.get("assists", 0),
        "points": data.get("points", 0),
        "plus_minus": data.get("plusMinus"),
        "penalty_minutes": data.get("penaltyMinutes", 0),
        "power_play_goals": data.get("powerPlayGoals", 0),
        "power_play_points": data.get("powerPlayPoints", 0),
        "short_handed_goals": data.get("shortHandedGoals", 0),
        "game_winning_goals": data.get("gameWinningGoals", 0),
        "over_time_goals": data.get("overTimeGoals", 0),
        "shots": data.get("shots", 0),
        "shooting_percentage": data.get("shootingPctg"),
        "time_on_ice_per_game": data.get("timeOnIcePerGame"),
        "face_off_win_percentage": data.get("faceOffPct"),
        "blocked_shots": data.get("blockedShots", 0),
        "takeaways": data.get("takeaways", 0),
        "giveaways": data.get("giveaways", 0),
    }


def parse_goalie_stats(data: dict[str, Any]) -> dict[str, Any]:
    """Parse goalie stats from NHL API response."""
    return {
        "player_nhl_id": data.get("playerId"),
        "season_id": data.get("season"),
        "team_nhl_id": data.get("team", {}).get("id"),
        "games_played": data.get("gamesPlayed", 0),
        "wins": data.get("wins", 0),
        "losses": data.get("losses", 0),
        "overtime_losses": data.get("overtimeLosses", 0),
        "shutouts": data.get("shutouts", 0),
        "saves": data.get("saves", 0),
        "goals_against": data.get("goalsAgainst", 0),
        "save_percentage": data.get("savePctg"),
        "goals_against_average": data.get("goalsAgainstAvg"),
        "time_on_ice": data.get("timeOnIce"),
    }


# --- Utility functions ---

def _parse_height(height_str: str | None) -> int | None:
    """Parse NHL height format (e.g., '5-11') to cm."""
    if not height_str:
        return None
    try:
        normalized = str(height_str).replace("'", "-").replace('"', "")
        parts = normalized.split("-")
        feet = int(parts[0])
        inches = int(parts[1]) if len(parts) > 1 else 0
        return int((feet * 12 + inches) * 2.54)
    except (ValueError, IndexError):
        return None


def _parse_weight(weight_str: str | int | float | None) -> int | None:
    """Parse NHL weight format (lbs) to kg."""
    if not weight_str:
        return None
    try:
        return int(float(weight_str) * 0.453592)
    except ValueError:
        return None


def _normalize_season_year(raw_value: str) -> str:
    value = (raw_value or "").strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[6:8]}"
    return value
