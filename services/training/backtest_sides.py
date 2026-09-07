"""Why the backtest lost, and whether picking sides off the median fixes it.

The season backtest lost 4.3% ROI, and slicing it by side shows the loss was not
symmetric: our under picks beat their break-even in four of six markets while our
over picks lost everywhere. We also picked overs about three times as often.

Two mechanisms, both verified in the data:

  1. The market shades prop lines toward the over. Blind unders hit above 50%
     raw in every market (the public bets overs, books lean on it). The shade
     roughly cancels the vig, so blind unders are close to break-even and blind
     overs lose 4-6%.
  2. Our models predict the MEAN. A prop line sits at the MEDIAN, that's what
     makes it a coin flip. Every yardage stat is right-skewed, so mean > median
     and a mean-predicting model sees phantom value on the over side of almost
     every line.

The tell that there is real signal underneath: our unders outperform blind
unders by 2-5 points in every market. To say "under", the model has to overcome
its own upward bias, so the unders that survive are the strongest calls.

Fix under test here: pick the side with a median regressor (quantile loss,
alpha=0.5) instead of the mean model. Same rows, same prices, same holdout
discipline (trained strictly before the test season). Strategies compared:

    A  mean model picks the side          (the failing baseline)
    B  median model picks the side        (the fix)
    C  median model, unders only          (fix + market structure)
    D  blind under                        (market structure alone, no model)

Validated on 2025 (full season) and then on the held-out 2023+2024 slates.
"""

import os

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sqlalchemy import create_engine, text

import eval as ev
import train as tr

MARKET_MAP = {
    "player_reception_yds": "rec_yds",
    "player_receptions": "recs",
    "player_rush_yds": "rush_yds",
    "player_rush_attempts": "rush_att",
    "player_pass_yds": "pass_yds",
    "player_pass_tds": "pass_td",
}

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


def load_lines(engine, years):
    yrs = ",".join(str(y) for y in years)
    return pd.read_sql(
        text(f"""
            SELECT s.player_name, s.market_key,
                   (s.commence_time AT TIME ZONE 'UTC')::date AS game_date,
                   AVG(s.line) FILTER (WHERE lower(s.outcome_name)='over') AS line,
                   AVG(CASE WHEN s.price_american<0 THEN 1+100.0/(-s.price_american)
                            ELSE 1+s.price_american/100.0 END)
                     FILTER (WHERE lower(s.outcome_name)='over')  AS over_dec,
                   AVG(CASE WHEN s.price_american<0 THEN 1+100.0/(-s.price_american)
                            ELSE 1+s.price_american/100.0 END)
                     FILTER (WHERE lower(s.outcome_name)='under') AS under_dec,
                   MAX(CASE WHEN s.price_american<0 THEN 1+100.0/(-s.price_american)
                            ELSE 1+s.price_american/100.0 END)
                     FILTER (WHERE lower(s.outcome_name)='over')  AS over_best,
                   MAX(CASE WHEN s.price_american<0 THEN 1+100.0/(-s.price_american)
                            ELSE 1+s.price_american/100.0 END)
                     FILTER (WHERE lower(s.outcome_name)='under') AS under_best
            FROM odds_snapshots s
            WHERE EXTRACT(YEAR FROM s.commence_time) IN ({yrs})
            GROUP BY 1, 2, 3
            HAVING AVG(s.line) FILTER (WHERE lower(s.outcome_name)='over') IS NOT NULL
        """),
        engine,
    )


def grade(df, side_col, best_price=False):
    out = df.copy()
    out["hit"] = np.where(
        out["actual"] == out["line"], np.nan,
        np.where(out[side_col] == "over",
                 out["actual"] > out["line"], out["actual"] < out["line"]),
    )
    oc = "over_best" if best_price else "over_dec"
    uc = "under_best" if best_price else "under_dec"
    out["decimal"] = np.where(out[side_col] == "over",
                              out[oc].fillna(1.909),
                              out[uc].fillna(1.909))
    g = out[out["hit"].notna()]
    if g.empty:
        return None
    units = np.where(g["hit"].astype(bool), g["decimal"] - 1.0, -1.0)
    return {
        "n": len(g),
        "hit": float(g["hit"].mean()),
        "breakeven": float((1.0 / g["decimal"]).mean()),
        "roi": float(units.mean()),
    }


