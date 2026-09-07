"""Build a real, multi-season track record instead of one frozen afternoon.

`prop_edge_results` holds 283 graded picks and every one of them is from
2023-12-11, because that is the single slate the live edge builder ever ran
against. So a player page shows two games from two years ago and calls it a
track record, which is worse than showing nothing.

Meanwhile `odds_snapshots` has real closing lines for 2023, 2024 and 2025, and
the models can be refit for each of those seasons using only the seasons before
it. That is enough to reconstruct what the product *would have* published, week
by week, without ever letting a model see the season it is being judged on.

The discipline, because a track record that flatters itself is worthless:

  * **Models are refit per season on strictly earlier data.** Scoring 2024 with
    a model that has seen 2024 would produce a beautiful number and mean
    nothing.
  * **The pick rule is the product's rule.** Calibrated P(over) off the quantile
    CDF, then expected value against the price actually offered, with the same
    tier cuts `build_prop_edges.py` uses. If the site's rule changes, this has
    to change with it.
  * **Rows are tagged `source='backtest'`.** Live picks stay tagged `'live'`.
    They are different things and the table has to say which is which.
  * **Best price per side, one row per player-game-market.** Every book prices
    the same prop; counting all three would triple the sample and make the
    record look far more certain than it is.

Env: SEASONS (default 2023,2024,2025), LOOKBACK, TRUNCATE_BACKTEST=1 to clear
previous backtest rows first (live rows are never touched).
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp
import eval as ev
import train as tr
import train_quantiles as tq
from backtest_quantile_ev import (american_to_decimal, american_to_prob,
                                  cdf_level, load_prices)
from backtest_season import DATABASE_URL, MARKET_MAP

SEASONS = [int(s) for s in os.getenv("SEASONS", "2023,2024,2025").split(",")]

DDL = """
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'live';
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS expected_value DOUBLE PRECISION;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS season INTEGER;
CREATE INDEX IF NOT EXISTS idx_edge_results_season ON prop_edge_results (season);
CREATE INDEX IF NOT EXISTS idx_edge_results_source ON prop_edge_results (source);
"""


def tier_for(ev_value: float, side: str = "under") -> str:
    """Exactly the cuts build_prop_edges.py uses.

    If these drift apart the track record stops describing the product, so they
    are deliberately stated identically in both places rather than imported
    across a module boundary that would make the coupling easy to miss.
    """
    cuts = ((0.12, 0.09, 0.06, 0.03) if side == "over"
            else (0.06, 0.04, 0.02, 0.0))
    if ev_value >= cuts[0]:
        return "elite"
    if ev_value >= cuts[1]:
        return "strong"
    if ev_value >= cuts[2]:
        return "medium"
    if ev_value > cuts[3]:
        return "small"
    return "none"


def season_of(dates) -> pd.Series:
    d = pd.to_datetime(pd.Series(dates))
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def fit_for_season(market: str, season: int):
    """Point model, quantiles and calibration, all from seasons before `season`."""
    import json
    engine = create_engine(DATABASE_URL, future=True)
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
        ), {"c": market}).mappings().first()
    if not row:
        return None
    meta_path = os.path.join(
        ev.ARTIFACT_DIR, f"{row['model_name']}_{market}_lb{row['lookback']}.json")
    if not os.path.exists(meta_path):
        return None
    cols = json.load(open(meta_path))["feature_cols"]

    df = ev.load_labeled_rows(cols)
    yr = season_of(df["as_of_game_date"])
    train = df[yr < season]
    if len(train) < 300:
        return None

    X = ev.build_feature_matrix(train, cols)
    y = train[ev.LABEL_COL].astype(float)
    point = tr.build_model(row["model_name"])
    point.fit(X, y)
    quants = {}
    for q in tq.QUANTILES:
        m = tq.build_quantile_model(q)
        m.fit(X, y)
        quants[q] = m
    # The same calibration the product ships, not a different one.
    #
    # This used to call the backtest helper, which fits the map on in-sample
    # training predictions and therefore comes out as almost the identity. The
    # site serves a map measured on a held-out season and restricted to the
    # priced-like population, which corrects in the opposite direction. Using
    # the wrong one here meant the published track record described a model that
    # is not the one running, which makes every number on that page wrong in a
    # way nobody could see.
    seasons = season_of(train["as_of_game_date"])
    cal = tq.fit_calibration_season_holdout(
        X, y, seasons, tq.QUANTILES, priced=tq.priced_like_mask(train, market))
    pit = (np.asarray(cal["grid"], dtype=float),
           np.asarray(cal["level"], dtype=float))
    return point, quants, cols, df, yr, pit, row["model_name"]


def main():
    engine = create_engine(DATABASE_URL, future=True)
    with engine.begin() as conn:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        if os.getenv("TRUNCATE_BACKTEST", "1") == "1":
            n = conn.execute(text(
                "DELETE FROM prop_edge_results WHERE source = 'backtest'"
            )).rowcount
            print(f"cleared {n} previous backtest rows (live rows untouched)")

    ids = pd.read_sql(text(
        "SELECT id AS app_id, external_id, name FROM players "
        "WHERE external_id IS NOT NULL"), engine)

    def key(s):
        return s.str.lower().str.replace(r"[.\-']", "", regex=True).str.strip()

    ids["key"] = key(ids["name"])
    # Names are not unique (there is a Josh Allen at QB and another on a
    # different line), so keep the first and accept it rather than silently
    # attaching a pick to the wrong profile.
    ids = ids.drop_duplicates(subset="key")

    all_rows = []
    for season in SEASONS:
        prices = load_prices(engine, season)
        prices["market_code"] = prices["market_key"].map(MARKET_MAP)
        prices = prices[prices["market_code"].notna()]
        if prices.empty:
            print(f"{season}: no lines")
            continue
        prices["key"] = key(prices["player_name"])

        for market, grp in prices.groupby("market_code"):
            os.environ["MARKET_CODE"] = market
            ev.MARKET_CODE = market
            fit = fit_for_season(market, season)
            if fit is None:
                continue
            point, quants, cols, df, yr, pit, model_name = fit
            test = df[yr == season]
            if test.empty:
                continue
            Xt = ev.build_feature_matrix(test, cols)
            test = test.assign(
                pred=np.clip(point.predict(Xt), 0, None),
                **{f"q{int(q * 100)}": np.clip(quants[q].predict(Xt), 0, None)
                   for q in tq.QUANTILES})

            m = grp.merge(ids, on="key", how="inner").merge(
                test[["player_id", "as_of_game_date", "pred", ev.LABEL_COL]
                     + [f"q{int(q * 100)}" for q in tq.QUANTILES]],
                left_on=["external_id", "game_date"],
                right_on=["player_id", "as_of_game_date"], how="inner",
            ).dropna(subset=["pred", ev.LABEL_COL, "over_am", "under_am"])
            if m.empty:
                continue

            qcols = {q: m[f"q{int(q * 100)}"].to_numpy() for q in tq.QUANTILES}
            grid, emp = pit
            p_under_over_line = np.clip(
                np.interp(cdf_level(qcols, m["over_line"].to_numpy()), grid, emp),
                0.01, 0.99)
            p_over = 1.0 - p_under_over_line
            p_under = np.clip(
                np.interp(cdf_level(qcols, m["under_line"].to_numpy()), grid, emp),
                0.01, 0.99)

            ev_over = p_over - american_to_prob(m["over_am"])
            ev_under = p_under - american_to_prob(m["under_am"])
            take_over = ev_over >= ev_under

            m["recommended_side"] = np.where(take_over, "over", "under")
            m["win_prob"] = np.where(take_over, p_over, p_under)
            m["expected_value"] = np.where(take_over, ev_over, ev_under)
            m["line"] = np.where(take_over, m["over_line"], m["under_line"])
            m["price_american"] = np.where(take_over, m["over_am"], m["under_am"])
            m["decimal"] = american_to_decimal(m["price_american"])
            m["projection"] = m["pred"]
            m["projection_median"] = m["q50"]
            m["actual"] = m[ev.LABEL_COL].astype(float)
            m["market_code"] = market
            m["season"] = season
            m["model_name"] = model_name
            m["edge_tier"] = [tier_for(v, sd) for v, sd in
                              zip(m["expected_value"], m["recommended_side"])]

            won = np.where(m["recommended_side"] == "over",
                           m["actual"] > m["line"], m["actual"] < m["line"])
            push = m["actual"] == m["line"]
            m["hit"] = np.where(push, None, won)

            keep = (m["edge_tier"] != "none") & (~push)
            m = m[keep]
            if not m.empty:
                all_rows.append(m)
                print(f"  {season} {market}: {len(m)} graded picks")

    if not all_rows:
        raise SystemExit("nothing to write")

    out = pd.concat(all_rows, ignore_index=True)
    # One pick per player-game-market. Three books pricing the same prop is one
    # opinion, not three, and counting them all would make the record look far
    # more certain than it is.
    out = out.sort_values("expected_value", ascending=False).drop_duplicates(
        subset=["external_id", "game_date", "market_code"])

    cols = ["external_id", "app_id", "name", "game_date", "market_code", "line",
            "projection", "projection_median", "recommended_side", "win_prob",
            "expected_value", "edge_tier", "actual", "hit", "season",
            "price_american"]
    w = out[cols].rename(columns={"external_id": "player_id", "name": "player_name"})
    w = w.drop(columns=["app_id"])
    w["source"] = "backtest"
    # Synthetic ids well clear of the live sequence so the two can never collide.
    w["edge_id"] = -(np.arange(len(w)) + 1)

    with engine.begin() as conn:
        w.to_sql("prop_edge_results", conn, if_exists="append", index=False,
                 method="multi", chunksize=500)

    print()
    print(f"WROTE {len(w)} backtested picks")
    summary = w.assign(hit=w["hit"].astype(float)).groupby("season").agg(
        picks=("hit", "size"), hit_rate=("hit", "mean"),
        players=("player_id", "nunique"))
    print(summary.round(3).to_string())


if __name__ == "__main__":
    main()
