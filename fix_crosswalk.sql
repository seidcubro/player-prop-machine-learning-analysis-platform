TRUNCATE TABLE player_id_crosswalk;
INSERT INTO player_id_crosswalk (gsis_id, pfr_id, espn_id, pff_id)
SELECT DISTINCT ON (player_id)
    player_id AS gsis_id,
    pfr_id,
    espn_id::text,
    pff_id::text
FROM rosters_weekly
WHERE player_id IS NOT NULL
ORDER BY player_id, season DESC, week DESC;
SELECT COUNT(*) AS total, COUNT(pfr_id) AS with_pfr FROM player_id_crosswalk;
