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
#   --closing two jobs, both scoped to the near slate, both free when no game
#             is close. Inside 90 minutes of kickoff it captures a closing
#             price, once per game, for measuring closing line value. Outside
#             that but within REFRESH_WINDOW_H hours it re-buys any game whose
#             prices have gone stale and rebuilds the board, so what the site
#             shows is a line somebody can still bet.
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
# Redo a subset instead of all eleven.
#
#   MARKETS_ONLY="pass_att pass_yds pass_completions"
#
# A full --migrate is eleven markets and each one trains a point model, an
# evaluation and five quantile models. On the production box that is well over
# ninety minutes, and most of it is wasted when a change touched one position.
# The quarterback window change touched the passing markets and nothing else, so
# rebuilding and retraining those three is the whole job.
#
# Everything else in the run still happens: the ingest refreshes the injury
# report, the calibrators refit, and the board is rebuilt at the end. Only the
# per-market feature rebuild and retrain are narrowed.
if [ -n "${MARKETS_ONLY:-}" ]; then
  MARKETS="$MARKETS_ONLY"
fi
# Quoted for SQL, so the prune below narrows with it rather than hardcoding a
# list that drifts out of step.
MARKETS_SQL=$(printf "'%s'," $MARKETS | sed "s/,$//")
# Which stack to drive, and on which network.
#
# These were hardcoded to the development stack, and the timers run this script
# with no arguments, so every scheduled run on the server drove the wrong one.
# `docker compose` with no -f reads docker-compose.yml, which is the dev file
# and is present in the clone, so it did not fail cleanly: it would have built
# and started a second Postgres with the password "app" on a published 5432 and
# a second API on 8000, beside the production stack it was supposed to be
# updating. The ingest then failed on a network that only exists in dev, which
# is the only reason this was noticed at all.
#
# Overridable so the server can point them at the production file. See
# deploy/README.md; /etc/priorline/env sets all three.
COMPOSE="${COMPOSE:-docker compose}"
INGEST_NETWORK="${INGEST_NETWORK:-player-prop-platform_default}"
INGEST_DATABASE_URL="${INGEST_DATABASE_URL:-postgresql://app:app@postgres:5432/app}"

log() { printf '\n[%s] ==> %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"; }

# Re-read the injury report and depth charts, then rebuild projections and the
# board on them.
#
# The hourly refresh used to rebuild the board on fresh prices and stale news.
# Prices are what it bought, but the reason a line moves on a Saturday is
# usually a player: Sam Darnold was ruled out, the book moved Drew Lock to a
# starter's number, and the refresh rebuilt a board that still thought Lock was
# the backup. Both reads are free, so the only cost is a few minutes, and the
# projections are rebuilt as well as the edges because the edge builder reads
# who is ruled out from the same tables.
rebuild_on_current_news() {
  log "re-read injuries and depth charts"
  docker build -q -f jobs/ingestion/Dockerfile -t priorline-ingest . >/dev/null
  docker run --rm --network "$INGEST_NETWORK" \
    -e DATABASE_URL="$INGEST_DATABASE_URL" \
    -e SEASON_START="$SEASON_START" -e SEASON_END="$SEASON_END" \
    -e ONLY=injuries,depth_charts priorline-ingest

  $COMPOSE build -q training >/dev/null
  log "rebuild projections"
  $COMPOSE run --rm training python build_projections.py
  log "rebuild the board"
  $COMPOSE run --rm training python build_prop_edges.py
}

case "$MODE" in
  --closing|--early|--board|--daily|--weekly|--migrate|--refresh) ;;
  *) echo "usage: $0 [--closing|--early|--board|--daily|--weekly|--migrate|--refresh]"
     exit 2 ;;
esac

# --refresh: republish the board on current news and current code, and nothing
# else.
#
# This exists because of a Sunday that should not have happened. A feature change
# landed on a game day and the only way to get it onto the board was --migrate,
# which re-downloads every season of every nflverse dataset, retrains eleven
# markets at five quantile models each, and refits six calibrators. Ninety
# minutes, of which the useful part was two: rebuild the features for three
# markets, reproject, republish. The slate was gone by the time it finished.
#
# So: injuries and depth charts only, no full ingest. Feature rebuild only for
# the markets named in MARKETS_ONLY, and only if it is set. No grading, no
# calibrator refits, no retrain. The existing point and quantile models are used
# as they are, which is correct whenever the change was to a feature rather than
# to a model.
#
#   MARKETS_ONLY="pass_att pass_yds" sudo systemctl start priorline@refresh
#
# Reach for --migrate only when the training set itself changed shape. For
# everything else this is the one to run.
case "$MODE" in
  --board|--refresh) FAST=1 ;;
  *) FAST=0 ;;
