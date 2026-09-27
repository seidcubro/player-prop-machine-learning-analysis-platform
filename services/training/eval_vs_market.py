"""The one number that says whether this project has a reason to exist.

Every metric on the board so far measures us against ourselves: hit rate, ROI,
reliability by tier. None of them answers the question a sportsbook would ask,
which is whether our probability is better than the one already in the price.

So: Brier skill score against the de-vigged market probability, on graded picks,
with confidence intervals that resample games rather than props.

Three things make this honest that were missing from earlier comparisons.

**The benchmark is de-vigged.** Both sides of a prop are priced to sum above 1.
That excess is the bookmaker's fee, not a belief, and leaving it in hands us a
free few points because the vigged probability is deliberately too high on
whichever side we took. The fair probability is recovered from the two-sided
price at the same book, line and moment, and a row without a matching other side
is dropped rather than approximated.

**The scoring rule is proper.** Hit rate rewards a forecaster for being
confident rather than for being right (Gneiting & Raftery 2007). Brier and log
loss do not, and Brier decomposes into calibration plus refinement, which is the
distinction that matters here: this model ranks better than it calibrates, and a
single accuracy figure hides that.

**The intervals resample games.** Props in one game share their outcome: if the
quarterback throws for 400 then his receiver went over and the other team's back
probably went under. Treating 7,000 props as 7,000 independent observations
understates every interval by roughly the square root of props per game. Most of
the 1% differences this project has acted on do not survive being measured
properly.

Read the skill score, not the Brier. Positive means our probability beats the
price. Zero, or an interval containing zero, means the honest description of
this board is that it repackages the market's opinion, which would be worth
knowing and worth saying on the site.
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
import fit_display_probability as display_prob
from sqlalchemy import create_engine, text

ARTIFACT_DIR = os.getenv("ARTIFACT_DIR", "/artifacts")

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
N_BOOT = int(os.getenv("N_BOOT", "2000"))
MIN_ROWS = 150

# prop_edge_results stores the market we published, odds_snapshots the key the
# provider uses. Only the markets that carry two-sided prices can be de-vigged.
MARKET_KEY = {
    "recs": "player_receptions",
    "rec_yds": "player_reception_yds",
    "rush_yds": "player_rush_yds",
    "rush_att": "player_rush_attempts",
    "pass_yds": "player_pass_yds",
    "pass_att": "player_pass_attempts",
    "pass_completions": "player_pass_completions",
    "pass_td": "player_pass_tds",
}


def load(eng: object) -> pd.DataFrame:
    """Graded picks joined to the opposite side of the same price.

    Joined in pandas rather than SQL because market_code maps to the provider's
    market_key through a dictionary this module owns and the database does not
    have.
    """
    picks = pd.read_sql(text("""
        SELECT player_name, game_date, market_code, line, recommended_side,
               win_prob, price_american, hit, home_team, away_team, season,
               edge_tier, bookmaker_title
        FROM prop_edge_results
        WHERE hit IS NOT NULL AND win_prob IS NOT NULL
          AND price_american IS NOT NULL AND price_american <> 0
          AND line IS NOT NULL
    """), eng)
    odds = pd.read_sql(text("""
        SELECT player_name, market_key, line, bookmaker_title, outcome_name,
               price_american,
               (commence_time AT TIME ZONE 'America/New_York')::date AS game_date
        FROM odds_snapshots
        WHERE price_american IS NOT NULL AND line IS NOT NULL
    """), eng)
    if picks.empty or odds.empty:
        return pd.DataFrame()

    odds["side"] = odds["outcome_name"].str.strip().str.lower()
    odds = odds[odds["side"].isin(["over", "under"])]
    # The best price on each side, which is the one a bettor would have taken
    # and so the one whose pair defines the market's fair number.
    best = (odds.groupby(["player_name", "market_key", "line", "game_date",
                          "side"], as_index=False)["price_american"].max())
    wide = best.pivot_table(index=["player_name", "market_key", "line",
                                   "game_date"],
                            columns="side", values="price_american").reset_index()
    wide = wide.dropna(subset=["over", "under"])

    picks["market_key"] = picks["market_code"].map(MARKET_KEY)
    picks = picks.dropna(subset=["market_key"])
    for d in (picks, wide):
        d["game_date"] = pd.to_datetime(d["game_date"])
    d = picks.merge(wide, on=["player_name", "market_key", "line", "game_date"],
                    how="inner")
    if d.empty:
        return d

    # Fair probability of the side we published.
    over_fair = S.devig_two_way(d["over"].to_numpy(), d["under"].to_numpy())
    d["market_fair"] = np.where(d["recommended_side"].str.lower() == "over",
                                over_fair, 1.0 - over_fair)
    d["market_vigged"] = S.implied_prob(d["price_american"].to_numpy())
    d["y"] = d["hit"].astype(bool).astype(float)
    d["win_prob"] = pd.to_numeric(d["win_prob"], errors="coerce")
    d = d.dropna(subset=["market_fair", "win_prob", "y"])

    # What prop_edge_results stores is the model's own probability, not the one
    # the board publishes. The display correction is applied in
    # build_prop_edges at publish time and the track record was backfilled, so
    # the stored column never went through it: it averages 62.5% against a 52.7%
    # hit rate, while the live board averages 52.5% against a 52.2% breakeven.
    #
    # Scoring the stored column alone would therefore condemn a probability the
    # site does not show. Both are scored: `model_p` as stored, and `published_p`
    # with the same per-side curve build_prop_edges applies, which is the number
    # a reader actually sees.
    d["model_p"] = d["win_prob"]
    curves = display_prob.load(ARTIFACT_DIR)
    if curves:
        pub = []
        for _, r in d.iterrows():
            c = curves.get(str(r["recommended_side"]).lower())
            pub.append(display_prob.apply_curve(c, r["model_p"],
                                                r["market_vigged"])
                       if c else r["model_p"])
        d["published_p"] = pub
    else:
        print("WARNING: no display-probability curves found in "
              f"{ARTIFACT_DIR}; scoring the stored column only.")
        d["published_p"] = d["model_p"]

    # And the same thing honestly.
    #
    # The shipped curves were fitted on these very rows, so scoring them here
    # measures how well a correction fits the data it was fitted to, which is a
    # number that only ever goes one way. The walk-forward column refits the
    # curve per side on strictly earlier seasons and applies it to the next one,
    # which is what the correction could actually have known at the time.
    #
    # The gap between the two columns is the size of the illusion, and on a
    # correction with three parameters per side it should be small. If it is
    # not, the shipped number is decoration.
    d = d.sort_values("game_date").reset_index(drop=True)
    d["published_oof"] = np.nan
    seasons = sorted(d["season"].dropna().unique())
    for s in seasons[1:]:
        past = d[d["season"] < s]
        now = d.index[d["season"] == s]
        if len(past) < 200 or not len(now):
            continue
        for side in ("over", "under"):
            tr = past[past["recommended_side"].str.lower() == side]
            te = [i for i in now
                  if str(d.at[i, "recommended_side"]).lower() == side]
            if len(tr) < 100 or not te:
                continue
            try:
                curve = display_prob.fit_curve(
                    tr["model_p"].to_numpy(), tr["market_vigged"].to_numpy(),
                    tr["y"].to_numpy())
            except Exception:
                continue
            for i in te:
                d.at[i, "published_oof"] = display_prob.apply_curve(
                    curve, d.at[i, "model_p"], d.at[i, "market_vigged"])
    d["game"] = (d["game_date"].dt.strftime("%Y%m%d") + "_"
                 + d["home_team"].astype(str) + "_" + d["away_team"].astype(str))
    d["hold"] = (S.implied_prob(d["over"].to_numpy())
                 + S.implied_prob(d["under"].to_numpy()) - 1.0)
    return d


def block(name: str, d: pd.DataFrame) -> None:
    print(f"\n{name}")
    print(f"  {len(d)} picks over {d['game'].nunique()} clusters, "
          f"average hold {d['hold'].mean():.1%}, "
          f"market said {d['market_fair'].mean():.1%}, "
          f"happened {d['y'].mean():.1%}")
    print(f"  {'':24}{'n':>6}{'Brier':>8}{'mkt':>8}{'ECE':>7}{'says':>7}"
          f"{'skill vs market':>24}")
    for label, col in (("the model, as stored", "model_p"),
                       ("the board, as published", "published_p"),
                       ("  the same, walk-forward", "published_oof")):
        # Each row is scored against the market on exactly its own rows, so the
        # walk-forward line, which covers fewer seasons, is never compared to a
        # market Brier computed over a different set.
        sub = d.dropna(subset=[col])
        if len(sub) < 100:
            continue
        y = sub["y"].to_numpy()
        q = sub["market_fair"].to_numpy()
        g = sub["game"].to_numpy()
        p = sub[col].to_numpy()

        def skill(idx, p=p, y=y, q=q):
            return S.brier_skill(y[idx], p[idx], q[idx])

        bss, lo, hi = S.clustered_bootstrap(skill, g, n_boot=N_BOOT)
        verdict = ("level with the price" if S.crosses_zero(lo, hi)
                   else ("BEATS it" if bss > 0 else "worse"))
        print(f"  {label:<24}{len(sub):>6}{S.brier(y, p):>8.4f}"
              f"{S.brier(y, q):>8.4f}{S.ece(y, p):>7.3f}{p.mean():>7.1%}"
              f"   {S.fmt_ci(bss, lo, hi, pct=True):>21}  {verdict}")


def main() -> None:
    eng = create_engine(DATABASE_URL, future=True)
    d = load(eng)
    if d.empty:
        raise SystemExit("no graded picks could be matched to a two-sided price")

    print("Our published probability against the de-vigged market price.\n"
          "Intervals resample games, not props, so they reflect the number of\n"
          f"independent contests. {N_BOOT} resamples.")

    block("EVERYTHING", d)

    for tier in ("elite", "strong", "medium"):
        t = d[d["edge_tier"] == tier]
        if len(t) >= MIN_ROWS:
            block(f"tier: {tier}", t)

    for side in ("over", "under"):
        s = d[d["recommended_side"].str.lower() == side]
        if len(s) >= MIN_ROWS:
            block(f"side: {side}", s)

    for code, g in d.groupby("market_code"):
        if len(g) >= MIN_ROWS:
            block(f"market: {code}", g)

    print("\n" + "=" * 70)
    print("A skill score whose interval contains zero means our probability is\n"
          "not distinguishable from the market's on this sample. That is not a\n"
          "failure of the board, it is a fact about what the board is: a\n"
          "calibrated second opinion rather than a better line.")


if __name__ == "__main__":
    main()
