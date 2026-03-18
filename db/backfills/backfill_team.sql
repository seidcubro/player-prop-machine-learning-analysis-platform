UPDATE player_market_features pmf
SET team = p.team
FROM players p
WHERE pmf.player_id = p.player_id
AND pmf.team IS NULL;
