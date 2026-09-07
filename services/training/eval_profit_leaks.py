"""Where the money actually goes.

The record says the model claims 65.6% and hits 51.5%, for -0.5% a unit. That
gap could mean two completely different things and they have opposite fixes:

  * **The ranking works, the scale is wrong.** A 70% claim really does win more
    often than a 55% claim, the numbers are just inflated. Then recalibration
    fixes it and the EV filter starts selecting real edges.
  * **The ranking is noise.** A 70% claim wins at the same rate as a 55% claim.
    Then no amount of calibration helps, every tier is decoration, and the
    honest answer is that the model cannot rank bets.

Nothing downstream can be judged until that is settled, because **the EV filter
is applied to the inflated probability**. If P is 14 points high, then
`EV = P - breakeven` is 14 points high too, and "EV > 10%" is really selecting
bets at roughly -4% EV. That alone would explain a negative return with no other
bug present.

So this measures, in order:

  1. **Discrimination.** AUC of win_prob against hit. This is the only question
     that matters first. 0.50 is a coin flip.
  2. **Calibration, and what it is worth.** Fit isotonic regression on one half
     of the seasons and apply it to the other, then re-run the EV filter on the
     corrected probability. Fitting and scoring on the same rows would prove
     nothing.
  3. **Where the loss concentrates.** By tier, market, side, price bucket and
     line tier, so a single leaking bucket cannot hide inside the average.
  4. **Concentration.** How many picks land on the same game, because twenty
     bets on one game is one bet with extra steps and makes every interval on
     this page too narrow.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)


def load(engine) -> pd.DataFrame:
    df = pd.read_sql(text("""
        SELECT player_id, player_name, game_date, season, market_code, line,
               projection, projection_median, recommended_side, win_prob,
               expected_value, edge_tier, actual, hit, price_american
        FROM prop_edge_results
        WHERE hit IS NOT NULL AND win_prob IS NOT NULL
          AND price_american IS NOT NULL
    """), engine)
    p = df["price_american"].astype(float)
    df["decimal"] = np.where(p < 0, 1 + 100.0 / -p, 1 + p / 100.0)
    df["breakeven"] = 1.0 / df["decimal"]
    df["hit"] = df["hit"].astype(float)
    df["units"] = np.where(df["hit"] > 0, df["decimal"] - 1.0, -1.0)
    return df


def auc(scores, labels) -> float:
    """Probability a random winner outranks a random loser.

    Computed from rank sums rather than a curve, which handles ties correctly.
    0.50 means the score carries no ordering information at all.
    """
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=float)
    pos, neg = y.sum(), (1 - y).sum()
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(1, len(s) + 1)
    # Average ranks within ties so a flat score cannot look like a signal.
    df = pd.DataFrame({"s": s, "r": ranks})
    ranks = df.groupby("s")["r"].transform("mean").to_numpy()
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def isotonic(x_fit, y_fit, x_apply):
    """Monotone recalibration, pool-adjacent-violators.

    Isotonic rather than a logistic fit because the miscalibration here is not a
    simple shift: it varies with the claimed probability, and isotonic can
    follow whatever shape the data has while still guaranteeing that a higher
    claim never maps to a lower corrected probability.
    """
    from sklearn.isotonic import IsotonicRegression
    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
    ir.fit(np.asarray(x_fit, dtype=float), np.asarray(y_fit, dtype=float))
    return ir.predict(np.asarray(x_apply, dtype=float))


def boot_roi(f, n_boot=2000, seed=5):
    """Slate-clustered bootstrap of ROI."""
    if len(f) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    groups = [g["units"].to_numpy() for _, g in f.groupby("game_date")]
    out = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(groups), len(groups))
        out.append(np.concatenate([groups[i] for i in idx]).mean())
    return tuple(np.percentile(out, [2.5, 97.5]))


HDR = f"{'':<34}{'n':>7}{'hit':>8}{'be':>8}{'edge':>9}{'ROI':>9}"


def row(f, label, ci=False):
    if len(f) == 0:
        return f"{label:<34}{0:>7}"
    hit, be = f["hit"].mean(), f["breakeven"].mean()
    s = (f"{label:<34}{len(f):>7}{hit:>8.3f}{be:>8.3f}{hit - be:>+9.3f}"
         f"{f['units'].mean():>+9.3f}")
    if ci:
        lo, hi = boot_roi(f)
        s += f"   [{lo:+.3f}, {hi:+.3f}]" + ("  PROFITABLE" if lo > 0 else "")
    return s


def main():
    engine = create_engine(DATABASE_URL, future=True)
    d = load(engine)
    if d.empty:
        raise SystemExit("no graded picks")
    print(f"{len(d)} graded picks, {d['game_date'].nunique()} slates, "
          f"seasons {sorted(d['season'].dropna().unique().astype(int))}")
    print()

    # ---------------------------------------------------------------- 1
    print("=== 1. DOES THE PROBABILITY RANK ANYTHING? ===")
    print("AUC is the chance a random winning pick was scored above a random")
    print("losing one. 0.50 is a coin flip and would mean the ranking, the")
    print("tiers and the EV filter are all decoration.")
    print()
    overall = auc(d["win_prob"], d["hit"])
    print(f"{'win_prob, all picks':<34}AUC {overall:.4f}   n={len(d)}")
    for mk, sub in d.groupby("market_code"):
        if len(sub) >= 200:
            print(f"{'  ' + mk:<34}AUC {auc(sub['win_prob'], sub['hit']):.4f}"
                  f"   n={len(sub)}")
    ev_auc = auc(d["expected_value"], d["hit"])
    print(f"{'expected_value, all picks':<34}AUC {ev_auc:.4f}")
    # The raw model number, untouched by any calibration, as a control.
    gap = (d["projection_median"] - d["line"]).abs()
    print(f"{'|median - line|, as a control':<34}AUC {auc(gap, d['hit']):.4f}")

    # ---------------------------------------------------------------- 2
    print()
    print("=== 2. IS RECALIBRATION WORTH ANYTHING? ===")
    seasons = sorted(d["season"].dropna().unique())
    if len(seasons) >= 2:
        fit_seasons, test_season = seasons[:-1], seasons[-1]
        fit = d[d["season"].isin(fit_seasons)]
        test = d[d["season"] == test_season].copy()
        print(f"isotonic fitted on {sorted(int(s) for s in fit_seasons)} "
              f"({len(fit)} picks), applied to {int(test_season)} ({len(test)})")
        test["p_cal"] = isotonic(fit["win_prob"], fit["hit"], test["win_prob"])
        test["ev_cal"] = test["p_cal"] - test["breakeven"]
        print()
        print(f"claimed {test['win_prob'].mean():.3f} -> calibrated "
              f"{test['p_cal'].mean():.3f}, actual {test['hit'].mean():.3f}")
        print()
        print(HDR + f"{'95% CI on ROI':>26}")
        print(row(test, "every pick", ci=True))
        for thr in (0.00, 0.02, 0.04, 0.06):
            sub = test[test["ev_cal"] > thr]
            if len(sub) >= 40:
                print(row(sub, f"calibrated EV > {thr:.0%}", ci=True))
        print()
        print("For contrast, the same season filtered on the UNcalibrated EV")
        print("the site currently uses:")
        for thr in (0.02, 0.06, 0.10):
            sub = test[test["expected_value"] > thr]
            if len(sub) >= 40:
                print(row(sub, f"raw EV > {thr:.0%}"))
    else:
        print("need at least two seasons to fit and test separately")

    # ---------------------------------------------------------------- 3
    print()
    print("=== 3. WHERE DOES THE LOSS CONCENTRATE? ===")
    print(HDR)
    print(row(d, "everything"))
    print()
    for tier in ("elite", "strong", "medium", "small"):
        print(row(d[d["edge_tier"] == tier], f"tier {tier}"))
    print()
    for side in ("over", "under"):
        print(row(d[d["recommended_side"] == side], f"side {side}"))
    print()
    for mk, sub in d.groupby("market_code"):
        if len(sub) >= 150:
            print(row(sub, mk))
    print()
    # Price matters: a favourite has to win far more often to break even, and
    # the model's overconfidence bites hardest exactly there.
    bands = [(-10000, -200, "heavy favourite (<= -200)"),
             (-200, -140, "-200 to -140"),
             (-140, -110, "-140 to -110"),
             (-110, 100, "-110 to +100"),
             (100, 10000, "underdog (>= +100)")]
    for lo, hi, lab in bands:
        sub = d[(d["price_american"] >= lo) & (d["price_american"] < hi)]
        if len(sub) >= 100:
            print(row(sub, lab))

    # ---------------------------------------------------------------- 4
    print()
    print("=== 4. HOW CONCENTRATED IS THE BOOK? ===")
    per_slate = d.groupby("game_date").size()
    per_player = d.groupby(["game_date", "player_id"]).size()
    print(f"picks per slate    median {per_slate.median():.0f}, "
          f"max {per_slate.max()}")
    print(f"picks per player   median {per_player.median():.0f}, "
          f"max {per_player.max()}")
    multi = (per_player > 1).mean()
    print(f"{multi:.1%} of player-games carry more than one pick.")
    print("Those are not independent bets: receptions and receiving yards on the")
    print("same player win and lose together, so stacking them multiplies")
    print("variance without multiplying edge, and makes every interval on the")
    print("track record narrower than it should be.")

    # One pick per player-game, keeping the strongest, as a variance check.
    one = (d.sort_values("expected_value", ascending=False)
             .drop_duplicates(subset=["game_date", "player_id"]))
    print()
    print(HDR + f"{'95% CI on ROI':>26}")
    print(row(d, "all picks", ci=True))
    print(row(one, "one pick per player-game", ci=True))


if __name__ == "__main__":
    main()
