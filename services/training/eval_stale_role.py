"""Does a measured stale-demotion adjustment survive out-of-sample testing?

A player whose rolling window is months old and who has since dropped down the
depth chart produces far less than his window suggests -- roughly 44% of his
weighted mean in-sample. The model cannot learn this: the bucket is ~0.3% of
rows, and with `max_features="sqrt"` the relevant feature is almost never even
considered at a split (measured importance 0.0002).

That leaves an explicit adjustment, which is only defensible if the factor is
*estimated on past data and shown to help on future data it never saw*. This
script does exactly that: fit the factor on the earlier portion, apply it to the
later portion, and report whether error improves. If it does not, the adjustment
should not ship.

"Data it never saw" has to mean the model as well as the factor. This used to
load the shipped artifact and predict the later portion with it, but train.py
refits that on every row it has, so the held-out quarter was part of its
training set and the measured improvement was how well a model fits rows it has
already memorised. A model refit on the earlier portion only is used instead.
It costs a fit per market and it is the difference between a measurement and a
guess: the same mistake, made on the spread calibrator, produced a correction
that stretched predictions where honest data said to shrink them by half.

The factor is shrunk toward 1.0 in proportion to sample size (empirical-Bayes
style), so a handful of observations cannot license a huge correction.
"""

import os

import numpy as np
import pandas as pd
import eval as ev
import train as tr
import train_quantiles as tq

STALE_DAYS = float(os.getenv("STALE_DAYS", "60"))
# Prior strength: with PRIOR_N comparable observations the factor is pulled
# halfway back to "no adjustment". Keeps a small sample from driving a big move.
PRIOR_N = float(os.getenv("PRIOR_N", "40"))
# Enough held-out bucket rows to call the result a measurement rather than an
# anecdote. The quarterback markets clear every statistical test above on six
# rows, which is three starters having a bad week, and a 19% cut to every stale
# quarterback projection is not something six rows should be allowed to buy.
MIN_HOLDOUT = int(os.getenv("MIN_HOLDOUT", "25"))
# Graded picks needed before the walk-forward record gets a vote on a factor.
MIN_RECORD_ROWS = int(os.getenv("MIN_RECORD_ROWS", "20"))


def confirm_on_record(market: str, factor: float) -> tuple[bool, str]:
    """Does the graded walk-forward record agree that this bucket underperforms?

    Everything above is measured on labeled feature rows. The site is judged on
    published picks, which is a narrower thing: a book posted a line, the model
    disagreed with it enough to publish, and the game was played and graded.

    `prop_edge_results` holds exactly that, built from models refit on seasons
    strictly before the one they predict. So it is the one place the correction
    can be checked against the product rather than against the dataset. If the
    bucket produces MORE than the rest relative to projection there, a factor
    below 1 is pushing published picks the wrong way, and the holdout MAE that
    licensed it was measured on a population the picks are drawn from but are
    not the same as.

    This is what caught recs. Every statistical test above passed it at 0.802,
    and on graded picks the bucket runs at 1.153 of projection against 1.045
    elsewhere, so the correction would have cut players the model was already
    reading low.

    Returns (confirmed, explanation). Too few graded rows to judge is not a
    confirmation, but it is not a rejection either: it leaves the decision to
    the tests above.
    """
    from sqlalchemy import create_engine, text

    url = os.getenv("DATABASE_URL", "postgresql://app:app@postgres:5432/app")
    q = text("""
        WITH j AS (
          SELECT r.projection, r.actual,
                 COALESCE((f.extra_features->>'depth_rank_delta')::float, 0) AS delta,
                 COALESCE((f.extra_features->>'days_since_last_game')::float, 0) AS stale
          FROM prop_edge_results r
          JOIN player_market_features f
            ON f.player_id = r.player_id
           AND f.as_of_game_date = r.game_date
           AND f.market_id = (SELECT id FROM prop_markets WHERE code = :m)
          WHERE r.market_code = :m AND r.actual IS NOT NULL AND r.projection > 0.5
        )
        SELECT count(*) FILTER (WHERE delta >= 1 AND stale > :s)          AS bn,
               avg(actual / projection) FILTER (WHERE delta >= 1 AND stale > :s) AS br,
               count(*) FILTER (WHERE NOT (delta >= 1 AND stale > :s))    AS rn,
               avg(actual / projection) FILTER (WHERE NOT (delta >= 1 AND stale > :s)) AS rr
        FROM j
    """)
    try:
        with create_engine(url, future=True).connect() as c:
            row = c.execute(q, {"m": market, "s": STALE_DAYS}).mappings().first()
    except Exception as exc:
        return True, f"could not read the graded record ({exc.__class__.__name__})"

    if not row or (row["bn"] or 0) < MIN_RECORD_ROWS or not row["br"] or not row["rr"]:
        return True, (f"only {row['bn'] if row else 0} graded picks in the bucket; "
                      f"nothing to confirm against")

    rel = float(row["br"]) / float(row["rr"])
    msg = (f"graded record: bucket {float(row['br']):.3f} of projection over "
           f"{row['bn']} picks, rest {float(row['rr']):.3f} over {row['rn']}, "
           f"relative {rel:.3f}")
    if rel > 1.0:
        return False, msg + " -- the bucket over-produces, so a cut is backwards"
    return True, msg + " -- agrees the bucket under-produces"

