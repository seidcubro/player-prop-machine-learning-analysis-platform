-- Each market is fitted and served on the positions that market is actually for.
--
--   rushing   (rush_yds, rush_att, rush_td)        quarterbacks and backs
--   receiving (rec_yds, recs, rec_td)              receivers, tight ends, backs
--   passing   (pass_yds, pass_att, pass_completions, pass_td)   quarterbacks
--
-- Rushing was eligible for {QB, RB, WR, FB}, and the wide receivers were 9,501
-- of 17,971 training rows: 53% of the fit, averaging 1.00 rushing yard and 0.18
-- carries, with 87% of those rows exactly zero.
--
--     rush_yds        rows     mean    pct zero
--     WR             9,501     1.00       87.4
--     RB             5,924    34.08       16.8
--     QB             2,214    17.43       12.7
--     FB               332     1.01       73.8
--
-- A squared-error loss spends its effort where the rows are, and no book prices
-- a receiver's rushing yards. A jet sweep happens. It is noise, and a model
-- fitted on noise learns the noise.
--
-- Fullbacks go too, on the same argument: 332 rows at a yard a game and 74%
-- zero is the same structural zero in a smaller package. A goal-line fullback
-- carry is a real thing and it belongs to anytime touchdown, which keeps its
-- fullbacks below.
--
-- research_priced_population.py measured the consequence in September: the top
-- quintile read 24% low, which turns the board into an all-unders board. It was
-- never shipped. docs/eda/FINDINGS.md found the same population from the other
-- direction and this is the fix for both.

UPDATE prop_markets
SET eligible_positions = ARRAY['QB', 'RB']
WHERE code IN ('rush_yds', 'rush_att', 'rush_td');

UPDATE prop_markets
SET eligible_positions = ARRAY['WR', 'TE', 'RB']
WHERE code IN ('rec_yds', 'recs', 'rec_td');

UPDATE prop_markets
SET eligible_positions = ARRAY['QB']
WHERE code IN ('pass_yds', 'pass_att', 'pass_completions', 'pass_td');

-- Anytime touchdown is the one market where everybody is genuinely eligible,
-- including the fullback on the one-yard line, so it keeps the wide net.
UPDATE prop_markets
SET eligible_positions = ARRAY['QB', 'RB', 'WR', 'TE', 'FB']
WHERE code = 'any_td';

SELECT code, eligible_positions FROM prop_markets ORDER BY code;
