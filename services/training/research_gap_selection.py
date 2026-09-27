"""Does a bigger disagreement with the line mean a better pick?

This is the question behind the board Seid wants: rank picks by how far our
projection sits from the line, publish the biggest gaps, and ignore the price.
A backup tight end who catches two a game with a 3.5 line is an elite pick
because the number is wrong, not because the under pays -120.

It is a different rule from the one that ships. Tiers are currently chosen on
expected value, which is the gap converted to a probability and then weighed
against the odds, and that rule will happily call a coin flip elite when the coin
flip is priced at +150. The two rules disagree most on exactly the picks that
prompted this: same projection as the line, good price.

The project has one previous measurement pointing the other way, recorded as
"we beat the book on only 43.8% of props; the biggest disagreements are where
we're most wrong". If that holds, ranking by gap selects the worst picks
available and the rule cannot work. It was measured before the season-boundary
fix, before the retrain, and without clustered intervals, so it is worth
measuring again properly rather than either trusting or dismissing.

How the gap is measured matters. Raw units do not compare across markets, and a
2-yard gap on a 15-yard line is not a 2-yard gap on an 80-yard line. Three
versions are reported:

    z       (projection - line) / sd, where sd comes from the published
            quantiles as (q75 - q25) / 1.349. This is the statistically right
            one: it asks how many standard deviations of that player's own
            distribution the disagreement is worth.
    pct     (projection - line) / line. What a person would eyeball.
    raw     the gap in the market's own units, for reference.

Judged by hit rate against the line, which is the thing Seid asked to optimise,
with intervals that resample dates rather than props because props in a game
share their outcome. A rule is only worth building if the top bucket beats the
break-even a bettor actually faces, and the interval says so.

The second half asks his specific case directly: does the rule work better on
low-volume players, where a line above everything the man has ever done is more
obviously wrong than a line in the middle of a star's range?
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
N_BOOT = int(os.getenv("N_BOOT", "2000"))


def load(eng):
    d = pd.read_sql(text("""
        SELECT r.player_name, r.game_date, r.market_code, r.line, r.projection,
               r.projection_median, r.q25, r.q75, r.recommended_side, r.hit,
               r.price_american, r.edge_tier, r.season, r.win_prob,
               r.expected_value
        FROM prop_edge_results r
        WHERE r.hit IS NOT NULL AND r.line IS NOT NULL
          AND r.projection IS NOT NULL AND r.price_american IS NOT NULL
          AND r.price_american <> 0
    """), eng)
    for c in ("line", "projection", "projection_median", "q25", "q75",
              "price_american", "win_prob", "expected_value"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["game_date"] = pd.to_datetime(d["game_date"])
    d["y"] = d["hit"].astype(bool).astype(float)
    d["cluster"] = d["game_date"].dt.strftime("%Y%m%d")

    # The side the gap rule would take, which is where the projection sits
    # relative to the line. The median is what the board currently uses and is
    # kept alongside, because the two disagree and that disagreement is itself a
    # finding this project has already paid for.
    src = d["projection_median"].fillna(d["projection"])
    d["gap_raw"] = src - d["line"]
    d["gap_pct"] = d["gap_raw"] / d["line"].replace(0, np.nan)
    sd = (d["q75"] - d["q25"]) / 1.349
    d["sd"] = sd.where(sd > 0)
    d["gap_z"] = d["gap_raw"] / d["sd"]
    d["gap_side"] = np.where(d["gap_raw"] > 0, "over", "under")

    # hit is recorded for the side the board published. Where the gap rule would
    # have taken the other side, the outcome inverts. Half-point lines make a
    # push impossible, so there is no third case.
    flip = d["gap_side"] != d["recommended_side"]
    d["y_gap"] = np.where(flip, 1.0 - d["y"], d["y"])
    d["breakeven"] = np.where(d["price_american"] > 0,
                              100.0 / (d["price_american"] + 100.0),
                              -d["price_american"] / (-d["price_american"] + 100.0))
    return d


def buckets(d, col, label, n=5, use_gap_side=True):
    v = d[col].abs()
    ok = d[np.isfinite(v)].copy()
    if len(ok) < 300:
        print(f"  {label}: too few rows")
        return
    ok["_v"] = ok[col].abs()
    try:
        ok["_b"] = pd.qcut(ok["_v"].rank(method="first"), n, labels=False)
    except ValueError:
        print(f"  {label}: cannot bucket")
        return
    y_col = "y_gap" if use_gap_side else "y"
    print(f"\n  {label}, {n} buckets from smallest disagreement to largest")
    print(f"  {'bucket':<10}{'n':>6}{'median gap':>12}{'hit rate':>26}"
          f"{'breakeven':>11}")
    for b in range(n):
        m = ok["_b"] == b
        s = ok[m]
        if len(s) < 50:
            continue
        y = s[y_col].to_numpy()
        g = s["cluster"].to_numpy()
        rate, lo, hi = S.clustered_bootstrap(
            lambda idx, y=y: float(np.mean(y[idx])), g, n_boot=N_BOOT)
        star = ""
        if np.isfinite(lo) and lo > s["breakeven"].mean():
            star = "  <- beats its price"
        print(f"  {b + 1:<10}{len(s):>6}{s['_v'].median():>12.2f}"
              f"   {rate:>7.1%} [{lo:>6.1%}, {hi:>6.1%}]"
              f"{s['breakeven'].mean():>11.1%}{star}")


def main():
    eng = create_engine(DATABASE_URL, future=True)
    d = load(eng)
    print(f"{len(d)} graded picks over {d['cluster'].nunique()} dates, "
          f"{int(d['season'].min())}-{int(d['season'].max())}.\n"
          f"Hit rate is for the side the GAP rule would take, so a pick the "
          f"board took the other way\nis scored inverted. Intervals resample "
          f"dates. {N_BOOT} resamples.")

    print("\n" + "=" * 78)
    print("1. Does a bigger gap win more often?")
    buckets(d, "gap_z", "by z, the gap in standard deviations of the player's "
                        "own distribution")
    buckets(d, "gap_pct", "by percentage of the line")
    for code, g in d.groupby("market_code"):
        if len(g) >= 600:
            buckets(g, "gap_z", f"by z, {code} only", n=4)

    print("\n" + "=" * 78)
    print("2. The rule that ships, for comparison: expected value")
    buckets(d, "expected_value", "by EV as the board computes it",
            use_gap_side=False)

    print("\n" + "=" * 78)
    print("3. Seid's case: is the rule better on players the line has to be "
          "wrong about?")
    # A low line is a low-volume player. The specific case is a line above
    # everything he plausibly does, where being wrong is easier to see.
    low = d[d["line"] <= d.groupby("market_code")["line"].transform("median")]
    high = d[d["line"] > d.groupby("market_code")["line"].transform("median")]
    for name, s in (("low lines (backups, role players)", low),
                    ("high lines (starters, stars)", high)):
        if len(s) >= 400:
            print(f"\n  {name}: {len(s)} picks")
            buckets(s, "gap_z", "by z", n=4)

    print("\n" + "=" * 78)
    print("4. How many picks the two rules disagree about")
    bet = d[d["edge_tier"].isin(["elite", "strong"])]
    tiny = bet[bet["gap_z"].abs() < 0.25]
    print(f"  {len(bet)} picks in a bet tier; {len(tiny)} of them "
          f"({len(tiny) / max(len(bet), 1):.0%}) sit within a quarter of a "
          f"standard deviation of the line,\n  which is the coin-flip-at-a-good-"
          f"price case. They hit {tiny['y'].mean():.1%} "
          f"against a break-even of {tiny['breakeven'].mean():.1%}.")
    big = d[d["gap_z"].abs() >= 1.0]
    if len(big) >= 100:
        y = big["y_gap"].to_numpy()
        rate, lo, hi = S.clustered_bootstrap(
            lambda idx, y=y: float(np.mean(y[idx])), big["cluster"].to_numpy(),
            n_boot=N_BOOT)
        print(f"  {len(big)} picks disagree with the line by a full standard "
              f"deviation or more.\n  Taking the gap's side on those: "
              f"{S.fmt_ci(rate, lo, hi, pct=True)} against a break-even of "
              f"{big['breakeven'].mean():.1%}.")

    print("\n" + "=" * 78)
    print("5. Does it hold season by season, or is it one good year?\n"
          "   A rule with no fitted parameters cannot be overfitted, but it can\n"
          "   still be a fluke, and a threshold chosen by looking at this table\n"
          "   would be. Reported at several thresholds so the choice is visible.")
    for thr in (0.5, 0.75, 1.0):
        s = d[d["gap_z"].abs() >= thr]
        print(f"\n   gap of at least {thr} sd: {len(s)} picks")
        print(f"   {'season':<9}{'picks':>7}{'hit':>8}{'breakeven':>11}"
              f"{'edge':>8}")
        for season, g in s.groupby("season"):
            if len(g) < 40:
                print(f"   {int(season):<9}{len(g):>7}   too few")
                continue
            h = g["y_gap"].mean()
            b = g["breakeven"].mean()
            print(f"   {int(season):<9}{len(g):>7}{h:>8.1%}{b:>11.1%}"
                  f"{h - b:>+8.1%}")
        h, b = s["y_gap"].mean(), s["breakeven"].mean()
        print(f"   {'all':<9}{len(s):>7}{h:>8.1%}{b:>11.1%}{h - b:>+8.1%}")


if __name__ == "__main__":
    main()
