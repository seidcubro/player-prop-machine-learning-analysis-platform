-- Expected value of the recommended side against the price actually offered.
--
-- The edge builder used to pick whichever side the model thought more likely
-- and rank by how far that probability sat from 50%. That ignores the price
-- entirely: on a -130 under the break-even is 56.5%, so a 52% under is the
-- likelier outcome and still a losing bet, and roughly half the props on a
-- slate have that shape.
--
-- Backtested on a held-out 2025 with models refit on earlier seasons only,
-- best of three books, bootstrapped by slate:
--
--   more-likely-side rule   -0.4% ROI
--   EV > 2%                 +1.1%
--   EV > 4%                 +2.0%
--   EV > 8%                 +2.7%   95% CI [+0.2%, +5.1%]
--   EV > 10%                +4.1%   95% CI [+1.0%, +7.2%]
--
-- Monotone across the whole range, and the first interval in this project that
-- clears zero. Tiers are cut on this column for that reason.
-- See services/training/backtest_quantile_ev.py.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS expected_value DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_prop_edges_ev ON prop_edges (expected_value DESC);
