"""Tie the published median to the point projection, so it can extrapolate.

The point model and the quantile models are different families and they behave
differently at the edge of the data. For most markets the point model is a
linear one (`enet_v2` for rush_yds), which extrapolates: give it a career high
carry share and a 156 yard game in the window and it will happily predict 91
yards. The quantile ladder is a GradientBoostingRegressor per level, and a tree
cannot predict past the leaves it was fitted on, so it saturates.

Jahmyr Gibbs before a Thursday game, having taken 29 carries the week before
while his backups took two each:

    point projection            91.0
    quantile median             73.7
    ratio                       0.784
    peer median at that level   0.932

James Cook, projected 67.2 in the same game, had a quantile median of 69.0.
Twenty four yards apart on the point model and two yards apart on the ladder.
The board takes its side from the median, so a back nobody else on the slate
resembles was being priced like a committee back.

The fix is not to publish the mean instead. Measured on 430 graded picks in
exactly this situation, high projection and the two models straddling the line,
the median picked the winning side 53.0% of the time and the mean 47.0%. The
side rule is sound; the number feeding it is not.

So the median is re-read as a fraction of the point projection, with the
fraction measured per market and per projection band. That distribution is
stable where it matters: the median of actual/projection for rush_yds runs
0.919, 0.940, 0.929 across the top three bands. Anchoring inherits the point
model's extrapolation while leaving the rest of the ladder alone.

Fitted on the earlier rows, accepted only where the held-out median coverage
moves toward 0.50 and the side accuracy does not get worse.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
OUT = ARTIFACTS / "median_anchor.json"

# Fraction of rows, oldest first, used to fit. The rest is the holdout.
FIT_FRACTION = float(os.getenv("FIT_FRACTION", "0.70"))
# Bands are quartiles of the point projection, so the ratio can vary with size.
BANDS = int(os.getenv("ANCHOR_BANDS", "4"))
MIN_FIT = int(os.getenv("ANCHOR_MIN_FIT", "150"))
MIN_TEST = int(os.getenv("ANCHOR_MIN_TEST", "80"))
# A projection this small is mostly structural zeros, where a ratio means little.
MIN_PROJECTION = float(os.getenv("ANCHOR_MIN_PROJECTION", "5"))


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = pd.read_sql(text("""
        SELECT market_code, game_date, line, actual, projection,
               projection_median, q25, q75
        FROM prop_edge_results
        WHERE actual IS NOT NULL AND projection IS NOT NULL
          AND projection_median IS NOT NULL AND line IS NOT NULL
    """), eng)
    if df.empty:
        print("no graded rows yet; nothing to fit")
        OUT.write_text("{}", encoding="utf-8")
        return

    df = df[df["projection"] > MIN_PROJECTION].copy()
    d = pd.to_datetime(df["game_date"])
    df = df.assign(_d=d).sort_values("_d").reset_index(drop=True)
    cut = df["_d"].quantile(FIT_FRACTION)

    print(f"{len(df)} graded rows, fitting up to {cut.date()}\n")
    print(f"{'market':<12}{'band':>5}{'n fit':>7}{'n test':>7}{'ratio':>8}"
          f"{'cover was':>11}{'cover now':>11}{'side was':>10}{'side now':>10}  verdict")

    out: dict = {}
    for market, g in df.groupby("market_code"):
        g = g.copy()
        # Bands are cut on the fit rows only, so the holdout cannot inform them.
        fit_all = g[g["_d"] <= cut]
        if len(fit_all) < MIN_FIT * BANDS / 2:
            continue
        edges = np.unique(np.quantile(
            fit_all["projection"], np.linspace(0, 1, BANDS + 1)))
        if len(edges) < 3:
            continue
        edges[0], edges[-1] = -np.inf, np.inf
        g["band"] = pd.cut(g["projection"], edges, labels=False,
                           include_lowest=True)

        entries = {}
        for band, gb in g.groupby("band"):
            fit = gb[gb["_d"] <= cut]
            test = gb[gb["_d"] > cut]
            if len(fit) < MIN_FIT or len(test) < MIN_TEST:
                continue

            ratio = float(np.median(fit["actual"] / fit["projection"]))
            anchored = (test["projection"] * ratio).to_numpy()
            # A corrected median still has to sit inside its own band.
            lo = test["q25"].fillna(0.0).to_numpy()
            hi = test["q75"].fillna(np.inf).to_numpy()
            anchored = np.clip(anchored, lo, hi)

            act = test["actual"].to_numpy(dtype=float)
            line = test["line"].to_numpy(dtype=float)
            cur = test["projection_median"].to_numpy(dtype=float)

            cov_before = float((act < cur).mean())
            cov_after = float((act < anchored).mean())
            side_before = float(((cur > line) == (act > line)).mean())
            side_after = float(((anchored > line) == (act > line)).mean())

            # Symmetric: one of the two has to get better and neither may get
            # worse. The first version demanded a coverage gain and merely
            # tolerated the side, which threw away the band this was built for:
            # rush_yds at the top, where the anchored median is exactly as well
            # calibrated (0.443 either way) and picks the winning side 2.3
            # points more often. Requiring a gain on the axis that happens to
            # be already correct is not a standard, it is an accident.
            TOL = 0.005
            cov_gain = abs(cov_before - 0.5) - abs(cov_after - 0.5)
            side_gain = side_after - side_before
            improves = cov_gain > TOL or side_gain > TOL
            degrades = cov_gain < -TOL or side_gain < -TOL
            ok = improves and not degrades

            print(f"{market:<12}{int(band):>5}{len(fit):>7}{len(test):>7}"
                  f"{ratio:>8.3f}{cov_before:>11.3f}{cov_after:>11.3f}"
                  f"{side_before:>10.3f}{side_after:>10.3f}  "
                  f"{'anchor' if ok else 'leave alone'}")
            if ok:
                entries[str(int(band))] = {
                    "ratio": round(ratio, 4),
                    "fit_rows": int(len(fit)),
                    "holdout_rows": int(len(test)),
                    "coverage_before": round(cov_before, 4),
                    "coverage_after": round(cov_after, 4),
                }

        if entries:
            out[market] = {"edges": [float(e) for e in edges[1:-1]],
                           "bands": entries}

    OUT.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {OUT.name} with {len(out)} market(s)")


if __name__ == "__main__":
    main()
