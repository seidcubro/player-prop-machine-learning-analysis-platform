-- Every board the site has ever shown, kept after the live table is rebuilt.
--
-- build_prop_edges TRUNCATEs prop_edges on every run, and grade_edges grades
-- whatever is in prop_edges at the time. That works only while a game's stats
-- arrive before the next rebuild, and they do not: nflverse publishes a day
-- later. The Monday night board of 2026-09-14 was rebuilt on the Tuesday, hours
-- before the stats for it were ingested, so 24 published picks were graded
-- against nothing and never entered the track record at all.
--
-- The track record is the product's whole claim, so the board has to outlive
-- the table it is served from. This is append-and-update: one row per prop per
-- book per game, refreshed while the game is still upcoming so it holds the
-- last thing published before kickoff.
CREATE TABLE IF NOT EXISTS prop_edges_history (LIKE prop_edges INCLUDING DEFAULTS);

ALTER TABLE prop_edges_history
  ADD COLUMN IF NOT EXISTS archived_at timestamptz NOT NULL DEFAULT NOW();

-- id comes from prop_edges' sequence and means nothing here.
ALTER TABLE prop_edges_history DROP CONSTRAINT IF EXISTS prop_edges_history_pkey;

CREATE UNIQUE INDEX IF NOT EXISTS prop_edges_history_natural_key
  ON prop_edges_history (player_name, market_code, bookmaker_key, commence_time);

CREATE INDEX IF NOT EXISTS idx_prop_edges_history_commence
  ON prop_edges_history (commence_time);
