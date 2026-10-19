"""Does the game simulator beat the quantile ladder at calling a side?

This is the test our own note demanded and could not run. simulate.py was
written on 2026-09-15, validated against real team-mate correlations, and then
parked with: "Not yet wired into served edges. It is validated on correlation
structure but has not been shown to beat the quantile models on P(over)
calibration; that test needs graded results. Do not swap it in until it wins on
Brier." There were no graded results then. There are 9,844 now.

Why it matters more than it did in September. WalterPicks, which is doing the
same job commercially, publishes that it runs "10,000 simulations of each NFL &
NBA game" and sells "simulated projections and correlations". That is this
architecture: a model per mechanism -- team volume, then shares, then
per-touch efficiency -- composed by simulation, rather than one regression per
market with many features in it. Every market comes off the same simulated
games, so they are mutually consistent, correlated for parlays, and any
threshold is free.

Our stack is the other shape: an independent point model per market plus a
separately fitted quantile ladder bolted on for the distribution. That ladder
is where several of this project's recent problems live. Its coverage drifts
(rec_yds q10 covering 30.3% of outcomes against a target of 10%), its width is
what gap_iqr tried and failed to rank accuracy with, and it has no idea that a
quarterback's passing yards are the sum of his receivers'.

So the comparison is not academic. If the simulator calls sides better, the
four markets with no working confidence signal get a distribution that comes
from structure instead of a fitted curve.

HOW IT IS SCORED. For each graded prop, the game it belonged to is simulated
from the feature rows that ended on that date and that game's own closing
spread and total, and P(over) is the share of simulated games above the line.
Nothing from after kickoff enters, and the simulator has never seen the
outcome. Against it stands `win_prob` as the board actually published it.

Three measures, because they answer different questions:

    Brier      is the probability honest
    side       would it have picked the correct side more often
    log loss   the same as Brier but punishing confident mistakes harder

Read-only. Writes nothing, ships nothing. The note's rule stands: it does not
go near the board unless it wins here.

    docker compose run --rm -e DATES=8 training python backtest_simulator.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp
import simulate as sim

N_DATES = int(os.getenv("DATES", "8"))
SOURCE = os.getenv("SOURCE", "live")


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    graded = pd.read_sql(text("""
        SELECT r.market_code, r.game_date, r.player_id, r.player_name,
               r.line, r.actual, r.hit, r.win_prob, r.recommended_side
        FROM prop_edge_results r
        WHERE r.source = :s AND r.hit IS NOT NULL AND r.line IS NOT NULL
          AND r.actual IS NOT NULL AND r.win_prob IS NOT NULL
          AND r.market_code IN ('rec_yds','recs','rush_yds','rush_att',
                                'pass_yds','pass_completions','pass_att')
    """), eng, params={"s": SOURCE})
    if graded.empty:
        raise SystemExit(f"no graded {SOURCE} rows to score against")
    graded = graded.drop_duplicates(
        subset=["game_date", "player_name", "market_code", "line"])

    dates = sorted(graded["game_date"].unique())[-N_DATES:]
    print(f"simulating {len(dates)} game date(s) at {sim.N_SIMS:,} sims each")
    print(f"scoring against {SOURCE} picks")

    rng = np.random.default_rng(sim.SEED)
    rows = []
    for d in dates:
        slate = sim.load_slate(eng, as_of=d)
        if slate.empty:
            print(f"  {d}: no feature rows, skipped")
            continue
        draws = {}
        for (game_id, team), grp in slate.groupby(["game_id", "team"]):
            r0 = grp.iloc[0]
            total = float(r0["total_line"]) if pd.notna(r0["total_line"]) else 44.0
            spread = float(r0["spread_line"]) if pd.notna(r0["spread_line"]) else 0.0
            team_spread = spread if r0["team"] == r0["home_team"] else -spread
            implied = (total + team_spread) / 2.0
            try:
                draws.update(sim.simulate_team(rng, grp, implied, team_spread))
            except Exception as e:
                print(f"  {d} {team}: {type(e).__name__} {e}")
        day = graded[graded["game_date"] == d]
        got = 0
        for r in day.itertuples(index=False):
            s = draws.get((r.player_id, r.market_code))
            if s is None or len(s) == 0:
                continue
            rows.append({
                "game_date": d, "market_code": r.market_code,
                "line": r.line, "actual": r.actual,
                "over_hit": float(r.actual > r.line),
                "p_sim": float(np.mean(np.asarray(s) > r.line)),
                # win_prob is P(the side we took); convert to P(over) so the
                # two are the same quantity before anything is compared.
                "p_board": float(r.win_prob if r.recommended_side == "over"
                                 else 1.0 - r.win_prob),
            })
            got += 1
        print(f"  {d}: {len(slate):,} slate rows, {got} of {len(day)} props matched")

    if not rows:
        raise SystemExit("nothing matched; the simulator produced no draws for "
                         "any graded prop")
    d = pd.DataFrame(rows)
    d["cluster"] = d["game_date"].astype(str)
    print()
    print(f"{len(d):,} props scored both ways")
    print()
    print(f"  {'population':<16}{'n':>7}{'Brier sim':>11}{'Brier brd':>11}"
          f"{'side sim':>10}{'side brd':>10}{'logloss sim':>13}{'logloss brd':>13}")

    def line(sub, label):
        if len(sub) < 40:
            return
        y = sub["over_hit"].to_numpy()
        ps, pb = sub["p_sim"].to_numpy(), sub["p_board"].to_numpy()
        side_s = float(np.mean((ps > 0.5) == (y > 0.5)))
        side_b = float(np.mean((pb > 0.5) == (y > 0.5)))
        print(f"  {label:<16}{len(sub):>7}{brier(ps, y):>11.4f}{brier(pb, y):>11.4f}"
              f"{side_s:>10.1%}{side_b:>10.1%}"
              f"{logloss(ps, y):>13.4f}{logloss(pb, y):>13.4f}")

    line(d, "everything")
    for m in sorted(d["market_code"].unique()):
        line(d[d["market_code"] == m], f"  {m}")

    # The difference, with an interval, because two numbers side by side are
    # not a result.
    y = d["over_hit"].to_numpy()
    diff = ((d["p_board"].to_numpy() - y) ** 2) - ((d["p_sim"].to_numpy() - y) ** 2)
    r, lo, hi = S.clustered_bootstrap(
        lambda i: float(np.mean(diff[i])), d["cluster"].to_numpy(), n_boot=2000)
    print()
    print(f"  Brier improvement from the simulator: {r:+.4f} [{lo:+.4f}, {hi:+.4f}]")
    print("  Positive means the simulator is better calibrated. The interval")
    print("  has to clear zero before this goes anywhere near the board, which")
    print("  is the rule simulate.py was parked under in September.")
    sd = float(np.mean((d["p_sim"] > 0.5) == (y > 0.5)))
    sb = float(np.mean((d["p_board"] > 0.5) == (y > 0.5)))
    print(f"  side accuracy: simulator {sd:.1%}, board {sb:.1%}, "
          f"difference {sd - sb:+.1%}")


if __name__ == "__main__":
    main()
