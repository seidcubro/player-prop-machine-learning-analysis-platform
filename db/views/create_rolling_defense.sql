CREATE MATERIALIZED VIEW team_defense_rec_rolling AS
WITH team_game_totals AS (
  SELECT
    opponent AS defense_team,
    game_date,
    SUM(receiving_yards) AS total_rec_yds_allowed,
    SUM(targets) AS total_targets_allowed
  FROM player_game_stats_app
  WHERE receiving_yards IS NOT NULL
  GROUP BY opponent, game_date
),
rolling AS (
  SELECT
    defense_team,
    game_date,
    AVG(total_rec_yds_allowed) OVER (
      PARTITION BY defense_team
      ORDER BY game_date
      ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
    ) AS opp_rec_yds_allowed_rolling,
    AVG(total_targets_allowed) OVER (
      PARTITION BY defense_team
      ORDER BY game_date
      ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
    ) AS opp_targets_allowed_rolling
  FROM team_game_totals
)
SELECT * FROM rolling;
