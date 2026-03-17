CREATE MATERIALIZED VIEW team_defense_rec AS
WITH team_game_totals AS (
  SELECT
    opponent AS defense_team,
    game_date,
    SUM(receiving_yards) AS total_rec_yds_allowed,
    SUM(targets) AS total_targets_allowed
  FROM player_game_stats_app
  WHERE receiving_yards IS NOT NULL
  GROUP BY opponent, game_date
)
SELECT
  defense_team AS team,
  AVG(total_rec_yds_allowed) AS opp_rec_yds_allowed,
  AVG(total_targets_allowed) AS opp_targets_allowed
FROM team_game_totals
GROUP BY defense_team;
