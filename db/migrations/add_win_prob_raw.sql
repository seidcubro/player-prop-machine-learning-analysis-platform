-- Keep the probability before the isotonic correction, so the correction is
-- never refitted on its own output.
--
-- build_prop_edges applies the probability calibrator and stores the result as
-- win_prob. grade_edges copies that into prop_edge_results, and
-- fit_probability_calibrator fits its next map on that column. The loop closes.
--
-- The failure here is not compounding, it is washout. Isotonic calibration maps
-- a probability to the rate it actually wins at, so if the shipped map is
-- working, a refit on its corrected output learns the identity: "these numbers
-- need no correction". Applying the identity then removes the correction, and
-- the board goes back out of calibration until the next refit notices. The
-- published probability would oscillate season to season with nothing failing.
--
-- Diluted for now, because the 6,827 backtested rows come from
-- backfill_track_record, which calibrates through its own PIT map rather than
-- this one, and only the 299 live rows are affected. That ratio moves every
-- week the season runs.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS win_prob_raw DOUBLE PRECISION;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS win_prob_raw DOUBLE PRECISION;
