"""Where the market is wrong about itself.

Every previous attempt to find an edge asked the same question: is our number
better than the book's number? Four different framings of that question all came
back no, and the honest reading is that a five-game rolling window cannot out-
forecast a market that prices thousands of these a week.

So stop asking it. There is a second, completely different question available in
the same data, and it needs no model at all: **do the books agree with each
other?** When DraftKings posts a receptions line at 4.5 and the other two books
have it at 5.5, one of those is stale or mispriced, and the disagreement is
visible before kickoff. Taking the under at 5.5, or the over at 4.5, is a bet
that the consensus is closer to the truth than the outlier is. That is a
market-internal inefficiency, not a forecasting claim.

Method, deliberately free of anything I could fool myself with:

  * The consensus is leave-one-out. A book is never compared against a number it
    helped set, which would drag the consensus toward the outlier and shrink the
    very signal being measured.
  * Each bet is graded at the outlier book's own line and its own price for the
    side taken. No cross-book price shopping, no grading an under at an over's
    price.
  * The control group is every prop where the books agree exactly. If the
    agreeing props also print a profit, the "edge" is just the sample, not the
    disagreement.
  * Confidence intervals bootstrap whole slates, because props on the same
    Sunday share weather and game scripts and are nowhere near independent.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from odds_markets import ALL_ODDS_TO_MARKET

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

# Historical snapshots still carry the two dead touchdown keys, so this uses
# the map that includes them. One definition, in odds_markets.py.
MARKET_MAP = ALL_ODDS_TO_MARKET


def load(engine) -> pd.DataFrame:
    """One row per book per prop: that book's line, its two prices, the result.

    Only the last snapshot before kickoff is used, so this measures closing
    disagreement rather than an opening line nobody could still bet.
    """
    q = text("""
        WITH latest AS (
            SELECT DISTINCT ON (provider_event_id, bookmaker_key, market_key,
                                player_name, outcome_name)
                   provider_event_id, bookmaker_key, market_key, player_name,
                   outcome_name, line, price_american, commence_time
            FROM odds_snapshots
            WHERE line IS NOT NULL
            ORDER BY provider_event_id, bookmaker_key, market_key, player_name,
                     outcome_name, observed_at DESC
        ),
        pergame AS (
            SELECT provider_event_id, bookmaker_key, market_key, player_name,
                   (commence_time AT TIME ZONE 'UTC')::date AS game_date,
                   MAX(line) FILTER (WHERE lower(outcome_name) = 'over') AS line,
                   MAX(price_american) FILTER (WHERE lower(outcome_name) = 'over')
                       AS over_am,
                   MAX(price_american) FILTER (WHERE lower(outcome_name) = 'under')
                       AS under_am
            FROM latest
            GROUP BY 1, 2, 3, 4, 5
        )
        SELECT * FROM pergame
        WHERE line IS NOT NULL AND over_am IS NOT NULL AND under_am IS NOT NULL
    """)
    df = pd.read_sql(q, engine)
    df["market_code"] = df["market_key"].map(MARKET_MAP)
    df = df[df["market_code"].notna()].copy()

    actuals = pd.read_sql(text("""
        SELECT f.player_id, f.as_of_game_date AS game_date, m.code AS market_code,
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

    out = df.merge(
        actuals[["key", "market_code", "game_date", "actual"]],
        on=["key", "market_code", "game_date"], how="inner",
    )
    return out


def dec(american):
    a = np.asarray(american, dtype=float)
    return np.where(a < 0, 1 + 100.0 / -a, 1 + a / 100.0)


