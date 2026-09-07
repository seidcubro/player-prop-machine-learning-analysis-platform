"""Backtest the rule the product actually uses, which no previous test did.

Two things came out of the disagreement and asymmetry work that together
invalidate every earlier negative result:

**1. The mean is the wrong number to compare to a line.** Measured on the graded
picks, the point model is a near-perfect mean predictor and the mean is nowhere
near the median on these markets:

    rec_yds    pred 32.85   mean actual 33.07   median actual 24
    rush_yds   pred 34.85   mean actual 36.11   median actual 27
    recs       pred  3.05   mean actual  2.99   median actual  3
    pass_yds   pred 219     mean actual 220     median actual 220

Receiving and rushing yards are floored at zero with a long right tail, so the
mean sits 25-35% above the median. A book prices the line near the mean. Calling
"over" whenever mean > line is therefore a bet that a right-skewed variable lands
above its own mean, which happens well under half the time. That is not a model
failure, it is a decision-rule failure, and it explains why the two markets with
no skew (receptions, passing yards) behaved completely differently from the two
with heavy skew in every slice I looked at.

**2. Every backtest so far scored a rule the live system does not use.**
`build_prop_edges.py` reads P(over) off a fitted quantile CDF. `backtest_log`
was built from `pred > book_line`. So the four "no edge" results measured a
strawman.

This scores the real thing, out of sample:

  * Quantile models are refit on seasons strictly before the test season, the
    same discipline as the point model. Nothing the bet could not have known.
  * P(over) comes off the fitted CDF, then through a calibration map estimated
    on those same earlier seasons, so the probability is honest rather than
    merely available.
  * A bet is placed only when the model's probability beats the price's implied
    probability by a stated margin, at the best of the three books. Betting the
    more likely side regardless of price is not a strategy, it is a coin flip
    with a commission.
  * The mean rule is scored alongside it on the identical population, so the
    comparison is like for like rather than against a remembered number.
"""

import argparse
import json
import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import eval as ev
import train as tr
import train_quantiles as tq
from backtest_season import (DATABASE_URL, MARKET_MAP, american_to_decimal,
                             american_to_prob)

MIN_EV = float(os.getenv("MIN_EV", "0.02"))


def load_prices(engine, season: int) -> pd.DataFrame:
    """Best price per side per prop, each at the line the offering book posted.

    Best price is taken per side independently because the book that is generous
    on the over and the one that is generous on the under are rarely the same.
    """
    return pd.read_sql(text("""
        WITH latest AS (
            SELECT DISTINCT ON (provider_event_id, bookmaker_key, market_key,
                                player_name, outcome_name)
                   provider_event_id, bookmaker_key, market_key, player_name,
                   outcome_name, line, price_american, commence_time
            FROM odds_snapshots
            WHERE line IS NOT NULL
              AND EXTRACT(YEAR FROM commence_time) = :yr
            ORDER BY provider_event_id, bookmaker_key, market_key, player_name,
                     outcome_name, observed_at DESC
        ),
        ranked AS (
            SELECT *,
                   (commence_time AT TIME ZONE 'UTC')::date AS game_date,
                   ROW_NUMBER() OVER (
                     PARTITION BY provider_event_id, market_key, player_name,
                                  lower(outcome_name)
                     ORDER BY CASE WHEN price_american < 0
                                   THEN 1 + 100.0 / (-price_american)
                                   ELSE 1 + price_american / 100.0 END DESC
                   ) AS rk
            FROM latest
        )
        SELECT market_key, player_name, game_date,
               MAX(line)  FILTER (WHERE lower(outcome_name) = 'over')  AS over_line,
               MAX(price_american) FILTER (WHERE lower(outcome_name) = 'over')
                   AS over_am,
               MAX(line)  FILTER (WHERE lower(outcome_name) = 'under') AS under_line,
               MAX(price_american) FILTER (WHERE lower(outcome_name) = 'under')
                   AS under_am,
               MAX(CASE WHEN lower(outcome_name) = 'over' THEN line END) AS line
        FROM ranked WHERE rk = 1
        GROUP BY 1, 2, 3
    """), engine, params={"yr": season})


def cdf_level(qcols: dict, values: np.ndarray) -> np.ndarray:
    """Predicted P(Y <= value), read off the fitted quantile CDF."""
    levels = np.array(sorted(qcols.keys()), dtype=float)
    mat = np.column_stack([qcols[q] for q in sorted(qcols.keys())])
    mat = np.sort(mat, axis=1)
    out = np.empty(len(values), dtype=float)
    for i in range(len(values)):
        out[i] = float(np.interp(values[i], mat[i], levels))
    return out


