"""Which window predicts the next game better: this season, or the last five?

The window used to stop at the start of a career, so for any player with fewer
than five games this season it reached into last December and weighted it equally
with the games that describe who he is now. It now stops at the start of the
season. This measures what that is worth.

Model-free on purpose. Both candidates are plain averages of the same box scores,
scored against the same outcomes, so nothing here depends on which model family
is live or on how a feature matrix happens to be assembled. If the season-only
window is the better estimator of the next game, it is better before any model
sees it, and if it is not, no model is going to rescue it.

    crossing     mean of the last five games, seasons mixed. The old window.
    season       mean of this season's games only, up to five. The new one.
    position     the position's own average, as a floor: any window that cannot
                 beat it is not carrying information about the player.

Judged on Weeks 2 to 6, where the two differ at all, across every season with
enough history, and reported per week so a Week 2 answer cannot hide inside a
Week 6 one. Two metrics:

    MAE          how close the estimate is
    side         how often it calls the right side of the player's own
                 season-long median, which is the closest model-free stand-in
                 for calling the right side of a line

Rookies, fringe players and men on new teams are all in. The previous version of
this question excluded them (`research_early_season.py`, MIN_PREV_GAMES = 6) and
returned a confident no, which is how the defect survived to Week 3 of 2026: that
filter removes the entire population a season-boundary correction exists for.

No holdout split, because there is nothing being fitted. Two estimators are
compared on every row that exists.
"""

import os
from collections import defaultdict

import numpy as np
import pandas as pd

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")

import train as tr  # noqa: E402  (env must be set before import)
from psycopg2.extras import RealDictCursor  # noqa: E402

LOOKBACK = 5
WEEKS = (2, 3, 4, 5, 6)
MIN_ROWS = 40


def markets():
    """Every market with its own stat column, read from the table.

    Not written down here: two research scripts wrote this mapping down and
    disagree with each other about passing attempts, and jobs.py resolves it
    from prop_markets for the same reason.
    """
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT code, stat_field, eligible_positions FROM prop_markets "
            "WHERE stat_field IS NOT NULL ORDER BY code")
        return cur.fetchall()


def load_games(stat_field: str, positions) -> pd.DataFrame:
    if not stat_field.isidentifier():
        raise SystemExit(f"refusing to interpolate {stat_field!r} into SQL")
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT player_id, position, season, week, season_type, game_date,
                   COALESCE({stat_field}, 0)::float8 AS y
            FROM player_game_stats_app
            WHERE game_date IS NOT NULL AND season_type = 'REG'
              AND week IS NOT NULL
            ORDER BY player_id, game_date
            """
        )
        g = pd.DataFrame(cur.fetchall())
    if g.empty:
        return g
    if positions:
        g = g[g["position"].isin(list(positions))]
    return g


def evaluate(g: pd.DataFrame):
    """One row per player-game in WEEKS, with both estimates and the outcome."""
    # The position floor, computed per season from that season's own games, so it
    # is not a number from the future.
    pos_mean = (g.groupby(["position", "season"])["y"].mean()
                 .rename("pos_mean").reset_index())
    rows = []
    for pid, p in g.groupby("player_id", sort=False):
        p = p.sort_values("game_date")
        ys = p["y"].to_numpy(dtype=float)
        seasons = p["season"].to_numpy()
        weeks = p["week"].to_numpy()
        for i in range(1, len(p)):
            if weeks[i] not in WEEKS:
                continue
            season = seasons[i]
            # Where this season starts, walking back as the feature builder does.
            s0 = i
            while s0 > 0 and seasons[s0 - 1] == season:
                s0 -= 1
            n_cur = i - s0
            if n_cur < 1:
                # Week 1, or his first game of the year: neither window has
                # anything and the new one declines to project him at all.
                continue
            cross = ys[max(0, i - LOOKBACK):i]
            seas = ys[max(s0, i - LOOKBACK):i]
            if len(cross) == len(seas):
                # The windows are identical, so this row cannot tell them apart.
                continue
            rows.append({
                "player_id": pid, "position": p["position"].iloc[i],
                "season": season, "week": weeks[i], "n_cur": n_cur,
                "y": ys[i],
                "crossing": float(cross.mean()),
                "season_only": float(seas.mean()),
                # The player's own median across this season, as the stand-in
                # for a line. Uses the whole season, which is legitimate here:
                # it is the thing being called, not an input to the call.
                "ref": float(np.median(ys[s0:][seasons[s0:] == season])),
            })
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    return d.merge(pos_mean, on=["position", "season"], how="left")


def side_rate(est, y, ref):
    """How often the estimate calls the right side of the reference."""
    call = np.sign(np.asarray(est) - np.asarray(ref))
    truth = np.sign(np.asarray(y) - np.asarray(ref))
    live = (call != 0) & (truth != 0)
    if not live.any():
        return float("nan")
    return float((call[live] == truth[live]).mean())


def main():
    print("Weeks 2-6, every season, rookies and new arrivals included.\n"
          "Only rows where the two windows actually differ.\n")
    overall = defaultdict(lambda: [0, 0.0, 0.0])
    for m in markets():
        code = m["code"]
        g = load_games(str(m["stat_field"]), m.get("eligible_positions"))
        if g.empty:
            print(f"{code}: no rows\n")
            continue
        d = evaluate(g)
        if len(d) < MIN_ROWS:
            print(f"{code}: {len(d)} comparable rows, too few\n")
            continue

        print(f"{code}   ({len(d)} rows, {d['season'].min()}-{d['season'].max()})")
        print(f"{'':10}{'n':>7}{'MAE cross':>11}{'MAE season':>11}{'gain':>8}"
              f"{'side cross':>12}{'side season':>13}")
        for wk in (*WEEKS, "all"):
            s = d if wk == "all" else d[d["week"] == wk]
            if len(s) < MIN_ROWS:
                continue
            c_mae = float(np.abs(s["crossing"] - s["y"]).mean())
            s_mae = float(np.abs(s["season_only"] - s["y"]).mean())
            gain = (c_mae - s_mae) / c_mae * 100 if c_mae else 0.0
            c_sd = side_rate(s["crossing"], s["y"], s["ref"])
            s_sd = side_rate(s["season_only"], s["y"], s["ref"])
            label = "all" if wk == "all" else f"week {wk}"
            print(f"{label:<10}{len(s):>7}{c_mae:>11.3f}{s_mae:>11.3f}"
                  f"{gain:>+7.1f}%{c_sd:>11.1%}{s_sd:>12.1%}")
            if wk == "all":
                overall[code] = [len(s), gain, (s_sd - c_sd) * 100]
        p_mae = float(np.abs(d["pos_mean"] - d["y"]).mean())
        print(f"{'position':<10}{len(d):>7}{p_mae:>11.3f}"
              f"{'':>11}{'':>8}   (the floor both must beat)\n")

    print("=" * 66)
    print(f"{'market':<20}{'rows':>7}{'MAE gain':>11}{'side pts':>10}")
    for code, (n, gain, side) in overall.items():
        print(f"{code:<20}{n:>7}{gain:>+10.1f}%{side:>+9.1f}")
    print("\nA positive MAE gain means the season-only window is closer. "
          "Side points\nare percentage points of correct side calls, which is "
          "the metric the board\nis actually judged on.")


if __name__ == "__main__":
    main()
