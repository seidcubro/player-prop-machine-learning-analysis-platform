"""Every loss at once, with a football cause and a statistical one.

Not "here is a bad pick and here is why". A taxonomy over the whole set of
losses, and the same taxonomy over the whole set of wins, so the difference
between them is a number rather than a story.

The construction. An outcome is workload times efficiency: targets times yards
per target, carries times yards per carry. Both are observable after the fact,
and the projection implies a value for each. So every miss decomposes into a
workload miss and an efficiency miss, and the shares of variance say which one
the model is actually getting wrong. This is the standard exposure-and-rate
split that a Poisson or negative binomial model handles with a log-exposure
offset, used here as a diagnostic rather than as a model.

Verdict, October 2026, on 216 published picks from the live 2026 board with a
prior-usage baseline:

    cause                             losses          wins      lift
    more work, normal efficiency       36.5%         23.8%      1.54
    normal work, bigger plays          24.3%         30.7%      0.79
    more work AND bigger plays         17.4%          2.0%      8.78
    he did what we expected             8.7%         19.8%      0.44

Fifty-four percent of losses are a player doing more work than his own recent
games implied, against twenty-six percent of wins. And the variance
decomposition puts 98% of the projection error in workload against 59% in
efficiency, with a negative covariance between them of -57%: when the touches
go up the yards per touch come down, which is ordinary regression and not
something to fix.

The cleanest cut in the whole exercise, our unders split by what the player's
workload actually did relative to his own baseline:

    workload vs his own base     picks     hit     units
    at or below base                59   74.6%   +25.17
    1.0 to 1.2x                     39   43.6%    -8.26
    1.2 to 1.5x                     17   29.4%    -6.34
    over 1.5x                       58   17.2%   -38.93

The model is not bad at football. It is good at football and blind to role
changes. When the role holds it wins three out of four. Every unit it lost went
to fifty-eight picks where the man got dramatically more work than he had been
getting: Terrance Ferguson from one target a game to nine, Jakobi Meyers from
one and a half to eight, Ollie Gordon from three carries to seventeen.

So the question becomes: what predicts a workload surge? Two candidates were
tested on three seasons.

**Matchup and scheme do not.** Defensive profiles built only from a defence's
earlier games in the same season, correlated against the next game's workload
surprise and efficiency surprise:

    receiver yards per target   cushion allowed       n=4673   r=+0.003  p=0.86
    receiver yards per target   separation allowed    n=4673   r=+0.039  p=0.007
    receiver yards per target   blitzers faced        n=4647   r=-0.004  p=0.81
    back yards per carry        box defenders         n=2041   r=+0.003  p=0.89
    receiver targets            cushion allowed       n=5401   r=-0.002  p=0.89
    back carries                box defenders         n=2178   r=+0.034  p=0.11

The profiles are real and have spread: cushion allowed runs from 4.56 yards to
8.47 across 2026 defences, which is the zone-against-man signature anybody
would want. It does not translate. The one result clearing p=0.01 explains
0.15% of the variance, which is detectable at n=4,673 and worth nothing.

**Who else is playing does.** Position-mates missing, against the same workload
surprise:

    receivers   n=3585   r=+0.122   p=2.6e-13
    backs       n=2223   r=+0.116   p=4.2e-08
    tight ends  n=1619   r=+0.010   p=0.68

    receivers, 0 regulars out   0.90x his own baseline
    receivers, 4 regulars out   1.19x
    backs, 0 regulars out       0.93x
    backs, 3 regulars out       1.27x

Three times the effect size of anything scheme-related, on the quantity that
actually costs us money. Tight ends show nothing, which fits: a tight end room
is two deep and not substitutable.

**And then the part that did not work.** A workload-weighted version of the
teammate feature was built and measured, because the existing one is a headcount
off the injury report: it weights a man taking eight targets a game the same as
one taking one, and it cannot see injured reserve at all, which is why De'Von
Achane tore an ACL and kept being projected.

    arm        picks     hit                  ROI     units
    without     1345   59.0% [55.4, 62.4]   +10.4%   +140.4
    with        1341   59.1% [55.3, 62.8]   +10.8%   +145.1

Paired on the 1,316 picks both arms publish: 59.3% to 59.3%, +145.37 units to
+145.37 units. Identical to the cent. It was not shipped.

Two reasons, and the second matters more than the first. It does not beat the
incumbent, which is the rule. And the version measured above reads
`players.status`, which holds a player's status *today* rather than in the week
being described, so on historical rows it knows things the model could not have
known. `rosters_weekly` carries a per-week status and is what a correct version
would use. A feature that is inert while enjoying a small look-ahead is not
going to become useful once the look-ahead is removed.

What did ship from this is the operational half: `players` is now refreshed on
every fast run rather than once a day, because that column is the only record in
this database that a man is on injured reserve, and a board built on a
twenty-four hour old roster will keep projecting him.

Usage:

    python research_loss_consensus.py            weeks 1 to 3 of 2026
    FROM=2026-10-01 TO=2026-10-05 python research_loss_consensus.py
"""

import os

import gap_tier
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

FROM = os.getenv("FROM", "2026-09-09")
TO = os.getenv("TO", "2026-09-28")

