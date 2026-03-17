SELECT model_name, market_id, lookback, mae, rmse, r2, created_at
FROM trained_models
WHERE market_id IN (93, 98, 101)
ORDER BY market_id, created_at DESC;
