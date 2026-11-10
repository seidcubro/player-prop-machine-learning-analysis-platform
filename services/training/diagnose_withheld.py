"""What happened to the props the builder priced and did not publish?

diagnose_coverage.py says where props go missing. In five weeks out of nine
passing yards put 2 to 14 quarterbacks on the board out of about 30 priced, and
receiving yards boarded under half of what it priced for six weeks.

A quarterback who is priced for passing yards has a model, a feature row and a
team, or his attempts prop would be missing too. That leaves one gate that
looks at a single row after it has been projected. The builder takes the side
from the median and the probability from the calibrator, and withholds a pick
whose probability comes out at 50% or under ("model gives the pick under
50%"). When that was written it took six rows in 350. The calibrator has since
gained an offset toward the over for this season, and a yardage median sits
close to its line compared with how wide the outcome is, so an under the
median only just prefers is the kind of pick that offset pushes below 50%.

That is a suspect by elimination and not a finding. Every build now prints its
drops by market, so the next board says outright which gate took what, and
/var/log/priorline/board.log already holds the totals for the weeks in
question.

A withheld prop was never graded, because it was never a pick. But it was
priced and the game was played, so it can still be scored against its line.
This does that, and it answers the question that decides what to do about the
gate:

    If the withheld props went over about half the time, they are coin flips
    and belong on the board as small, which is what small means.

    If they went over well above half, the calibrator was right that the
    median's under was wrong, and the pick to publish is the over.

    If they went under well above half, the gate has been throwing away
    winners and should go.

It does not know which side the model preferred on a withheld prop, because
nothing stored it. It reports which way the prop went, and the same figure for
the props that were boarded, where the side is known.

A quarterback who did not start is not a withheld pick, he is a prop that was
correctly not offered, and his zero would count as an under. Passing props are
only scored when the player threw ten passes.

Reads four tables. Writes nothing and spends no credits.

    docker compose run --rm training python diagnose_withheld.py
"""

import math
import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp
from odds_markets import ALL_ODDS_TO_MARKET

SEASON = int(os.getenv("SEASON", "2026"))

STAT = {
    "rec_yds": "receiving_yards",
    "recs": "receptions",
    "rush_yds": "rushing_yards",
    "rush_att": "carries",
    "pass_yds": "passing_yards",
    "pass_att": "attempts",
    "pass_completions": "completions",
    "pass_td": "passing_tds",
}
PASSING = {"pass_yds", "pass_att", "pass_completions", "pass_td"}
YARDAGE = {"rec_yds", "rush_yds", "pass_yds"}


def norm(s):
    return (s.fillna("").str.lower().str.replace(".", "", regex=False)
            .str.replace("-", " ", regex=False).str.strip())


def week_of(ts):
    t = pd.to_datetime(ts, utc=True).dt.tz_convert("America/New_York")
    return (t - pd.Timedelta(days=2)).dt.isocalendar().week.astype(int)


