# Project Decisions

## 1. Historical Data Horizon
**Decision:** 5 years of historical data as bootstrap window
**Rationale:** Enough baseline for trend analysis without excessive ingest cost

## 2. Data Source
**Decision:** NHL API (primary) + natural language notes (secondary)
**Rationale:** NHL API provides structured event data; notes capture qualitative scouting

## 3. Database
**Decision:** PostgreSQL
**Rationale:** Relational integrity, JSONB support for flexible trait storage, mature ecosystem

## 4. Evaluation Interface
**Decision:** CLI-first with CSV export
**Rationale:** Fast iteration, scriptable, low overhead; CSV for sharing

## 5. Trait Grading System
**Decision:** Granular but comparable â numeric grades (1-10) with categorical labels
**Rationale:** Allows both fine-grained comparison and intuitive communication

## 6. API Connectivity Fallback
**Decision:** File-based fallback mode when NHL API is unreachable
**Rationale:** `api-web.nhle.com` subdomain is unreachable from this network (DNS resolution failure). Pipeline supports `DATA_MODE=file` env var to load from local JSON fixtures in `data/` directory. Enables development, testing, and demo without live API access.
**Fixation:** Use `DATA_MODE=file` for initial development. Switch to `DATA_MODE=api` when API connectivity is available.
