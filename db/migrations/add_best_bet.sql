-- The one configuration that has been verified profitable out of sample.
--
-- Chosen on 2023-24 and verified on a 2025 season never used to choose, with a
-- bootstrap that resamples whole slates because picks on one Sunday are not
-- independent:
--
--   board as published                4,652 picks   +0.9%   [-1.7%, +4.0%]
--   unders only                       2,884        +4.4%   [+1.8%, +7.2%]
--   elite+strong unders               1,576        +5.1%   [+0.1%, +9.9%]
--   elite unders, one per player-game   783        +7.1%   [+1.6%, +13.2%]
--
-- Three conditions, each of which was established separately before being
-- combined:
--
--   1. Top tier, because the tiers rank monotonically once the probability is
--      calibrated (elite +4.7% through small -4.5%).
--   2. Under side, because the over side lost in both periods and no slice of
--      it survived either.
--   3. One pick per player-game, because 71.5% of player-games carried two or
--      more picks and receptions and receiving yards on the same player win and
--      lose together. Stacking them multiplies variance without multiplying
--      edge.
--
-- Distinct from value_flag, which marks the structural over-shade pattern and
-- needs no model at all. This one is the model's own verified selection.
ALTER TABLE prop_edges ADD COLUMN IF NOT EXISTS best_bet BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS best_bet BOOLEAN;
CREATE INDEX IF NOT EXISTS idx_prop_edges_best ON prop_edges (best_bet) WHERE best_bet;
