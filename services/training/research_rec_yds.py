"""Why is receiving yards the one market that does not pay?

Every other volume market clears its break-even by two to four points. Receiving
yards hits 52.1% against 52.7%, which is a loss, and it is the biggest market on
the board by pick count. Three candidate explanations, tested in order of how
cheap they are to check.

**The wrong family is live.** Production serves rec_yds from rf_default. The
bakeoff and MODEL.md both say vote_v1 won this market, by +1.0% on rolling
origins. One of those is wrong and it is worth knowing which, because a market
served by a family that lost its own bakeoff is a bug rather than a mystery.

**The z scale is wrong.** Tiers divide the gap by (q75 - q25) / 1.349, so a
market whose predicted quartiles are too narrow gets z inflated and promotes
picks into elite that have not earned it. The interval calibrator already
corrects p25, p75 and p90 for rec_yds and only two levels for any other market,
which is a hint that its raw ladder is the worst behaved of the seven. If z does
not sort outcomes here the way it does elsewhere, the tier ladder is built on
sand for this market specifically.

**It is the market itself.** Receiving yards is volume times yards per catch,
and the second term has a tail no other market has: one broken tackle is forty
yards, and no projection built on a five-game window sees it coming. The loss
autopsy already found this shape here more sharply than anywhere else, with
"more work and bigger plays" at 34.0% of losses and 1.0% of wins.

The first two are fixable. The third is not, and if it is the answer then the
honest response is a higher bar for this market rather than a better model for it.
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from psycopg2.extras import RealDictCursor

os.environ.setdefault("MARKET_CODE", "rec_yds")
os.environ.setdefault("LOOKBACK", "5")
import train as tr  # noqa: E402

import backtest_gap_system as bt  # noqa: E402

FAMILIES = ["rf_default", "vote_v1", "enet_v2", "ridge_v2", "hgb_v1"]
FOLDS = 3
N_BOOT = int(os.getenv("N_BOOT", "1500"))


def rows():
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code='rec_yds'")
        m = cur.fetchone()
        cur.execute("""
            SELECT pmf.player_id, p.name, p.position, pmf.as_of_game_date,
                   pmf.opponent, pmf.team, pmf.mean, pmf.stddev,
                   pmf.weighted_mean, pmf.trend, pmf.aux_mean, pmf.aux_trend,
                   pmf.recs_mean, pmf.recs_trend, pmf.extra_features,
                   pmf.label_actual
            FROM player_market_features pmf
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pmf.market_id=%s AND pmf.lookback=5
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date, pmf.player_id
        """, (m["id"],))
        got = cur.fetchall()
    d = pd.DataFrame(got)
    elig = m.get("eligible_positions")
    if elig:
        d = d[d["position"].isin(list(elig))]
    d["as_of_game_date"] = pd.to_datetime(d["as_of_game_date"], errors="coerce")
    return d[d["as_of_game_date"].notna()].reset_index(drop=True)


def lines():
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT player_name, line,
                   (commence_time AT TIME ZONE 'America/New_York')::date AS d
            FROM odds_snapshots
            WHERE market_key='player_reception_yds' AND line IS NOT NULL
        """)
        o = pd.DataFrame(cur.fetchall())
    if o.empty:
        return o
    o = o.groupby(["player_name", "d"], as_index=False)["line"].median()
    o["d"] = pd.to_datetime(o["d"])
    return o


