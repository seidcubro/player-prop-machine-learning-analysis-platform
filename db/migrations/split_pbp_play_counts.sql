-- Separate the two play counts that pbp_player_game used to share.
--
-- total_plays meant targets on a receiving row and carries on a rushing one,
-- and the two were collapsed to one row per player-game, so a rusher's carries
-- were written into his target count and a receiving back lost his rushing
-- context entirely.
ALTER TABLE pbp_player_game ADD COLUMN IF NOT EXISTS pbp_targets INT;
ALTER TABLE pbp_player_game ADD COLUMN IF NOT EXISTS pbp_carries INT;
