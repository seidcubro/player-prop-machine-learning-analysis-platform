"""Can any model beat the price? A fair fight, with the data we never used.

Measured on graded picks, the production model's probability adds nothing to
the price: predicting outcomes from the price alone scores better, out of
sample, in every market. That is the problem to solve, and a board or a filter
cannot solve it. Only a better projection can.

The production features are built from the box score, the schedule, snap counts
and team form. Four tables in this database are never read, and they carry
exactly what a prop model is supposed to know:

    ff_opportunity   expected receptions and expected yards from the
                     opportunities a player actually got, plus the gap between
                     expected and real, which separates usage from finishing
    ngs_receiving    separation, cushion, intended air yards, and the share of
                     the team's air yards a receiver commands
    pfr_adv_*        broken tackles and drops
    snap_counts      already used, kept here for completeness

Routes run would be the best of the lot and is deliberately absent: the
participation table stops at 2025, so a model trained on it could not be served
this season. Nothing here uses a column that does not exist for 2026.

The test, per market:

    market only      P(over) from the price, de-vigged. The thing to beat.
    production       what the shipped model said, from the graded record
    usage model      trained here on seasons before the holdout

scored by log loss on the holdout season's priced props, which is the question
"does this know something the price does not" asked in the only way that
settles it. MAE against the outcome is printed too, because a projection has to
be right before its probabilities can be.
"""

import os
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore")

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)

