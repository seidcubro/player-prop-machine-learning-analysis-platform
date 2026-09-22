"""Can we tell, before kickoff, that a player's role is about to change?

The pick autopsy says this is where the board loses. In Week 1 of 2026, 57 of
319 picks landed on players whose usage bore no resemblance to their recent
norm: they hit 29.8% and cost 24 units, while everything else made money. Week
2 repeated it, with James Cook on 21 carries against a usual 14.6 and Aaron
Jones on 23 against 15.4, both with snap shares 20 points above their average.

The projection cannot fix that. It is built from what a player has been doing,
and the answer to "he is about to do something else" is not a better average.
But it may be predictable that a projection is *unreliable*, and a pick we can
tell is unreliable is a pick not worth publishing.

So: a stability score from what is knowable before kickoff.

    snap volatility     the spread of his snap share over recent games
    snap trend          the most recent snap share against that average
    volume volatility   the same for targets or carries
    games with team     a player two games into a new team has no norm
    depth movement      where the chart has him now against where it did
    teammates out       somebody else's absence is how roles move
    week of season      early is when last year's norm is least useful

Two questions, in order, because the second only matters if the first is yes:

    1. Does the score actually predict a role change? Measured as the AUC
       against the autopsy's own definition, on seasons the fit never saw.
    2. Does refusing to publish the least stable picks make money? Measured as
       the return on what survives, on those same unseen seasons.

Question two is the one that decides. A score that ranks role changes
beautifully and does not improve returns is an interesting number, not a
filter, and the project has already been fooled once by a slice that looked
good on the seasons that chose it.

Verdict, September 2026: not built. Question one is yes and question two is no.

    AUC 0.674 on unseen seasons, leaning on volume volatility (+1.79), the
    most recent snap share against its average (+0.89) and snap volatility
    (+0.82). It really does find the players whose roles are about to move.

    kept                picks     won     roi
    everything           5851   52.6%   +0.5%
    steadiest 90%        5266   52.6%   +0.5%
    steadiest 75%        4388   52.6%   +0.4%
    steadiest 50%        2926   52.3%   -0.1%
    the 10% refused       585   52.8%   +0.1%

The picks it refuses return +0.1% and the ones it keeps +0.5%: the same, and
filtering harder is worse. A role change explains a loss after the fact and
breaks in our favour just as often, so classifying losses by what happened is
not the same as predicting which picks will lose. The Week 1 figure that
prompted this, 57 role-change picks at 29.8%, was one week of hindsight.

Run it again if a new signal arrives that is knowable before kickoff, such as
a real inactive-list feed. The harness is the useful part.
"""

import os

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)

VOLUME = {
    "recs": "targets", "rec_yds": "targets", "rec_td": "targets",
    "rush_yds": "carries", "rush_att": "carries", "rush_td": "carries",
    "pass_yds": "attempts", "pass_att": "attempts",
    "pass_completions": "attempts", "pass_td": "attempts",
}
# A role change, as the autopsy defines it: volume a long way from the norm.
CHANGE_HI, CHANGE_LO = 1.6, 0.5
HOLDOUT_FROM = 2025


def load(engine):
    picks = pd.read_sql(
        text(
            """
            SELECT player_id, player_name, game_date, market_code, line,
                   recommended_side, edge_tier, win_prob, price_american,
                   actual, hit
            FROM prop_edge_results
            WHERE hit IS NOT NULL AND price_american IS NOT NULL
              AND price_american <> 0
            """
        ),
        engine,
    )
    games = pd.read_sql(
        text(
            """
            SELECT g.player_id, g.season, g.week, g.game_date, g.team, g.position,
                   COALESCE(g.targets,0) targets, COALESCE(g.carries,0) carries,
                   COALESCE(g.attempts,0) attempts,
                   s.offense_pct
            FROM player_game_stats_app g
            LEFT JOIN snap_counts s
                   ON s.player_id = g.player_id AND s.season = g.season
                  AND s.week = g.week
            WHERE g.season_type = 'REG'
            ORDER BY g.player_id, g.game_date
            """
        ),
        engine,
    )
    depth = pd.read_sql(
        text(
            """
            SELECT player_id, season, week::int AS week, MIN(depth_team) AS rank
            FROM depth_charts WHERE depth_team IS NOT NULL
            GROUP BY player_id, season, week::int
            """
        ),
        engine,
    )
    inj = pd.read_sql(
        text(
            """
            SELECT team, position, season, week,
                   COUNT(*) FILTER (WHERE report_status IN ('Out','Doubtful')) AS mates_out
            FROM injuries WHERE team IS NOT NULL AND position IS NOT NULL
            GROUP BY team, position, season, week
            """
        ),
        engine,
    )
    for d in (picks, games):
        d["game_date"] = pd.to_datetime(d["game_date"])
    return picks, games, depth, inj


