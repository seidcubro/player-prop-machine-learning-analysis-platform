#!/bin/sh
# Refresh the whole platform, in dependency order, and refuse to finish dirty.
#
# The ordering here is not cosmetic. Every step invalidates the artifacts of the
# ones after it, and each of these has silently shipped stale output before:
#
#   ingestion -> features        new seasons arrive with NULL team, and the
#                                team backfill is a separate script
#   features  -> models          a model trained on an older feature space
#   models    -> quantiles       quantile bundles must share the point model's
#                                exact feature space or inference throws
#   models    -> stale factors   factors are measured against a specific model
#   all       -> edges           the edge builder reads active_models
#   edges     -> grading         results are graded from the edges just built
#
# Finishes by running audit_freshness.py, which exits non-zero if anything is
# stale or inconsistent. Usage: sh scripts/refresh_pipeline.sh [--skip-ingest]

set -e

# any_td is the anytime touchdown scorer market, which is what books
# actually post for touchdowns. The split rush_td and rec_td keys returned
# zero rows across three seasons, but their models still run because the
# projections page shows a number for every market a position can produce.
MARKETS="rec_yds rush_yds pass_yds recs rush_att pass_att pass_completions pass_td rush_td rec_td any_td"
MODEL="${MODEL:-rf_posfilt_v9}"
API="${API:-http://localhost:8000/api/v1}"

# The write endpoints now require a shared secret, so an exposed API cannot have
# its Odds credits spent by a stranger. Export ADMIN_TOKEN before running this.
: "${ADMIN_TOKEN:?set ADMIN_TOKEN to the value the API is running with}"
AUTH="X-Admin-Token: ${ADMIN_TOKEN}"

if [ "$1" != "--skip-ingest" ]; then
  echo "==> 1/7 nflverse ingestion"
  docker build -q -f jobs/ingestion/Dockerfile -t propsignal-ingest . >/dev/null
  docker run --rm --network player-prop-platform_default \
    -e DATABASE_URL="postgresql://app:app@postgres:5432/app" \
    -e SEASON_START=2022 -e SEASON_END="$(date +%Y)" propsignal-ingest
fi

echo "==> 2/7 team backfill (new rows arrive with NULL team)"
docker compose exec -T postgres psql -U app -d app -f - < db/backfills/fix_team_final.sql

echo "==> 3/7 features + labels"
for m in $MARKETS; do
  printf '    %-18s ' "$m"
  curl -sf -X POST -H "$AUTH" "$API/jobs/build_features?market_code=$m&lookback=5" >/dev/null || {
    echo "FAILED"; exit 1; }
  curl -sf -X POST -H "$AUTH" "$API/jobs/attach_labels?market_code=$m&lookback=5" >/dev/null
  echo "ok"
done

echo "==> 4/7 train + evaluate"
for m in $MARKETS; do
  docker compose run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$MODEL" -e LOOKBACK=5 \
    training python train.py >/dev/null
  docker compose run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$MODEL" -e LOOKBACK=5 \
    training python eval.py >/dev/null
  echo "    $m trained"
done

echo "==> 5/7 quantile ensembles (must match the active feature space)"
for m in $MARKETS; do
  docker compose run --rm -e MARKET_CODE="$m" -e LOOKBACK=5 -e SPLIT_MODE=season \
    training python train_quantiles.py >/dev/null
  echo "    $m quantiles"
done

echo "==> 6/7 stale-role factors (written only where they validate)"
for m in $MARKETS; do
  docker compose run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$MODEL" -e LOOKBACK=5 \
    -e WRITE_FACTORS=1 training python eval_stale_role.py 2>&1 \
    | grep -E "wrote factor|no factor|cannot validate|not enough" || true
done

echo "==> 7/7 edges + grading"
docker compose run --rm training python build_projections.py
# The calibrator has to exist before edges are built: the builder corrects
# P(over) with it before choosing a side or assigning a tier.
docker compose run --rm training python fit_probability_calibrator.py ||   echo "  (no graded results yet; edges will ship uncalibrated)"
docker compose run --rm training python build_prop_edges.py
docker compose run --rm training python grade_edges.py

echo
echo "==> audit"
docker compose run --rm training python audit_freshness.py
