UPDATE player_market_features pmf
SET extra_features = COALESCE(extra_features, '{}'::jsonb) || jsonb_build_object(
  'opp_rec_yds_allowed', t.opp_rec_yds_allowed,
  'opp_targets_allowed', t.opp_targets_allowed
)
FROM team_defense_rec t
WHERE pmf.opponent = t.team;
