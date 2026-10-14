"""What do books actually quote for an alternate line, against what it is worth?

The base-rate table replicates: 48 cells fitted on the walk-forward rows and
checked against the live season come back at 0.955 correlation, 3.5% mean
absolute difference, +1.3% drift. So "a receiver clears half his line 70.6% of
the time in this band" is a price, not a historical curiosity.

What nobody here knows is what a sportsbook charges for that. If the book quotes
-240 against a fair -241, the whole idea is dead on arrival and no amount of
modelling rescues it. If it quotes -200, there are eight points sitting there
for anyone with the table. One slate of alternate markets answers it.

This asks, for ONE event, and reports every quoted alternate line beside the
measured rate for its market and line band, with the edge in points. It writes
nothing to the database, which is deliberate: odds_player_props is unique on
(event, bookmaker, market, player, outcome) with no line in the key, so several
alternate lines for one player would collapse onto each other. Storing them
properly is a schema change and is not worth making before the prices justify it.

COST. The Odds API bills one credit per market per region per event. The default
list is six markets, so one event is about six credits. It refuses to run
against more than one event and prints the exact request before sending it.

    # see the request and spend nothing
    docker compose run --rm training python probe_alt_odds.py

    # actually buy it
    docker compose run --rm -e SPEND=1 training python probe_alt_odds.py
"""

import json
import os
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import create_engine, text

import build_prop_edges as bp

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
TABLE = ARTIFACTS / "alt_line_base_rates.json"

