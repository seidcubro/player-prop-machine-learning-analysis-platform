"""What would make medium a real tier?

The tiers are a promise about order: elite hits most, then strong, then
medium, and small is the coin flip. Replayed walk forward over nine weeks of
2026 the top of that holds and the middle does not:

    elite    257 picks   65.0%
    strong   257         56.8%
    medium   257         47.9%
    small  2,196         51.0%

Medium is the 40 to 60% band of the gap rank, and the gap stops carrying
anything at about 40%: the bands below it read 48.9%, 51.9% and 52.3%. So
medium is cut from the part of the ranking that has no information left in it,
and it lands wherever a coin lands. Removing the label would hide that. Fixing
it means choosing medium with something that still separates picks after the
gap has run out.

This tests four ways of doing that. Elite and strong are left exactly as the
board cuts them, because they work. Everything else, including the markets
with no ranking at all, is the pool medium is drawn from, and every candidate
draws the same number of picks each week that the board does today, so the
only thing that differs is which ones.

    as is     the 40 to 60% band of the gap rank. What the board does now.

    cell      the record of this market and side so far. Receptions overs have
              hit 60% and rushing overs 45%, and the gap rank knows neither.
              Each pick is scored by how often its market-and-side has been
              right in the weeks before, pulled toward a coin flip when the
              cell is thin, and medium is the top of that.

    price     what the book says. The picks whose own price implies the
              highest chance of landing. This is the book's opinion and not
              ours, so it is here as the bar the others have to clear and as
              the honest answer to "which picks are most likely right".

    model     a logistic regression on everything known before the week: the
              gap, the price, the side, the market and the market-by-side
              cell. Medium is the top of its predicted chance of being right.

Each rule sees only the backtest and the live weeks before the week it tiers.

What counts as fixed: medium above small on the season AND in most weeks, and
below strong. A candidate that only manages the first is one good month.

Read-only.

    docker compose run --rm training python research_medium_tier.py
"""

import os

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine

import build_prop_edges as bp
import replay_current_rules as rc

PRIOR_WEIGHT = float(os.getenv("PRIOR_WEIGHT", "50"))
CANDIDATES = ["as is", "cell", "price", "model"]


def prepare(d):
    d = d.copy()
    d["over"] = (d["p50"] > d["line"]).astype(float)
    d["cell"] = d["market_code"] + ":" + np.where(d["over"] == 1, "over", "under")
    d["need"] = d["needed"].fillna(0.524)
    return d


def cell_score(past, now):
    """Share right in this market and side so far, shrunk toward even."""
    g = past.groupby("cell")["hit"].agg(["sum", "count"])
    rate = (g["sum"] + 0.5 * PRIOR_WEIGHT) / (g["count"] + PRIOR_WEIGHT)
    return now["cell"].map(rate).fillna(0.5)


def design(frame, cells):
    x = pd.DataFrame({
        "gap": frame["gap"].clip(upper=3.0).to_numpy(),
        "need": frame["need"].to_numpy(),
        "over": frame["over"].to_numpy(),
    }, index=frame.index)
    for c in cells:
        x["c_" + c] = (frame["cell"] == c).astype(float)
    return x


def model_score(past, now):
    if len(past) < 300 or past["hit"].nunique() < 2:
        return pd.Series(0.5, index=now.index)
    cells = sorted(past["cell"].unique())
    fit = make_pipeline(StandardScaler(),
                        LogisticRegression(C=0.3, max_iter=2000))
    fit.fit(design(past, cells), past["hit"].astype(int))
    return pd.Series(fit.predict_proba(design(now, cells))[:, 1],
                     index=now.index)


