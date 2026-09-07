#!/bin/sh
# In-season weekly run. Everything the platform needs to stay current and to
# keep accumulating the record that makes it worth anything.
#
# Run this twice a week during the season:
#
#   Tuesday  (grade last week, refresh data and models)   sh scripts/weekly_update.sh
#   Sunday   (capture closing lines an hour before kick)  sh scripts/weekly_update.sh --close-only
#
# The Sunday run matters more than it looks. Closing line value converges far
# faster than win/loss does, and a closing line I did not capture is gone for
# good unless I pay the archive rate for it later.
#
# Odds credits used: roughly 130 for a normal run, 130 for a close-only run.
# The historical archive costs about 840 per slate, so capturing live is ~6x
# cheaper than buying the same data back afterwards.

set -e

API="${API:-http://localhost:8000/api/v1}"

# The write endpoints now require a shared secret, so an exposed API cannot have
# its Odds credits spent by a stranger. Export ADMIN_TOKEN before running this.
: "${ADMIN_TOKEN:?set ADMIN_TOKEN to the value the API is running with}"
AUTH="X-Admin-Token: ${ADMIN_TOKEN}"
# any_td is the anytime touchdown scorer market, which is what books
# actually post for touchdowns. The split rush_td and rec_td keys returned
# zero rows across three seasons, but their models still run because the
# projections page shows a number for every market a position can produce.
MARKETS="rec_yds rush_yds pass_yds recs rush_att pass_att pass_completions pass_td rush_td rec_td any_td"
MODEL="${MODEL:-rf_v10}"
CLOSE_ONLY=0
[ "$1" = "--close-only" ] && CLOSE_ONLY=1

log() { printf '\n==> %s\n' "$1"; }

# ---------------------------------------------------------------- odds
log "odds: events"
curl -sf -X POST -H "$AUTH" "$API/odds/sync/events" >/dev/null || {
  echo "FAILED: events sync"; exit 1; }

log "odds: player props for the upcoming slate"
curl -sf -X POST -H "$AUTH" "$API/odds/sync/player_props?days_ahead=8" >/dev/null || {
  echo "FAILED: props sync"; exit 1; }

# Snapshot whatever is currently posted into the append-only history. This is
# what CLV is computed against later.
log "snapshot current lines into odds_snapshots"
docker compose exec -T postgres psql -U app -d app -q <<'SQL'
INSERT INTO odds_snapshots
  (provider_event_id, sport_key, commence_time, home_team, away_team,
   bookmaker_key, bookmaker_title, market_key, player_name, outcome_name,
   line, price_american, last_update, observed_at, source)
SELECT p.provider_event_id, p.sport_key, e.commence_time, e.home_team, e.away_team,
       p.bookmaker_key, p.bookmaker_title, p.market_key, p.player_name,
       p.outcome_name, p.line, p.price_american, p.last_update,
       date_trunc('hour', NOW()), 'live'
FROM odds_player_props p
LEFT JOIN odds_events e ON e.provider_event_id = p.provider_event_id
WHERE e.commence_time >= NOW()
ON CONFLICT (provider_event_id, bookmaker_key, market_key,
             player_name, outcome_name, observed_at) DO NOTHING;
SQL

if [ "$CLOSE_ONLY" = "1" ]; then
  log "close-only run finished"
  exit 0
fi

# ---------------------------------------------------------------- data
log "ingest nflverse (last week's results land here)"
docker build -q -f jobs/ingestion/Dockerfile -t propsignal-ingest . >/dev/null
docker run --rm --network player-prop-platform_default \
  -e DATABASE_URL="postgresql://app:app@postgres:5432/app" \
  -e SEASON_START=2022 -e SEASON_END="$(date +%Y)" propsignal-ingest

log "team backfill (new rows arrive with a NULL team)"
docker compose exec -T postgres psql -U app -d app -q -f - < db/backfills/fix_team_final.sql

# ---------------------------------------------------------------- features
log "rebuild features"
for m in $MARKETS; do
  curl -sf -X POST -H "$AUTH" "$API/jobs/build_features?market_code=$m&lookback=5" >/dev/null || {
    echo "FAILED: build_features $m"; exit 1; }
  curl -sf -X POST -H "$AUTH" "$API/jobs/attach_labels?market_code=$m&lookback=5" >/dev/null
  printf '    %s\n' "$m"
done

# ---------------------------------------------------------------- models
# Retraining weekly is deliberate. Each week adds real games, and the whole
# point of the setup is that it keeps learning from its own results.
log "retrain and re-evaluate"
for m in $MARKETS; do
  docker compose run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$MODEL" -e LOOKBACK=5 \
    training python train.py >/dev/null
  docker compose run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$MODEL" -e LOOKBACK=5 \
    training python eval.py >/dev/null
  printf '    %s\n' "$m"
done

log "quantiles (must match the point model's feature space)"
for m in $MARKETS; do
  docker compose run --rm -e MARKET_CODE="$m" -e LOOKBACK=5 -e SPLIT_MODE=season \
    training python train_quantiles.py >/dev/null
done

log "stale-role factors"
for m in $MARKETS; do
  docker compose run --rm -e MARKET_CODE="$m" -e LOOKBACK=5 -e WRITE_FACTORS=1 \
    training python eval_stale_role.py 2>&1 | grep -E "wrote factor|no factor" || true
done

# ---------------------------------------------------------------- serve
log "project every eligible player"
docker compose run --rm training python build_projections.py

# Refit the probability calibrator before building edges, not after.
#
# The edge builder reads this artifact to correct P(over) before it chooses a
# side, computes expected value or assigns a tier. Building edges first would
# publish a week of picks against last week's calibration.
log "refit probability calibrator from graded results"
docker compose run --rm training python fit_probability_calibrator.py

log "build edges"
docker compose run --rm training python build_prop_edges.py

log "grade whatever has been played"
docker compose run --rm training python grade_edges.py

log "closing line value"
docker compose run --rm training python eval_clv.py || \
  echo "  (no closed games to score yet)"

# ---------------------------------------------------------------- gate
log "freshness audit"
docker compose run --rm training python audit_freshness.py

log "done"
