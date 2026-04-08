INSERT INTO players (external_id, position, team, name)
SELECT DISTINCT
    pgs.player_id,
    pgs.position,
    pgs.team,
    pgs.player_id
FROM player_game_stats pgs
WHERE NOT EXISTS (
    SELECT 1
    FROM players p
    WHERE p.external_id = pgs.player_id
);
