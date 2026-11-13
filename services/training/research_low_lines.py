"""Does the elite tier over-rate overs on tiny lines?

Grading the players who took a snap and recorded nothing put 57 props into the
2026 record, 47 of them losses. Eight were elite picks and seven of those were
the same bet: over 0.5 or over 1.5 catches on a backup tight end or a fourth
receiver (Drew Sample twice, Austin Hooper, Devontez Walker, Theo Johnson, Van
Jefferson, Jake Ferguson).

There is a reason to expect that. Tiers are ranked on the gap between my
number and the line as a share of the line. A projection of 1.4 catches
against a line of 0.5 is a gap of 180 percent, which outranks almost anything
a starter can produce, and it is built on the players whose role is least
certain. The price says so too: those overs are offered at -160 and shorter,
so they have to win far more than half the time to be worth anything.

This cuts every graded pick by the size of its line and its side, for the
whole record, the elite tier and this season's live picks, and puts the rate
each band needed beside the rate it hit. The needed rate is the average
implied probability of the prices taken.

If small-line overs hit well under what they needed while the same tier's
other picks do not, the ranking wants a floor on the line or a gap measured
in the stat's own units for these lines. That is a change to the ranking, not
a pick taken off the board.

Read-only.

    docker compose run --rm training python research_low_lines.py
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SINCE = int(os.getenv("SINCE_SEASON", "2023"))
NOW = int(os.getenv("SEASON", "2026"))
COUNTS = ["recs", "rush_att", "pass_td", "pass_att", "pass_completions"]


def implied(price):
    p = pd.to_numeric(price, errors="coerce")
    return np.where(p < 0, -p / (-p + 100.0), 100.0 / (p + 100.0))


def line(d):
    n = len(d)
    if n == 0:
        return f"{'none':<44}"
    hit = d["hit"].mean()
    need = d["need"].mean()
    half = 1.96 * np.sqrt(hit * (1 - hit) / n)
    return (f"{hit:>6.1%} on {n:<5} needed {need:>5.1%}  "
            f"{hit - need:>+6.1%} [{hit - need - half:+.1%}, "
            f"{hit - need + half:+.1%}]")


def show(d, label):
    print(f"\n{label}: {line(d)}")
    for market, bands in (("recs", [(0, 1.0, "0.5"), (1.0, 2.0, "1.5"),
                                    (2.0, 3.0, "2.5"), (3.0, 99, "3.5 and up")]),
                          ("rec_yds", [(0, 10, "under 10"), (10, 25, "10 to 25"),
                                       (25, 999, "25 and up")]),
                          ("rush_yds", [(0, 15, "under 15"), (15, 40, "15 to 40"),
                                        (40, 999, "40 and up")])):
        part = d[d.market_code == market]
        if part.empty:
            continue
        print(f"  {market}")
        for lo, hi, name in bands:
            b = part[(part.line >= lo) & (part.line < hi)]
            print(f"    line {name:<11} over   {line(b[b.side == 'over'])}")
            print(f"    {'':<16} under  {line(b[b.side == 'under'])}")


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = pd.read_sql(text("""
        SELECT season, source, market_code, line::float8 AS line,
               recommended_side AS side, edge_tier AS tier, hit, actual,
               price_american
        FROM prop_edge_results
        WHERE hit IS NOT NULL AND season >= :since
    """), eng, params={"since": SINCE})
    d["hit"] = d["hit"].astype(bool)
    d["need"] = implied(d["price_american"])
    d.loc[d["price_american"].isna(), "need"] = 110.0 / 210.0

    live = d[(d.season == NOW) & (d.source == "live")]
    show(d, f"EVERY PICK SINCE {SINCE}")
    show(d[d.tier == "elite"], "ELITE, EVERY SEASON")
    show(live, f"{NOW} LIVE")
    show(live[live.tier == "elite"], f"{NOW} LIVE, ELITE")
    show(live[live.tier.isin(["strong", "medium"])], f"{NOW} LIVE, STRONG AND MEDIUM")

    zero = live[(live.actual == 0) & (live.side == "over")]
    print(f"\n{NOW} live overs where the player finished on zero: {len(zero)}. "
          f"By tier: " + ", ".join(f"{t} {n}" for t, n in
                                   zero.tier.value_counts().items()))
    print("\nThe last figure on each row is hit rate minus what the prices "
          "needed, with its interval. A band is a problem if that interval "
          "sits below zero on a few hundred picks and the band beside it "
          "does not.")


if __name__ == "__main__":
    main()
