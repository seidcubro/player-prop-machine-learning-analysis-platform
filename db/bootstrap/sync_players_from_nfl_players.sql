INSERT INTO players (external_id, first_name, last_name, name, position, team)
SELECT
    np.player_id AS external_id,
    CASE
        WHEN strpos(trim(np.full_name), ' ') > 0
            THEN split_part(trim(np.full_name), ' ', 1)
        ELSE trim(np.full_name)
    END AS first_name,
    CASE
        WHEN strpos(trim(np.full_name), ' ') > 0
            THEN regexp_replace(trim(np.full_name), '^\S+\s+', '')
        ELSE NULL
    END AS last_name,
    trim(np.full_name) AS name,
    np.position,
    np.team
FROM nfl_players np
WHERE np.player_id IS NOT NULL
ON CONFLICT (external_id) DO UPDATE SET
    first_name = EXCLUDED.first_name,
    last_name  = EXCLUDED.last_name,
    name       = EXCLUDED.name,
    position   = EXCLUDED.position,
    team       = EXCLUDED.team;
