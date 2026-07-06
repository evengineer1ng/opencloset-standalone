-- Hockey Talent ID Engine â Initial Schema
-- PostgreSQL 15+

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. players
CREATE TABLE players (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nhl_id INTEGER UNIQUE,
    name VARCHAR(255) NOT NULL,
    position VARCHAR(10) NOT NULL,
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

-- 2. seasons
CREATE TABLE seasons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label VARCHAR(20) NOT NULL UNIQUE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. teams
CREATE TABLE teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nhl_id INTEGER UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    abbreviation VARCHAR(10) NOT NULL UNIQUE,
    city VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. games
CREATE TABLE games (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nhl_game_id INTEGER UNIQUE,
    season_id UUID REFERENCES seasons(id),
    date DATE NOT NULL,
    home_team_id UUID REFERENCES teams(id),
    away_team_id UUID REFERENCES teams(id),
    home_score INTEGER,
    away_score INTEGER,
    game_status VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. shifts
CREATE TABLE shifts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID REFERENCES games(id),
    player_id UUID REFERENCES players(id),
    team_id UUID REFERENCES teams(id),
    period INTEGER NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME,
    strength VARCHAR(20),
    on_ice BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. game_events
CREATE TABLE game_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID REFERENCES games(id),
    event_type VARCHAR(50) NOT NULL,
    event_code VARCHAR(50),
    period INTEGER NOT NULL,
    event_time TIME NOT NULL,
    x_coordinate DECIMAL(6,2),
    y_coordinate DECIMAL(6,2),
    player1_id UUID REFERENCES players(id),
    player2_id UUID REFERENCES players(id),
    player3_id UUID REFERENCES players(id),
    team_id UUID REFERENCES teams(id),
    raw_event JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. trait_definitions
CREATE TABLE trait_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    category VARCHAR(50) NOT NULL,
    scale_min INTEGER DEFAULT 1,
    scale_max INTEGER DEFAULT 100,
    is_composite BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. trait_grades
CREATE TABLE trait_grades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID REFERENCES players(id),
    trait_id UUID REFERENCES trait_definitions(id),
    season_id UUID REFERENCES seasons(id),
    grade INTEGER NOT NULL CHECK (grade >= 1 AND grade <= 100),
    sample_size INTEGER DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(player_id, trait_id, season_id)
);

-- 9. evaluations
CREATE TABLE evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID REFERENCES players(id),
    season_id UUID REFERENCES seasons(id),
    evaluator VARCHAR(255),
    evaluation_type VARCHAR(50) NOT NULL,
    overall_grade INTEGER CHECK (overall_grade >= 1 AND overall_grade <= 100),
    summary TEXT,
    raw_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 10. raw_data_cache
CREATE TABLE raw_data_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(100) NOT NULL,
    endpoint VARCHAR(255) NOT NULL,
    params JSONB,
    response JSONB NOT NULL,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX idx_games_season ON games(season_id);
CREATE INDEX idx_games_date ON games(date);
CREATE INDEX idx_shifts_game_player ON shifts(game_id, player_id);
CREATE INDEX idx_game_events_game ON game_events(game_id);
CREATE INDEX idx_game_events_type ON game_events(event_type);
CREATE INDEX idx_trait_grades_player ON trait_grades(player_id);
CREATE INDEX idx_trait_grades_season ON trait_grades(season_id);
CREATE INDEX idx_evaluations_player ON evaluations(player_id);
CREATE INDEX idx_raw_data_cache_source ON raw_data_cache(source);
