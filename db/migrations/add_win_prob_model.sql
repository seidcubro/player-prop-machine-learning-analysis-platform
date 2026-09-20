-- Keep the model's own probability, before it is blended toward the line.
--
-- build_prop_edges publishes the model's confidence corrected to the rate that
-- confidence actually hits (see fit_display_probability.py): on 2025, picks the
-- model rated 70% or better won 57% on unders and 65% on overs. win_prob holds
-- the published, corrected number, because that is the claim the record should
-- be judged on.
--
-- The correction is fitted on graded picks, so fitting it on win_prob would
-- learn from its own output and compound week to week, the same trap
-- win_prob_raw and projection_raw exist to avoid. This keeps the pre-blend figure where the
-- fit can read it. Historical rows leave it NULL; their win_prob was never
-- blended, and the fit reads COALESCE(win_prob_model, win_prob).
--
-- prop_edges_history as well: it was created LIKE prop_edges, and the archive
-- step inserts using prop_edges' column list, so a column missing there fails
-- the whole build.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS win_prob_model DOUBLE PRECISION;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS win_prob_model DOUBLE PRECISION;
ALTER TABLE prop_edges_history ADD COLUMN IF NOT EXISTS win_prob_model DOUBLE PRECISION;
