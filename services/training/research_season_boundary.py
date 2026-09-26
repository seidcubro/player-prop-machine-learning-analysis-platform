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
            # All of last season, regular games only. The third option, and the
            # one the old window never had: December's games are what dragged
            # the board down, and a full-season average is not December, it is a
            # year of evidence compressed into one number.
            prev = ys[:s0][(seasons[:s0] == season - 1)]
            # The stand-in for a line: the player's own median across this
            # season, with the target game left out. Leaving it in made the
            # reference partly a function of the thing being called, which is
            # why the first version of this reported week-2 side rates below a
            # coin flip on a metric that cannot be worse than chance by luck.
            same = seasons == season
            others = np.concatenate([ys[s0:i], ys[i + 1:][same[i + 1:]]])
            rows.append({
                "player_id": pid, "position": p["position"].iloc[i],
                "season": season, "week": weeks[i], "n_cur": n_cur,
                "y": ys[i],
                "crossing": float(cross.mean()),
                "season_only": float(seas.mean()),
                "prev_full": float(prev.mean()) if len(prev) else np.nan,
                "prev_n": float(len(prev)),
                "ref": float(np.median(others)) if len(others) else np.nan,
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


K_GRID = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 9.0, 14.0, 25.0)
FIT_BEFORE = 2025


def blended(d, k):
    """This season shrunk toward all of last season, by how much of this there is.

    Falls back to the season-only figure for a player with no prior season,
    which is a rookie: there is nothing to shrink toward and pretending
    otherwise would hand him somebody else's average.
    """
    n = d["n_cur"].to_numpy(dtype=float)
    cur = d["season_only"].to_numpy(dtype=float)
    prev = d["prev_full"].to_numpy(dtype=float)
    out = (n * cur + k * prev) / (n + k)
    return np.where(np.isnan(prev), cur, out)


def fit_k(d):
    """Pick k on the earlier seasons only, score it on the later ones."""
    fit = d[d["season"] < FIT_BEFORE]
    if len(fit) < MIN_ROWS:
        return None, None
    best, best_mae = None, float("inf")
    for k in K_GRID:
        mae = float(np.abs(blended(fit, k) - fit["y"]).mean())
        if mae < best_mae:
            best, best_mae = k, mae
    return best, d[d["season"] >= FIT_BEFORE]


def main():
    print("Weeks 2-6, every season, rookies and new arrivals included.\n"
          "Only rows where the windows actually differ.\n"
          f"k for the blend is fitted on seasons before {FIT_BEFORE} and scored "
          f"on {FIT_BEFORE} and later.\n")
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

        k, d = fit_k(d)
        if k is None or d is None or len(d) < MIN_ROWS:
            print(f"{code}: not enough history either side of {FIT_BEFORE}\n")
            continue
        d = d.copy()
        d["blend"] = blended(d, k)

        print(f"{code}   ({len(d)} scored rows, k={k:g}, "
              f"{int(d['season'].min())}-{int(d['season'].max())})")
        print(f"{'':10}{'n':>7}{'cross':>9}{'season':>9}{'blend':>9}"
              f"{'blend vs cross':>16}{'side X':>9}{'side S':>8}{'side B':>8}")
        for wk in (*WEEKS, "all"):
            s = d if wk == "all" else d[d["week"] == wk]
            if len(s) < MIN_ROWS:
                continue
            c = float(np.abs(s["crossing"] - s["y"]).mean())
            o = float(np.abs(s["season_only"] - s["y"]).mean())
            b = float(np.abs(s["blend"] - s["y"]).mean())
            gain = (c - b) / c * 100 if c else 0.0
            label = "all" if wk == "all" else f"week {wk}"
            print(f"{label:<10}{len(s):>7}{c:>9.3f}{o:>9.3f}{b:>9.3f}"
                  f"{gain:>+15.1f}%"
                  f"{side_rate(s['crossing'], s['y'], s['ref']):>9.1%}"
                  f"{side_rate(s['season_only'], s['y'], s['ref']):>8.1%}"
                  f"{side_rate(s['blend'], s['y'], s['ref']):>8.1%}")
            if wk == "all":
                overall[code] = [len(s), gain, (c - o) / c * 100 if c else 0.0, k]
        print()

    print("=" * 72)
    print(f"{'market':<20}{'rows':>7}{'k':>6}{'season only':>13}{'blend':>9}")
    for code, v in overall.items():
        n, blend_gain, season_gain, k = v
        print(f"{code:<20}{n:>7}{k:>6g}{season_gain:>+12.1f}%{blend_gain:>+8.1f}%")
    print("\nBoth columns are versus the old cross-season window, positive means\n"
          "closer to the actual next game. 'season only' is what ships right now.\n"
          "'blend' is this season anchored to all of last season, never its tail.")


if __name__ == "__main__":
    main()
