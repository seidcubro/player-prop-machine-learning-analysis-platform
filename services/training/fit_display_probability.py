"""How often does this pick actually hit? The published probability.

The model ranks well and claims far too much: on 2025 it rated picks at 70% or
better and they won 57%. The board needs the honest figure, because it is the
one thing a reader is really asking, and because expected value is computed
from it, so an inflated probability inflates every edge on the page.

Two things predict whether a pick lands: how confident the model is, and what
the price says. Fitted together, per side, as a logistic regression on graded
picks:

    logit(P) = a + b * logit(model probability) + c * logit(price probability)

Fitting them together rather than correcting the model's number alone is what
keeps expected value honest. The model's confidence ignores the price, and
plus-money picks win less often than minus-money ones by construction, so a
confidence-only correction rated a +135 pick at 58% when its band only reaches
that across all prices. Predicted EV against realized return, by quarter, on
the 2025 holdout:

    confidence only    -5%/-3%   -0%/+4%   +3%/+4%   +10%/+3%
    with the price     -2%/-1%   -0%/+4%   +2%/+1%    +7%/+4%

The fitted weights say where the model is worth anything:

    under   confidence +0.42   price +0.54    log loss 0.7233 -> 0.6911
    over    confidence -0.48   price +0.94    log loss 0.7108 -> 0.6903

On unders the model's confidence predicts hits. On overs the weight is
negative: once the price is known, a more confident over is slightly less
likely to land. That is the same conclusion the graded record reaches from the
other direction, where elite overs lost in two seasons of three. Overs are
still published and still ranked; the number beside them is just honest about
what the model adds, which is nothing.

A side ships only if it beats the uncorrected probability on a season it was
not fitted to. Accepted sides are refitted on every complete season, since the
acceptance was already earned out of sample.

Reads COALESCE(win_prob_model, win_prob), the model's figure before any display
correction, so a refit never learns from its own output.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
OUT = ARTIFACTS / "display_probability.json"

MIN_FIT = 300
MIN_TEST = 80
EPS = 1e-6
# Never published as a certainty, however much history agrees. A prop is one
# game.
FLOOR, CEILING = 0.05, 0.95


def log_loss(y, p):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def season_of(dates):
    d = pd.to_datetime(dates)
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def implied(price):
    """The win rate a price needs to break even, vig included."""
    price = np.asarray(price, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(price > 0, 100.0 / (price + 100.0),
                        -price / (-price + 100.0))


def fit_curve(model_p, market_p, y) -> dict:
    """Weights for the model's probability and the price's, on this side."""
    x = np.c_[logit(model_p), logit(market_p)]
    m = LogisticRegression(C=1e6).fit(x, np.asarray(y, dtype=int))
    return {"a": float(m.intercept_[0]),
            "b_model": float(m.coef_[0][0]),
            "b_market": float(m.coef_[0][1])}


def apply_curve(curve: dict, model_p, market_p) -> float:
    """The published probability for one pick, clamped away from certainty."""
    z = (curve["a"] + curve["b_model"] * float(logit(model_p))
         + curve["b_market"] * float(logit(market_p)))
    return float(min(max(1.0 / (1.0 + np.exp(-z)), FLOOR), CEILING))


def band_report(p, y, model_p):
    out = []
    for lo, hi, label in ((0.70, 1.01, "70%+"), (0.60, 0.70, "60-70%")):
        m = (model_p >= lo) & (model_p < hi)
        if m.sum() >= 40:
            out.append(f"{label}: says {np.mean(p[m]):.0%}, hit {np.mean(y[m]):.0%} (n={int(m.sum())})")
    return "; ".join(out)


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = pd.read_sql(
        text(
            """
            SELECT recommended_side AS side, game_date, price_american,
                   COALESCE(win_prob_model, win_prob) AS model_p, hit
            FROM prop_edge_results
            WHERE hit IS NOT NULL
              AND price_american IS NOT NULL AND price_american <> 0
              AND COALESCE(win_prob_model, win_prob) BETWEEN 0.001 AND 0.999
            """
        ),
        eng,
    )
    result = {"sides": {}}
    if df.empty:
        print("no graded picks; nothing to fit")
        OUT.write_text(json.dumps(result), encoding="utf-8")
        return

    df["y"] = df["hit"].astype(int)
    df["market_p"] = implied(df["price_american"])
    df["season"] = season_of(df["game_date"])
    current = int(season_of(pd.Series([pd.Timestamp.today()])).iloc[0])
    complete = sorted(s for s in df["season"].unique() if s < current)
    if len(complete) < 2:
        print("need two complete seasons to fit and score; nothing written")
        OUT.write_text(json.dumps(result), encoding="utf-8")
        return
    holdout = complete[-1]
    result["holdout_season"] = int(holdout)
    print(f"validated on {holdout}, fitted on the seasons before it\n")

    for side in ("under", "over"):
        d = df[df["side"] == side]
        fit, test = d[d["season"] < holdout], d[d["season"] == holdout]
        if len(fit) < MIN_FIT or len(test) < MIN_TEST:
            print(f"{side:<7} too few rows ({len(fit)} to fit, {len(test)} to score)")
            result["sides"][side] = {"accepted": False, "reason": "too few rows"}
            continue
        curve = fit_curve(fit["model_p"], fit["market_p"], fit["y"])
        mp, y = test["model_p"].to_numpy(), test["y"].to_numpy()
        p = np.array([apply_curve(curve, m, k) for m, k
                      in zip(mp, test["market_p"].to_numpy())])
        raw_ll, cal_ll = log_loss(y, mp), log_loss(y, p)
        accepted = cal_ll < raw_ll
        print(f"{side:<7} log loss {raw_ll:.4f} -> {cal_ll:.4f}  "
              f"weights: confidence {curve['b_model']:+.2f}, "
              f"price {curve['b_market']:+.2f}  "
              f"{'ACCEPTED' if accepted else 'not applied'}")
        print(f"        {band_report(p, y, mp)}")
        whole = d[d["season"].isin(complete)]
        ship = (fit_curve(whole["model_p"], whole["market_p"], whole["y"])
                if accepted else None)
        result["sides"][side] = {
            "accepted": bool(accepted),
            "curve": ship,
            "holdout_log_loss": [raw_ll, cal_ll],
            "holdout_rows": int(len(test)),
            "fitted_rows": int(len(d[d["season"].isin(complete)])),
        }

    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    live = [s for s, v in result["sides"].items() if v.get("accepted")]
    print(f"\nwrote {OUT.name}: {', '.join(live) if live else 'no side'} accepted")


def load(artifact_dir) -> dict:
    """{side: curve} for accepted sides only. Missing artifact: no correction."""
    path = Path(artifact_dir) / "display_probability.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {s: v["curve"] for s, v in data.get("sides", {}).items()
            if v.get("accepted") and v.get("curve")}


if __name__ == "__main__":
    main()
