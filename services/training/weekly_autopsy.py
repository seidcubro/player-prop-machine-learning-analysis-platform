"""Every week of the season side by side, so a pattern is visible as a pattern.

Seven separate autopsies produce seven stories and no conclusion. A thing that
is true in one week is noise; a thing that is true in six is the product. So
this prints one row per week for each question and lets the column be read
down.

The questions, in the order they matter:

  1. Are we right? Side accuracy per week, overall and per tier, against the
     52.4% a -110 price needs and the 55% the project is aiming at.
  2. Is elite actually elite? The whole promise of the board is that the tier
     ranks being right. If elite is not on top most weeks, it is decoration.
  3. Which side are we taking, and which side is landing? The board has a long
     history of taking unders because the median reads low. "We sat above the
     line on X% of picks, the outcome landed above it on Y%" is the fastest
     test of whether we are picking a side before reading a player.
  4. Where is the number wrong? Median against line against outcome, per
     market per week, so a bias shows up as a column of the same sign rather
     than one bad Sunday.
  5. Is it the same markets every week, or does it move? A market that is bad
     every week is a modelling problem. A market that is bad in alternate
     weeks is variance and should be left alone.

Weeks come from nfl_games, not from guessing at date ranges, so a Thursday and
the following Monday belong to the same week the way the schedule says.

Deduplicated to one row per distinct prop: the board writes a row per
bookmaker, so the same right-or-wrong answer is otherwise counted three times
and every week looks three times more certain than it is.

Read-only.

    docker compose run --rm training python weekly_autopsy.py
    docker compose run --rm -e SEASON=2026 -e SOURCE=live training python weekly_autopsy.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SEASON = int(os.getenv("SEASON", "2026"))
SOURCE = os.getenv("SOURCE", "live")
MIN_CELL = int(os.getenv("MIN_CELL", "15"))
BREAK_EVEN = 0.524


def load(eng):
    d = pd.read_sql(text("""
        SELECT r.market_code, r.game_date, r.player_name, r.line, r.actual,
               r.hit, r.edge_tier, r.recommended_side, r.win_prob,
               r.projection, r.projection_median AS p50,
               g.week
        FROM prop_edge_results r
        LEFT JOIN LATERAL (
            SELECT week FROM nfl_games n
            WHERE n.game_date = r.game_date AND n.season = :season
            LIMIT 1
        ) g ON TRUE
        WHERE r.source = :source
          AND r.hit IS NOT NULL AND r.line IS NOT NULL AND r.actual IS NOT NULL
          AND r.game_date >= :since
        ORDER BY r.game_date
    """), eng, params={"season": SEASON, "source": SOURCE,
                       "since": f"{SEASON}-08-01"})
    if d.empty:
        return d
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    d["over_hit"] = (d["actual"] > d["line"]).astype(float)
    d["took_over"] = (d["recommended_side"] == "over").astype(float)
    d["cluster"] = d["game_date"].astype(str)
    d["week"] = d["week"].fillna(-1).astype(int)
    return d


def ci(sub):
    w = sub["hit"].to_numpy()
    if len(w) < MIN_CELL:
        return None
    return S.clustered_bootstrap(
        lambda i: float(np.mean(w[i])), sub["cluster"].to_numpy(), n_boot=1200)


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = load(eng)
    if d.empty:
        raise SystemExit(f"no graded {SOURCE} rows for {SEASON}")
    weeks = [w for w in sorted(d["week"].unique()) if w > 0]
    print(f"{len(d):,} distinct graded props, {SOURCE}, season {SEASON}")
    print(f"weeks {weeks[0]} to {weeks[-1]}, break-even at -110 is {BREAK_EVEN:.1%}")
    print()

    # 1. accuracy per week
    print("ACCURACY BY WEEK")
    print(f"  {'week':<6}{'props':>7}{'correct':>9}  95% CI{'':<10}{'vs break-even':>15}")
    for w in weeks:
        g = d[d["week"] == w]
        c = ci(g)
        if c is None:
            print(f"  {w:<6}{len(g):>7}{'too few':>9}")
            continue
        print(f"  {w:<6}{len(g):>7}{c[0]:>9.1%}  [{c[1]:>5.1%},{c[2]:>5.1%}]"
              f"{c[0] - BREAK_EVEN:>15.1%}")
    c = ci(d)
    print(f"  {'all':<6}{len(d):>7}{c[0]:>9.1%}  [{c[1]:>5.1%},{c[2]:>5.1%}]"
          f"{c[0] - BREAK_EVEN:>15.1%}")
    print()

    # 2. is elite actually elite
    print("DOES THE TIER RANK BEING RIGHT (accuracy by tier, per week)")
    tiers = ["elite", "strong", "medium", "small"]
    print(f"  {'week':<6}" + "".join(f"{t:>20}" for t in tiers) + f"{'elite on top?':>15}")
    for w in weeks:
        g = d[d["week"] == w]
        cells, vals = [], {}
        for t in tiers:
            s = g[g["edge_tier"] == t]
            if len(s) < MIN_CELL:
                cells.append(f"{'-':>20}")
                continue
            vals[t] = float(s["hit"].mean())
            cells.append(f"{f'{vals[t]:.1%} (n={len(s)})':>20}")
        ok = ("yes" if vals and max(vals, key=vals.get) == "elite"
              else ("no" if vals else "-"))
        print(f"  {w:<6}" + "".join(cells) + f"{ok:>15}")
    print()

    # 3. which side we take against which side lands
    print("SIDE SELECTION (are we picking a side before we read the player)")
    print(f"  {'week':<6}{'props':>7}{'we took over':>14}{'landed over':>13}"
          f"{'gap':>8}{'over acc':>10}{'under acc':>11}")
    for w in weeks:
        g = d[d["week"] == w]
        if len(g) < MIN_CELL:
            continue
        ov = g[g["recommended_side"] == "over"]
        un = g[g["recommended_side"] == "under"]
        oa = f"{ov['hit'].mean():.1%}" if len(ov) >= MIN_CELL else "-"
        ua = f"{un['hit'].mean():.1%}" if len(un) >= MIN_CELL else "-"
        print(f"  {w:<6}{len(g):>7}{g['took_over'].mean():>14.0%}"
              f"{g['over_hit'].mean():>13.0%}"
              f"{g['took_over'].mean() - g['over_hit'].mean():>+8.0%}"
              f"{oa:>10}{ua:>11}")
    print()

    # 4. where the number is wrong
    print("WHERE THE NUMBER IS WRONG (median minus outcome, per market per week)")
    markets = sorted(d["market_code"].unique())
    print(f"  {'week':<6}" + "".join(f"{m[:9]:>10}" for m in markets))
    for w in weeks:
        g = d[d["week"] == w]
        cells = []
        for m in markets:
            s = g[g["market_code"] == m]
            cells.append(f"{(s['p50'] - s['actual']).median():>+10.1f}"
                         if len(s) >= MIN_CELL else f"{'-':>10}")
        print(f"  {w:<6}" + "".join(cells))
    cells = []
    for m in markets:
        s = d[d["market_code"] == m]
        cells.append(f"{(s['p50'] - s['actual']).median():>+10.1f}")
    print(f"  {'all':<6}" + "".join(cells))
    print("  negative means our median reads BELOW what happened")
    print()

    # 5. same markets every week, or moving
    print("ACCURACY BY MARKET BY WEEK")
    print(f"  {'week':<6}" + "".join(f"{m[:9]:>10}" for m in markets))
    for w in weeks:
        g = d[d["week"] == w]
        cells = []
        for m in markets:
            s = g[g["market_code"] == m]
            cells.append(f"{s['hit'].mean():>10.0%}" if len(s) >= MIN_CELL
                         else f"{'-':>10}")
        print(f"  {w:<6}" + "".join(cells))
    cells, counts = [], []
    for m in markets:
        s = d[d["market_code"] == m]
        cells.append(f"{s['hit'].mean():>10.1%}")
        counts.append(f"{len(s):>10}")
    print(f"  {'all':<6}" + "".join(cells))
    print(f"  {'n':<6}" + "".join(counts))
    print()

    # The consistency question, stated as a number rather than left to the eye.
    print("CONSISTENT OR NOISY (how many weeks each market was above break-even)")
    print(f"  {'market':<20}{'weeks >52.4%':>14}{'weeks scored':>14}"
          f"{'season':>9}{'spread across weeks':>21}")
    for m in markets:
        per = []
        for w in weeks:
            s = d[(d["week"] == w) & (d["market_code"] == m)]
            if len(s) >= MIN_CELL:
                per.append(float(s["hit"].mean()))
        if not per:
            continue
        above = sum(1 for x in per if x > BREAK_EVEN)
        s = d[d["market_code"] == m]
        print(f"  {m:<20}{above:>14}{len(per):>14}{s['hit'].mean():>9.1%}"
              f"{f'{min(per):.0%} to {max(per):.0%}':>21}")


if __name__ == "__main__":
    main()
