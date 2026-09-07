"""Touchdown props need a count model, not a quantile CDF.

The calibration audit turned up something quantile regression cannot fix by
being tuned better. On the held-out season the TD bundles report:

    rush_td   q10 0.823   q25 0.823   q50 0.823   q75 0.823   q90 0.900
    rec_td    q10 0.862   q25 0.862   q50 0.862   q75 0.862   q90 0.925

Five levels, one number. That is not a bug in the fit, it is the correct answer
to the wrong question: 91% of rushing-TD rows are exactly zero, so the true 10th
through 75th percentiles really are all zero, and a model that reports them
honestly is useless for pricing an over 0.5. Interpolating P(over) off a CDF
that is flat across the entire region the line sits in produces a number with no
information in it.

The distributions say plainly what to do instead:

    market     mean    variance   P(Y = 0)
    pass_td    1.294   1.314      0.296
    rec_td     0.156   0.170      0.862
    rush_td    0.111   0.142      0.909

pass_td has variance equal to its mean to three decimal places, which is Poisson
in the textbook sense. The other two are mildly overdispersed. So fit the rate
directly with Poisson loss and read the probability off the Poisson survival
function, where P(Y > k) has a closed form and every line a book posts (0.5,
1.5, 2.5) is an exact integer threshold. No interpolation, no assumed shape.

Scored against the quantile CDF it replaces, on the same holdout rows and the
same posted lines, using Brier score and log loss -- proper scoring rules, so a
model cannot win by being confidently wrong. Writes nothing unless it wins.

Env: MARKETS (default the three TD markets), LOOKBACK, SPLIT_MODE, WRITE=1.
"""

import json
import os
from datetime import date

import joblib
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.ensemble import HistGradientBoostingRegressor

import eval as ev
import train as tr
import train_quantiles as tq

MARKETS = os.getenv("MARKETS", "pass_td,rush_td,rec_td").split(",")
TD_MODEL_NAME = os.getenv("TD_MODEL_NAME", "td_poisson_v1")
WRITE = os.getenv("WRITE", "0") == "1"


def build_count_model():
    """Gradient boosting on Poisson deviance, at the capacity the data supports.

    Poisson loss rather than squared error because squared error treats an error
    of one touchdown as equally serious whether the expected rate is 0.1 or 1.3,
    which is exactly backwards for counts.

    The settings come straight from `train.build_model("pois_v4")` rather than
    being restated here. The first version of this script hardcoded its own
    (depth 4, 400 iterations) and lost to the quantile CDF on Brier, which was
    the right verdict on the wrong model: the capacity sweep later showed that
    anything above depth 3 on 1,557 quarterback rows is memorising, and the
    tuned version cut holdout bias from -11.2% to -1.5%. Sharing the builder
    also means this cannot drift away from the model that actually ships.
    """
    return tr.build_model("pois_v4")


