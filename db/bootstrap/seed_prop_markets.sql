UPDATE prop_markets
SET
    scope = 'player',
    target_kind = 'regression',
    entity_key = 'player_id',
    is_active = TRUE,
    train_enabled = TRUE,
    predict_enabled = TRUE,
    can_be_upstream_feature = TRUE,
    is_synthetic_target = FALSE
WHERE scope IS NULL;
