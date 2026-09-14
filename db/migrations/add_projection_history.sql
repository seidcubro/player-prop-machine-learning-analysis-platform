-- Keep what we projected, after the game is played.
--
-- player_projections is TRUNCATEd on every build, because it answers "what do
-- we think about the upcoming slate". That makes it useless for the question a
-- reader actually asks first, which is "you projected this, what happened?".
-- Once a game kicks off the number we published vanishes and the only surviving
-- record is prop_edge_results, which holds a row only where a sportsbook posted
-- a line we had paid for and the pick cleared the publication filters. For a
-- player like Trey McBride that is nothing at all: real projections, no stored
-- history, because no pick was ever published on him.
--
-- This keeps one row per player, market and game: the most recent projection
-- made before that game. Upserted rather than appended, so a slate reprojected
-- five times in a week leaves the last word rather than five rows.
CREATE TABLE IF NOT EXISTS player_projection_history (
    player_id     TEXT NOT NULL,
    player_name   TEXT,
    team          TEXT,
    opponent      TEXT,
    position      TEXT,
    market_code   TEXT NOT NULL,
    game_date     DATE NOT NULL,
    projection    DOUBLE PRECISION,
    p10           DOUBLE PRECISION,
    p25           DOUBLE PRECISION,
    p50           DOUBLE PRECISION,
    p75           DOUBLE PRECISION,
    p90           DOUBLE PRECISION,
    model_name    TEXT,
    depth_rank    INTEGER,
    projected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_id, market_code, game_date)
);

CREATE INDEX IF NOT EXISTS idx_projection_history_player
    ON player_projection_history (player_id, game_date DESC);
