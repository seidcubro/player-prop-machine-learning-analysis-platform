UPDATE player_market_features pmf
SET extra_features = COALESCE(pmf.extra_features, '{}'::jsonb) || jsonb_build_object(
  'opp_allowed_mean',
  tds.avg_allowed
)
FROM team_defense_stats tds
WHERE pmf.opponent = tds.team;
