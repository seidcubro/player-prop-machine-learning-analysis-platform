SELECT extra_features
FROM player_market_features
WHERE extra_features::text LIKE '%opp_rec_yds_allowed%'
LIMIT 5;
