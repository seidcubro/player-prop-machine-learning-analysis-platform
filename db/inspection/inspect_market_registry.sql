SELECT
  id,
  code,
  name,
  stat_field,
  scope,
  target_kind,
  entity_key,
  eligible_positions,
  feature_family,
  is_active,
  train_enabled,
  predict_enabled,
  can_be_upstream_feature,
  is_synthetic_target
FROM prop_markets
WHERE code IN ('rec_yds','pass_yds','tackles_combined','fg_made','punt_yds')
ORDER BY id;
