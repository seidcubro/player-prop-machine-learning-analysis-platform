SELECT extra_features
FROM player_market_features
WHERE extra_features::text LIKE '%opp_allowed_mean%'
LIMIT 5;
