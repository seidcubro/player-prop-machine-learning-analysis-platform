"""How would today's tiering have done on weeks 1 to 8, week by week?

Two versions of that question, and only one of them is honest.

IN-SAMPLE just applies today's configuration to every past week. It is the
flattering version and it cannot be trusted, because the configuration was
chosen using those same weeks: recs was set to the high end of the gap and
rush_yds to the low end by measuring weeks 1 to 8. Scoring that back on weeks
1 to 8 asks a rule how well it describes the data it was fitted to.

WALK-FORWARD refits before every week using only what was known by then. For
week W the direction of each market is decided from the backtest rows plus the
live weeks before W, and only then is week W tiered. In week 3 that means
almost nothing is known and most markets publish flat; by week 8 it has seven
weeks of live evidence, which is roughly what the board has now. This is the
version that answers "what if we had been running this all along", and it is
the one to read.

Both are printed, because the gap between them is itself the measurement: if
in-sample looks strong and walk-forward does not, the rule is fitted noise.

WHAT THIS DOES NOT REPLAY. The side of each pick, and the projection behind it,
are taken as they were published at the time. Today's median would differ --
different calibrators, a different season offset, a spread calibrator that
accepted two markets in October and none now -- and reproducing that would mean
re-running the whole projection pipeline for every week, which is
backtest_production's job and takes hours. So this answers "how would today's
TIERING have ranked the picks we made", not "what would today's model have
picked". The first question is the one the tier labels are a promise about.

Read-only.

    docker compose run --rm training python replay_current_rules.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp
import gap_tier

SEASON = int(os.getenv("SEASON", "2026"))
SHARE = float(os.getenv("SHARE", "0.20"))
MIN_EFFECT = float(os.getenv("MIN_EFFECT", "0.08"))
MIN_SIDE = int(os.getenv("MIN_SIDE", "20"))
MIN_FIT = int(os.getenv("MIN_FIT", "300"))
TIERS = ("elite", "strong", "medium", "small")


def load(eng):
    d = pd.read_sql(text("""
        SELECT r.market_code, r.game_date, r.player_name, r.source,
               r.line, r.hit, r.projection_median AS p50,
               g.week
        FROM prop_edge_results r
        LEFT JOIN LATERAL (
            SELECT week FROM nfl_games n
            WHERE n.game_date = r.game_date AND n.season = :season LIMIT 1
        ) g ON TRUE
        WHERE r.hit IS NOT NULL AND r.line IS NOT NULL
          AND r.projection_median IS NOT NULL
    """), eng, params={"season": SEASON})
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    d["gap"] = (d["p50"] - d["line"]).abs() / d["line"].abs().clip(lower=0.5)
    d["cluster"] = d["game_date"].astype(str)
    d["week"] = d["week"].fillna(-1).astype(int)
    return d


def band_vs_rest(g, high):
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
    r, lo, _ = S.clustered_bootstrap(
        lambda i: (float(np.mean(vals[i][flags[i] == 1]))
                   - float(np.mean(vals[i][flags[i] == 0]))
                   if (flags[i] == 1).any() and (flags[i] == 0).any() else 0.0),
        cl, n_boot=800)
    return {"d": r, "lo": lo}


def decide(live_so_far, backtest):
    """The same rule fit_gap_tiers applies, given only what was known then."""
    cfg = {}
    for m in sorted(set(backtest["market_code"]) | set(live_so_far["market_code"])):
        gl = live_so_far[live_so_far["market_code"] == m]
        chosen = None
        if len(gl) >= MIN_SIDE * 6:
            cut = gl["game_date"].quantile(0.5)
            h1, h2 = gl[gl["game_date"] <= cut], gl[gl["game_date"] > cut]
            for high in (True, False):
                r1 = band_vs_rest(h1, high) if len(h1) else None
                r2 = band_vs_rest(h2, high) if len(h2) else None
                if (r1 and r2 and r1["d"] > MIN_EFFECT and r2["d"] > MIN_EFFECT):
                    chosen = "high" if high else "low"
                    break
        if chosen is None:
            gb = backtest[backtest["market_code"] == m]
            if len(gb) >= MIN_FIT:
                r = band_vs_rest(gb, True)
                if r and r["lo"] > 0:
                    chosen = "high"
        if chosen:
            cfg[m] = {"direction": chosen}
    return cfg


def tier_week(week_rows, cfg):
    gap_tier._V2 = cfg
    out = pd.Series("small", index=week_rows.index, dtype=object)
    for m, idx in week_rows.groupby("market_code").groups.items():
        out.loc[idx] = gap_tier.tiers_by_rank(
            week_rows.loc[idx, "gap"].tolist(), m)
    return out


def report(title, d, col):
    print(title)
    print(f"  {'week':<6}" + "".join(f"{t:>18}" for t in TIERS)
          + f"{'elite on top?':>15}")
    for w in sorted(x for x in d["week"].unique() if x > 0):
        g = d[d["week"] == w]
        cells, vals = [], {}
        for t in TIERS:
            s = g[g[col] == t]
            if len(s) < 10:
                cells.append(f"{'-':>18}")
                continue
            vals[t] = float(s["hit"].mean())
            cells.append(f"{f'{vals[t]:.1%} (n={len(s)})':>18}")
        ok = ("yes" if vals and max(vals, key=vals.get) == "elite"
              else ("no" if vals else "-"))
        print(f"  {w:<6}" + "".join(cells) + f"{ok:>15}")
    print(f"  {'ALL':<6}", end="")
    for t in TIERS:
        s = d[d[col] == t]
        if len(s) < 30:
            print(f"{'-':>18}", end="")
            continue
        w_ = s["hit"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w_[i])), s["cluster"].to_numpy(), n_boot=2000)
        print(f"{f'{r:.1%} (n={len(s)})':>18}", end="")
    print()
    for t in TIERS:
        s = d[d[col] == t]
        if len(s) < 30:
            continue
        w_ = s["hit"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w_[i])), s["cluster"].to_numpy(), n_boot=2000)
        print(f"    {t:<8}{len(s):>6}{r:>9.1%}  [{lo:>5.1%},{hi:>5.1%}]")
    print()


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = load(eng)
    live = d[(d["source"] == "live") & (d["week"] > 0)].copy()
    back = d[d["source"] == "backtest"]
    if live.empty:
        raise SystemExit("no live graded rows with a week")
    weeks = sorted(live["week"].unique())
    print(f"{len(live):,} live graded props, weeks {weeks[0]} to {weeks[-1]}, "
          f"{len(back):,} backtest rows available to fit on")
    print(f"as published: {live['hit'].mean():.1%} overall")
    print()

    # 1. today's configuration, applied to everything. The flattering one.
    today = {k: {"direction": v.get("direction", "high")}
             for k, v in (gap_tier.v2_thresholds() or {}).items()}
    print(f"today's configuration: "
          + (", ".join(f"{k} ({v['direction']})" for k, v in sorted(today.items()))
             or "none loaded"))
    print()
    if today:
        live["tier_insample"] = ""
        for w in weeks:
            idx = live.index[live["week"] == w]
            live.loc[idx, "tier_insample"] = tier_week(live.loc[idx], today).values
        report("IN SAMPLE -- today's rules on the weeks they were fitted to "
               "(do not trust this)", live, "tier_insample")

    # 2. walk-forward. The one that answers the question.
    live["tier_wf"] = ""
    print("refitting before each week, on what was known by then:")
    for w in weeks:
        prior = live[live["week"] < w]
        cfg = decide(prior, back)
        idx = live.index[live["week"] == w]
        live.loc[idx, "tier_wf"] = tier_week(live.loc[idx], cfg).values
        print(f"  week {w}: fitted on {len(prior):,} live rows -> "
              + (", ".join(f"{k} ({v['direction']})" for k, v in sorted(cfg.items()))
                 or "nothing ranked, all flat"))
    print()
    report("WALK FORWARD -- what the board would actually have published",
           live, "tier_wf")

    # Where does the edge actually stop?
    #
    # Walk-forward, elite (the top 10%) came back 61.1% and strong (the next
    # 20%) came back 62.0%. Those are the same number, so the elite label is
    # drawn narrower than the thing it is labelling, and medium at 51.5% and
    # small at 50.1% are coin flips. That puts the boundary somewhere around
    # 30% of the board rather than 10%, but reading it off two rows is how
    # thresholds get chosen badly, so it is swept.
    #
    # Each share is applied with the SAME walk-forward configuration already
    # computed per week, so this is only moving the cut, not refitting the
    # direction. The volume column matters as much as the accuracy: a share
    # that hits well on nine picks is not a product.
    print("where the edge stops: top N% of each ranked market, walk forward")
    print(f"  {'share':<8}{'picks':>7}{'correct':>9}  95% CI{'':<8}"
          f"{'vs 52.4%':>10}")
    for share in (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50):
        old_e, old_s, old_m = (gap_tier.ELITE_SHARE, gap_tier.STRONG_SHARE,
                               gap_tier.MEDIUM_SHARE)
        gap_tier.ELITE_SHARE, gap_tier.STRONG_SHARE, gap_tier.MEDIUM_SHARE =             share, 0.0, 0.0
        picks = []
        for w in weeks:
            prior = live[live["week"] < w]
            cfg = decide(prior, back)
            idx = live.index[live["week"] == w]
            t = tier_week(live.loc[idx], cfg)
            picks.append(live.loc[idx][t.values == "elite"])
        gap_tier.ELITE_SHARE, gap_tier.STRONG_SHARE, gap_tier.MEDIUM_SHARE =             old_e, old_s, old_m
        sel = pd.concat(picks) if picks else live.iloc[:0]
        if len(sel) < 30:
            print(f"  {share:<8.0%}{len(sel):>7}{'too few':>9}")
            continue
        w_ = sel["hit"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w_[i])), sel["cluster"].to_numpy(),
            n_boot=2000)
        flag = "clears" if lo > 0.524 else ""
        print(f"  {share:<8.0%}{len(sel):>7}{r:>9.1%}  [{lo:>5.1%},{hi:>5.1%}]"
              f"{flag:>10}")
    print()

    base = live["hit"].mean()
    el = live[live["tier_wf"] == "elite"]
    if len(el) >= 30:
        w_ = el["hit"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w_[i])), el["cluster"].to_numpy(), n_boot=2000)
        print(f"elite, walk forward: {len(el)} picks at {r:.1%} "
              f"[{lo:.1%}, {hi:.1%}] against {base:.1%} for the whole board, "
              f"a lift of {r - base:+.1%}")


if __name__ == "__main__":
    main()
