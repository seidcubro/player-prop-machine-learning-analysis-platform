UPDATE player_market_features pmf
SET extra_features = extra_features
  || jsonb_build_object(
    'target_share',
    CASE
      WHEN (extra_features->>'team_pass_attempts')::float > 0
      THEN (targets_mean / (extra_features->>'team_pass_attempts')::float)
      ELSE 0
    END
  );
