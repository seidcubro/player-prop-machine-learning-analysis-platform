#!/bin/sh
# Pull on the server, without the generated record blocking it.
#
#   sh scripts/pull.sh
#
# published-record.json is tracked, because the web build imports it, AND it is
# rewritten by every scheduled run that grades, because the figures the site
# states as fact going stale is how it ended up claiming +4.0% against an actual
# +0.7%. Those two facts together mean the working tree on the server is dirty
# in exactly one file most of the time, and `git pull` refuses:
#
#   error: Your local changes to the following files would be overwritten by
#   merge: apps/web/src/lib/published-record.json
#
# Nothing is lost by discarding the server's copy. It is derived: every number
# in it comes from prop_edge_results, the next scheduled run regenerates it, and
# `eval_strategy.py WRITE_RECORD=1` reproduces it on demand.
#
# This discards that ONE file and pulls. Any other local change is left alone
# and will still stop the pull, which is correct: a change to anything else on
# this machine is either a mistake or something worth keeping.

set -e

REPO="${REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
GENERATED="apps/web/src/lib/published-record.json"

cd "$REPO"

if ! git diff --quiet -- "$GENERATED" 2>/dev/null; then
    echo "discarding the server's generated copy of $GENERATED"
    echo "  (it is rebuilt from prop_edge_results by the next run that grades)"
    git checkout -- "$GENERATED"
fi

dirty=$(git status --porcelain | grep -v "^?? " || true)
if [ -n "$dirty" ]; then
    echo
    echo "other local changes are present and this will not touch them:"
    echo "$dirty"
    echo
    echo "deal with those first; the pull below will likely stop on them."
fi

git pull
