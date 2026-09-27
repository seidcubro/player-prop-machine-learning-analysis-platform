"""The gap system, run over 2024, 2025 and the two graded weeks of 2026.

The earlier gap measurement had a hole in it. It ran on 7,153 published picks,
and those had already been chosen by the expected-value filter, so the finding
that big gaps win was conditional on a population the old rule had selected. It
could not say what the new rule does on its own.

This does. Every priced prop in the window, not just the ones we published, with
projections a model could actually have made at the time.

    for each market, for each target season
        train the live family on every labelled row before that season
        predict that season's rows
        join the line the books actually offered
        z = (projection - line) / the spread of the player's recent games
        tier on |z| exactly as gap_tier does
        take the gap's side and see whether it hit

The model never sees its own season, which is stricter than production, where the
weekly job retrains as the season goes. So these numbers understate what the live
board would do, and the direction of that bias is worth remembering when reading
them.

The scale is the window standard deviation rather than the fitted quantile
ladder. Production divides by (q75 - q25) / 1.349 from the quantile models, and
refitting those walk-forward for every market and season is a different and much
heavier job. The window spread is the same quantity measured more crudely, so the
thresholds here are in slightly different units from the live ones and the shape
of the result is what transfers, not the exact cut points.

Then the part Seid actually asked for: where it loses. Broken out by season, by
market, by week of season, by side, by how much volume the player has, and by
whether the line sits inside or outside what he has recently done. A rule that
wins overall and loses somewhere specific is a rule with a filter waiting to be
found.
"""

import os

import numpy as np
import pandas as pd
import gap_tier
import stats_ci as S
from psycopg2.extras import RealDictCursor

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")
import train as tr  # noqa: E402

FAMILY = {
    "recs": "enet_v2", "rec_yds": "vote_v1", "rush_yds": "vote_v1",
    "rush_att": "vote_v1", "pass_yds": "hgb_v1", "pass_att": "hgb_v1",
    "pass_completions": "hgb_v1",
}
ODDS_KEY = {
    "recs": "player_receptions", "rec_yds": "player_reception_yds",
    "rush_yds": "player_rush_yds", "rush_att": "player_rush_attempts",
    "pass_yds": "player_pass_yds", "pass_att": "player_pass_attempts",
    "pass_completions": "player_pass_completions",
}
SEASONS = [2024, 2025, 2026]
N_BOOT = int(os.getenv("N_BOOT", "1500"))
# Matches gap_tier's refusal to divide by a collapsed spread.
MIN_SD = gap_tier.MIN_SD


def features(code):
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code=%s",
                    (code,))
        m = cur.fetchone()
        if not m:
            return None
        cur.execute("""
            SELECT pmf.player_id, p.name, p.position, pmf.as_of_game_date,
                   pmf.opponent, pmf.team, pmf.mean, pmf.stddev,
                   pmf.weighted_mean, pmf.trend, pmf.aux_mean, pmf.aux_trend,
                   pmf.extra_features, pmf.label_actual
            FROM player_market_features pmf
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pmf.market_id=%s AND pmf.lookback=5
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date, pmf.player_id
        """, (m["id"],))
        rows = cur.fetchall()
    if not rows:
        return None
    d = pd.DataFrame(rows)
    elig = m.get("eligible_positions")
    if elig:
        d = d[d["position"].isin(list(elig))]
    d["as_of_game_date"] = pd.to_datetime(d["as_of_game_date"], errors="coerce")
    d = d[d["as_of_game_date"].notna()].reset_index(drop=True)
    # NFL seasons run into January, so anything before March belongs to the year
    # before it.
    yr = d["as_of_game_date"].dt.year
    d["season"] = np.where(d["as_of_game_date"].dt.month <= 2, yr - 1, yr)
    return d


