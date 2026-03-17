SELECT pm.code, COUNT(*) AS rows_built, COUNT(*) FILTER (WHERE pmf.label_actual IS NOT NULL) AS rows_labeled
FROM player_market_features pmf
JOIN prop_markets pm ON pm.id = pmf.market_id
WHERE pm.code IN ('rec_yds','pass_yds','tackles_combined','fg_made')
GROUP BY pm.code
ORDER BY pm.code;
