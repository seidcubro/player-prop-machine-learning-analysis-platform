"""Predict where the line is wrong, instead of predicting the stat.

Everything else in this project models the player and then compares the answer
to the sportsbook. That has now failed three separate tests: the model lands
closer to the result than the line on only 46.5% of props, and a full 2025
backtest came out at 51.5% against a 53.9% break-even.

The market knows things I don't, and it prices them faster than I can. So stop
competing with it and use it. This models

    residual = actual - line

with the line itself as an input alongside everything else. The question changes
from "what will this player do" to "given what the market already says, in which
direction is it wrong". Those are different problems and the second one is
better posed, because the line has already absorbed all the public information
and what's left is the part a model might actually add.

The bar is not R2. A residual model is useful only if the *sign* of its
prediction beats the break-even implied by the price. Everything below is scored
that way, on slates the model never trained on, against the direct model on
exactly the same rows.

Env: RESID_MODEL (default hist_gbm), TEST_FROM (date, default 2025-11-01).
"""

import os

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, text

import eval as ev

TEST_FROM = os.getenv("TEST_FROM", "2025-11-01")
RESID_MODEL = os.getenv("RESID_MODEL", "hist_gbm")

MARKET_MAP = {
    "player_reception_yds": "rec_yds",
    "player_receptions": "recs",
    "player_rush_yds": "rush_yds",
    "player_rush_attempts": "rush_att",
    "player_pass_yds": "pass_yds",
    "player_pass_tds": "pass_td",
}

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)


def build_model(name):
    if name == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    if name == "rf":
        return RandomForestRegressor(
            n_estimators=400, max_depth=6, min_samples_leaf=20,
            max_features="sqrt", random_state=42, n_jobs=-1,
        )
    return HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.05, max_depth=4,
        min_samples_leaf=40, l2_regularization=1.0, random_state=42,
    )


def load(engine, market_code: str, market_key: str) -> pd.DataFrame:
    """Lines joined to the feature row and the realised outcome for that game."""
    return pd.read_sql(
        text("""
            WITH consensus AS (
                SELECT s.player_name,
                       (s.commence_time AT TIME ZONE 'UTC')::date AS game_date,
                       AVG(s.line)  FILTER (WHERE lower(s.outcome_name) = 'over') AS line,
                       AVG(CASE WHEN s.price_american < 0
                                THEN 1 + 100.0 / (-s.price_american)
                                ELSE 1 + s.price_american / 100.0 END)
                         FILTER (WHERE lower(s.outcome_name) = 'over')  AS over_dec,
                       AVG(CASE WHEN s.price_american < 0
                                THEN 1 + 100.0 / (-s.price_american)
                                ELSE 1 + s.price_american / 100.0 END)
                         FILTER (WHERE lower(s.outcome_name) = 'under') AS under_dec
                FROM odds_snapshots s
                WHERE s.market_key = :mkey AND s.line IS NOT NULL
                GROUP BY 1, 2
            )
            SELECT c.player_name, c.game_date, c.line, c.over_dec, c.under_dec,
                   f.player_id, f.mean, f.stddev, f.weighted_mean, f.trend,
                   f.aux_mean, f.aux_trend, f.recs_mean, f.recs_trend,
                   f.extra_features, f.label_actual
            FROM consensus c
            JOIN players p
              ON lower(replace(replace(p.name, '.', ''), '-', ' ')) =
                 lower(replace(replace(c.player_name, '.', ''), '-', ' '))
            JOIN prop_markets m ON m.code = :mcode
            JOIN player_market_features f
              ON f.player_id = p.external_id
             AND f.as_of_game_date = c.game_date
             AND f.market_id = m.id
             AND f.lookback = 5
            WHERE c.line IS NOT NULL AND f.label_actual IS NOT NULL
        """),
        engine,
        params={"mkey": market_key, "mcode": market_code},
    )


