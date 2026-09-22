"""Does averaging several model families beat picking one?

The bakeoff chose a single family per market on time-series folds, and per
market that is the right question to ask of a family. It is not the only
question. Averaging several models usually beats any one of them, because
their errors are not identical: a linear model and a forest are wrong about
different players, and the average of two honest mistakes is a smaller
mistake.

Nothing new is needed to find out. Same rows, same time-ordered split, same
feature matrix as train.py, which this imports rather than reimplements so the
comparison cannot drift from what actually ships.

For each market:

    incumbent     the family that is live today
    each family   ridge, elastic net, random forest, hist gradient boosting
    average       the mean prediction of the families that beat the
                  incumbent's own baseline, which is the only ensemble worth
                  shipping: averaging in a model that is simply worse drags
                  the result toward it

Judged on the held-out quarter of the timeline, by MAE and R2, the same two
numbers train.py reports. A win has to be big enough to survive the noise rule
before it changes anything.
"""

import os

import numpy as np
import pandas as pd

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")

import train as tr  # noqa: E402  (env must be set before import)
from psycopg2.extras import RealDictCursor  # noqa: E402

MARKETS = {
    "recs": "enet_v2", "rec_yds": "rf_default", "rush_yds": "enet_v2",
    "rush_att": "ridge_v2", "pass_yds": "hgb_v1", "pass_att": "hgb_v1",
    "pass_completions": "hgb_v1",
}
FAMILIES = ["ridge_x", "enet_x", "rf_x", "hgb_x"]


def load_market(code: str):
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code = %s",
                    (code,))
        m = cur.fetchone()
        if not m:
            return None
        cur.execute(
            """
            SELECT pmf.player_id, p.position, pmf.as_of_game_date, pmf.opponent,
                   pmf.team, pmf.mean, pmf.stddev, pmf.weighted_mean, pmf.trend,
                   pmf.aux_mean, pmf.aux_trend, pmf.extra_features, pmf.label_actual
            FROM player_market_features pmf
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pmf.market_id = %s AND pmf.lookback = %s
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date, pmf.player_id
            """,
            (m["id"], int(os.environ["LOOKBACK"])),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows)
    elig = m.get("eligible_positions")
    if elig:
        df = df[df["position"].isin(list(elig))]
    return df


def score(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    mae = float(np.mean(np.abs(y - p)))
    ss = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(((y - p) ** 2).sum()) / ss if ss else float("nan")
    return mae, r2


def main():
    print(f"{'market':<18}{'model':<14}{'MAE':>9}{'R2':>8}   note")
    for code, incumbent in MARKETS.items():
        df = load_market(code)
        if df is None or len(df) < 400:
            print(f"{code:<18}{'-':<14}{'-':>9}{'-':>8}   too few labelled rows")
            continue
        # Split first, then build features on each side, which is the order
        # train.py uses: building once and splitting after lets the test rows
        # influence which JSON feature columns exist.
        df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"], errors="coerce")
        df = df[df["as_of_game_date"].notna()].copy()
        train_raw, test_raw = tr._time_split(df, test_frac=0.25)
        train_df, cols = tr._build_feature_dataframe(train_raw)
        test_df, _ = tr._build_feature_dataframe(test_raw)
        for c in cols:
            if c not in test_df.columns:
                test_df[c] = 0.0
        ytr = train_raw["label_actual"].astype(float).to_numpy()
        yte = test_raw["label_actual"].astype(float).to_numpy()

        preds, results = {}, {}
        for fam in FAMILIES:
            try:
                model = tr.build_model(fam)
                model.fit(train_df[cols], ytr)
                p = model.predict(test_df[cols])
            except Exception as e:  # a family that will not fit is not a candidate
                results[fam] = (float("nan"), float("nan"), str(e)[:40])
                continue
            preds[fam] = p
            results[fam] = (*score(yte, p), "")

        if not preds:
            print(f"{code:<18}{'-':<14}{'-':>9}{'-':>8}   nothing fitted")
            continue

        base_mae = min(m for m, _, _ in results.values() if np.isfinite(m))
        keep = {k: v for k, v in preds.items()
                if np.isfinite(results[k][0]) and results[k][0] <= base_mae * 1.05}
        avg = np.mean(np.stack(list(keep.values())), axis=0)
        a_mae, a_r2 = score(yte, avg)

        best_fam = min((k for k in preds), key=lambda k: results[k][0])
        print(f"{code:<18}{'incumbent ' + incumbent[:4]:<14}"
              f"{results.get(best_fam, (float('nan'),))[0]:>9.3f}"
              f"{results[best_fam][1]:>8.4f}   best single family: {best_fam}")
        for fam in FAMILIES:
            mae, r2, err = results.get(fam, (float("nan"), float("nan"), "missing"))
            if err:
                print(f"{'':<18}{fam:<14}{'-':>9}{'-':>8}   {err}")
            else:
                print(f"{'':<18}{fam:<14}{mae:>9.3f}{r2:>8.4f}")
        gain = (results[best_fam][0] - a_mae) / results[best_fam][0] * 100
        print(f"{'':<18}{'AVERAGE of ' + str(len(keep)):<14}{a_mae:>9.3f}{a_r2:>8.4f}"
              f"   {gain:+.1f}% vs the best single\n")


if __name__ == "__main__":
    main()