esac
NEED_FEATURES=0
if [ "$FAST" = "0" ]; then
  NEED_FEATURES=1
elif [ "$MODE" = "--refresh" ] && [ -n "${MARKETS_ONLY:-}" ]; then
  NEED_FEATURES=1
fi

# --migrate is --weekly with the feature store cleared first.
#
# A mode rather than a separate script so it runs through the same systemd unit
# as everything else: WorkingDirectory, the EnvironmentFile holding ADMIN_TOKEN
# and the Odds key, User=priorline, the log in /var/log/priorline, and above all
# the flock that keeps it from running at the same time as the hourly closing
# capture. Both call build_prop_edges.py, which truncates prop_edges and refills
# it, and two of those at once leaves the board empty.
#
#   sudo systemctl start priorline@migrate
#
# Run once after a change to what a feature row means, then never again: once the
# definitions are stable the ordinary upsert keeps the table correct on its own.
PRUNE_FEATURES=0
if [ "$MODE" = "--migrate" ]; then
  PRUNE_FEATURES=1
  MODE="--weekly"
  log "mode --migrate: --weekly, with the feature store pruned first"
elif [ "$MODE" = "--refresh" ] && [ -n "${MARKETS_ONLY:-}" ]; then
  # A refresh that rebuilds features prunes them first, for the same reason a
  # migration does: build_features upserts, so a change that stops emitting a row
  # leaves the old one behind holding values computed under the previous rule.
  # The quarterback snap floor does exactly that, and a stale row for a man with
  # one cameo is the bug it was written to remove.
  #
  # Safe because it is scoped to MARKETS_ONLY and because feature rows are
  # derived: the rebuild below reconstructs every one of them from box scores and
  # re-attaches the labels.
  PRUNE_FEATURES=1
