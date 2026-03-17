UPDATE player_market_features pmf
SET team = p.team
FROM players p
WHERE pmf.player_id::text = p.external_id
AND pmf.team IS NULL;