API_KEY = os.getenv("ODDS_API_KEY", "")
BASE = os.getenv("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
SPORT = os.getenv("ODDS_API_SPORT", "americanfootball_nfl")
REGIONS = os.getenv("ODDS_API_REGIONS", "us")
BOOKMAKERS = os.getenv("ODDS_API_BOOKMAKERS", "")

# The alternate twins of the markets this project already publishes. Anytime
# touchdown has no alternate, it is already a threshold.
ALT_MARKETS = os.getenv("ALT_MARKETS", ",".join([
    "player_reception_yds_alternate",
    "player_receptions_alternate",
    "player_rush_yds_alternate",
    "player_rush_attempts_alternate",
    "player_pass_yds_alternate",
    "player_pass_tds_alternate",
]))

ALT_TO_CODE = {
    "player_reception_yds_alternate": "rec_yds",
    "player_receptions_alternate": "recs",
    "player_rush_yds_alternate": "rush_yds",
    "player_rush_attempts_alternate": "rush_att",
    "player_pass_yds_alternate": "pass_yds",
    "player_pass_tds_alternate": "pass_td",
}


def implied(american: float) -> float:
    a = float(american)
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def fmt(american) -> str:
    a = float(american)
    return f"{a:,.0f}" if a < 0 else f"+{a:,.0f}"


def lookup(table, market, main_line, alt_line):
    """The measured rate for this alternate line, from its own line band."""
    cells = table.get(market) or []
    best = None
    for c in cells:
        lo = c["line_min"] if c["line_min"] is not None else float("-inf")
        hi = c["line_max"] if c["line_max"] is not None else float("inf")
        if not (lo <= main_line < hi):
            continue
        want = main_line * c["step"] if c["kind"] == "fraction" else main_line + c["step"]
        d = abs(want - alt_line)
        if best is None or d < best[0]:
            best = (d, c, want)
    return best


def main():
    if not API_KEY:
        raise SystemExit("ODDS_API_KEY is not set in this container")
    if not TABLE.exists():
        raise SystemExit(
            f"{TABLE.name} not found. Run fit_alt_line_table.py with WRITE=1 first; "
            "there is nothing to compare a quote against without it.")
    table = json.loads(TABLE.read_text(encoding="utf-8"))

    eng = create_engine(bp.DATABASE_URL, future=True)
    ev = pd.read_sql(text("""
        SELECT provider_event_id, commence_time, home_team, away_team
        FROM odds_events
        WHERE commence_time > NOW()
        ORDER BY commence_time
        LIMIT 1
    """), eng)
    if ev.empty:
        raise SystemExit("no upcoming event to probe")
    e = ev.iloc[0]

    # The main lines we already hold for that game, so a quote can be placed in
    # the right band and compared against the right cell.
    main = pd.read_sql(text("""
        SELECT player_name, market_key, AVG(line) AS line
        FROM odds_player_props
        WHERE provider_event_id = :eid AND line IS NOT NULL
        GROUP BY player_name, market_key
    """), eng, params={"eid": e["provider_event_id"]})
    from odds_markets import MARKET_TO_ODDS
    odds_to_code = {v: k for k, v in MARKET_TO_ODDS.items()}
    main["code"] = main["market_key"].map(odds_to_code)
    main_lines = {(r.player_name, r.code): float(r.line)
                  for r in main.itertuples(index=False) if r.code}

    markets = [m.strip() for m in ALT_MARKETS.split(",") if m.strip()]
    url = f"{BASE}/sports/{SPORT}/events/{e['provider_event_id']}/odds"
    params = {"apiKey": "<redacted>", "regions": REGIONS,
              "markets": ",".join(markets), "oddsFormat": "american"}
    if BOOKMAKERS:
        params["bookmakers"] = BOOKMAKERS

    print(f"event : {e['away_team']} at {e['home_team']}  {e['commence_time']}")
    print(f"main lines held for it: {len(main_lines)}")
    print(f"markets: {len(markets)}  regions: {REGIONS}")
    print(f"cost  : about {len(markets)} credit(s), one per market per region")
    print(f"GET {url}")
    print(f"    {params}")
    if os.getenv("SPEND") != "1":
        print("\nSPEND is not 1, so nothing was requested and nothing was spent.")
        return

    params["apiKey"] = API_KEY
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    print(f"\nremaining credits: {r.headers.get('x-requests-remaining')}  "
          f"used: {r.headers.get('x-requests-used')}")
    data = r.json()

    rows = []
    for bk in data.get("bookmakers", []):
        for mk in bk.get("markets", []):
            code = ALT_TO_CODE.get(mk.get("key"))
            if not code:
                continue
            for oc in mk.get("outcomes", []):
                if str(oc.get("name", "")).lower() not in ("over", "yes"):
                    continue
                player = oc.get("description") or oc.get("participant")
                point, price = oc.get("point"), oc.get("price")
                if player is None or point is None or price is None:
                    continue
                ml = main_lines.get((player, code))
                if ml is None:
                    continue
                hit = lookup(table, code, ml, float(point))
                if hit is None:
                    continue
                _, cell, want = hit
                need = implied(price)
                rows.append({
                    "book": bk.get("key"), "player": player, "market": code,
                    "main": ml, "alt": float(point), "price": float(price),
                    "need": need, "measured": cell["rate"],
                    "lo": cell["ci"][0], "n": cell["n"],
                    "edge": cell["rate"] - need,
                    # The table's cells moved up to ten points between the
                    # backtest and the live season, so the honest edge uses the
                    # bottom of the interval rather than the point estimate.
                    "edge_lo": cell["ci"][0] - need,
                    "nearest": want,
                })
    if not rows:
        print("no quoted alternate line matched a main line we hold")
        return
    d = pd.DataFrame(rows).sort_values("edge_lo", ascending=False)
    print(f"\n{len(d)} quoted alternate line(s) matched to a measured cell\n")
    print(f"  {'player':<22}{'mkt':<9}{'main':>7}{'alt':>7}{'price':>8}"
          f"{'needs':>8}{'measured':>10}{'edge':>8}{'edge@lo':>9}  book")
    for r_ in d.head(40).itertuples(index=False):
        print(f"  {r_.player[:21]:<22}{r_.market:<9}{r_.main:>7.1f}{r_.alt:>7.1f}"
              f"{fmt(r_.price):>8}{r_.need:>8.1%}{r_.measured:>10.1%}"
              f"{r_.edge:>+8.1%}{r_.edge_lo:>+9.1%}  {r_.book}")
    pos = d[d["edge_lo"] > 0]
    print(f"\n{len(pos)} of {len(d)} clear the bottom of their interval. "
          f"Median edge at the point estimate {d['edge'].median():+.1%}, "
          f"at the interval floor {d['edge_lo'].median():+.1%}.")
    print("If the second number is negative across the board, books price these "
          "at or above the true rate and the idea is finished.")


if __name__ == "__main__":
    main()
