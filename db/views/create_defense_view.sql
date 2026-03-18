CREATE MATERIALIZED VIEW IF NOT EXISTS team_defense_stats AS
SELECT
  opponent AS team,
  AVG(label_actual) AS avg_allowed
FROM player_market_features
WHERE label_actual IS NOT NULL
GROUP BY opponent;
