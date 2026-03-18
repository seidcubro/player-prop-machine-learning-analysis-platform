SELECT
  market_id,
  COUNT(*) AS total_rows,
  COUNT(extra_features) AS non_null_extra,
  COUNT(*) FILTER (WHERE extra_features::text != '{}' ) AS non_empty_extra
FROM player_market_features
GROUP BY market_id
ORDER BY market_id;
