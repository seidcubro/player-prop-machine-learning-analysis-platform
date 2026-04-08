DROP TABLE IF EXISTS players;

CREATE TABLE players AS
SELECT DISTINCT
    player_id AS external_id,
    player_id AS id,
    position,
    team
FROM player_game_stats;

ALTER TABLE players ADD COLUMN name TEXT;
