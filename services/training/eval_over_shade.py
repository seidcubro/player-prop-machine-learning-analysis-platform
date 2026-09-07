"""Is the over-shade structural, or was it one good season?

After the walk-forward result, this is the only candidate edge left standing. The
model adds nothing; what remains is a property of the market itself, measured on
props where all three books post the identical line:

    blind over    hit 0.460   break-even 0.521   edge -6.1%
    blind under   hit 0.540   break-even 0.542   edge -0.2%

Vig is symmetric by construction, so a six-point gap between the two sides of the
same market is the books shading the number toward the side the public buys.

That was 2025. One season is a story, not a finding, and `under_asymmetry.py`
already showed the top-quartile version flipping sign between the two halves of
that season (+6.4% then -0.5%). So this runs the same measurement season by
season across every year of odds history, with no model involved anywhere.

Three things have to hold before any of it is worth acting on:

  1. **The sign is the same every season.** A structural feature of how books
     price a recreational market does not switch direction year to year.
  2. **The line-tier gradient is monotone every season.** The claim is that
     shading tracks public attention, so it should be strongest on the biggest
     names and absent or reversed on the smallest. A gradient that only appears
     in aggregate is a red flag for a pooled-sample artifact.
  3. **It survives at real prices.** Edge measured against break-even at the
     best of the three books, and bootstrapped over whole slates, since props
     from the same Sunday share weather and game scripts.

Nothing here uses a projection, a feature row or a trained model, which is the
point: if the effect is real it should be visible without any of them.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from market_disagreement import DATABASE_URL, MARKET_MAP, dec

MIN_BOOKS = int(os.getenv("MIN_BOOKS", "2"))


def load(engine) -> pd.DataFrame:
    """Every priced prop with a result, across every season of odds history."""
    df = pd.read_sql(text("""
        WITH latest AS (
            SELECT DISTINCT ON (provider_event_id, bookmaker_key, market_key,
                                player_name, outcome_name)
                   provider_event_id, bookmaker_key, market_key, player_name,
                   outcome_name, line, price_american, commence_time
            FROM odds_snapshots
            WHERE line IS NOT NULL
            ORDER BY provider_event_id, bookmaker_key, market_key, player_name,
                     outcome_name, observed_at DESC
        )
        SELECT provider_event_id, bookmaker_key, market_key, player_name,
               (commence_time AT TIME ZONE 'UTC')::date AS game_date,
               MAX(line) FILTER (WHERE lower(outcome_name) = 'over') AS line,
               MAX(price_american) FILTER (WHERE lower(outcome_name) = 'over')
                   AS over_am,
               MAX(price_american) FILTER (WHERE lower(outcome_name) = 'under')
                   AS under_am
        FROM latest
        GROUP BY 1, 2, 3, 4, 5
    """), engine)
    df["market_code"] = df["market_key"].map(MARKET_MAP)
    df = df[df["market_code"].notna()].copy()
    df = df.dropna(subset=["line", "over_am", "under_am"])

    actuals = pd.read_sql(text("""
        SELECT f.as_of_game_date AS game_date, m.code AS market_code,
               f.label_actual AS actual, p.name AS player_name
        FROM player_market_features f
        JOIN prop_markets m ON m.id = f.market_id
        JOIN players p ON p.external_id = f.player_id
        WHERE f.lookback = 5 AND f.label_actual IS NOT NULL
    """), engine)

    def key(s):
        return s.str.lower().str.replace(r"[.\-']", "", regex=True).str.strip()

    df["key"] = key(df["player_name"])
    actuals["key"] = key(actuals["player_name"])
    actuals = actuals.drop_duplicates(subset=["key", "market_code", "game_date"])
    return df.merge(actuals[["key", "market_code", "game_date", "actual"]],
                    on=["key", "market_code", "game_date"], how="inner")


def to_props(df: pd.DataFrame) -> pd.DataFrame:
    """One row per prop, holding the best price available on each side.

    Best price is taken per side independently, at the line the offering book
    posted, because the generous book on the over is rarely the generous one on
    the under.
    """
    df = df.copy()
    df["over_dec"] = dec(df["over_am"])
    df["under_dec"] = dec(df["under_am"])
    keys = ["provider_event_id", "market_code", "player_name", "game_date"]
    g = df.groupby(keys, as_index=False)
    props = g.agg(books=("bookmaker_key", "nunique"),
                  line=("line", "median"),
                  actual=("actual", "first"))
    for side in ("over", "under"):
        best = df.loc[df.groupby(keys)[f"{side}_dec"].idxmax()]
        props = props.merge(
            best[keys + ["line", f"{side}_dec"]].rename(columns={
                "line": f"{side}_line", f"{side}_dec": f"{side}_best"}),
            on=keys, how="left")
    props = props[props["books"] >= MIN_BOOKS].copy()
    d = pd.to_datetime(props["game_date"])
    # NFL seasons straddle the new year, so anything before March belongs to the
    # previous season.
    props["season"] = d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)
    return props


def grade(props: pd.DataFrame, side: str) -> pd.DataFrame:
    line = props[f"{side}_line"]
    d = props[f"{side}_best"]
    won = (props["actual"] < line) if side == "under" else (props["actual"] > line)
    push = props["actual"] == line
    out = props.copy()
    out["decimal"] = d
    out["breakeven"] = 1.0 / d
    out["hit"] = np.where(push, np.nan, won.astype(float))
    out["units"] = np.where(push, 0.0, np.where(won, d - 1.0, -1.0))
    return out.dropna(subset=["hit", "decimal"])


def boot(f, n_boot=2000, seed=23):
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


HDR = f"{'':<32}{'n':>6}{'hit':>8}{'be':>8}{'edge':>9}{'ROI':>9}"


def row(f, label, ci=False):
    if len(f) == 0:
        return f"{label:<32}{0:>6}"
    hit, be = f["hit"].mean(), f["breakeven"].mean()
    s = (f"{label:<32}{len(f):>6}{hit:>8.3f}{be:>8.3f}{hit - be:>+9.3f}"
         f"{f['units'].mean():>+9.3f}")
    if ci:
        lo, hi = boot(f)
        s += f"   [{lo:+.3f}, {hi:+.3f}]" + ("  CLEARS ZERO" if lo > 0 else "")
    return s


def main():
    engine = create_engine(DATABASE_URL, future=True)
    props = to_props(load(engine))
    if props.empty:
        raise SystemExit("no priced props matched to results")
    seasons = sorted(props["season"].unique())
    print(f"{len(props)} priced props, {props['game_date'].nunique()} slates, "
          f"seasons {seasons}")
    print()

    print("=== TEST 1: is the sign the same every season? ===")
    print(HDR + f"{'95% CI on edge':>26}")
    for s in seasons:
        sub = props[props["season"] == s]
        print(row(grade(sub, "over"), f"{s} blind over"))
        print(row(grade(sub, "under"), f"{s} blind under", ci=True))
    print(row(grade(props, "over"), "all seasons, over"))
    print(row(grade(props, "under"), "all seasons, under", ci=True))

    print()
    print("=== TEST 2: is the line-tier gradient monotone every season? ===")
    print("The claim is that shading tracks public attention, so the under")
    print("should pay best on the biggest lines and worst on the smallest.")
    print()
    tiers = [(0.75, 1.01, "top quartile"), (0.50, 0.75, "third"),
             (0.25, 0.50, "second"), (0.00, 0.25, "bottom quartile")]
    # Ranked within season AND market: a 90-yard line is a star in receiving and
    # does not exist in receptions, so a pooled rank would just sort by market.
    q = props.groupby(["season", "market_code"])["line"].rank(pct=True)
    print(f"{'season':<10}" + "".join(f"{lab:>18}" for _, _, lab in tiers))
    gradients = []
    for s in seasons:
        cells, edges = [], []
        for lo, hi, _ in tiers:
            sub = props[(props["season"] == s) & (q >= lo) & (q < hi)]
            g = grade(sub, "under")
            if len(g) < 40:
                cells.append(f"{'-':>18}")
                edges.append(np.nan)
                continue
            e = g["hit"].mean() - g["breakeven"].mean()
            edges.append(e)
            cells.append(f"{e:>+11.3f} (n{len(g):>3})")
        print(f"{s:<10}" + "".join(cells))
        clean = [e for e in edges if not np.isnan(e)]
        # Tiers are printed high to low, so a monotone decrease is the shape the
        # hypothesis predicts.
        gradients.append(len(clean) >= 3 and
                         all(a >= b for a, b in zip(clean, clean[1:])))
    print()
    print(f"seasons with a monotone gradient: {sum(gradients)} of {len(gradients)}")

    print()
    print("=== TEST 3: top-quartile unders, season by season, at best price ===")
    print(HDR + f"{'95% CI on edge':>26}")
    top = props[q >= 0.75]
    for s in seasons:
        print(row(grade(top[top["season"] == s], "under"), f"{s}", ci=True))
    print(row(grade(top, "under"), "pooled", ci=True))

    print()
    print("=== TEST 4: by market, pooled across seasons ===")
    print(HDR + f"{'95% CI on edge':>26}")
    for mk, sub in top.groupby("market_code"):
        g = grade(sub, "under")
        if len(g) >= 120:
            print(row(g, mk, ci=True))

    print()
    print("=== VERDICT ===")
    unders = [grade(props[props["season"] == s], "under") for s in seasons]
    edges = [g["hit"].mean() - g["breakeven"].mean() for g in unders if len(g)]
    overs = [grade(props[props["season"] == s], "over") for s in seasons]
    oedges = [g["hit"].mean() - g["breakeven"].mean() for g in overs if len(g)]
    print(f"under edge by season: {[round(e, 3) for e in edges]}")
    print(f"over  edge by season: {[round(e, 3) for e in oedges]}")
    if all(o < -0.01 for o in oedges) and len(oedges) >= 2:
        print("The over is negative in every season, which is the asymmetry.")
    else:
        print("The over is NOT consistently negative; the asymmetry is not"
              " established.")
    if sum(gradients) == len(gradients) and len(gradients) >= 2:
        print("The tier gradient holds every season: consistent with shading"
              " that tracks public attention.")
    else:
        print("The tier gradient does NOT hold every season, so the top-tier"
              " result should be treated as unproven.")


if __name__ == "__main__":
    main()
