"""Does training on structural zeros flatten the players the board is about?

The rushing-yards model trains on 9,491 wide-receiver rows averaging 1.0 rushing
yards and 5,919 running-back rows averaging 34.1. Half the fit is rows whose
answer is zero, and a squared or absolute loss spends its effort where the rows
are. Run the live artifact over every labelled row and the damage is not in the
average, which is unbiased to a tenth of a yard, but in the shape:

    quintile by what the player actually did     actual   predicted     bias
    1                                             -0.43        4.08    +4.51
    2                                              0.00        1.65    +1.65
    3                                              0.20        2.60    +2.40
    4                                             10.76       16.78    +6.02
    5                                             58.55       44.32   -14.23

The distribution is compressed from both ends: the bottom is lifted and the top
is cut by 24%. That matters more than it looks, because the players a sportsbook
prices are the top end by definition. A model that reads the top quintile 24% low
will disagree with the line downward on nearly every pick it makes, which is
exactly what the Week 3 2026 board did: 87 of 92 published picks were unders.

Position is not the explanation. Bias by position is -0.08 for quarterbacks and
-0.06 for running backs, and adding the player's position as a feature moved
Lamar Jackson's projection by 0.2 of a yard. The explanation is the population.

So: train on the population the board serves, and keep the structural zeros out
of the fit. A wide receiver's rushing yards are not a rushing prop, they are a
jet sweep that mostly does not happen.

    all         every eligible position, which is what ships
    priced      only positions that carry the ball or catch it enough to be
                priced in this market

Both scored on the same rows, the priced ones, because those are the rows the
board is built from and a comparison on the zeros would be answering a question
nobody asked. Reported overall and on the top quintile, which is where the defect
lives and where an overall average will hide it.
"""

import os

import numpy as np
import pandas as pd

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")

import train as tr  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

# The positions a book actually prices in each market. Rushing props are for
# backs and running quarterbacks; receiving props are for receivers, tight ends
# and the backs who catch. Fullbacks stay wherever they carry.
PRICED = {
    "rush_yds": ("RB", "FB", "QB"),
    "rush_att": ("RB", "FB", "QB"),
    "recs": ("WR", "TE", "RB"),
    "rec_yds": ("WR", "TE", "RB"),
}
FAMILIES = {"rush_yds": "vote_v1", "rush_att": "vote_v1",
            "recs": "enet_v2", "rec_yds": "vote_v1"}


def load(code):
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code=%s",
                    (code,))
        m = cur.fetchone()
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
    df = pd.DataFrame(rows)
    elig = m.get("eligible_positions")
    if elig:
        df = df[df["position"].isin(list(elig))]
    df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"], errors="coerce")
    return df[df["as_of_game_date"].notna()].copy()


def fit_predict(family, train_raw, test_raw):
    tr_df, cols = tr._build_feature_dataframe(train_raw)
    te_df, _ = tr._build_feature_dataframe(test_raw)
    for c in cols:
        if c not in te_df.columns:
            te_df[c] = 0.0
    mdl = tr.build_model(family)
    mdl.fit(tr_df[cols], train_raw["label_actual"].astype(float))
    return np.maximum(0.0, mdl.predict(te_df[cols]))


def main():
    for code, family in FAMILIES.items():
        df = load(code)
        priced_pos = PRICED[code]
        cut = int(len(df) * 0.75)
        train_all, test = df.iloc[:cut], df.iloc[cut:]
        # Scored on the priced rows only, both times.
        test = test[test["position"].isin(priced_pos)].copy()
        train_priced = train_all[train_all["position"].isin(priced_pos)]
        if len(test) < 200 or len(train_priced) < 500:
            print(f"{code}: too few rows ({len(train_priced)}/{len(test)})\n")
            continue

        y = test["label_actual"].astype(float).to_numpy()
        p_all = fit_predict(family, train_all, test)
        p_priced = fit_predict(family, train_priced, test)

        zeros = len(train_all) - len(train_priced)
        print(f"{code}  ({family})  {len(train_all)} training rows, "
              f"{zeros} of them structural zeros ({zeros / len(train_all):.0%}); "
              f"scored on {len(test)} priced rows")
        print(f"{'':16}{'MAE':>9}{'bias':>9}{'top-Q MAE':>11}{'top-Q bias':>12}")
        q = pd.qcut(pd.Series(y).rank(method="first"), 5, labels=False).to_numpy()
        top = q == 4
        for name, p in (("all positions", p_all), ("priced only", p_priced)):
            print(f"{name:<16}{np.abs(p - y).mean():>9.3f}{(p - y).mean():>+9.3f}"
                  f"{np.abs(p - y)[top].mean():>11.3f}{(p - y)[top].mean():>+12.3f}")
        g = (np.abs(p_all - y).mean() - np.abs(p_priced - y).mean())
        gt = (np.abs(p_all - y)[top].mean() - np.abs(p_priced - y)[top].mean())
        print(f"{'gain':<16}{g / np.abs(p_all - y).mean() * 100:>+8.1f}%"
              f"{'':>9}{gt / np.abs(p_all - y)[top].mean() * 100:>+10.1f}%\n")


if __name__ == "__main__":
    main()