fi

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
# So this asks the schedule what is imminent. A 90 minute window against an
# hourly run gives each game exactly one capture, as near kickoff as the cadence
# allows, and no game two captures: the slates are hours apart and a game that
# has already started is no longer in the window.
#
# That capture is for the record, not for the board. Buying once at 90 minutes
# is the right way to measure closing line value and a bad way to run a site:
# --daily buys at 08:00 and nothing else buys until 19:05, so on a Thursday the
# board sat all day on eleven hour old prices. Cook was posted at 16.5 carries
# in the morning and 17.5 by the evening, and the board still showed a signal
# against 16.5, which is a signal against a line nobody could bet. So the
# refresh tier below keeps the near slate current, and the capture stays exactly
# as it was.
if [ "$MODE" = "--closing" ]; then
  : "${ADMIN_TOKEN:?set ADMIN_TOKEN to the value the API is running with}"
  WINDOW_MIN="${CLOSING_WINDOW_MIN:-90}"

  # ---------------------------------------------------------------- refresh
  # Re-buy prices for games near kickoff, so the board tracks the book.
  #
  # Scoped by the same two questions as everything else here: how close is the
  # game, and how old is what we already hold. Inside REFRESH_WINDOW_H hours of
  # kickoff, re-buy anything whose last real price is older than
  # REFRESH_EVERY_H hours.
  #
  # Cost, per game, is nine credits a refresh. The defaults give a single
  # Thursday game two refreshes plus its capture, so about 27 credits. A
  # thirteen game Sunday morning is the expensive case, which is what
  # REFRESH_MAX_GAMES is for: it caps one run, soonest kickoff first, so a big
  # slate is refreshed nearest-first rather than all at once.
  #
  # Anytime touchdown does not count as a real price. A game days out comes
  # back with that market alone, and this site does not publish it, so treating
  # it as priced would hold the board on morning lines all week.
  REFRESH_H="${REFRESH_WINDOW_H:-8}"
  REFRESH_EVERY_H="${REFRESH_EVERY_H:-3}"
  REFRESH_MAX_GAMES="${REFRESH_MAX_GAMES:-6}"

  stale=$($COMPOSE exec -T postgres psql -U app -d app -tA -c \
    "SELECT count(*) FROM odds_events e
      WHERE e.commence_time >= NOW() + make_interval(mins => $WINDOW_MIN)
        AND e.commence_time < NOW() + make_interval(hours => $REFRESH_H)
        AND NOT EXISTS (
          SELECT 1 FROM odds_snapshots s
           WHERE s.provider_event_id = e.provider_event_id
             AND s.market_key <> 'player_anytime_td'
             AND s.observed_at > NOW() - make_interval(hours => $REFRESH_EVERY_H));")
  stale=$(printf '%s' "$stale" | tr -d '[:space:]')

  if [ "${stale:-0}" -gt 0 ]; then
    if [ "$stale" -gt "$REFRESH_MAX_GAMES" ]; then
      log "refresh: $stale game(s) hold stale prices, capping at REFRESH_MAX_GAMES=$REFRESH_MAX_GAMES"
      stale="$REFRESH_MAX_GAMES"
    fi
    log "refresh: $stale game(s) within ${REFRESH_H}h, about $((stale * 9)) credits"

    # `limit` takes the soonest games in the window, which is the right
    # priority but is not exactly the set counted above: a game refreshed an
    # hour ago can sit ahead of one that needs it. Soonest-first is what a
    # board wants either way, and --early already works this way.
    props=$(curl -sf -X POST -H "$AUTH" \
      "$API/odds/sync/player_props?hours_ahead=$REFRESH_H&limit=$stale") || {
      echo "FAILED: refresh props sync"; exit 1; }
    echo "    $props"

    # Archived as 'refresh': a real price before kickoff, so eval_clv may take
    # it as the close if no capture follows, but distinct from the 90 minute
    # capture so the guard below can still tell whether that capture happened.
    $COMPOSE exec -T postgres psql -U app -d app -q <<'SQL'
INSERT INTO odds_snapshots
  (provider_event_id, sport_key, commence_time, home_team, away_team,
   bookmaker_key, bookmaker_title, market_key, player_name, outcome_name,
   line, price_american, last_update, observed_at, source)
SELECT p.provider_event_id, p.sport_key, e.commence_time, e.home_team, e.away_team,
       p.bookmaker_key, p.bookmaker_title, p.market_key, p.player_name,
       p.outcome_name, p.line, p.price_american, p.last_update,
       date_trunc('hour', NOW()), 'refresh'
FROM odds_player_props p
LEFT JOIN odds_events e ON e.provider_event_id = p.provider_event_id
WHERE e.commence_time IS NOT NULL
  AND e.commence_time >= NOW()
ON CONFLICT (provider_event_id, bookmaker_key, market_key,
             player_name, outcome_name, observed_at) DO NOTHING;
SQL

    rebuild_on_current_news
  fi

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
  #
  # Restricted to 'live' rows, or the refresh tier above would cancel the
  # capture it is standing in front of: a refresh at 18:05 is one hour old at
  # 19:05, the guard would read that as captured, and the game would kick off
  # with no closing price at all. Only a capture counts as a capture.
  imminent=$($COMPOSE exec -T postgres psql -U app -d app -tA -c     "SELECT count(*) FROM odds_events e
      WHERE e.commence_time >= NOW()
        AND e.commence_time < NOW() + make_interval(mins => $WINDOW_MIN)
        AND NOT EXISTS (
          SELECT 1 FROM odds_snapshots s
           WHERE s.provider_event_id = e.provider_event_id
             AND s.source = 'live'
             AND s.observed_at > NOW() - interval '2 hours');")
  imminent=$(printf '%s' "$imminent" | tr -d '[:space:]')

  if [ "${imminent:-0}" -eq 0 ]; then
    log "closing: nothing new within ${WINDOW_MIN}m, nothing to buy"
    # Still re-read the news before leaving.
    #
    # This used to exit here, which meant the injury report was only re-read on
    # the hours that happened to buy prices. On a Sunday morning that is most of
    # them: a quarterback cleared on the 11am report would not reach the board
    # until an hour with a game inside the buy window, and a board built on
    # Saturday's report says a starter is out when he is playing at one.
    #
    # Reading injuries and depth charts costs nothing. Only the odds sync spends
    # credits, and it is above this line, so doing this unconditionally buys
    # freshness for the price of a few minutes of CPU.
    rebuild_on_current_news
    log "done"
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

  rebuild_on_current_news
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
             AND s.observed_at > NOW() - make_interval(hours => ${EARLY_RECHECK_H:-20})
             -- A touchdown-only response does not count as bought.
             --
             -- Sportsbooks post the anytime touchdown market days before they
             -- post receptions or yardage. Sunday's thirteen games, fetched on
             -- the Tuesday, came back with that one market and nothing else,
             -- and this site does not publish it. Treating those games as
             -- priced would leave them alone until the window expired and the
             -- board would stay empty through the week for no reason.
             AND s.market_key <> 'player_anytime_td');")
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
# A failed odds sync must not take the rest of the run with it.
#
# This block used to `exit 1` on the first failed call, and everything that
# matters comes after it: the nflverse ingest, the feature rebuild, grading,
# the calibrator refits, the projections. The API was unreachable from the host
# for two days after deploy, so `--daily` died on its first line both mornings
# and none of that ran. Week 1 was played, and the models never saw it, while
# the board kept rebuilding and the site looked fine.
#
# So odds failures are recorded and the run continues on the free work, which
# needs no sportsbook and no credits. The exit code is still non-zero at the
# end, so a scheduler alerts either way, but a dead card or a rate limit now
# costs prices rather than a week of training data.
ODDS_FAILED=0

if [ "${ODDS:-0}" = "1" ]; then
  log "odds: events"
  curl -sf -X POST -H "$AUTH" "$API/odds/sync/events" >/dev/null || {
    echo "FAILED: events sync"; ODDS_FAILED=1; }
fi

if [ "${ODDS:-0}" = "1" ] && [ "$ODDS_FAILED" = "0" ]; then

  # Three days, not eight.
  #
  # Billing is per event per market, and a sportsbook does not post the markets
  # this site uses until the game is close. Bought on the Monday, Sunday's
  # twelve games returned 243 rows of anytime touchdown and not one reception or
  # yardage line: one market out of the nine we paid to ask for. The daily job
  # then bought the same twelve games again the next morning, and the next.
  #
  # Inside three days the full board comes back. The Thursday game, two days
  # out, returned all nine markets. So the window is the point at which the data
  # exists rather than the furthest ahead the schedule can see, and --early
  # handles anything that needs to be live sooner.
  DAYS="${ODDS_DAYS_AHEAD:-3}"
  log "odds: player props for the next ${DAYS} day(s)"
  props=$(curl -sf -X POST -H "$AUTH" "$API/odds/sync/player_props?days_ahead=$DAYS") || {
    echo "FAILED: props sync"; ODDS_FAILED=1; }
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
elif [ "$ODDS_FAILED" = "1" ]; then
  log "odds: events sync failed, so prices are stale; continuing on the free work"
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
if [ "$FAST" = "1" ]; then
  ONLY_ARG="-e ONLY=injuries,depth_charts"
  log "nflverse ingest: injuries and depth charts only"
else
  ONLY_ARG=""
  log "nflverse ingest, seasons $SEASON_START-$SEASON_END"
fi

docker build -q -f jobs/ingestion/Dockerfile -t priorline-ingest . >/dev/null

# ONLY_ARG is deliberately unquoted: it is either empty or a flag and a value.
# shellcheck disable=SC2086
docker run --rm --network "$INGEST_NETWORK" \
  -e DATABASE_URL="$INGEST_DATABASE_URL" \
  -e SEASON_START="$SEASON_START" -e SEASON_END="$SEASON_END" \
  $ONLY_ARG priorline-ingest

if [ "$NEED_FEATURES" = "1" ]; then
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

  # One-time prune, when the definition of a feature row has changed.
  #
  # `build_features` upserts and never deletes, which is right on an ordinary run
  # and wrong the first time after the window definition moves. The season-only
  # window changed which rows exist as well as what they hold: Week 1, a player's
  # first game of a season and anyone with no game this season stopped
  # qualifying, while Weeks 2 to 5 started. The rows that stopped qualifying
  # would survive this rebuild carrying values computed under the old
  # definition, and training would read them beside the new ones. On the
  # development copy that was 2,400 rows per market out of 21,000: enough to make
  # every before-and-after number meaningless and not enough to look wrong.
  #
  # Derived data, so this is safe without a backup. Every row is rebuilt from box
  # scores in the loop below and its label re-attached from
  # player_game_stats_app. Only the seven point markets: the touchdown markets
  # are trained by a different script off a different feature path.
  if [ "$PRUNE_FEATURES" = "1" ]; then
    # Rebuild the API first, because the feature builder is in it.
    #
    # Everything else here drives the training image and this script only
    # rebuilds that one, so a migration run against a stale API container would
    # delete every feature row and rebuild it with the very code the migration
    # exists to replace: a silent, total no-op that looks like a success and
    # leaves the models retrained on the old definition. The check after the
    # rebuild loop is the belt to this braces.
    log "rebuild the API image, which is where the feature builder lives"
    $COMPOSE up -d --build api
    # /health sits at the application root, not under the versioned prefix, so
    # it is reached by stripping /api/v1 rather than appending to it. Checking
    # "$API/health" polls a 404 for two minutes and then declares a healthy API
    # dead, which is exactly what it did on the first real run of this.
    HEALTH="${API%/api/v1}/health"
    log "wait for the API at $HEALTH"
    i=0
    until curl -sf "$HEALTH" >/dev/null 2>&1; do
      i=$((i + 1))
      [ "$i" -gt 60 ] && {
        echo "FAILED: no 2xx from $HEALTH after 120s"
        echo "  last response:"
        curl -s -o /dev/null -w "    HTTP %{http_code} in %{time_total}s\n" \
          "$HEALTH" || echo "    (no response at all)"
        exit 1
      }
      sleep 2
    done

    log "prune feature rows written under the previous window definition"
    $COMPOSE exec -T postgres psql -U app -d app -c \
      "DELETE FROM player_market_features
        WHERE lookback = 5
          AND market_id IN (SELECT id FROM prop_markets
                             WHERE code IN ($MARKETS_SQL));"
  fi

  log "rebuild features"
  for m in $MARKETS; do
    curl -sf -X POST -H "$AUTH" "$API/jobs/build_features?market_code=$m&lookback=5" >/dev/null || {
      echo "FAILED: build_features $m"; exit 1; }
    curl -sf -X POST -H "$AUTH" "$API/jobs/attach_labels?market_code=$m&lookback=5" >/dev/null
    printf '    %s\n' "$m"
  done

  # Did the rebuild actually use the new code?
  #
  # y_blend is written by the season-only window and by nothing else, so its
  # absence means the rows were just rebuilt by an API still running the old
  # build. That is worth failing on rather than reporting: the retrain below
  # would fit every market on the definition this run was supposed to remove,
  # and the only visible symptom would be a board that did not change.
  if [ "$PRUNE_FEATURES" = "1" ]; then
    blended=$($COMPOSE exec -T postgres psql -U app -d app -tA -c \
      "SELECT count(*) FROM player_market_features
        WHERE lookback = 5 AND extra_features ? 'y_blend';")
    blended=$(printf '%s' "$blended" | tr -d '[:space:]')
    if [ "${blended:-0}" -lt 1000 ]; then
      echo "FAILED: only ${blended:-0} feature rows carry y_blend, so the"
      echo "rebuild ran against an API without the season-only window. Deploy"
      echo "the current code first:"
      echo "  cd /opt/priorline"
      echo "  git pull"
      echo "  set -a; . /etc/priorline/env; set +a"
      echo "  docker compose -f deploy/docker-compose.prod.yml up -d --build"
      exit 1
    fi
    log "$blended feature rows carry y_blend, so the new window is in force"
  fi

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
    # Say which market is starting, before it starts.
    #
    # Every step here wrote to /dev/null and the only output was one line after a
    # market finished, so a ninety minute retrain looked identical to a hung one:
    # container-creation lines and nothing else. Working that out from the outside
    # cost most of a game day. A line per step per market is a few dozen lines a
    # week and it is the difference between waiting and guessing.
    # Output goes to a file and the interesting lines are grepped out of it
    # afterwards, rather than piping the command into grep. A pipeline takes the
    # exit status of its last stage, so `python train.py | grep | sed` reports
    # sed's success and a failed retrain would sail past `set -e` unnoticed. The
    # whole point of this block is to see what is happening; hiding failures to
    # do it would be a poor trade.
    tmp="/tmp/priorline_step.$$"
    run_step() {
      label=$1; shift
      printf '    %s  %s: %s\n' "$(date '+%H:%M:%S')" "$m" "$label"
      if ! "$@" >"$tmp" 2>&1; then
        echo "FAILED: $label for $m"
        tail -25 "$tmp" | sed 's/^/        /'
        rm -f "$tmp"
        exit 1
      fi
      grep -E 'MAE|R2|pinball|refit on all|accepted' "$tmp" \
        | sed 's/^/        /' || true
    }
    started=$(date '+%H:%M:%S')
    run_step "training point model" \
      $COMPOSE run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$active" \
      -e LOOKBACK=5 training python train.py
    run_step "evaluating" \
      $COMPOSE run --rm -e MARKET_CODE="$m" -e MODEL_NAME="$active" \
      -e LOOKBACK=5 training python eval.py
    # The slow one: five quantile models per market, and the reason a full
    # retrain runs into the hours.
    run_step "fitting quantiles (the slow step)" \
      $COMPOSE run --rm -e MARKET_CODE="$m" -e LOOKBACK=5 -e SPLIT_MODE=season \
      training python train_quantiles.py
    rm -f "$tmp"
    printf '    %s (%s) %s -> %s\n' "$m" "$active" "$started" "$(date '+%H:%M:%S')"
  done
fi

# ------------------------------------------------------------------ serve
if [ "$FAST" = "0" ]; then
  log "grade whatever has been played"
  $COMPOSE run --rm training python grade_edges.py

  # Refit before building edges, never after. The edge builder reads this to
  # correct P(over) before it picks a side, so building first would publish a
  # slate against the previous calibration.
  log "refit probability calibrator"
  $COMPOSE run --rm training python fit_probability_calibrator.py

  # Correct the model's confidence to the rate that confidence actually hits,
  # per side. After the calibrator, because it corrects the calibrated
  # probability. It changes the published probability and EV, never which picks
  # are made. See fit_display_probability.py.
  log "refit display probability"
  $COMPOSE run --rm training python fit_display_probability.py

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

  # And the median anchor, which reads the median off the point projection in
  # the bands where that was shown to help. The quantile models are trees and
  # cannot predict past their training leaves, so on a player at the edge of
  # the data their median lags a linear point model badly. Fitted per market
  # and per projection band, accepted only where held-out coverage or side
  # accuracy improves and neither gets worse.
  log "refit median anchor"
  $COMPOSE run --rm training python fit_median_anchor.py

  # Who inherits a ruled-out teammate's volume. Fitted on past absences and
  # scored on the last complete season; only pools that improve the projection
  # in yards, not just in shares, are written as accepted. Before the
  # projections, which read it. See vacated_volume.py.
  log "refit vacated volume"
  $COMPOSE run --rm training python fit_vacated_volume.py
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

# Whether the numbers are believable, which is not the same question as whether
# they are current, and had been failing while the freshness audit passed. See
# audit_plausibility.py: a starting quarterback projected for 20.8 attempts
# broke nothing a metric could see.
#
# Reports without failing for now. The ranges are new and want a few weeks of
# watching before a violation is allowed to stop a board from publishing, and a
# gate nobody trusts yet gets switched off in a hurry at the worst moment. Set
# FAIL_ON_PLAUSIBILITY=1 in /etc/priorline/env once the violations it prints are
# ones worth stopping for.
log "plausibility audit"
FAIL_ON_PLAUSIBILITY="${FAIL_ON_PLAUSIBILITY:-0}" \
  $COMPOSE run --rm -e FAIL_ON_PLAUSIBILITY="${FAIL_ON_PLAUSIBILITY:-0}" \
  training python audit_plausibility.py

# The odds failure is reported here rather than where it happened, so the run
# still does its free work first and the scheduler still hears about it.
if [ "$ODDS_FAILED" = "1" ]; then
  log "done, but the odds sync failed: the board is running on prices nothing refreshed"
  exit 1
fi

log "done"
