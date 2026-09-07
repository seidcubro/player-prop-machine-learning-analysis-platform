"""The hole is the over, and it has been in front of me the whole time.

The control group in `market_disagreement.py` was supposed to be a throwaway
sanity check, and it turned out to be the most important number in the project.
On the 7,782 props where all three books post the identical line:

    blind over    hit 0.460   break-even 0.521   edge -6.1%
    blind under   hit 0.540   break-even 0.542   edge -0.2%

Both sides of the same market, priced by the same books, and one of them is a
disaster while the other is free. That asymmetry is not noise and it is not the
vig, because the vig is symmetric by construction. It is the books shading the
number: player props are a recreational market, recreational money buys overs,
and the line gets pushed up until the over is a bad bet. The under absorbs the
whole correction, which leaves it sitting at break-even before any skill is
applied at all.

That reframes every failed test in this project. Each one was scored on a pool
that was roughly 60% overs, which meant carrying a -6% tax into every comparison.
No five-game rolling window was ever going to out-forecast that. The question was
never "can the model beat the market" -- it was "why am I paying six points to
take the side the market wants me on."

So this measures what is left once that tax is removed:

  1. Unders only, which start from break-even rather than -4.5%.
  2. At the best under price of the three books rather than the average, since
     averaging away a price difference throws out the one advantage that is
     mechanically certain rather than statistically hoped for.
  3. Sliced by line tier, to confirm the shading is strongest where the public
     money is heaviest.
  4. With the model's opinion layered on last, so it is obvious whether the
     model contributes anything or whether all of this is structural.

Every confidence interval bootstraps whole slates.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from market_disagreement import DATABASE_URL, MARKET_MAP, dec, load

MIN_BOOKS = int(os.getenv("MIN_BOOKS", "3"))


def to_props(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse book-lines into one row per prop, keeping both price regimes.

    `avg_*` is what a single-book bettor gets. `best_*` is what someone with all
    three accounts open gets. The gap between them is the part of the edge that
    requires no forecasting ability whatsoever.
    """
    df = df.copy()
    df["over_dec"] = dec(df["over_am"])
    df["under_dec"] = dec(df["under_am"])
    g = df.groupby(["provider_event_id", "market_code", "player_name",
                    "game_date"], as_index=False)
    props = g.agg(
        books=("bookmaker_key", "nunique"),
        line=("line", "median"),
        actual=("actual", "first"),
        over_avg=("over_dec", "mean"),
        under_avg=("under_dec", "mean"),
    )
    # Best price has to come from the row that offers it, at that row's own
    # line, or this quietly becomes a bet nobody could have placed.
    best_u = df.loc[df.groupby(
        ["provider_event_id", "market_code", "player_name", "game_date"]
    )["under_dec"].idxmax()]
    best_o = df.loc[df.groupby(
        ["provider_event_id", "market_code", "player_name", "game_date"]
    )["over_dec"].idxmax()]
    keys = ["provider_event_id", "market_code", "player_name", "game_date"]
    props = props.merge(
        best_u[keys + ["line", "under_dec"]].rename(
            columns={"line": "under_line", "under_dec": "under_best"}),
        on=keys, how="left",
    ).merge(
        best_o[keys + ["line", "over_dec"]].rename(
            columns={"line": "over_line", "over_dec": "over_best"}),
        on=keys, how="left",
    )
    return props[props["books"] >= MIN_BOOKS].copy()


def grade(props: pd.DataFrame, side: str, price: str) -> pd.DataFrame:
    """Grade one side at one price regime, dropping pushes rather than counting
    them as losses."""
    line = props[f"{side}_line"] if price == "best" else props["line"]
    d = props[f"{side}_{'best' if price == 'best' else 'avg'}"]
    won = (props["actual"] < line) if side == "under" else (props["actual"] > line)
    push = props["actual"] == line
    out = props.copy()
    out["bet_line"] = line
    out["decimal"] = d
    out["breakeven"] = 1.0 / d
    out["hit"] = np.where(push, np.nan, won.astype(float))
    out["units"] = np.where(push, 0.0, np.where(won, d - 1.0, -1.0))
    return out.dropna(subset=["hit", "decimal"])


def boot(f, n_boot=2000, seed=13):
    if len(f) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    groups = [g for _, g in f.groupby("game_date")]
    out = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(groups), len(groups))
        s = pd.concat([groups[i] for i in idx], ignore_index=True)
        out.append(s["hit"].mean() - s["breakeven"].mean())
    return tuple(np.percentile(out, [2.5, 97.5]))


