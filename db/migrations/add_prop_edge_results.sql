-- Graded history: for every past edge, did the pick actually win?
--
-- Without this the platform can claim an 83% win probability and never be held
-- to it. Grading closes the loop: each historical edge is matched to what the
-- player actually did, so hit rate can be compared against predicted win
-- probability (the only real test of calibration) and shown per player.

CREATE TABLE IF NOT EXISTS prop_edge_results (
    edge_id           BIGINT PRIMARY KEY REFERENCES prop_edges(id) ON DELETE CASCADE,
    player_id         TEXT,
    game_date         DATE,
    market_code       TEXT NOT NULL,
    line              DOUBLE PRECISION,
    projection        DOUBLE PRECISION,
    recommended_side  TEXT,
    win_prob          DOUBLE PRECISION,
    edge_tier         TEXT,
    actual            DOUBLE PRECISION,
    -- TRUE  = the recommended side won
    -- FALSE = it lost
    -- NULL  = push (actual landed exactly on the line). Excluded from hit rate
    hit               BOOLEAN,
    graded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_edge_results_player ON prop_edge_results (player_id);
CREATE INDEX IF NOT EXISTS idx_edge_results_market ON prop_edge_results (market_code);
CREATE INDEX IF NOT EXISTS idx_edge_results_tier   ON prop_edge_results (edge_tier);