VOLUME = {"rec_yds": "targets", "recs": "targets", "rec_td": "targets",
          "rush_yds": "carries", "rush_att": "carries", "rush_td": "carries",
          "pass_yds": "attempts", "pass_att": "attempts",
          "pass_completions": "attempts", "pass_td": "attempts"}


def classify(vol_ratio, eff_ratio):
    """One label per pick, from what the player did rather than from the score."""
    if not np.isfinite(vol_ratio):
        return "no usage history"
    more, less = vol_ratio > 1.20, vol_ratio < 0.80
    big = np.isfinite(eff_ratio) and eff_ratio > 1.20
    small = np.isfinite(eff_ratio) and eff_ratio < 0.80
    if more and big:
        return "more work AND bigger plays"
    if more:
        return "more work, normal efficiency"
    if big:
        return "normal work, bigger plays"
    if less and small:
        return "less work AND quieter"
    if less:
        return "less work, normal efficiency"
    if small:
        return "normal work, quieter"
    return "he did what we expected"


def main():
    import autopsy_board as ab
    engine = create_engine(bp.DATABASE_URL, future=True)
    d = ab.load(engine, FROM, TO)

    act = pd.read_sql(text("""
        SELECT player_id, game_date, targets, carries, attempts
        FROM player_game_stats WHERE season >= 2026
    """), engine)
    act["game_date"] = pd.to_datetime(act["game_date"])
    idx = act.set_index(["player_id", "game_date"])

    def vol_of(r):
        col = VOLUME.get(r.market_code)
        try:
            v = idx.loc[(r.player_id, r.game_date), col]
        except KeyError:
            return np.nan
        return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)

    d["vol"] = [vol_of(r) for r in d.itertuples()]
    hist = act.sort_values("game_date")
    base = []
    for r in d.itertuples():
        col = VOLUME.get(r.market_code)
        h = hist[(hist.player_id == r.player_id)
                 & (hist.game_date < r.game_date)][col]
        base.append(float(h.mean()) if len(h) else np.nan)
    d["vol_base"] = base

    t = ab.tiers(d, d.p50)
    t["vol"] = d.loc[t.index, "vol"].values
    t["vol_base"] = d.loc[t.index, "vol_base"].values
    t["p50"] = d.loc[t.index, "p50"].values
    pub = t[t.tier.isin(ab.PUBLISHED)].copy()
    pub = pub[pub.vol.notna() & pub.vol_base.notna()]
    if pub.empty:
        raise SystemExit("no published picks with a usage baseline in that span")

    pub["vol_ratio"] = pub.vol / pub.vol_base.replace(0, np.nan)
    pub["eff_ratio"] = ((pub.actual / pub.vol.replace(0, np.nan))
                        / (pub.p50 / pub.vol_base.replace(0, np.nan)))
    pub["why"] = [classify(r.vol_ratio, r.eff_ratio) for r in pub.itertuples()]

    print(f"\n{len(pub)} published picks with a prior usage baseline, "
          f"{FROM} to {TO}")
    print(f"{pub.won.mean():.1%} hit, {pub.profit.sum():+.1f} units\n")
    L, W = pub[~pub.won], pub[pub.won]
    print(f"  {'cause':<32}{'losses':>8}{'of all':>9}{'wins':>7}{'of all':>9}{'lift':>7}")
    for k in pub.why.value_counts().index:
        nl, nw = (L.why == k).sum(), (W.why == k).sum()
        if nl + nw < 6:
            continue
        pl, pw = nl / max(len(L), 1), nw / max(len(W), 1)
        print(f"  {k:<32}{nl:>8}{pl:>9.1%}{nw:>7}{pw:>9.1%}"
              f"{pl / max(pw, 1e-9):>7.2f}")

    print("\n  error variance, workload against efficiency:")
    lv = np.log(pub.vol.replace(0, np.nan) / pub.vol_base.replace(0, np.nan))
    le = np.log(pub.eff_ratio.replace(0, np.nan))
    m = np.isfinite(lv) & np.isfinite(le)
    tot = (lv[m] + le[m]).var()
    print(f"    workload   {lv[m].var() / tot:>6.0%}")
    print(f"    efficiency {le[m].var() / tot:>6.0%}")
    print(f"    covariance {2 * np.cov(lv[m], le[m])[0, 1] / tot:>6.0%}   n={m.sum()}")

    print("\n  unders, by what the workload actually did:\n")
    u = pub[pub.side == "under"]
    print(f"  {'workload vs his own base':<28}{'picks':>8}{'hit':>9}{'units':>9}")
    for lo, hi, lab in [(0, 1.0, "at or below base"), (1.0, 1.2, "1.0 to 1.2x"),
                        (1.2, 1.5, "1.2 to 1.5x"), (1.5, 99, "over 1.5x")]:
        s = u[(u.vol_ratio >= lo) & (u.vol_ratio < hi)]
        if len(s) < 8:
            continue
        print(f"  {lab:<28}{len(s):>8}{s.won.mean():>9.1%}{s.profit.sum():>+9.2f}")

    print("\n  the losses where the role moved most:\n")
    worst = L.nlargest(10, "vol_ratio")
    print(worst[["player_name", "market_code", "side", "p50", "line", "actual",
                 "vol_base", "vol", "vol_ratio"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
