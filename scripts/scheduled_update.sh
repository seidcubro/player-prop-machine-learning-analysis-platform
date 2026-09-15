#!/bin/sh
# The scheduled refresh. One script, three cadences, safe to run unattended.
#
#   sh scripts/scheduled_update.sh --closing   every hour, all day
#   sh scripts/scheduled_update.sh --board     every hour or two on a game day
#   sh scripts/scheduled_update.sh --daily     once a morning
#   sh scripts/scheduled_update.sh --weekly    Tuesday, after a week is graded
#
# Each mode is a superset of the one above it.
#
#   --closing only does anything when a game kicks off inside the next 75
#             minutes, so it can run on the hour all day and costs nothing on
#             the 20 or so hours when no slate is near. When one is, it buys
#             prices for that slate alone.
#   --board   free unless ODDS=1. Pulls injury reports, rebuilds projections and
#             the board, audits. Injury reports are the thing that actually
#             changes hour to hour: they land Wednesday through Friday and a
#             player ruled out has to come off the board before someone bets
#             him.
#   --daily   the above plus the full nflverse ingest and a feature rebuild, so
#             results from games just played reach the models, plus grading.
#   --weekly  the above plus retraining every market. This is slow, tens of
#             minutes, and belongs on a day nobody is looking at the board.
#
# Odds cost real money and are therefore opt-in, per run:
#
#   ODDS=1 sh scripts/scheduled_update.sh --daily
#
# A props sync is one credit per event per market. Sixteen games across nine
# markets is about 145 credits, so hourly is roughly 3,500 a day and daily is
# about 145. Set ODDS_MIN_CREDITS so an unattended run stops before it drains
# the account rather than after.
#
# Exits non-zero if the freshness audit fails, so a scheduler can alert on it.

set -e

MODE="${1:---board}"
API="${API:-http://localhost:8000/api/v1}"
AUTH="X-Admin-Token: ${ADMIN_TOKEN}"

# Always the full range. Every nflverse loader truncates before writing, so a
# narrow range deletes the seasons outside it. The ingest refuses to do that now
# but there is no reason to make it argue.
SEASON_START="${SEASON_START:-2022}"
SEASON_END="${SEASON_END:-$(date +%Y)}"

MARKETS="rec_yds rush_yds pass_yds recs rush_att pass_att pass_completions pass_td rush_td rec_td any_td"
COMPOSE="docker compose"

log() { printf '\n[%s] ==> %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"; }

case "$MODE" in
  --closing|--early|--board|--daily|--weekly) ;;
  *) echo "usage: $0 [--closing|--early|--board|--daily|--weekly]"; exit 2 ;;
esac

log "mode $MODE"

# The token is only needed by the runs that call a write endpoint, which is the
# odds sync and the feature rebuild. A --board run touches neither, so it can be
# scheduled on a machine that never holds the secret.
if [ "${ODDS:-0}" = "1" ] || [ "$MODE" != "--board" ]; then
  : "${ADMIN_TOKEN:?set ADMIN_TOKEN to the value the API is running with}"
fi