def part_one(d, ln):
    print("=" * 78)
    print("1. Is the live family the right one?\n")
    d = d.merge(ln, left_on=["name", "as_of_game_date"],
                right_on=["player_name", "d"], how="left")
    n = len(d)
    edges = [int(n * (0.4 + 0.2 * i)) for i in range(FOLDS + 1)]
    print(f"  {'family':<14}{'MAE':>10}{'vs live':>10}{'side accuracy':>24}")
    base = None
    for fam in FAMILIES:
        maes, sides, dates = [], [], []
        for k in range(FOLDS):
            trn, tst = d.iloc[:edges[k]], d.iloc[edges[k]:edges[k + 1]]
            if len(trn) < 2000 or len(tst) < 300:
                continue
            X_tr, cols = tr._build_feature_dataframe(trn)
            X_te, _ = tr._build_feature_dataframe(tst)
            for c in cols:
                if c not in X_te.columns:
                    X_te[c] = 0.0
            try:
                mdl = tr.build_model(fam)
                mdl.fit(X_tr[cols], trn["label_actual"].astype(float))
                pred = np.maximum(0.0, mdl.predict(X_te[cols]))
            except Exception as e:
                print(f"  {fam:<14}would not fit ({type(e).__name__})")
                maes = []
                break
            y = tst["label_actual"].astype(float).to_numpy()
            maes.append(np.abs(pred - y))
            l = pd.to_numeric(tst["line"], errors="coerce").to_numpy()
            ok = np.isfinite(l)
            call, truth = np.sign(pred[ok] - l[ok]), np.sign(y[ok] - l[ok])
            live = (call != 0) & (truth != 0)
            sides.append((call[live] == truth[live]).astype(float))
            dates.append(tst["as_of_game_date"].dt.strftime("%Y%m%d")
                         .to_numpy()[ok][live])
        if not maes:
            continue
        mae = float(np.concatenate(maes).mean())
        if base is None:
            base = mae
        sd = np.concatenate(sides) if sides else np.array([])
        dt = np.concatenate(dates) if dates else np.array([])
        if len(sd) > 50:
            r, lo, hi = S.clustered_bootstrap(
                lambda i, s=sd: float(np.mean(s[i])), dt, n_boot=N_BOOT)
            txt = f"{r:>7.1%} [{lo:>5.1%}, {hi:>5.1%}]"
        else:
            txt = "n/a"
        print(f"  {fam:<14}{mae:>10.3f}{(base - mae) / base * 100:>+9.2f}%{txt:>24}")


def part_two(d):
    print("\n" + "=" * 78)
    print("2. Does z sort outcomes here the way it does elsewhere?\n")
    b = bt.build()
    if b.empty:
        print("  no walk-forward picks")
        return
    for code in ("rec_yds", "recs", "rush_yds"):
        g = b[(b["market_code"] == code) & np.isfinite(b["z"])].copy()
        if len(g) < 300:
            continue
        g["az"] = g["z"].abs()
        print(f"  {code}")
        for lo_t, hi_t in ((0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 99)):
            s = g[(g["az"] >= lo_t) & (g["az"] < hi_t)]
            if len(s) < 40:
                continue
            print(f"      |z| {lo_t:g}-{hi_t if hi_t < 99 else '+':<4}"
                  f"{len(s):>6} picks   hit {s['won'].mean():>6.1%}"
                  f"   break {s['breakeven'].mean():>6.1%}"
                  f"   edge {s['won'].mean() - s['breakeven'].mean():>+6.1%}")
        print()

    print("  How wide the predicted middle actually is, against how wide it "
          "should be:")
    for code, g in b.groupby("market_code"):
        g = g[np.isfinite(g["sd"])]
        if len(g) < 200:
            continue
        # A correctly scaled sd puts about half of outcomes inside +/- 0.6745 sd
        # of the projection. Far from half means z is measured in the wrong unit.
        inside = (np.abs(g["label_actual"] - g["pred"]) <= 0.6745 * g["sd"]).mean()
        print(f"      {code:<12}{len(g):>6} rows   {inside:>6.1%} of outcomes "
              f"inside the middle 50% band   (want 50%)")


def part_three(d):
    print("\n" + "=" * 78)
    print("3. Where inside receiving yards does it go wrong?\n")
    b = bt.build()
    g = b[(b["market_code"] == "rec_yds")
          & b["tier"].isin(["elite", "strong", "medium"])].copy()
    if len(g) < 100:
        print("  too few published picks")
        return
    g["band"] = pd.cut(g["line"], [-1, 25, 45, 70, 1000],
                       labels=["<=25 WR3/TE2", "26-45 WR2/TE1", "46-70 WR1",
                               "70+ alpha"])
    for by, label in (("band", "by where the line sits"), ("side", "by side")):
        print(f"  {label}")
        for k, s in g.groupby(by, observed=True):
            if len(s) < 30:
                continue
            w = s["won"].to_numpy()
            r, lo, hi = S.clustered_bootstrap(
                lambda i, w=w: float(np.mean(w[i])), s["cluster"].to_numpy(),
                n_boot=N_BOOT)
            print(f"      {str(k):<16}{len(s):>6}   {r:>6.1%} "
                  f"[{lo:>5.1%}, {hi:>5.1%}]   break "
                  f"{s['breakeven'].mean():>6.1%}   edge "
                  f"{r - s['breakeven'].mean():>+6.1%}")
        print()


def main():
    d = rows()
    ln = lines()
    print(f"rec_yds: {len(d)} labelled rows, "
          f"{d['as_of_game_date'].min().date()} to "
          f"{d['as_of_game_date'].max().date()}\n")
    part_one(d, ln)
    part_two(d)
    part_three(d)


if __name__ == "__main__":
    main()
