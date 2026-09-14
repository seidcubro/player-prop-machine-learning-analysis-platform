-- Refresh the three materialized views the feature build reads.
--
-- team_offense_pass, team_defense_rec and team_defense_rec_rolling are
-- materialized, and nothing in this repository refreshed them. They were last
-- populated up to 2026-02-08, the Super Bowl, while player_game_stats_app ran
-- to the current week.
--
-- That was not yet visible in the numbers, because the features these views
-- feed are averaged over a player's previous games and in week 1 those are all
-- from last season, which the views did cover. It would have become visible
-- slowly: every 2026 game entering a player's window joins to nothing, is
-- coalesced to zero, and drags team_pass_attempts, target_share and the
-- opponent receiving features toward zero as the season goes on. A model
-- quietly getting worse week by week with nothing failing.
--
-- team_offense_pass also sums targets, so it carried the target corruption that
-- wrote carry counts into the target column. Its pass volume was inflated and
-- every target share computed from it was too low.
REFRESH MATERIALIZED VIEW team_offense_pass;
REFRESH MATERIALIZED VIEW team_defense_rec;
REFRESH MATERIALIZED VIEW team_defense_rec_rolling;
