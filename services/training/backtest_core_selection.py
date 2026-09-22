"""Which picks are worth publishing as bets? Chosen on old seasons, tested on new.

The board publishes about 140 picks a slate across ten market and side
combinations, and graded over four seasons only a couple of those combinations
make money. The rest average out to nothing and drag the published hit rate to
the coin flip a reader sees. Fewer picks, chosen where the edge has actually
held, is worth more than a full board that lands at 50%.

The trap is that the slices were found by looking, which is how a backtest
learns noise. So the rule is chosen using 2023 and 2024 only: every market,
side and line band with enough picks, ranked by return, and whatever clears the
bar is frozen. Then it is applied unchanged to 2025 and to the live 2026
season, neither of which had any say in choosing it.

Prints what the rule would have published, how often it hit, what it returned,
and how many picks a slate that leaves. A rule that wins on the seasons that
picked it and not on the ones that did not is a rule that learned noise, and
this is the report that says so.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)

# Line bands per market, in the market's own units. Coarse on purpose: the
# finer the grid, the more chances to find a pattern that is not there.
BANDS = {
    "recs": [(4.5, 99), (2.5, 4.5), (0, 2.5)],
    "rec_yds": [(60, 999), (40, 60), (25, 40), (0, 25)],
    "rush_yds": [(50, 999), (25, 50), (0, 25)],
    "rush_att": [(12, 99), (6, 12), (0, 6)],
    "pass_yds": [(240, 999), (0, 240)],
    "pass_att": [(0, 999)],
    "pass_completions": [(0, 999)],
    "pass_td": [(0, 999)],
}
CHOOSE_SEASONS = (2023, 2024)
MIN_PICKS_TO_CHOOSE = 40
MIN_ROI_TO_CHOOSE = 0.03


def load(engine):
    df = pd.read_sql(
        text(
            """
            SELECT game_date, market_code, recommended_side AS side, line,
                   edge_tier, hit, price_american
            FROM prop_edge_results
            WHERE hit IS NOT NULL AND price_american IS NOT NULL
              AND price_american <> 0 AND line IS NOT NULL
            """
        ),
        engine,
    )
    d = pd.to_datetime(df["game_date"])
    df["season"] = d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)
    df["won"] = df["hit"].astype(int)
    p = df["price_american"].astype(float)
    df["profit"] = np.where(df["hit"], np.where(p > 0, p / 100.0, 100.0 / -p), -1.0)
    df["band"] = [band_of(m, ln) for m, ln in zip(df["market_code"], df["line"])]
    return df.dropna(subset=["band"])


def band_of(market, line):
    for lo, hi in BANDS.get(market, []):
        if lo <= float(line) < hi:
            return f"{lo:g}-{hi:g}"
    return None


def summarise(g):
    return pd.Series({"n": len(g), "won": g["won"].mean(),
                      "roi": g["profit"].mean(), "units": g["profit"].sum()})


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = load(eng)
    key = ["market_code", "side", "band"]

    choose = df[df["season"].isin(CHOOSE_SEASONS)]
    table = choose.groupby(key).apply(summarise, include_groups=False).reset_index()
    picked = table[(table["n"] >= MIN_PICKS_TO_CHOOSE)
                   & (table["roi"] >= MIN_ROI_TO_CHOOSE)]

    print(f"chosen on {CHOOSE_SEASONS[0]} and {CHOOSE_SEASONS[1]}: "
          f"at least {MIN_PICKS_TO_CHOOSE} picks and {MIN_ROI_TO_CHOOSE:+.0%} return\n")
    if picked.empty:
        print("nothing clears the bar on the choosing seasons")
        return
    print(f"{'market':<18}{'side':<7}{'line band':<12}{'n':>5}{'won':>8}{'roi':>8}")
    for r in picked.itertuples(index=False):
        print(f"{r.market_code:<18}{r.side:<7}{r.band:<12}{int(r.n):>5}"
              f"{r.won:>8.1%}{r.roi:>+8.1%}")

    rule = set(zip(picked["market_code"], picked["side"], picked["band"]))
    df["in_rule"] = [tuple(x) in rule for x in zip(df["market_code"], df["side"], df["band"])]

    print("\napplied unchanged to the seasons that did not choose it:\n")
    print(f"{'season':<9}{'picks':>7}{'won':>8}{'roi':>8}{'units':>8}   "
          f"{'board as published':>22}")
    for season in sorted(df["season"].unique()):
        s = df[df["season"] == season]
        r, b = s[s["in_rule"]], s
        if not len(r):
            continue
        weeks = max(1, s["game_date"].nunique() / 3.0)
        mark = "  (chosen on this season)" if season in CHOOSE_SEASONS else ""
        print(f"{season:<9}{len(r):>7}{r['won'].mean():>8.1%}{r['profit'].mean():>+8.1%}"
              f"{r['profit'].sum():>+8.1f}   "
              f"{len(b):>6} picks {b['won'].mean():>6.1%} {b['profit'].mean():>+7.1%}"
              f"{mark}")
        if season not in CHOOSE_SEASONS:
            print(f"{'':9}{'':7}{'':8}{'':8}{'':8}   about {len(r) / weeks:.0f} picks a slate")


if __name__ == "__main__":
    main()