def lines(code):
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT player_name, line, price_american, outcome_name,
                   (commence_time AT TIME ZONE 'America/New_York')::date AS d
            FROM odds_snapshots
            WHERE market_key = %s AND line IS NOT NULL
              AND price_american IS NOT NULL
        """, (ODDS_KEY[code],))
        o = pd.DataFrame(cur.fetchall())
    if o.empty:
        return o
    o["side"] = o["outcome_name"].str.strip().str.lower()
    o = o[o["side"].isin(["over", "under"])]
    # The line most books agreed on, and the best price available on each side.
    agg = o.groupby(["player_name", "d"]).agg(
        line=("line", "median")).reset_index()
    best = (o.groupby(["player_name", "d", "side"], as_index=False)
             ["price_american"].max()
             .pivot_table(index=["player_name", "d"], columns="side",
                          values="price_american").reset_index())
    out = agg.merge(best, on=["player_name", "d"], how="inner")
    out["d"] = pd.to_datetime(out["d"])
    return out


def walk_forward(d, family):
    """Predict each target season from a model trained only on earlier rows."""
    d = d.copy()
    d["pred"] = np.nan
    for s in SEASONS:
        past = d[d["season"] < s]
        now = d.index[d["season"] == s]
        if len(past) < 2000 or not len(now):
            continue
        X_tr, cols = tr._build_feature_dataframe(past)
        X_te, _ = tr._build_feature_dataframe(d.loc[now])
        for c in cols:
            if c not in X_te.columns:
                X_te[c] = 0.0
        try:
            model = tr.build_model(family)
            model.fit(X_tr[cols], past["label_actual"].astype(float))
            d.loc[now, "pred"] = np.maximum(0.0, model.predict(X_te[cols]))
        except Exception as e:
            print(f"    fold {s} failed: {type(e).__name__}: {e}")
    return d


def build():
    frames = []
    for code, family in FAMILY.items():
        d = features(code)
        if d is None or len(d) < 3000:
            print(f"  {code}: too few labelled rows")
            continue
        o = lines(code)
        if o.empty:
            print(f"  {code}: no priced props")
            continue
        d = walk_forward(d, family)
        d = d[d["pred"].notna()]
        j = d.merge(o, left_on=["name", "as_of_game_date"],
                    right_on=["player_name", "d"], how="inner")
        if j.empty:
            print(f"  {code}: nothing matched a line")
            continue
        j["market_code"] = code
        floor = MIN_SD.get(code, gap_tier.MIN_SD_DEFAULT)
        sd = pd.to_numeric(j["stddev"], errors="coerce")
        j["sd"] = sd.where(sd >= floor)
        j["z"] = ((j["pred"] - j["line"]) / j["sd"]).clip(-gap_tier.Z_CEILING,
                                                         gap_tier.Z_CEILING)
        j["tier"] = [gap_tier.tier_for(z if np.isfinite(z) else None)
                     for z in j["z"]]
        j["side"] = np.where(j["z"] > 0, "over", "under")
        j["won"] = np.where(j["z"] > 0,
                            j["label_actual"] > j["line"],
                            j["label_actual"] < j["line"]).astype(float)
        price = np.where(j["side"] == "over", j["over"], j["under"])
        j["price"] = price
        j["breakeven"] = np.where(price > 0, 100.0 / (price + 100.0),
                                  -price / (-price + 100.0))
        pay = np.where(price > 0, price / 100.0, 100.0 / -price)
        j["profit"] = np.where(j["won"] > 0, pay, -1.0)
        j["cluster"] = j["as_of_game_date"].dt.strftime("%Y%m%d")
        j["week_bucket"] = np.where(
            j["as_of_game_date"].dt.month <= 2, "Jan (playoffs)",
            np.where(j["as_of_game_date"].dt.month == 9, "Sep (weeks 1-4)",
                     np.where(j["as_of_game_date"].dt.month == 10, "Oct",
                              np.where(j["as_of_game_date"].dt.month == 11,
                                       "Nov", "Dec"))))
        frames.append(j)
        print(f"  {code}: {len(j)} priced props with a walk-forward projection")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def table(d, by, title, min_n=60):
    print(f"\n{title}")
    print(f"  {'':<26}{'picks':>7}{'hit':>25}{'break':>8}{'edge':>8}{'roi':>8}")
    for key, g in d.groupby(by, dropna=False):
        if len(g) < min_n:
            continue
        w = g["won"].to_numpy()
        rate, lo, hi = S.clustered_bootstrap(
            lambda idx, w=w: float(np.mean(w[idx])), g["cluster"].to_numpy(),
            n_boot=N_BOOT)
        be = g["breakeven"].mean()
        label = key if isinstance(key, str) else str(key)
        print(f"  {label:<26}{len(g):>7}   {rate:>6.1%} [{lo:>5.1%}, {hi:>5.1%}]"
              f"{be:>8.1%}{rate - be:>+8.1%}{g['profit'].mean():>+8.1%}")


def main():
    print("Walk-forward: each season predicted by a model trained only on what\n"
          "came before it, then every priced prop tiered by the gap rule.\n")
    d = build()
    if d.empty:
        raise SystemExit("nothing to backtest")
    bet = d[d["tier"].isin(["elite", "strong", "medium"])]
    print(f"\n{len(d)} priced props, {len(bet)} of them reach a published tier "
          f"({len(bet) / len(d):.0%}).")

    print("\n" + "=" * 82)
    print("1. Does the tier ladder rank?")
    order = ["none", "small", "medium", "strong", "elite"]
    d["tier"] = pd.Categorical(d["tier"], order, ordered=True)
    table(d, "tier", "by published tier, worst to best", min_n=40)

    print("\n" + "=" * 82)
    print("2. Where it wins and loses")
    table(bet, "season", "by season")
    table(bet, "market_code", "by market")
    table(bet, "week_bucket", "by month")
    table(bet, "side", "by side")
    bet = bet.copy()
    bet["line_band"] = pd.cut(bet["line"], [-1, 2.5, 5.5, 20, 60, 1000],
                              labels=["tiny (<=2.5)", "small (3-5.5)",
                                      "mid (6-20)", "large (21-60)", "huge"])
    table(bet, "line_band", "by how big the line is")

    print("\n" + "=" * 82)
    print("3. The elite tier alone, which is what gets hammered")
    elite = d[d["tier"] == "elite"]
    if len(elite) >= 60:
        table(elite, "season", "elite by season", min_n=30)
        table(elite, "market_code", "elite by market", min_n=30)
        table(elite, "side", "elite by side", min_n=30)

    print("\n" + "=" * 82)
    print("4. Is the line inside or outside what the player has been doing?\n"
          "   Seid's case is a line the player has never reached. If the rule "
          "works there\n   and not elsewhere, that is the filter.")
    b = d[d["tier"].isin(["elite", "strong"])].copy()
    wm = pd.to_numeric(b["weighted_mean"], errors="coerce")
    b["line_vs_form"] = np.where(
        b["line"] > wm + b["sd"], "line above his form",
        np.where(b["line"] < wm - b["sd"], "line below his form",
                 "line inside his form"))
    table(b, "line_vs_form", "elite and strong, by where the line sits", min_n=40)

    print("\n" + "=" * 82)
    print("5. How big does the gap have to be, per side?\n"
          "   The old expected-value rule held overs to double the under "
          "threshold and the\n   gap rule dropped that. Overs are now 70% of "
          "the published board and return\n   nothing, so the question is "
          "whether they are hopeless or just cheap at this bar.")
    a = d[np.isfinite(d["z"])].copy()
    a["az"] = a["z"].abs()
    for side in ("under", "over"):
        s = a[a["side"] == side]
        print(f"\n  {side}s, by how far the projection sits from the line")
        print(f"  {'|z| at least':<26}{'picks':>7}{'hit':>25}{'break':>8}"
              f"{'edge':>8}{'roi':>8}")
        for thr in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
            g = s[s["az"] >= thr]
            if len(g) < 40:
                continue
            w = g["won"].to_numpy()
            rate, lo, hi = S.clustered_bootstrap(
                lambda idx, w=w: float(np.mean(w[idx])),
                g["cluster"].to_numpy(), n_boot=N_BOOT)
            be = g["breakeven"].mean()
            print(f"  {thr:<26g}{len(g):>7}   {rate:>6.1%} "
                  f"[{lo:>5.1%}, {hi:>5.1%}]{be:>8.1%}{rate - be:>+8.1%}"
                  f"{g['profit'].mean():>+8.1%}")


if __name__ == "__main__":
    main()