def build(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the leave-one-out consensus and grade the outlier-side bet."""
    g = df.groupby(["provider_event_id", "market_code", "player_name"])["line"]
    df = df.assign(_n=g.transform("size"), _sum=g.transform("sum"))
    df = df[df["_n"] >= 3].copy()
    # Leave-one-out mean of the other books. With three books this is the mean
    # of the two the outlier did not set.
    df["consensus"] = (df["_sum"] - df["line"]) / (df["_n"] - 1)
    df["diff"] = df["line"] - df["consensus"]

    # The side the disagreement points at. A line above the market means the
    # under is available at a better number than the market thinks it is worth.
    df["side"] = np.where(df["diff"] > 0, "under",
                          np.where(df["diff"] < 0, "over", "none"))
    df["decimal"] = np.where(df["side"] == "under",
                             dec(df["under_am"]), dec(df["over_am"]))
    df["breakeven"] = 1.0 / df["decimal"]

    won = np.where(
        df["side"] == "under", df["actual"] < df["line"],
        np.where(df["side"] == "over", df["actual"] > df["line"], False),
    )
    push = (df["actual"] == df["line"]).to_numpy()
    df["hit"] = np.where(push, np.nan, won.astype(float))
    df["units"] = np.where(push, 0.0, np.where(won, df["decimal"] - 1.0, -1.0))

    # Line granularity differs wildly by market: half a reception is a large
    # disagreement, half a passing yard is nothing. Scale each gap by how much
    # the lines in that market typically vary.
    scale = df.groupby("market_code")["line"].transform(lambda s: s.std() or 1.0)
    df["z"] = df["diff"].abs() / scale.replace(0, np.nan)
    return df


def boot(frame, n_boot=2000, seed=11):
    f = frame.dropna(subset=["hit"])
    if f.empty:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    groups = [g for _, g in f.groupby("game_date")]
    out = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(groups), len(groups))
        s = pd.concat([groups[i] for i in idx], ignore_index=True)
        out.append(s["hit"].mean() - s["breakeven"].mean())
    return tuple(np.percentile(out, [2.5, 97.5]))


def row(frame, label, ci=False):
    f = frame.dropna(subset=["hit"])
    if len(f) == 0:
        return f"{label:<34}{0:>7}"
    hit, be = f["hit"].mean(), f["breakeven"].mean()
    s = (f"{label:<34}{len(f):>7}{hit:>8.3f}{be:>8.3f}{hit - be:>+9.3f}"
         f"{f['units'].mean():>+9.3f}")
    if ci:
        lo, hi = boot(f)
        s += f"   [{lo:+.3f}, {hi:+.3f}]" + ("  SIGNIFICANT" if lo > 0 else "")
    return s


HDR = f"{'':<34}{'n':>7}{'hit':>8}{'be':>8}{'edge':>9}{'ROI':>9}"


def main():
    engine = create_engine(DATABASE_URL, future=True)
    raw = load(engine)
    if raw.empty:
        raise SystemExit("no book lines matched to results")
    df = build(raw)
    props = df.groupby(["provider_event_id", "market_code", "player_name"]).ngroups
    print(f"{len(df)} book-lines on {props} props, "
          f"{df['game_date'].nunique()} slates, "
          f"{df['bookmaker_key'].nunique()} books\n")

    agree = df[df["diff"] == 0]
    disagree = df[df["diff"] != 0]
    print(f"books agree exactly on {len(agree) / len(df):.1%} of book-lines\n")

    print("=== CONTROL: props where every book posts the same line ===")
    print(HDR)
    # No side is indicated when the books agree, so both are shown. If either
    # prints a profit here the whole exercise is measuring something else.
    for s in ("over", "under"):
        a = agree.copy()
        a["decimal"] = dec(a["under_am"] if s == "under" else a["over_am"])
        a["breakeven"] = 1.0 / a["decimal"]
        w = (a["actual"] < a["line"]) if s == "under" else (a["actual"] > a["line"])
        push = (a["actual"] == a["line"]).to_numpy()
        a["hit"] = np.where(push, np.nan, w.to_numpy().astype(float))
        a["units"] = np.where(push, 0.0,
                              np.where(w, a["decimal"] - 1.0, -1.0))
        print(row(a, f"agreed line, blind {s}"))

    print("\n=== FOLLOW THE CONSENSUS AGAINST THE OUTLIER BOOK ===")
    print(HDR + f"{'95% CI (slate bootstrap)':>28}")
    print(row(disagree, "any disagreement", ci=True))
    for lo, hi, lab in [(0.0, 0.5, "small gap (z<0.5)"),
                        (0.5, 1.0, "medium gap (0.5-1)"),
                        (1.0, 99.0, "large gap (z>1)")]:
        sub = disagree[(disagree["z"] >= lo) & (disagree["z"] < hi)]
        if len(sub.dropna(subset=["hit"])) >= 100:
            print(row(sub, lab, ci=True))

    print("\n=== BY MARKET (any disagreement) ===")
    print(HDR)
    for m, sub in disagree.groupby("market_code"):
        if len(sub.dropna(subset=["hit"])) >= 100:
            print(row(sub, m))

    print("\n=== BY OUTLIER BOOK ===")
    print(HDR)
    for b, sub in disagree.groupby("bookmaker_key"):
        print(row(sub, b))

    print("\n=== WHICH DIRECTION THE OUTLIER IS WRONG ===")
    print(HDR)
    print(row(disagree[disagree["side"] == "under"], "outlier line too HIGH -> under"))
    print(row(disagree[disagree["side"] == "over"], "outlier line too LOW -> over"))

    print("\n=== SPLIT BY SEASON HALF (is it stable?) ===")
    print(HDR)
    slates = sorted(disagree["game_date"].unique())
    mid = slates[len(slates) // 2]
    print(row(disagree[disagree["game_date"] < mid], f"before {mid}"))
    print(row(disagree[disagree["game_date"] >= mid], f"{mid} onward"))


if __name__ == "__main__":
    main()
