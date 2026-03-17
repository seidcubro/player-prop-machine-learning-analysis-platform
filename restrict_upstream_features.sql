-- Reset EVERYTHING to false
UPDATE prop_markets
SET can_be_upstream_feature = FALSE;

-- Enable ONLY safe foundational stats
UPDATE prop_markets
SET can_be_upstream_feature = TRUE
WHERE code IN (
  'targets',
  'recs',
  'pass_attempts',
  'pass_completions',
  'carries'
);

SELECT code, feature_family, can_be_upstream_feature
FROM prop_markets
ORDER BY feature_family, code;