def fit_pit(qcols: dict, y: np.ndarray):
    """Probability-integral-transform recalibration, fit on training rows only.

    If the fitted quantiles were honest, the predicted CDF level at each row's
    own outcome would be uniform on (0, 1). It is not: the quantile models are
    fit independently and the upper ones get pulled toward a long right tail
    that most players never reach, so the CDF puts too little mass below the
    line and P(over) comes out too high. That is exactly the direction of the
    error already showing in the results, where +EV overs lose and +EV unders
    win.

    The empirical distribution of those levels IS the correction, and it needs
    no lines, no prices and no test-season data to estimate. Returns a monotone
    map from predicted level to calibrated level.
    """
    u = np.sort(cdf_level(qcols, np.asarray(y, dtype=float)))
    grid = np.linspace(0.0, 1.0, 101)
    emp = np.searchsorted(u, grid, side="right") / max(len(u), 1)
    return grid, emp


def apply_pit(pit, levels: np.ndarray) -> np.ndarray:
    grid, emp = pit
    return np.clip(np.interp(levels, grid, emp), 0.01, 0.99)


def fit_holdout(engine, market: str, season: int):
    """Point model, quantile ensemble and calibration map, from earlier seasons
    only."""
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
        ), {"c": market}).mappings().first()
    if not row:
        return None
    meta_path = os.path.join(
        ev.ARTIFACT_DIR, f"{row['model_name']}_{market}_lb{row['lookback']}.json"
    )
    if not os.path.exists(meta_path):
        return None
    cols = json.load(open(meta_path))["feature_cols"]

    df = ev.load_labeled_rows(cols)
    dates = pd.to_datetime(df["as_of_game_date"])
    yr = dates.dt.year.where(dates.dt.month >= 3, dates.dt.year - 1)
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

    # Calibrate the CDF against the seasons it was fit on. Calibrating on the
    # season being scored would be leakage dressed up as rigour.
    qtrain = {q: np.clip(quants[q].predict(X), 0, None) for q in tq.QUANTILES}
    pit = fit_pit(qtrain, y.to_numpy())
    print(f"  {market}: fit point + {len(quants)} quantiles on {len(train)} rows "
          f"from seasons < {season}")
    return point, quants, cols, df, pit


def score(f, label, extra=""):
    if len(f) == 0:
        return f"{label:<40}{0:>6}"
    hit, be = f["hit"].mean(), f["breakeven"].mean()
    return (f"{label:<40}{len(f):>6}{hit:>8.3f}{be:>8.3f}{hit - be:>+9.3f}"
            f"{f['units'].mean():>+9.3f}{extra}")


def boot(f, n_boot=2000, seed=17):
    if len(f) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    groups = [g for _, g in f.groupby("game_date")]
    out = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(groups), len(groups))
        s = pd.concat([groups[i] for i in idx], ignore_index=True)
        out.append(s["units"].mean())
    return tuple(np.percentile(out, [2.5, 97.5]))


