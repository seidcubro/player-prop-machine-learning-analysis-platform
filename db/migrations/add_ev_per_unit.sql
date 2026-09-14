-- Expected value per unit staked, alongside the probability edge.
--
-- prop_edges.expected_value holds the model's probability minus the book's
-- implied probability. That is a probability edge, not an expected value, and
-- the two differ by exactly the decimal odds:
--
--   EV = p * b - (1 - p) = (b + 1) * (p - 1/(b + 1)) = decimal_odds * prob_edge
--
-- so a 5% probability edge is worth 6.8% per unit at -280 and 13.6% at +172.
-- Prices on the board span 1.36 to 2.72 in decimal terms, so ranking on the
-- probability edge under-ranks plus money by up to a factor of two, which is
-- the wrong direction for a platform whose one measured edge depends on taking
-- the best available price.
--
-- expected_value is left alone. The tier cuts were fitted on that quantity
-- against measured returns, and re-pointing them at a different number would
-- invalidate the only calibration on the board that has held up.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS ev_per_unit DOUBLE PRECISION;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS ev_per_unit DOUBLE PRECISION;
