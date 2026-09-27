"""When a pick loses, what happened on the field?

The backtest says the gap system wins. It does not say why it loses the 39% of
the time it does, and "the model was wrong" is not an answer anyone can act on.

A prop on yardage is volume times efficiency. A receiver goes over 44.5 because
he was thrown at more than we expected, or because he did more with the same
looks, and those are different failures with different fixes. The first is a role
we misread and is worth chasing. The second is a fifty-yard catch-and-run, which
is variance and is not.

So every losing pick is attributed:

    got more work      his touches beat his recent norm by a clear margin, and
                       that alone explains the miss. A role change, a teammate
                       out, a game plan we did not see.
    same work, more    touches near his norm, but far more yards per touch.
    with them          Broken tackles and blown coverage. Variance.
    both               volume and efficiency together. Usually a blowout that
                       turned into a track meet.
    neither            he did what we expected and the line was simply in the
                       wrong place, which means the line was right and we were
                       not.

Then the same split on the picks that won, because a factor that shows up equally
in wins and losses explains nothing. Only the difference between the two columns
is a finding.

Context, too: blowouts, shootouts and games that stayed close, since game script
is the football reason a projection built on a normal afternoon falls apart.

Uses the same walk-forward picks as backtest_gap_system, so the population is the
one the board would actually have published.
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import backtest_gap_system as bt

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
# The touch that drives each market, and the stat the prop is on.
VOLUME = {
    "recs": "targets", "rec_yds": "targets",
    "rush_yds": "carries", "rush_att": "carries",
}
# How far past his norm counts as "more work". A fifth of a standard deviation
# either way is noise; this is deliberately generous so the bucket means
# something when it fires.
SHOCK = 1.25


def context(eng):
    """Actual usage and the game's shape, per player-game."""
    g = pd.read_sql(text("""
        SELECT s.player_id, s.game_date, s.team, s.opponent,
               COALESCE(s.targets, 0)::float8   AS targets,
               COALESCE(s.carries, 0)::float8   AS carries,
               COALESCE(s.receptions, 0)::float8 AS receptions,
               COALESCE(s.receiving_yards, 0)::float8 AS rec_yards,
               COALESCE(s.rushing_yards, 0)::float8   AS rush_yards,
               ng.home_team, ng.away_team, ng.home_score, ng.away_score,
               ng.total_line
        FROM player_game_stats_app s
        LEFT JOIN nfl_games ng
               ON ng.season = s.season AND ng.week = s.week
              AND (ng.home_team = s.team OR ng.away_team = s.team)
        WHERE s.season_type = 'REG'
    """), eng)
    g["game_date"] = pd.to_datetime(g["game_date"])
    pts = g["home_score"].fillna(0) + g["away_score"].fillna(0)
    margin = (g["home_score"].fillna(0) - g["away_score"].fillna(0)).abs()
    g["margin"] = margin
    g["points"] = pts
    g["shape"] = np.where(
        margin >= 17, "blowout",
        np.where(pts >= g["total_line"].fillna(44) + 7, "shootout",
                 np.where(pts <= g["total_line"].fillna(44) - 7, "slog",
                          "as expected")))
    return g


