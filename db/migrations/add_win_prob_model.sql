-- Keep the model's own probability, before it is blended toward the line.
--
-- build_prop_edges now publishes a win probability shrunk toward the price's
-- implied one (see fit_market_blend.py): graded on 2025, the model claimed
-- 65.9% on elite picks that won 53.5%, and the blend put them at 53.3%.
-- win_prob holds the published, blended number, because that is the claim the
-- record should be judged on.
--
-- The blend is fitted on graded picks, so fitting it on win_prob would learn
-- from its own output and compound week to week, the same trap win_prob_raw
-- and projection_raw exist to avoid. This keeps the pre-blend figure where the
-- fit can read it. Historical rows leave it NULL; their win_prob was never
-- blended, and the fit reads COALESCE(win_prob_model, win_prob).
--
-- prop_edges_history as well: it was created LIKE prop_edges, and the archive
-- step inserts using prop_edges' column list, so a column missing there fails
-- the whole build.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS win_prob_model DOUBLE PRECISION;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS win_prob_model DOUBLE PRECISION;
ALTER TABLE prop_edges_history ADD COLUMN IF NOT EXISTS win_prob_model DOUBLE PRECISION;