def assign(d):
    """A tier column per candidate, walk forward. Split out to run on a fake."""
    live = d[d["source"] == "live"]
    back = d[d["source"] != "live"]
    weeks = sorted(w for w in live["week"].unique() if w > 0)
    out = {c: pd.Series("small", index=live.index, dtype=object)
           for c in CANDIDATES}
    for w in weeks:
        now = live[live["week"] == w]
        past_live = live[(live["week"] > 0) & (live["week"] < w)]
        past = pd.concat([back, past_live])
        base = rc.tier_week(now, rc.decide(past_live, back))
        top = base.isin(["elite", "strong"])
        pool = now[~top]
        n_medium = int((base == "medium").sum())
        scores = {
            "cell": cell_score(past_live if len(past_live) else past, pool),
            "price": pool["need"],
            "model": model_score(past, pool),
        }
        for c in CANDIDATES:
            t = base.copy()
            if c != "as is":
                t[t == "medium"] = "small"
                pick = (scores[c].sort_values(ascending=False, kind="mergesort")
                        .index[:n_medium])
                t.loc[pick] = "medium"
            out[c].loc[now.index] = t
    return live, out, weeks


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = prepare(rc.load(eng))
    live, out, weeks = assign(d)
    if not weeks:
        raise SystemExit("no live weeks to replay")
    print(f"{len(live[live['week'] > 0]):,} live graded props, weeks "
          f"{weeks[0]} to {weeks[-1]}. Elite and strong are the same picks in "
          f"every row below.")
    print()

    summary = []
    for c in CANDIDATES:
        live = live.assign(tier=out[c])
        print(f"MEDIUM CHOSEN BY: {c}")
        print(f"  {'week':<6}{'strong':>16}{'medium':>16}{'small':>16}"
              f"   medium over small?")
        above = below = scored = under_strong = 0
        for w in weeks:
            g = live[live["week"] == w]
            cell = {t: g[g["tier"] == t]["hit"] for t in ("strong", "medium", "small")}
            row = f"  {w:<6}" + "".join(
                (f"{cell[t].mean():>9.1%} ({len(cell[t]):>3})" if len(cell[t])
                 else f"{'-':>16}") for t in ("strong", "medium", "small"))
            mark = ""
            if len(cell["medium"]) >= 10 and len(cell["small"]) >= 10:
                scored += 1
                if cell["medium"].mean() > cell["small"].mean():
                    above += 1
                    mark = "yes"
                else:
                    below += 1
                    mark = "no"
                if len(cell["strong"]) >= 10 and (cell["medium"].mean()
                                                  <= cell["strong"].mean()):
                    under_strong += 1
            print(row + f"   {mark}")
        print(f"    {'tier':<8}{'picks':>6}{'hit':>9}{'needed':>9}"
              f"{'hit - needed':>22}")
        rates = {}
        for t in rc.TIERS:
            s = live[(live["tier"] == t) & (live["week"] > 0)]
            rates[t] = float(s["hit"].mean()) if len(s) else float("nan")
            if len(s) >= 30:
                print("    " + rc.edge_line(t, s))
        m = live[(live["tier"] == "medium") & (live["week"] > 0)]
        if len(m):
            mix = (m["cell"].value_counts(normalize=True).head(4))
            print("    medium is mostly: " + ", ".join(
                f"{k} {v:.0%}" for k, v in mix.items()))
        print()
        summary.append((c, rates, above, scored, under_strong))

    print("THE QUESTION: is medium between strong and small?")
    print(f"  {'chosen by':<10}{'strong':>9}{'medium':>9}{'small':>9}"
          f"{'medium - small':>16}   weeks medium over small   ordered?")
    for c, r, above, scored, under_strong in summary:
        ordered = (r["strong"] >= r["medium"] > r["small"]
                   and scored and above > scored / 2)
        print(f"  {c:<10}{r['strong']:>9.1%}{r['medium']:>9.1%}"
              f"{r['small']:>9.1%}{r['medium'] - r['small']:>+16.1%}"
              f"   {above} of {scored:<21}{'yes' if ordered else 'no'}")
    print()
    print("ordered means strong at or above medium, medium above small on the "
          "season, and medium above small in more than half the weeks.")


if __name__ == "__main__":
    main()