HDR = f"{'':<36}{'n':>6}{'hit':>8}{'be':>8}{'edge':>9}{'ROI':>9}"


def line(f, label, ci=False):
    if len(f) == 0:
        return f"{label:<36}{0:>6}"
    hit, be = f["hit"].mean(), f["breakeven"].mean()
    s = (f"{label:<36}{len(f):>6}{hit:>8.3f}{be:>8.3f}"
         f"{hit - be:>+9.3f}{f['units'].mean():>+9.3f}")
    if ci:
        lo, hi = boot(f)
        s += f"   [{lo:+.3f}, {hi:+.3f}]" + ("  PROFITABLE" if lo > 0 else "")
    return s


def main():
    engine = create_engine(DATABASE_URL, future=True)
    props = to_props(load(engine))
    print(f"{len(props)} props with {MIN_BOOKS}+ books, "
          f"{props['game_date'].nunique()} slates\n")

    print("=== THE ASYMMETRY, AT BOTH PRICE REGIMES ===")
    print(HDR + f"{'95% CI':>26}")
    for side in ("over", "under"):
        for price in ("avg", "best"):
            g = grade(props, side, price)
            print(line(g, f"blind {side}, {price} price", ci=True))

    # How much of the gap is price and how much is the shading itself.
    u_avg = grade(props, "under", "avg")
    u_best = grade(props, "under", "best")
    print(f"\nshopping the under price is worth "
          f"{u_avg['breakeven'].mean() - u_best['breakeven'].mean():+.3f} of "
          f"break-even, with no forecasting involved at all")

    print("\n=== UNDERS BY LINE TIER (is the shading worst where the money is?) ===")
    print(HDR + f"{'95% CI':>26}")
    q = props.groupby("market_code")["line"].transform(
        lambda s: s.rank(pct=True))
    for lo, hi, lab in [(0.75, 1.01, "top quartile line"),
                        (0.50, 0.75, "third quartile"),
                        (0.25, 0.50, "second quartile"),
                        (0.00, 0.25, "bottom quartile line")]:
        sub = props[(q >= lo) & (q < hi)]
        print(line(grade(sub, "under", "best"), lab, ci=True))

    print("\n=== UNDERS BY MARKET, BEST PRICE ===")
    print(HDR + f"{'95% CI':>26}")
    for m, sub in props.groupby("market_code"):
        g = grade(sub, "under", "best")
        if len(g) >= 150:
            print(line(g, m, ci=True))

    print("\n=== TOP-QUARTILE UNDERS BY MARKET, BEST PRICE ===")
    print(HDR)
    top = props[q >= 0.75]
    for m, sub in top.groupby("market_code"):
        g = grade(sub, "under", "best")
        if len(g) >= 60:
            print(line(g, m))

    print("\n=== STABILITY: split the season in half ===")
    print(HDR)
    slates = sorted(props["game_date"].unique())
    mid = slates[len(slates) // 2]
    for lab, sub in [(f"before {mid}", top[top["game_date"] < mid]),
                     (f"{mid} onward", top[top["game_date"] >= mid])]:
        print(line(grade(sub, "under", "best"), f"top-quartile unders, {lab}"))

    print("\n=== DOES THE MODEL ADD ANYTHING ON TOP? ===")
    proj = pd.read_sql(text("""
        SELECT b.player_name, b.market_code, b.game_date, b.pred
        FROM backtest_log b WHERE b.pred IS NOT NULL
    """), engine)

    def key(s):
        return s.str.lower().str.replace(r"[.\-']", "", regex=True).str.strip()

    proj["key"] = key(proj["player_name"])
    t = top.copy()
    t["key"] = key(t["player_name"])
    t = t.merge(proj[["key", "market_code", "game_date", "pred"]],
                on=["key", "market_code", "game_date"], how="inner")
    if t.empty:
        print("  no model predictions matched; skipping")
        return
    print(HDR)
    print(line(grade(t, "under", "best"), f"all top-quartile unders (n matched)"))
    agree = t[t["pred"] < t["under_line"]]
    disagree = t[t["pred"] >= t["under_line"]]
    print(line(grade(agree, "under", "best"), "  model also says under"))
    print(line(grade(disagree, "under", "best"), "  model says over, bet under anyway"))
    print("\nIf those two rows are within noise of each other the model is a"
          "\npassenger and the edge is entirely structural.")


if __name__ == "__main__":
    main()
