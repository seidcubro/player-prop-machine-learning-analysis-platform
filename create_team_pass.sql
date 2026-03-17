CREATE MATERIALIZED VIEW team_offense_pass AS
SELECT
  team,
  game_date,
  SUM(targets) AS team_pass_attempts
FROM player_game_stats_app
WHERE targets IS NOT NULL
GROUP BY team, game_date;
