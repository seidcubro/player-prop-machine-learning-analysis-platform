"""Find a statistic that ranks accuracy in the four markets the gap cannot.

fit_gap_tiers settled two of six. The share-of-line gap separates recs (+6.6%)
and pass_td (+13.0%) and does nothing for pass_yds, rec_yds, rush_att or
rush_yds, so those four publish every pick as small and nobody can be told which
of them is worth more than a coin flip. This searches for something that does.

Twelve candidates across four markets is forty-eight tests, which is enough to
manufacture a result. Drawing from pure noise, a one-sided 95% gate accepts 2.3
of 48 on average and 91% of searches find at least one "signal", so a single
gate here would be a machine for inventing confidence.

FIRST RUN, and what it changed. Nothing held, but two flaws in the test could
hide a real signal and both were visible in its own output.

The cut was an absolute number fitted on the backtest and applied to the live
rows, which for several candidates selected almost nothing: rec_yds gap_iqr
took 20% of the backtest and 9 live rows, iqr_rel took 2, rush_att iqr_rel took
0. That is the statistic's SCALE drifting between populations, a different
failure from its RANKING not transferring, and only the second is the question.
Cuts are now taken at the same quantile WITHIN each population.

And the live stage had no power. Twenty percent of 150 to 335 props is 30 to 70
picks, which needs a margin of 11 to 18 points before an interval clears zero,
so everything smaller read as "nothing" whether it was there or not. The
verdict now rests on the later 40% of the backtest, which is several hundred
props, and the live season is a forward check that only has to not contradict
it. The minimum detectable effect is printed beside every stage so an
underpowered null is never mistaken for a finding.

The candidates, and why each might rank being right:

    gap_pct     |p50 - line| / |line|. The one that worked twice.
    gap_abs     the same in raw units, which was flat pooled.
    gap_iqr     gap over the player's own published interval width. A five yard
                gap on a steady player is a different claim than the same gap
                on a volatile one, and this is the only form that knows it.
    gap_sd      the same idea from the feature row's window instead.
    gap_point   the gap from the point projection rather than the median.
    agree       gap_pct, zeroed unless the point and the median fall on the
                same side of the line. Disagreeing with ourselves is a
                confession and should cost the pick its grade.
    iqr_rel     interval width over the line. Not a gap: just how sure the
                model is about this player, smaller being surer.
    cv          the window's coefficient of variation. A player who does the
                same thing every week is a player we can call.
    games       how many games the window holds. Thin history is behind more
                than one failure in this project.
    line_size   the line itself. Books are sharper on headline numbers than on
                a fourth receiver, so our edge may live at the bottom.
    depth       depth chart rank, where a settled starter should be easier.
    win_prob    what the board already publishes, as the incumbent to beat.

Read-only, writes nothing. Whatever survives goes through fit_gap_tiers next.

    docker compose run --rm training python find_confidence_signal.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

TOP_SHARE = float(os.getenv("TOP_SHARE", "0.20"))
MIN_FIT = int(os.getenv("MIN_FIT", "300"))
MIN_HOLD = int(os.getenv("MIN_HOLD", "150"))
TARGET = tuple(os.getenv(
    "MARKETS", "pass_yds,rec_yds,rush_att,rush_yds").split(","))

CANDIDATES = ["gap_pct", "gap_abs", "gap_iqr", "gap_sd", "gap_point", "agree",
              "iqr_rel", "cv", "games", "line_size", "depth", "win_prob"]


def load(eng):
    d = pd.read_sql(text("""
        SELECT r.market_code, r.game_date, r.player_id, r.player_name, r.source,
               r.line, r.hit, r.win_prob, r.projection,
               r.projection_median AS p50, r.q25, r.q75,
               f.stddev AS win_sd, f.mean AS win_mean,
               (f.extra_features->>'depth_rank')::float AS depth_rank,
               (f.extra_features->>'games_in_window')::float AS games_in_window
        FROM prop_edge_results r
        JOIN prop_markets m ON m.code = r.market_code
        LEFT JOIN active_models a ON a.market_id = m.id
        LEFT JOIN player_market_features f
               ON f.player_id::text = r.player_id::text
              AND f.market_id = m.id
              AND f.as_of_game_date = r.game_date
              AND f.lookback = a.lookback
        WHERE r.hit IS NOT NULL AND r.line IS NOT NULL
          AND r.projection_median IS NOT NULL
    """), eng)
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    d["cluster"] = d["game_date"].astype(str)

    ln = d["line"].abs().clip(lower=0.5)
    gap = (d["p50"] - d["line"]).abs()
    iqr = (d["q75"] - d["q25"]).abs()
    d["gap_pct"] = gap / ln
    d["gap_abs"] = gap
    d["gap_iqr"] = gap / iqr.clip(lower=0.1)
    d["gap_sd"] = gap / d["win_sd"].abs().clip(lower=0.1)
    d["gap_point"] = (d["projection"] - d["line"]).abs() / ln
    same = np.sign(d["projection"] - d["line"]) == np.sign(d["p50"] - d["line"])
    d["agree"] = np.where(same, d["gap_pct"], 0.0)
    # Negated so "larger is more confident" holds for every candidate and the
    # same top-slice logic applies without special cases.
    d["iqr_rel"] = -(iqr / ln)
    d["cv"] = -(d["win_sd"].abs() / d["win_mean"].abs().clip(lower=0.1))
    d["line_size"] = -ln
    d["games"] = d["games_in_window"]
    d["depth"] = -d["depth_rank"]
    return d


def margin(sub):
    """elite minus the rest, with a clustered interval on the difference."""
    a = sub.loc[sub["elite"], "hit"].to_numpy()
    b = sub.loc[~sub["elite"], "hit"].to_numpy()
    if len(a) < 25 or len(b) < 25:
        return None
    vals = np.concatenate([a, b])
    flags = np.concatenate([np.ones(len(a)), np.zeros(len(b))])
    cl = np.concatenate([sub.loc[sub["elite"], "cluster"].to_numpy(),
                         sub.loc[~sub["elite"], "cluster"].to_numpy()])
    return S.clustered_bootstrap(
        lambda i: (float(np.mean(vals[i][flags[i] == 1]))
                   - float(np.mean(vals[i][flags[i] == 0]))
                   if (flags[i] == 1).any() and (flags[i] == 0).any() else 0.0),
        cl, n_boot=1500)


def mde(n_a, n_b):
    """Smallest margin an interval at these sizes could ever clear zero on."""
    if n_a < 2 or n_b < 2:
        return float("nan")
    return 1.96 * ((0.25 / n_a + 0.25 / n_b) ** 0.5)


def elite_mask(frame, col, share):
    """Top `share` of THIS population, handling statistics with few values."""
    v = frame[col].replace([np.inf, -np.inf], np.nan)
    if v.notna().sum() < 50:
        return None
    if v.nunique() < 5:
        # depth_rank and games_in_window take a handful of values, so a
        # quantile is meaningless; split above the median instead.
        thr = float(v.median())
        m = (v > thr).fillna(False)
        if m.sum() < 25 or (~m).sum() < 25:
            m = (v >= thr).fillna(False)
        return m
    return (v >= float(v.quantile(1.0 - share))).fillna(False)


def stage(frame, col, share):
    m = elite_mask(frame, col, share)
    if m is None or m.sum() < 25 or (~m).sum() < 25:
        return None
    sub = frame.assign(elite=m)
    r = margin(sub)
    if r is None:
        return None
    return {"n": int(m.sum()), "acc": float(sub.loc[m, "hit"].mean()),
            "margin": r[0], "lo": r[1], "hi": r[2],
            "mde": mde(int(m.sum()), int((~m).sum()))}


def fmt(st):
    if st is None:
        return f"{'-':>8}{'-':>10}{'-':>20}"
    ci = f"[{st['lo']:+.1%},{st['hi']:+.1%}]"
    return f"{st['n']:>8}{st['margin']:>+10.1%}{ci:>20}"


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = load(eng)
    back = d[d["source"] == "backtest"].sort_values("game_date")
    live = d[d["source"] == "live"]
    print(f"{len(back):,} backtest props, {len(live):,} live")
    print(f"top {TOP_SHARE:.0%} of each population is the candidate elite band")
    print()
    print("  Three stages. The cut is fitted on the earlier 60% of the backtest,")
    print("  judged on the later 40%, then checked forward on the live season.")
    print("  The middle stage is the verdict: it is the only one with enough")
    print("  rows to see a small effect. 'mde' is the smallest margin an")
    print("  interval that size could clear zero on, so a null below it means")
    print("  untested rather than disproved.")
    print()

    winners = []
    for market in TARGET:
        gb = back[back["market_code"] == market]
        gl = live[live["market_code"] == market]
        if len(gb) < MIN_FIT:
            print(f"{market}: only {len(gb)} backtest props, skipped")
            print()
            continue
        cut_date = gb["game_date"].quantile(0.60)
        fit = gb[gb["game_date"] < cut_date]
        hold = gb[gb["game_date"] >= cut_date]
        acc = f"{gl['hit'].mean():.1%}" if len(gl) else "n/a"
        print(f"{market}   fit {len(fit):,} to {cut_date}   "
              f"holdout {len(hold):,}   live {len(gl):,} at {acc}")
        if len(hold) < MIN_HOLD:
            print("  holdout too small to judge")
            print()
            continue
        print(f"  {'statistic':<12}{'fit n':>7}{'fit mgn':>9}"
              f"{'hold n':>8}{'hold mgn':>10}{'hold 95% CI':>20}{'mde':>8}"
              f"{'live n':>8}{'live mgn':>10}{'live 95% CI':>20}")
        for col in CANDIDATES:
            sf = stage(fit, col, TOP_SHARE)
            sh = stage(hold, col, TOP_SHARE)
            sl = stage(gl, col, TOP_SHARE)
            if sf is None or sh is None:
                print(f"  {col:<12}{'too few rows or too few distinct values':>55}")
                continue
            held = sh["lo"] > 0
            agrees = sl is None or sl["margin"] > 0
            keep = sf["margin"] > 0 and held and agrees
            mark = "  <== holds" if keep else ""
            print(f"  {col:<12}{sf['n']:>7}{sf['margin']:>+9.1%}"
                  f"{fmt(sh)}{sh['mde']:>8.1%}{fmt(sl)}{mark}")
            if keep:
                winners.append((market, col, sh, sl))
        print()

    print("=" * 70)
    if not winners:
        print("Nothing survived the powered holdout.")
        print()
        print("Read the mde column before calling it settled. Where a margin is")
        print("smaller than mde, the question is open and needs more graded")
        print("weeks, not a different statistic. Those four markets stay flat")
        print("until something earns them.")
        return
    print(f"{len(winners)} candidate(s) cleared the holdout with the live season")
    print("not contradicting them:")
    print(f"  {'market':<12}{'statistic':<12}{'hold n':>8}{'hold acc':>10}"
          f"{'hold margin':>14}{'live margin':>13}")
    for market, col, sh, sl in winners:
        lv = f"{sl['margin']:+.1%}" if sl else "untested"
        print(f"  {market:<12}{col:<12}{sh['n']:>8}{sh['acc']:>10.1%}"
              f"{sh['margin']:>+14.1%}{lv:>13}")
    print()
    print("Still not shipped. Run fit_gap_tiers with the winning statistic and")
    print("let it set the cuts, so the thresholds are fitted the same way the")
    print("two markets already live were.")


if __name__ == "__main__":
    main()