def wilson(k, n):
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def line_for(n, k):
    lo, hi = wilson(k, n)
    return f"{n:>6}{k / n:>9.1%}   [{lo:.1%}, {hi:.1%}]" if n else f"{0:>6}"


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    since = f"{SEASON}-08-25"

    # The last price captured before kickoff, one line per prop: the middle of
    # the books that posted it.
    priced = pd.read_sql(text("""
        WITH last AS (
            SELECT provider_event_id, player_name, market_key,
                   MAX(observed_at) AS seen
            FROM odds_snapshots
            WHERE commence_time >= :since AND commence_time < NOW()
              AND observed_at <= commence_time
              AND line IS NOT NULL AND LOWER(outcome_name) = 'over'
            GROUP BY 1, 2, 3
        )
        SELECT s.provider_event_id, s.player_name, s.market_key,
               MIN(s.commence_time) AS commence_time,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY s.line) AS line
        FROM odds_snapshots s
        JOIN last l
          ON l.provider_event_id = s.provider_event_id
         AND l.player_name = s.player_name
         AND l.market_key = s.market_key
         AND l.seen = s.observed_at
        WHERE s.line IS NOT NULL AND LOWER(s.outcome_name) = 'over'
        GROUP BY 1, 2, 3
    """), eng, params={"since": since})
    if priced.empty:
        raise SystemExit("odds_snapshots holds nothing for this season")
    priced["market_code"] = priced["market_key"].map(ALL_ODDS_TO_MARKET)
    priced = priced[priced["market_code"].isin(STAT)].copy()
    priced["key"] = norm(priced["player_name"])
    priced["week"] = week_of(priced["commence_time"])
    priced["game_date"] = (pd.to_datetime(priced["commence_time"], utc=True)
                           .dt.tz_convert("America/New_York").dt.normalize()
                           .dt.tz_localize(None))

    boarded = pd.read_sql(text("""
        SELECT player_name, market_code, commence_time, recommended_side,
               win_prob, edge_tier
        FROM prop_edges_history
        WHERE commence_time >= :since AND commence_time < NOW()
    """), eng, params={"since": since})
    boarded["key"] = norm(boarded["player_name"])
    boarded["week"] = week_of(boarded["commence_time"])
    # One row a prop. Books can disagree on the side when their lines differ,
    # so take the side most of them were given.
    side = (boarded.groupby(["key", "market_code", "week"])["recommended_side"]
            .agg(lambda s: s.mode().iloc[0]).rename("side").reset_index())

    stats = pd.read_sql(text("""
        SELECT p.name AS player_name, pgs.game_date,
               pgs.receiving_yards, pgs.receptions, pgs.rushing_yards,
               pgs.carries, pgs.passing_yards, pgs.attempts, pgs.completions,
               pgs.passing_tds
        FROM player_game_stats_app pgs
        JOIN players p ON p.external_id = pgs.player_id
        WHERE pgs.game_date >= :since
    """), eng, params={"since": since})
    stats["key"] = norm(stats["player_name"])
    stats["game_date"] = pd.to_datetime(stats["game_date"])
    # Two players who normalise to one name cannot be told apart here.
    twice = stats.groupby(["key", "game_date"]).size()
    shared = set(twice[twice > 1].index.get_level_values(0))
    stats = stats[~stats["key"].isin(shared)]

    d = priced.merge(stats.drop(columns=["player_name"]), on="key",
                     suffixes=("", "_stat"))
    gap = (d["game_date_stat"] - d["game_date"]).abs().dt.days
    d = d[gap <= 1].copy()
    d["gap"] = gap[gap <= 1]
    d = (d.sort_values("gap")
         .drop_duplicates(["provider_event_id", "key", "market_code"]))

    d["actual"] = np.nan
    for m, col in STAT.items():
        mask = d["market_code"] == m
        d.loc[mask, "actual"] = pd.to_numeric(d.loc[mask, col], errors="coerce")
    d = d[d["actual"].notna()]
    started = pd.to_numeric(d["attempts"], errors="coerce").fillna(0) >= 10
    d = d[~d["market_code"].isin(PASSING) | started]
    d = d[d["actual"] != d["line"]]  # a push settles nothing
    d["over"] = d["actual"] > d["line"]

    d = d.merge(side, on=["key", "market_code", "week"], how="left")
    d["boarded"] = d["side"].notna()
    d["won"] = np.where(d["side"] == "over", d["over"], ~d["over"])

    print(f"{SEASON}: {len(d):,} priced props that were played and did not "
          f"push. {int(d['boarded'].sum()):,} were boarded, "
          f"{int((~d['boarded']).sum()):,} were not.")
    print()
    print("WITHHELD: priced, played, never a pick. How often they went over.")
    print(f"  {'market':<18}{'props':>6}{'over':>9}   95% interval")
    for m in sorted(STAT):
        s = d[(d["market_code"] == m) & ~d["boarded"]]
        print(f"  {m:<18}" + line_for(len(s), int(s["over"].sum())))
    for label, group in (("yardage markets", YARDAGE),
                         ("count markets", set(STAT) - YARDAGE)):
        s = d[d["market_code"].isin(group) & ~d["boarded"]]
        print(f"  {label:<18}" + line_for(len(s), int(s["over"].sum())))
    print()

    print("BOARDED: the same props, where the side is known.")
    print(f"  {'market':<18}{'props':>6}{'unders':>9}{'over':>9}{'won':>9}"
          f"   95% interval on won")
    for m in sorted(STAT):
        s = d[(d["market_code"] == m) & d["boarded"]]
        if s.empty:
            print(f"  {m:<18}{0:>6}")
            continue
        lo, hi = wilson(int(s["won"].sum()), len(s))
        print(f"  {m:<18}{len(s):>6}{(s['side'] == 'under').mean():>9.1%}"
              f"{s['over'].mean():>9.1%}{s['won'].mean():>9.1%}"
              f"   [{lo:.1%}, {hi:.1%}]")
    print()

    print("WITHHELD, by half of the season, to see whether it holds twice.")
    weeks = sorted(d["week"].unique())
    cut = weeks[len(weeks) // 2]
    print(f"  {'market':<18}{'first half':>24}{'second half':>30}")
    for m in sorted(YARDAGE):
        s = d[(d["market_code"] == m) & ~d["boarded"]]
        a, b = s[s["week"] < cut], s[s["week"] >= cut]
        fa = f"{len(a)} at {a['over'].mean():.1%} over" if len(a) else "none"
        fb = f"{len(b)} at {b['over'].mean():.1%} over" if len(b) else "none"
        print(f"  {m:<18}{fa:>24}{fb:>30}")
    print()
    print("Under about 500 props in a row, read the interval and not the "
          "percentage.")


if __name__ == "__main__":
    main()
