"""What does the model actually use, and does football agree?

Mecha (2026) treats this as a validity check rather than a curiosity: field
position had to come out as the dominant feature of a drive-outcome model,
because "a model that failed to identify field position as the dominant feature
would have learned a materially incorrect probability surface, regardless of its
aggregate Brier score performance".

We have never looked. The cost of not looking was found by accident: the rushing
markets carried 81 features and not one of them was the player's own position,
so the model could not tell a quarterback from a running back and nobody noticed
for months because the aggregate metrics were fine. SHAP finds that class of
error in an afternoon.

Mean absolute SHAP value per feature, which is how much each one moves the
prediction on average regardless of direction, computed with TreeSHAP where the
model is a tree ensemble and by permutation importance otherwise. Linear models
get their standardised coefficients, which answer the same question exactly for
that family and need no approximation.

What to look for, in order:

    the top of the list      should be volume: the window mean, the EWMA level,
                             the season average. A market whose top feature is
                             the weather or the day of the week has learned
                             something that is not football.
    depth_rank and role      should appear. They are the only features that can
                             react to a role change before production does.
    the opponent             should be present and small. A matchup adjustment
                             that dominates volume is a model that thinks the
                             defense picks the touches.
    anything near zero       is a feature we are paying to compute for nothing,
                             and there are eighty of them per market.
"""

import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from psycopg2.extras import RealDictCursor

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")
import train as tr  # noqa: E402

ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
TOP = int(os.getenv("TOP_FEATURES", "18"))
SAMPLE = int(os.getenv("SHAP_SAMPLE", "2000"))
MARKETS = ["recs", "rec_yds", "rush_yds", "rush_att",
           "pass_yds", "pass_att", "pass_completions"]


def active(code):
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT a.model_name FROM active_models a
            JOIN prop_markets p ON p.id = a.market_id
            WHERE p.code = %s AND a.lookback = 5
        """, (code,))
        r = cur.fetchone()
    return r["model_name"] if r else None


def rows(code):
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code=%s",
                    (code,))
        m = cur.fetchone()
        cur.execute("""
            SELECT pmf.player_id, p.position, pmf.as_of_game_date, pmf.opponent,
                   pmf.team, pmf.mean, pmf.stddev, pmf.weighted_mean, pmf.trend,
                   pmf.aux_mean, pmf.aux_trend, pmf.extra_features,
                   pmf.label_actual
            FROM player_market_features pmf
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pmf.market_id=%s AND pmf.lookback=5
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date DESC
            LIMIT %s
        """, (m["id"], SAMPLE))
        got = cur.fetchall()
    df = pd.DataFrame(got)
    elig = m.get("eligible_positions")
    if elig is not None and not df.empty:
        df = df[df["position"].isin(list(elig))]
    return df


def importance(model, X, cols, y):
    """Mean absolute SHAP where a tree explainer applies, else permutation."""
    # A linear model's standardised coefficient is the exact answer, so no
    # approximation is used where none is needed.
    inner = getattr(model, "steps", [(None, model)])[-1][1]
    if hasattr(inner, "coef_") and not hasattr(inner, "estimators_"):
        try:
            sd = X[cols].std().replace(0, np.nan)
            coef = pd.Series(np.ravel(inner.coef_), index=cols)
            return (coef.abs() * sd).fillna(0.0), "standardised |coefficient|"
        except Exception:
            pass
    try:
        import shap  # noqa: PLC0415
        try:
            ex = shap.TreeExplainer(inner)
            vals = ex.shap_values(X[cols], check_additivity=False)
        except Exception:
            # The model-agnostic explainer is minutes per market and prints a
            # progress bar per row. Permutation importance answers the same
            # question here in seconds, so the fallback goes there rather than
            # to a slower approximation of SHAP.
            raise RuntimeError("no tree explainer for this family")
        vals = np.asarray(vals)
        if vals.ndim == 3:
            vals = vals.mean(axis=2)
        return pd.Series(np.abs(vals).mean(axis=0), index=cols), "mean |SHAP|"
    except Exception as e:
        from sklearn.inspection import permutation_importance  # noqa: PLC0415
        r = permutation_importance(model, X[cols], y, n_repeats=3,
                                   random_state=0, scoring="neg_mean_absolute_error")
        return (pd.Series(r.importances_mean, index=cols),
                f"permutation (no SHAP: {type(e).__name__})")


def main():
    for code in MARKETS:
        name = active(code)
        if not name:
            print(f"\n{code}: no active model\n")
            continue
        mp = ARTIFACT_DIR / f"{name}_{code}_lb5.joblib"
        jp = ARTIFACT_DIR / f"{name}_{code}_lb5.json"
        if not mp.exists() or not jp.exists():
            print(f"\n{code}: artifact missing for {name}\n")
            continue
        meta = json.loads(jp.read_text())
        cols = meta["feature_cols"]
        df = rows(code)
        if df.empty or len(df) < 100:
            print(f"\n{code}: too few rows\n")
            continue
        X, _ = tr._build_feature_dataframe(df)
        for c in cols:
            if c not in X.columns:
                X[c] = 0.0
        y = df["label_actual"].astype(float).to_numpy()
        model = joblib.load(mp)
        imp, how = importance(model, X, cols, y)
        imp = imp.sort_values(ascending=False)
        total = imp.sum() or 1.0

        print(f"\n=== {code} ({name}, {len(cols)} features, {len(df)} rows) "
              f"[{how}] ===")
        for f, v in imp.head(TOP).items():
            bar = "#" * max(1, int(round(v / imp.iloc[0] * 40)))
            print(f"  {f:<32}{v / total:>7.1%}  {bar}")
        dead = imp[imp <= imp.max() * 0.002]
        print(f"  ... {len(dead)} feature(s) contribute under 0.2% of the top "
              f"one's weight")
        if len(dead):
            print("      " + ", ".join(list(dead.index)[:12])
                  + (" ..." if len(dead) > 12 else ""))


if __name__ == "__main__":
    main()
