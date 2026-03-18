UPDATE player_market_features pmf
SET team = pgs.team
FROM player_game_stats pgs
WHERE pmf.player_id = pgs.player_id
AND pmf.as_of_game_date = pgs.game_date
AND pmf.team IS NULL;
