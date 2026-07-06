# Phase 0.2: PostgreSQL Schema Design

## Design Principles
- Normalized core data, denormalized lookup paths for speed
- All grades on 1â100 scale for cross-trait comparability
- JSONB for flexible event data and raw API payloads
- UUID primary keys for all entities
- Timestamps on every table (created_at, updated_at)

## Tables

### 1. players
Core player identity and metadata.

```sql
CREATE TABLE players (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nhl_id INTEGER UNIQUE,              -- NHL.com player ID
    name VARCHAR(255) NOT NULL,
    position VARCHAR(10) NOT NULL,      -- C, LW, RW, D, G
    birthdate DATE NOT NULL,
    height_cm INTEGER,
    weight_kg INTEGER,
    nationality VARCHAR(100),
    draft_year INTEGER,
    draft_round INTEGER,
    draft_overall INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 2. seasons
Define season windows (e.g., 2023-24).

```sql
CREATE TABLE seasons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label VARCHAR(20) NOT NULL UNIQUE,  -- e.g. '2023-24'
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 3. games
Game-level metadata.

```sql
CREATE TABLE games (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nhl_game_id INTEGER UNIQUE,         -- NHL.com game ID
    season_id UUID REFERENCES seasons(id),
    game_date DATE NOT NULL,
    home_team VARCHAR(10) NOT NULL,     -- e.g. 'TOR'
    away_team VARCHAR(10) NOT NULL,
    home_score INTEGER,
    away_score INTEGER,
    is_playoff BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4. player_game_stats
Per-player, per-game box score stats.

```sql
CREATE TABLE player_game_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID REFERENCES players(id),
    game_id UUID REFERENCES games(id),
    team VARCHAR(10) NOT NULL,
    position VARCHAR(10),
    goals INTEGER DEFAULT 0,
    assists INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0,
    plus_minus INTEGER,
    pims INTEGER DEFAULT 0,
    hits INTEGER DEFAULT 0,
    blocked_shots INTEGER DEFAULT 0,
    time_on_ice INTERVAL,               -- e.g. '18:32'
    shots INTEGER DEFAULT 0,
    giveaways INTEGER DEFAULT 0,
    takeaways INTEGER DEFAULT 0,
    -- Goalie-specific (nullable for skaters)
    saves INTEGER,
    save_pct DECIMAL(5,4),
    goals_against INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, game_id)
);
```

### 5. trait_categories
Define the trait hierarchy.

```sql
CREATE TABLE trait_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,  -- e.g. 'Skating', 'Shooting', 'Hockey IQ'
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 6. traits
Individual traits within categories.

```sql
CREATE TABLE traits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id UUID REFERENCES trait_categories(id),
    name VARCHAR(100) NOT NULL,         -- e.g. 'Acceleration', 'Release'
    description TEXT,
    weight DECIMAL(3,2) DEFAULT 1.0,    -- weighting for composite scores
    UNIQUE(category_id, name)
);
```

### 7. trait_grades
Per-player, per-trait, per-season grades.

```sql
CREATE TABLE trait_grades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID REFERENCES players(id),
    trait_id UUID REFERENCES traits(id),
    season_id UUID REFERENCES seasons(id),
    grade SMALLINT NOT NULL CHECK (grade BETWEEN 1 AND 100),
    sample_size INTEGER,                -- games/events evaluated
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, trait_id, season_id)
);
```

### 8. evaluations
Scouting reports / evaluation records.

```sql
CREATE TABLE evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID REFERENCES players(id),
    season_id UUID REFERENCES seasons(id),
    evaluator VARCHAR(255),             -- who wrote it (or 'system')
    evaluation_date DATE NOT NULL,
    summary TEXT,                       -- narrative summary
    raw_data JSONB,                     -- structured evaluation data
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 9. game_events
Play-by-play event data (from NHL API).

```sql
CREATE TABLE game_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID REFERENCES games(id),
    event_type VARCHAR(50) NOT NULL,    -- e.g. 'Goal', 'Hit', 'Faceoff'
    period SMALLINT,
    event_time INTERVAL,
    team VARCHAR(10),
    player_ids UUID[],                  -- players involved
    raw_event JSONB,                    -- raw NHL API event data
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 10. raw_data_cache
Store raw API responses for reprocessing.

```sql
CREATE TABLE raw_data_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(50) NOT NULL,        -- e.g. 'nhl_api', 'eliteprospects'
    endpoint VARCHAR(255),              -- API endpoint or URL
    payload JSONB NOT NULL,             -- raw response
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ              -- cache expiry
);
```

## Indexes

```sql
-- Player lookups
CREATE INDEX idx_players_nhl_id ON players(nhl_id);
CREATE INDEX idx_players_position ON players(position);

-- Game lookups
CREATE INDEX idx_games_season ON games(season_id);
CREATE INDEX idx_games_date ON games(game_date);

-- Stats lookups
CREATE INDEX idx_player_game_stats_player ON player_game_stats(player_id);
CREATE INDEX idx_player_game_stats_game ON player_game_stats(game_id);

-- Grade lookups
CREATE INDEX idx_trait_grades_player ON trait_grades(player_id);
CREATE INDEX idx_trait_grades_trait ON trait_grades(trait_id);
CREATE INDEX idx_trait_grades_season ON trait_grades(season_id);

-- Event lookups
CREATE INDEX idx_game_events_game ON game_events(game_id);
CREATE INDEX idx_game_events_type ON game_events(event_type);
```

## Trait Hierarchy (Initial)

| Category | Traits |
|----------|--------|
| Skating | Acceleration, Top Speed, Agility, Edge Work, Deceleration |
| Shooting | Release, Accuracy, Wrist Shot, Backhand, One-timer |
| Passing | Vision, Accuracy, Creativity, Breakout Passes |
| Hockey IQ | Positioning, Decision Making, Awareness, Puck Reading |
| Physical | Strength, Hit Resistance, Balance, Endurance |
| Defensive | Gap Control, Angles, Puck Retrieval, Shot Blocking |
| Offensive | Puck Handling, Creativity, Net Front, Offensive Positioning |
| Competitiveness | Work Ethic, Consistency, Clutch Performance, Leadership |

## Notes
- Grades are 1â100 scale. A 75 = NHL average regular starter.
- `sample_size` in trait_grades tracks how many games/events informed the grade.
- `game_events.raw_event` stores full NHL API event JSON for later feature extraction.
- `raw_data_cache` allows reprocessing without re-hitting APIs.
- Composite scores (category averages, overall ratings) are computed on-the-fly via SQL aggregations, not stored.
