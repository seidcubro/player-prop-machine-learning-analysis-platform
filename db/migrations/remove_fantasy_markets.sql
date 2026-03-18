DELETE FROM prop_markets
WHERE code IN ('fantasy_pts', 'fantasy_pts_ppr', 'dst_fantasy_pts');

SELECT id, code, name
FROM prop_markets
ORDER BY id;
