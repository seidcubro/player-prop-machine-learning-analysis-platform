-- Anytime touchdown, as a real column.
--
-- Books do not split touchdowns by type for ordinary players, which is why
-- `player_rush_tds` and `player_reception_tds` returned zero rows across three
-- full seasons of odds history. What they post is "anytime touchdown scorer",
-- one of the most heavily bet props in football, and the platform had no
-- coverage of it at all.
--
-- The feature builder resolves `prop_markets.stat_field` to a column on
-- player_game_stats_app, so this has to be a column rather than an expression.
-- Generated (stored) means it is always consistent with its two inputs and
-- costs nothing to keep in sync.
ALTER TABLE player_game_stats_app
  ADD COLUMN IF NOT EXISTS any_tds DOUBLE PRECISION
  GENERATED ALWAYS AS (COALESCE(rushing_tds, 0) + COALESCE(receiving_tds, 0)) STORED;