def prob_over(rate: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(Y > line) for a Poisson rate.

    A line of 0.5 means "at least one", so the threshold is floor(line) and the
    survival function gives the answer exactly. `poisson.sf(k, mu)` is
    P(Y > k), which is already what is wanted -- no continuity correction, and
    no push case, because the lines are always half-integers.
    """
    k = np.floor(np.asarray(line, dtype=float))
    return np.clip(poisson.sf(k, np.clip(rate, 1e-6, None)), 0.01, 0.99)


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def load_lines(engine, market_key: str) -> pd.DataFrame:
    """The lines books actually posted, so the comparison is on real thresholds.

    Scoring both models at an invented line would let either one look good on
    thresholds no one was ever offered.
    """
    from sqlalchemy import text
    return pd.read_sql(text("""
        SELECT player_name,
               (commence_time AT TIME ZONE 'UTC')::date AS game_date,
               AVG(line) FILTER (WHERE lower(outcome_name) = 'over') AS line
        FROM odds_snapshots
        WHERE market_key = :mk AND line IS NOT NULL
        GROUP BY 1, 2
        HAVING AVG(line) FILTER (WHERE lower(outcome_name) = 'over') IS NOT NULL
    """), engine, params={"mk": market_key})


MARKET_KEY = {
    "pass_td": "player_pass_tds",
    "rush_td": "player_rush_tds",
    "rec_td": "player_reception_tds",
}


def main():
    from sqlalchemy import create_engine, text

    from backtest_season import DATABASE_URL

    engine = create_engine(DATABASE_URL, future=True)
    ids = pd.read_sql(text(
        "SELECT external_id, name FROM players WHERE external_id IS NOT NULL"
    ), engine)

    def key(s):
        return s.str.lower().str.replace(r"[.\-']", "", regex=True).str.strip()

    ids["key"] = key(ids["name"])

    for market in MARKETS:
        market = market.strip()
        os.environ["MARKET_CODE"] = market
        ev.MARKET_CODE = market

        with engine.connect() as c:
            row = c.execute(text(
                "SELECT a.model_name, a.lookback FROM active_models a "
                "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
            ), {"c": market}).mappings().first()
        if not row:
            print(f"{market}: no active model")
            continue
        meta_path = os.path.join(
            ev.ARTIFACT_DIR,
            f"{row['model_name']}_{market}_lb{row['lookback']}.json")
        if not os.path.exists(meta_path):
            print(f"{market}: no metadata")
            continue
        cols = json.load(open(meta_path))["feature_cols"]

        df = ev.load_labeled_rows(cols)
        train_df, test_df = (ev.season_split(df) if ev.SPLIT_MODE == "season"
                             else ev.time_split(df, ev.TEST_FRAC))
        X_train = ev.build_feature_matrix(train_df, cols)
        X_test = ev.build_feature_matrix(test_df, cols)
        y_train = train_df[ev.LABEL_COL].astype(float)
        y_test = test_df[ev.LABEL_COL].astype(float)

        count = build_count_model()
        count.fit(X_train, y_train)
        rate = np.clip(count.predict(X_test), 1e-6, None)

        quants = {}
        for q in tq.QUANTILES:
            qm = tq.build_quantile_model(q)
            qm.fit(X_train, y_train)
            quants[q] = np.clip(qm.predict(X_test), 0.0, None)

        lines = load_lines(engine, MARKET_KEY[market])
        lines["key"] = key(lines["player_name"])
        lines = lines.merge(ids, on="key", how="inner")
        scored = test_df.reset_index(drop=True).assign(
            rate=rate, **{f"q{int(q * 100)}": quants[q] for q in tq.QUANTILES}
        ).merge(
            lines[["external_id", "game_date", "line"]],
            left_on=["player_id", "as_of_game_date"],
            right_on=["external_id", "game_date"], how="inner",
        ).dropna(subset=["line"])

        print()
        print(f"=== {market} ===")
        print(f"train {len(train_df)}, holdout {len(test_df)}, "
              f"holdout rows with a posted line {len(scored)}")
        if len(scored) < 50:
            print("  too few priced rows in the holdout to compare honestly")
            continue

        y_bin = (scored[ev.LABEL_COL].astype(float) > scored["line"]).to_numpy()
        p_pois = prob_over(scored["rate"].to_numpy(), scored["line"].to_numpy())
        qcols = {q: scored[f"q{int(q * 100)}"].to_numpy() for q in tq.QUANTILES}
        p_quant = tq.prob_over(qcols, scored["line"].to_numpy())

        base = float(y_bin.mean())
        print(f"{'':<26}{'Brier':>10}{'log loss':>11}{'mean p':>9}")
        print(f"{'always the base rate':<26}{brier(np.full(len(y_bin), base), y_bin):>10.4f}"
              f"{logloss(np.full(len(y_bin), base), y_bin):>11.4f}{base:>9.3f}")
        print(f"{'quantile CDF (current)':<26}{brier(p_quant, y_bin):>10.4f}"
              f"{logloss(p_quant, y_bin):>11.4f}{p_quant.mean():>9.3f}")
        print(f"{'Poisson survival (new)':<26}{brier(p_pois, y_bin):>10.4f}"
              f"{logloss(p_pois, y_bin):>11.4f}{p_pois.mean():>9.3f}")
        print(f"{'realised':<26}{'':>10}{'':>11}{base:>9.3f}")

        better = (brier(p_pois, y_bin) < brier(p_quant, y_bin)
                  and logloss(p_pois, y_bin) < logloss(p_quant, y_bin))
        beats_base = brier(p_pois, y_bin) < brier(np.full(len(y_bin), base), y_bin)
        print(f"  Poisson beats the quantile CDF on both scores: {better}")
        print(f"  Poisson beats predicting the base rate:        {beats_base}")

        if not WRITE:
            continue
        # Only ship a model that wins on both proper scoring rules AND carries
        # information beyond the base rate. Beating a broken baseline is not a
        # reason to deploy.
        if not (better and beats_base):
            print("  not written: it has to win on both scores and beat the "
                  "base rate")
            continue
        stem = f"{TD_MODEL_NAME}_{market}_lb{ev.LOOKBACK}"
        path = os.path.join(ev.ARTIFACT_DIR, f"{stem}.joblib")
        joblib.dump({"model": count, "feature_cols": cols,
                     "kind": "poisson_rate"}, path)
        with open(os.path.join(ev.ARTIFACT_DIR, f"{stem}.json"), "w") as f:
            json.dump({
                "model_name": TD_MODEL_NAME, "market_code": market,
                "lookback": ev.LOOKBACK, "generated_at": date.today().isoformat(),
                "artifact_path": path, "feature_cols": cols,
                "brier": brier(p_pois, y_bin), "log_loss": logloss(p_pois, y_bin),
                "brier_quantile_cdf": brier(p_quant, y_bin),
                "brier_base_rate": brier(np.full(len(y_bin), base), y_bin),
                "rows_scored": int(len(scored)),
            }, f, indent=2)
        print(f"  wrote {path}")


if __name__ == "__main__":
    main()
