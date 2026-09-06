-- Player prop odds had no natural-key constraint, so every re-sync appended a
-- fresh copy of every line instead of refreshing it. Dedupe, then enforce it.
DELETE FROM odds_player_props a
USING odds_player_props b
WHERE a.id < b.id
  AND a.provider_event_id = b.provider_event_id
  AND a.bookmaker_key     = b.bookmaker_key
  AND a.market_key        = b.market_key
  AND a.player_name       = b.player_name
  AND a.outcome_name      = b.outcome_name;

CREATE UNIQUE INDEX IF NOT EXISTS odds_player_props_natural_key
  ON odds_player_props
     (provider_event_id, bookmaker_key, market_key, player_name, outcome_name);
