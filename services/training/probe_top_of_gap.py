"""Is the very top of the gap the best place to be, or the band just below it?

Asked because the walk-forward replay said no, twice over. Scored against the
price, elite's whole margin came from the second half of its own band:

    0%-5%     45 picks   +1.3%  [-9.1%,+10.9%]
    5%-10%    53 picks   -1.4%  [-13.7%,+13.1%]
    10%-15%   55 picks  +14.8%  [+3.9%,+28.4%]
    15%-20%   54 picks  +12.2%  [-0.0%,+23.5%]

Cumulatively the top 10% was +1.4% and the top 20% was +7.6%, so adding picks
RAISED the margin, which can only happen if the ones added are better than the
ones already there. And this is the second sighting of that shape: the 2025
work found |z| peaking at 0.75-1.00 rather than above it, a different season
and a different statistic pointing the same way.

I am not re-cutting a tier on 108 picks against 109 with overlapping
intervals. That is the mistake I am on record for twice. This file asks the
same question where there is enough data to answer it: the backtest rows,
which are a different population entirely (older seasons, and production
falls about 3% a year), so read the SHAPE and not the level.

The test is deliberately narrow. Within each ranked market, on each board,
rank by the gap in that market's own direction, cut percentile bands, and
print hit against what the book needed. Then split the backtest in half by
date and ask whether the shape replicates, because one pass over one
population is how rush_att nearly got shipped.

WHAT COUNTS AS A BOARD turned out to be worth as much as the measurement.
The first version of this file ranked within one game date; production ranks
across everything a build priced, which is Thursday to Monday. That single
change moved the live top decile from +1.4% to +5.6%, so both groupings are
run now and the verdict is read off the one production uses.

WHAT WOULD CHANGE THE RULE. If the top 10% underperforms the next 10% in both
halves of the backtest by more than the noise bar, then elite needs a ceiling
as well as a floor and the top decile belongs in strong. If it does not
replicate, the live pattern is 108 picks of variance and ELITE_SHARE stays.

Read-only. Writes nothing, changes no artifact.

    docker compose run --rm training python probe_top_of_gap.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp
import gap_tier

MIN_BOARD = int(os.getenv("MIN_BOARD", str(gap_tier.MIN_RANKABLE)))
MIN_BAND = int(os.getenv("MIN_BAND", "40"))
MIN_EFFECT = float(os.getenv("MIN_EFFECT", "0.08"))
EDGES = [0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0]

# WHAT COUNTS AS ONE BOARD, which is not a detail.
#
# build_prop_edges tiers with out.groupby("market_code") over everything it
# priced in that run, and the run prices every event that has not kicked off.
# So a Wednesday build pools Thursday through Monday into one ranking. A pick
# is elite because of where it sits among that whole slate.
#
# The first version of this file ranked within a single game_date, which
# production never does: it ranks a Thursday prop only against other Thursday
# props. That alone moved the live top decile from +1.4% to +5.6%, so the
# grouping is worth as much as the thing being measured and the two numbers
# were never comparable.
#
# "week" buckets Thursday through Monday together, which is the slate one
# build sees. The date is shifted back two days first because an NFL week runs
# Thursday to Monday and ISO weeks start on Monday, so a raw ISO week would
# file Monday night with the NEXT week's Thursday.
#
# "day" is kept as a robustness check, not as the answer. If the verdict
# depends on which one is used, that is itself the finding.
BOARDS = ("week", "day")


def load(eng):
    d = pd.read_sql(text("""
        SELECT market_code, game_date, player_name, source,
               line, hit, projection_median AS p50, price_american
        FROM prop_edge_results
        WHERE hit IS NOT NULL AND line IS NOT NULL
          AND projection_median IS NOT NULL
    """), eng)
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    d["gap"] = (d["p50"] - d["line"]).abs() / d["line"].abs().clip(lower=0.5)
    d["cluster"] = d["game_date"].astype(str)
    p = pd.to_numeric(d["price_american"], errors="coerce")
    d["needed"] = np.where(
        p > 0, 100.0 / (p + 100.0),
        np.where(p < 0, (-p) / ((-p) + 100.0), np.nan))
    return d


def board_key(d, mode):
    """Which rows production would have ranked against each other."""
    gd = pd.to_datetime(d["game_date"])
    if mode == "day":
        return gd.dt.date.astype(str)
    shifted = gd - pd.Timedelta(days=2)
    iso = shifted.dt.isocalendar()
    return iso["year"].astype(str) + "-w" + iso["week"].astype(str)


def rank_within_boards(d, cfg):
    """Percentile of the gap within each market's own board, its direction.

    This is what tiers_by_rank does, so it is what has to be reproduced here.
    Boards too thin to rank are dropped rather than pooled, because pooling
    two slates ranks picks production ranked separately.
    """
    pct = pd.Series(np.nan, index=d.index, dtype=float)
    kept = dropped = 0
    for (m, _), idx in d.groupby(["market_code", "board"]).groups.items():
        if m not in cfg or len(idx) < MIN_BOARD:
            dropped += len(idx)
            continue
        g = d.loc[idx, "gap"]
        if g.notna().sum() < MIN_BOARD:
            dropped += len(idx)
            continue
        asc = cfg[m]["direction"] == "low"
        pct.loc[idx] = g.rank(ascending=asc, method="first", pct=True).values
        kept += len(idx)
    return pct, kept, dropped


def band_line(label, s):
    ok = s["needed"].notna()
    if len(s) < MIN_BAND or ok.sum() < MIN_BAND:
        return (f"  {label:<12}{len(s):>7}{s['hit'].mean():>9.1%}"
                f"{'too thin':>12}")
    g = s[ok]
    diff = (g["hit"] - g["needed"]).to_numpy()
    r, lo, hi = S.clustered_bootstrap(
        lambda i: float(np.mean(diff[i])), g["cluster"].to_numpy(), n_boot=2000)
    flag = "  BEATS THE BOOK" if lo > 0 else ("  loses to it" if hi < 0 else "")
    return (f"  {label:<12}{len(g):>7}{g['hit'].mean():>9.1%}"
            f"{g['needed'].mean():>9.1%}{r:>+9.1%}"
            f"  [{lo:>+5.1%},{hi:>+5.1%}]{flag}")


def margin(s):
    """hit minus needed on a slice, or None if it cannot be measured."""
    g = s[s["needed"].notna()]
    if len(g) < MIN_BAND:
        return None
    return float((g["hit"] - g["needed"]).mean())


def bands(title, d):
    print(title)
    print(f"  {'band':<12}{'picks':>7}{'hit':>9}{'needed':>9}"
          f"{'hit - needed':>21}")
    for a, b in zip(EDGES, EDGES[1:]):
        s = d[(d["rank_pct"] > a) & (d["rank_pct"] <= b)]
        if s.empty:
            continue
        print(band_line(f"{a:.0%}-{b:.0%}", s))
    print()


def verdict(d, label):
    """The one comparison this file exists for: top decile vs the next."""
    top = d[d["rank_pct"] <= 0.10]
    nxt = d[(d["rank_pct"] > 0.10) & (d["rank_pct"] <= 0.20)]
    a, b = margin(top), margin(nxt)
    if a is None or b is None:
        print(f"  {label:<14}not enough priced rows to call "
              f"({len(top)} and {len(nxt)} picks)")
        return None
    print(f"  {label:<14}top 10% {a:>+7.1%}   next 10% {b:>+7.1%}"
          f"   difference {b - a:>+7.1%}")
    return b - a


def one_grouping(d, cfg, mode):
    """Rank, report and return the top-decile deltas under one definition."""
    d = d.copy()
    d["board"] = board_key(d, mode)
    d["rank_pct"], kept, dropped = rank_within_boards(d, cfg)
    d = d[d["rank_pct"].notna()]

    note = ("one build's slate, Thursday to Monday, which is what production "
            "ranks" if mode == "week" else
            "a single game day, which production never ranks on its own")
    print("=" * 74)
    print(f"BOARD = {mode.upper()}  ({note})")
    print("=" * 74)
    sizes = d.groupby(["market_code", "board"]).size() if len(d) else None
    med = int(sizes.median()) if sizes is not None and len(sizes) else 0
    print(f"{kept:,} props on boards big enough to rank, "
          f"{dropped:,} dropped as too thin; "
          f"{d['board'].nunique():,} boards, median {med} props each")
    back = d[d["source"] == "backtest"]
    live = d[d["source"] == "live"]
    print(f"backtest {len(back):,} props, live {len(live):,} props")
    print()

    if len(back):
        bands("BACKTEST -- older seasons, read the shape not the level", back)
    if len(live):
        bands("LIVE -- the 2026 board", live)

    print("top decile against the next")
    deltas = []
    if len(back):
        cut = back["game_date"].quantile(0.5)
        h1, h2 = back[back["game_date"] <= cut], back[back["game_date"] > cut]
        verdict(back, "backtest all")
        deltas = [verdict(h1, "backtest 1st"), verdict(h2, "backtest 2nd")]
    live_delta = verdict(live, "live 2026") if len(live) else None
    print()
    return [x for x in deltas if x is not None], live_delta


def call_it(halves, live_delta):
    if len(halves) < 2:
        print("CANNOT CALL IT. The backtest does not split into two halves")
        print("with enough priced rows in both bands, so there is nothing")
        print("here to overrule 108 live picks with. ELITE_SHARE stays.")
    elif all(x > MIN_EFFECT for x in halves):
        print("REPLICATES. The next decile beat the top one by more than")
        print(f"{MIN_EFFECT:.0%} in both halves of the backtest. Elite needs")
        print("a ceiling: the top decile belongs in strong, and ELITE_SHARE")
        print("should become a band rather than a floor.")
    elif all(x > 0 for x in halves):
        # The binding number is the WEAKER half, not the stronger one. An
        # earlier version printed max() here and so announced an effect of
        # +14.3% as being under an 8% bar, which reads as a typo and is
        # actually the whole point of a two-half test: the bar has to be
        # cleared twice, so the half that nearly misses is the one to quote.
        print("SAME SIGN, TOO SMALL. Both halves put the next decile ahead,")
        print(f"but the weaker only by {min(halves):.1%}, under the "
              f"{MIN_EFFECT:.0%} bar where")
        print("pure noise agrees about one time in four. The effect is")
        print("probably real and is far smaller than the live slice implied,")
        print("which is what a 108-pick slice does. ELITE_SHARE stays at 20%.")
    else:
        print("DOES NOT REPLICATE. The halves disagree, so the live pattern")
        print("is 108 picks of variance. ELITE_SHARE stays at 20% and the")
        print("top decile keeps its label.")
    if live_delta is not None and live_delta < 0:
        print()
        print("Note: on live rows the top decile was AHEAD of the next one,")
        print("so the live board does not support a ceiling either.")


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = load(eng)
    cfg = {k: v for k, v in gap_tier.v2_thresholds().items()
           if isinstance(v, dict) and v.get("direction")}
    if not cfg:
        raise SystemExit("gap_tiers.json has no ranked markets to probe")
    print("ranked markets: "
          + ", ".join(f"{k} ({v['direction']})" for k, v in sorted(cfg.items())))
    d = d[d["market_code"].isin(cfg)].copy()
    priced = int(d["needed"].notna().sum())
    print(f"{len(d):,} graded props in those markets, "
          f"{priced:,} with a price ({priced / max(len(d), 1):.0%})")
    print()

    out = {m: one_grouping(d, cfg, m) for m in BOARDS}

    print("=" * 74)
    print("VERDICT, on the week grouping, because that is what production does")
    print("=" * 74)
    call_it(*out["week"])
    print()
    wk, dy = out["week"][0], out["day"][0]
    if len(wk) == 2 and len(dy) == 2:
        same = all(x > MIN_EFFECT for x in wk) == all(x > MIN_EFFECT for x in dy)
        print(f"week grouping {wk[0]:+.1%} / {wk[1]:+.1%}, "
              f"day grouping {dy[0]:+.1%} / {dy[1]:+.1%}: "
              f"{'same answer either way' if same else 'THE ANSWER DEPENDS ON THE GROUPING'}")


if __name__ == "__main__":
    main()
