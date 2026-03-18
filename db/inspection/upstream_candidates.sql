SELECT code, feature_family, can_be_upstream_feature
FROM prop_markets
WHERE can_be_upstream_feature = TRUE
ORDER BY feature_family, code;
