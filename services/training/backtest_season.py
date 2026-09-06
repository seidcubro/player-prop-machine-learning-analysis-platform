"""Honest full-season backtest: train on the past, predict a season, grade it.

This answers the only question that matters: if the platform had been running
last season, what would it have said, how often would it have been right, and
would it have made money?

Three rules make it honest, and all three are easy to get wrong:

  1. **The model never sees the test season.** A fresh model is trained on
     seasons strictly before the one being graded. Scoring the shipped model on
     2025 would be in-sample -- it was trained on those games -- and would look
     far better than reality.
  2. **The projection is the model's, not a rolling average.** An earlier version
     of this comparison used `weighted_mean` as a stand-in, which measured the
     baseline rather than the product.
  3. **The line is the real line.** Consensus across books from `odds_snapshots`,
     captured at the time, not reconstructed after the fact.

Outputs a per-prop log and the summary that matters: how often the recommended
side actually won, broken out by edge size, and whether that clears the
break-even a real bettor faces at the price on offer.
"""

import argparse
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sqlalchemy import create_engine, text

import eval as ev
import train as tr

DATABASE_URL = ev.DATABASE_URL if hasattr(ev, "DATABASE_URL") else os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)

MARKET_MAP = {
    "player_reception_yds": "rec_yds",
    "player_receptions": "recs",
    "player_rush_yds": "rush_yds",
    "player_rush_attempts": "rush_att",
    "player_pass_yds": "pass_yds",
    "player_pass_tds": "pass_td",
}

STAT_COL = {
    "rec_yds": "receiving_yards", "recs": "receptions",
    "rush_yds": "rushing_yards", "rush_att": "carries",
    "pass_yds": "passing_yards", "pass_td": "passing_tds",
}


def american_to_decimal(price):
    """American odds -> decimal payout multiplier.

    Averaging American odds across books is invalid: they are two different
    scales spliced at +/-100 with a discontinuity between them, so the mean of
    -130 and +100 is -15, which implies a 6.7x payout that no book ever offered.
    Convert each book's price to a decimal multiplier first, then average those.
    """
    p = np.asarray(price, dtype=float)
    return np.where(p < 0, 1.0 + 100.0 / (-p), 1.0 + p / 100.0)


def american_to_prob(price):
    """Break-even win rate implied by an American price, including the vig."""
    p = np.asarray(price, dtype=float)
    return np.where(p < 0, (-p) / ((-p) + 100.0), 100.0 / (p + 100.0))


def profit_units(hit, decimal):
    """Profit in units from a 1-unit stake, given decimal odds. NaN for a push."""
    d = np.asarray(decimal, dtype=float)
    return np.where(pd.isna(hit), np.nan, np.where(hit, d - 1.0, -1.0))


