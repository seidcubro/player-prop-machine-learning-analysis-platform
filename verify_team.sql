SELECT COUNT(*) FILTER (WHERE team IS NULL) AS null_teams,
       COUNT(*) AS total_rows
FROM player_market_features;
