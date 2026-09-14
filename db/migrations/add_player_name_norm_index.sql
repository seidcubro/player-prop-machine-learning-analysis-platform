-- Index the normalised name the edge board resolves players by.
--
-- /edges matches a prop's player_name against players.name through
-- lower(replace(replace(name, '.', ''), '-', ' ')), which no plain index can
-- serve, so every board row drove a sequential scan of 25,079 players
-- evaluating that expression on each one. Forty-six rows on the board meant
-- forty-six full scans and about 300ms on the endpoint the dashboard loads
-- first.
--
-- The expression has to match the query character for character to be used.
CREATE INDEX IF NOT EXISTS idx_players_name_norm
    ON players (lower(replace(replace(name, '.', ''), '-', ' ')));
