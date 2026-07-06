-- prop_markets.eligible_positions was empty for every market, so jobs.py's
-- existing (but unused) position filter never applied, and train.py had no
-- position filtering at all. Every model was trained on all 25 player
-- positions, including ones that trivially never produce that stat (e.g.
-- defensive linemen always rushing for 0 yards), diluting training data and
-- making cross-market eval comparisons meaningless. Positions below were
-- chosen from actual nonzero-stat counts in player_game_stats_app.
UPDATE prop_markets SET eligible_positions = ARRAY['QB']
  WHERE code IN ('pass_att', 'pass_yds', 'pass_td', 'pass_completions');

UPDATE prop_markets SET eligible_positions = ARRAY['QB', 'RB', 'WR', 'FB']
  WHERE code IN ('rush_att', 'rush_yds', 'rush_td');

UPDATE prop_markets SET eligible_positions = ARRAY['WR', 'TE', 'RB', 'FB']
  WHERE code IN ('rec_yds', 'recs', 'rec_td');