HDR = f"{'':<40}{'n':>6}{'hit':>8}{'be':>8}{'edge':>9}{'ROI':>9}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    args = ap.parse_args()
    engine = create_engine(DATABASE_URL, future=True)

    prices = load_prices(engine, args.season)
    prices["market_code"] = prices["market_key"].map(MARKET_MAP)
    prices = prices[prices["market_code"].notna()]
    if prices.empty:
        raise SystemExit(f"no {args.season} lines in odds_snapshots")

    ids = pd.read_sql(text(
        "SELECT external_id, name FROM players WHERE external_id IS NOT NULL"
    ), engine)

    def key(s):
        return s.str.lower().str.replace(r"[.\-']", "", regex=True).str.strip()

    ids["key"] = key(ids["name"])
    prices["key"] = key(prices["player_name"])

    all_rows = []
    for market, grp in prices.groupby("market_code"):
        os.environ["MARKET_CODE"] = market
        ev.MARKET_CODE = market
        fit = fit_holdout(engine, market, args.season)
        if fit is None:
            print(f"  {market}: skipped")
            continue
        point, quants, cols, df, pit = fit

        dates = pd.to_datetime(df["as_of_game_date"])
        yr = dates.dt.year.where(dates.dt.month >= 3, dates.dt.year - 1)
        test = df[yr == args.season]
        if test.empty:
            continue
        Xt = ev.build_feature_matrix(test, cols)
        test = test.assign(
            pred=np.clip(point.predict(Xt), 0, None),
            **{f"q{int(q * 100)}": np.clip(quants[q].predict(Xt), 0, None)
               for q in tq.QUANTILES},
        )

        g = grp.merge(ids, on="key", how="inner")
        m = g.merge(
            test[["player_id", "as_of_game_date", "pred", ev.LABEL_COL]
                 + [f"q{int(q * 100)}" for q in tq.QUANTILES]],
            left_on=["external_id", "game_date"],
            right_on=["player_id", "as_of_game_date"], how="inner",
        ).dropna(subset=["pred", ev.LABEL_COL, "line", "over_am", "under_am"])
        if m.empty:
            continue

        qcols = {q: m[f"q{int(q * 100)}"].to_numpy() for q in tq.QUANTILES}
        m["p_over_raw"] = tq.prob_over(qcols, m["over_line"].to_numpy())
        m["p_over"] = 1.0 - apply_pit(
            pit, cdf_level(qcols, m["over_line"].to_numpy()))
        # Kept raw so the walk-forward section can recalibrate them week by week
        # without refitting anything.
        m["lvl_over"] = cdf_level(qcols, m["over_line"].to_numpy())
        m["lvl_under"] = cdf_level(qcols, m["under_line"].to_numpy())
        # The under's probability is read at its own line, which differs from
        # the over's whenever the two best-priced books post different numbers.
        m["p_under"] = apply_pit(pit, m["lvl_under"].to_numpy())
        m["actual"] = m[ev.LABEL_COL].astype(float)
        # Where each outcome actually landed on its own predicted CDF. A
        # calibrated model spreads these uniformly over (0, 1); the empirical
        # distribution of the ones already played is the in-season correction.
        m["pit_actual"] = cdf_level(qcols, m["actual"].to_numpy())
        m["market_code"] = market
        m["over_dec"] = american_to_decimal(m["over_am"])
        m["under_dec"] = american_to_decimal(m["under_am"])
        m["over_be"] = american_to_prob(m["over_am"])
        m["under_be"] = american_to_prob(m["under_am"])
        m["ev_over"] = m["p_over"] - m["over_be"]
        m["ev_under"] = m["p_under"] - m["under_be"]
        all_rows.append(m)

    if not all_rows:
        raise SystemExit("nothing matched")
    d = pd.concat(all_rows, ignore_index=True)
    print()
    print(f"{len(d)} priced props in {args.season} across "
          f"{d['game_date'].nunique()} slates")
    print()

    def bets(line_col, dec_col, be_col, mask, side):
        f = d[mask].copy()
        won = (f["actual"] > f[line_col]) if side == "over" \
            else (f["actual"] < f[line_col])
        push = f["actual"] == f[line_col]
        f["hit"] = np.where(push, np.nan, won.astype(float))
        f["units"] = np.where(push, 0.0, np.where(won, f[dec_col] - 1.0, -1.0))
        f["breakeven"] = f[be_col]
        f["bet_side"] = side
        return f.dropna(subset=["hit"])

    def combine(over_mask, under_mask):
        a = bets("over_line", "over_dec", "over_be", over_mask, "over")
        b = bets("under_line", "under_dec", "under_be", under_mask, "under")
        return pd.concat([a, b], ignore_index=True)

    print("=== IS THE PREDICTED DISTRIBUTION HONEST? ===")
    print("A calibrated CDF puts the outcome below its own qN exactly N% of the")
    print("time. Measured on the held-out season:")
    print()
    print(f"{'market':<18}" + "".join(f"{'<q' + str(int(q * 100)):>8}"
                                      for q in tq.QUANTILES))
    for mk, sub in d.groupby("market_code"):
        cov = [(sub[f"q{int(q * 100)}"] >= sub[ev.LABEL_COL]).mean()
               for q in tq.QUANTILES]
        print(f"{mk:<18}" + "".join(f"{c:>8.3f}" for c in cov))
    print(f"{'target':<18}" + "".join(f"{q:>8.2f}" for q in tq.QUANTILES))
    print()
    print(f"mean P(over) raw {d['p_over_raw'].mean():.3f}, "
          f"calibrated {d['p_over'].mean():.3f}, "
          f"realised {(d['actual'] > d['over_line']).mean():.3f}")

    print()
    print("=== THE OLD RULE: mean vs line, take that side, best price ===")
    print(HDR)
    old = combine(d["pred"] > d["over_line"], d["pred"] <= d["under_line"])
    print(score(old, "mean > line -> over, else under"))
    print(score(old[old["market_code"].isin(["rec_yds", "rush_yds"])],
                "  on the skewed markets only"))
    print(score(old[~old["market_code"].isin(["rec_yds", "rush_yds"])],
                "  on the symmetric markets only"))

    print()
    print("=== MEDIAN INSTEAD OF MEAN, same population ===")
    print(HDR)
    print(score(combine(d["q50"] > d["over_line"], d["q50"] <= d["under_line"]),
                "q50 > line -> over, else under"))

    print()
    print("=== THE PRODUCT'S RULE: higher CDF probability wins ===")
    print(HDR)
    print(score(combine(d["p_over_raw"] >= 0.5, d["p_over_raw"] < 0.5),
                "raw P(over) >= 0.5"))
    print(score(combine(d["p_over"] >= 0.5, d["p_over"] < 0.5),
                "calibrated P(over) >= 0.5"))

    print()
    print("=== POSITIVE EV ONLY: probability must beat the price ===")
    print(HDR + f"{'95% CI on ROI':>26}")
    for thr in (0.00, 0.02, 0.04, 0.06, 0.08, 0.10):
        f = combine(d["ev_over"] > thr, d["ev_under"] > thr)
        if len(f) < 40:
            print(score(f, f"EV edge > {thr:.0%}"))
            continue
        lo, hi = boot(f)
        flag = "  PROFITABLE" if lo > 0 else ""
        print(score(f, f"EV edge > {thr:.0%}", f"   [{lo:+.3f}, {hi:+.3f}]{flag}"))

    f = combine(d["ev_over"] > MIN_EV, d["ev_under"] > MIN_EV)
    print()
    print("=== POSITIVE-EV BETS SPLIT BY SIDE ===")
    print(HDR)
    for side in ("over", "under"):
        print(score(f[f["bet_side"] == side], f"{side}s, EV > {MIN_EV:.0%}"))

    print()
    print("=== POSITIVE-EV BETS BY MARKET ===")
    print(HDR)
    for mk, sub in f.groupby("market_code"):
        if len(sub) >= 50:
            print(score(sub, mk))

    print()
    print("=== IN-SEASON RECALIBRATION, WALK FORWARD ===")
    print("Per-row production is falling about 3% a season (rec_yds averaged")
    print("26.6 in 2022 and 23.4 in 2025), so a model trained through 2024 reads")
    print("high all year and no calibration fit on old data can know that.")
    print("What a live system CAN do is recalibrate on the season in progress.")
    print("Each slate below is priced using only slates already completed, which")
    print("is the same information the product would have had that morning.")
    print()
    d_sorted = d.sort_values("game_date").reset_index(drop=True)
    slates = sorted(d_sorted["game_date"].unique())
    wf_over, wf_under = [], []
    for i, day in enumerate(slates):
        past = d_sorted[d_sorted["game_date"] < day]
        cur = d_sorted[d_sorted["game_date"] == day]
        # Needs a real sample before it means anything; early weeks fall back to
        # the map fit on prior seasons, exactly as production would.
        if len(past) < 400:
            wf_over.append(cur["p_over"].to_numpy())
            wf_under.append(cur["p_under"].to_numpy())
            continue
        u = np.sort(past["pit_actual"].to_numpy())
        grid = np.linspace(0.0, 1.0, 101)
        emp = np.searchsorted(u, grid, side="right") / len(u)
        live = (grid, emp)
        wf_over.append(1.0 - apply_pit(live, cur["lvl_over"].to_numpy()))
        wf_under.append(apply_pit(live, cur["lvl_under"].to_numpy()))
    d_sorted["p_over_wf"] = np.concatenate(wf_over)
    d_sorted["p_under_wf"] = np.concatenate(wf_under)
    d_sorted["ev_over_wf"] = d_sorted["p_over_wf"] - d_sorted["over_be"]
    d_sorted["ev_under_wf"] = d_sorted["p_under_wf"] - d_sorted["under_be"]

    saved = d
    d = d_sorted
    print(HDR + f"{'95% CI on ROI':>26}")
    for thr in (0.02, 0.04, 0.06, 0.08, 0.10):
        g = combine(d["ev_over_wf"] > thr, d["ev_under_wf"] > thr)
        if len(g) < 40:
            print(score(g, f"walk-forward EV > {thr:.0%}"))
            continue
        lo, hi = boot(g)
        flag = "  PROFITABLE" if lo > 0 else ""
        print(score(g, f"walk-forward EV > {thr:.0%}",
                    f"   [{lo:+.3f}, {hi:+.3f}]{flag}"))
    print()
    print(f"mean P(over): fixed map {d['p_over'].mean():.3f}, "
          f"walk-forward {d['p_over_wf'].mean():.3f}, "
          f"realised {(d['actual'] > d['over_line']).mean():.3f}")
    d = saved

    print()
    print("=== STABILITY ===")
    print(HDR)
    slates = sorted(f["game_date"].unique())
    if slates:
        mid = slates[len(slates) // 2]
        print(score(f[f["game_date"] < mid], f"before {mid}"))
        print(score(f[f["game_date"] >= mid], f"{mid} onward"))


if __name__ == "__main__":
    main()
