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

HIT RATE IS NOT THE SCORE, AND THE FIRST VERSION OF THIS FILE TREATED IT AS
ONE. It reported elite at 66.2% against 51.8% for the whole board and called
that a lift of fourteen points. That compares our picks to a different set of
picks and proves nothing, which the live board then demonstrated: break-even
on the published tiers runs 54.0% at small and 68.0% at elite, because
gap_share divides by the line and the ranked markets are pass_td, recs and
rush_yds, where a 0.5 or 1.5 line carries a heavily skewed price. The rule is
partly selecting props that were going to land anyway.

So every table here now prints what the book needed next to what we hit, and
puts the interval on the difference. A prop priced at -212 lands 68% of the
time on its own; hitting 66% of those means the book read them better than we
did. Only hit minus needed, with an interval clear of zero, says we know
something the market does not.

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
               r.price_american,
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

    # What the book thought, in the same unit as our hit rate.
    #
    # Added after the first run of this file reported elite at 66.2% against
    # 51.8% for the whole board and called it a lift of +14.4 points. That
    # compares our picks against a DIFFERENT set of picks, which answers
    # nothing: the board average includes markets we do not rank and props at
    # prices nothing like these.
    #
    # The live board gave the game away. Break-even climbs with every tier,
    # 54.0% at small to 68.0% at elite, because gap_share divides by the line
    # and the ranked markets are pass_td, recs and rush_yds, where a 0.5 or
    # 1.5 line carries a heavily skewed price. So the rule is partly selecting
    # props that were likely to land anyway, and a hit rate measured against
    # the board average cannot tell that apart from skill.
    #
    # The honest comparison is per pick: the implied probability with the vig
    # in, which is the share the book says has to win. hit minus needed is the
    # only number here that says we know something the market does not.
    p = pd.to_numeric(d["price_american"], errors="coerce")
    d["needed"] = np.where(
        p > 0, 100.0 / (p + 100.0),
        np.where(p < 0, (-p) / ((-p) + 100.0), np.nan))
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


def edge_line(label, s):
    """picks, hit, what the book needed, and the difference with an interval.

    hit on its own cannot distinguish a good pick from a likely one. A prop
    priced at -212 lands 68% of the time because it was going to; hitting 66%
    on it means the book read it better than we did. So the difference is the
    number with the interval on it, and it is bootstrapped over game-days
    because picks on one Sunday share weather and game script.
    """
    ok = s["needed"].notna()
    if ok.sum() < 30:
        return (f"{label:<8}{len(s):>6}{s['hit'].mean():>9.1%}"
                f"{'no prices':>12}")
    g = s[ok]
    diff = (g["hit"] - g["needed"]).to_numpy()
    r, lo, hi = S.clustered_bootstrap(
        lambda i: float(np.mean(diff[i])), g["cluster"].to_numpy(), n_boot=2000)
    flag = "  BEATS THE BOOK" if lo > 0 else ("  loses to it" if hi < 0 else "")
    return (f"{label:<8}{len(g):>6}{g['hit'].mean():>9.1%}"
            f"{g['needed'].mean():>9.1%}"
            f"{r:>+9.1%}  [{lo:>+5.1%},{hi:>+5.1%}]{flag}")


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
    print(f"    {'tier':<8}{'picks':>6}{'hit':>9}{'needed':>9}{'hit - needed':>22}")
    for t in TIERS:
        s = d[d[col] == t]
        if len(s) < 30:
            continue
        print("    " + edge_line(t, s))
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
    print(f"  {'share':<8}{'picks':>6}{'hit':>9}{'needed':>9}"
          f"{'hit - needed':>22}")
    for share in (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50):
        old_e, old_s, old_m = (gap_tier.ELITE_SHARE, gap_tier.STRONG_SHARE,
                               gap_tier.MEDIUM_SHARE)
        gap_tier.ELITE_SHARE = share
        gap_tier.STRONG_SHARE = gap_tier.MEDIUM_SHARE = 0.0
        picks = []
        for w in weeks:
            prior = live[live["week"] < w]
            cfg = decide(prior, back)
            idx = live.index[live["week"] == w]
            t = tier_week(live.loc[idx], cfg)
            picks.append(live.loc[idx][t.values == "elite"])
        gap_tier.ELITE_SHARE, gap_tier.STRONG_SHARE, gap_tier.MEDIUM_SHARE = (
            old_e, old_s, old_m)
        sel = pd.concat(picks) if picks else live.iloc[:0]
        if len(sel) < 30:
            print(f"  {share:<8.0%}{len(sel):>7}{'too few':>9}")
            continue
        print("  " + edge_line(f"{share:.0%}", sel))
    print()

    # The cumulative column above hides where the edge actually stops, because
    # every row of it contains every row above it: top 50% reads 57.0% only
    # because the top 20% is inside it carrying the whole thing. So the same
    # walk-forward ranks are cut into DISJOINT bands and each one is asked on
    # its own. This is the measurement the tier boundaries are set from, and it
    # is printed every run so the boundary gets re-checked rather than
    # inherited.
    print("disjoint bands of the walk-forward rank, within each ranked market")
    print(f"  {'band':<12}{'picks':>6}{'hit':>9}{'needed':>9}"
          f"{'hit - needed':>22}")
    pct = pd.Series(np.nan, index=live.index, dtype=float)
    for w in weeks:
        prior = live[live["week"] < w]
        cfg = decide(prior, back)
        wk = live[live["week"] == w]
        for m, idx in wk.groupby("market_code").groups.items():
            if m not in cfg or len(idx) < gap_tier.MIN_RANKABLE:
                continue
            g = wk.loc[idx, "gap"]
            if g.notna().sum() < gap_tier.MIN_RANKABLE:
                continue
            # Rank so that 0 is the confident end, whichever end that is for
            # this market. An inverted market's narrowest gaps rank first.
            asc = cfg[m]["direction"] == "low"
            r = g.rank(ascending=asc, method="first", pct=True)
            pct.loc[idx] = r.values
    live["rank_pct"] = pct
    edges = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.75, 1.0]
    for lo_e, hi_e in zip(edges, edges[1:]):
        sel = live[(live["rank_pct"] > lo_e) & (live["rank_pct"] <= hi_e)]
        label = f"{lo_e:.0%}-{hi_e:.0%}"
        if len(sel) < 30:
            print(f"  {label:<12}{len(sel):>6}{'too few':>9}")
            continue
        print("  " + edge_line(label, sel))
    unranked = live["rank_pct"].isna()
    if unranked.sum() >= 30:
        print(f"  {'unranked':<12}{int(unranked.sum()):>7}"
              f"{live.loc[unranked, 'hit'].mean():>9.1%}"
              f"   markets with no validated direction, all published small")
    print()

    el = live[(live["tier_wf"] == "elite") & live["needed"].notna()]
    if len(el) >= 30:
        diff = (el["hit"] - el["needed"]).to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(diff[i])), el["cluster"].to_numpy(),
            n_boot=2000)
        print(f"elite, walk forward: {len(el)} picks hitting "
              f"{el['hit'].mean():.1%} where the book needed "
              f"{el['needed'].mean():.1%}, a margin of {r:+.1%} "
              f"[{lo:+.1%}, {hi:+.1%}]")
        if lo <= 0:
            print("  That interval contains zero, so on these picks we have")
            print("  NOT shown we know better than the book. A high hit rate")
            print("  on heavily priced props is mostly the price, not us.")


if __name__ == "__main__":
    main()
