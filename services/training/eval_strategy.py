"""Which subset of the board is actually worth betting?

Two effects are now established on out-of-sample graded picks and neither was
found by fishing: the tiers rank monotonically once the probability is
calibrated (elite +4.7% down to small -4.5%), and the under side has been
positive in every season and every cut while the over side has been negative in
all of them (+3.8% against -5.0%).

The obvious question is what happens when they are combined, and whether the
over side is worth carrying at all. That is worth answering carefully rather
than by eye, because "filter until the number goes up" is how a backtest gets
talked into anything.

So the rules are fixed in advance:

  * **Candidates are chosen on 2023-24 and verified on 2025.** The verification
    season is never used to pick.
  * **Only pre-registered dimensions.** Tier, side, market and the value flag,
    all of which were established before this script existed. No searching over
    arbitrary feature buckets.
  * **Slate-clustered bootstrap.** Picks on one Sunday share weather and game
    scripts, and 71.5% of player-games carry more than one pick, so treating
    them as independent makes every interval far too narrow.
  * **One pick per player-game is reported alongside.** Receptions and receiving
    yards on the same player win and lose together; stacking them multiplies
    variance without multiplying edge.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

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
VERIFY_SEASON = int(os.getenv("VERIFY_SEASON", "2025"))


def load(engine) -> pd.DataFrame:
    d = pd.read_sql(text("""
        SELECT player_id, game_date, season, market_code, recommended_side,
               win_prob, expected_value, edge_tier, hit, price_american, line,
               actual
        FROM prop_edge_results
        WHERE hit IS NOT NULL AND price_american IS NOT NULL
    """), engine)
    p = d["price_american"].astype(float)
    d["decimal"] = np.where(p < 0, 1 + 100.0 / -p, 1 + p / 100.0)
    d["breakeven"] = 1.0 / d["decimal"]
    d["hit"] = d["hit"].astype(float)
    d["units"] = np.where(d["hit"] > 0, d["decimal"] - 1.0, -1.0)
    return d


def boot(f, n_boot=3000, seed=11):
    if len(f) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    groups = [g["units"].to_numpy() for _, g in f.groupby("game_date")]
    out = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(groups), len(groups))
        out.append(np.concatenate([groups[i] for i in idx]).mean())
    return tuple(np.percentile(out, [2.5, 97.5]))


def stats(f):
    if len(f) == 0:
        return None
    return {"n": len(f), "hit": f["hit"].mean(), "be": f["breakeven"].mean(),
            "roi": f["units"].mean()}


HDR = f"{'':<38}{'n':>7}{'hit':>8}{'be':>8}{'edge':>9}{'ROI':>9}"


def line(f, label, ci=False):
    s = stats(f)
    if not s:
        return f"{label:<38}{0:>7}"
    out = (f"{label:<38}{s['n']:>7}{s['hit']:>8.3f}{s['be']:>8.3f}"
           f"{s['hit'] - s['be']:>+9.3f}{s['roi']:>+9.3f}")
    if ci:
        lo, hi = boot(f)
        out += f"   [{lo:+.3f}, {hi:+.3f}]" + ("  CLEARS ZERO" if lo > 0 else "")
    return out


def one_per_player_game(f):
    return (f.sort_values("expected_value", ascending=False)
             .drop_duplicates(subset=["game_date", "player_id"]))


# Pre-registered candidate strategies. Every filter here uses a dimension that
# was established before this script, so nothing is being discovered by search.
STRATEGIES = {
    "everything": lambda d: d,
    "unders only": lambda d: d[d["recommended_side"] == "under"],
    "overs only": lambda d: d[d["recommended_side"] == "over"],
    "elite tier": lambda d: d[d["edge_tier"] == "elite"],
    "elite + strong": lambda d: d[d["edge_tier"].isin(["elite", "strong"])],
    "elite, unders only": lambda d: d[(d["edge_tier"] == "elite")
                                      & (d["recommended_side"] == "under")],
    "elite+strong, unders only": lambda d: d[
        d["edge_tier"].isin(["elite", "strong"])
        & (d["recommended_side"] == "under")],
    "elite, unders, one per player": lambda d: one_per_player_game(
        d[(d["edge_tier"] == "elite") & (d["recommended_side"] == "under")]),
}


def main():
    engine = create_engine(DATABASE_URL, future=True)
    d = load(engine)
    pick = d[d["season"] < VERIFY_SEASON]
    verify = d[d["season"] == VERIFY_SEASON]
    print(f"{len(d)} graded picks. Choosing on "
          f"{sorted(int(s) for s in pick['season'].dropna().unique())} "
          f"({len(pick)}), verifying on {VERIFY_SEASON} ({len(verify)}).")
    print()

    print("=== SELECTION SEASONS (2023-24) ===")
    print(HDR)
    for name, fn in STRATEGIES.items():
        print(line(fn(pick), name))

    print()
    print(f"=== VERIFICATION SEASON ({VERIFY_SEASON}), never used to choose ===")
    print(HDR + f"{'95% CI on ROI':>26}")
    for name, fn in STRATEGIES.items():
        print(line(fn(verify), name, ci=True))

    print()
    print("=== IS THE OVER SIDE WORTH CARRYING AT ALL? ===")
    print("If no slice of the over side is positive across both periods, it is")
    print("costing money on every board it appears on.")
    print()
    print(HDR)
    for period, frame in (("2023-24", pick), (str(VERIFY_SEASON), verify)):
        overs = frame[frame["recommended_side"] == "over"]
        print(line(overs, f"{period}: all overs"))
        for tier in ("elite", "strong"):
            print(line(overs[overs["edge_tier"] == tier], f"{period}:   {tier} overs"))
        for mk, sub in overs.groupby("market_code"):
            if len(sub) >= 120:
                print(line(sub, f"{period}:   {mk} overs"))
        print()

    print("=== WHAT DROPPING THE OVER SIDE WOULD HAVE DONE ===")
    print(HDR + f"{'95% CI on ROI':>26}")
    for period, frame in (("2023-24", pick), (str(VERIFY_SEASON), verify)):
        print(line(frame, f"{period}: board as published", ci=True))
        print(line(frame[frame["recommended_side"] == "under"],
                   f"{period}: unders only", ci=True))
    print()
    print("Both numbers come from the same board. The difference is only which")
    print("rows were bet, so it is a decision rule rather than a new model.")


if __name__ == "__main__":
    main()
