ALTER TABLE player_market_features
DROP CONSTRAINT IF EXISTS player_market_features_player_id_fkey;

ALTER TABLE player_market_features
ALTER COLUMN player_id TYPE TEXT
USING player_id::text;
