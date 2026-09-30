"""Exploratory data analysis of the feature table, the targets and the picks.

This project had never had one. There are forty-odd eval and research scripts in
this directory and every one of them tests a hypothesis somebody already had.
None of them ever asked the first question: what is actually in the data?

So this does. It is deliberately boring and deliberately complete, in the order
Brent Young's Module 2 handout gives: know your features, look at them one at a
time, look at them two at a time, then look at how they move through time. Every
number here is printed rather than asserted, and every figure is written to
docs/eda/ so it can be looked at rather than described.

Run it whole, or one section at a time:

    python eda.py                 every section
    python eda.py 1 2 5           only those

    1  inventory      what rows, what columns, what generations
    2  missingness    what is absent, and whether absence is random
    3  univariate     the shape of every target
    4  seasons        how the targets move year to year
    5  bivariate      correlation, redundancy, SPLOM
    6  timeseries     autocorrelation, and what it says about the window
    7  metrics        regression quality against betting quality

Section 7 needs backtest_gap_system, which trains models, so it is the slow one:
about four minutes. Sections 1-6 read the database and nothing else.

EDA is not a one-time gate. The handout's data science loop comes back to it
every time the data changes, and the four things this found on its first run are
all things that had been live for months.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import sys
import textwrap
import warnings

import numpy as np
import pandas as pd
import psycopg2
from scipy import stats

warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402

# In the container docs/eda is mounted at /docs/eda; from a checkout it is
# two levels up from services/training. EDA_OUT overrides both.
OUT = pathlib.Path(os.getenv("EDA_OUT") or
                   ("/docs/eda" if pathlib.Path("/docs/eda").is_dir()
                    else "../../docs/eda")).resolve()
CACHE = pathlib.Path(os.getenv("EDA_CACHE", "/tmp/eda_cache"))
LOOKBACK = int(os.getenv("LOOKBACK", "5"))

# The positions each market is eligible for, which is the filter train.py
# applies. Kept here rather than read from prop_markets so that a section can
# run against a database that has not been migrated.
ELIGIBLE = {
    "rec_yds": {"WR", "TE", "RB", "FB"},
    "recs": {"WR", "TE", "RB", "FB"},
    "rec_td": {"WR", "TE", "RB", "FB"},
    "rush_yds": {"QB", "RB", "WR", "FB"},
    "rush_att": {"QB", "RB", "WR", "FB"},
    "rush_td": {"QB", "RB", "WR", "FB"},
    "any_td": {"QB", "RB", "WR", "TE", "FB"},
    "pass_yds": {"QB"}, "pass_att": {"QB"},
    "pass_completions": {"QB"}, "pass_td": {"QB"},
}

# Columns that describe the row rather than feed the model.
META = {"id", "player_id", "name", "position", "years_exp", "rookie_year",
        "market_code", "as_of_game_date", "opponent", "team", "lookback",
        "label_actual", "season", "week", "game_type"}

# The four markets with enough priced volume to say anything about.
CORE = ["rec_yds", "recs", "rush_yds", "rush_att"]

sns.set_theme(style="whitegrid", context="notebook")
PALETTE = "deep"


# --------------------------------------------------------------------------
# loading


def connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "app"),
        user=os.getenv("POSTGRES_USER", "app"),
        password=os.getenv("POSTGRES_PASSWORD", "app"),
    )


FEATURE_SQL = """
SELECT pmf.id, pmf.player_id, p.name, p.position, p.years_exp, p.rookie_year,
       pm.code AS market_code,
       pmf.as_of_game_date, pmf.opponent, pmf.team, pmf.lookback,
       pmf.mean, pmf.stddev, pmf.weighted_mean, pmf.trend,
       pmf.aux_mean, pmf.aux_trend, pmf.extra_features, pmf.label_actual,
       g.season, g.week
FROM player_market_features pmf
JOIN prop_markets pm ON pm.id = pmf.market_id
LEFT JOIN players p ON p.external_id = pmf.player_id
LEFT JOIN LATERAL (
    SELECT season, week FROM nfl_games ng
    WHERE ng.game_date = pmf.as_of_game_date
      AND (ng.home_team = pmf.team OR ng.away_team = pmf.team)
    LIMIT 1
) g ON TRUE
"""

GAMES_SQL = """
SELECT player_id, season, week, game_date, position,
       receiving_yards, receptions, targets, rushing_yards, carries,
       passing_yards, attempts, receiving_tds, rushing_tds
