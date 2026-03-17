CREATE MATERIALIZED VIEW IF NOT EXISTS team_defense_rec AS
SELECT
  opponent AS team,
  AVG(receiving_yards) AS opp_rec_yds_allowed,
  AVG(targets) AS opp_targets_allowed
FROM player_game_stats_app
WHERE receiving_yards IS NOT NULL
GROUP BY opponent;
