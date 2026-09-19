#!/bin/sh
# Say that a scheduled run failed, somewhere a person will actually see.
#
# The odds sync failed at 08:00 on two consecutive mornings and nobody knew.
# systemd recorded both failures correctly and the script wrote a clear reason
# to its log; the gap was that nothing carried either off the machine. Week 1
# was played in the meantime and the models never saw it.
#
# Called by priorline-alert@.service, which is the OnFailure= of every mode.
#
# Two channels, both optional, set in /etc/priorline/env:
#
#   ALERT_NTFY_TOPIC   a topic on ntfy.sh. Install the app, subscribe to the
#                      same topic, and failures arrive as phone notifications.
#                      No account. Anyone who guesses the topic can read it, so
#                      make it long and random.
#   ALERT_WEBHOOK      a Discord or Slack incoming webhook. Posted as JSON with
#                      a "content" field, which is what Discord expects; Slack
#                      wants "text", so ALERT_WEBHOOK_FIELD overrides the name.
#
# Neither set means this exits quietly. A machine with no alerting configured
# should not fail its own alerting.

set -e

UNIT="${1:-unknown}"

# The log the failed run wrote, if it wrote one. /var/log/priorline/<mode>.log
# is where the service sends stdout and stderr.
LOG="/var/log/priorline/${UNIT}.log"

if [ -f "$LOG" ]; then
  # Redact anything shaped like a secret before this leaves the machine.
  #
  # The admin token and the database URL are both in the environment of every
  # run, and a shell trace or a curl error can put either in the log. An alert
  # is a message to a third-party service, so it gets the failure and not the
  # credentials.
  TAIL=$(tail -n 12 "$LOG" \
    | sed -E 's#(postgresql|postgres)://[^[:space:]]*#\1://[redacted]#g' \
    | sed -E 's#[A-Za-z0-9_-]{24,}#[redacted]#g')
else
  TAIL="(no log at $LOG)"
fi

BODY="PriorLine: the $UNIT run failed on $(hostname) at $(date '+%Y-%m-%d %H:%M %Z').

$TAIL"

sent=0

if [ -n "${ALERT_NTFY_TOPIC:-}" ]; then
  # -m 20 so a hanging notification service cannot wedge the alert unit, and
  # || true because a failed alert must not itself be reported as a failure.
  curl -fsS -m 20 \
    -H "Title: PriorLine $UNIT failed" \
    -H "Priority: high" \
    -H "Tags: rotating_light" \
    -d "$BODY" \
    "https://ntfy.sh/${ALERT_NTFY_TOPIC}" >/dev/null || true
  sent=1
fi

if [ -n "${ALERT_WEBHOOK:-}" ]; then
  FIELD="${ALERT_WEBHOOK_FIELD:-content}"
  # Built with a heredoc through python3 rather than string-pasted, so a quote
  # or a newline in the log cannot produce invalid JSON and silently drop the
  # alert.
  printf '%s' "$BODY" | python3 -c \
    'import json,sys; print(json.dumps({sys.argv[1]: sys.stdin.read()[:1800]}))' "$FIELD" \
    | curl -fsS -m 20 -H "Content-Type: application/json" -d @- "$ALERT_WEBHOOK" >/dev/null || true
  sent=1
fi

if [ "$sent" = "0" ]; then
  echo "priorline alert: $UNIT failed, but no ALERT_NTFY_TOPIC or ALERT_WEBHOOK is set"
fi
