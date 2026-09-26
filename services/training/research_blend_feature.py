"""Does y_blend earn its place, judged through the model rather than beside it?

research_season_boundary.py compares estimators of the next game directly, which
is the right question about a window and the wrong one about a feature. A model
with sixty other columns may already know what y_blend knows, in which case
adding it changes nothing, and it may be unable to use it, in which case it
changes nothing either. Only the model can answer that.

So: same rows, same time split, same family, twice, with y_blend and without it.
Nothing else differs, which is the whole point. This is the comparison that was
missing when the early-season correction was rejected in September 2026, and it
is the one that decides.

Also reported per market: the same pair restricted to rows with fewer than five
games of the current season, which is the population the feature exists for.
Averaging over a whole season dilutes a gain that lives in four weeks of it, and
that dilution is how the first version of this question came back negative.
"""

import os

import numpy as np
import pandas as pd

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")

import train as tr  # noqa: E402  (env must be set before import)
from psycopg2.extras import RealDictCursor  # noqa: E402

FAMILIES = {
    "recs": "enet_v2", "rec_yds": "vote_v1", "rush_yds": "vote_v1",
    "rush_att": "vote_v1", "pass_yds": "hgb_v1", "pass_att": "hgb_v1",
    "pass_completions": "hgb_v1",
}
DROP = ["y_blend"]
THIN = 4


def load(code):
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code=%s",
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
            WHERE pmf.market_id=%s AND pmf.lookback=%s AND pmf.label_actual IS NOT NULL
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
    return mae, (1 - float(((y - p) ** 2).sum()) / ss if ss else float("nan"))


def main():
    print("Same rows, same split, same family. The only difference is y_blend.\n")
    print(f"{'market':<18}{'rows':>7}{'MAE without':>13}{'MAE with':>10}"
          f"{'gain':>8}{'thin gain':>11}{'thin n':>8}")
    for code, family in FAMILIES.items():
        df = load(code)
        if df is None or len(df) < 600:
            print(f"{code:<18}{'too few labelled rows':>40}")
            continue
        df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"], errors="coerce")
        df = df[df["as_of_game_date"].notna()].copy()
        train_raw, test_raw = tr._time_split(df, test_frac=0.25)
        train_df, cols = tr._build_feature_dataframe(train_raw)
        test_df, _ = tr._build_feature_dataframe(test_raw)
        for c in cols:
            if c not in test_df.columns:
                test_df[c] = 0.0
        if not any(c in cols for c in DROP):
            print(f"{code:<18}{'y_blend not in the feature columns':>40}")
            continue
        ytr = train_raw["label_actual"].astype(float).to_numpy()
        yte = test_raw["label_actual"].astype(float).to_numpy()
        without = [c for c in cols if c not in DROP]

        def fit(columns):
            mdl = tr.build_model(family)
            mdl.fit(train_df[columns], ytr)
            return mdl.predict(test_df[columns])

        p_no, p_yes = fit(without), fit(list(cols))
        m_no, _ = score(yte, p_no)
        m_yes, _ = score(yte, p_yes)
        gain = (m_no - m_yes) / m_no * 100 if m_no else 0.0

        n_cur = pd.to_numeric(
            test_raw["extra_features"].apply(
                lambda e: (e or {}).get("y_season_n")
                if isinstance(e, dict) else None),
            errors="coerce").to_numpy()
        thin = np.isfinite(n_cur) & (n_cur <= THIN)
        if thin.sum() >= 40:
            t_no, _ = score(yte[thin], p_no[thin])
            t_yes, _ = score(yte[thin], p_yes[thin])
            tgain = f"{(t_no - t_yes) / t_no * 100:+.1f}%" if t_no else "-"
        else:
            tgain = "too few"
        print(f"{code:<18}{len(test_raw):>7}{m_no:>13.4f}{m_yes:>10.4f}"
              f"{gain:>+7.1f}%{tgain:>11}{int(thin.sum()):>8}")


if __name__ == "__main__":
    main()
