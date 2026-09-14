-- Drop live prices for games that have already kicked off, once archived.
--
-- odds_player_props is meant to hold the prices currently on offer. It never
-- dropped anything, so it accumulated every game of the season: 4,181 of 4,509
-- rows were for games already played. The edge builder ignores them and the
-- freshness audit has warned about them for weeks, which is the worst kind of
-- warning, the one that is always there.
--
-- The delete is conditioned on the row existing in odds_snapshots, so a price
-- that was never archived is kept rather than lost. Closing prices cannot be
-- bought back at anything like the same cost, which is the whole reason the
-- archive exists.
DELETE FROM odds_player_props p
USING odds_events e
WHERE e.provider_event_id = p.provider_event_id
  AND e.commence_time < NOW()
  AND EXISTS (
        SELECT 1 FROM odds_snapshots s
         WHERE s.provider_event_id = p.provider_event_id
           AND s.bookmaker_key     = p.bookmaker_key
           AND s.market_key        = p.market_key
           AND s.player_name       = p.player_name
           AND s.outcome_name      = p.outcome_name);
