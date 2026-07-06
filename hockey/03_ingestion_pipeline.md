# Phase 0.3: Data Ingestion Pipeline Design

## Overview

Python-based pipeline to fetch NHL API data, parse it, and load into PostgreSQL following the schema from Phase 0.2.

## Architecture

```
NHL API âââº Fetcher âââº Parser âââº Validator âââº Loader âââº PostgreSQL
```

### Components

1. **Fetcher**: HTTP client that calls NHL API endpoints with rate limiting and retry logic.
2. **Parser**: Transforms API JSON responses into Python dicts matching our schema.
3. **Validator**: Checks data quality (required fields, value ranges, referential integrity).
4. **Loader**: Inserts/updates records into PostgreSQL using `psycopg` or `SQLAlchemy`.

## Tech Stack

- **Language**: Python 3.12+
- **HTTP Client**: `httpx` (async support, good timeout handling)
- **DB Driver**: `psycopg` (v3, modern PostgreSQL driver)
- **Orchestration**: `click` for CLI commands (simple, no cron needed initially)
- **Config**: `pydantic-settings` for typed config from `.env` file
- **Logging**: Standard `logging` module with structured format

## Pipeline Commands

```bash
# Bootstrap: load all seasons, players, teams
python pipeline.py bootstrap --seasons 2020-21 2021-22 2022-23 2023-24 2024-25

# Ingest a single season
python pipeline.py ingest-season --season 2024-25

# Ingest a single game
python pipeline.py ingest-game --game-id 2024020001

# Ingest player stats for a season
python pipeline.py ingest-stats --player-id 8478483 --season 2024-25

# Re-process cached raw data (for grade recalculations)
python pipeline.py reprocess --season 2024-25
```

## Data Flow Details

### 1. Seasons
- Source: Hardcoded list (NHL API doesn't have a seasons endpoint)
- Action: Insert into `seasons` table

### 2. Teams
- Source: `https://statsapi.web.nhl.com/api/v1/teams`
- Action: Insert/update into `teams` table

### 3. Players
- Source: `https://statsapi.web.nhl.com/api/v1/people?activeSeason={season}`
- Pagination: API returns 500 per page, loop until exhausted
- Action: Insert/update into `players` table

### 4. Games
- Source: `https://statsapi.web.nhl.com/api/v1/schedule?startDate={start}&endDate={end}`
- Action: Insert into `games` table

### 5. Game Events (Play-by-Play)
- Source: `https://statsapi.web.nhl.com/api/v1/game/{gameId}/feed/live`
- Fields: eventTypeId, eventName, period, time, players[], coordinates, description
- Action: Batch insert into `game_events` table (use `COPY` for performance)
- Raw JSON: Stored in `raw_data_cache` for reprocessing

### 6. Player Stats
- Source: `https://statsapi.web.nhl.com/api/v1/people/{id}/stats?stats=seasonStat&season={season}`
- Action: Insert into `player_stats` table

### 7. Shift Data (if available)
- Source: `https://statsapi.web.nhl.com/api/v1/game/{gameId}/boxscore`
- Note: Shift data may not be available for all games; handle gracefully

## Rate Limiting & Reliability

- **Rate limit**: 1 request per second (NHL API is generous but polite)
- **Retry**: 3 attempts with exponential backoff (1s, 2s, 4s)
- **Timeout**: 30s per request
- **Caching**: Raw API responses stored in `raw_data_cache` to avoid re-fetching
- **Idempotency**: All inserts use `ON CONFLICT` upserts; pipeline is re-runnable

## Error Handling

- Failed game events: Log warning, continue with next game
- Missing player: Create placeholder record with known fields
- API errors: Retry, then skip with logged error
- Data validation failures: Log and skip the record (don't crash the pipeline)

## Project Structure

```
hockey/
âââ 00_research.md
âââ 01_decisions.md
âââ 02_schema.md
âââ 03_ingestion_pipeline.md
âââ pipeline/
â   âââ __init__.py
â   âââ cli.py              # Click commands
â   âââ config.py           # Pydantic settings
â   âââ fetcher.py          # HTTP client, rate limiting
â   âââ parsers.py          # API response â dict transformers
â   âââ validator.py        # Data quality checks
â   âââ loader.py           # PostgreSQL insert/update logic
â   âââ models.py           # Pydantic models matching schema
âââ migrations/
â   âââ 001_initial.sql     # Schema SQL
âââ .env.example
âââ requirements.txt
```

## Next Steps

After this design is approved:
1. Create the project structure and `requirements.txt`
2. Implement the schema migration SQL
3. Build the Fetcher component with rate limiting
4. Build parsers for each API endpoint
5. Build the Loader with upsert logic
6. Wire up CLI commands
7. Test with a single game, then a full season
