-- The graded track record must outlive the edges table.
--
-- prop_edges is rebuilt (TRUNCATE + reload) on every run, so a foreign key from
-- prop_edge_results forced a CASCADE that wiped every historical result, the
-- exact data the player track record and calibration checks are built on.
-- prop_edge_results already stores the full pick (line, projection, side,
-- win_prob, tier) so it stands alone; edge_id stays as a provenance reference.
ALTER TABLE prop_edge_results
  DROP CONSTRAINT IF EXISTS prop_edge_results_edge_id_fkey;

-- Re-grading the same game/market must update in place rather than duplicate.
ALTER TABLE prop_edge_results
  ADD COLUMN IF NOT EXISTS player_name TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS prop_edge_results_natural_key
  ON prop_edge_results (player_id, game_date, market_code, line, recommended_side);

-- The record must be readable after prop_edges is rebuilt, so it carries its
-- own matchup/book context rather than joining back to a row that is gone.
ALTER TABLE prop_edge_results
  ADD COLUMN IF NOT EXISTS home_team       TEXT,
  ADD COLUMN IF NOT EXISTS away_team       TEXT,
  ADD COLUMN IF NOT EXISTS bookmaker_title TEXT,
  ADD COLUMN IF NOT EXISTS price_american  INTEGER;
