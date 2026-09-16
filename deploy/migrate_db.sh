#!/bin/sh
# Move the database to the server, once.
#
#   sh deploy/migrate_db.sh dump                    # local -> priorline.dump
#   sh deploy/migrate_db.sh restore user@host       # ship it and load it
#
# Custom format rather than plain SQL: it compresses, and `pg_restore -j` loads
# the indexes in parallel, which is most of the time on a table with 130k
# feature rows.
#
# The odds history is the part that cannot be rebuilt. Game stats and features
# can be regenerated from nflverse in twenty minutes, but a closing price not
# captured before kickoff is gone, so this dumps everything rather than trying
# to be clever about what to keep.

set -e
DUMP="${DUMP:-priorline.dump}"
MODE="${1:-dump}"

case "$MODE" in
  dump)
    echo "==> dumping local database"
    docker compose exec -T postgres pg_dump -U app -d app -Fc -Z6 > "$DUMP"
    echo "    wrote $DUMP ($(du -h "$DUMP" | cut -f1))"
    ;;

  restore)
    HOST="${2:?usage: $0 restore user@host}"
    [ -f "$DUMP" ] || { echo "no $DUMP; run '$0 dump' first"; exit 1; }

    echo "==> copying to $HOST"
    scp "$DUMP" "$HOST:/tmp/$DUMP"

    # Start from an empty schema, not from --clean.
    #
    # The production compose mounts db/init.sql into the Postgres entrypoint so
    # the API has tables to answer against before the dump lands. On a fresh
    # volume that runs, and it creates a nine-column players table that the
    # forty-table dump then cannot replace: --clean issues DROP TABLE players,
    # two foreign keys depend on it, the drop fails, CREATE fails as "already
    # exists", and the COPY fails on a column that version has never had. The
    # restore reports "errors ignored on restore: 11" and exits 0, and every
    # other table looks right, so the row counts say it worked. players is
    # empty and every projection on the site joins to it.
    #
    # Dropping the schema first removes the collision at the source. It is
    # destructive by design: this command means "make the server look like my
    # laptop", and it is the reason it is a separate subcommand rather than
    # something the pipeline runs.
    echo "==> clearing the bootstrap schema"
    ssh "$HOST" "set -a; . /etc/priorline/env; set +a; cd /opt/priorline && \
      docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
        psql -U app -d app -v ON_ERROR_STOP=1 \
        -c 'DROP SCHEMA public CASCADE' \
        -c 'CREATE SCHEMA public' \
        -c 'GRANT ALL ON SCHEMA public TO app' \
        -c 'GRANT ALL ON SCHEMA public TO public'"

    echo "==> restoring"
    # -j4 because the index builds dominate and the box has more than one core.
    #
    # The dump is copied into the container rather than piped into it, which
    # looks like a pointless extra step and is not. pg_restore refuses to run
    # parallel jobs against standard input, because it has to seek around the
    # archive to schedule them: "parallel restore from standard input is not
    # supported", exit 1. This piped it in and passed -j4, so it had never
    # worked. Found on the first real deploy, which is late for a script whose
    # entire job runs once.
    #
    # The env file is sourced first. Every variable in the production
    # compose file is written ${VAR:?...}, so compose refuses to read the
    # file at all without them, and a non-interactive ssh session starts
    # with none of them set: the restore would abort on a missing password
    # before it ever reached the dump.
    ssh "$HOST" "set -a; . /etc/priorline/env; set +a; cd /opt/priorline && \
      docker compose -f deploy/docker-compose.prod.yml cp /tmp/$DUMP postgres:/tmp/$DUMP && \
      docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
        pg_restore -U app -d app --no-owner -j4 /tmp/$DUMP && \
      docker compose -f deploy/docker-compose.prod.yml exec -T postgres rm -f /tmp/$DUMP && \
      rm -f /tmp/$DUMP"

    echo "==> verifying"
    ssh "$HOST" "set -a; . /etc/priorline/env; set +a; cd /opt/priorline && \
      docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
        psql -U app -d app -c \"
          SELECT 'features' t, count(*) FROM player_market_features
          UNION ALL SELECT 'game stats', count(*) FROM player_game_stats_app
          UNION ALL SELECT 'odds snapshots', count(*) FROM odds_snapshots
          UNION ALL SELECT 'graded picks', count(*) FROM prop_edge_results;\""
    ;;

  *)
    echo "usage: $0 [dump|restore user@host]"; exit 2 ;;
esac
