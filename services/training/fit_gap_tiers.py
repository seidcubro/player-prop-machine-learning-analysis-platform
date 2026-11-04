"""Which markets earn a tiering, and which end of the gap is the confident end.

The board tiers by RANK within each market's own board (gap_tier.tiers_by_rank),
so this no longer decides where to cut. It decides two things: whether a market
is ranked by the gap at all, and if so which way round.

Both matter, and assuming the second cost a season. Measured on 2,495 live
props, cut in half by date and asked twice:

    recs      top of the gap     +17.6%  then  +9.9%   -> ranked, high end
    rush_yds  BOTTOM of the gap  +12.5%  then +23.7%   -> ranked, LOW end
    rush_att  top of the gap     +19.5%  then  -0.5%   -> not ranked
    rec_yds   top of the gap      +4.0%  then  +5.9%   -> too small to call

rush_yds runs backwards. Its narrowest disagreements hit 60.0% and 67.4% in the
two halves against 49.1% for the market as a whole, so the widest ones, which
the board was calling its most confident, are its worst. Pooled across markets
this is invisible: rush_yds pointing one way and recs the other cancel to a rank
correlation of +0.002, which is how the first version of this file came to fit
every market in the same direction.

TWO WAYS IN, and a market needs one of them.

  Split-half replication on the live season, at either end. This is the
  stronger test because it is the population the board actually serves, and
  because a signal that survives being asked twice is not a decile someone
  noticed. The size bar is simulated rather than assumed: at these counts two
  halves of pure noise agree in sign with both sides above 3 points in 28% of
  trials, so the bar is 8 points, where noise passes about one time in
  fourteen.

  The backtest gate, for markets with too few live rows to split. Elite minus
  the rest on the fitting rows, with a bootstrap interval that must clear zero.
  High end only, since there is not enough live evidence to justify inverting a
  market on backtest alone.

Everything else stays flat, and flat means every pick in it publishes as small.

Writes artifacts/gap_tiers.json with WRITE=1.

    docker compose run --rm training python fit_gap_tiers.py
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
OUT = ARTIFACTS / "gap_tiers.json"

SHARE = float(os.getenv("SHARE", "0.20"))
MIN_EFFECT = float(os.getenv("MIN_EFFECT", "0.08"))
MIN_SIDE = int(os.getenv("MIN_SIDE", "20"))
MIN_FIT = int(os.getenv("MIN_FIT", "300"))
SEASON = int(os.getenv("SEASON", "2026"))


def load(eng, source):
    d = pd.read_sql(text("""
        SELECT market_code, game_date, player_name, line, hit,
               projection_median AS p50
        FROM prop_edge_results
        WHERE source = :s AND hit IS NOT NULL AND line IS NOT NULL
          AND projection_median IS NOT NULL
    """), eng, params={"s": source})
    if d.empty:
        return d
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    d["gap"] = (d["p50"] - d["line"]).abs() / d["line"].abs().clip(lower=0.5)
    d["cluster"] = d["game_date"].astype(str)
    return d


def band_vs_rest(g, high):
    """Top (or bottom) SHARE of this slice against the rest of it."""
    v = g["gap"]
    thr = v.quantile(1.0 - SHARE if high else SHARE)
    sel = (v >= thr) if high else (v <= thr)
    a, b = g.loc[sel, "hit"].to_numpy(), g.loc[~sel, "hit"].to_numpy()
    if len(a) < MIN_SIDE or len(b) < MIN_SIDE:
        return None
    vals = np.concatenate([a, b])
    flags = np.concatenate([np.ones(len(a)), np.zeros(len(b))])
    cl = np.concatenate([g.loc[sel, "cluster"].to_numpy(),
                         g.loc[~sel, "cluster"].to_numpy()])
    r, lo, hi = S.clustered_bootstrap(
        lambda i: (float(np.mean(vals[i][flags[i] == 1]))
                   - float(np.mean(vals[i][flags[i] == 0]))
                   if (flags[i] == 1).any() and (flags[i] == 0).any() else 0.0),
        cl, n_boot=1500)
    return {"n": int(sel.sum()), "acc": float(a.mean()), "d": r, "lo": lo, "hi": hi}


def replicates(g1, g2, high):
    """Same sign in both halves, both above the simulated size bar."""
    r1, r2 = band_vs_rest(g1, high), band_vs_rest(g2, high)
    if r1 is None or r2 is None:
        return None
    ok = ((r1["d"] > 0) == (r2["d"] > 0)
          and abs(r1["d"]) > MIN_EFFECT and abs(r2["d"]) > MIN_EFFECT
          and r1["d"] > 0)
    return {"ok": bool(ok), "first": r1["d"], "second": r2["d"],
            "acc_first": r1["acc"], "acc_second": r2["acc"]}


def backtest_gate(g):
    """elite minus the rest on the fitting rows, interval must clear zero."""
    if len(g) < MIN_FIT:
        return None
    r = band_vs_rest(g, True)
    if r is None:
        return None
    return {"ok": bool(r["lo"] > 0), "d": r["d"], "lo": r["lo"]}


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    back, live = load(eng, "backtest"), load(eng, "live")
    if live.empty:
        raise SystemExit("no live graded rows")
    live = live[live["game_date"] >= pd.Timestamp(f"{SEASON}-08-01").date()] \
        if live["game_date"].dtype == object else live
    cut = live["game_date"].quantile(0.5)
    h1, h2 = live[live["game_date"] <= cut], live[live["game_date"] > cut]

    print(f"{len(back):,} backtest props, {len(live):,} live "
          f"({len(h1):,} / {len(h2):,} either side of {cut})")
    print(f"top/bottom {SHARE:.0%}, replication bar {MIN_EFFECT:.0%} in each half")
    print()
    print(f"  {'market':<12}{'live n':>7}{'high end':>22}{'low end':>22}  verdict")

    out = {}
    for m in sorted(set(live["market_code"]) | set(back["market_code"])):
        gl = live[live["market_code"] == m]
        g1, g2 = h1[h1["market_code"] == m], h2[h2["market_code"] == m]
        hi = replicates(g1, g2, True) if len(g1) and len(g2) else None
        lo = replicates(g1, g2, False) if len(g1) and len(g2) else None

        def fmt(r):
            return (f"{'-':>22}" if r is None
                    else f"{r['first']:>+10.1%}{r['second']:>+10.1%}  ")

        if hi and hi["ok"]:
            out[m] = {"direction": "high", "evidence": "live split-half",
                      "first_half": hi["first"], "second_half": hi["second"],
                      "elite_accuracy": [hi["acc_first"], hi["acc_second"]]}
            verdict = "RANKED, high end"
        elif lo and lo["ok"]:
            out[m] = {"direction": "low", "evidence": "live split-half",
                      "first_half": lo["first"], "second_half": lo["second"],
                      "elite_accuracy": [lo["acc_first"], lo["acc_second"]]}
            verdict = "RANKED, LOW end (inverted)"
        else:
            bt = backtest_gate(back[back["market_code"] == m])
            if bt and bt["ok"] and (hi is None or hi["first"] > 0):
                out[m] = {"direction": "high", "evidence": "backtest gate",
                          "backtest_margin": bt["d"], "backtest_lower": bt["lo"]}
                verdict = "ranked on the backtest, too thin live"
            else:
                verdict = "flat, publishes as small"
        print(f"  {m:<12}{len(gl):>7}{fmt(hi)}{fmt(lo)}  {verdict}")

    print()
    print(f"{len(out)} market(s) earn a tiering: "
          + ", ".join(f"{k} ({v['direction']})" for k, v in sorted(out.items())))
    inverted = [k for k, v in out.items() if v["direction"] == "low"]
    if inverted:
        print(f"  inverted: {', '.join(inverted)} -- for these the NARROWEST")
        print("  disagreements are graded elite, because that is where they hit.")

    if os.getenv("WRITE") == "1":
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {OUT.name}")
    else:
        print("\nnothing written. WRITE=1 to save.")


if __name__ == "__main__":
    main()
