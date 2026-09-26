"""Season-only window or cross-season window: which one does the model prefer?

The only comparison that settles it, and the one that was missing every previous
time this question was asked.

research_season_boundary.py compares the two windows as estimators on their own
and says the cross-season one is closer on five of seven markets. That is a fact
about averages, not about the model, and the model has sixty other columns
including the depth chart, the Vegas total and the opponent. research_blend_feature.py
shows the blended anchor adds almost nothing once those columns are present,
which is the same result that produced the wrong verdict in September: a signal
that is real on its own and redundant inside the model.

So neither of those can answer whether the window change helps. This does, by
building the features both ways and comparing them on the same rows with the same
family and the same split. Two passes, because both generations use the same
primary key and cannot be in the table at once:

    WINDOW_SEASON_ONLY=0  build features  dump    -> crossing.parquet
    WINDOW_SEASON_ONLY=1  build features  dump    -> season.parquet
    compare

`dump` writes the labelled rows for one market. `compare` intersects them on
(player_id, as_of_game_date, opponent), so every row scored exists in both
generations and no part of the difference is a population change. That
intersection is the whole point: the retrain that prompted this looked like a
1-2% gain on the volume markets and a 1% loss on passing, and none of those
numbers mean anything while one generation is training on 2,400 rows the other
does not have.

Reported overall and on rows with fewer than five games of the current season,
which is the population the change is for.

Usage:
    python research_window_generations.py dump    <dir>
    python research_window_generations.py compare <crossing_dir> <season_dir>
"""

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")

import train as tr  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

FAMILIES = {
    "recs": "enet_v2", "rec_yds": "vote_v1", "rush_yds": "vote_v1",
    "rush_att": "vote_v1", "pass_yds": "hgb_v1", "pass_att": "hgb_v1",
    "pass_completions": "hgb_v1",
}
KEY = ["player_id", "as_of_game_date", "opponent"]
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
    df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"], errors="coerce")
    return df[df["as_of_game_date"].notna()].copy()


def dump(out_dir):
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    for code in FAMILIES:
        df = load(code)
        if df is None:
            print(f"{code}: nothing to dump")
            continue
        df["extra_features"] = df["extra_features"].apply(
            lambda e: e if isinstance(e, dict) else {})
        df.to_pickle(d / f"{code}.pkl")
        print(f"{code:<18}{len(df):>7} labelled rows -> {d / (code + '.pkl')}")


def score(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    return float(np.mean(np.abs(y - p)))


def compare(dir_a, dir_b):
    print("Same rows in both generations, same family, same split.\n"
          "'crossing' is the old window, 'season' is this season only.\n")
    print(f"{'market':<18}{'rows':>7}{'crossing':>10}{'season':>9}{'gain':>8}"
          f"{'thin gain':>11}{'thin n':>8}")
    for code, family in FAMILIES.items():
        pa, pb = Path(dir_a) / f"{code}.pkl", Path(dir_b) / f"{code}.pkl"
        if not pa.exists() or not pb.exists():
            print(f"{code:<18}{'missing a dump':>25}")
            continue
        a, b = pd.read_pickle(pa), pd.read_pickle(pb)
        keys = a[KEY].merge(b[KEY], on=KEY, how="inner").drop_duplicates()
        if len(keys) < 600:
            print(f"{code:<18}{len(keys):>7}  too few rows in both")
            continue
        a = a.merge(keys, on=KEY, how="inner").sort_values(KEY).reset_index(drop=True)
        b = b.merge(keys, on=KEY, how="inner").sort_values(KEY).reset_index(drop=True)
        assert (a["label_actual"].to_numpy() == b["label_actual"].to_numpy()).all(), \
            f"{code}: the same row carries different labels in the two dumps"

        # One split, by position in the shared timeline, so both generations are
        # trained and scored on exactly the same games.
        cut = int(len(a) * 0.75)
        res = {}
        for name, gen in (("crossing", a), ("season", b)):
            tr_raw, te_raw = gen.iloc[:cut], gen.iloc[cut:]
            tr_df, cols = tr._build_feature_dataframe(tr_raw)
            te_df, _ = tr._build_feature_dataframe(te_raw)
            for c in cols:
                if c not in te_df.columns:
                    te_df[c] = 0.0
            mdl = tr.build_model(family)
            mdl.fit(tr_df[cols], tr_raw["label_actual"].astype(float))
            res[name] = mdl.predict(te_df[cols])
        yte = a.iloc[cut:]["label_actual"].astype(float).to_numpy()
        m_a, m_b = score(yte, res["crossing"]), score(yte, res["season"])
        gain = (m_a - m_b) / m_a * 100 if m_a else 0.0

        n_cur = pd.to_numeric(
            b.iloc[cut:]["extra_features"].apply(lambda e: e.get("y_season_n")),
            errors="coerce").to_numpy()
        thin = np.isfinite(n_cur) & (n_cur <= THIN)
        if thin.sum() >= 40:
            t = (score(yte[thin], res["crossing"][thin])
                 - score(yte[thin], res["season"][thin]))
            base = score(yte[thin], res["crossing"][thin])
            tgain = f"{t / base * 100:+.1f}%" if base else "-"
        else:
            tgain = "too few"
        print(f"{code:<18}{len(yte):>7}{m_a:>10.4f}{m_b:>9.4f}{gain:>+7.1f}%"
              f"{tgain:>11}{int(thin.sum()):>8}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "dump":
        dump(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "compare":
        compare(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__.strip().splitlines()[-3].strip())
