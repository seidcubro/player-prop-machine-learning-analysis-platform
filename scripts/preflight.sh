#!/bin/sh
# Check the things that have actually broken, before a build runs for an hour
# and then fails, or worse, succeeds and publishes something wrong.
#
#   sh scripts/preflight.sh                                    dev stack
#   COMPOSE="docker compose -f deploy/docker-compose.prod.yml" \
#     sh scripts/preflight.sh                                  the server
#
# Every check here is one that cost a real afternoon. Nothing is included
# because it seemed prudent.
#
# Exit codes: 0 all clear, 1 at least one FAIL. WARN never fails the run, it
# is for things that are worth seeing and not worth blocking on.

set -e

COMPOSE="${COMPOSE:-docker compose}"
API="${API:-http://localhost:8000/api/v1}"
HEALTH="${API%/api/v1}/health"

fails=0
warns=0

ok()   { printf '  ok    %s\n' "$1"; }
warn() { printf '  WARN  %s\n' "$1"; warns=$((warns + 1)); }
die()  { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

psql_q() {
  $COMPOSE exec -T postgres psql -U app -d app -tA -c "$1" 2>/dev/null \
    | tr -d '\r' | tr -d '[:space:]'
}

echo
echo "== containers and connectivity =="

if $COMPOSE ps --status running 2>/dev/null | grep -q postgres; then
  ok "postgres is running"
else
  die "postgres is not running"
fi

# /health lives at the app root, not under /api/v1. A preflight that checked
# the wrong path once looped for two minutes and then declared a healthy API
# dead, which is why the path is derived rather than written twice.
if curl -sf -o /dev/null --max-time 10 "$HEALTH"; then
  ok "API answers at $HEALTH"
else
  die "API does not answer at $HEALTH"
fi

# Nineteen scripts honour DATABASE_URL and the server supplies a psycopg 3
# dialect, so the image has to carry both drivers. The weekly retrain died on
# ModuleNotFoundError partway through once, after the features were rebuilt.
if $COMPOSE run --rm -T training python -c "
import os, sqlalchemy
# Either spelling is fine; the scripts here read both. What is not fine is a
# DATABASE_URL that names a driver the image does not carry, which is how a
# weekly retrain died partway through once, after rebuilding the features.
u = os.getenv('DATABASE_URL') or (
    'postgresql+psycopg2://{}:{}@{}:{}/{}'.format(
        os.getenv('POSTGRES_USER', 'app'), os.getenv('POSTGRES_PASSWORD', 'app'),
        os.getenv('POSTGRES_HOST', 'postgres'), os.getenv('POSTGRES_PORT', '5432'),
        os.getenv('POSTGRES_DB', 'app')))
sqlalchemy.create_engine(u).connect().close()
" >/dev/null 2>&1; then
  ok "training container can open a database connection"
else
  die "training container cannot open a database connection (driver or credentials)"
fi

# And the ingest image, which is a separate image built from a separate
# Dockerfile and reads the same DATABASE_URL. It had only psycopg2 while the
# server supplies a psycopg 3 URL, so the full nflverse ingest raised
# ModuleNotFoundError on its first statement every morning for four days. The
# training image had been fixed for exactly this in September; nobody checked
# the other one.
if docker build -q -f jobs/ingestion/Dockerfile -t priorline-ingest . >/dev/null 2>&1; then
  if docker run --rm priorline-ingest python -c "
import os, sqlalchemy
u = os.getenv('DATABASE_URL') or os.getenv('INGEST_DATABASE_URL')     or 'postgresql+psycopg://a:b@h:5432/d'
sqlalchemy.create_engine(u)
" >/dev/null 2>&1; then
    ok "ingest image can build an engine for its DATABASE_URL dialect"
  else
    die "the ingest image cannot load the driver its DATABASE_URL asks for;
        the nflverse ingest will die before it writes anything"
  fi
else
  warn "could not build the ingest image to check its driver"
fi

echo
echo "== scheduled runs =="

# Whether anything has been failing quietly.
#
# The timers fire. That is not the same as the work happening. --daily failed
# every morning for four days while the board kept publishing, because the only
# thing that would have said so is alert.sh, and alert.sh exits silently when
# no channel is configured. A machine with no alerting configured should not
# fail its own alerting, but it should not be the only thing standing between a
# broken pipeline and nobody knowing either.
if command -v systemctl >/dev/null 2>&1; then
  failed=""
  for m in closing early board daily weekly; do
    if systemctl is-failed --quiet "priorline@${m}.service" 2>/dev/null; then
      failed="$failed $m"
    fi
  done
  if [ -n "$failed" ]; then
    die "these scheduled runs are in a failed state:$failed
        journalctl -u priorline@<mode> -n 40 --no-pager"
  else
    ok "no scheduled run is in a failed state"
  fi
  if [ -n "${ALERT_NTFY_TOPIC:-}${ALERT_WEBHOOK:-}" ]; then
    ok "failure alerting is configured"
  else
    die "no ALERT_NTFY_TOPIC or ALERT_WEBHOOK in the environment, so a failed
        run tells nobody. This is how four days of dead ingests went unnoticed"
  fi
else
  warn "systemctl not available here, skipping the scheduled-run checks"
fi

echo
echo "== schema =="

applied=$(psql_q "SELECT count(*) FROM schema_migrations;")
onchain=$(ls db/migrations/*.sql 2>/dev/null | wc -l | tr -d '[:space:]')
if [ "${applied:-0}" -ge "${onchain:-0}" ] && [ "${onchain:-0}" -gt 0 ]; then
  ok "$applied of $onchain migrations applied"
else
  die "$applied of $onchain migrations applied; run deploy/apply_migrations.sh"
fi

for col in gap_z negative_ev; do
  has=$(psql_q "SELECT count(*) FROM information_schema.columns
                 WHERE table_name='prop_edges' AND column_name='$col';")
  [ "${has:-0}" -ge 1 ] && ok "prop_edges.$col exists" \
                        || die "prop_edges.$col missing"
done

echo
echo "== data freshness =="

# Depth charts went two seasons stale once without anything noticing, because
# nflverse changed the file format in 2025 and the loader silently produced
# nothing. Everything about who starts comes from this table.
dcs=$(psql_q "SELECT max(season) FROM depth_charts;")
cur=$(psql_q "SELECT max(season) FROM nfl_games WHERE game_date <= CURRENT_DATE;")
if [ "${dcs:-0}" = "${cur:-1}" ]; then
  ok "depth charts cover season $dcs"
else
  die "depth charts stop at season ${dcs:-none} but games run to ${cur:-none};
        every depth_rank and every backup-QB decision is from the wrong year"
fi

dcw=$(psql_q "SELECT max(week) FROM depth_charts WHERE season=${cur:-0};")
gw=$(psql_q "SELECT max(week) FROM nfl_games
              WHERE season=${cur:-0} AND game_date <= CURRENT_DATE;")
if [ "${dcw:-0}" -ge "${gw:-0}" ]; then
  ok "depth charts current through week $dcw"
else
  warn "depth charts stop at week ${dcw:-none}, games through ${gw:-none}"
fi

stale=$(psql_q "SELECT count(*) FROM player_game_stats s
                 JOIN nfl_games g ON g.game_id = s.game_id
                WHERE g.season = ${cur:-0};")
[ "${stale:-0}" -gt 100 ] && ok "$stale player-game rows for season $cur" \
                          || die "only ${stale:-0} player-game rows for season $cur"

echo
echo "== models and features =="

# A market whose features were built under one window and whose model expects
# another gets served nothing. Both serving paths read the window back from
# active_models, so this is the check that the two agree.
bad=$(psql_q "
  SELECT count(*) FROM active_models am
   WHERE NOT EXISTS (
     SELECT 1 FROM player_market_features f
      WHERE f.market_id = am.market_id AND f.lookback = am.lookback
      LIMIT 1);")
[ "${bad:-1}" -eq 0 ] && ok "every active model has features at its own lookback" \
  || die "${bad} active model(s) expect a lookback with no feature rows;
        rebuild features before the board build"

lbs=$(psql_q "SELECT string_agg(DISTINCT lookback::text, ',') FROM active_models;")
ok "active model window(s): ${lbs:-none}"

missing=$($COMPOSE run --rm -T training python -c "
import os, pathlib, json
from sqlalchemy import create_engine, text
e = create_engine(os.getenv('DATABASE_URL') or 'postgresql+psycopg2://{}:{}@{}:{}/{}'.format(os.getenv('POSTGRES_USER','app'), os.getenv('POSTGRES_PASSWORD','app'), os.getenv('POSTGRES_HOST','postgres'), os.getenv('POSTGRES_PORT','5432'), os.getenv('POSTGRES_DB','app')), future=True)
art = pathlib.Path(os.getenv('ARTIFACT_DIR', '/artifacts'))
bad = []
with e.begin() as c:
    rows = c.execute(text('''
        SELECT pm.code, am.model_name, am.lookback
        FROM active_models am JOIN prop_markets pm ON pm.id = am.market_id
    ''')).fetchall()
for code, name, lb in rows:
    if not (art / f'{name}_{code}_lb{lb}.joblib').exists():
        bad.append(f'{code}: point model {name} missing')
    q = art / f'quant_v1_{code}_lb{lb}.joblib'
    if not q.exists():
        bad.append(f'{code}: quantile bundle missing')
print('|'.join(bad) if bad else 'CLEAN')
" 2>/dev/null | tr -d '\r')
if [ -z "$missing" ]; then
  die "could not check artifacts (the check itself failed)"
elif [ "$missing" = "CLEAN" ]; then
  ok "every active model has its point artifact and quantile bundle on disk"
else
  echo "$missing" | tr '|' '\n' | while read -r m; do
    [ -n "$m" ] && die "$m"
  done
  fails=$((fails + 1))
fi

# A quantile bundle fitted before a feature existed rejects the serving matrix
# outright, and the only symptom is build_projections dying partway through.
skew=$($COMPOSE run --rm -T training python -c "
import os, pathlib, json, joblib
from sqlalchemy import create_engine, text
e = create_engine(os.getenv('DATABASE_URL') or 'postgresql+psycopg2://{}:{}@{}:{}/{}'.format(os.getenv('POSTGRES_USER','app'), os.getenv('POSTGRES_PASSWORD','app'), os.getenv('POSTGRES_HOST','postgres'), os.getenv('POSTGRES_PORT','5432'), os.getenv('POSTGRES_DB','app')), future=True)
art = pathlib.Path(os.getenv('ARTIFACT_DIR', '/artifacts'))
bad = []
with e.begin() as c:
    rows = c.execute(text('''
        SELECT pm.code, am.model_name, am.lookback
        FROM active_models am JOIN prop_markets pm ON pm.id = am.market_id
    ''')).fetchall()
for code, name, lb in rows:
    meta = art / f'{name}_{code}_lb{lb}.json'
    q = art / f'quant_v1_{code}_lb{lb}.joblib'
    if not (meta.exists() and q.exists()):
        continue
    want = set(json.loads(meta.read_text())['feature_cols'])
    try:
        b = joblib.load(q)
    except Exception as ex:
        bad.append(f'{code}: quantile bundle unreadable ({type(ex).__name__})')
        continue
    # The bundle stores no feature list, so ask the fitted estimators what
    # they were shown. sklearn raises on a mismatch at predict time and the
    # only symptom is build_projections dying halfway through a build.
    got = set()
    for m in (b.get('models') or {}).values():
        names = getattr(m, 'feature_names_in_', None)
        if names is not None:
            got |= set(map(str, names))
    if got and got != want:
        d = sorted(want ^ got)[:4]
        bad.append(f'{code}: ladder fitted on a different feature set '
                   f'({len(want ^ got)} differ, e.g. {d}); retrain quantiles')
print('|'.join(bad) if bad else 'CLEAN')
" 2>/dev/null | tr -d '\r')
if [ -z "$skew" ]; then
  die "could not check the ladder feature space (the check itself failed)"
elif [ "$skew" = "CLEAN" ]; then
  ok "quantile bundles share the point models' feature space"
else
  echo "$skew" | tr '|' '\n' | while read -r m; do
    [ -n "$m" ] && die "$m"
  done
  fails=$((fails + 1))
fi

echo
echo "== selection =="

# The board divided the gap by the quantile ladder's interquartile range while
# every threshold in gap_tier was measured against the spread of the player's
# recent games. The ladder's spread runs 1.4 to 1.9 times wider, so the bar was
# far stricter than the measured one and the board published a third of the
# picks it should have. This is the cheap guard.
# This asks the image, not the working tree, because the image is what runs.
# An absent answer means the container predates the code it is being checked
# against, which is worth knowing on its own before an hour-long build.
gap=$($COMPOSE run --rm -T training python -c "
import gap_tier
print(getattr(gap_tier, 'GAP_SCALE', 'ABSENT'))
" 2>/dev/null | tr -d '[:space:]')
case "$gap" in
  window)
    ok "the gap is scaled by the window spread, as the thresholds were" ;;
  ABSENT|"")
    die "the training image predates gap_tier.GAP_SCALE; rebuild it with
        docker compose build training before anything else runs" ;;
  *)
    warn "the gap is scaled by '$gap'; the thresholds were measured on the window spread" ;;
esac

echo
echo "== odds =="

fresh=$(psql_q "SELECT count(*) FROM odds_snapshots
                 WHERE commence_time > NOW()
                   AND commence_time < NOW() + INTERVAL '8 days';")
[ "${fresh:-0}" -gt 100 ] && ok "$fresh priced outcomes for upcoming games" \
  || warn "only ${fresh:-0} priced outcomes for upcoming games; the board will be thin"

# A game nobody bought prices for is a game with no picks in it, and that is
# the cheapest money this project loses. Week 2 of 2026 had twelve games on the
# Sunday and 486 prices across them, about forty a game, against eleven
# thousand the week before and thirty-one thousand the week after: two or three
# markets instead of nine. It cost the whole week, 23 published picks where the
# neighbouring weeks had 222 and 190.
#
# A fully priced game carries several hundred outcomes. Under a hundred means
# the sync bought one or two markets and stopped, which is what a credit floor
# or a half-finished run looks like from here.
thin=$(psql_q "
  SELECT count(*) FROM (
    SELECT e.provider_event_id
    FROM odds_events e JOIN odds_player_props p
      ON p.provider_event_id = e.provider_event_id
    WHERE e.commence_time > NOW()
      AND e.commence_time < NOW() + INTERVAL '8 days'
    GROUP BY 1 HAVING count(*) < 100) t;")
games=$(psql_q "
  SELECT count(*) FROM odds_events
   WHERE commence_time > NOW() AND commence_time < NOW() + INTERVAL '8 days';")
if [ "${thin:-0}" -eq 0 ]; then
  ok "all ${games:-0} upcoming game(s) priced across the full market set"
else
  warn "${thin} of ${games:-0} upcoming game(s) have under 100 priced outcomes;
        those games produce no picks. priorline@early buys them"
fi

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails check(s) failed, $warns warning(s). Fix before building."
  exit 1
fi
echo "all clear${warns:+, $warns warning(s)}."