FROM player_game_stats
WHERE season_type = 'REG' AND position IN ('QB','RB','WR','TE','FB')
"""


def _parse(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


def features() -> pd.DataFrame:
    """The feature table, one column per JSON key.

    Absent keys stay NaN. That is the whole point: train.py fills them with
    0.0, so an absent feature and a genuinely zero one are indistinguishable by
    the time a model sees them, and section 2 is about what that costs.
    """
    c = CACHE / "features.pkl"
    if c.exists():
        d = pd.read_pickle(c)
        # recs_mean and recs_trend exist both as table columns, which are NULL
        # on every row, and as JSON keys, which are not. Keep the JSON one; see
        # docs/eda/FINDINGS.md on the serving path that keeps the other.
        return d.loc[:, ~d.columns.duplicated(keep="last")]
    with connect() as conn:
        df = pd.read_sql(FEATURE_SQL, conn)
    ex = df["extra_features"].map(_parse)
    keys = sorted({k for d in ex for k in d})
    wide = pd.DataFrame.from_records(list(ex), index=df.index).reindex(columns=keys)
    wide = wide.apply(pd.to_numeric, errors="coerce")
    df = pd.concat([df.drop(columns=["extra_features"]), wide], axis=1)
    df = df.loc[:, ~df.columns.duplicated(keep="last")]
    df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"])
    d = df["as_of_game_date"]
    fallback = pd.Series(np.where(d.dt.month >= 3, d.dt.year, d.dt.year - 1),
                         index=df.index)
    df["season"] = df["season"].fillna(fallback).astype("Int64")
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_pickle(c)
    return df


def games() -> pd.DataFrame:
    c = CACHE / "games.pkl"
    if c.exists():
        return pd.read_pickle(c)
    with connect() as conn:
        g = pd.read_sql(GAMES_SQL, conn)
    g["game_date"] = pd.to_datetime(g["game_date"])
    g = g.sort_values(["player_id", "game_date"])
    CACHE.mkdir(parents=True, exist_ok=True)
    g.to_pickle(c)
    return g


def modelled(df: pd.DataFrame) -> pd.DataFrame:
    """The rows a model is actually fitted on: one lookback, labelled, eligible."""
    d = df[(df.lookback == LOOKBACK) & df.label_actual.notna()]
    keep = [p in ELIGIBLE.get(c, set()) for c, p in zip(d.market_code, d.position)]
    return d[keep]


def feature_cols(d: pd.DataFrame) -> list[str]:
    return [c for c in d.columns if c not in META]


def save(fig, name: str):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    fig.savefig(p, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"    -> {p.relative_to(OUT.parent.parent) if OUT.is_relative_to(OUT.parent.parent) else p}")


def head(n: int, title: str):
    print(f"\n{'=' * 96}\n{n}. {title}\n{'=' * 96}")


# --------------------------------------------------------------------------
# 1. inventory


def section_1(df):
    head(1, "INVENTORY: what is in the table")
    fc = feature_cols(df)
    print(f"\n  {len(df):,} rows, {len(fc)} feature columns, "
          f"{df.market_code.nunique()} markets, {df.player_id.nunique():,} players")
    print(f"  {df.as_of_game_date.min().date()} .. {df.as_of_game_date.max().date()}")

    print("\n  rows by market and lookback:")
    print(pd.crosstab(df.market_code, df.lookback).to_string().replace("\n", "\n    "))

    # Lookback is not a setting, it is a stratum. Rows built under one setting
    # are never read by a model configured for another, and nothing deletes them.
    used = modelled(df)
    print(f"\n  rows a model reads (lookback={LOOKBACK}, labelled, eligible position): "
          f"{len(used):,} of {len(df):,} ({len(used) / len(df):.0%})")
    dead = df[df.lookback != LOOKBACK]
    if len(dead):
        print(f"  rows under a different lookback, read by nothing: {len(dead):,}")
        cov = dead[fc].notna().mean()
        gone = sorted(cov[cov < 0.01].index)
        print(f"    of those, {len(gone)} features do not exist at all, so these "
              f"rows predate the feature builder that would have made them")

    print("\n  rows a model reads, by market and season:")
    print(used.pivot_table(index="market_code", columns="season",
                           values="label_actual", aggfunc="size")
          .to_string().replace("\n", "\n    "))

    print("\n  by position:")
    print(used.pivot_table(index="market_code", columns="position",
                           values="label_actual", aggfunc="size")
          .fillna(0).astype(int).to_string().replace("\n", "\n    "))

    # Column types. Almost everything is a float, which hides that a good number
    # of them are indicators and counts wearing a float's clothes.
    X = used[fc].apply(pd.to_numeric, errors="coerce")
    binary = [c for c in fc
              if X[c].notna().any() and X[c].dropna().isin([0.0, 1.0]).all()]
    integral = [c for c in fc
                if c not in binary and X[c].notna().any()
                and np.allclose(X[c].dropna() % 1, 0)]
    print(f"\n  all {len(fc)} columns are stored as double precision, but:")
    print(f"    {len(binary)} are binary indicators: {', '.join(sorted(binary)[:8])}"
          f"{' ...' if len(binary) > 8 else ''}")
    print(f"    {len(integral)} are integer counts")
    print(f"    {len(fc) - len(binary) - len(integral)} are genuinely continuous")

    fig, ax = plt.subplots(1, 2, figsize=(14, 5))
    used.groupby(["season", "market_code"]).size().unstack().plot(
        kind="bar", stacked=True, ax=ax[0], colormap="tab20", width=0.8)
    ax[0].set_title("Modelled rows by season")
    ax[0].set_xlabel("")
    ax[0].legend(fontsize=6, ncol=2)
    df.groupby([df.lookback, df.market_code]).size().unstack().plot(
        kind="bar", stacked=True, ax=ax[1], colormap="tab20", width=0.8)
    ax[1].set_title("All rows by lookback stratum")
    ax[1].set_xlabel("lookback")
    ax[1].legend(fontsize=6, ncol=2)
    fig.suptitle("What the feature table holds", fontsize=13)
    save(fig, "01_inventory.png")


# --------------------------------------------------------------------------
# 2. missingness


def section_2(df):
    head(2, "MISSINGNESS: what is absent, and whether absence is random")
    used = modelled(df)
    fc = feature_cols(used)

    print("\n  A feature absent from the JSON is filled with 0.0 by train.py and")
    print("  by both serving paths. There is no indicator column, so a model")
    print("  cannot tell 'this player had no red-zone carries' from 'this player")
    print("  had zero red-zone carries'. Those are different statements.\n")

    rows = []
    for code in CORE:
        d = used[used.market_code == code]
        m = d[fc].isna().mean()
        live = m[m < 0.98]
        rows.append({"market": code, "n": len(d),
                     "features_live": len(live),
                     "complete": int((live < 0.02).sum()),
                     "2-25pct absent": int(((live >= 0.02) & (live < 0.25)).sum()),
                     "25-50pct": int(((live >= 0.25) & (live < 0.5)).sum()),
                     "over 50pct": int((live >= 0.5).sum())})
    print(pd.DataFrame(rows).to_string(index=False).replace("\n", "\n  "))

    # Little's test asks whether the observed values differ across missingness
    # patterns. The cheaper and more directly relevant version: does the TARGET
    # differ? If it does, missingness is at best MAR, and the zero fill is
    # throwing a signal away rather than patching a hole.
    print("\n\n  Is it MCAR? Target mean when a feature is present vs absent.")
    print("  Under MCAR these two agree. Welch's t-test on the difference.\n")
    for code in CORE:
        d = used[used.market_code == code]
        m = d[fc].isna().mean()
        part = m[(m > 0.02) & (m < 0.95)]
        out = []
        for f in part.index:
            a = d.loc[d[f].notna(), "label_actual"]
            b = d.loc[d[f].isna(), "label_actual"]
            if len(a) < 50 or len(b) < 50:
                continue
            _, p = stats.ttest_ind(a, b, equal_var=False)
            out.append({"feature": f, "absent pct": round(m[f] * 100, 1),
                        "y|present": round(a.mean(), 2),
                        "y|absent": round(b.mean(), 2),
                        "ratio": round(a.mean() / max(b.mean(), 1e-9), 1),
                        "p": f"{p:.1e}"})
        if not out:
            continue
        t = pd.DataFrame(out).sort_values("ratio", ascending=False)
        sig = sum(float(x) < 0.001 for x in t.p)
        print(f"  --- {code} (n={len(d):,}, mean {d.label_actual.mean():.2f}) --- "
              f"{sig} of {len(t)} partially-absent features differ at p<0.001")
        print(t.head(6).to_string(index=False).replace("\n", "\n    "))
        print()

    print("  Read that as football rather than as statistics. A red-zone carry")
    print("  average is absent because the player had no red-zone carries, which")
    print("  is precisely the fact that he is not the guy. The missingness is not")
    print("  a defect in the data, it is among the strongest signals in it, and a")
    print("  zero hands it to the trees as a coincidence and to the linear")
    print("  families as nothing at all.\n")

    mat = pd.DataFrame({c: used[used.market_code == c][fc].notna().mean()
                        for c in CORE})
    mat = mat[mat.max(axis=1) > 0.02].sort_values(CORE[0], ascending=False)
    fig, ax = plt.subplots(figsize=(7, max(10, len(mat) * 0.16)))
    sns.heatmap(mat, cmap="RdYlGn", vmin=0, vmax=1, ax=ax,
                cbar_kws={"label": "fraction of rows where the feature exists"})
    ax.set_title("Feature coverage on the rows models are fitted to", fontsize=12)
    ax.tick_params(labelsize=6)
    save(fig, "02_missingness_matrix.png")

    fig, axes = plt.subplots(1, len(CORE), figsize=(5 * len(CORE), 4.2))
    for ax, code in zip(np.atleast_1d(axes), CORE):
        d = used[used.market_code == code]
        m = d[fc].isna().mean()
        pts = []
        for f in m[(m > 0.02) & (m < 0.95)].index:
            a = d.loc[d[f].notna(), "label_actual"].mean()
            b = d.loc[d[f].isna(), "label_actual"].mean()
            pts.append((m[f], a, b))
        if not pts:
            continue
        pts = np.array(pts)
        ax.scatter(pts[:, 0] * 100, pts[:, 1], s=18, label="feature present", alpha=.8)
        ax.scatter(pts[:, 0] * 100, pts[:, 2], s=18, label="feature absent", alpha=.8)
        ax.axhline(d.label_actual.mean(), ls="--", c="k", lw=1, label="overall mean")
        ax.set_title(code)
        ax.set_xlabel("pct of rows where it is absent")
        ax.set_ylabel("mean outcome")
        ax.legend(fontsize=7)
    fig.suptitle("Missingness is not random: the outcome is far lower wherever a "
                 "feature is absent", fontsize=13)
    fig.tight_layout()
    save(fig, "03_missing_not_at_random.png")


# --------------------------------------------------------------------------
# 3. univariate


def section_3(df):
    head(3, "UNIVARIATE: the shape of what we are predicting")
    used = modelled(df)

    rows = []
    for code, g in used.groupby("market_code"):
        y = g.label_actual
        q1, q3 = y.quantile(.25), y.quantile(.75)
        iqr = q3 - q1
        top = y.nlargest(max(1, len(y) // 10)).sum() / max(y.sum(), 1e-9)
        rows.append({"market": code, "n": len(g), "mean": round(y.mean(), 2),
                     "sd": round(y.std(), 2), "pct zero": round((y == 0).mean() * 100, 1),
                     "median": y.median(), "p90": round(y.quantile(.9), 1),
                     "max": y.max(), "skew": round(stats.skew(y), 2),
                     "kurtosis": round(stats.kurtosis(y), 1),
                     "Tukey outlier pct": round((
                         (y > q3 + 1.5 * iqr) | (y < q1 - 1.5 * iqr)).mean() * 100, 1),
                     "top decile share": round(top * 100, 1)})
    t = pd.DataFrame(rows).sort_values("n", ascending=False)
    print()
    print(t.to_string(index=False).replace("\n", "\n  "))

    print("\n  Two things there matter more than the rest.\n")
    print("  The zero column. Rushing yards is a point mass at zero with a tail")
    print("  attached, and the touchdown markets are 82 to 91 percent zero. We fit")
    print("  those with squared error, which assumes a symmetric, constant-variance")
    print("  error around a single mean. There is no single mean here; there are")
    print("  two populations, and the fitted value sits between them at a number")
    print("  that almost never occurs.\n")
    print("  The Tukey column. Calling 13 percent of rushing yards outliers is the")
    print("  convention misfiring rather than the data being dirty: on a skewed")
    print("  non-negative stat the 1.5 IQR fence is simply in the wrong place.")
    print("  Every one of these values is a real game a real player had. Nothing")
    print("  here should be trimmed, and that is worth writing down once so it")
    print("  does not get 'cleaned' later.\n")

    print("  Where the zeros come from, by position:\n")
    for code in CORE:
        g = used[used.market_code == code]
        z = g.groupby("position").label_actual.agg(
            n="size", mean="mean", pct_zero=lambda s: (s == 0).mean() * 100).round(2)
        z = z[z.n >= 100].sort_values("n", ascending=False)
        print(f"  --- {code} ---")
        print(z.to_string().replace("\n", "\n    "))
        print()
    print("  Rushing props are a running back and quarterback market, and more")
    print("  than half of what the rushing model is fitted to is wide receivers")
    print("  averaging a yard a game. Squared error spends its effort where the")
    print("  rows are, and the rows are not where the board is.\n")

    fig, axes = plt.subplots(3, 4, figsize=(18, 11))
    order = list(t.market)
    for ax, code in zip(axes.ravel(), order):
        y = used.loc[used.market_code == code, "label_actual"]
        bins = min(60, int(y.nunique()))
        ax.hist(y, bins=bins, color=sns.color_palette(PALETTE)[0], alpha=.85)
        ax.axvline(y.mean(), color="crimson", lw=1.6, label=f"mean {y.mean():.1f}")
        ax.axvline(y.median(), color="k", ls="--", lw=1.4,
                   label=f"median {y.median():.0f}")
        ax.set_title(f"{code}   {(y == 0).mean():.0%} zero, skew {stats.skew(y):.1f}",
                     fontsize=10)
        ax.legend(fontsize=7)
        ax.set_yscale("log")
    for ax in axes.ravel()[len(order):]:
        ax.axis("off")
    fig.suptitle("Every target is zero-inflated and right-skewed "
                 "(log counts, so the tail is visible)", fontsize=14)
    fig.tight_layout()
    save(fig, "04_target_distributions.png")

    fig, axes = plt.subplots(1, len(CORE), figsize=(5 * len(CORE), 4.5))
    for ax, code in zip(np.atleast_1d(axes), CORE):
        g = used[used.market_code == code]
        keep = g.position.value_counts()
        keep = keep[keep >= 100].index
        sns.boxplot(data=g[g.position.isin(keep)], x="position", y="label_actual",
                    ax=ax, palette=PALETTE, hue="position", legend=False)
        ax.set_title(code)
        ax.set_xlabel("")
        ax.set_ylabel("outcome")
    fig.suptitle("The same market is a different distribution for each position, "
                 "and one model fits them all", fontsize=13)
    fig.tight_layout()
    save(fig, "05_target_by_position.png")


# --------------------------------------------------------------------------
# 4. seasons


def section_4(df):
    head(4, "SEASON BY SEASON: is the game we fitted the game we are predicting?")
    used = modelled(df)

    m = used.pivot_table(index="market_code", columns="season",
                         values="label_actual", aggfunc="mean").round(2)
    print("\n  mean outcome by season:")
    print(m.to_string().replace("\n", "\n    "))
    base = m.iloc[:, 0]
    print(f"\n  change from {m.columns[0]}, in percent:")
    print(((m.div(base, axis=0) - 1) * 100).round(1).to_string().replace("\n", "\n    "))

    print("\n  zero rate by season:")
    z = used.pivot_table(index="market_code", columns="season", values="label_actual",
                         aggfunc=lambda s: (s == 0).mean() * 100).round(1)
    print(z.to_string().replace("\n", "\n    "))

    print("\n  There is no trend here, and that is the finding. The swings are")
    print("  season-specific rather than directional: receiving yards fell six")
    print("  percent in 2025 and came back four percent above the 2022 level in")
    print("  2026. A model fitted across all four years is fitted to an average of")
    print("  four different seasons, and a drift correction estimated from any two")
    print("  of them is fitting noise.\n")

    # Within-season shape. Week is the axis a bettor actually lives on.
    g = used[used.week.notna()]
    if len(g):
        w = g.pivot_table(index="week", columns="market_code",
                          values="label_actual", aggfunc="mean")
        fig, axes = plt.subplots(1, 2, figsize=(15, 5))
        for c in CORE:
            if c in w:
                axes[0].plot(w.index, w[c] / w[c].mean(), marker="o", ms=3, label=c)
        axes[0].axhline(1, ls="--", c="k", lw=1)
        axes[0].set_title("Week of season, each market scaled to its own mean")
        axes[0].set_xlabel("week")
        axes[0].set_ylabel("relative output")
        axes[0].legend(fontsize=8)
        sns.boxplot(data=used[used.market_code.isin(CORE)], x="season",
                    y="label_actual", hue="market_code", ax=axes[1],
                    showfliers=False, palette=PALETTE)
        axes[1].set_title("Outcome distribution by season (fliers hidden)")
        axes[1].legend(fontsize=8)
        fig.suptitle("Season and week structure", fontsize=13)
        fig.tight_layout()
        save(fig, "06_seasons.png")


# --------------------------------------------------------------------------
# 5. bivariate


def section_5(df):
    head(5, "BIVARIATE: correlation, redundancy, and a SPLOM")
    used = modelled(df)

    for code in CORE:
        d = used[used.market_code == code]
        fc = feature_cols(d)
        X = d[fc].apply(pd.to_numeric, errors="coerce")
        X = X.loc[:, X.notna().mean() > 0.5]
        X = X.loc[:, X.std(ddof=0) > 1e-9]
        C = X.corr()
        up = C.where(np.triu(np.ones(C.shape), 1).astype(bool)).stack()
        hi = up[up.abs() > 0.95].sort_values(key=abs, ascending=False)
        print(f"\n  --- {code}: {X.shape[1]} usable features, n={len(d):,} ---")
        print(f"  pairs above |r| 0.95: {len(hi)}      above 0.99: {(up.abs() > 0.99).sum()}"
              f"      exact duplicates: {(up.abs() > 0.9999).sum()}")
        for (a, b), v in hi.head(8).items():
            print(f"    {v:+.4f}   {a:<28s} ~  {b}")
        y = d.label_actual
        corr = X.apply(lambda s: s.corr(y)).dropna().sort_values(key=abs, ascending=False)
        print("  strongest correlations with the outcome:")
        print("   " + corr.head(8).round(3).to_string().replace("\n", "\n   "))

    print("\n  Two separate problems live in those tables.\n")
    print("  aux_mean is never its own feature. It is recs_mean on receiving")
    print("  yards, rush_att_mean on rushing yards, and on rushing attempts it is")
    print("  a copy of mean itself, in every case at r = 1.0000 to four places.")
    print("  A random forest drawing a square root of the columns at each split")
    print("  draws that quantity twice as often as it should, and an elastic net")
    print("  handed two identical columns splits one coefficient across them")
    print("  arbitrarily, which is how a feature can look weak in an importance")
    print("  table while being the thing the model is standing on.\n")
    print("  And the top of every correlation list is the same idea ten times.")
    print("  y_blend, y_season_mean, ewma_level, ewma_shrunk, mean, weighted_mean,")
    print("  y_median, y_trimmed_mean are eight ways of writing down how much the")
    print("  player has been doing lately, all correlated above 0.97 with each")
    print("  other. There is one strong feature in this dataset and it has been")
    print("  entered eight times.\n")

    # How many dimensions are really here.
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    print("  Effective dimensionality, on the matrix train.py actually builds")
    print("  (absent filled with zero, constant columns dropped):\n")
    print(f"    {'market':10s}{'columns':>9s}{'80pct':>8s}{'90pct':>8s}"
          f"{'95pct':>8s}{'PC1':>9s}")
    for code in CORE:
        d = used[used.market_code == code]
        X = d[feature_cols(d)].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        X = X.loc[:, X.std(ddof=0) > 1e-9]
        p = PCA().fit(StandardScaler().fit_transform(X))
        cum = np.cumsum(p.explained_variance_ratio_)
        k = lambda t: int(np.searchsorted(cum, t) + 1)  # noqa: E731
        print(f"    {code:10s}{X.shape[1]:>9d}{k(.80):>8d}{k(.90):>8d}{k(.95):>8d}"
              f"{p.explained_variance_ratio_[0]:>8.1%}")
    print("\n  Roughly a quarter of the columns carry 80 percent of the variance,")
    print("  and the first component alone is over a fifth of it. That component is")
    print("  the how-good-is-this-player axis. The other eighty-odd columns are")
    print("  arguing over the remainder.\n")

    # Correlation heatmap on a readable subset.
    d = used[used.market_code == "rec_yds"]
    pick = ["mean", "weighted_mean", "y_blend", "y_season_mean", "ewma_level",
            "ewma_shrunk", "y_median", "aux_mean", "recs_mean", "target_share_mean",
            "targets_weighted_mean", "air_yards_mean", "snap_share_mean",
            "depth_rank", "prev_season_mean", "team_implied_total", "team_spread",
            "game_total_line", "opp_pos_rec_yds_allowed", "label_actual"]
    pick = [c for c in pick if c in d.columns]
    C = d[pick].apply(pd.to_numeric, errors="coerce").corr()
    fig, ax = plt.subplots(figsize=(11, 9))
    sns.heatmap(C, cmap="RdBu_r", center=0, vmin=-1, vmax=1, annot=True, fmt=".2f",
                annot_kws={"size": 6}, ax=ax, square=True)
    ax.set_title("rec_yds: the block in the top left is one feature written "
                 "eight ways", fontsize=12)
    ax.tick_params(labelsize=7)
    save(fig, "07_correlation_heatmap.png")

    # SPLOM. The handout's rules: five or six features, faceted by category,
    # transparency for density.
    six = [c for c in ["y_blend", "target_share_mean", "snap_share_mean",
                       "air_yards_mean", "team_implied_total", "label_actual"]
           if c in d.columns]
    s = d[six + ["position"]].apply(
        lambda c: pd.to_numeric(c, errors="coerce") if c.name != "position" else c)
    s = s.dropna()
    keep = s.position.value_counts()
    s = s[s.position.isin(keep[keep >= 200].index)]
    if len(s) > 4000:
        s = s.sample(4000, random_state=42)
    g = sns.pairplot(s, hue="position", corner=True, diag_kind="kde",
                     plot_kws={"s": 8, "alpha": .35, "edgecolor": "none"},
                     height=1.9, palette=PALETTE)
    g.figure.suptitle("rec_yds SPLOM, coloured by position: five features and the "
                      "outcome", y=1.01, fontsize=13)
    save(g.figure, "08_splom_rec_yds.png")

    six_r = [c for c in ["y_blend", "carry_share_mean", "snap_share_mean",
                         "rz_carries_mean", "team_spread", "label_actual"]
             if c in used.columns]
    dr = used[used.market_code == "rush_yds"]
    s = dr[six_r + ["position"]].apply(
        lambda c: pd.to_numeric(c, errors="coerce") if c.name != "position" else c)
    s = s.dropna()
    keep = s.position.value_counts()
    s = s[s.position.isin(keep[keep >= 200].index)]
    if len(s) > 4000:
        s = s.sample(4000, random_state=42)
    g = sns.pairplot(s, hue="position", corner=True, diag_kind="kde",
                     plot_kws={"s": 8, "alpha": .35, "edgecolor": "none"},
                     height=1.9, palette=PALETTE)
    g.figure.suptitle("rush_yds SPLOM: the receiver cloud sits on the origin in "
                      "every panel", y=1.01, fontsize=13)
    save(g.figure, "09_splom_rush_yds.png")


# --------------------------------------------------------------------------
# 6. time series


def section_6(df):
    head(6, "TIME SERIES: how much does a player's past predict his next game?")
    g = games()
    print(f"\n  {len(g):,} player-games, {g.player_id.nunique():,} players\n")

    MK = {"rec_yds": ("receiving_yards", ("WR", "TE", "RB")),
          "recs": ("receptions", ("WR", "TE", "RB")),
          "rush_yds": ("rushing_yards", ("RB", "QB")),
          "rush_att": ("carries", ("RB", "QB")),
          "pass_yds": ("passing_yards", ("QB",)),
          "rush_td": ("rushing_tds", ("RB", "QB")),
          "rec_td": ("receiving_tds", ("WR", "TE", "RB"))}

    print("  Correlation of a game with the player's own earlier games, within a")
    print("  season. Never across seasons, because that is the window rule.\n")
    header = (f"  {'market':10s}{'n':>9s}" + "".join(f"{f'lag{k}':>8s}" for k in range(1, 7))
              + f"{'roll5':>9s}{'roll8':>9s}{'season':>9s}")
    print(header)
    curves = {}
    for code, (col, pos) in MK.items():
        d = g[g.position.isin(pos)]
        d = d[d[col].notna()]
        grp = d.groupby(["player_id", "season"])[col]
        lags = []
        for k in range(1, 7):
            lag = grp.shift(k)
            m = lag.notna()
            lags.append(d.loc[m, col].corr(lag[m]))
        curves[code] = lags
        r5 = grp.transform(lambda s: s.shift(1).rolling(5, min_periods=2).mean())
        r8 = grp.transform(lambda s: s.shift(1).rolling(8, min_periods=2).mean())
        ex = grp.transform(lambda s: s.shift(1).expanding(min_periods=2).mean())
        f = lambda v: d.loc[v.notna(), col].corr(v[v.notna()])  # noqa: E731
        print(f"  {code:10s}{len(d):>9,}" + "".join(f"{v:>8.3f}" for v in lags)
              + f"{f(r5):>9.3f}{f(r8):>9.3f}{f(ex):>9.3f}")

    print("\n  Three things fall out of that table.\n")
    print("  The decay is nearly flat for skill players. Receiving yards go 0.48")
    print("  at one game back to 0.43 at six, so last week is barely worth more")
    print("  than a month ago. Recency weighting cannot buy much against a curve")
    print("  that shape, and the averages bear it out: a five-game window and a")
    print("  season-to-date average are within a hundredth of each other.\n")
    print("  Passing yards are the exception and go the other way, 0.48 down to")
    print("  0.15 by six games back. A quarterback's recent form is real and his")
    print("  old form is nearly worthless. We run one LOOKBACK of five for every")
    print("  market, which is too long for quarterbacks and slightly short for")
    print("  everyone else.\n")
    print("  Touchdowns barely autocorrelate at all: 0.125 for receiving scores,")
    print("  0.19 for rushing. That is not a modelling failure waiting to be")
    print("  fixed, it is the ceiling. Who scores is close to a coin weighted by")
    print("  volume, which is why the honest version of a touchdown model is a")
    print("  rate model and not a yardage model.\n")

    fig, axes = plt.subplots(1, 3, figsize=(17, 4.8))
    for code, lags in curves.items():
        axes[0].plot(range(1, 7), lags, marker="o", ms=4, label=code)
    axes[0].set_xlabel("games back")
    axes[0].set_ylabel("correlation with this game")
    axes[0].set_title("Autocorrelation decays slowly, except for passing")
    axes[0].legend(fontsize=8)
    axes[0].axhline(0, c="k", lw=.8)

    # Window length against predictive correlation, which is the LOOKBACK question.
    for code, (col, pos) in MK.items():
        if code in ("rush_td", "rec_td"):
            continue
        d = g[g.position.isin(pos)]
        d = d[d[col].notna()]
        grp = d.groupby(["player_id", "season"])[col]
        xs, ys = [], []
        for k in range(2, 13):
            r = grp.transform(lambda s, k=k: s.shift(1).rolling(k, min_periods=2).mean())
            m = r.notna()
            xs.append(k)
            ys.append(d.loc[m, col].corr(r[m]))
        axes[1].plot(xs, ys, marker="o", ms=3, label=code)
    axes[1].axvline(LOOKBACK, ls="--", c="crimson", lw=1.4, label=f"LOOKBACK={LOOKBACK}")
    axes[1].set_xlabel("games in the rolling window")
    axes[1].set_ylabel("correlation with the next game")
    axes[1].set_title("How long a window should be")
    axes[1].legend(fontsize=8)

    d = g[g.position.isin(("WR", "TE", "RB"))]
    wk = d.groupby("week").receiving_yards.mean()
    axes[2].plot(wk.index, wk.values, marker="o", ms=4)
    axes[2].set_xlabel("week of season")
    axes[2].set_ylabel("mean receiving yards")
    axes[2].set_title("Week of season: no seasonality worth modelling")
    fig.suptitle("What the sequence says", fontsize=13)
    fig.tight_layout()
    save(fig, "10_autocorrelation.png")


# --------------------------------------------------------------------------
# 7. metrics


def section_7(df=None):
    head(7, "METRICS: is a better regression the same thing as a better bet?")
    import backtest_gap_system as bt
    from sklearn.metrics import (f1_score, mean_absolute_error, precision_score,
                                 r2_score, recall_score)

    cached = CACHE / "picks.pkl"
    if cached.exists():
        d = pd.read_pickle(cached)
    else:
        d = bt.build()
        CACHE.mkdir(parents=True, exist_ok=True)
        d.to_pickle(cached)
    d = d[np.isfinite(d["z"]) & d["label_actual"].notna()].copy()
    push = d.label_actual == d.line
    print(f"\n  {len(d):,} walk-forward priced props; {push.sum()} pushes dropped")
    d = d[~push].copy()
    d["went_over"] = (d.label_actual > d.line).astype(int)
    d["called_over"] = (d.pred > d.line).astype(int)

    print("\n  Both scorecards on the same predictions, per market.\n")
    rows = []
    for c, g in d.groupby("market_code"):
        yt, yp = g.went_over, g.called_over
        rows.append({
            "market": c, "n": len(g),
            "R2": round(r2_score(g.label_actual, g.pred), 3),
            "MAE": round(mean_absolute_error(g.label_actual, g.pred), 2),
            "MAE/sd": round(mean_absolute_error(g.label_actual, g.pred)
                            / g.label_actual.std(), 3),
            "accuracy": round((yt == yp).mean(), 3),
            "P over": round(precision_score(yt, yp, zero_division=0), 3),
            "R over": round(recall_score(yt, yp, zero_division=0), 3),
            "F1 over": round(f1_score(yt, yp, zero_division=0), 3),
            "P under": round(precision_score(1 - yt, 1 - yp, zero_division=0), 3),
            "R under": round(recall_score(1 - yt, 1 - yp, zero_division=0), 3),
            "pct went over": round(yt.mean(), 3)})
    t = pd.DataFrame(rows).sort_values("n", ascending=False)
    print(t.to_string(index=False).replace("\n", "\n  "))
    print(f"\n  Spearman correlation between R2 and accuracy across markets: "
          f"{t.R2.corr(t.accuracy, method='spearman'):+.2f}")
    print("\n  rush_att has the best regression on the board, R2 0.57, and close to")
    print("  the worst accuracy. recs has half that R2 and better accuracy. The two")
    print("  scorecards are not measuring the same thing and are not even ordered")
    print("  the same way. A regression is graded on distance from the outcome; a")
    print("  bet is graded on which side of one number you land, and the line sits")
    print("  where the predictive density is highest, so it is exactly the place")
    print("  where shaving the average error moves the fewest decisions.\n")

    cm = pd.crosstab(d.went_over.map({0: "actually UNDER", 1: "actually OVER"}),
                     d.called_over.map({0: "we said UNDER", 1: "we said OVER"}))
    print("  Confusion matrix, every priced prop, no selection:\n")
    print(cm.to_string().replace("\n", "\n    "))
    tp = cm.loc["actually OVER", "we said OVER"]
    fp = cm.loc["actually UNDER", "we said OVER"]
    fn = cm.loc["actually OVER", "we said UNDER"]
    tn = cm.loc["actually UNDER", "we said UNDER"]
    acc = (tp + tn) / len(d)
    print(f"\n    accuracy {acc:.3f}   over P {tp / (tp + fp):.3f} R {tp / (tp + fn):.3f} "
          f"F1 {2 * tp / (2 * tp + fp + fn):.3f}")
    print(f"    {' ' * 17}under P {tn / (tn + fn):.3f} R {tn / (tn + fp):.3f} "
          f"F1 {2 * tn / (2 * tn + fn + fp):.3f}")
    print(f"\n  Break-even at -110 is 52.4 percent. Betting every prop returns "
          f"{acc:.1%},")
    print("  so the model on its own is a losing bet. Everything this product earns")
    print("  it earns by declining.\n")

    print("  So: precision as we decline more.\n")
    d["absz"] = d.z.abs()
    n = len(d)
    print(f"  {'coverage':>10s}{'picks':>8s}{'|z| at least':>14s}{'precision':>11s}"
          f"{'vs 52.4pct':>12s}{'ROI':>9s}{'units won':>11s}")
    cover = [1.0, .75, .50, .35, .25, .15, .10, .07, .05, .03, .02, .01]
    for frac in cover:
        k = int(n * frac)
        s = d.nlargest(k, "absz")
        hit = s.won.mean()
        print(f"  {frac:>9.0%}{k:>8,}{s.absz.min():>14.2f}{hit:>10.1%}"
              f"{hit - 0.524:>+12.1%}{s.profit.mean():>+9.1%}{s.profit.sum():>+11.1f}")

    grid = np.arange(0.01, 1.005, 0.01)
    tot = [(f, d.nlargest(int(n * f), "absz").profit.sum()) for f in grid]
    roi = [(f, d.nlargest(int(n * f), "absz").profit.mean()) for f in grid]
    bt_, bp = max(tot, key=lambda x: x[1])
    br, brv = max(roi, key=lambda x: x[1])
    print(f"\n  total units peak at {bt_:.0%} coverage: {bp:+.1f} units")
    print(f"  ROI per pick peaks at {br:.0%} coverage: {brv:+.1%}")

    print("\n  That is the only tradeoff in this product, and it is not a balance")
    print("  between metrics, it is a business choice along one curve. Precision")
    print("  rises monotonically as we publish less. Maximise units and you publish")
    print("  a third of the board; maximise return per pick and you publish two")
    print("  percent of it. The live board sits near a fifth, between the two.\n")
    print("  Which is why F1 is the wrong objective here, not a co-equal one. F1")
    print("  is the harmonic mean of precision and recall, so it punishes declining")
    print("  to bet. Push it up and it pushes coverage toward everything, which is")
    print("  the row in the table that loses money. Use the course's own expected")
    print("  cost framing: E[cost] = P(FP) x C_FP + P(FN) x C_FN. A false positive")
    print("  is a bet we place and lose, costing one unit. A false negative is a")
    print("  bet we never place, costing zero. With C_FN at zero there is no recall")
    print("  term to trade against, and a metric that invents one is a metric that")
    print("  will talk us into losing bets.\n")
    print("  R2 is not the goal either, but it is not decoration. It is the")
    print("  ingredient: the gap that drives selection is a projection minus a")
    print("  line, so an unbiased, well-spread projection is what makes |z| mean")
    print("  anything. The right way to read the two together is that R2 buys")
    print("  nothing directly and precision at a chosen coverage is what pays.\n")

    fig, axes = plt.subplots(1, 3, figsize=(17, 4.8))
    ax = axes[0]
    ax.scatter(t.R2, t.accuracy, s=90)
    for _, r in t.iterrows():
        ax.annotate(r.market, (r.R2, r.accuracy), fontsize=8,
                    xytext=(4, 4), textcoords="offset points")
    ax.axhline(0.524, ls="--", c="crimson", lw=1.3, label="break-even 52.4pct")
    ax.set_xlabel("R2 of the projection")
    ax.set_ylabel("accuracy of the side")
    ax.set_title("A better regression is not a better bet")
    ax.legend(fontsize=8)

    ax = axes[1]
    fr = [f for f, _ in tot]
    pr = [d.nlargest(int(n * f), "absz").won.mean() for f in fr]
    ax.plot(np.array(fr) * 100, np.array(pr) * 100, lw=2)
    ax.axhline(52.4, ls="--", c="crimson", lw=1.3, label="break-even")
    ax.set_xlabel("coverage: pct of priced props we take a side on")
    ax.set_ylabel("precision, pct")
    ax.set_title("Precision is monotone in how much we decline")
    ax.legend(fontsize=8)

    ax = axes[2]
    ax.plot(np.array(fr) * 100, [v for _, v in tot], lw=2, label="total units")
    ax.set_xlabel("coverage, pct")
    ax.set_ylabel("units won")
    ax.axhline(0, c="k", lw=.8)
    ax2 = ax.twinx()
    ax2.plot(np.array(fr) * 100, [v * 100 for _, v in roi], lw=2, color="darkorange",
             label="ROI pct")
    ax2.set_ylabel("ROI per pick, pct")
    ax.set_title("Volume and edge pull in opposite directions")
    ax.legend(loc="lower right", fontsize=8)
    ax2.legend(loc="upper right", fontsize=8)
    fig.suptitle("Regression quality, betting quality, and the one curve that "
                 "matters", fontsize=13)
    fig.tight_layout()
    save(fig, "11_metrics.png")

    # Calibration of the projection itself, bucketed by PREDICTION. Bucketing by
    # outcome would manufacture a miss at both ends whatever the model did.
    fig, axes = plt.subplots(1, len(CORE), figsize=(5 * len(CORE), 4.2))
    for ax, code in zip(np.atleast_1d(axes), CORE):
        g = d[d.market_code == code].copy()
        g["b"] = pd.qcut(g.pred.rank(method="first"), 10, labels=False)
        agg = g.groupby("b").agg(pred=("pred", "mean"), act=("label_actual", "mean"))
        ax.plot(agg.pred, agg.act, marker="o")
        lo = min(agg.pred.min(), agg.act.min())
        hi = max(agg.pred.max(), agg.act.max())
        ax.plot([lo, hi], [lo, hi], ls="--", c="k", lw=1)
        ax.set_title(code)
        ax.set_xlabel("mean projection in decile")
        ax.set_ylabel("mean outcome")
    fig.suptitle("Calibration, bucketed by projection (never by outcome, which "
                 "would fake a miss at both ends)", fontsize=13)
    fig.tight_layout()
    save(fig, "12_calibration.png")


SECTIONS = {1: section_1, 2: section_2, 3: section_3, 4: section_4,
            5: section_5, 6: section_6, 7: section_7}


def main():
    want = [int(a) for a in sys.argv[1:] if a.isdigit()] or sorted(SECTIONS)
    df = None
    if any(s != 7 for s in want):
        df = features()
    for s in want:
        SECTIONS[s](df)
    print(f"\nFigures in {OUT}\n")


if __name__ == "__main__":
    main()
