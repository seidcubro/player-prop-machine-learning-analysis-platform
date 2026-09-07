"""Hunt for the situations where the market is actually wrong.

Four tests have now shown the model cannot beat the market on average. That does
not mean the market is uniformly sharp. Books price thousands of props a week at
low limits and cannot possibly give equal attention to all of them, so if there
are holes they will be in identifiable *situations*, not spread evenly.

This searches for those situations, with the discipline that search demands.
Slice 6,700 graded picks fifty ways and several slices will look profitable by
luck alone, so:

  1. **Discovery and validation are separate slates.** Candidates are found on
     the earlier 60% of the season and only reported if they survive on the
     later 40%, which the search never touched.
  2. **Bootstrap by slate, not by pick.** Props on the same slate share weather
     and game scripts. Treating them as independent makes everything look
     significant.
  3. **The multiplicity is stated.** With N buckets tested, the expected number
     of false positives at p<0.05 is 0.05N, and that number is printed so the
     survivors can be read against it.

Every condition tested is something knowable before kickoff. Anything else would
be a fantasy backtest.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import eval as ev

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)

MIN_N = int(os.getenv("MIN_N", "150"))


def load(engine) -> pd.DataFrame:
    """Graded picks joined to the pre-kickoff context they were made in."""
    df = pd.read_sql(
        text("""
            SELECT b.*, p.external_id, p.position, m.id AS market_id
            FROM backtest_log b
            JOIN players p
              ON lower(replace(replace(p.name, '.', ''), '-', ' ')) =
                 lower(replace(replace(b.player_name, '.', ''), '-', ' '))
            JOIN prop_markets m ON m.code = b.market_code
            WHERE b.hit IS NOT NULL
        """),
        engine,
    )
    feats = pd.read_sql(
        text("""
            SELECT player_id, market_id, as_of_game_date, extra_features,
                   weighted_mean, stddev
            FROM player_market_features WHERE lookback = 5
        """),
        engine,
    )
    df = df.merge(
        feats,
        left_on=["external_id", "market_id", "game_date"],
        right_on=["player_id", "market_id", "as_of_game_date"],
        how="inner",
    )
    ex = df["extra_features"].apply(ev._normalize_extra_features)
    for k in ["snap_share_mean", "depth_rank", "depth_rank_delta",
              "days_since_last_game", "is_home", "is_indoor", "game_wind",
              "game_temp", "team_implied_total", "team_spread",
              "pos_teammates_out", "y_season_n", "rz_targets_mean",
              "opp_pos_rec_yds_allowed", "opp_pos_rush_yds_allowed",
              "injury_questionable", "y_max", "y_median"]:
        df[k] = ex.apply(lambda d, k=k: d.get(k, np.nan))
    df["breakeven"] = 1.0 / df["decimal"]
    df["units"] = np.where(df["hit"] > 0, df["decimal"] - 1.0, -1.0)
    # How volatile the player has been. A boom/bust profile is exactly the kind
    # of thing a line priced off an average would misprice.
    df["spikiness"] = df["y_max"] / df["y_median"].replace(0, np.nan)
    return df


def conditions(df: pd.DataFrame):
    """Pre-kickoff conditions worth testing, as (name, boolean mask) pairs.

    All hypothesis-driven rather than exhaustive: each one is a situation where
    there is a reason to think a book might be slow or a line stale.
    """
    q = df.quantile(numeric_only=True)
    c = []

    def add(name, mask):
        c.append((name, mask.fillna(False)))

    # Role changed since the window the line was probably priced off.
    add("demoted since window", df["depth_rank_delta"] >= 1)
    add("promoted since window", df["depth_rank_delta"] <= -1)
    add("stale window (>60d)", df["days_since_last_game"] > 60)

    # Opportunity vacated by a team-mate.
    add("teammate out at same pos", df["pos_teammates_out"] >= 1)

    # Thin sample: the book has less to go on too.
    add("under 4 games this season", df["y_season_n"] < 4)
    add("9+ games this season", df["y_season_n"] >= 9)

    # Role size.
    add("low snap share (<40%)", df["snap_share_mean"] < 0.40)
    add("high snap share (>70%)", df["snap_share_mean"] > 0.70)
    add("starter (depth 1)", df["depth_rank"] <= 1)
    add("backup (depth 2+)", df["depth_rank"] >= 2)

    # Game environment.
    add("high total (implied 25+)", df["team_implied_total"] >= 25)
    add("low total (implied <20)", df["team_implied_total"] < 20)
    add("big favourite (spread<=-7)", df["team_spread"] <= -7)
    add("big underdog (spread>=7)", df["team_spread"] >= 7)
    add("windy (15+ mph)", df["game_wind"] >= 15)
    add("indoors", df["is_indoor"] == 1)
    add("away game", df["is_home"] == 0)

    # Volatility profile.
    add("spiky player (max>3x median)", df["spikiness"] > 3)
    add("steady player (max<2x median)", df["spikiness"] < 2)

    # Injury designation.
    add("questionable tag", df["injury_questionable"] == 1)

    # Our own disagreement size, which failed on average but might work when
    # combined with a situation.
    add("we disagree a lot", df["edge"] >= df["edge"].quantile(0.75))
    add("we barely disagree", df["edge"] <= df["edge"].quantile(0.25))
    return c


def score(frame):
    if len(frame) == 0:
        return None
    return {
        "n": len(frame),
        "hit": frame["hit"].mean(),
        "be": frame["breakeven"].mean(),
        "edge": frame["hit"].mean() - frame["breakeven"].mean(),
        "roi": frame["units"].mean(),
    }


def boot_ci(frame, n_boot=1500, seed=7):
    """Slate-level bootstrap of the edge."""
    if frame.empty:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    slates = frame["game_date"].unique()
    groups = {d: g for d, g in frame.groupby("game_date")}
    out = []
    for _ in range(n_boot):
        pick = rng.choice(slates, size=len(slates), replace=True)
        s = pd.concat([groups[d] for d in pick], ignore_index=True)
        out.append(s["hit"].mean() - s["breakeven"].mean())
    return tuple(np.percentile(out, [2.5, 97.5]))


def main():
    engine = create_engine(DATABASE_URL, future=True)
    df = load(engine)
    if df.empty:
        raise SystemExit("no graded picks joined to features; run backtest_season.py")

    slates = sorted(df["game_date"].unique())
    cut = slates[int(len(slates) * 0.6)]
    disc = df[df["game_date"] < cut]
    val = df[df["game_date"] >= cut]
    print(f"{len(df)} graded picks over {len(slates)} slates")
    print(f"discovery: {len(disc)} picks before {cut}")
    print(f"validation: {len(val)} picks from {cut} onward\n")

    base = score(df)
    print(f"baseline (all picks): hit={base['hit']:.3f} be={base['be']:.3f} "
          f"edge={base['edge']:+.3f}\n")

    # Both sides of every condition, since a situation the book overprices is
    # just as useful as one it underprices.
    cands = []
    for name, mask in conditions(disc):
        for side in ("over", "under"):
            sub = disc[mask & (disc["side"] == side)]
            r = score(sub)
            if r and r["n"] >= MIN_N:
                cands.append((f"{name} + {side}", name, side, r))

    n_tested = len(cands)
    cands.sort(key=lambda t: -t[3]["edge"])
    print(f"=== DISCOVERY: {n_tested} conditions tested, top 12 by edge ===")
    print(f"{'condition':<38}{'n':>6}{'hit':>7}{'be':>7}{'edge':>8}")
    print("-" * 66)
    for label, _, _, r in cands[:12]:
        print(f"{label:<38}{r['n']:>6}{r['hit']:>7.3f}{r['be']:>7.3f}{r['edge']:>+8.3f}")

    print(f"\nexpected false positives at p<0.05 with {n_tested} tests: "
          f"{0.05 * n_tested:.1f}")

    print("\n=== VALIDATION on slates the search never saw ===")
    print(f"{'condition':<38}{'n':>6}{'hit':>7}{'be':>7}{'edge':>8}{'95% CI':>20}")
    print("-" * 86)
    survivors = []
    cond_val = dict(conditions(val))
    for label, name, side, dr in cands[:12]:
        mask = cond_val.get(name)
        if mask is None:
            continue
        sub = val[mask & (val["side"] == side)]
        r = score(sub)
        if not r or r["n"] < 40:
            print(f"{label:<38}{r['n'] if r else 0:>6}   (too few to validate)")
            continue
        lo, hi = boot_ci(sub)
        flag = "  SURVIVES" if lo > 0 else ""
        print(f"{label:<38}{r['n']:>6}{r['hit']:>7.3f}{r['be']:>7.3f}"
              f"{r['edge']:>+8.3f}   [{lo:+.3f}, {hi:+.3f}]{flag}")
        if lo > 0:
            survivors.append((label, r, (lo, hi)))

    print("\n" + "=" * 66)
    if survivors:
        print(f"{len(survivors)} condition(s) survived out-of-sample with a CI above zero:")
        for label, r, (lo, hi) in survivors:
            print(f"  {label}: edge {r['edge']:+.3f} [{lo:+.3f}, {hi:+.3f}], "
                  f"ROI {r['roi']:+.3f} on n={r['n']}")
        print("\nTreat as a lead, not a conclusion. They were chosen as the best")
        print("of the discovery set, so even honest validation is optimistic.")
    else:
        print("Nothing survived validation. The discovery-set winners were noise,")
        print("which is the expected outcome when the underlying edge is zero.")


if __name__ == "__main__":
    main()
