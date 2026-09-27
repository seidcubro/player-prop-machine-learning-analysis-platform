"""Is our projection better, worse, or complementary to the line?

The framework is nfelo's, from "Using Market Regression to Improve Prediction
Accuracy in the NFL" (2020). Their result on game spreads: the market alone
beats their model alone, and a weighted average of the two beats both. The
reasoning is that a market price is the aggregate of many independent models, so
any single model is likely to hold some signal the aggregate has diluted, while
the aggregate holds much that the single model has missed.

It also supplies a diagnostic worth more than the blend itself:

    "A model that achieves its greatest accuracy when the market's price is
    weighted 100% contains no signal. Generally speaking, the more accurate a
    model is relative to the market, the less it should be regressed."

That is a direct, per-market answer to the question this project keeps circling:
does our projection add anything to the price at all? Six previous measurements
have said the model does not beat the price on mainline props. None of them asked
whether it adds anything *to* the price, which is a different question with a
different answer, and the one that decides whether the projection is worth
publishing beside a line.

Three point forecasts of the same outcome, on the same rows:

    model      what we project
    line       the sportsbook's number, treated as its projection, which for a
               half-point line is approximately its median
    blend      w * model + (1 - w) * line

w is fitted on the earlier seasons and scored on the later ones, never on the
rows that judge it. Reported per market, because the bakeoff already showed these
markets do not behave alike and a pooled answer would hide it.

Two metrics. MAE, because it is the projection's own units and the loss the point
models are fitted on. And side accuracy: how often the forecast lands on the same
side of the line as the outcome did, which is the only thing the board actually
does with a projection. A blend can be closer on average and no better at
picking, and if so it is worth knowing before anything changes.

One caveat that cannot be fixed from this table. These 7,153 rows are published
picks, not every priced prop, so they are selected precisely where the model and
the line disagreed most. nfelo's framework predicts that is where a model is
least reliable, so this is the hardest population for the model and the most
relevant one for the board. A result here is about the picks we make, not about
the projection in general.

Verdict, September 2026. Fitted on 2023-2024, scored on 2025-2026:

    market     w* on our model   MAE model   MAE line   MAE blend
    recs             0.35           1.541      1.510      1.495
    rush_yds         0.25          19.908     18.817     18.880
    rec_yds          0.10          20.664     19.290     19.305
    rush_att         0.05           3.396      3.169      3.164

The line is the better point forecast in every market, by 3 to 7%, and the
fitted weight on our own projection is between 5% and 35%. The blend beats both
on receptions and rush attempts and sits a hair behind the line on the two
yardage markets, where w was fitted on a few hundred rows. So the model holds
signal, and not much of it. That is the same answer six other measurements have
given, in the most useful form yet: a number per market for how much of our own
projection is worth keeping.

The side columns are identical to three decimals, and they have to be. A convex
combination of the projection and the line lies between them, so it is on the
same side of the line as the projection, always. Market regression cannot change
which side we pick. What it can do is make the published number defensible
without touching pick selection, which is the entire complaint that prompted
this: a projection blended at w=0.25 cannot read 24 against a line of 38.5.

What it would change instead is the size of the disagreement, and therefore how
many picks clear the EV bar. That needs its own test before anything ships,
because this project has already measured that its biggest disagreements are
where it is most wrong, and a blend concentrates the board on exactly those.

The last table is a separate finding. Its "flipped" column is not the blend, it
is our median-based side selection disagreeing with the mean on 21 to 37% of
picks. Forcing the mean gives rec_yds -4.4% against +1.8%, rush_yds -2.7%
against +0.8%, rush_att +1.0% against +2.8%, recs +2.8% against +1.5%: worse on
three of four markets, which independently re-confirms that the low median is
the edge.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
# Weight on our model. 0.0 is the line alone, 1.0 is the model alone.
W_GRID = np.round(np.arange(0.0, 1.05, 0.05), 2)
FIT_THROUGH = 2024
MIN_ROWS = 120


def load(eng):
    d = pd.read_sql(text("""
        SELECT market_code, season, game_date, player_name, line, projection,
               projection_median, actual, price_american, recommended_side, hit
        FROM prop_edge_results
        WHERE actual IS NOT NULL AND line IS NOT NULL
          AND projection IS NOT NULL AND price_american IS NOT NULL
          AND price_american <> 0
    """), eng)
    for c in ("line", "projection", "projection_median", "actual"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["line", "projection", "actual"])
    d["season"] = pd.to_numeric(d["season"], errors="coerce")
    return d.dropna(subset=["season"])


def mae(pred, actual):
    return float(np.mean(np.abs(np.asarray(pred, float) - np.asarray(actual, float))))


def side_rate(pred, line, actual):
    """How often the forecast is on the same side of the line as the outcome.

    Pushes are impossible on a half-point line and dropped elsewhere, because a
    forecast cannot be right or wrong about a side that did not happen.
    """
    pred, line, actual = (np.asarray(x, float) for x in (pred, line, actual))
    call = np.sign(pred - line)
    truth = np.sign(actual - line)
    live = (call != 0) & (truth != 0)
    if not live.any():
        return float("nan")
    return float((call[live] == truth[live]).mean())


def profit(hit, price):
    price = np.asarray(price, float)
    pay = np.where(price > 0, price / 100.0, 100.0 / -price)
    return np.where(np.asarray(hit, bool), pay, -1.0)


def main():
    eng = create_engine(DATABASE_URL, future=True)
    d = load(eng)
    print(f"{len(d)} graded picks, seasons {int(d['season'].min())}"
          f"-{int(d['season'].max())}. w is the weight on OUR model, so w=0 is "
          f"the line alone.\nFitted on {FIT_THROUGH} and earlier, scored on "
          f"later seasons.\n")

    print(f"{'market':<18}{'n fit':>7}{'n test':>7}{'w*':>6}"
          f"{'MAE model':>11}{'MAE line':>10}{'MAE blend':>11}"
          f"{'side mdl':>10}{'side blend':>12}")
    verdicts = []
    for code, g in d.groupby("market_code"):
        fit = g[g["season"] <= FIT_THROUGH]
        test = g[g["season"] > FIT_THROUGH]
        if len(fit) < MIN_ROWS or len(test) < MIN_ROWS:
            print(f"{code:<18}{len(fit):>7}{len(test):>7}   too few rows")
            continue

        # Fit w on the earlier seasons only.
        best_w, best_mae = None, float("inf")
        for w in W_GRID:
            m = mae(w * fit["projection"] + (1 - w) * fit["line"], fit["actual"])
            if m < best_mae:
                best_w, best_mae = float(w), m

        blend = best_w * test["projection"] + (1 - best_w) * test["line"]
        m_model = mae(test["projection"], test["actual"])
        m_line = mae(test["line"], test["actual"])
        m_blend = mae(blend, test["actual"])
        s_model = side_rate(test["projection"], test["line"], test["actual"])
        s_blend = side_rate(blend, test["line"], test["actual"])

        print(f"{code:<18}{len(fit):>7}{len(test):>7}{best_w:>6.2f}"
              f"{m_model:>11.3f}{m_line:>10.3f}{m_blend:>11.3f}"
              f"{s_model:>9.1%}{s_blend:>11.1%}")
        verdicts.append((code, best_w, m_model, m_line, m_blend, s_model, s_blend,
                         len(test)))

    if not verdicts:
        return

    print("\n" + "=" * 78)
    print("What each market's fitted weight says about whether the model adds "
          "anything:\n")
    for code, w, m_model, m_line, m_blend, s_model, s_blend, n in verdicts:
        if w <= 0.0:
            note = ("no signal at all. The line alone was the most accurate "
                    "forecast available on the fitting seasons.")
        elif w >= 1.0:
            note = ("the model was better alone than blended, which on this "
                    "population would be surprising enough to re-check.")
        else:
            note = (f"{w:.0%} model, {1 - w:.0%} line. The model holds signal "
                    f"the line does not.")
        better = m_blend < min(m_model, m_line)
        print(f"  {code:<18}{note}")
        print(f"  {'':<18}blend {'beats' if better else 'does not beat'} both "
              f"on MAE; side calls {s_blend - s_model:+.1%} vs the model alone "
              f"on {n} picks")

    # What it would have done to the money, using the side the blend picks at
    # the price we actually got. Not a backtest of a strategy, an indication of
    # whether the side changes would have helped.
    print("\n" + "=" * 78)
    print("If the blend had picked the side, at the same prices:\n")
    print(f"{'market':<18}{'picks':>7}{'flipped':>9}{'roi model':>11}"
          f"{'roi blend':>11}")
    for code, w, *_ , n in verdicts:
        g = d[(d["market_code"] == code) & (d["season"] > FIT_THROUGH)].copy()
        blend = w * g["projection"] + (1 - w) * g["line"]
        want = np.where(blend > g["line"], "over", "under")
        # hit is recorded for the side we published; a flip inverts it, except
        # on the pushes that a half-point line makes impossible anyway.
        flipped = want != g["recommended_side"].to_numpy()
        hit_blend = np.where(flipped, ~g["hit"].astype(bool), g["hit"].astype(bool))
        p_model = profit(g["hit"].astype(bool), g["price_american"])
        p_blend = profit(hit_blend, g["price_american"])
        print(f"{code:<18}{len(g):>7}{flipped.mean():>8.0%}"
              f"{p_model.mean():>+10.1%}{p_blend.mean():>+10.1%}")
    print("\nA flip is only informative where the price was not itself chosen "
          "because of the side,\nwhich it was. Read the ROI columns as a "
          "direction, not as a backtest.")


if __name__ == "__main__":
    main()