def main():
    eng = create_engine(DATABASE_URL, future=True)
    print("Rebuilding the walk-forward picks (this is the slow part)...\n")
    d = bt.build()
    if d.empty:
        raise SystemExit("no picks to autopsy")
    d = d[d["tier"].isin(["elite", "strong", "medium"])].copy()
    d = d[d["market_code"].isin(VOLUME)]

    ctx = context(eng)
    d = d.merge(ctx, left_on=["player_id", "as_of_game_date"],
                right_on=["player_id", "game_date"], how="left",
                suffixes=("", "_ctx"))

    # His recent norm for the touch that matters, from the same window the
    # features used, so "more work than expected" means more than the model saw.
    d["vol_stat"] = d["market_code"].map(VOLUME)
    d["actual_volume"] = np.where(d["vol_stat"] == "targets",
                                  d["targets"], d["carries"])
    aux = pd.to_numeric(d["aux_mean"], errors="coerce")
    d["expected_volume"] = aux
    d["volume_ratio"] = d["actual_volume"] / d["expected_volume"].replace(0, np.nan)

    # Yards per touch, actual against the norm implied by the projection.
    yards = np.where(d["market_code"].isin(["rec_yds", "recs"]),
                     d["rec_yards"], d["rush_yards"])
    d["actual_yards"] = yards
    d["actual_eff"] = yards / d["actual_volume"].replace(0, np.nan)
    d["expected_eff"] = d["pred"] / d["expected_volume"].replace(0, np.nan)
    d["eff_ratio"] = d["actual_eff"] / d["expected_eff"].replace(0, np.nan)

    vol_up = d["volume_ratio"] >= SHOCK
    vol_dn = d["volume_ratio"] <= 1 / SHOCK
    eff_up = d["eff_ratio"] >= SHOCK
    eff_dn = d["eff_ratio"] <= 1 / SHOCK
    # For an under, "against us" means more; for an over, less.
    over = d["side"] == "over"
    vol_against = np.where(over, vol_dn, vol_up)
    eff_against = np.where(over, eff_dn, eff_up)
    d["blame"] = np.select(
        [vol_against & eff_against, vol_against & ~eff_against,
         ~vol_against & eff_against],
        ["both: more work and bigger plays",
         "got more work than we expected",
         "same work, bigger plays"],
        default="neither: he did what we expected")

    d = d[d["volume_ratio"].notna() & d["eff_ratio"].notna()]
    lost = d[d["won"] < 1]
    won = d[d["won"] > 0]
    print(f"{len(d)} published picks with usable usage data: "
          f"{len(won)} won, {len(lost)} lost ({len(won) / len(d):.1%})\n")

    print("=" * 78)
    print("1. Why the losses happened, against how often the same thing happens "
          "when we win\n")
    print(f"  {'':<36}{'of losses':>11}{'of wins':>10}{'tells us':>12}")
    for b in ["got more work than we expected", "same work, bigger plays",
              "both: more work and bigger plays",
              "neither: he did what we expected"]:
        pl = (lost["blame"] == b).mean() if len(lost) else 0
        pw = (won["blame"] == b).mean() if len(won) else 0
        flag = "  <- real" if pl - pw > 0.05 else ""
        print(f"  {b:<36}{pl:>10.1%}{pw:>10.1%}{pl - pw:>+11.1%}{flag}")

    print("\n" + "=" * 78)
    print("2. The same thing, per market\n")
    for code, g in d.groupby("market_code"):
        gl, gw = g[g["won"] < 1], g[g["won"] > 0]
        if len(gl) < 30:
            continue
        print(f"  {code}  ({len(g)} picks, {len(gw) / len(g):.1%} won)")
        for b in ["got more work than we expected", "same work, bigger plays",
                  "both: more work and bigger plays"]:
            pl = (gl["blame"] == b).mean()
            pw = (gw["blame"] == b).mean() if len(gw) else 0
            print(f"      {b:<34}{pl:>8.1%} of losses vs {pw:>6.1%} of wins"
                  f"{pl - pw:>+9.1%}")
        print()

    print("=" * 78)
    print("3. What kind of game it was\n")
    print(f"  {'':<16}{'picks':>7}{'hit':>24}{'break':>8}{'edge':>8}")
    for shape, g in d.groupby("shape"):
        if len(g) < 60:
            continue
        w = g["won"].to_numpy()
        rate, lo, hi = S.clustered_bootstrap(
            lambda idx, w=w: float(np.mean(w[idx])), g["cluster"].to_numpy(),
            n_boot=1500)
        be = g["breakeven"].mean()
        print(f"  {shape:<16}{len(g):>7}   {rate:>6.1%} [{lo:>5.1%}, {hi:>5.1%}]"
              f"{be:>8.1%}{rate - be:>+8.1%}")

    print("\n" + "=" * 78)
    print("4. The worst cell we know about: receiving yards\n")
    ry = d[d["market_code"] == "rec_yds"]
    if len(ry) >= 60:
        ry = ry.copy()
        ry["band"] = pd.cut(ry["line"], [-1, 25, 45, 70, 1000],
                            labels=["<=25 (WR3, TE2)", "26-45 (WR2, TE1)",
                                    "46-70 (WR1)", "70+ (alpha)"])
        print(f"  {'':<20}{'picks':>7}{'hit':>10}{'break':>8}{'edge':>8}")
        for band, g in ry.groupby("band", observed=True):
            if len(g) < 30:
                continue
            print(f"  {str(band):<20}{len(g):>7}{g['won'].mean():>10.1%}"
                  f"{g['breakeven'].mean():>8.1%}"
                  f"{g['won'].mean() - g['breakeven'].mean():>+8.1%}")


if __name__ == "__main__":
    main()
