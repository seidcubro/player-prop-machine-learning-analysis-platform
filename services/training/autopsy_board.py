"""Grade a span of the live board against the book and the box score.

grade_edges.py settles the picks that were published. This is the wider
question: of every prop a book priced and we had a live projection for, which
side would each candidate selection statistic have taken, and how did it do.

That distinction is the whole point. A board that publishes 73 picks and goes
42.5% can fail in two very different ways, and only one of them is bad luck:

    the projections were wrong          nothing to do but improve the model
    the projections were fine and the   a selection bug, and much cheaper
    rule picked the wrong side of them

Week 3 of 2026 was the second. The point projection sat 0.30 above the line on
receiving yards while the quantile median sat 7.11 below it, and the board
ranks on the median, so 70 of 73 published picks were unders on a slate that
went over half the time.

Usage:

    python autopsy_board.py 2026-09-24 2026-09-28        one week
    python autopsy_board.py 2026-09-09 2026-09-28        the season so far

Reads player_projection_history, odds_snapshots and player_game_stats. Rows
reconstructed by backfill_projection_history.py are excluded by default, since
those were produced by today's models over games they were trained on; pass
INCLUDE_RECONSTRUCTED=1 to keep them and read the result accordingly.
"""

from __future__ import annotations

import os
import sys

import gap_tier
import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

N_BOOT = int(os.getenv("N_BOOT", "2000"))
PUBLISHED = ("elite", "strong", "medium")

STAT = {
    "rec_yds": "receiving_yards", "recs": "receptions", "rec_td": "receiving_tds",
    "rush_yds": "rushing_yards", "rush_att": "carries", "rush_td": "rushing_tds",
    "pass_yds": "passing_yards", "pass_att": "attempts",
    "pass_completions": "completions", "pass_td": "passing_tds",
}
ODDS_KEY = {
    "recs": "player_receptions", "rec_yds": "player_reception_yds",
    "rec_td": "player_reception_tds", "rush_yds": "player_rush_yds",
    "rush_att": "player_rush_attempts", "rush_td": "player_rush_tds",
    "pass_yds": "player_pass_yds", "pass_att": "player_pass_attempts",
    "pass_completions": "player_pass_completions", "pass_td": "player_pass_tds",
}


