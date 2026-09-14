#!/bin/sh
# Apply every migration in db/migrations, once, in order.
#
#   sh deploy/apply_migrations.sh                 # against the dev stack
#   COMPOSE="docker compose -f deploy/docker-compose.prod.yml" \
#     sh deploy/apply_migrations.sh               # against the server
#
# There was no runner for this. Migrations were applied by hand with psql, which
# works exactly as long as the person doing it remembers every file and the
# order they go in. Six were added in a single day, and the only thing carrying
# them to a new machine was the database dump, so any path that did not restore
# a dump produced a half-built schema with no error to say so.
#
# Applied files are recorded in schema_migrations, so this is safe to run
# repeatedly and safe to run on a database that is already current: it prints
# what it skipped and does nothing else.
#
# Ordering is by filename. Migrations here are additive (ADD COLUMN IF NOT
# EXISTS, CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS) so they do not
# depend on each other, but filename order keeps a rerun deterministic.

set -e

COMPOSE="${COMPOSE:-docker compose}"
DIR="${MIGRATIONS_DIR:-db/migrations}"

psql_do() {
  $COMPOSE exec -T postgres psql -U app -d app -v ON_ERROR_STOP=1 "$@"
}

psql_do -q -c "
  CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );"

# Baseline an existing database instead of replaying its history.
#
# These files go back to the first version of the schema and some of them can no
# longer run: add_tackles_combined.sql alters a column that was removed from
# player_game_stats seasons ago, so applying it in order against today's schema
# fails on a database that is already correct.
#
# The state a migration describes is already present on any database built from
# a restored dump, which is how the runbook builds one. So on a database that
# clearly has the schema and no migration record, every existing file is
# recorded as applied without being run, and only what is added afterwards
# actually executes. A genuinely empty database gets init.sql first, then this,
# and takes the same path.
# An empty database is a different problem, and says so.
#
# db/init.sql creates nine tables. The running system has forty: the rest are
# created by the ingest and the training scripts with CREATE TABLE IF NOT
# EXISTS, on first use. So a database that has only had init.sql applied is
# missing most of what these migrations alter, and replaying them there fails
# eight files deep in a cascade that looks like the migrations are broken when
# the database is simply empty.
#
# The runbook restores a dump before this runs, which is why this path is
# usually never taken. Saying what to do is better than failing obscurely.
# prop_edges is the marker, not player_market_features: init.sql creates the
# feature table, so its presence says nothing about whether the rest exists.
if [ "$(psql_do -tA -c "SELECT to_regclass('public.prop_edges') IS NOT NULL;" | tr -d '[:space:]')" != "t" ]; then
  echo "This database has only the tables db/init.sql creates, so it has not"
  echo "been restored or built yet."
  echo
  echo "Migrations alter tables the ingest creates on first use, so they cannot"
  echo "run against an empty schema. Do one of these first:"
  echo
  echo "  sh deploy/migrate_db.sh restore user@host    # the runbook's path"
  echo "  docker run ... priorline-ingest              # or build it from nflverse"
  echo
  echo "then run this again."
  exit 1
fi

has_schema=$(psql_do -tA -c "SELECT to_regclass('public.prop_edges') IS NOT NULL;" | tr -d '[:space:]')
tracked=$(psql_do -tA -c "SELECT count(*) FROM schema_migrations;" | tr -d '[:space:]')

if [ "$has_schema" = "t" ] && [ "${tracked:-0}" -eq 0 ]; then
  echo "existing schema with no migration record: baselining"
  for f in "$DIR"/*.sql; do
    [ -f "$f" ] || continue
    psql_do -q -c "INSERT INTO schema_migrations (filename) VALUES ('$(basename "$f")')
                   ON CONFLICT DO NOTHING;"
  done
  echo "baselined $(ls "$DIR"/*.sql 2>/dev/null | wc -l | tr -d ' ') migration(s) as already applied"
  echo "new migrations from here on will be applied normally"
  exit 0
fi

applied=0
skipped=0
for f in "$DIR"/*.sql; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  seen=$(psql_do -tA -c \
    "SELECT 1 FROM schema_migrations WHERE filename = '$name';" | tr -d '[:space:]')
  if [ "$seen" = "1" ]; then
    skipped=$((skipped + 1))
    continue
  fi

  printf 'applying %s\n' "$name"
  # Each migration and its bookkeeping in one transaction, so a failure leaves
  # neither the change nor the record of it.
  {
    echo "BEGIN;"
    # Nine of these files used to start with a byte order mark. psql tolerates
    # one at the very start of its input and rejects it anywhere else, so
    # wrapping such a file in BEGIN and COMMIT turned a migration that applied
    # fine by hand into "syntax error at or near ALTER", with an invisible
    # character as the cause. The marks were removed from the files rather than
    # worked around here, since nothing wanted them.
    cat "$f"
    echo ";"
    echo "INSERT INTO schema_migrations (filename) VALUES ('$name');"
    echo "COMMIT;"
  } | psql_do -q -f - || {
    echo "FAILED on $name, nothing from this file was applied"
    exit 1
  }
  applied=$((applied + 1))
done

echo "migrations: $applied applied, $skipped already current"
