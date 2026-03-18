UPDATE prop_markets
SET
  is_active = FALSE,
  train_enabled = FALSE,
  predict_enabled = FALSE,
  notes = 'Disabled for now: current stat_field maps to punt return stats, not true punting stats.'
WHERE code IN ('punts', 'punt_yds');

SELECT code, stat_field, is_active, train_enabled, predict_enabled, notes
FROM prop_markets
WHERE code IN ('punts', 'punt_yds')
ORDER BY id;
