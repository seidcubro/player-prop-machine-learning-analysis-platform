#!/bin/sh
# What the scheduler is actually doing. Read-only: starts nothing, changes nothing.
#
#   sudo sh scripts/scheduler_status.sh
#
# "Nothing runs automatically" has four distinct causes and the logs cannot tell
# them apart:
#
#   1. the timer unit was never installed
#   2. it was installed but never enabled, so it is on disk and inert
#   3. it is enabled and firing, and the run fails every time
#   4. it is enabled and firing and succeeding, and something downstream of it
#      is empty for its own reasons
#
# A log that stops on a Tuesday looks the same under 2 and 3. This prints the
# enablement, the schedule, the last and next fire, the exit status of the most
# recent run and the last line of each log, per mode, so the cause is visible
# in one screen instead of five commands and a guess.

MODES="closing board early daily weekly"
UNITS="/etc/systemd/system"
LOGS="/var/log/priorline"

echo "============================================================"
echo "PriorLine scheduler status"
echo "============================================================"

echo
echo "[1] unit files present"
for m in $MODES; do
  t="$UNITS/priorline@$m.timer"
  if [ -f "$t" ]; then
    printf '  %-8s timer installed\n' "$m"
  else
    printf '  %-8s TIMER NOT INSTALLED (%s)\n' "$m" "$t"
  fi
done
for f in priorline@.service priorline-alert@.service; do
  if [ -f "$UNITS/$f" ]; then
    printf '  %-28s present\n' "$f"
  else
    printf '  %-28s MISSING\n' "$f"
  fi
done

echo
echo "[2] enabled, and when each fires"
for m in $MODES; do
  en=$(systemctl is-enabled "priorline@$m.timer" 2>/dev/null || echo "not-found")
  ac=$(systemctl is-active "priorline@$m.timer" 2>/dev/null || echo "inactive")
  printf '  %-8s enabled=%-12s active=%-10s\n' "$m" "$en" "$ac"
done

echo
echo "  systemctl list-timers:"
systemctl list-timers --all 'priorline@*' --no-pager 2>/dev/null \
  | sed 's/^/    /' || echo "    (none listed)"

echo
echo "[3] how each last run ended"
for m in $MODES; do
  u="priorline@$m.service"
  res=$(systemctl show "$u" -p Result --value 2>/dev/null)
  code=$(systemctl show "$u" -p ExecMainStatus --value 2>/dev/null)
  when=$(systemctl show "$u" -p InactiveEnterTimestamp --value 2>/dev/null)
  printf '  %-8s result=%-12s exit=%-4s ended=%s\n' \
    "$m" "${res:-never-run}" "${code:--}" "${when:-never}"
done

echo
echo "[4] the alert path"
if systemctl cat 'priorline@.service' 2>/dev/null | grep -q '^OnFailure='; then
  echo "  OnFailure is wired"
else
  echo "  OnFailure NOT wired: a failed run tells nobody"
fi
for v in NTFY_URL NTFY_TOPIC; do
  if grep -q "^$v=" /etc/priorline/env 2>/dev/null; then
    echo "  $v set in /etc/priorline/env"
  else
    echo "  $v not set in /etc/priorline/env"
  fi
done

echo
echo "[5] which stack the scheduled runs drive"
# The single most consequential pair of variables here. Without them the script
# defaults to the development compose file, which starts a second Postgres with
# the password "app" beside the production one. See deploy/README.md.
for v in COMPOSE_FILE COMPOSE_PROJECT_NAME INGEST_NETWORK; do
  line=$(grep "^$v=" /etc/priorline/env 2>/dev/null)
  if [ -n "$line" ]; then
    echo "  $line"
  else
    echo "  $v NOT SET -- scheduled runs would drive the dev stack"
  fi
done

echo
echo "[6] last line of each log, and how old it is"
now=$(date +%s)
for m in $MODES; do
  f="$LOGS/$m.log"
  if [ ! -f "$f" ]; then
    printf '  %-8s no log at %s\n' "$m" "$f"
    continue
  fi
  mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
  age=$(( (now - mt) / 3600 ))
  printf '  %-8s %sh old: %s\n' "$m" "$age" "$(tail -1 "$f" | cut -c1-90)"
done

echo
echo "[7] the last failure each mode recorded, if any"
for m in $MODES; do
  f="$LOGS/$m.log"
  [ -f "$f" ] || continue
  hit=$(grep -n "FAILED:\|Traceback\|FAILURE(S)\|Error response" "$f" 2>/dev/null | tail -1)
  if [ -n "$hit" ]; then
    printf '  %-8s %s\n' "$m" "$(echo "$hit" | cut -c1-110)"
  else
    printf '  %-8s no failure recorded in the log\n' "$m"
  fi
done

echo
echo "============================================================"
