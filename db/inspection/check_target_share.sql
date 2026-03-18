SELECT extra_features
FROM player_market_features
WHERE extra_features::text LIKE '%target_share%'
LIMIT 5;