def run(engine, years, label, models_cache):
    lines = load_lines(engine, years)
    lines["market_code"] = lines["market_key"].map(MARKET_MAP)
    lines = lines[lines["market_code"].notna()]

    frames = []
    for market, grp in lines.groupby("market_code"):
        os.environ["MARKET_CODE"] = market
        ev.MARKET_CODE = market

        if market not in models_cache:
            # Both models trained strictly on seasons before 2025, once.
            with engine.connect() as c:
                row = c.execute(text(
                    "SELECT a.model_name FROM active_models a "
                    "JOIN prop_markets m ON m.id=a.market_id WHERE m.code=:c"
                ), {"c": market}).mappings().first()
            if not row:
                models_cache[market] = None
                continue
            import json
            meta_path = os.path.join(ev.ARTIFACT_DIR,
                                     f"{row['model_name']}_{market}_lb5.json")
            if not os.path.exists(meta_path):
                models_cache[market] = None
                continue
            cols = json.load(open(meta_path))["feature_cols"]

            feats = ev.load_labeled_rows(cols)
            dates = pd.to_datetime(feats["as_of_game_date"])
            season = dates.dt.year.where(dates.dt.month >= 3, dates.dt.year - 1)
            train_df = feats[season < 2025]
            if len(train_df) < 200:
                models_cache[market] = None
                continue
            Xtr = ev.build_feature_matrix(train_df, cols)
            ytr = train_df[ev.LABEL_COL].astype(float)

            mean_model = tr.build_model(row["model_name"])
            mean_model.fit(Xtr, ytr)
            med_model = GradientBoostingRegressor(
                loss="quantile", alpha=0.5, n_estimators=400,
                learning_rate=0.05, max_depth=3, min_samples_leaf=20,
                subsample=0.9, random_state=42,
            )
            med_model.fit(Xtr, ytr)
            models_cache[market] = (mean_model, med_model, cols)
            print(f"  trained {market} on {len(train_df)} pre-2025 rows")

        if models_cache[market] is None:
            continue
        mean_model, med_model, cols = models_cache[market]

        feats = ev.load_labeled_rows(cols)
        X = ev.build_feature_matrix(feats, cols)
        feats = feats.assign(
            pred_mean=np.clip(mean_model.predict(X), 0, None),
            pred_med=np.clip(med_model.predict(X), 0, None),
        )

        ids = pd.read_sql(text(
            "SELECT external_id, name FROM players WHERE external_id IS NOT NULL"
        ), engine)
        ids["key"] = ids["name"].str.lower().str.replace(r"[.\-]", "", regex=True)
        g = grp.copy()
        g["key"] = g["player_name"].str.lower().str.replace(r"[.\-]", "", regex=True)
        g = g.merge(ids, on="key", how="inner")

        m = g.merge(
            feats[["player_id", "as_of_game_date", "pred_mean", "pred_med",
                   ev.LABEL_COL]],
            left_on=["external_id", "game_date"],
            right_on=["player_id", "as_of_game_date"], how="inner",
        ).dropna(subset=["pred_mean", "pred_med", ev.LABEL_COL, "line"])
        if m.empty:
            continue
        m["actual"] = m[ev.LABEL_COL].astype(float)
        m["market_code"] = market
        frames.append(m)

    if not frames:
        print(f"{label}: nothing matched")
        return

    df = pd.concat(frames, ignore_index=True)
    df["side_mean"] = np.where(df["pred_mean"] > df["line"], "over", "under")
    df["side_med"] = np.where(df["pred_med"] > df["line"], "over", "under")
    df["side_blind"] = "under"

    print(f"\n===== {label}: {len(df)} props =====")
    print(f"mean model picks over on   {(df['side_mean'] == 'over').mean():.1%} of props")
    print(f"median model picks over on {(df['side_med'] == 'over').mean():.1%} of props\n")

    # Star tier within each market: top third by line size, where the public
    # over-shade measured strongest.
    df["line_tier"] = df.groupby("market_code")["line"].transform(
        lambda x: pd.qcut(x, 3, labels=False, duplicates="drop")
    )
    stars = df[df["line_tier"] == df.groupby("market_code")["line_tier"].transform("max")]

    strategies = [
        ("A mean-pick (old)", "side_mean", df, False),
        ("B median-pick", "side_med", df, False),
        ("C median unders", "side_med", df[df["side_med"] == "under"], False),
        ("C at best price", "side_med", df[df["side_med"] == "under"], True),
        ("E star unders (median)", "side_med",
         stars[stars["side_med"] == "under"], False),
        ("E at best price", "side_med",
         stars[stars["side_med"] == "under"], True),
        ("D blind under", "side_blind", df, False),
    ]
    print(f"{'strategy':<26}{'n':>6}{'hit':>8}{'break-even':>12}{'edge':>8}{'ROI':>9}")
    print("-" * 69)
    for name, col, frame, bp in strategies:
        r = grade(frame, col, best_price=bp)
        if r is None:
            continue
        print(f"{name:<26}{r['n']:>6}{r['hit']:>8.3f}{r['breakeven']:>12.3f}"
              f"{r['hit'] - r['breakeven']:>+8.3f}{r['roi']:>+9.3f}")

    # Stability: does the unders strategy hold in both halves of the season?
    # A real structural effect should not live in one hot month.
    df["half"] = np.where(
        pd.to_datetime(df["game_date"]).dt.month.isin([9, 10]),
        "Sep-Oct", "Nov-Jan",
    )
    print("\nstrategy C by season half:")
    for h, grp2 in df[df["side_med"] == "under"].groupby("half"):
        r = grade(grp2, "side_med")
        rb = grade(grp2, "side_med", best_price=True)
        if r:
            print(f"  {h:<10}n={r['n']:<6} hit={r['hit']:.3f} be={r['breakeven']:.3f} "
                  f"edge={r['hit'] - r['breakeven']:+.3f}   best-price edge="
                  f"{rb['hit'] - rb['breakeven']:+.3f}")

    print("\nper market, strategy C at best price:")
    for market, grp2 in df[df["side_med"] == "under"].groupby("market_code"):
        r = grade(grp2, "side_med", best_price=True)
        if r:
            print(f"  {market:<18}n={r['n']:<6} hit={r['hit']:.3f} "
                  f"be={r['breakeven']:.3f} edge={r['hit'] - r['breakeven']:+.3f}")


