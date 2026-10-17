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

DRIFT = float(os.getenv("DRIFT_ALLOWANCE", "0.035"))

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


def pick_dist(dists, market, main_line):
    """The distribution for this market and this line's band."""
    for c in dists.get(market) or []:
        lo = c["line_min"] if c["line_min"] is not None else float("-inf")
        hi = c["line_max"] if c["line_max"] is not None else float("inf")
        if lo <= main_line < hi:
            return c
    return None


def exceed(grid, target):
    """P(outcome clears `target`), or None when target is off the support.

    Refusing is the whole point. The first version of this matched a quote to
    the NEAREST of a handful of stored thresholds, so a 1.5 reception main line
    with an alternate quoted at 6.5 was scored against "clears 0.5 catches",
    came back 84.5%, and the probe reported an 81 point edge. 557 of 751 quotes
    "cleared", median edge +30.1%, and every number was an artefact of the
    match. A quote outside what was measured gets no answer at all now.
    """
    import numpy as _np
    q = _np.asarray(grid["q"], dtype=float)
    p = _np.asarray(grid["p"], dtype=float)
    if target < q[0] or target > q[-1]:
        return None
    return float(1.0 - _np.interp(target, q, p))


def wilson(p, n, z=1.96):
    """Interval on a proportion, so a thin tail cannot read as a certainty."""
    if n <= 0:
        return (0.0, 1.0)
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main():
    if not API_KEY:
        raise SystemExit("ODDS_API_KEY is not set in this container")
    if not TABLE.exists():
        raise SystemExit(
            f"{TABLE.name} not found. Run fit_alt_line_table.py with WRITE=1 first; "
            "there is nothing to compare a quote against without it.")
    blob = json.loads(TABLE.read_text(encoding="utf-8"))
    dists = blob.get("distributions")
    if not dists:
        raise SystemExit(
            f"{TABLE.name} has no distributions in it. Rerun fit_alt_line_table.py "
            "with WRITE=1; the old format stored only a few fixed thresholds and "
            "could not answer a quoted alternate line.")

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
    refused = 0
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
                if ml is None or ml == 0:
                    continue
                g = pick_dist(dists, code, ml)
                if g is None:
                    continue
                # The quoted line expressed the way the distribution measures
                # it: a ratio for yardage, a difference for counts.
                target = (float(point) / ml if g["kind"] == "ratio"
                          else float(point) - ml)
                rate = exceed(g, target)
                if rate is None:
                    refused += 1
                    continue
                need = implied(price)
                lo, hi = wilson(rate, g["n"])
                # Cells drifted up to ten points between the backtest and the
                # live season, so the honest edge is the interval floor less
                # that drift allowance, not the point estimate.
                floor = max(0.0, lo - DRIFT)
                rows.append({
                    "book": bk.get("key"), "player": player, "market": code,
                    "main": ml, "alt": float(point), "price": float(price),
                    "need": need, "measured": rate, "lo": lo, "n": g["n"],
                    "edge": rate - need, "edge_lo": floor - need,
                })
    print(f"{refused} quote(s) refused: outside the range the data measures")
    if not rows:
        print("no quoted alternate line could be scored against measured data")
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
