INSERT INTO players (external_id, position, team, name)
SELECT
    pgs.player_id,
    MAX(pgs.position),
    MAX(pgs.team),
    pgs.player_id
FROM player_game_stats pgs
GROUP BY pgs.player_id
ON CONFLICT (external_id) DO NOTHING;
