#!/bin/sh
# One scheduled run, serialised against every other mode.
#
#   sh deploy/run_mode.sh closing
#
# This exists because of a Tuesday-morning alert that looked like a failure and
# was not:
#
#   PriorLine closing failed at 2026-11-03 02:05 EST
#   ...last log line: [2026-11-03 00:07:06] ==> done
#
# The 02:05 run produced no output at all, not even its own "mode" line,
# because the script never started. The weekly retrain begins at 01:00 on a
# Tuesday and runs the better part of an hour; the closing capture fires at
# :05 every hour and takes the same lock. flock was given -w 3600, so the 01:05
# run waited a full hour, gave up at 02:05, exited 1, and systemd called that a
# failure and sent a push.
#
# It was not a failure. It was two jobs correctly declining to run at once, at
# one in the morning on a Tuesday, when no game is within a day of kickoff and
# the closing capture had nothing to capture.
#
# So a run that cannot get the lock SKIPS, and says so, and exits clean. It is
# scheduled again within the hour and the lock is never held that long outside
# the weekly window. The wait is still generous, because during a slate the
# contention is seconds and waiting is right; it is only the hour-long wait
# ending in a page that was wrong.
#
# A genuine failure inside the script still exits non-zero and still pages.

set -e

MODE="${1:?usage: run_mode.sh <closing|board|early|daily|weekly|migrate|refresh>}"
REPO="${REPO:-/opt/priorline}"
LOCK="${PRIORLINE_LOCK:-/var/lock/priorline.lock}"
# Long enough that a board build or a props sync ahead of us finishes, short
# enough that it cannot swallow an hour and land on the next timer.
WAIT="${LOCK_WAIT:-1200}"
# flock returns this when the lock could not be taken in time (man flock, -E),
# which is how a timeout is told apart from the script itself exiting 1.
#
# 75 is chosen because scheduled_update.sh never returns it: it exits 0, 1 on a
# failed step, or 2 on a bad mode name. If that ever changes, a genuine failure
# would be silently reported as a skip, so the value has to stay outside the
# set of codes the script can produce.
BUSY=75

cd "$REPO"

set +e
/usr/bin/flock -w "$WAIT" -E "$BUSY" "$LOCK" \
    /bin/sh scripts/scheduled_update.sh "--$MODE"
status=$?
set -e

if [ "$status" = "$BUSY" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ==> $MODE skipped: another PriorLine" \
         "run held the lock for more than ${WAIT}s. This is normal while the" \
         "weekly retrain is running. The next timer picks it up."
    exit 0
fi

exit "$status"
