-- Rushing props are a running back and quarterback market. Train them on one.
--
-- rush_yds and rush_att were eligible for {QB, RB, WR, FB}, and the wide
-- receivers were 9,501 of 17,971 training rows: 53% of the fit, averaging 1.00
-- rushing yard and 0.18 carries, with 87% of those rows exactly zero. A
-- squared-error loss spends its effort where the rows are, and no book prices a
-- receiver's rushing yards.
--
--     rush_yds        rows     mean    pct zero
--     WR             9,501     1.00       87.4
--     RB             5,924    34.08       16.8
--     QB             2,214    17.43       12.7
--     FB               332     1.01       73.8
--
-- research_priced_population.py measured the consequence in September: the top
-- quintile read 24% low, which turns the board into an all-unders board. It was
-- never shipped. docs/eda/FINDINGS.md found the same population from the other
-- direction and this is the fix for both.
--
-- Fullbacks stay. They carry the ball rarely but they carry it, they are 332
-- rows, and a goal-line fullback is a real anytime-touchdown prop.
--
-- rush_td keeps its receivers. A receiver's rushing touchdown is a jet sweep
-- that mostly does not happen, but anytime-touchdown props are priced on
-- receivers and rush_td feeds that market's reasoning.

UPDATE prop_markets
SET eligible_positions = ARRAY['QB', 'RB', 'FB']
WHERE code IN ('rush_yds', 'rush_att');

SELECT code, eligible_positions FROM prop_markets ORDER BY code;
