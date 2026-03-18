ALTER TABLE prop_markets
  ADD COLUMN IF NOT EXISTS scope TEXT,
  ADD COLUMN IF NOT EXISTS target_kind TEXT,
  ADD COLUMN IF NOT EXISTS entity_key TEXT,
  ADD COLUMN IF NOT EXISTS eligible_positions TEXT[],
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS train_enabled BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS predict_enabled BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS feature_family TEXT,
  ADD COLUMN IF NOT EXISTS upstream_dependency_codes TEXT[],
  ADD COLUMN IF NOT EXISTS can_be_upstream_feature BOOLEAN DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS is_synthetic_target BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS notes TEXT;

UPDATE prop_markets
SET
  scope = 'player',
  target_kind = 'regression',
  entity_key = 'player_id',
  eligible_positions = CASE
    WHEN code IN ('pass_yds','pass_tds','pass_attempts','pass_completions','pass_ints')
      THEN ARRAY['QB']
    WHEN code IN ('rush_yds','rush_tds','carries')
      THEN ARRAY['QB','RB','WR','TE','FB']
    WHEN code IN ('rec_yds','recs','targets','rec_tds')
      THEN ARRAY['WR','TE','RB','FB']
    WHEN code IN ('tackles_solo','tackles_combined','sacks','def_ints','pass_defended',
                  'qb_hits','tfl','forced_fumbles')
      THEN ARRAY['LB','ILB','OLB','MLB','CB','DB','S','FS','SS','DE','DT','DL','NT']
    WHEN code IN ('fg_made','fg_att','fg_long','xp_made')
      THEN ARRAY['K']
    WHEN code IN ('punts','punt_yds')
      THEN ARRAY['P']
    ELSE eligible_positions
  END,
  is_active = TRUE,
  train_enabled = TRUE,
  predict_enabled = TRUE,
  feature_family = CASE
    WHEN code IN ('pass_yds','pass_tds','pass_attempts','pass_completions','pass_ints')
      THEN 'passing'
    WHEN code IN ('rush_yds','rush_tds','carries')
      THEN 'rushing'
    WHEN code IN ('rec_yds','recs','targets','rec_tds')
      THEN 'receiving'
    WHEN code IN ('tackles_solo','tackles_combined','sacks','def_ints','pass_defended',
                  'qb_hits','tfl','forced_fumbles')
      THEN 'defense'
    WHEN code IN ('fg_made','fg_att','fg_long','xp_made')
      THEN 'kicking'
    WHEN code IN ('punts','punt_yds')
      THEN 'punting'
    ELSE 'other'
  END,
  upstream_dependency_codes = COALESCE(upstream_dependency_codes, ARRAY[]::TEXT[]),
  can_be_upstream_feature = TRUE,
  is_synthetic_target = FALSE,
  notes = CASE
    WHEN code = 'pass_yds'
      THEN 'Core QB passing production market.'
    WHEN code = 'pass_tds'
      THEN 'QB touchdown production market.'
    WHEN code = 'pass_attempts'
      THEN 'QB usage/volume market.'
    WHEN code = 'pass_completions'
      THEN 'QB efficiency and volume market.'
    WHEN code = 'pass_ints'
      THEN 'QB turnover market.'
    WHEN code = 'rush_yds'
      THEN 'Player rushing production market.'
    WHEN code = 'rush_tds'
      THEN 'Player rushing touchdown market.'
    WHEN code = 'carries'
      THEN 'Player rushing usage market.'
    WHEN code = 'rec_yds'
      THEN 'Player receiving production market.'
    WHEN code = 'recs'
      THEN 'Player reception volume market.'
    WHEN code = 'targets'
      THEN 'Player receiving opportunity market.'
    WHEN code = 'rec_tds'
      THEN 'Player receiving touchdown market.'
    WHEN code = 'tackles_solo'
      THEN 'Defensive solo tackle market.'
    WHEN code = 'tackles_combined'
      THEN 'Defensive total tackle market.'
    WHEN code = 'sacks'
      THEN 'Defensive sack production market.'
    WHEN code = 'def_ints'
      THEN 'Defensive interception market.'
    WHEN code = 'pass_defended'
      THEN 'Defensive passes defended market.'
    WHEN code = 'qb_hits'
      THEN 'Defensive QB hits market.'
    WHEN code = 'tfl'
      THEN 'Defensive tackles for loss market.'
    WHEN code = 'forced_fumbles'
      THEN 'Defensive forced fumble market.'
    WHEN code = 'fg_made'
      THEN 'Kicker field goals made market.'
    WHEN code = 'fg_att'
      THEN 'Kicker field goal attempts market.'
    WHEN code = 'fg_long'
      THEN 'Kicker longest field goal market.'
    WHEN code = 'xp_made'
      THEN 'Kicker extra points made market.'
    WHEN code = 'punts'
      THEN 'Punter volume market.'
    WHEN code = 'punt_yds'
      THEN 'Punter yardage market.'
    ELSE COALESCE(notes, '')
  END;

SELECT
  id,
  code,
  scope,
  target_kind,
  entity_key,
  feature_family,
  eligible_positions,
  is_active,
  train_enabled,
  predict_enabled,
  can_be_upstream_feature,
  is_synthetic_target
FROM prop_markets
ORDER BY id;
