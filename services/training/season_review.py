"""Nine weeks in: where the board won, where it lost, and which changes would
have helped if they had been running all along.

weekly_autopsy.py shows the season a week at a time and research_loss_consensus
says what a loss looks like on the field. Neither tests a fix. This does, and
it tests each one the only way that means anything: for week W a rule may use
weeks before W and nothing else, and it is then scored on week W.

A rule fitted on all nine weeks and scored on the same nine always looks good.
I have shipped that mistake before (the tier re-rank that promoted 27 small
picks, the 10 to 20% band that did not replicate by week), so nothing here is
allowed to see the week it is graded on.

One row per distinct prop. The board writes a row per bookmaker and counting
them all makes every figure look three times more certain than it is.

    WHERE IT WON AND LOST   market by side, with what the price needed.
    WHY                     where our number sat against the line and against
                            what happened. A market whose median is on the
                            wrong side of the outcome every week is a level
                            problem, and a level can be corrected.
    WHAT WOULD HAVE HELPED  five ways of choosing the side, per market:

        published   what the board took
        under       always the under
        over        always the over
        level       our median moved by that market's own running miss
                    (median of actual minus median over the weeks before),
                    then the side read off the moved number
        leader      whichever of published, under and over had the best
                    record in that market over the weeks before

    Each is printed with how many weeks it beat the published side, because
    one good week is not a rule. A change is worth making when it is ahead on
    the season AND ahead in most weeks AND the market has enough props for
    the difference to be outside its interval.

    BY TIER, BY WEEK        whether elite is on top, as stored.

Read-only.

    docker compose run --rm training python season_review.py
"""

import math
import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SEASON = int(os.getenv("SEASON", "2026"))
MIN_PRIOR = int(os.getenv("MIN_PRIOR", "30"))
RULES = ["published", "under", "over", "level", "leader"]
TIERS = ["elite", "strong", "medium", "small"]


def wilson(k, n):
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def needed(price):
    p = pd.to_numeric(price, errors="coerce")
    return np.where(p > 0, 100.0 / (p + 100.0),
                    np.where(p < 0, (-p) / ((-p) + 100.0), np.nan))


def load(eng):
    d = pd.read_sql(text("""
        SELECT r.market_code, r.game_date, r.player_name, r.line, r.actual,
               r.edge_tier, r.recommended_side, r.price_american,
               r.projection_median AS p50, g.week
        FROM prop_edge_results r
        LEFT JOIN LATERAL (
            SELECT week FROM nfl_games n
            WHERE n.game_date = r.game_date AND n.season = :season
            LIMIT 1
        ) g ON TRUE
        WHERE r.source = 'live'
          AND r.hit IS NOT NULL AND r.line IS NOT NULL AND r.actual IS NOT NULL
          AND r.projection_median IS NOT NULL
          AND r.game_date >= :since
        ORDER BY r.game_date
    """), eng, params={"season": SEASON, "since": f"{SEASON}-08-01"})
    if d.empty:
        return d
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line"]).reset_index(drop=True)
    d = d[d["week"].notna() & (d["actual"] != d["line"])].copy()
    d["week"] = d["week"].astype(int)
    d["over"] = d["actual"] > d["line"]
    d["took_over"] = d["recommended_side"] == "over"
    d["needed"] = needed(d["price_american"])
    return d


def add_rules(d):
    """One column per rule: did that rule get this prop right.

    Split out so it can be run on a made-up season.
    """
    d = d.copy()
    d["published"] = d["took_over"] == d["over"]
    d["under"] = ~d["over"]
    d["over_rule"] = d["over"]
    d["level"] = d["published"]
    d["leader"] = d["published"]
    d["level_used"] = False
    for market, rows in d.groupby("market_code"):
        for week in sorted(rows["week"].unique()):
            now = rows[rows["week"] == week]
            past = rows[rows["week"] < week]
            if len(past) < MIN_PRIOR:
                continue
            shift = float((past["actual"] - past["p50"]).median())
            side_over = (now["p50"] + shift) > now["line"]
            d.loc[now.index, "level"] = (side_over == now["over"]).to_numpy()
            d.loc[now.index, "level_used"] = True
            best = max(("published", "under", "over_rule"),
                       key=lambda r: d.loc[past.index, r].mean())
            d.loc[now.index, "leader"] = d.loc[now.index, best].to_numpy()
    return d.rename(columns={"over_rule": "over_r"})


def col(rule):
    return "over_r" if rule == "over" else rule


