-- The quantity the tiers are actually cut on.
--
-- gap_share is |median projection - line| divided by the line, so a two yard
-- disagreement on a 4.5 yard line and the same two yards on a 70 yard line are
-- not treated as the same claim. Measured across the 2026 season, that ratio
-- ranks accuracy and the raw gap does not: the widest band beats the narrowest
-- by +7.9% [+1.7%, +21.2%] as a share of the line, and by -0.2% [-10.9%,
-- +10.7%] in raw units.
--
-- Stored rather than recomputed for the same reason gap_z is. The board is
-- tiered by RANK within each market's own board, so a pick's grade depends on
-- what else was published that night, and without the number that produced it
-- there is no way to check a tier after the fact or to re-cut the shares
-- against a graded season.
--
-- The build warned "prop_edges has no column(s) ['gap_share']; writing without
-- them" and carried on, which is the right behaviour and also the reason this
-- is easy to forget: nothing broke, the column was simply dropped on the floor.

ALTER TABLE prop_edges
  ADD COLUMN IF NOT EXISTS gap_share DOUBLE PRECISION;

ALTER TABLE prop_edges_history
  ADD COLUMN IF NOT EXISTS gap_share DOUBLE PRECISION;

-- Carried into the graded record, so next month's question -- does the top
-- tenth of a board still hit most -- has a column to ask.
ALTER TABLE prop_edge_results
  ADD COLUMN IF NOT EXISTS gap_share DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_prop_edges_gap_share
  ON prop_edges (gap_share);
