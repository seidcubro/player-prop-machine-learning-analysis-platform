-- Append-only history of every line we have ever seen.
--
-- `odds_player_props` holds the *current* line and upserts in place, which means
-- line history is destroyed on every sync. That costs two things:
--
--   1. Closing line value. Whether a pick beat the closing number is the fastest
--      honest signal that a model has real edge -- win/loss over a few hundred
--      picks is mostly variance, CLV converges in weeks.
--   2. Line movement as a feature. A line moving against public money is sharp
--      money, and it is predictive.
--
-- This table never updates a row. Every observation is kept, so the open, the
-- close, and everything between are all recoverable.

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id                BIGSERIAL PRIMARY KEY,
    provider_event_id TEXT NOT NULL,
    sport_key         TEXT NOT NULL DEFAULT 'americanfootball_nfl',
    commence_time     TIMESTAMPTZ,
    home_team         TEXT,
    away_team         TEXT,
    bookmaker_key     TEXT NOT NULL,
    bookmaker_title   TEXT,
    market_key        TEXT NOT NULL,
    player_name       TEXT NOT NULL,
    outcome_name      TEXT NOT NULL,
    line              DOUBLE PRECISION,
    price_american    INTEGER,
    -- When the sportsbook last changed this line, per the feed.
    last_update       TIMESTAMPTZ,
    -- When we asked. For historical pulls this is the as-of timestamp requested,
    -- which is what makes a snapshot reproducible.
    observed_at       TIMESTAMPTZ NOT NULL,
    -- 'live' for a normal sync, 'historical' for a backfilled as-of pull.
    source            TEXT NOT NULL DEFAULT 'live',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per line per observation moment. Re-running a backfill for the same
-- timestamp is then idempotent instead of duplicating the whole slate.
CREATE UNIQUE INDEX IF NOT EXISTS odds_snapshots_observation
    ON odds_snapshots (provider_event_id, bookmaker_key, market_key,
                       player_name, outcome_name, observed_at);

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_event   ON odds_snapshots (provider_event_id);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_player  ON odds_snapshots (player_name);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_kickoff ON odds_snapshots (commence_time);

-- Tracks credit spend so a backfill can be costed before it is repeated.
CREATE TABLE IF NOT EXISTS odds_api_usage (
    id            BIGSERIAL PRIMARY KEY,
    ran_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    operation     TEXT NOT NULL,
    detail        TEXT,
    requests_used INTEGER,
    remaining     INTEGER
);