# ------------------------------------------------------------------ closing
# Buy prices for the slate about to start, and nothing else.
#
# Closing line value is the fastest honest evidence that a pick had an edge, and
# a price not captured before kickoff cannot be bought back at anything like the
# same cost. But billing is per event per market, so paying for all sixteen
# games every hour to catch the one about to start is most of a month's credits
# for data that has not moved.
#
# So this asks the schedule what is imminent. A 75 minute window against an
# hourly run gives each game exactly one capture, as near kickoff as the cadence
# allows, and no game two captures: the slates are hours apart and a game that
# has already started is no longer in the window.
if [ "$MODE" = "--closing" ]; then
  : "${ADMIN_TOKEN:?set ADMIN_TOKEN to the value the API is running with}"
  WINDOW_MIN="${CLOSING_WINDOW_MIN:-90}"

  # Count only the games not already captured.
  #
  # A fixed window on a fixed cadence either buys a slate twice or catches it
  # minutes before kickoff, depending on where the kickoff falls between two
  # runs. A 90 minute window on an hourly run covers a 20:20 kickoff comfortably
  # at 19:05, and would also cover it again at 20:05; asking which imminent
  # games have no recent snapshot makes the second run free instead.
  #
  # Two hours because that is longer than the window, so a game seen once in
  # this cycle stays seen for the rest of it.
  imminent=$($COMPOSE exec -T postgres psql -U app -d app -tA -c     "SELECT count(*) FROM odds_events e
      WHERE e.commence_time >= NOW()
        AND e.commence_time < NOW() + make_interval(mins => $WINDOW_MIN)
        AND NOT EXISTS (
          SELECT 1 FROM odds_snapshots s
           WHERE s.provider_event_id = e.provider_event_id
             AND s.observed_at > NOW() - interval '2 hours');")
  imminent=$(printf '%s' "$imminent" | tr -d '[:space:]')

  if [ "${imminent:-0}" -eq 0 ]; then
    log "closing: nothing new within ${WINDOW_MIN}m, nothing to buy"
    exit 0
  fi

  log "closing: $imminent game(s) within ${WINDOW_MIN}m, about $((imminent * 9)) credits"
  props=$(curl -sf -X POST -H "$AUTH"     "$API/odds/sync/player_props?hours_ahead=$(awk "BEGIN{print $WINDOW_MIN/60}")") || {
    echo "FAILED: closing props sync"; exit 1; }
  echo "    $props"

  $COMPOSE exec -T postgres psql -U app -d app -q <<'SQL'
INSERT INTO odds_snapshots
  (provider_event_id, sport_key, commence_time, home_team, away_team,
   bookmaker_key, bookmaker_title, market_key, player_name, outcome_name,
   line, price_american, last_update, observed_at, source)
SELECT p.provider_event_id, p.sport_key, e.commence_time, e.home_team, e.away_team,
       p.bookmaker_key, p.bookmaker_title, p.market_key, p.player_name,
       p.outcome_name, p.line, p.price_american, p.last_update,
       date_trunc('hour', NOW()),
       -- Anything already kicked off is archived as 'late'.
       --
       -- This used to be filtered to upcoming games only, so a price for a game
       -- that started since the last run was never archived at all: 300 rows
       -- across the Thursday opener and Wednesday night sat in the live table
       -- with nothing behind them, one props sync from gone. They are still
       -- worth keeping and they are not closing prices, so they are labelled
       -- rather than dropped and eval_clv can ignore them.
       CASE WHEN e.commence_time >= NOW() THEN 'live' ELSE 'late' END
FROM odds_player_props p
LEFT JOIN odds_events e ON e.provider_event_id = p.provider_event_id
WHERE e.commence_time IS NOT NULL
ON CONFLICT (provider_event_id, bookmaker_key, market_key,
             player_name, outcome_name, observed_at) DO NOTHING;
SQL

  log "rebuild the board on the fresh prices"
  $COMPOSE build -q training >/dev/null
  $COMPOSE run --rm training python build_prop_edges.py
  log "done"
  exit 0
fi

if [ "$MODE" = "--early" ]; then
  : "${ADMIN_TOKEN:?set ADMIN_TOKEN to the value the API is running with}"
  WINDOW_H="${EARLY_WINDOW_H:-72}"

  # Put a board up days before kickoff, not minutes.
  #
  # --closing exists to capture a price that is about to disappear, which is the
  # right job for measuring closing line value and the wrong one for having a
  # site. It buys inside 90 minutes of kickoff, so for most of the week there is
  # no upcoming priced game and the board is empty. Run this on a Friday and
  # Sunday's slate is live from Friday instead of from Sunday lunchtime.
  #
  # Same "only what is not already bought" guard as --closing, so running it
  # twice in a day costs nothing. A day is the right memory here: lines move
  # over a week, and re-buying a game the next morning is a deliberate refresh
  # rather than an accident.
  unpriced=$($COMPOSE exec -T postgres psql -U app -d app -tA -c \
    "SELECT count(*) FROM odds_events e
      WHERE e.commence_time >= NOW()
        AND e.commence_time < NOW() + make_interval(hours => $WINDOW_H)
        AND NOT EXISTS (
          SELECT 1 FROM odds_snapshots s
           WHERE s.provider_event_id = e.provider_event_id
             AND s.observed_at > NOW() - make_interval(hours => ${EARLY_RECHECK_H:-20}));")
  unpriced=$(printf '%s' "$unpriced" | tr -d '[:space:]')

  if [ "${unpriced:-0}" -eq 0 ]; then
    log "early: every game within ${WINDOW_H}h already has prices, nothing to buy"
    exit 0
  fi

  # A hard ceiling, because this is the one mode that can reach a whole slate.
  # Nine markets a game, so the default cap is about 135 credits.
  MAX_GAMES="${EARLY_MAX_GAMES:-15}"
  if [ "$unpriced" -gt "$MAX_GAMES" ]; then
    log "early: $unpriced games need prices, which is more than EARLY_MAX_GAMES=$MAX_GAMES; buying the first $MAX_GAMES"
    unpriced="$MAX_GAMES"
  fi

  log "early: $unpriced game(s) within ${WINDOW_H}h, about $((unpriced * 9)) credits"
  props=$(curl -sf -X POST -H "$AUTH" \
    "$API/odds/sync/player_props?hours_ahead=$WINDOW_H&limit=$unpriced") || {
    echo "FAILED: early props sync"; exit 1; }
  echo "    $props"

  # Record what was just bought, or the guard above can never see it.
  #
  # That guard asks odds_snapshots which games already have prices. Without this
  # block the mode never writes there, so every run looks at a slate it bought
  # an hour ago, decides it is unpriced, and buys it again. It cost 9 credits to
  # find that out.
  $COMPOSE exec -T postgres psql -U app -d app -q <<'SQL'
INSERT INTO odds_snapshots
  (provider_event_id, sport_key, commence_time, home_team, away_team,
   bookmaker_key, bookmaker_title, market_key, player_name, outcome_name,
   line, price_american, last_update, observed_at, source)
SELECT p.provider_event_id, p.sport_key, e.commence_time, e.home_team, e.away_team,
       p.bookmaker_key, p.bookmaker_title, p.market_key, p.player_name,
       p.outcome_name, p.line, p.price_american, p.last_update,
       date_trunc('hour', NOW()),
       -- 'early' rather than 'live', because these are not closing prices and
       -- eval_clv must not score them as if they were.
       'early'
FROM odds_player_props p
LEFT JOIN odds_events e ON e.provider_event_id = p.provider_event_id
WHERE e.commence_time IS NOT NULL
  AND e.commence_time >= NOW()
ON CONFLICT (provider_event_id, bookmaker_key, market_key,
             player_name, outcome_name, observed_at) DO NOTHING;
SQL

  log "rebuild the board on the new prices"
  $COMPOSE build -q training >/dev/null
  $COMPOSE run --rm training python build_prop_edges.py
  log "done"
  exit 0
fi

# ------------------------------------------------------------------ odds
if [ "${ODDS:-0}" = "1" ]; then
  log "odds: events"
  curl -sf -X POST -H "$AUTH" "$API/odds/sync/events" >/dev/null || {
    echo "FAILED: events sync"; exit 1; }

  log "odds: player props"
  props=$(curl -sf -X POST -H "$AUTH" "$API/odds/sync/player_props?days_ahead=8") || {
    echo "FAILED: props sync"; exit 1; }
  echo "    $props"

  # Append to the price history. Closing line value is measured against this and
  # a price not captured before kickoff cannot be bought back cheaply.
  log "snapshot prices into odds_snapshots"
  $COMPOSE exec -T postgres psql -U app -d app -q <<'SQL'
INSERT INTO odds_snapshots
  (provider_event_id, sport_key, commence_time, home_team, away_team,
   bookmaker_key, bookmaker_title, market_key, player_name, outcome_name,
   line, price_american, last_update, observed_at, source)
SELECT p.provider_event_id, p.sport_key, e.commence_time, e.home_team, e.away_team,
       p.bookmaker_key, p.bookmaker_title, p.market_key, p.player_name,
       p.outcome_name, p.line, p.price_american, p.last_update,
       date_trunc('hour', NOW()),
       -- Anything already kicked off is archived as 'late'.
       --
       -- This used to be filtered to upcoming games only, so a price for a game
       -- that started since the last run was never archived at all: 300 rows
       -- across the Thursday opener and Wednesday night sat in the live table
       -- with nothing behind them, one props sync from gone. They are still
       -- worth keeping and they are not closing prices, so they are labelled
       -- rather than dropped and eval_clv can ignore them.
       CASE WHEN e.commence_time >= NOW() THEN 'live' ELSE 'late' END
FROM odds_player_props p
LEFT JOIN odds_events e ON e.provider_event_id = p.provider_event_id
WHERE e.commence_time IS NOT NULL
ON CONFLICT (provider_event_id, bookmaker_key, market_key,
             player_name, outcome_name, observed_at) DO NOTHING;
SQL
else
  log "odds: skipped (set ODDS=1 to spend credits)"
fi

# The training image bakes its source in, so a run that does not rebuild it
# executes whatever the image was built from. That is how an audit fix sat in
# the working tree and the audit kept failing on the old query.
#
# Built here rather than at the top of the script. --closing runs every hour and
# exits in seconds on the twenty-odd hours a day when no game is near, and it
# was building an image it then never used on every one of those runs.
$COMPOSE build -q training >/dev/null

# ------------------------------------------------------------------ data
# The ingest runs in every mode, including --board, because injury reports are
# published through the week and a ruled-out player has to leave the board. It
# is the only free source that changes between one hour and the next.
#
# The frequent run pulls only that, though. A full ingest re-downloads every
# season of every dataset, which is minutes of transfer and buys nothing when no
# game has been played since the last run.
if [ "$MODE" = "--board" ]; then
  ONLY_ARG="-e ONLY=injuries,depth_charts"
  log "nflverse ingest: injuries and depth charts only"
else
  ONLY_ARG=""
  log "nflverse ingest, seasons $SEASON_START-$SEASON_END"
fi

docker build -q -f jobs/ingestion/Dockerfile -t priorline-ingest . >/dev/null

# ONLY_ARG is deliberately unquoted: it is either empty or a flag and a value.
# shellcheck disable=SC2086
docker run --rm --network player-prop-platform_default \
  -e DATABASE_URL="postgresql://app:app@postgres:5432/app" \
  -e SEASON_START="$SEASON_START" -e SEASON_END="$SEASON_END" \
  $ONLY_ARG priorline-ingest

if [ "$MODE" != "--board" ]; then
  # Refresh the materialized views the feature build reads, before it reads
  # them. Nothing in this repository refreshed them and they had stopped at
  # the Super Bowl; see db/views/refresh_matviews.sql for what that costs.
  # Drop live prices for games already played, now that they are archived.
  # Runs after the snapshot above, never before it, and only removes rows that
  # are provably in odds_snapshots.
  log "prune played games from the live odds table"
  $COMPOSE exec -T postgres psql -U app -d app -q -f - < db/backfills/prune_played_props.sql

  log "refresh materialized views"
  $COMPOSE exec -T postgres psql -U app -d app -q -f - < db/views/refresh_matviews.sql

  log "rebuild features"
  for m in $MARKETS; do
    curl -sf -X POST -H "$AUTH" "$API/jobs/build_features?market_code=$m&lookback=5" >/dev/null || {
      echo "FAILED: build_features $m"; exit 1; }
    curl -sf -X POST -H "$AUTH" "$API/jobs/attach_labels?market_code=$m&lookback=5" >/dev/null
    printf '    %s\n' "$m"
  done

  # After the rebuild, never before it.
  #
  # The backfill patches player_market_features, which is the table the
  # rebuild writes. Running it first repairs the previous run's rows and
  # leaves every row this run just inserted with a NULL team, which is how
  # 258 of them ended up on the board with no team attached.
  log "team backfill (new rows arrive with a NULL team)"
  $COMPOSE exec -T postgres psql -U app -d app -q -f - < db/backfills/fix_team_final.sql
fi

# ------------------------------------------------------------------ models
if [ "$MODE" = "--weekly" ]; then
  # Retrain each market as the family it already uses.
  #
  # `train.py` defaults to MODEL_NAME=rf_default and writes to active_models,
  # so a loop that does not pass a name retrains every market as a random
  # forest and repoints the platform at it. The families were chosen per
  # market on time-series folds and linear models won three of them outright.
  # Losing that to a scheduled job nobody watched would be a bad way to find
  # out.
  log "retrain every market, each as its current family"
  for m in $MARKETS; do
    active=$($COMPOSE exec -T postgres psql -U app -d app -tA -c \
      "SELECT a.model_name FROM active_models a
         JOIN prop_markets p ON p.id = a.market_id
        WHERE p.code = '$m';")
    active=$(printf '%s' "$active" | tr -d '[:space:]')
    if [ -z "$active" ]; then
      echo "    $m: no active model, skipped"
      continue
    fi
    $COMPOSE run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$active" -e LOOKBACK=5 \
      training python train.py >/dev/null
    $COMPOSE run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$active" -e LOOKBACK=5 \
      training python eval.py >/dev/null
    $COMPOSE run --rm -e MARKET_CODE="$m" -e LOOKBACK=5 -e SPLIT_MODE=season \
      training python train_quantiles.py >/dev/null
    printf '    %s (%s)\n' "$m" "$active"
  done
fi

# ------------------------------------------------------------------ serve
if [ "$MODE" != "--board" ]; then
  log "grade whatever has been played"
  $COMPOSE run --rm training python grade_edges.py

  # Refit before building edges, never after. The edge builder reads this to
  # correct P(over) before it picks a side, so building first would publish a
  # slate against the previous calibration.
  log "refit probability calibrator"
  $COMPOSE run --rm training python fit_probability_calibrator.py

  # Refit the level-dependent correction on the projection history.
  #
  # Separate from the probability calibrator above: that one corrects how often
  # a pick wins, this one corrects the number itself. It writes only the
  # markets that beat the raw prediction on a held-out season, so a market that
  # stops improving drops out on its own rather than being carried forever.
  log "refit spread calibrator"
  $COMPOSE run --rm training python fit_spread_calibrator.py

  # And the interval correction, which fixes what the published range means.
  # Fitted on priced player-games, accepted one quantile level at a time.
  log "refit interval calibrator"
  $COMPOSE run --rm training python fit_interval_calibrator.py
fi

log "project every eligible player"
$COMPOSE run --rm training python build_projections.py

log "build the board"
$COMPOSE run --rm training python build_prop_edges.py

if [ "$MODE" != "--board" ] && [ "${ODDS:-0}" = "1" ]; then
  log "closing line value"
  $COMPOSE run --rm training python eval_clv.py || \
    echo "    (no closed games to score yet)"
fi

# ------------------------------------------------------------------ gate
log "freshness audit"
$COMPOSE run --rm training python audit_freshness.py

log "done"