def build(picks, games, depth, inj):
    """One row per pick, with the stability signals known before kickoff."""
    g = games.sort_values(["player_id", "game_date"]).copy()
    by = g.groupby("player_id", sort=False)
    # Everything shifted: nothing about this game informs its own features.
    g["snap_mean"] = by["offense_pct"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=2).mean())
    g["snap_sd"] = by["offense_pct"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=2).std())
    g["snap_last"] = by["offense_pct"].shift(1)
    g["games_with_team"] = g.groupby(["player_id", "team"], sort=False).cumcount()
    g = g.merge(depth, on=["player_id", "season", "week"], how="left")
    # Regrouped after the merge: `by` was built before it and has no rank.
    g["rank_prev"] = g.groupby("player_id", sort=False)["rank"].shift(1)
    g = g.merge(inj, on=["team", "position", "season", "week"], how="left")

    rows = []
    for market, vol in VOLUME.items():
        p = picks[picks["market_code"] == market]
        if p.empty:
            continue
        v = g.copy()
        vb = v.groupby("player_id", sort=False)[vol]
        v["vol_mean"] = vb.transform(
            lambda s: s.shift(1).rolling(5, min_periods=2).mean())
        v["vol_sd"] = vb.transform(
            lambda s: s.shift(1).rolling(5, min_periods=2).std())
        v["vol_now"] = v[vol]
        m = p.merge(v, on=["player_id", "game_date"], how="inner")
        m["market_volume"] = vol
        rows.append(m)
    if not rows:
        raise SystemExit("no picks could be matched to player games")
    d = pd.concat(rows, ignore_index=True)

    d["snap_cv"] = d["snap_sd"] / d["snap_mean"].replace(0, np.nan)
    d["snap_jump"] = d["snap_last"] - d["snap_mean"]
    d["vol_cv"] = d["vol_sd"] / d["vol_mean"].replace(0, np.nan)
    d["rank_move"] = (d["rank_prev"] - d["rank"]).fillna(0)
    d["mates_out"] = d["mates_out"].fillna(0)
    d["early_season"] = (d["week"] <= 4).astype(float)
    d["new_to_team"] = (d["games_with_team"] < 4).astype(float)

    ratio = d["vol_now"] / d["vol_mean"].replace(0, np.nan)
    d["role_change"] = ((ratio > CHANGE_HI) | (ratio < CHANGE_LO)).astype(float)
    d["profit"] = np.where(
        d["hit"],
        np.where(d["price_american"] > 0, d["price_american"] / 100.0,
                 100.0 / -d["price_american"]),
        -1.0)
    return d


FEATURES = ["snap_cv", "snap_jump", "vol_cv", "mates_out", "rank_move",
            "early_season", "new_to_team", "games_with_team"]


def main():
    eng = create_engine(DATABASE_URL, future=True)
    d = build(*load(eng))
    d = d.dropna(subset=["vol_mean", "snap_mean"])
    fit = d[d["season"] < HOLDOUT_FROM]
    test = d[d["season"] >= HOLDOUT_FROM]
    print(f"{len(d)} picks matched; fitting on {len(fit)} (before {HOLDOUT_FROM}), "
          f"scoring on {len(test)}\n")
    if len(fit) < 300 or len(test) < 300:
        raise SystemExit("not enough history on either side of the split")

    X = fit[FEATURES].fillna(0)
    model = LogisticRegression(C=1.0, max_iter=2000).fit(X, fit["role_change"])
    test = test.copy()
    test["risk"] = model.predict_proba(test[FEATURES].fillna(0))[:, 1]

    from sklearn.metrics import roc_auc_score
    auc = roc_auc_score(test["role_change"], test["risk"])
    print("1. does the score predict a role change on unseen seasons?")
    print(f"   AUC {auc:.3f}   (0.50 is a coin flip)")
    print("   what it leans on:")
    for f, w in sorted(zip(FEATURES, model.coef_[0]), key=lambda kv: -abs(kv[1])):
        print(f"     {f:<16}{w:+.3f}")

    print("\n2. does refusing the least stable picks make money?")
    print(f"   {'kept':>22}{'picks':>7}{'won':>8}{'roi':>8}")
    base = test
    print(f"   {'everything':>22}{len(base):>7}{base['hit'].mean():>8.1%}"
          f"{base['profit'].mean():>+8.1%}")
    for q in (0.9, 0.75, 0.5):
        cut = test["risk"].quantile(q)
        keep = test[test["risk"] <= cut]
        print(f"   {f'steadiest {q:.0%}':>22}{len(keep):>7}{keep['hit'].mean():>8.1%}"
              f"{keep['profit'].mean():>+8.1%}")
    worst = test[test["risk"] > test["risk"].quantile(0.9)]
    print(f"   {'the 10% refused':>22}{len(worst):>7}{worst['hit'].mean():>8.1%}"
          f"{worst['profit'].mean():>+8.1%}")

    print("\n   by season, keeping the steadiest 75%:")
    for season, s in test.groupby("season"):
        cut = s["risk"].quantile(0.75)
        keep = s[s["risk"] <= cut]
        print(f"     {int(season)}   all {len(s):>5} picks {s['profit'].mean():>+7.1%}"
              f"    kept {len(keep):>5} {keep['profit'].mean():>+7.1%}")


if __name__ == "__main__":
    main()