def load(engine, lo: str, hi: str) -> pd.DataFrame:
    proj = pd.read_sql(text("""
        SELECT player_id, player_name, team, opponent, position, market_code,
               game_date, projection, p10, p25, p50, p75, p90, depth_rank,
               projected_at, model_name
        FROM player_projection_history
        WHERE game_date BETWEEN :lo AND :hi
    """), engine, params={"lo": lo, "hi": hi})
    if proj.empty:
        raise SystemExit(f"no projections recorded for {lo}..{hi}")
    # The last number published before kickoff is the one that was live.
    proj = (proj.sort_values("projected_at")
                .groupby(["player_id", "market_code", "game_date"], as_index=False)
                .last())

    odds = pd.read_sql(text("""
        SELECT player_name, market_key, outcome_name, line, price_american,
               (commence_time AT TIME ZONE 'America/New_York')::date AS d
        FROM odds_snapshots
        WHERE (commence_time AT TIME ZONE 'America/New_York')::date
              BETWEEN :lo AND :hi
          AND line IS NOT NULL AND price_american IS NOT NULL
    """), engine, params={"lo": lo, "hi": hi})
    if odds.empty:
        raise SystemExit("no odds snapshots in that range")

    sd = pd.read_sql(text("""
        SELECT DISTINCT ON (f.player_id, m.code)
               f.player_id, m.code AS market_code, f.stddev
        FROM player_market_features f
        JOIN prop_markets m ON m.id = f.market_id
        LEFT JOIN active_models am ON am.market_id = m.id
        WHERE f.lookback = COALESCE(am.lookback, 5)
          AND f.as_of_game_date <= :hi
        ORDER BY f.player_id, m.code, f.as_of_game_date DESC
    """), engine, params={"hi": hi})

    act = pd.read_sql(text("""
        SELECT s.player_id, s.game_date, s.week,
               s.receiving_yards, s.receptions, s.rushing_yards, s.carries,
               s.passing_yards, s.attempts, s.completions,
               s.passing_tds, s.receiving_tds, s.rushing_tds,
               sc.offense_pct
        FROM player_game_stats s
        LEFT JOIN snap_counts sc
          ON sc.player_id = s.player_id AND sc.game_id = s.game_id
        WHERE s.game_date BETWEEN :lo AND :hi
    """), engine, params={"lo": lo, "hi": hi})

    odds["side"] = odds.outcome_name.str.strip().str.lower()
    odds = odds[odds.side.isin(["over", "under"])]
    # The line most books agreed on, and the best price on each side.
    mid = odds.groupby(["player_name", "market_key", "d"], as_index=False).line.median()
    best = (odds.groupby(["player_name", "market_key", "d", "side"], as_index=False)
                .price_american.max()
                .pivot_table(index=["player_name", "market_key", "d"],
                             columns="side", values="price_american").reset_index())
    mkt = mid.merge(best, on=["player_name", "market_key", "d"])
    mkt["market_code"] = mkt.market_key.map({v: k for k, v in ODDS_KEY.items()})
    mkt = mkt[mkt.market_code.notna()]

    for f in (proj, mkt, act):
        for c in ("game_date", "d"):
            if c in f.columns:
                f[c] = pd.to_datetime(f[c])

    d = proj.merge(mkt, left_on=["player_name", "market_code", "game_date"],
                   right_on=["player_name", "market_code", "d"], how="inner")
    d = d.merge(sd, on=["player_id", "market_code"], how="left")
    idx = act.set_index(["player_id", "game_date"])

    def look(row, col):
        try:
            v = idx.loc[(row.player_id, row.game_date), col]
        except KeyError:
            return np.nan
        return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)

    d["actual"] = [look(r, STAT[r.market_code]) for r in d.itertuples()]
    d["snap_pct"] = [look(r, "offense_pct") for r in d.itertuples()]
    before_outcome = len(d)
    d = d[d.actual.notna()].copy()
    if d.empty:
        # Loudly, because the silent version of this printed an empty table and
        # a row of nan% and looked like an answer. Week 4 of 2026 went four days
        # with no box scores and that output is what it produced.
        raise SystemExit("\n".join([
            f"nothing to grade between {lo} and {hi}.",
            f"  projections recorded : {len(proj):,}",
            f"  priced props matched : {before_outcome:,}",
            "  with a box score     : 0",
            "",
            "A box score count of zero means the ingest has not pulled those",
            "games yet. player_game_stats is refreshed only by the full ingest,",
            "which --daily runs; --board and --refresh deliberately skip it.",
            "Run priorline@daily, confirm it did not fail, then try again.",
        ]))
    if os.getenv("INCLUDE_RECONSTRUCTED") != "1":
        before = len(d)
        d = d[d.projected_at.dt.date >= (d.game_date.dt.date - pd.Timedelta(days=10))]
        if len(d) < before:
            print(f"  dropped {before - len(d)} reconstructed rows "
                  f"(set INCLUDE_RECONSTRUCTED=1 to keep them)")
    # Props in the same game share an outcome, so they resample together.
    d["cluster"] = (d.game_date.dt.strftime("%Y-%m-%d") + "|"
                    + d.team.fillna("?").astype(str))
    return d


def tiers(d: pd.DataFrame, centre) -> pd.DataFrame:
    z = [gap_tier.gap_z(m, l, a, b, c, window_sd=w)
         for m, l, a, b, c, w in zip(centre, d.line, d.p25, d.p75,
                                     d.market_code, d.stddev)]
    t = pd.DataFrame({"z": z}, index=d.index)
    t["tier"] = [gap_tier.tier_for(v, c) for v, c in zip(t.z, d.market_code)]
    t["side"] = np.where(t.z > 0, "over", "under")
    t["price"] = np.where(t.side == "over", d["over"], d["under"])
    ok = t.price.notna() & t.z.notna()
    t, dd = t[ok], d[ok]
    push = dd.actual == dd.line
    t["won"] = np.where(t.side == "over", dd.actual > dd.line, dd.actual < dd.line)
    dec = np.where(t.price > 0, 1 + t.price / 100.0, 1 + 100.0 / np.abs(t.price))
    t["profit"] = np.where(push, 0.0, np.where(t.won, dec - 1, -1.0))
    t["breakeven"] = 1.0 / dec
    for c in ("cluster", "market_code", "position", "player_name", "actual",
              "line", "p50", "projection", "snap_pct", "stddev"):
        t[c] = dd[c].values
    return t


