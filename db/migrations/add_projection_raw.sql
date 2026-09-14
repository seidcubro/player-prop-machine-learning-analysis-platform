-- Keep the uncorrected projection, so the correction is never fitted on itself.
--
-- fit_spread_calibrator.py learns its map from player_projection_history, and
-- build_projections.py writes that history after applying the map. Left alone
-- the two form a loop: each week's slate enters history already corrected, the
-- next fit measures the residual bias of corrected numbers, and the correction
-- compounds on itself a little more every week. Nothing would fail; the
-- projections would simply drift upward all season.
--
-- Storing both breaks the loop. The fit reads the raw column, the site reads
-- the corrected one, and neither can contaminate the other.
ALTER TABLE player_projection_history
  ADD COLUMN IF NOT EXISTS projection_raw DOUBLE PRECISION;
ALTER TABLE player_projection_history
  ADD COLUMN IF NOT EXISTS p50_raw DOUBLE PRECISION;

-- Rows written before this column existed came from the backfill, which never
-- applied the correction, so their projection is already the raw one.
UPDATE player_projection_history
   SET projection_raw = projection, p50_raw = p50
 WHERE projection_raw IS NULL;
