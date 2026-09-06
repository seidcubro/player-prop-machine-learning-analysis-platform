"""Where does the model overproject, and why?

Guards against the failure everyone worries about: a low-usage player being
projected like a star. Scores the held-out split of the active model and breaks
the error down by role size, sample size, and season phase, so any fix targets
the real mechanism rather than an assumed one.
"""
import os

import numpy as np
import pandas as pd
import joblib

import eval as ev  # reuse the honest loader/split


def main():
    meta = ev.load_model_metadata()
    feature_cols = meta["feature_cols"]
    df = ev.load_labeled_rows(feature_cols)
    if ev.SPLIT_MODE == "season":
        _, test_df = ev.season_split(df)
    else:
        _, test_df = ev.time_split(df, ev.TEST_FRAC)
    X = ev.build_feature_matrix(test_df, feature_cols)
    model = joblib.load(
        os.path.join(ev.ARTIFACT_DIR, os.path.basename(meta["artifact_path"]))
    )

    t = test_df.copy()
    t["pred"] = model.predict(X)
    t["actual"] = t[ev.LABEL_COL].astype(float)
    t["err"] = t["pred"] - t["actual"]

    ex = t["extra_features"].apply(ev._normalize_extra_features)
    t["season_n"] = ex.apply(lambda d: d.get("y_season_n", 0.0))
    t["snap"] = ex.apply(lambda d: d.get("snap_share_mean", 0.0))
    t["y_max"] = ex.apply(lambda d: d.get("y_max", 0.0))
    t["y_med"] = ex.apply(lambda d: d.get("y_median", 0.0))

    agg = dict(
        rows=("err", "size"),
        bias=("err", "mean"),
        mae=("err", lambda s: s.abs().mean()),
        p95_over=("err", lambda s: s.quantile(0.95)),
    )

    print(f"\n=== {ev.MARKET_CODE} / {meta['model_name']} - test rows {len(t)} ===")

    print("\n-- error by games played this season (sample size) --")
    t["nbin"] = pd.cut(t["season_n"], [-0.1, 1, 2, 4, 8, 100],
                       labels=["0-1", "2", "3-4", "5-8", "9+"])
    print(t.groupby("nbin", observed=True).agg(**agg).round(2).to_string())

    print("\n-- error by rolling snap share (role size) --")
    t["sbin"] = pd.cut(t["snap"], [-0.1, .2, .4, .6, .8, 1.01],
                       labels=["<20%", "20-40%", "40-60%", "60-80%", "80%+"])
    print(t.groupby("sbin", observed=True).agg(**agg).round(2).to_string())

    # Early season is the real production risk: in September the rolling window
    # is built entirely from *last* season's games, so an offseason change of
    # team, role, or depth position is invisible to it.
    t["mon"] = pd.to_datetime(t["as_of_game_date"]).dt.month
    t["phase"] = np.where(t["mon"] == 9, "Sep (wk1-4, stale window)",
                          np.where(t["mon"] == 10, "Oct", "Nov+ (in-season)"))
    print("\n-- error by season phase --")
    g = t.groupby("phase", observed=True).agg(**agg)
    # how often we project a materially bigger game than actually happened
    g["pred_2x_actual"] = (
        t.assign(m=(t["pred"] > 2 * t["actual"]) & (t["actual"] >= 5))
        .groupby("phase", observed=True)["m"].mean()
    )
    print(g.round(3).to_string())

    print("\n-- the case that matters: small role, big projection --")
    scrub = t[(t["snap"] < 0.35) & (t["y_med"] < 20)]
    print(f"rows with <35% snaps and median <20 yds: {len(scrub)}")
    if len(scrub):
        print(f"  their projections: mean {scrub['pred'].mean():.1f}, "
              f"p95 {scrub['pred'].quantile(0.95):.1f}, max {scrub['pred'].max():.1f}")
        print(f"  their actuals:     mean {scrub['actual'].mean():.1f}, "
              f"p95 {scrub['actual'].quantile(0.95):.1f}, max {scrub['actual'].max():.1f}")
        print(f"  projected over 60 yds: {(scrub['pred'] > 60).sum()} rows")

    print("\n-- worst 10 overprojections --")
    cols = ["pred", "actual", "err", "season_n", "snap", "y_med", "y_max"]
    print(t.nlargest(10, "err")[cols].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
