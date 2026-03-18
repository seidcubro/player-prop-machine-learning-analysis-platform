SELECT extra_features
FROM player_market_features
WHERE extra_features::text LIKE '%team_pass_attempts%'
LIMIT 5;