def main():
    meta = ev.load_model_metadata()
    cols = meta["feature_cols"]
    df = ev.load_labeled_rows(cols)

    ex = df["extra_features"].apply(ev._normalize_extra_features)
    df = df.assign(
        delta=ex.apply(lambda d: d.get("depth_rank_delta", 0.0)),
        stale=ex.apply(lambda d: d.get("days_since_last_game", 0.0)),
    )
    df = df.sort_values("as_of_game_date").reset_index(drop=True)

    # Measure on the population the correction is applied to.
    #
    # Every labeled row includes deep reserves nobody posts a line on, and in
    # the demoted-and-stale bucket they dominate: their projections are small,
    # they produce nothing, and the ratio of actual to projection collapses.
    # Fit on all of them and the factor describes third string tight ends.
    #
    # It never touches a third string tight end. It reaches the site through
    # projections that become published picks, and those exist only for players
    # a book prices. On that population the same bucket runs at 0.906 of
    # projection for rec_yds against 1.073 elsewhere, an effect a third the size
    # of the one measured on everyone, and for recs it runs at 1.153 against
    # 1.045, meaning those players are already under-projected and the
    # all-rows factor of 0.78 would have cut them another fifth.
    #
    # Same population filter the quantile calibration uses, for the same reason.
    priced = tq.priced_like_mask(df, ev.MARKET_CODE)
    kept, total = int(priced.sum()), len(df)
    if kept >= 400:
        df = df[priced].reset_index(drop=True)
        print(f"priced-like population: {kept} of {total} rows")
    else:
        print(f"only {kept} priced-like rows of {total}; measuring on all of them")

    cut = int(len(df) * 0.75)
    train, test = df.iloc[:cut], df.iloc[cut:]

    # Refit on the earlier portion so the later portion is genuinely unseen.
    #
    # The family and hyperparameters are whatever the market actually ships, so
    # this measures the model that runs rather than a stand-in for it. It is
    # only the fitting rows that differ.
    model_name = meta.get("model_name") or os.getenv("MODEL_NAME", "rf_default")
    model = tr.build_model(model_name)
    model.fit(ev.build_feature_matrix(train, cols),
              train[ev.LABEL_COL].astype(float))

    def predict(frame):
        return np.clip(model.predict(ev.build_feature_matrix(frame, cols)), 0.0, None)

    def bucket(frame):
        return (frame["delta"] >= 1) & (frame["stale"] > STALE_DAYS)

    tr_b = train[bucket(train)]
    if len(tr_b) < 10:
        print(f"only {len(tr_b)} training rows in bucket; not enough to estimate")
        return

    # Factor = how much of the model's own prediction actually materialises.
    #
    # These are the fitting rows, so a tree ensemble has partly memorised them
    # and the ratio comes out closer to 1 than the truth. That understates the
    # correction rather than inventing one, and the holdout test below is
    # unaffected either way, so it errs in the direction that ships less.
    tr_pred = predict(tr_b)
    tr_actual = tr_b[ev.LABEL_COL].astype(float).to_numpy()
    keep = tr_pred > 1e-6
    raw_factor = float(np.mean(tr_actual[keep] / tr_pred[keep]))

    n = len(tr_b)
    factor = (n * raw_factor + PRIOR_N * 1.0) / (n + PRIOR_N)

    print(f"\n=== {ev.MARKET_CODE} | stale>{STALE_DAYS:.0f}d and demoted ===")
    print(f"train rows in bucket: {n}")
    print(f"raw factor (in-sample):      {raw_factor:.3f}")
    print(f"shrunk factor (prior n={PRIOR_N:.0f}): {factor:.3f}")

    te_b = test[bucket(test)]
    if len(te_b) < 5:
        print(f"\nonly {len(te_b)} held-out rows in bucket -- cannot validate honestly")
        return

    te_pred = predict(te_b)
    te_actual = te_b[ev.LABEL_COL].astype(float).to_numpy()
    adj = te_pred * factor

    def mae(p):
        return float(np.mean(np.abs(p - te_actual)))

    def bias(p):
        return float(np.mean(p - te_actual))

    print(f"\nheld-out rows in bucket: {len(te_b)}")
    print(f"{'':<12}{'MAE':>9}{'bias':>9}")
    print(f"{'unadjusted':<12}{mae(te_pred):>9.2f}{bias(te_pred):>9.2f}")
    print(f"{'adjusted':<12}{mae(adj):>9.2f}{bias(adj):>9.2f}")

    # Placebo control: does the same factor help rows that are NOT in the bucket?
    #
    # MAE is minimised by the median, and most of these targets are right
    # skewed with a pile of zeros, so multiplying any prediction by 0.24 moves
    # it toward the median and "improves" MAE on rows the correction has no
    # business touching. Run without this control the test passed 9 markets of
    # 10, including rush_td at a factor of 0.242, which is not a depth chart
    # finding: it is the observation that most players do not score.
    #
    # A real bucket effect improves the bucket by more than it improves
    # everything else. If the control gains as much, the factor is reading the
    # shape of the distribution rather than anything about demoted players.
    rest = test[~bucket(test)]
    ctrl_gain = 0.0
    if len(rest) >= 50:
        rest_pred = predict(rest)
        rest_actual = rest[ev.LABEL_COL].astype(float).to_numpy()
        rest_before = float(np.mean(np.abs(rest_pred - rest_actual)))
        rest_after = float(np.mean(np.abs(rest_pred * factor - rest_actual)))
        ctrl_gain = (rest_before - rest_after) / rest_before if rest_before else 0.0
        print(f"\ncontrol rows outside the bucket: {len(rest)}")
        print(f"{'unadjusted':<12}{rest_before:>9.2f}")
        print(f"{'adjusted':<12}{rest_after:>9.2f}   ({ctrl_gain:+.1%})")

    gain = (mae(te_pred) - mae(adj)) / mae(te_pred) if mae(te_pred) else 0.0
    # The bucket has to gain at least half again what the control gains, and
    # the control must not be gaining much on its own.
    better = gain > 0 and gain > 1.5 * ctrl_gain and ctrl_gain < 0.05
    print(
        f"\nVERDICT: adjustment {'HELPS' if better else 'DOES NOT HELP'} out of sample "
        f"({mae(te_pred):.2f} -> {mae(adj):.2f} MAE, bucket {gain:+.1%} vs "
        f"control {ctrl_gain:+.1%})"
    )

    # Persist the factor only when it earns its place on held-out data, so a
    # market where the correction does not help simply has no entry and the
    # edge builder leaves its projections alone.
    if os.getenv("WRITE_FACTORS", "0") == "1":
        import json

        path = os.path.join(ev.ARTIFACT_DIR, "stale_role_factors.json")
        store = {}
        if os.path.exists(path):
            with open(path) as f:
                store = json.load(f)
        if better:
            ok, why = confirm_on_record(ev.MARKET_CODE, factor)
            print(f"  {why}")
            if not ok:
                better = False
        if better and len(te_b) < MIN_HOLDOUT:
            # Remove as well as refuse to write. Printing "no factor stored"
            # while leaving the previous run's entry in place meant a market
            # that stopped qualifying kept correcting anyway, which is how
            # rush_yds held a 0.505 fitted on a population this no longer
            # measures on.
            store.pop(ev.MARKET_CODE, None)
            print(f"no factor stored for {ev.MARKET_CODE} "
                  f"({len(te_b)} held-out rows, need {MIN_HOLDOUT})")
        elif better:
            store[ev.MARKET_CODE] = {
                "factor": round(factor, 4),
                "stale_days": STALE_DAYS,
                "train_rows": int(n),
                "holdout_rows": int(len(te_b)),
                "holdout_mae_before": round(mae(te_pred), 3),
                "holdout_mae_after": round(mae(adj), 3),
            }
            print(f"wrote factor for {ev.MARKET_CODE} -> {path}")
        else:
            store.pop(ev.MARKET_CODE, None)
            print(f"no factor stored for {ev.MARKET_CODE} (did not help)")
        with open(path, "w") as f:
            json.dump(store, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
