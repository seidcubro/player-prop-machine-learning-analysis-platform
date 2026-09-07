-- How prominent a player is, for default ordering.
--
-- The edges board ranked purely by expected value, so it opened on George
-- Holani and a fourth-string back with a 21.5 rushing line. That is arguably
-- the "best" row by EV and it is the wrong thing to put at the top of a page
-- someone just landed on: nobody is looking for Holani, and a board that leads
-- with players you have to look up reads as noise.
--
-- Star score is last completed season's fantasy production, which is the
-- closest thing in this data to "how much is this player bet on". It is a
-- display-ordering signal only. It never touches a model, a projection or a
-- price, and choosing any sort column replaces it entirely.
ALTER TABLE players ADD COLUMN IF NOT EXISTS star_score DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS idx_players_star ON players (star_score DESC NULLS LAST);
