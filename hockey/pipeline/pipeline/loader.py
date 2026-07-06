"""Database loader - supports both PostgreSQL and SQLite (local mode)."""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

@contextmanager
def get_connection():
    """Yield a database connection (PostgreSQL or SQLite based on config)."""
    if settings.mode == "local":
        db_path = Path(settings.local_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        from psycopg import Connection
        conn = Connection.connect(settings.database_url)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _is_sqlite(conn) -> bool:
    return isinstance(conn, sqlite3.Connection)

# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def create_tables(conn) -> None:
    """Create all tables if they don't exist."""
    if _is_sqlite(conn):
        _create_tables_sqlite(conn)
    else:
        _create_tables_postgres(conn)


def init_db_schema(conn) -> None:
    """Backward-compatible schema init entrypoint used by the CLI."""
    create_tables(conn)


def _create_tables_sqlite(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nhl_id INTEGER UNIQUE,
            name TEXT NOT NULL,
            position TEXT,
            birthdate TEXT,
            height_cm REAL,
            weight_kg REAL,
            nationality TEXT,
            draft_year INTEGER,
            draft_round INTEGER,
            draft_overall INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_year TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nhl_id INTEGER UNIQUE NOT NULL,
            name TEXT NOT NULL,
            abbreviation TEXT,
            city TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nhl_game_id TEXT UNIQUE NOT NULL,
            season_year TEXT NOT NULL,
            game_date TEXT NOT NULL,
            home_team_id INTEGER,
            away_team_id INTEGER,
            home_score INTEGER,
            away_score INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            period INTEGER,
            time_on_ice TEXT,
            strength TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id),
            FOREIGN KEY (player_id) REFERENCES players(id)
        );
        CREATE TABLE IF NOT EXISTS trait_grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            season_id INTEGER,
            trait_category TEXT NOT NULL,
            trait_name TEXT NOT NULL,
            grade REAL NOT NULL,
            sample_size INTEGER DEFAULT 0,
            notes TEXT,
            evaluator TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players(id),
            FOREIGN KEY (season_id) REFERENCES seasons(id)
        );
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            season_id INTEGER,
            evaluator TEXT NOT NULL,
            evaluation_type TEXT NOT NULL,
            overall_grade REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players(id),
            FOREIGN KEY (season_id) REFERENCES seasons(id)
        );
    """)
    logger.info("SQLite tables created/verified")


def _create_tables_postgres(conn) -> None:
    from psycopg import sql
    conn.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id SERIAL PRIMARY KEY,
            nhl_id INTEGER UNIQUE,
            name TEXT NOT NULL,
            position TEXT,
            birthdate DATE,
            height_cm REAL,
            weight_kg REAL,
            nationality TEXT,
            draft_year INTEGER,
            draft_round INTEGER,
            draft_overall INTEGER,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS seasons (
            id SERIAL PRIMARY KEY,
            season_year TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS teams (
            id SERIAL PRIMARY KEY,
            nhl_id INTEGER UNIQUE NOT NULL,
            name TEXT NOT NULL,
            abbreviation TEXT,
            city TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS games (
            id SERIAL PRIMARY KEY,
            nhl_game_id TEXT UNIQUE NOT NULL,
            season_year TEXT NOT NULL,
            game_date DATE NOT NULL,
            home_team_id INTEGER,
            away_team_id INTEGER,
            home_score INTEGER,
            away_score INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            game_id INTEGER NOT NULL REFERENCES games(id),
            player_id INTEGER NOT NULL REFERENCES players(id),
            event_type TEXT NOT NULL,
            period INTEGER,
            time_on_ice TEXT,
            strength TEXT,
            details JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS trait_grades (
            id SERIAL PRIMARY KEY,
            player_id INTEGER NOT NULL REFERENCES players(id),
            season_id INTEGER REFERENCES seasons(id),
            trait_category TEXT NOT NULL,
            trait_name TEXT NOT NULL,
            grade REAL NOT NULL,
            sample_size INTEGER DEFAULT 0,
            notes TEXT,
            evaluator TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS evaluations (
            id SERIAL PRIMARY KEY,
            player_id INTEGER NOT NULL REFERENCES players(id),
            season_id INTEGER REFERENCES seasons(id),
            evaluator TEXT NOT NULL,
            evaluation_type TEXT NOT NULL,
            overall_grade REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    logger.info("PostgreSQL tables created/verified")

# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------

def upsert_player(conn, player: dict) -> None:
    """Insert or update a player record."""
    if _is_sqlite(conn):
        conn.execute("""
            INSERT INTO players (nhl_id, name, position, birthdate, height_cm, weight_kg,
                                 nationality, draft_year, draft_round, draft_overall, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nhl_id) DO UPDATE SET
                name=excluded.name, position=excluded.position, birthdate=excluded.birthdate,
                height_cm=excluded.height_cm, weight_kg=excluded.weight_kg,
                nationality=excluded.nationality, updated_at=CURRENT_TIMESTAMP
        """, [
            player.get("nhl_id"), player.get("name"), player.get("position"),
            player.get("birthdate"), player.get("height_cm"), player.get("weight_kg"),
            player.get("nationality"), player.get("draft_year"), player.get("draft_round"),
            player.get("draft_overall"), player.get("is_active", 1),
        ])
    else:
        conn.execute("""
            INSERT INTO players (nhl_id, name, position, birthdate, height_cm, weight_kg,
                                 nationality, draft_year, draft_round, draft_overall, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (nhl_id) DO UPDATE SET
                name = EXCLUDED.name, position = EXCLUDED.position, birthdate = EXCLUDED.birthdate,
                height_cm = EXCLUDED.height_cm, weight_kg = EXCLUDED.weight_kg,
                nationality = EXCLUDED.nationality, updated_at = NOW()
        """, [
            player.get("nhl_id"), player.get("name"), player.get("position"),
            player.get("birthdate"), player.get("height_cm"), player.get("weight_kg"),
            player.get("nationality"), player.get("draft_year"), player.get("draft_round"),
            player.get("draft_overall"), player.get("is_active", True),
        ])


def upsert_team(conn, team: dict) -> None:
    """Insert or update a team record."""
    if _is_sqlite(conn):
        conn.execute(
            """
            INSERT INTO teams (nhl_id, name, abbreviation, city)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(nhl_id) DO UPDATE SET
                name = excluded.name,
                abbreviation = excluded.abbreviation,
                city = excluded.city,
                updated_at = CURRENT_TIMESTAMP
            """,
            [team.get("nhl_id"), team.get("name"), team.get("abbreviation"), team.get("city")],
        )
    else:
        conn.execute(
            """
            INSERT INTO teams (nhl_id, name, abbreviation, city)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (nhl_id) DO UPDATE SET
                name = EXCLUDED.name,
                abbreviation = EXCLUDED.abbreviation,
                city = EXCLUDED.city,
                updated_at = CURRENT_TIMESTAMP
            """,
            [team.get("nhl_id"), team.get("name"), team.get("abbreviation"), team.get("city")],
        )


def upsert_season(conn, season_year: str) -> int:
    """Insert a season if not exists. Returns season id."""
    if _is_sqlite(conn):
        cur = conn.execute("SELECT id FROM seasons WHERE season_year = ?", (season_year,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO seasons (season_year) VALUES (?)", (season_year,))
        return cur.lastrowid
    else:
        from psycopg import sql
        result = conn.execute("""
            INSERT INTO seasons (season_year) VALUES (%s)
            ON CONFLICT (season_year) DO UPDATE SET season_year = EXCLUDED.season_year
            RETURNING id
        """, (season_year,))
        return result.fetchone()[0]


def upsert_game(conn, game: dict) -> int:
    """Insert or update a game. Returns game id."""
    nhl_game_id = game["nhl_game_id"]
    if _is_sqlite(conn):
        cur = conn.execute("SELECT id FROM games WHERE nhl_game_id = ?", (nhl_game_id,))
        row = cur.fetchone()
        if row:
            conn.execute("""
                UPDATE games SET game_date=?, home_team_id=?, away_team_id=?,
                    home_score=?, away_score=? WHERE nhl_game_id=?
            """, [game.get("game_date"), game.get("home_team_id"), game.get("away_team_id"),
                  game.get("home_score"), game.get("away_score"), nhl_game_id])
            return row["id"]
        cur = conn.execute("""
            INSERT INTO games (nhl_game_id, season_year, game_date, home_team_id, away_team_id,
                               home_score, away_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [nhl_game_id, game.get("season_year"), game.get("game_date"),
              game.get("home_team_id"), game.get("away_team_id"),
              game.get("home_score"), game.get("away_score")])
        return cur.lastrowid
    else:
        result = conn.execute("""
            INSERT INTO games (nhl_game_id, season_year, game_date, home_team_id, away_team_id,
                               home_score, away_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (nhl_game_id) DO UPDATE SET
                game_date = EXCLUDED.game_date, home_score = EXCLUDED.home_score,
                away_score = EXCLUDED.away_score, updated_at = NOW()
            RETURNING id
        """, [nhl_game_id, game.get("season_year"), game.get("game_date"),
              game.get("home_team_id"), game.get("away_team_id"),
              game.get("home_score"), game.get("away_score")])
        return result.fetchone()[0]


def insert_event(conn, event: dict) -> None:
    """Insert a single event."""
    if _is_sqlite(conn):
        import json
        details_json = json.dumps(event.get("details", {}))
        conn.execute("""
            INSERT INTO events (game_id, player_id, event_type, period, time_on_ice, strength, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [event["game_id"], event["player_id"], event["event_type"],
              event.get("period"), event.get("time_on_ice"), event.get("strength"),
              details_json])
    else:
        conn.execute("""
            INSERT INTO events (game_id, player_id, event_type, period, time_on_ice, strength, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, [event["game_id"], event["player_id"], event["event_type"],
              event.get("period"), event.get("time_on_ice"), event.get("strength"),
              event.get("details", {})])


def upsert_game_event(conn, event: dict) -> bool:
    """Compatibility wrapper for game-event ingestion used by the CLI."""
    game_pk = _resolve_game_pk(conn, event.get("game_id"))
    player_pk = _resolve_player_pk(conn, event.get("player_id"), event.get("player_nhl_id"))
    if game_pk is None or player_pk is None:
        logger.warning(
            "Skipping event because game/player could not be resolved: game=%r player=%r player_nhl=%r",
            event.get("game_id"),
            event.get("player_id"),
            event.get("player_nhl_id"),
        )
        return False

    insert_event(
        conn,
        {
            "game_id": game_pk,
            "player_id": player_pk,
            "event_type": event.get("event_type", "UNKNOWN"),
            "period": event.get("period"),
            "time_on_ice": event.get("time_on_ice"),
            "strength": event.get("strength"),
            "details": event.get("details", {}),
        },
    )
    return True


def upsert_trait_grade(conn, grade: dict) -> None:
    """Insert or update a trait grade."""
    if _is_sqlite(conn):
        conn.execute("""
            INSERT INTO trait_grades (player_id, season_id, trait_category, trait_name,
                                      grade, sample_size, notes, evaluator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
        """, [grade["player_id"], grade.get("season_id"), grade["trait_category"],
              grade["trait_name"], grade["grade"], grade.get("sample_size", 0),
              grade.get("notes"), grade.get("evaluator", "system")])
    else:
        conn.execute("""
            INSERT INTO trait_grades (player_id, season_id, trait_category, trait_name,
                                      grade, sample_size, notes, evaluator)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, [grade["player_id"], grade.get("season_id"), grade["trait_category"],
              grade["trait_name"], grade["grade"], grade.get("sample_size", 0),
              grade.get("notes"), grade.get("evaluator", "system")])


def _resolve_game_pk(conn, game_ref: object) -> int | None:
    if game_ref in (None, ""):
        return None
    if _is_sqlite(conn):
        row = conn.execute(
            "SELECT id FROM games WHERE id = ? OR nhl_game_id = ?",
            (game_ref, str(game_ref)),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM games WHERE id = %s OR nhl_game_id = %s",
            [game_ref, str(game_ref)],
        ).fetchone()
    if not row:
        return None
    return row["id"] if hasattr(row, "keys") else row[0]


def _resolve_player_pk(conn, player_ref: object, player_nhl_id: object) -> int | None:
    candidates = [candidate for candidate in (player_ref, player_nhl_id) if candidate not in (None, "")]
    if not candidates:
        return None

    if _is_sqlite(conn):
        row = conn.execute(
            "SELECT id FROM players WHERE id = ? OR nhl_id = ? LIMIT 1",
            (candidates[0], candidates[0]),
        ).fetchone()
        if row is None and len(candidates) > 1:
            row = conn.execute(
                "SELECT id FROM players WHERE id = ? OR nhl_id = ? LIMIT 1",
                (candidates[1], candidates[1]),
            ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM players WHERE id = %s OR nhl_id = %s LIMIT 1",
            [candidates[0], candidates[0]],
        ).fetchone()
        if row is None and len(candidates) > 1:
            row = conn.execute(
                "SELECT id FROM players WHERE id = %s OR nhl_id = %s LIMIT 1",
                [candidates[1], candidates[1]],
            ).fetchone()

    if not row:
        return None
    return row["id"] if hasattr(row, "keys") else row[0]
