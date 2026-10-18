"""Find a statistic that ranks accuracy in the four markets the gap cannot.

fit_gap_tiers settled two of six. The share-of-line gap separates recs (+6.6%)
and pass_td (+13.0%) and does nothing for pass_yds, rec_yds, rush_att or
rush_yds, so those four publish every pick as small and nobody can be told which
of them is worth more than a coin flip. This searches for something that does.

Twelve candidates, four markets, forty-eight tests. That is enough tests to
manufacture a result, and the size of the problem is worth stating in numbers:
drawing from pure noise, a one-sided 95% gate accepts 2.3 of 48 on average and
91% of searches find at least one "signal". A backtest gate alone would
therefore be a machine for inventing confidence.

So nothing is accepted on one measurement. A statistic has to clear a bootstrap
interval on the BACKTEST, where the cuts are fitted, and then separate again on
the LIVE season, which the search never touches. Both columns are printed for
every candidate including the losers, because a search that only shows its
winners is the same mistake in a nicer shirt.

The candidates, and why each might rank being right:

    gap_pct        |p50 - line| / |line|. The one that worked twice.
    gap_abs        the same in raw units, which was flat pooled.
    gap_iqr        gap over the player's own published interval width. A five
                   yard gap on a steady player is a different claim than the
                   same gap on a volatile one, and this is the only candidate
                   that knows the difference.
    gap_sd         gap over the window's standard deviation, the same idea
                   measured from the feature row rather than the ladder.
    gap_point      the gap from the point projection rather than the median.
    agree          gap_pct, but zero unless the point and the median fall on
                   the same side of the line. Disagreement between our own two
                   numbers is a confession and should cost the pick its grade.
    iqr_rel        interval width over the line. Not a gap at all: just "how
                   sure is the model about this player", small being surer.
    cv             the window's own coefficient of variation. A player who
                   does the same thing every week is a player we can call.
    games          how many games the window actually holds. Thin history has
                   been behind more than one failure in this project.
    line_size      the line itself. Books are sharper on headline numbers than
                   on a fourth receiver, so our edge may live at the bottom.
    depth          depth chart rank, where a settled starter should be easier
                   to call than a rotation piece.
    win_prob       what the board already publishes, as the incumbent to beat.

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
MIN_TEST = int(os.getenv("MIN_TEST", "60"))
TARGET = ("pass_yds", "rec_yds", "rush_att", "rush_yds")


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
    same = (np.sign(d["projection"] - d["line"])
            == np.sign(d["p50"] - d["line"]))
    d["agree"] = np.where(same, d["gap_pct"], 0.0)
    # Smaller is more confident for these three, so they are negated and the
    # same "top slice" logic applies to every candidate without special cases.
    d["iqr_rel"] = -(iqr / ln)
    d["cv"] = -(d["win_sd"].abs() / d["win_mean"].abs().clip(lower=0.1))
    d["line_size"] = -ln
    d["games"] = d["games_in_window"]
    d["depth"] = -d["depth_rank"]
    return d


CANDIDATES = ["gap_pct", "gap_abs", "gap_iqr", "gap_sd", "gap_point", "agree",
              "iqr_rel", "cv", "games", "line_size", "depth", "win_prob"]


def margin(sub, flag_col):
    """elite minus the rest, with a clustered interval on the difference."""
    a = sub.loc[sub[flag_col], "hit"].to_numpy()
    b = sub.loc[~sub[flag_col], "hit"].to_numpy()
    if len(a) < 25 or len(b) < 25:
        return None
    vals = np.concatenate([a, b])
    flags = np.concatenate([np.ones(len(a)), np.zeros(len(b))])
    cl = np.concatenate([sub.loc[sub[flag_col], "cluster"].to_numpy(),
                         sub.loc[~sub[flag_col], "cluster"].to_numpy()])
    return S.clustered_bootstrap(
        lambda i: (float(np.mean(vals[i][flags[i] == 1]))
                   - float(np.mean(vals[i][flags[i] == 0]))
                   if (flags[i] == 1).any() and (flags[i] == 0).any() else 0.0),
        cl, n_boot=1500)


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = load(eng)
    fit_all, test_all = d[d["source"] == "backtest"], d[d["source"] == "live"]
    print(f"{len(fit_all):,} backtest props to search, {len(test_all):,} live to confirm")
    print(f"top {TOP_SHARE:.0%} of each statistic is the candidate elite band")
    print()
    print("  48 tests is enough to manufacture a result: from pure noise a")
    print("  one-sided 95% gate accepts 2.3 of them on average and 91% of")
    print("  searches find at least one. Nothing counts unless BOTH columns")
    print("  hold, and the live column was never searched over.")
    print()

    winners = []
    for market in TARGET:
        gf = fit_all[fit_all["market_code"] == market]
        gt = test_all[test_all["market_code"] == market]
        print(f"{market}   backtest {len(gf):,}   live {len(gt):,}   "
              f"overall live accuracy {gt['hit'].mean():.1%}"
              if len(gt) else f"{market}   no live rows")
        if len(gf) < MIN_FIT or len(gt) < MIN_TEST:
            print("  too few rows to judge\n")
            continue
        print(f"  {'statistic':<12}{'cut':>9}{'fit n':>7}{'fit elite':>11}"
              f"{'fit margin':>24}{'live n':>8}{'live elite':>12}{'live margin':>13}")
        for col in CANDIDATES:
            v = gf[col].replace([np.inf, -np.inf], np.nan)
            if v.notna().sum() < MIN_FIT or v.nunique() < 10:
                print(f"  {col:<12}{'-':>9}{int(v.notna().sum()):>7}"
                      f"{'not enough distinct values':>35}")
                continue
            cut = float(v.quantile(1.0 - TOP_SHARE))
            gf2 = gf.assign(elite=gf[col] >= cut)
            m = margin(gf2, "elite")
            if m is None:
                continue
            fm, flo, _ = m
            gt2 = gt.assign(elite=gt[col] >= cut)
            n_live = int(gt2["elite"].sum())
            live_acc = (gt2.loc[gt2["elite"], "hit"].mean()
                        if n_live >= 25 else float("nan"))
            lm = margin(gt2, "elite") if n_live >= 25 else None
            # BOTH intervals must clear zero, not just both point estimates.
            #
            # Rehearsed on a statistic that works on the fitting half and not on
            # the other: requiring only a positive live margin kept it, because
            # a fluke still lands at +1.5% half the time. Requiring the live
            # interval to exclude zero rejected it and kept the real one.
            passed = flo > 0 and lm is not None and lm[1] > 0
            mark = "  <== holds both" if passed else ""
            print(f"  {col:<12}{cut:>9.3f}{int(gf2['elite'].sum()):>7}"
                  f"{gf2.loc[gf2['elite'], 'hit'].mean():>11.1%}"
                  f"{fm:>+10.1%} [{flo:>+6.1%},{m[2]:>+6.1%}]"
                  f"{n_live:>8}"
                  f"{live_acc:>12.1%}"
                  f"{(lm[0] if lm else float('nan')):>+13.1%}"
                  f"{('' if lm is None else f' [{lm[1]:+.1%},{lm[2]:+.1%}]'):>18}{mark}")
            if passed:
                winners.append((market, col, cut, live_acc, lm[0]))
        print()

    print("=" * 70)
    if not winners:
        print("Nothing held on both. These four markets have no confidence")
        print("ranking that survives being tested twice, and publishing them")
        print("as flat small is the honest answer until something does.")
        return
    print(f"{len(winners)} candidate(s) held on the backtest AND the live season")
    print(f"  {'market':<12}{'statistic':<12}{'cut':>9}{'live elite':>12}{'live margin':>13}")
    for market, col, cut, acc, lm in winners:
        print(f"  {market:<12}{col:<12}{cut:>9.3f}{acc:>12.1%}{lm:>+13.1%}")
    print()
    print("Still not proof. The cut was chosen on the backtest and the live")
    print("season is one partial year, so the next week it has not seen is the")
    print("one that decides. Run fit_gap_tiers on anything here before it ships.")


if __name__ == "__main__":
    main()
