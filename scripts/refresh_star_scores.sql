-- Recompute player prominence from the last completed season.
--
-- Run after ingestion. Fantasy scoring is used because it is the closest proxy
-- in this data for how heavily a player is bet, and it needs no extra source.
-- Ordering only; nothing in the modelling path reads this.
WITH latest AS (
  SELECT max(EXTRACT(YEAR FROM (CASE WHEN EXTRACT(MONTH FROM game_date) >= 3
         THEN game_date ELSE game_date - INTERVAL '1 year' END))::int) AS s
  FROM player_game_stats_app
),
fp AS (
  SELECT g.player_id,
         SUM(COALESCE(g.passing_yards, 0) * 0.04
           + COALESCE(g.rushing_yards, 0) * 0.1
           + COALESCE(g.receiving_yards, 0) * 0.1
           + COALESCE(g.receptions, 0) * 0.5) AS pts
  FROM player_game_stats_app g, latest
  WHERE EXTRACT(YEAR FROM (CASE WHEN EXTRACT(MONTH FROM g.game_date) >= 3
        THEN g.game_date ELSE g.game_date - INTERVAL '1 year' END))::int = latest.s
  GROUP BY g.player_id
)
UPDATE players p SET star_score = fp.pts FROM fp WHERE fp.player_id = p.external_id;