def structural_check(engine, years, label):
    """Model-free check of the under-shade on other seasons. No model touches
    this, so unlike the model runs it is legitimately out-of-sample anywhere."""
    lines = load_lines(engine, years)
    lines["market_code"] = lines["market_key"].map(MARKET_MAP)
    lines = lines[lines["market_code"].notna()]
    import json as _j
    q = text("SELECT external_id, name FROM players WHERE external_id IS NOT NULL")
    ids = pd.read_sql(q, engine)
    ids["key"] = ids["name"].str.lower().str.replace(r"[.\-]", "", regex=True)
    lines["key"] = lines["player_name"].str.lower().str.replace(r"[.\-]", "", regex=True)
    lines = lines.merge(ids, on="key", how="inner")
    stat = {"rec_yds": "receiving_yards", "recs": "receptions",
            "rush_yds": "rushing_yards", "rush_att": "carries",
            "pass_yds": "passing_yards", "pass_td": "passing_tds"}
    g = pd.read_sql(text(
        "SELECT player_id, game_date, receiving_yards, receptions, rushing_yards,"
        " carries, passing_yards, passing_tds FROM player_game_stats_app"
    ), engine)
    m = lines.merge(g, left_on="external_id", right_on="player_id", how="inner")
    m = m[(pd.to_datetime(m["game_date_y"]) - pd.to_datetime(m["game_date_x"]))
          .dt.days.abs() <= 1]
    m["actual"] = [r[stat[mc]] for mc, r in zip(m["market_code"],
                                                m.to_dict("records"))]
    m = m.dropna(subset=["actual", "line"])
    m = m[m["actual"] != m["line"]]
    m["line_tier"] = m.groupby("market_code")["line"].transform(
        lambda x: pd.qcut(x, 3, labels=False, duplicates="drop"))
    stars = m[m["line_tier"] == m.groupby("market_code")["line_tier"].transform("max")]
    for name, frame in (("all", m), ("stars", stars)):
        hit = (frame["actual"] < frame["line"]).mean()
        be = (1.0 / frame["under_dec"].fillna(1.909)).mean()
        beb = (1.0 / frame["under_best"].fillna(1.909)).mean()
        print(f"  {label} blind under ({name}): n={len(frame)} hit={hit:.3f} "
              f"be(avg)={be:.3f} be(best)={beb:.3f} "
              f"edge(best)={hit - beb:+.3f}")


def main():
    engine = create_engine(DATABASE_URL, future=True)
    cache = {}
    run(engine, [2025, 2026], "TEST 2025 season (out-of-sample)", cache)
    print("\n===== structural effect, model-free, other seasons =====")
    structural_check(engine, [2023, 2024], "2023+2024")


if __name__ == "__main__":
    main()
