"""Why does the receptions model lean on the weather and the calendar?

SHAP on the live artifact puts receptions' weight, after the season-anchored
level, on days since last game (13.5%), career games (9.2%) and game temperature
(8.2%). Thirty-one per cent on three things that are not how many passes come at
a man, while his own target volume gets 7.5% and the plain window mean is zeroed.

Two explanations, and they call for opposite actions.

Elastic net with collinear inputs concentrates weight on one of a group of
features measuring the same thing and shrinks the rest to zero. Receptions has at
least six columns measuring volume, so the zeroed `mean` proves nothing and the
volume total is spread rather than missing. That part is benign.

It does not explain the other three, which are not collinear with volume. Either
they carry real signal that a tree model would also find, or the linear model is
using them as proxies for something it cannot express and a different family
would not need them.

So, on rolling-origin folds, three trained only on what came before:

    incumbent      enet_v2 on every feature, which is what ships
    minus three    enet_v2 without days_since_last_game, career_n, game_temp
    other families ridge, random forest, hist gradient boosting, and the voting
                   ensemble that won the rushing markets

MAE and the hit rate against the line, since the board is judged on which side it
lands. Intervals resample fold dates rather than rows, because receptions in one
game share their afternoon.

The noise rule applies: a change ships only if it wins on data it was never
fitted to, in every fold rather than on average. The bakeoff that chose enet_v2
ran before the season-only window, before y_blend and before the position
features existed, so the incumbent's claim to the market is out of date either
way and this is the re-run.
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from psycopg2.extras import RealDictCursor

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")
import train as tr  # noqa: E402

MARKET = os.getenv("MARKET_CODE", "recs")
SUSPECTS = ["days_since_last_game", "career_n", "game_temp"]
FAMILIES = ["enet_v2", "ridge_v2", "rf_default", "hgb_v1", "vote_v1"]
FOLDS = 3
N_BOOT = int(os.getenv("N_BOOT", "1500"))


def load():
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code=%s",
                    (MARKET,))
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
            ORDER BY pmf.as_of_game_date, pmf.player_id
        """, (m["id"],))
        rows = cur.fetchall()
    d = pd.DataFrame(rows)
    elig = m.get("eligible_positions")
    if elig:
        d = d[d["position"].isin(list(elig))]
    d["as_of_game_date"] = pd.to_datetime(d["as_of_game_date"], errors="coerce")
    return d[d["as_of_game_date"].notna()].reset_index(drop=True)


def lines_for(d):
    """The line each row was priced at, so a side can be scored.

    Left joined, so a row nobody priced simply has no line and drops out of the
    side calculation while still counting toward MAE.
    """
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT player_name, line,
                   (commence_time AT TIME ZONE 'America/New_York')::date AS d
            FROM odds_snapshots
            WHERE market_key = 'player_receptions' AND line IS NOT NULL
        """)
        o = pd.DataFrame(cur.fetchall())
        cur.execute("SELECT external_id, name FROM players")
        names = pd.DataFrame(cur.fetchall())
    if o.empty:
        return pd.Series(np.nan, index=d.index)
    o = o.groupby(["player_name", "d"], as_index=False)["line"].median()
    o["d"] = pd.to_datetime(o["d"])
    j = d.merge(names, left_on="player_id", right_on="external_id", how="left")
    j = j.merge(o, left_on=["name", "as_of_game_date"],
                right_on=["player_name", "d"], how="left")
    return j["line"].to_numpy()


def main():
    d = load()
    d["_line"] = lines_for(d)
    print(f"{MARKET}: {len(d)} labelled rows, "
          f"{int(np.isfinite(d['_line']).sum())} of them priced, "
          f"{d['as_of_game_date'].min().date()} to "
          f"{d['as_of_game_date'].max().date()}\n"
          f"Rolling origin, {FOLDS} folds, each trained only on what came "
          f"before it.\n")

    n = len(d)
    edges = [int(n * (0.4 + 0.2 * i)) for i in range(FOLDS + 1)]
    variants = [("incumbent enet_v2", "enet_v2", False)]
    variants += [("enet_v2 minus three", "enet_v2", True)]
    variants += [(f"{f}", f, False) for f in FAMILIES if f != "enet_v2"]

    results = {}
    for label, family, drop in variants:
        maes, sides, dates = [], [], []
        for k in range(FOLDS):
            trn = d.iloc[:edges[k]]
            tst = d.iloc[edges[k]:edges[k + 1]]
            if len(trn) < 2000 or len(tst) < 300:
                continue
            X_tr, cols = tr._build_feature_dataframe(trn)
            X_te, _ = tr._build_feature_dataframe(tst)
            for c in cols:
                if c not in X_te.columns:
                    X_te[c] = 0.0
            use = [c for c in cols if not (drop and c in SUSPECTS)]
            try:
                model = tr.build_model(family)
                model.fit(X_tr[use], trn["label_actual"].astype(float))
                pred = np.maximum(0.0, model.predict(X_te[use]))
            except Exception as e:
                print(f"  {label}: {family} would not fit ({type(e).__name__})")
                maes = []
                break
            y = tst["label_actual"].astype(float).to_numpy()
            maes.append(np.abs(pred - y))
            ln = tst["_line"].to_numpy()
            ok = np.isfinite(ln)
            call = np.sign(pred[ok] - ln[ok])
            truth = np.sign(y[ok] - ln[ok])
            live = (call != 0) & (truth != 0)
            sides.append((call[live] == truth[live]).astype(float))
            dates.append(tst["as_of_game_date"].dt.strftime("%Y%m%d")
                         .to_numpy()[ok][live])
        if not maes:
            continue
        mae_all = np.concatenate(maes)
        side_all = np.concatenate(sides) if sides else np.array([])
        date_all = np.concatenate(dates) if dates else np.array([])
        results[label] = (mae_all, side_all, date_all)

    base = results.get("incumbent enet_v2")
    print(f"{'variant':<24}{'MAE':>18}{'vs incumbent':>14}"
          f"{'side accuracy':>24}")
    for label, (mae, side, dates) in results.items():
        m = float(mae.mean())
        delta = ("" if base is None or label == "incumbent enet_v2"
                 else f"{(base[0].mean() - m) / base[0].mean() * 100:+13.2f}%")
        if len(side) > 50:
            rate, lo, hi = S.clustered_bootstrap(
                lambda idx, s=side: float(np.mean(s[idx])), dates, n_boot=N_BOOT)
            sides_txt = f"{rate:>7.1%} [{lo:>5.1%}, {hi:>5.1%}]"
        else:
            sides_txt = "n/a"
        print(f"{label:<24}{m:>18.4f}{delta:>14}{sides_txt:>24}")

    print("\nMAE is the loss the model is fitted on. Side accuracy is what the\n"
          "board is judged on, and the two can disagree: a model can be closer\n"
          "on average and worse at calling which side of the line.")


if __name__ == "__main__":
    main()