def main():
    engine = create_engine(DATABASE_URL, future=True)
    cut = pd.Timestamp(TEST_FROM).date()

    print(f"residual model: {RESID_MODEL}, test slates from {cut}\n")
    rows = []

    for market_key, market_code in MARKET_MAP.items():
        df = load(engine, market_code, market_key)
        if len(df) < 400:
            print(f"{market_code:<10} skipped, only {len(df)} rows")
            continue

        ex = df["extra_features"].apply(ev._normalize_extra_features)
        feat_names = sorted({k for d in ex for k in d})
        X = pd.DataFrame([{k: d.get(k, 0.0) for k in feat_names} for d in ex])
        for base in ["mean", "stddev", "weighted_mean", "trend",
                     "aux_mean", "aux_trend", "recs_mean", "recs_trend"]:
            X[base] = pd.to_numeric(df[base], errors="coerce").fillna(0.0).values

        # The market's own opinion, as inputs. `line_vs_form` is the important
        # one: it says how far the book has moved away from the player's recent
        # level, which is where a mispricing would show up.
        X["book_line"] = df["line"].astype(float).values
        X["line_vs_form"] = X["book_line"] - X["weighted_mean"]
        X["line_over_form"] = X["book_line"] / X["weighted_mean"].replace(0, np.nan)
        X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)

        y_actual = df["label_actual"].astype(float).values
        y_resid = y_actual - df["line"].astype(float).values

        is_test = pd.to_datetime(df["game_date"]).dt.date >= cut
        if is_test.sum() < 100 or (~is_test).sum() < 300:
            print(f"{market_code:<10} skipped, split too small "
                  f"({(~is_test).sum()} train / {is_test.sum()} test)")
            continue

        model = build_model(RESID_MODEL)
        model.fit(X[~is_test], y_resid[~is_test])
        pred_resid = model.predict(X[is_test])

        te = df[is_test].copy()
        te["pred_resid"] = pred_resid
        te["actual"] = y_actual[is_test.values]
        te["side"] = np.where(te["pred_resid"] > 0, "over", "under")
        te["hit"] = np.where(
            te["actual"] == te["line"], np.nan,
            np.where(te["side"] == "over",
                     te["actual"] > te["line"], te["actual"] < te["line"]),
        )
        te["decimal"] = np.where(te["side"] == "over",
                                 te["over_dec"].fillna(1.909),
                                 te["under_dec"].fillna(1.909))
        te["market_code"] = market_code

        # The direct model on the same rows: project the stat from the same
        # features (without the line) and bet the side it implies. This is the
        # comparison that matters, not R2.
        direct = build_model(RESID_MODEL)
        Xd = X.drop(columns=["book_line", "line_vs_form", "line_over_form"])
        direct.fit(Xd[~is_test], y_actual[~is_test])
        dp = direct.predict(Xd[is_test])
        te["direct_side"] = np.where(dp > te["line"], "over", "under")
        te["direct_hit"] = np.where(
            te["actual"] == te["line"], np.nan,
            np.where(te["direct_side"] == "over",
                     te["actual"] > te["line"], te["actual"] < te["line"]),
        )
        # Is the residual actually predictable at all? If R2 on the held-out
        # residual is ~0 then the line has already absorbed whatever these
        # features know, and a coin-flip hit rate is market efficiency rather
        # than a broken model. Those two look identical from the hit rate alone.
        from sklearn.metrics import r2_score
        r2_resid = r2_score(y_resid[is_test.values], pred_resid)
        corr = float(np.corrcoef(pred_resid, y_resid[is_test.values])[0, 1])
        print(f"  {market_code:<10} residual R2 {r2_resid:+.4f}  "
              f"corr(pred, actual residual) {corr:+.4f}  "
              f"pred sd {pred_resid.std():.2f}  actual sd {y_resid[is_test.values].std():.2f}")

        rows.append(te)

    if not rows:
        raise SystemExit("nothing to evaluate")
    g = pd.concat(rows, ignore_index=True)
    g = g[g["hit"].notna()].copy()
    g["breakeven"] = 1.0 / g["decimal"]
    g["units"] = np.where(g["hit"].astype(bool), g["decimal"] - 1.0, -1.0)

    print(f"{'market':<12}{'n':>6}{'residual':>10}{'direct':>9}{'break-even':>12}{'ROI':>9}")
    print("-" * 58)
    for m, grp in g.groupby("market_code"):
        print(f"{m:<12}{len(grp):>6}{grp['hit'].mean():>10.3f}"
              f"{grp['direct_hit'].mean():>9.3f}{grp['breakeven'].mean():>12.3f}"
              f"{grp['units'].mean():>+9.3f}")

    print("-" * 58)
    print(f"{'ALL':<12}{len(g):>6}{g['hit'].mean():>10.3f}"
          f"{g['direct_hit'].mean():>9.3f}{g['breakeven'].mean():>12.3f}"
          f"{g['units'].mean():>+9.3f}")

    print("\n=== only the picks the residual model is most confident about ===")
    g["conf"] = g["pred_resid"].abs()
    for q in (0.5, 0.75, 0.9):
        top = g[g["conf"] >= g["conf"].quantile(q)]
        print(f"  top {int((1 - q) * 100):>2}% by |predicted residual|: "
              f"n={len(top):<5} hit={top['hit'].mean():.3f} "
              f"break-even={top['breakeven'].mean():.3f} "
              f"ROI={top['units'].mean():+.3f}")

    edge = g["hit"].mean() - g["breakeven"].mean()
    print(f"\nresidual model edge vs break-even: {edge:+.3f}")
    print(f"direct model on the same rows:     "
          f"{g['direct_hit'].mean() - g['breakeven'].mean():+.3f}")


if __name__ == "__main__":
    main()
