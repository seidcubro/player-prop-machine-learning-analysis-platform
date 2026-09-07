-- Marks edges that match the one strategy that survived out-of-sample testing:
-- under side per the quantile median, top line tier within the market (where
-- the public over-shade concentrates), any market except pass_yds. Measured at
-- +3.7% edge over break-even at best price on 2025, with a model-free +5.9%
-- structural check on 2023/24. See services/training/backtest_sides.py.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS value_flag BOOLEAN NOT NULL DEFAULT FALSE;
