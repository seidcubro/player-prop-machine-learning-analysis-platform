#!/bin/sh
# One-time migration for the season-only feature window. Run once, then never again.
#
# The window used to reach into last December and now stops at the start of the
# season, which changes which feature rows exist as well as what they contain:
#
#   rows that no longer qualify   Week 1, a player's first game of a season, and
#                                 anyone with no game this season at all
#   rows that now qualify         Weeks 2 to 5, where a window of two or three
#                                 games used to be refused
#
# `build_features` upserts and never deletes, so the first group would survive a
# normal rebuild carrying values computed under the old definition, and training
# would read them beside the new ones. On the development copy that was 2,400
# rows per market out of 21,000: enough to make every before-and-after number
# meaningless and not enough to look obviously wrong.
#
# So this deletes them once. Afterwards the definitions are stable and the
# ordinary weekly job stays correct on its own, because every row the builder
# emits is a row it upserts. Nothing here is needed a second time.
#
# Derived data only. Every row deleted is rebuilt from box scores in the next two
# steps, and the labels are re-attached from player_game_stats_app, which is why
# this is safe to run without a backup of the feature store. It does not touch
# player_game_stats_app, odds_snapshots, prop_edge_results or anything else that
# cannot be recomputed.
#
# Usage, on the server, with the production environment loaded:
#
#   . /etc/priorline/env
#   ./scripts/migrate_window_change.sh
#
# Then the ordinary weekly run, which retrains every market on the rebuilt
# features and refits the calibrators in the order they depend on each other:
#
#   ./scripts/scheduled_update.sh --weekly
#
# Do not skip that second command. `career_n` counts career games again and the
# training set has a different shape, so the models that exist now were fitted on
# features that no longer exist. Old models on new features is the one state worse
# than either.

set -e

API="${API:-http://localhost:8000/api/v1}"
AUTH="X-Admin-Token: ${ADMIN_TOKEN}"
COMPOSE="${COMPOSE:-docker compose}"
# The seven point markets. The touchdown markets are trained by a different
# script on a different feature path and are left alone.
MARKETS="rec_yds rush_yds pass_yds recs rush_att pass_att pass_completions"

log() { printf '\n[%s] ==> %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"; }

if [ -z "${ADMIN_TOKEN}" ]; then
  echo "ADMIN_TOKEN is not set. Load the production environment first:"
  echo "  . /etc/priorline/env"
  exit 1
fi

log "rebuild the API and training images on the new code"
$COMPOSE up -d --build api
$COMPOSE build training

# Wait for the API rather than guessing at a sleep: the delete below is only safe
# if the rebuild that follows it can actually run.
log "wait for the API"
i=0
until curl -sf "$API/health" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && { echo "API did not come up"; exit 1; }
  sleep 2
done

log "count what is there now"
$COMPOSE exec -T postgres psql -U app -d app -c \
  "SELECT p.code, count(*) AS rows,
          count(*) FILTER (WHERE f.label_actual IS NOT NULL) AS labelled
     FROM player_market_features f
     JOIN prop_markets p ON p.id = f.market_id
    WHERE f.lookback = 5
      AND p.code IN ('rec_yds','rush_yds','pass_yds','recs','rush_att',
                     'pass_att','pass_completions')
    GROUP BY p.code ORDER BY p.code;"

log "delete the feature rows for those markets"
$COMPOSE exec -T postgres psql -U app -d app -c \
  "DELETE FROM player_market_features
    WHERE lookback = 5
      AND market_id IN (SELECT id FROM prop_markets
                         WHERE code IN ('rec_yds','rush_yds','pass_yds','recs',
                                        'rush_att','pass_att','pass_completions'));"

log "rebuild them, and re-attach the labels"
for m in $MARKETS; do
  curl -sf -X POST -H "$AUTH" \
    "$API/jobs/build_features?market_code=$m&lookback=5" >/dev/null || {
      echo "FAILED: build_features $m"; exit 1; }
  curl -sf -X POST -H "$AUTH" \
    "$API/jobs/attach_labels?market_code=$m&lookback=5" >/dev/null || {
      echo "FAILED: attach_labels $m"; exit 1; }
  printf '    %s\n' "$m"
done

log "count what is there now, for comparison"
$COMPOSE exec -T postgres psql -U app -d app -c \
  "SELECT p.code, count(*) AS rows,
          count(*) FILTER (WHERE f.label_actual IS NOT NULL) AS labelled,
          count(*) FILTER (WHERE f.extra_features ? 'y_blend') AS with_blend,
          count(*) FILTER (WHERE f.label_actual IS NULL
                             AND f.as_of_game_date >= CURRENT_DATE) AS serving
     FROM player_market_features f
     JOIN prop_markets p ON p.id = f.market_id
    WHERE f.lookback = 5
      AND p.code IN ('rec_yds','rush_yds','pass_yds','recs','rush_att',
                     'pass_att','pass_completions')
    GROUP BY p.code ORDER BY p.code;"

cat <<'DONE'

Features rebuilt. Two things to check in the table above.

  with_blend should equal rows. If it is zero, the API is running old code and
  the rebuild used the old window; check that `up -d --build api` actually
  replaced the container.

  serving should be well under what it was. On the development copy it fell from
  780 to 377, and the difference is players being projected with no game this
  season at all, which is what this change exists to stop.

Now retrain. Nothing should be trusted until this finishes, because every model
on disk was fitted on features that no longer exist:

  ./scripts/scheduled_update.sh --weekly

DONE
