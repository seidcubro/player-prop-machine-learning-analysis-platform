"""Should our projection be anchored to the market line?

`projection_log.py` produced an uncomfortable result: our projection lands closer
to the actual outcome than the sportsbook line on only ~44% of props, and the
pattern gets worse the more we disagree -- 49.6% when we are close to the line,
37.1% when we are furthest from it. Our biggest disagreements are where we are
most wrong, which is precisely backwards for a product that tiers "elite" by
disagreement size.

The market line is a sharp, liquid, market-clearing estimate. Treating it as a
prior rather than as an opponent is the standard response: predict

    blend = w * ours + (1 - w) * line

and learn `w` from data. w = 1 is what we do now; w = 0 abandons the model and
just repeats the line. The right answer is almost certainly in between, and if it
is near 0 that is worth knowing before staking money on the difference.

Fitted on the earlier slates, evaluated on the later ones, so the weight is never
chosen on the data it is scored against.
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


def main():
    engine = create_engine(DATABASE_URL, future=True)
    df = pd.read_sql(
        text("SELECT * FROM projection_log ORDER BY game_date"), engine
    )
    if df.empty:
        raise SystemExit("projection_log is empty; run projection_log.py first")

    slates = sorted(df["game_date"].unique())
    if len(slates) < 4:
        print(f"only {len(slates)} slates; the split will be coarse")
    cut = slates[len(slates) * 2 // 3]
    train = df[df["game_date"] < cut]
    test = df[df["game_date"] >= cut]
    print(f"{len(train)} train props (< {cut}), {len(test)} test props\n")

    def mae(w, d):
        blend = w * d["our_projection"] + (1 - w) * d["book_line"]
        return float((blend - d["actual"]).abs().mean())

    grid = np.linspace(0, 1, 21)

    print("=== weight on OUR projection, fitted per market ===")
    print(f"{'market':<18}{'best w':>8}{'train MAE':>11}{'test MAE':>10}"
          f"{'ours only':>11}{'line only':>11}{'improve':>9}")
    print("-" * 78)

    chosen = {}
    for market, g_tr in train.groupby("market_code"):
        g_te = test[test["market_code"] == market]
        if len(g_tr) < 40 or len(g_te) < 15:
            continue
        w = float(grid[int(np.argmin([mae(w, g_tr) for w in grid]))])
        chosen[market] = w
        ours, line, blended = mae(1.0, g_te), mae(0.0, g_te), mae(w, g_te)
        best_single = min(ours, line)
        print(f"{market:<18}{w:>8.2f}{mae(w, g_tr):>11.3f}{blended:>10.3f}"
              f"{ours:>11.3f}{line:>11.3f}{(best_single - blended) / best_single:>8.1%}")

    print("\n=== overall ===")
    w_all = float(grid[int(np.argmin([mae(w, train) for w in grid]))])
    print(f"single global weight on our projection: {w_all:.2f}")
    print(f"test MAE  blended {mae(w_all, test):.3f} | "
          f"ours only {mae(1.0, test):.3f} | line only {mae(0.0, test):.3f}")

    # Does anchoring fix the "further we disagree, the worse we are" pattern?
    print("\n=== beat-the-line rate by disagreement, before and after ===")
    t = test.copy()
    t["w"] = t["market_code"].map(chosen).fillna(w_all)
    t["blend"] = t["w"] * t["our_projection"] + (1 - t["w"]) * t["book_line"]
    t["blend_err"] = (t["blend"] - t["actual"]).abs()
    t["bucket"] = pd.qcut(
        (t["our_projection"] - t["book_line"]).abs(), 4,
        labels=["closest", "close", "far", "furthest"], duplicates="drop",
    )
    print(t.groupby("bucket", observed=True).agg(
        n=("our_err", "size"),
        before=("we_beat_book", "mean"),
        after=("blend_err", lambda s: float((s < t.loc[s.index, "book_err"]).mean())),
        our_mae=("our_err", "mean"),
        blend_mae=("blend_err", "mean"),
        book_mae=("book_err", "mean"),
    ).round(3).to_string())

    print("\nRecommended weights (1.0 = trust the model fully, 0.0 = repeat the line):")
    for m, w in sorted(chosen.items()):
        print(f"  {m:<18}{w:.2f}")


if __name__ == "__main__":
    main()
