"""When the board is next rebuilt.

The site says "updated 18:43, next check in 22 minutes", and that sentence has
to come from somewhere. The honest source is the systemd timers in
deploy/systemd, which is on the server and not in the browser, so this module
mirrors them and the API serves the answer.

Mirrors, not reads. Parsing OnCalendar= at request time would be exact, but the
API runs in a container that does not mount /etc/systemd and has no business
doing so. The cost is that this file and the timers can drift, which is why the
schedule below is one table with the unit names on it: changing a timer without
changing this is a visible omission rather than a silent one.

Everything here is Eastern, because that is what the server clock is set to and
what the timers therefore fire on. See deploy/README.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# Mirrors deploy/systemd/priorline@*.timer. Times are Eastern.
#
# `--early` is deliberately absent: it buys Sunday's prices on a Friday and
# rebuilds the board, so it does change what the site shows, but it only fires
# when a slate is unpriced. Promising a rebuild that usually does not happen
# would make the countdown a liar once a week.
SCHEDULE = (
    # (unit, weekday or None for daily, hour, minute, what it does)
    ("closing", None, None, 5, "checks for price moves near kickoff"),
    ("daily", None, 8, 0, "full rebuild: results, features, grading, calibration"),
    ("board", None, 17, 0, "injuries and depth charts, then a rebuild"),
    ("weekly", 1, 1, 0, "retrains every market"),  # 1 = Tuesday
)


def _next_for(entry: tuple, now: datetime) -> datetime:
    """The next time this entry fires, strictly after `now`."""
    _unit, weekday, hour, minute, _what = entry

    # Hourly: the next occurrence of :minute, which may be this hour or the
    # next one. Seconds and microseconds are dropped so the countdown lands on
    # the minute rather than 43 seconds before it.
    if hour is None:
        candidate = now.replace(minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(hours=1)
        return candidate

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday is None:
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # Weekly. Walk forward to the right weekday, then past it if today's slot
    # has already gone.
    days = (weekday - candidate.weekday()) % 7
    candidate += timedelta(days=days)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def next_board_update(now: datetime | None = None) -> dict:
    """The next scheduled rebuild, and the schedule it came from.

    Returns the soonest entry, which outside of 08:00 and 17:00 is always the
    hourly check. That check is a check: it rebuilds the board when a game is
    near enough that its prices have moved, and costs nothing when none is. The
    wording the site uses says "checks", not "updates", because promising an
    update every hour would be wrong three days out of seven.
    """
    now = (now or datetime.now(EASTERN)).astimezone(EASTERN)

    upcoming = sorted(
        ((_next_for(entry, now), entry) for entry in SCHEDULE),
        key=lambda pair: pair[0],
    )
    when, entry = upcoming[0]
    unit, _weekday, _hour, _minute, what = entry

    return {
        "at": when.isoformat(),
        "job": unit,
        "does": what,
        "timezone": "America/New_York",
        # The whole schedule, so a page can show more than the next one without
        # a second round trip or its own copy of these times.
        "schedule": [
            {"job": e[0], "at": w.isoformat(), "does": e[4]} for w, e in upcoming
        ],
    }
