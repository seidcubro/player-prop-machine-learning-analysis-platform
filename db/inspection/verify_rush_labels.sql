SELECT COUNT(*) AS rush_labeled_rows
FROM player_market_features pmf
JOIN prop_markets pm ON pm.id = pmf.market_id
WHERE pm.code = 'rush_yds'
  AND pmf.label_actual IS NOT NULL;