def weeks_ahead(rows, rule):
    ahead = behind = 0
    for _, w in rows.groupby("week"):
        a, b = w[col(rule)].mean(), w["published"].mean()
        if a > b:
            ahead += 1
        elif a < b:
            behind += 1
    return ahead, behind


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = load(eng)
    if d.empty:
        raise SystemExit(f"no graded live rows for {SEASON}")
    d = add_rules(d)
    weeks = sorted(d["week"].unique())
    markets = sorted(d["market_code"].unique())
    k, n = int(d["published"].sum()), len(d)
    lo, hi = wilson(k, n)
    print(f"{SEASON}, weeks {weeks[0]} to {weeks[-1]}: {n:,} distinct graded "
          f"props, {k / n:.1%} correct [{lo:.1%}, {hi:.1%}]")
    print()

    print("WHERE IT WON AND LOST: market by side, best first")
    print(f"  {'market':<18}{'side':<7}{'props':>6}{'won':>8}{'needed':>9}"
          f"{'diff':>8}   95% interval on won")
    cells = []
    for (m, side), s in d.groupby(["market_code", "recommended_side"]):
        w = s["published"].mean()
        cells.append((w - s["needed"].mean(), m, side, len(s), w,
                      s["needed"].mean(), int(s["published"].sum())))
    for diff, m, side, cnt, w, need, won in sorted(cells, reverse=True):
        lo, hi = wilson(won, cnt)
        print(f"  {m:<18}{side:<7}{cnt:>6}{w:>8.1%}{need:>9.1%}{diff:>+8.1%}"
              f"   [{lo:.1%}, {hi:.1%}]")
    print()

    print("WHY: where our number sat. Medians, in the market's own units.")
    print(f"  {'market':<18}{'props':>6}{'took over':>11}{'went over':>11}"
          f"{'ours - line':>13}{'actual - line':>15}{'actual - ours':>15}"
          f"{'weeks low':>11}")
    for m in markets:
        s = d[d["market_code"] == m]
        low = sum(1 for _, w in s.groupby("week")
                  if (w["actual"] - w["p50"]).median() > 0)
        print(f"  {m:<18}{len(s):>6}{s['took_over'].mean():>11.1%}"
              f"{s['over'].mean():>11.1%}"
              f"{(s['p50'] - s['line']).median():>+13.2f}"
              f"{(s['actual'] - s['line']).median():>+15.2f}"
              f"{(s['actual'] - s['p50']).median():>+15.2f}"
              f"{low:>7} of {s['week'].nunique()}")
    print("  weeks low: weeks in which the outcome sat above our number more "
          "often than below.")
    print()

    print("WHAT WOULD HAVE HELPED: share correct under each way of choosing "
          "the side")
    print(f"  {'market':<18}{'props':>6}" + "".join(f"{r:>11}" for r in RULES)
          + "   weeks ahead of published / behind")
    for m in markets + ["ALL"]:
        s = d if m == "ALL" else d[d["market_code"] == m]
        line = f"  {m:<18}{len(s):>6}" + "".join(
            f"{s[col(r)].mean():>11.1%}" for r in RULES)
        tail = ", ".join(
            f"{r} {a}/{b}" for r in RULES[1:]
            for a, b in [weeks_ahead(s, r)])
        print(line + "   " + tail)
    print()
    used = d[d["level_used"]]
    if len(used):
        print(f"  level and leader had {MIN_PRIOR} earlier props to work from "
              f"on {len(used):,} of {len(d):,} props. On those alone:")
        print(f"  {'market':<18}{'props':>6}{'published':>11}{'level':>11}"
              f"{'leader':>11}   95% interval on level minus published")
        for m in markets + ["ALL"]:
            s = used if m == "ALL" else used[used["market_code"] == m]
            if len(s) < MIN_PRIOR:
                continue
            diff = s["level"].astype(float) - s["published"].astype(float)
            se = diff.std(ddof=1) / math.sqrt(len(s)) if len(s) > 1 else 0.0
            print(f"  {m:<18}{len(s):>6}{s['published'].mean():>11.1%}"
                  f"{s['level'].mean():>11.1%}{s['leader'].mean():>11.1%}"
                  f"   [{diff.mean() - 1.96 * se:+.1%}, "
                  f"{diff.mean() + 1.96 * se:+.1%}]")
        print()

    print("BY TIER, BY WEEK: share correct as stored (props)")
    print(f"  {'week':<6}" + "".join(f"{t:>16}" for t in TIERS))
    for w in weeks + ["all"]:
        s = d if w == "all" else d[d["week"] == w]
        out = f"  {str(w):<6}"
        for t in TIERS:
            c = s[s["edge_tier"] == t]
            out += (f"{c['published'].mean():>9.1%} ({len(c):>3})"
                    if len(c) else f"{'-':>16}")
        print(out)
    print()
    print("Under about 500 props in a row, read the interval and the weeks "
          "column, not the percentage.")


if __name__ == "__main__":
    main()
