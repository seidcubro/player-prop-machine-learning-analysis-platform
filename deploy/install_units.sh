#!/bin/sh
# Install the systemd units from this checkout and reload.
#
#   sudo sh deploy/install_units.sh
#
# `git pull` does not install systemd units. deploy/README.md documents the cp
# and the daemon-reload as part of first setup, which means they happen once and
# then never again, and every later change to a unit sits in the repo doing
# nothing while the machine keeps running the version from before it.
#
# That is not a theoretical gap. priorline@early.timer gained a Sunday 09:00
# schedule so the week's prices would be bought before Sunday's slate. The
# installed copy never got it, so the Sunday buy has never once run, and
# everything reported healthy the whole time: the timer was enabled, active, and
# firing exactly as its installed file said it should.
#
# Safe to run any time. It copies, reloads, enables the timers it installed, and
# prints what changed. It does not start a run.

set -e

SRC="$(cd "$(dirname "$0")/systemd" && pwd)"
DEST=/etc/systemd/system

if [ "$(id -u)" != "0" ]; then
  echo "run this with sudo: sudo sh deploy/install_units.sh" >&2
  exit 2
fi

changed=0
for f in "$SRC"/*; do
  b=$(basename "$f")
  if [ -f "$DEST/$b" ] && cmp -s "$f" "$DEST/$b"; then
    echo "  unchanged  $b"
    continue
  fi
  if [ -f "$DEST/$b" ]; then
    echo "  updated    $b"
  else
    echo "  installed  $b"
  fi
  cp "$f" "$DEST/$b"
  changed=$((changed + 1))
done

if [ "$changed" = "0" ]; then
  echo
  echo "nothing to do; the machine already matches the repo"
  exit 0
fi

echo
echo "reloading systemd"
systemctl daemon-reload

# Enable every timer that was shipped, not a hardcoded list. A timer added to
# the repo and copied here but never enabled is the same silent failure in a
# different place.
timers=""
for f in "$SRC"/*.timer; do
  timers="$timers $(basename "$f")"
done
echo "enabling:$timers"
# shellcheck disable=SC2086
systemctl enable --now $timers

echo
systemctl list-timers --all 'priorline@*' --no-pager
echo
echo "$changed unit(s) installed. No run was started."
