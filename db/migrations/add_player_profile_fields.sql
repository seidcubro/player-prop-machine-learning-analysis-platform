-- Player profile metadata for the public UI: headshots, physicals, and status.
-- `players` is the app-facing dimension the API serves; `nfl_players` is the
-- raw nflverse mirror that feeds it.
ALTER TABLE nfl_players
  ADD COLUMN IF NOT EXISTS headshot      TEXT,
  ADD COLUMN IF NOT EXISTS jersey_number TEXT,
  ADD COLUMN IF NOT EXISTS height        DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS weight        DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS college       TEXT,
  ADD COLUMN IF NOT EXISTS years_exp     INTEGER,
  ADD COLUMN IF NOT EXISTS status        TEXT,
  ADD COLUMN IF NOT EXISTS rookie_year   INTEGER;

ALTER TABLE players
  ADD COLUMN IF NOT EXISTS headshot      TEXT,
  ADD COLUMN IF NOT EXISTS jersey_number TEXT,
  ADD COLUMN IF NOT EXISTS height        DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS weight        DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS college       TEXT,
  ADD COLUMN IF NOT EXISTS years_exp     INTEGER,
  ADD COLUMN IF NOT EXISTS status        TEXT,
  ADD COLUMN IF NOT EXISTS rookie_year   INTEGER;

-- Player search is name-driven and currently scans the whole 25k-row table.
CREATE INDEX IF NOT EXISTS idx_players_name_lower ON players (lower(name));
