"""Backfill historical sportsbook lines into odds_snapshots.

Why not the existing API endpoint: `/odds/sync/historical_events` begins with
`DELETE FROM odds_events`, so pulling history would wipe the upcoming slate. This
writes only to `odds_snapshots`, which is append-only, and never touches the
tables the live site reads.

Cost matters here. The Odds API bills historical requests at roughly ten times a
current one, per market per region per event, so a careless loop over four
seasons can burn thousands of credits. Every run therefore:

  - reports remaining quota before and after, and records it in odds_api_usage
  - refuses to start if the estimate exceeds MAX_CREDITS
  - supports --dry-run to price a pull without spending anything

Usage:
    python backfill_historical_odds.py --season 2024 --week-of 2024-09-08 --dry-run
    python backfill_historical_odds.py --season 2024 --week-of 2024-09-08
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import create_engine, text

API_BASE = os.getenv("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
API_KEY = os.getenv("ODDS_API_KEY", "")
SPORT = os.getenv("ODDS_API_SPORT", "americanfootball_nfl")
REGIONS = os.getenv("ODDS_API_REGIONS", "us")
BOOKS = os.getenv("ODDS_API_BOOKMAKERS", "draftkings,fanduel,betmgm,caesars")

# The markets the platform actually models. Every extra one multiplies cost.
MARKETS = os.getenv(
    "HIST_MARKETS",
    "player_reception_yds,player_receptions,player_rush_yds,"
    "player_rush_attempts,player_pass_yds,player_pass_tds",
).split(",")

MAX_CREDITS = int(os.getenv("MAX_CREDITS", "6000"))
HIST_MULTIPLIER = 10  # historical requests cost ~10x a current one

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)


def quota() -> tuple[int, int]:
    r = requests.get(f"{API_BASE}/sports/", params={"apiKey": API_KEY}, timeout=30)
    r.raise_for_status()
    return (
        int(r.headers.get("x-requests-remaining", -1)),
        int(r.headers.get("x-requests-used", -1)),
    )


def historical_events(as_of: str) -> list[dict]:
    r = requests.get(
        f"{API_BASE}/historical/sports/{SPORT}/events",
        params={"apiKey": API_KEY, "date": as_of},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def historical_props(event_id: str, as_of: str) -> dict:
    r = requests.get(
        f"{API_BASE}/historical/sports/{SPORT}/events/{event_id}/odds",
        params={
            "apiKey": API_KEY,
            "date": as_of,
            "regions": REGIONS,
            "bookmakers": BOOKS,
            "markets": ",".join(MARKETS),
            "oddsFormat": "american",
        },
        timeout=60,
    )
    # 404: no odds recorded for that event at that timestamp.
    # 422: the market did not exist yet -- player props only go back to roughly
    # mid-2023, so 2022 and earlier return this for every event. Neither is a
    # failure worth aborting a multi-season backfill for.
    if r.status_code in (404, 422):
        return {}
    r.raise_for_status()
    return r.json().get("data", {}) or {}


INSERT = text(
    """
    INSERT INTO odds_snapshots
      (provider_event_id, sport_key, commence_time, home_team, away_team,
       bookmaker_key, bookmaker_title, market_key, player_name, outcome_name,
       line, price_american, last_update, observed_at, source)
    VALUES
      (:eid, :sport, :commence, :home, :away, :bk, :bt, :mk, :pn, :on,
       :line, :price, :lu, :observed, 'historical')
    ON CONFLICT (provider_event_id, bookmaker_key, market_key,
                 player_name, outcome_name, observed_at)
    DO NOTHING
    """
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week-of", required=True,
                    help="as-of date, e.g. 2024-09-08 (props are pulled at kickoff-ish)")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--hour", default="17:00:00Z",
                    help="UTC time on that date to snapshot (default just before 1pm ET kickoffs)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not API_KEY:
        sys.exit("ODDS_API_KEY not set")

    as_of = f"{args.week_of}T{args.hour}"
    remaining_before, used_before = quota()
    print(f"quota before: {remaining_before} remaining, {used_before} used")

    events = historical_events(as_of)
    # Only games kicking off within ~4 days of the snapshot: the historical
    # events feed returns the whole forward schedule, and pulling props for
    # games three weeks out is both useless and expensive.
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) + timedelta(days=4)
    slate = [
        e for e in events
        if e.get("commence_time")
        and datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) <= cutoff
    ]

    est = len(slate) * len(MARKETS) * HIST_MULTIPLIER
    print(f"as-of {as_of}: {len(events)} events, {len(slate)} in the next 4 days")
    print(f"markets: {len(MARKETS)} -> estimated cost ~{est} credits")

    if args.dry_run:
        print("dry run; nothing fetched")
        return
    if est > MAX_CREDITS:
        sys.exit(f"estimate {est} exceeds MAX_CREDITS={MAX_CREDITS}; narrow the pull")

    engine = create_engine(DATABASE_URL, future=True)
    rows = 0
    with engine.begin() as conn:
        for i, ev in enumerate(slate, 1):
            payload = historical_props(ev["id"], as_of)
            for book in payload.get("bookmakers", []) or []:
                for market in book.get("markets", []) or []:
                    for oc in market.get("outcomes", []) or []:
                        name = oc.get("description") or oc.get("name")
                        if not name:
                            continue
                        conn.execute(INSERT, {
                            "eid": ev["id"], "sport": SPORT,
                            "commence": ev.get("commence_time"),
                            "home": ev.get("home_team"), "away": ev.get("away_team"),
                            "bk": book.get("key"), "bt": book.get("title"),
                            "mk": market.get("key"), "pn": name,
                            "on": oc.get("name"), "line": oc.get("point"),
                            "price": oc.get("price"),
                            "lu": book.get("last_update"), "observed": as_of,
                        })
                        rows += 1
            print(f"  [{i}/{len(slate)}] {ev.get('away_team')} @ {ev.get('home_team')}: {rows} rows so far")

    remaining_after, used_after = quota()
    spent = used_after - used_before
    print(f"\nwrote {rows} snapshot rows")
    print(f"quota after: {remaining_after} remaining (spent {spent} credits)")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO odds_api_usage (operation, detail, requests_used, remaining) "
                "VALUES ('historical_backfill', :d, :u, :r)"
            ),
            {"d": f"season={args.season} as_of={as_of} events={len(slate)}",
             "u": spent, "r": remaining_after},
        )


if __name__ == "__main__":
    main()
