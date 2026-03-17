UPDATE player_market_features pmf
SET extra_features = extra_features
  || jsonb_build_object(
    'team_pass_attempts', t.team_pass_attempts
  )
FROM team_offense_pass t
WHERE pmf.team = t.team
AND pmf.as_of_game_date = t.game_date;
