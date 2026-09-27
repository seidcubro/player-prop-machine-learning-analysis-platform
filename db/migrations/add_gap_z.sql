-- The disagreement that now decides a pick's tier, and the price label that no
-- longer does.
--
-- gap_z is (median projection - line) divided by the width of the predicted
-- distribution, so it is in standard deviations and comparable across markets.
-- It is what edge_tier is cut on: see services/training/gap_tier.py. Stored
-- rather than recomputed because the site needs to show why a pick is ranked
-- where it is, and because grading it later is how the thresholds get checked
-- against a season rather than argued about.
--
-- negative_ev marks a pick whose published probability does not beat the price
-- on offer. Under the old expected-value ranking such a pick was demoted; under
-- the gap ranking it is published and labelled, because the ranking is about
-- whether the line is wrong and the price is a separate question for whoever is
-- betting.

ALTER TABLE prop_edges
  ADD COLUMN IF NOT EXISTS gap_z DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS negative_ev BOOLEAN;

ALTER TABLE prop_edges_history
  ADD COLUMN IF NOT EXISTS gap_z DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS negative_ev BOOLEAN;

-- Carried into the graded record so the rule can be judged on outcomes. Without
-- this, next season's question of whether a full standard deviation still hits
-- 60% has no column to ask.
ALTER TABLE prop_edge_results
  ADD COLUMN IF NOT EXISTS gap_z DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS negative_ev BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_prop_edges_gap_z
  ON prop_edges (gap_z);