def train_holdout_model(engine, market: str, test_season: int):
    """Fit a model on seasons strictly before `test_season`."""
    meta_path = None
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
        ), {"c": market}).mappings().first()
    if not row:
        return None, None
    import json
    meta_path = os.path.join(
        ev.ARTIFACT_DIR, f"{row['model_name']}_{market}_lb{row['lookback']}.json"
    )
    if not os.path.exists(meta_path):
        return None, None
    meta = json.load(open(meta_path))
    cols = meta["feature_cols"]

    df = ev.load_labeled_rows(cols)
    dates = pd.to_datetime(df["as_of_game_date"])
    season = dates.dt.year.where(dates.dt.month >= 3, dates.dt.year - 1)
    train = df[season < test_season]
    if len(train) < 200:
        return None, None

    X = ev.build_feature_matrix(train, cols)
    y = train[ev.LABEL_COL].astype(float)
    # Same family the bakeoff chose for this market, refit on the earlier data.
    model = tr.build_model(row["model_name"])
    model.fit(X, y)
    print(f"  {market}: trained {row['model_name']} on {len(train)} rows "
          f"from seasons < {test_season}")
    return model, cols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, future=True)

    lines = pd.read_sql(text("""
        -- Both sides' prices. Grading an under bet at the over's price is a
        -- silent way to invent profit: on a market like passing TDs the over
        -- can be +150 while the under is -180, so using one for both turns a
        -- losing model into a fake winner.
        SELECT s.player_name, s.market_key,
               (s.commence_time AT TIME ZONE 'UTC')::date AS game_date,
               AVG(s.line) FILTER (WHERE lower(s.outcome_name) = 'over') AS book_line,
               AVG(CASE WHEN s.price_american < 0 THEN 1 + 100.0 / (-s.price_american)
                        ELSE 1 + s.price_american / 100.0 END)
                 FILTER (WHERE lower(s.outcome_name) = 'over') AS over_dec,
               MAX(CASE WHEN s.price_american < 0 THEN 1 + 100.0 / (-s.price_american)
                        ELSE 1 + s.price_american / 100.0 END)
                 FILTER (WHERE lower(s.outcome_name) = 'over') AS over_best,
               AVG(CASE WHEN s.price_american < 0 THEN 1 + 100.0 / (-s.price_american)
                        ELSE 1 + s.price_american / 100.0 END)
                 FILTER (WHERE lower(s.outcome_name) = 'under') AS under_dec,
               MAX(CASE WHEN s.price_american < 0 THEN 1 + 100.0 / (-s.price_american)
                        ELSE 1 + s.price_american / 100.0 END)
                 FILTER (WHERE lower(s.outcome_name) = 'under') AS under_best,
               COUNT(DISTINCT s.bookmaker_key) AS books
        FROM odds_snapshots s
        WHERE s.line IS NOT NULL
          AND EXTRACT(YEAR FROM s.commence_time) = :yr
        GROUP BY 1, 2, 3
        HAVING AVG(s.line) FILTER (WHERE lower(s.outcome_name) = 'over') IS NOT NULL
    """), engine, params={"yr": args.season})
    if lines.empty:
        raise SystemExit(f"no {args.season} lines in odds_snapshots; back-fill first")

    lines["market_code"] = lines["market_key"].map(MARKET_MAP)
    lines = lines[lines["market_code"].notna()]
    print(f"{len(lines)} consensus lines across "
          f"{lines['game_date'].nunique()} slates in {args.season}\n")

    results = []
    for market, grp in lines.groupby("market_code"):
        os.environ["MARKET_CODE"] = market
        ev.MARKET_CODE = market
        model, cols = train_holdout_model(engine, market, args.season)
        if model is None:
            print(f"  {market}: skipped (no model or too little history)")
            continue

        feats = ev.load_labeled_rows(cols)
        feats = feats[["player_id", "as_of_game_date", ev.LABEL_COL] +
                      [c for c in feats.columns if c not in
                       ("player_id", "as_of_game_date", ev.LABEL_COL)]]
        X = ev.build_feature_matrix(feats, cols)
        feats = feats.assign(pred=np.clip(model.predict(X), 0, None))

        ids = pd.read_sql(text(
            "SELECT external_id, name FROM players WHERE external_id IS NOT NULL"
        ), engine)
        ids["key"] = ids["name"].str.lower().str.replace(r"[.\-]", "", regex=True)
        g = grp.copy()
        g["key"] = g["player_name"].str.lower().str.replace(r"[.\-]", "", regex=True)
        g = g.merge(ids, on="key", how="inner")

        m = g.merge(
            feats[["player_id", "as_of_game_date", "pred", ev.LABEL_COL]],
            left_on=["external_id", "game_date"],
            right_on=["player_id", "as_of_game_date"], how="inner",
        ).dropna(subset=["pred", ev.LABEL_COL, "book_line"])
        if m.empty:
            print(f"  {market}: no rows matched to features")
            continue

        m["actual"] = m[ev.LABEL_COL].astype(float)
        m["market_code"] = market
        m["side"] = np.where(m["pred"] > m["book_line"], "over", "under")
        m["hit"] = np.where(
            m["actual"] == m["book_line"], np.nan,
            np.where(m["side"] == "over",
                     m["actual"] > m["book_line"], m["actual"] < m["book_line"]),
        )
        m["edge"] = (m["pred"] - m["book_line"]).abs()
        m["our_err"] = (m["pred"] - m["actual"]).abs()
        m["book_err"] = (m["book_line"] - m["actual"]).abs()
        # Price the side we actually recommend.
        # Decimal payout for the side actually recommended; -110 is the
        # standard prop price and stands in when a book did not post one.
        # What line shopping is worth: the best price on offer versus the
        # average. A bettor with accounts at several books always takes the best.
        m["decimal_best"] = np.where(
            m["side"] == "over",
            m["over_best"].fillna(1.909),
            m["under_best"].fillna(1.909),
        )
        m["decimal"] = np.where(
            m["side"] == "over",
            m["over_dec"].fillna(1.909),
            m["under_dec"].fillna(1.909),
        )
        results.append(m)

    if not results:
        raise SystemExit("nothing graded")
    df = pd.concat(results, ignore_index=True)
    graded = df[df["hit"].notna()].copy()
    graded["units"] = profit_units(graded["hit"].astype(bool), graded["decimal"])
    # Break-even win rate is just the reciprocal of the decimal payout.
    graded["breakeven"] = 1.0 / graded["decimal"]

    print(f"\n{'='*70}\n{args.season} BACKTEST, model never saw this season\n{'='*70}")
    print(f"graded picks: {len(graded)}\n")

    print("=== by market ===")
    by = graded.groupby("market_code").agg(
        n=("hit", "size"), hit_rate=("hit", "mean"),
        breakeven=("breakeven", "mean"),
        units=("units", "sum"), roi=("units", "mean"),
        our_mae=("our_err", "mean"), book_mae=("book_err", "mean"),
    )
    by["edge_vs_breakeven"] = by["hit_rate"] - by["breakeven"]
    print(by.round(3).to_string())

    print("\n=== by size of our disagreement with the line ===")
    graded["bucket"] = pd.qcut(graded["edge"], 5,
                               labels=["smallest", "small", "mid", "big", "biggest"],
                               duplicates="drop")
    print(graded.groupby("bucket", observed=True).agg(
        n=("hit", "size"), hit_rate=("hit", "mean"),
        breakeven=("breakeven", "mean"), roi=("units", "mean"),
    ).round(3).to_string())

    print("\n=== overall ===")
    hr = graded["hit"].mean()
    be = graded["breakeven"].mean()
    print(f"hit rate      {hr:.3f}")
    print(f"break-even    {be:.3f}   (the price you actually get)")
    print(f"edge          {hr - be:+.3f}")
    print(f"units         {graded['units'].sum():+.1f} on {len(graded)} unit bets")
    print(f"ROI           {graded['units'].mean():+.3%}")
    print(f"beat the line on {(graded['our_err'] < graded['book_err']).mean():.1%} of props")

    best_units = profit_units(graded["hit"].astype(bool), graded["decimal_best"])
    print("\n=== what line shopping is worth ===")
    print(f"average price : ROI {graded['units'].mean():+.3%}  "
          f"break-even {(1.0 / graded['decimal']).mean():.3f}")
    print(f"best price    : ROI {np.nanmean(best_units):+.3%}  "
          f"break-even {(1.0 / graded['decimal_best']).mean():.3f}")
    print(f"difference    : {np.nanmean(best_units) - graded['units'].mean():+.3%} "
          "of every unit staked, for taking the best number instead of the average")

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS backtest_log"))
        keep = ["player_name", "market_code", "game_date", "book_line", "pred",
                "actual", "side", "hit", "edge", "decimal", "our_err", "book_err"]
        graded[keep].to_sql("backtest_log", conn, index=False)
    print(f"\nwrote {len(graded)} rows to backtest_log")


if __name__ == "__main__":
    main()
