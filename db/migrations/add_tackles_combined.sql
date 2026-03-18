-- Add tackles_combined as computed column
ALTER TABLE player_game_stats_app
ADD COLUMN IF NOT EXISTS def_tackles_combined FLOAT
GENERATED ALWAYS AS (
    COALESCE(def_tackles_solo, 0) + COALESCE(def_tackle_assists, 0)
) STORED;

SELECT 'column added' AS status;