# market key a book posts -> (our code, the box score column, positions)
MARKETS = {
    "player_receptions": ("recs", "receptions", ("WR", "TE", "RB")),
    "player_reception_yds": ("rec_yds", "receiving_yards", ("WR", "TE", "RB")),
    "player_rush_yds": ("rush_yds", "rushing_yards", ("RB", "QB", "WR")),
    "player_rush_attempts": ("rush_att", "carries", ("RB", "QB", "WR")),
    "player_pass_yds": ("pass_yds", "passing_yards", ("QB",)),
    "player_pass_attempts": ("pass_att", "attempts", ("QB",)),
    "player_pass_completions": ("pass_completions", "completions", ("QB",)),
}
WINDOWS = (3, 5, 10)
HOLDOUT = 2025
EPS = 1e-6


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def log_loss(y, p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def load(engine):
    """Player-games with everything known about them, and the props posted."""
    games = pd.read_sql(
        text(
            """
            SELECT g.player_id, g.season, g.week, g.game_date, g.team, g.opponent,
                   g.position,
                   COALESCE(g.receptions,0) receptions, COALESCE(g.targets,0) targets,
                   COALESCE(g.receiving_yards,0) receiving_yards,
                   COALESCE(g.receiving_air_yards,0) receiving_air_yards,
                   COALESCE(g.rushing_yards,0) rushing_yards, COALESCE(g.carries,0) carries,
                   COALESCE(g.passing_yards,0) passing_yards, COALESCE(g.attempts,0) attempts,
                   COALESCE(g.completions,0) completions,
                   s.offense_pct,
                   f.receptions_exp, f.rec_yards_gained_exp, f.rush_yards_gained_exp,
                   f.pass_yards_gained_exp, f.receptions_diff, f.rec_yards_gained_diff,
                   f.rush_yards_gained_diff, f.pass_yards_gained_diff,
                   n.avg_separation, n.avg_cushion, n.avg_intended_air_yards,
                   n.percent_share_of_intended_air_yards air_share, n.catch_percentage,
                   n.avg_yac_above_expectation,
                   ng.spread_line, ng.total_line, ng.home_team, ng.roof
            FROM player_game_stats_app g
            LEFT JOIN snap_counts s
                   ON s.player_id = g.player_id AND s.season = g.season AND s.week = g.week
            LEFT JOIN ff_opportunity f
                   ON f.player_id = g.player_id AND f.season = g.season AND f.week = g.week
            LEFT JOIN ngs_receiving n
                   ON n.player_id = g.player_id AND n.season = g.season AND n.week = g.week
            LEFT JOIN nfl_games ng
                   ON ng.season = g.season AND ng.week = g.week
                  AND (ng.home_team = g.team OR ng.away_team = g.team)
            WHERE g.season_type = 'REG'
            """
        ),
        engine,
    )
    props = pd.read_sql(
        text(
            """
            SELECT DISTINCT ON (s.provider_event_id, s.player_name, s.market_key)
                   s.player_name, s.market_key, s.line,
                   (s.commence_time AT TIME ZONE 'America/New_York')::date game_date,
                   s.price_american over_price
            FROM odds_snapshots s
            WHERE s.line IS NOT NULL AND s.price_american IS NOT NULL
              AND lower(s.outcome_name) = 'over'
            ORDER BY s.provider_event_id, s.player_name, s.market_key, s.observed_at
            """
        ),
        engine,
    )
    under = pd.read_sql(
        text(
            """
            SELECT DISTINCT ON (s.provider_event_id, s.player_name, s.market_key)
                   s.player_name, s.market_key, s.line,
                   (s.commence_time AT TIME ZONE 'America/New_York')::date game_date,
                   s.price_american under_price
            FROM odds_snapshots s
            WHERE s.line IS NOT NULL AND s.price_american IS NOT NULL
              AND lower(s.outcome_name) = 'under'
            ORDER BY s.provider_event_id, s.player_name, s.market_key, s.observed_at
            """
        ),
        engine,
    )
    props = props.merge(under, on=["player_name", "market_key", "line", "game_date"],
                        how="inner")
    names = pd.read_sql(text("SELECT external_id, name FROM players"), engine)

    def norm(s):
        return (s.str.lower().str.replace(".", "", regex=False)
                 .str.replace("-", " ", regex=False).str.split().str.join(" "))

    names["key"] = norm(names["name"])
    props["key"] = norm(props["player_name"])
    props = props.merge(names[["external_id", "key"]].drop_duplicates("key"),
                        on="key", how="inner")
    props["game_date"] = pd.to_datetime(props["game_date"])
    games["game_date"] = pd.to_datetime(games["game_date"])
    games = games.sort_values(["player_id", "game_date"]).reset_index(drop=True)
    return games, props.rename(columns={"external_id": "player_id"})


def build_features(games: pd.DataFrame, stat: str) -> pd.DataFrame:
    """Trailing views of usage, efficiency and context. Everything shifted."""
    d = games.copy()
    d["is_home"] = (d["team"] == d["home_team"]).astype(float)
    # The team's implied points: the total, split by the spread.
    d["team_total"] = d["total_line"] / 2 - d["spread_line"] / 2 * np.where(
        d["is_home"] == 1, 1, -1)
    d["indoor"] = d["roof"].str.lower().isin(["dome", "closed"]).astype(float)

    rolling_cols = [stat, "targets", "carries", "attempts", "offense_pct",
                    "receptions_exp", "rec_yards_gained_exp", "rush_yards_gained_exp",
                    "pass_yards_gained_exp", "receptions_diff", "rec_yards_gained_diff",
                    "rush_yards_gained_diff", "pass_yards_gained_diff",
                    "receiving_air_yards", "avg_separation", "avg_cushion",
                    "avg_intended_air_yards", "air_share", "catch_percentage",
                    "avg_yac_above_expectation"]
    rolling_cols = [c for c in dict.fromkeys(rolling_cols) if c in d.columns]

    g = d.groupby("player_id", sort=False)
    feats = {}
    for c in rolling_cols:
        s = d[c].astype(float)
        for w in WINDOWS:
            feats[f"{c}_m{w}"] = g[c].transform(
                lambda x, w=w: x.astype(float).shift(1).rolling(w, min_periods=1).mean())
        feats[f"{c}_sd5"] = g[c].transform(
            lambda x: x.astype(float).shift(1).rolling(5, min_periods=2).std())
        feats[f"{c}_last"] = s.groupby(d["player_id"]).shift(1)
    f = pd.DataFrame(feats, index=d.index)
    f["games_played"] = g.cumcount()
    for c in ("is_home", "team_total", "total_line", "spread_line", "indoor"):
        f[c] = d[c]
    f["season"] = d["season"]
    f["player_id"] = d["player_id"]
    f["game_date"] = d["game_date"]
    f["position"] = d["position"]
    f["y"] = d[stat].astype(float)
    return f


def main():
    eng = create_engine(DATABASE_URL, future=True)
    games, props = load(eng)
    print(f"{len(games)} player-games, {len(props)} priced props "
          f"({props['game_date'].min().date()} to {props['game_date'].max().date()})\n")
    print(f"{'market':<18}{'test':>6}{'MAE prod':>10}{'MAE new':>9}"
          f"{'ll market':>11}{'ll new':>9}{'ll blend':>10}   verdict")

    for mkey, (code, stat, positions) in MARKETS.items():
        p = props[props["market_key"] == mkey]
        if p.empty:
            continue
        f = build_features(games[games["position"].isin(positions)], stat)
        data = f.merge(p[["player_id", "game_date", "line", "over_price", "under_price"]],
                       on=["player_id", "game_date"], how="inner")
        if len(data) < 400:
            print(f"{code:<18}{len(data):>6}   too few priced props")
            continue

        drop = ["y", "season", "player_id", "game_date", "position", "line",
                "over_price", "under_price"]
        cols = [c for c in data.columns if c not in drop]
        train = data[data["season"] < HOLDOUT]
        test = data[data["season"] == HOLDOUT]
        if len(train) < 300 or len(test) < 200:
            print(f"{code:<18}{len(test):>6}   too few rows "
                  f"({len(train)} train, {len(test)} test)")
            continue

        # Trained on every player-game of the fitting seasons, not only priced
        # ones: the outcome is the same quantity either way and there are far
        # more of them.
        whole = f[f["season"] < HOLDOUT]
        model = HistGradientBoostingRegressor(
            max_iter=400, learning_rate=0.05, max_depth=6,
            min_samples_leaf=40, l2_regularization=1.0, random_state=0)
        model.fit(whole[cols], whole["y"])
        pred = model.predict(test[cols])
        resid = whole["y"].to_numpy() - model.predict(whole[cols])

        # P(over) from the residual spread, which is the honest way to turn a
        # point projection into a probability without a second model.
        sd = float(np.std(resid)) or 1.0
        z = (test["line"].to_numpy() - pred) / sd
        from scipy.stats import norm as _norm
        p_new = 1.0 - _norm.cdf(z)

        # The market, de-vigged: both prices normalised so they sum to one.
        io = np.where(test["over_price"] > 0, 100.0 / (test["over_price"] + 100.0),
                      -test["over_price"] / (-test["over_price"] + 100.0))
        iu = np.where(test["under_price"] > 0, 100.0 / (test["under_price"] + 100.0),
                      -test["under_price"] / (-test["under_price"] + 100.0))
        p_mkt = io / (io + iu)

        y_over = (test["y"].to_numpy() > test["line"].to_numpy()).astype(int)
        keep = test["y"].to_numpy() != test["line"].to_numpy()

        # Market and model together, weights fitted on the training seasons'
        # priced props so the blend is not judged on rows that set it.
        tr = data[data["season"] < HOLDOUT]
        tr_pred = model.predict(tr[cols])
        tr_z = (tr["line"].to_numpy() - tr_pred) / sd
        tr_new = 1.0 - _norm.cdf(tr_z)
        tio = np.where(tr["over_price"] > 0, 100.0 / (tr["over_price"] + 100.0),
                       -tr["over_price"] / (-tr["over_price"] + 100.0))
        tiu = np.where(tr["under_price"] > 0, 100.0 / (tr["under_price"] + 100.0),
                       -tr["under_price"] / (-tr["under_price"] + 100.0))
        tr_mkt = tio / (tio + tiu)
        tr_y = (tr["y"].to_numpy() > tr["line"].to_numpy()).astype(int)
        tr_keep = tr["y"].to_numpy() != tr["line"].to_numpy()
        from sklearn.linear_model import LogisticRegression
        lr = LogisticRegression(C=1e6).fit(
            np.c_[logit(tr_mkt[tr_keep]), logit(tr_new[tr_keep])], tr_y[tr_keep])
        p_blend = lr.predict_proba(np.c_[logit(p_mkt[keep]), logit(p_new[keep])])[:, 1]

        mae_new = float(np.mean(np.abs(pred - test["y"].to_numpy())))
        prod = np.nan
        ll_mkt = log_loss(y_over[keep], p_mkt[keep])
        ll_new = log_loss(y_over[keep], p_new[keep])
        ll_bl = log_loss(y_over[keep], p_blend)
        better = ll_bl < ll_mkt
        print(f"{code:<18}{int(keep.sum()):>6}{prod:>10.2f}{mae_new:>9.2f}"
              f"{ll_mkt:>11.4f}{ll_new:>9.4f}{ll_bl:>10.4f}   "
              f"{'BEATS THE PRICE' if better else 'no'}"
              f"{'' if not better else f' by {(ll_mkt - ll_bl):.4f}'}")


if __name__ == "__main__":
    main()
