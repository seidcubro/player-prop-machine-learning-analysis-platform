"""Should this season's first few games count for more than last season's?

The features are five-game windows, and a window does not know where a season
ends. In Week 2 it holds one game from this season and four from last
December, weighted equally: a player's new role, new coordinator or new
quarterback shows up in one game out of five, and his old role in the other
four. Jaylen Warren caught five passes in Week 1 and was projected for 2.3,
because the window still held a December with a 0 and a 1 in it.

Whether that is wrong is an empirical question. Week 1 is one noisy game, and a
full season of history is a lot of evidence. So this tests, on box scores, for
player-games in Weeks 2-5 (one to four games of the current season behind
them):

    A  the last five games, seasons mixed, equally weighted. What the features
       effectively do now.
    W  the same five games with the current season's weighted w times as much.
       w = 1 is A.
    S  this season's average shrunk toward last season's full-season average:
       (n * this season + k * last season) / (n + k). All of last season, not
       just its last few games.

w and k are fitted per market on 2023 and 2024 and scored on 2025. Nothing is
written; this decides whether a correction is worth building.

Verdict, September 2026: not built. Against a plain five-game average, S wins
clearly (receptions +7.3%, receiving yards +6.0%, rush attempts +9.2%, pass
attempts +9.6%). Against the model, it does not: applying the S-over-A ratio to
the model's own walk-forward projections for the same 2025 games moved
receptions +0.4%, receiving yards -0.4%, rush yards -1.9% and rush attempts
-10.1%. The model is not a plain average. Depth rank, the Vegas total and the
opponent already carry most of what "this season is different" means, and the
correction counts it a second time. Run this again if the features change;
the second test is the one that decides.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)

STATS = {
    "recs": "receptions",
    "rec_yds": "receiving_yards",
    "rush_att": "carries",
    "rush_yds": "rushing_yards",
    "pass_att": "attempts",
    "pass_yds": "passing_yards",
}
POSITIONS = {
    "recs": ("WR", "TE", "RB"), "rec_yds": ("WR", "TE", "RB"),
    "rush_att": ("RB",), "rush_yds": ("RB",),
    "pass_att": ("QB",), "pass_yds": ("QB",),
}
WINDOW = 5
W_GRID = np.array([0.5, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 10.0])
K_GRID = np.array([0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 20.0, 50.0])
# Only players whose last season says they have a role: enough games to have a
# season average, so a rookie or a fringe player does not decide the fit.
MIN_PREV_GAMES = 6


def load(engine):
    cols = ", ".join(f"COALESCE({c}, 0) AS {c}" for c in set(STATS.values()))
    return pd.read_sql(
        text(f"""
            SELECT player_id, season, week, game_date, position, {cols}
            FROM player_game_stats_app
            WHERE season_type = 'REG'
        """),
        engine,
    ).sort_values(["player_id", "game_date"]).reset_index(drop=True)


def frame(df, stat):
    """One row per player-game in Weeks 2-5, with what was known before it."""
    rows = []
    # player_id and game_date carried so the model's own walk-forward
    # projection for the same game can be joined on below.
    for pid, g in df.groupby("player_id", sort=False):
        g = g.reset_index(drop=True)
        for i in range(len(g)):
            s, w = g.at[i, "season"], g.at[i, "week"]
            if w < 2 or w > 5:
                continue
            prior = g.iloc[:i]
            cur = prior[prior["season"] == s][stat].to_numpy()
            prev = prior[prior["season"] == s - 1][stat].to_numpy()
            if len(cur) == 0 or len(prev) < MIN_PREV_GAMES:
                continue
            window = prior.tail(WINDOW)
            is_cur = (window["season"] == s).to_numpy()
            rows.append({
                "player_id": pid, "game_date": g.at[i, "game_date"],
                "season": s, "week": w, "position": g.at[i, "position"],
                "actual": g.at[i, stat],
                "win_vals": window[stat].to_numpy(), "win_cur": is_cur,
                "cur_mean": cur.mean(), "n_cur": len(cur),
                "prev_mean": prev.mean(),
            })
    return pd.DataFrame(rows)


def pred_w(f, w):
    return np.array([
        np.average(v, weights=np.where(c, w, 1.0))
        for v, c in zip(f["win_vals"], f["win_cur"])
    ])


def pred_s(f, k):
    n = f["n_cur"].to_numpy()
    return (n * f["cur_mean"].to_numpy() + k * f["prev_mean"].to_numpy()) / (n + k)


def mae(y, p):
    return float(np.mean(np.abs(y - p)))


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = load(eng)
    seasons = sorted(df["season"].unique())
    holdout = [s for s in seasons if s < max(seasons)][-1]
    print(f"weeks 2-5, fitting on seasons before {holdout}, scoring on {holdout}\n")
    print(f"{'market':<10}{'n test':>7}{'A now':>9}{'W best':>9}{'w':>6}"
          f"{'S best':>9}{'k':>6}   best vs A")
    for market, stat in STATS.items():
        d = df[df["position"].isin(POSITIONS[market])]
        f = frame(d, stat)
        fit, test = f[f["season"] < holdout], f[f["season"] == holdout]
        # Quarterbacks: about 32 starters, four weeks, two fitting seasons.
        # One parameter does not need thousands of rows.
        min_fit = 150 if market.startswith("pass") else 200
        if len(fit) < min_fit or len(test) < 100:
            print(f"{market:<10}{len(test):>7}  too few rows")
            continue
        yf, yt = fit["actual"].to_numpy(), test["actual"].to_numpy()
        w = min(W_GRID, key=lambda x: mae(yf, pred_w(fit, x)))
        k = min(K_GRID, key=lambda x: mae(yf, pred_s(fit, x)))
        a, bw, bs = mae(yt, pred_w(test, 1.0)), mae(yt, pred_w(test, w)), mae(yt, pred_s(test, k))
        best = min(bw, bs)
        which = "W" if bw <= bs else "S"
        print(f"{market:<10}{len(test):>7}{a:>9.3f}{bw:>9.3f}{w:>6g}{bs:>9.3f}{k:>6g}"
              f"   {which} {(a - best) / a:+.1%}")

        # The stricter test: does it improve the model, not just an average?
        # The model's walk-forward projections for these same games, from
        # prop_edge_results (refit on earlier seasons only), multiplied by the
        # S-over-A ratio the correction would apply.
        t = test.assign(a_pred=pred_w(test, 1.0),
                        s_pred=pred_w(test, w) if which == "W" else pred_s(test, k))
        mp = pd.read_sql(text("""
            SELECT DISTINCT ON (player_id, game_date) player_id, game_date, projection
            FROM prop_edge_results
            WHERE market_code = :m AND projection IS NOT NULL
            ORDER BY player_id, game_date, edge_id
        """), eng, params={"m": market})
        j = t.merge(mp, on=["player_id", "game_date"], how="inner")
        j = j[j["a_pred"] > 0]
        if len(j) >= 50:
            ratio = np.clip(j["s_pred"] / j["a_pred"], 0.5, 2.0)
            mm, mc = mae(j["actual"], j["projection"]), mae(j["actual"], j["projection"] * ratio)
            print(f"{'':10}  model itself on {len(j)} priced games: "
                  f"{mm:.3f} -> {mc:.3f}  {(mm - mc) / mm:+.1%}")
        else:
            print(f"{'':10}  model itself: only {len(j)} priced games, not enough to judge")


if __name__ == "__main__":
    main()
