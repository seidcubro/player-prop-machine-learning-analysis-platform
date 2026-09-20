"""Fit how far to believe the model against the line, per side.

The published win probability is shrunk toward the probability the price
implies, in log-odds:

    logit(P) = logit(implied) + w * (logit(model) - logit(implied))

w = 1 is the model as it is; w = 0 is the line. Graded picks put the answer far
below 1. On 2025, fitted on the seasons before it, elite picks claimed 65.9%
and won 53.5%, and the blend put them at 53.3%; log loss improved on every
pooled group. For overs the fitted weight was 0.00: on 1,433 over picks the
model's disagreement with the line carried no information the line did not.

This changes what the board says about a pick, not which picks it makes. The
same backtest re-selected elite picks by the blended edge and lost money doing
it (+3.9% to +2.9%), so tiers, best bets and value flags are still chosen on
the model's own edge. Only the published probability and EV use this.

Fitted per side. Validated on the last complete season with a fit on the
seasons before it; a side is accepted only if the blend beats the published
probability there on log loss. An accepted side ships with w refitted on every
complete season, which for a single parameter is more data at no cost in
honesty: the acceptance was earned out of sample.

Reads COALESCE(win_prob_model, win_prob): the model's own probability. Once the
blend is live, win_prob holds its output, and fitting on that would learn from
itself. See db/migrations/add_win_prob_model.sql.
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
OUT = ARTIFACTS / "market_blend.json"

MIN_FIT = 300
MIN_TEST = 80
W_GRID = np.round(np.arange(0.0, 1.21, 0.02), 2)
EPS = 1e-6


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def implied(price):
    price = np.asarray(price, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(price > 0, 100.0 / (price + 100.0), -price / (-price + 100.0))


def blend(model_p, market_p, w):
    lm = logit(market_p)
    return sigmoid(lm + w * (logit(model_p) - lm))


def log_loss(y, p):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_w(g) -> float:
    y, m, k = g["y"].to_numpy(), g["model_p"].to_numpy(), g["implied"].to_numpy()
    return float(W_GRID[int(np.argmin([log_loss(y, blend(m, k, w)) for w in W_GRID]))])


def season_of(dates):
    d = pd.to_datetime(dates)
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = pd.read_sql(
        text(
            """
            SELECT recommended_side AS side, game_date,
                   COALESCE(win_prob_model, win_prob) AS model_p,
                   price_american, hit
            FROM prop_edge_results
            WHERE hit IS NOT NULL
              AND COALESCE(win_prob_model, win_prob) > 0
              AND COALESCE(win_prob_model, win_prob) < 1
              AND price_american IS NOT NULL AND price_american <> 0
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
    df["implied"] = implied(df["price_american"])
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
    print(f"{'side':<7}{'n test':>7}{'w':>6}{'claimed':>9}{'blended':>9}{'won':>7}"
          f"{'ll raw':>9}{'ll blend':>10}  verdict")

    for side in ("under", "over"):
        d = df[df["side"] == side]
        fit = d[d["season"] < holdout]
        test = d[d["season"] == holdout]
        if len(fit) < MIN_FIT or len(test) < MIN_TEST:
            print(f"{side:<7}{len(test):>7}  too few rows ({len(fit)} to fit)")
            result["sides"][side] = {"accepted": False, "reason": "too few rows"}
            continue
        w = fit_w(fit)
        y, m, k = test["y"].to_numpy(), test["model_p"].to_numpy(), test["implied"].to_numpy()
        b = blend(m, k, w)
        raw_ll, bl_ll = log_loss(y, m), log_loss(y, b)
        accepted = bl_ll < raw_ll
        # Refit on every complete season once earned; see the module docstring.
        w_ship = fit_w(d[d["season"].isin(complete)]) if accepted else 1.0
        print(f"{side:<7}{len(test):>7}{w:>6.2f}{m.mean():>9.1%}{b.mean():>9.1%}"
              f"{y.mean():>7.1%}{raw_ll:>9.4f}{bl_ll:>10.4f}  "
              + (f"ACCEPTED, shipping w={w_ship:.2f}" if accepted else "not applied"))
        result["sides"][side] = {
            "accepted": bool(accepted),
            "w": float(w_ship),
            "w_validated": float(w),
            "holdout_log_loss": [raw_ll, bl_ll],
            "holdout_claimed_blended_won": [float(m.mean()), float(b.mean()), float(y.mean())],
            "holdout_rows": int(len(test)),
        }

    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    live = [s for s, v in result["sides"].items() if v.get("accepted")]
    print(f"\nwrote {OUT.name}: {', '.join(live) if live else 'no side'} accepted")


def load(artifact_dir) -> dict:
    """{side: w} for accepted sides only. Missing artifact: no blend."""
    path = Path(artifact_dir) / "market_blend.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {s: float(v["w"]) for s, v in data.get("sides", {}).items() if v.get("accepted")}


if __name__ == "__main__":
    main()
