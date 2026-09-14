-- App players table
CREATE TABLE IF NOT EXISTS players (
  id SERIAL PRIMARY KEY,
  external_id TEXT UNIQUE,
  first_name TEXT,
  last_name TEXT,
  name TEXT,
  position TEXT,
  team TEXT
);

-- Prop markets registry
CREATE TABLE IF NOT EXISTS prop_markets (
  id SERIAL PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  stat_field TEXT NOT NULL
);

INSERT INTO prop_markets (code, name, stat_field) VALUES
  ('rec_yds',  'Receiving Yards', 'receiving_yards'),
  ('rush_yds', 'Rushing Yards',   'rushing_yards'),
  ('pass_yds', 'Passing Yards',   'passing_yards'),
  ('recs',     'Receptions',      'receptions')
ON CONFLICT (code) DO NOTHING;

-- Staging tables (also created by ingestion job, but safe to define here)
CREATE TABLE IF NOT EXISTS nfl_players (
  player_id TEXT PRIMARY KEY,
  full_name TEXT,
  position TEXT,
  team TEXT
);

CREATE TABLE IF NOT EXISTS nfl_games (
  game_id TEXT PRIMARY KEY,
  season INT,
  week INT,
  game_type TEXT,
  game_date DATE,
  home_team TEXT,
  away_team TEXT
);

CREATE TABLE IF NOT EXISTS player_game_stats (
  player_id TEXT,
  game_id TEXT,
  season INT,
  week INT,
  game_date DATE,
  opponent TEXT,
  team TEXT,
  position TEXT,
  passing_yards FLOAT,
  passing_tds FLOAT,
  rushing_yards FLOAT,
  rush_attempts FLOAT,
  receiving_yards FLOAT,
  receptions FLOAT,
  touchdowns FLOAT,
  PRIMARY KEY (player_id, game_id)
);

-- App-layer player game stats
CREATE TABLE IF NOT EXISTS player_game_stats_app (
  id SERIAL PRIMARY KEY,
  player_id INTEGER REFERENCES players(id),
  game_date DATE,
  opponent TEXT,
  receiving_yards FLOAT,
  receptions FLOAT,
  rushing_yards FLOAT,
  rush_attempts FLOAT,
  passing_yards FLOAT,
  passing_tds FLOAT,
  touchdowns FLOAT
);

-- Feature store
CREATE TABLE IF NOT EXISTS player_market_features (
  id SERIAL PRIMARY KEY,
  player_id INTEGER REFERENCES players(id),
  market_id INTEGER REFERENCES prop_markets(id),
  as_of_game_date DATE,
  opponent TEXT,
  lookback INTEGER,
  mean DOUBLE PRECISION,
  stddev DOUBLE PRECISION,
  weighted_mean DOUBLE PRECISION,
  trend DOUBLE PRECISION,
  recs_mean DOUBLE PRECISION,
  recs_trend DOUBLE PRECISION,
  label_actual DOUBLE PRECISION,
  UNIQUE (player_id, market_id, as_of_game_date, opponent, lookback)
);

-- Baseline projections


-- Three tables were removed here: projections, ml_projections and prop_lines.
--
-- Scaffolding from the first version of the schema, superseded by
-- player_projections and odds_player_props and referenced by nothing since:
-- no query, no script, no page. Two held zero rows and the third held 32 stale
-- ones. They were left out rather than dropped, so an existing database keeps
-- whatever is in them and a fresh one simply never creates them.

-- Model registry
CREATE TABLE IF NOT EXISTS trained_models (
  id SERIAL PRIMARY KEY,
  model_name TEXT,
  market_id INTEGER REFERENCES prop_markets(id),
  lookback INTEGER,
  artifact_path TEXT,
  train_rows INTEGER,
  test_rows INTEGER,
  mae DOUBLE PRECISION,
  rmse DOUBLE PRECISION,
  r2 DOUBLE PRECISION,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (model_name, market_id, lookback)
);

CREATE TABLE IF NOT EXISTS active_models (
  id SERIAL PRIMARY KEY,
  market_id INTEGER REFERENCES prop_markets(id) UNIQUE,
  lookback INTEGER,
  model_name TEXT,
  artifact_path TEXT,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

