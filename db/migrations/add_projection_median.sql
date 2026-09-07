-- The median projection, alongside the mean.
--
-- The recommended side is chosen from the predicted distribution, so the number
-- displayed next to it has to come from that same distribution. It did not: the
-- table stored the mean, and on a right-skewed market the mean sits well above
-- the median. That produced 59 rows out of 283 reading "model 75.6, line 66.5,
-- pick UNDER", which looks like a straightforward bug and is really a
-- mean-versus-median mismatch. For receiving yards the mean runs 25-35% above
-- the median, so the two disagree constantly.
--
-- The mean is kept because it is still the right number for a projection (it is
-- what the point model is trained to predict, and it is what the projections
-- page shows). The median is what belongs beside a pick.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS projection_median DOUBLE PRECISION;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS projection_median DOUBLE PRECISION;