def line(label, g):
    if not len(g):
        return f"  {label:<24}{'-':>7}"
    w = g.won.to_numpy()
    if len(g) >= 30:
        r, lo, hi = S.clustered_bootstrap(lambda i, w=w: float(np.mean(w[i])),
                                          g.cluster.to_numpy(), n_boot=N_BOOT)
        ci = f"[{lo:>5.1%},{hi:>6.1%}]"
    else:
        r, ci = float(w.mean()), ""
    return (f"  {label:<24}{len(g):>7}{r:>9.1%}  {ci:<17}"
            f"{g.breakeven.mean():>10.1%}{g.profit.mean():>+9.1%}"
            f"{g.profit.sum():>+9.2f}{(g.side == 'over').mean():>8.0%}")


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    lo, hi = sys.argv[1], sys.argv[2]
    engine = create_engine(bp.DATABASE_URL, future=True)
    d = load(engine, lo, hi)
    wks = ""
    print(f"\n{len(d):,} priced props with a live projection and a box score, "
          f"{lo} to {hi}{wks}\n")

    live = tiers(d, d.p50)
    alt = tiers(d, d.projection)

    hdr = (f"  {'':<24}{'picks':>7}{'hit':>9}  {'95% CI':<17}"
           f"{'break-even':>10}{'ROI':>9}{'units':>9}{'overs':>8}")
    print("=" * 100)
    print("THE BOARD AS PUBLISHED\n")
    print(hdr)
    pub = live[live.tier.isin(PUBLISHED)]
    print(line("ALL PUBLISHED", pub))
    print()
    for t in PUBLISHED:
        print(line(t, pub[pub.tier == t]))
    print("\n  by market:")
    for c, g in pub.groupby("market_code"):
        print(line(c, g))
    print("\n  by position:")
    for c, g in pub.groupby("position"):
        print(line(c, g))

    print("\n" + "=" * 100)
    print("WHERE THE PROJECTIONS ACTUALLY LANDED\n")
    print(f"  {'market':<18}{'n':>5}{'projection':>12}{'median':>9}{'line':>9}"
          f"{'actual':>9}{'proj-act':>10}{'p50-act':>10}")
    for c, g in d.groupby("market_code"):
        if len(g) < 8:
            continue
        print(f"  {c:<18}{len(g):>5}{g.projection.mean():>12.1f}{g.p50.mean():>9.1f}"
              f"{g.line.mean():>9.1f}{g.actual.mean():>9.1f}"
              f"{(g.projection - g.actual).mean():>+10.1f}"
              f"{(g.p50 - g.actual).mean():>+10.1f}")
    print(f"\n  we sat above the line on   projection {(d.projection > d.line).mean():.0%}"
          f"   median {(d.p50 > d.line).mean():.0%}")
    print(f"  the outcome landed above it {(d.actual > d.line).mean():>15.0%}")
    print("\n  Those three numbers are the health check. When the middle one is far")
    print("  from the last one, the board is picking a side before it reads a")
    print("  player, and no amount of model work fixes that.")

    print("\n" + "=" * 100)
    print("WHAT IF THE GAP WERE MEASURED FROM THE POINT PROJECTION INSTEAD\n")
    print(hdr)
    print(line("ranked on the median", live[live.tier.isin(PUBLISHED)]))
    print(line("ranked on projection", alt[alt.tier.isin(PUBLISHED)]))

    print("\n" + "=" * 100)
    print("THE TEN WORST MISSES\n")
    w = d.assign(miss=d.p50 - d.actual).nsmallest(10, "miss")
    print(w[["player_name", "position", "market_code", "projection", "p50",
             "line", "actual", "snap_pct"]].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
