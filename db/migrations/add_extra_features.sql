ALTER TABLE player_market_features
ADD COLUMN IF NOT EXISTS extra_features JSONB;
