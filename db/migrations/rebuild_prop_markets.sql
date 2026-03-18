-- Clear and rebuild prop_markets with full sportsbook scope
TRUNCATE TABLE prop_markets CASCADE;

INSERT INTO prop_markets (code, name, stat_field) VALUES
  -- PASSING
  ('pass_yds',        'Passing Yards',              'passing_yards'),
  ('pass_tds',        'Passing Touchdowns',          'passing_tds'),
  ('pass_attempts',   'Pass Attempts',               'attempts'),
  ('pass_completions','Pass Completions',             'completions'),
  ('pass_ints',       'Interceptions Thrown',         'passing_interceptions'),
  -- RUSHING
  ('rush_yds',        'Rushing Yards',               'rushing_yards'),
  ('rush_tds',        'Rushing Touchdowns',           'rushing_tds'),
  ('carries',         'Carries',                     'carries'),
  -- RECEIVING
  ('rec_yds',         'Receiving Yards',             'receiving_yards'),
  ('recs',            'Receptions',                  'receptions'),
  ('targets',         'Targets',                     'targets'),
  ('rec_tds',         'Receiving Touchdowns',         'receiving_tds'),
  -- DEFENSE
  ('tackles_solo',    'Solo Tackles',                'def_tackles_solo'),
  ('tackles_combined','Tackles Combined',             'def_tackles_combined'),
  ('sacks',           'Sacks',                       'def_sacks'),
  ('def_ints',        'Defensive Interceptions',      'def_interceptions'),
  ('pass_defended',   'Passes Defended',              'def_pass_defended'),
  ('qb_hits',         'QB Hits',                     'def_qb_hits'),
  ('tfl',             'Tackles For Loss',             'def_tackles_for_loss'),
  ('forced_fumbles',  'Forced Fumbles',               'def_fumbles_forced'),
  -- KICKING
  ('fg_made',         'Field Goals Made',             'fg_made'),
  ('fg_att',          'Field Goal Attempts',          'fg_att'),
  ('fg_long',         'Longest Field Goal',           'fg_long'),
  ('xp_made',         'Extra Points Made',            'pat_made'),
  -- PUNTING
  ('punts',           'Punts',                       'punt_returns'),
  ('punt_yds',        'Punting Yards',               'punt_return_yards'),
  -- FANTASY
  ('fantasy_pts',     'Fantasy Points',              'fantasy_points'),
  ('fantasy_pts_ppr', 'Fantasy Points PPR',          'fantasy_points_ppr');

SELECT id, code, name FROM prop_markets ORDER BY id;
