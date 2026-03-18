UPDATE player_market_features pmf
SET extra_features = extra_features
  || jsonb_build_object(
    'opp_rec_yds_allowed_rolling', r.opp_rec_yds_allowed_rolling,
    'opp_targets_allowed_rolling', r.opp_targets_allowed_rolling
  )
FROM team_defense_rec_rolling r
WHERE pmf.opponent = r.defense_team
AND pmf.as_of_game_date = r.game_date;
