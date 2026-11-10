"""Where do the props go missing between the book and the record?

The live record has holes that have nothing to do with the model. Passing yards
has been graded in two weeks out of nine. Week 5 graded 20 props in total. A
market that is not on the board cannot be right or wrong, it is just absent, and
every week it is absent is a week of evidence I paid for in credits and did not
collect.

A prop passes three gates and can fall at any of them:

    priced    the book posted it and a sync captured it      odds_snapshots
    boarded   the builder turned it into a pick              prop_edges_history
    graded    the game was played and the pick was scored    prop_edge_results

This counts player-props at each gate, per market, per week, and names the gate
where each market lost them. It does not say why, only where, which is the part
that decides what to read next: nothing priced means the sync or the book,
priced and not boarded means the builder dropped it (no model bundle, no
features, a name that did not match), boarded and not graded means the grader.

Reads three tables. Writes nothing and spends no credits.

    docker compose run --rm training python diagnose_coverage.py
"""

import os

import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp
from odds_markets import ALL_ODDS_TO_MARKET

SEASON = int(os.getenv("SEASON", "2026"))
# Below this share of the priced props surviving a gate, the gate is named.
LEAK = float(os.getenv("LEAK", "0.5"))


def week_of(ts) -> pd.Series:
    """NFL week as an ISO week, with Monday night counted with its Sunday."""
    t = pd.to_datetime(ts, utc=True).dt.tz_convert("America/New_York")
    return (t - pd.Timedelta(days=2)).dt.isocalendar().week.astype(int)


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    since = f"{SEASON}-08-25"

    priced = pd.read_sql(text("""
        SELECT market_key, commence_time,
               COUNT(DISTINCT provider_event_id) AS games,
               COUNT(DISTINCT provider_event_id || '|' || player_name) AS props,
               COUNT(DISTINCT observed_at) AS pulls
        FROM odds_snapshots
        WHERE commence_time >= :since AND commence_time < NOW()
        GROUP BY market_key, commence_time
    """), eng, params={"since": since})
    boarded = pd.read_sql(text("""
        SELECT market_code, commence_time,
               COUNT(DISTINCT player_name) AS props
        FROM prop_edges_history
        WHERE commence_time >= :since AND commence_time < NOW()
        GROUP BY market_code, commence_time
    """), eng, params={"since": since})
    graded = pd.read_sql(text("""
        SELECT market_code, game_date, COUNT(*) AS props
        FROM prop_edge_results
        WHERE source = 'live' AND hit IS NOT NULL AND game_date >= :since
        GROUP BY market_code, game_date
    """), eng, params={"since": since})

    if priced.empty:
        raise SystemExit("odds_snapshots holds nothing for this season")

    priced["market_code"] = priced["market_key"].map(ALL_ODDS_TO_MARKET)
    unmapped = sorted(priced.loc[priced["market_code"].isna(),
                                 "market_key"].unique())
    priced = priced.dropna(subset=["market_code"])
    priced["week"] = week_of(priced["commence_time"])
    boarded["week"] = week_of(boarded["commence_time"])
    # A game date has no time on it. Noon keeps it on its own day in New York.
    graded["week"] = week_of(
        pd.to_datetime(graded["game_date"]) + pd.Timedelta(hours=17))

    first = int(priced["week"].min())
    for f in (priced, boarded, graded):
        f["week"] = f["week"] - first + 1

    p = priced.groupby(["market_code", "week"])[["games", "props"]].sum()
    pulls = priced.groupby(["market_code", "week"])["pulls"].max()
    b = boarded.groupby(["market_code", "week"])["props"].sum()
    g = graded.groupby(["market_code", "week"])["props"].sum()

    weeks = sorted(set(priced["week"]) | set(boarded["week"]) | set(graded["week"]))
    markets = sorted(set(priced["market_code"]) | set(boarded["market_code"])
                     | set(graded["market_code"]))
    slate = priced.groupby("week")["games"].max()

    print(f"{SEASON} season, games already played. Week 1 is the first week "
          "with a captured price.")
    if unmapped:
        print(f"market keys in the snapshots with no internal market: {unmapped}")
    print()
    print("games with any price captured, by week:")
    print("  " + "  ".join(f"wk{w}: {int(slate.get(w, 0))}" for w in weeks))

    leaks = []
    for m in markets:
        print()
        print(m)
        print(f"  {'week':<6}{'games':>7}{'pulls':>7}{'priced':>8}"
              f"{'boarded':>9}{'graded':>8}   lost at")
        for w in weeks:
            games = int(p["games"].get((m, w), 0))
            n_p = int(p["props"].get((m, w), 0))
            n_b = int(b.get((m, w), 0))
            n_g = int(g.get((m, w), 0))
            n_pull = int(pulls.get((m, w), 0))
            full = int(slate.get(w, 0))
            where = ""
            if n_p == 0 and full > 0:
                where = "never priced"
            elif full and games < LEAK * full:
                where = f"priced for {games} of {full} games"
            elif n_p and n_b < LEAK * n_p:
                where = "the builder"
            elif n_b and n_g < LEAK * n_b:
                where = "the grader"
            if where:
                leaks.append((m, w, where))
            print(f"  {w:<6}{games:>7}{n_pull:>7}{n_p:>8}{n_b:>9}{n_g:>8}   {where}")

    print()
    if not leaks:
        print("every market kept at least half its priced props through "
              "every gate, every week.")
        return
    print("where it leaked, counted:")
    tally = pd.DataFrame(leaks, columns=["market", "week", "gate"])
    tally["gate"] = tally["gate"].str.replace(r"priced for \d+ of \d+ games",
                                              "priced for part of the slate",
                                              regex=True)
    out = tally.groupby(["market", "gate"])["week"].apply(
        lambda s: ", ".join(str(int(x)) for x in sorted(s)))
    for (m, gate), wk in out.items():
        print(f"  {m:<18}{gate:<30}weeks {wk}")
    print()
    print("the builder only boards players it has a model and features for, "
          "so boarded sits under priced even when nothing is wrong. Half is "
          "the bar, not all.")


if __name__ == "__main__":
    main()
