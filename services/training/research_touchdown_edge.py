"""Can we beat the book on who scores a touchdown?

The one market family never tested against the price. Everything else here has
been: on receptions, receiving yards, rushing and passing, predicting outcomes
from the price alone beats predicting them from the price plus the model, out
of sample, every time.

Touchdowns are a different shape of problem and might be a different answer.
They are rare, so a season of box scores carries less signal per game than a
yardage market. But they are also driven by one thing the yardage markets only
hint at: who gets the ball inside the twenty. Red zone carries and red zone
targets are in pbp_player_game and expected touchdowns are in ff_opportunity,
and a book pricing a fourth receiver at +900 is doing less work per price than
it does on a mainline yardage number.

Against that, this project has already measured the market as the better
ranker here: on 278 player games its prices ranked scorers at AUC 0.769 against
the model's 0.758, and betting where the model claimed a two point edge
returned -25%. That was the model as it ships, without red zone usage rolled
over a window. This asks whether a model built for the question does better.

    market        the best anytime touchdown price across books, as a
                  probability. Best-of-books measured close to fair here: 0.2086
                  implied against 0.2073 scored on 328 player games.
    model         gradient boosting on usage, red zone share, expected
                  touchdowns and game context
    both          the two combined, weights fitted where neither was trained

Split three ways in time: train, then a slice to fit the combination, then a
slice neither has seen. Log loss decides, because a touchdown market is a
yes-or-no question and log loss is what an honest probability optimises.
"""

import os
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore")

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
EPS = 1e-6
WINDOWS = (3, 6)


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def log_loss(y, p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def load(engine):
    games = pd.read_sql(
        text(
            """
            SELECT g.player_id, g.season, g.week, g.game_date, g.team, g.opponent,
                   g.position,
                   COALESCE(g.any_tds, 0)      AS tds,
                   COALESCE(g.carries, 0)      AS carries,
                   COALESCE(g.targets, 0)      AS targets,
                   COALESCE(g.receptions, 0)   AS receptions,
                   COALESCE(g.rushing_yards,0) AS rushing_yards,
                   COALESCE(g.receiving_yards,0) AS receiving_yards,
                   s.offense_pct,
                   COALESCE(pbp.red_zone_carries, 0)     AS rz_carries,
                   COALESCE(pbp.red_zone_targets, 0)     AS rz_targets,
                   COALESCE(pbp.red_zone_carry_rate, 0)  AS rz_carry_rate,
                   COALESCE(pbp.red_zone_target_rate, 0) AS rz_target_rate,
                   COALESCE(fo.rush_touchdown_exp, 0)    AS exp_rush_td,
                   COALESCE(fo.rec_touchdown_exp, 0)     AS exp_rec_td,
                   ng.spread_line, ng.total_line, ng.home_team
            FROM player_game_stats_app g
            LEFT JOIN snap_counts s
                   ON s.player_id = g.player_id AND s.season = g.season AND s.week = g.week
            LEFT JOIN pbp_player_game pbp
                   ON pbp.player_id = g.player_id AND pbp.season = g.season
                  AND pbp.week = g.week
            LEFT JOIN ff_opportunity fo
                   ON fo.player_id = g.player_id AND fo.season = g.season
                  AND fo.week = g.week
            LEFT JOIN nfl_games ng
                   ON ng.season = g.season AND ng.week = g.week
                  AND (ng.home_team = g.team OR ng.away_team = g.team)
            WHERE g.season_type = 'REG'
              AND g.position IN ('RB', 'WR', 'TE', 'FB')
            """
        ),
        engine,
    )
    props = pd.read_sql(
        text(
            """
            SELECT s.player_name,
                   (s.commence_time AT TIME ZONE 'America/New_York')::date AS game_date,
                   MAX(s.price_american) AS best_price,
                   COUNT(DISTINCT s.bookmaker_key) AS books
            FROM odds_snapshots s
            WHERE s.market_key = 'player_anytime_td'
              AND lower(s.outcome_name) = 'yes'
              AND s.price_american IS NOT NULL
            GROUP BY 1, 2
            """
        ),
        engine,
    )
    names = pd.read_sql(text("SELECT external_id, name FROM players"), engine)

    def norm(x):
        return (x.str.lower().str.replace(".", "", regex=False)
                 .str.replace("-", " ", regex=False).str.split().str.join(" "))

    names["key"] = norm(names["name"])
    props["key"] = norm(props["player_name"])
    props = props.merge(names[["external_id", "key"]].drop_duplicates("key"),
                        on="key", how="inner").rename(columns={"external_id": "player_id"})
    games["game_date"] = pd.to_datetime(games["game_date"])
    props["game_date"] = pd.to_datetime(props["game_date"])
    return games.sort_values(["player_id", "game_date"]), props


ROLL = ["tds", "carries", "targets", "receptions", "rz_carries", "rz_targets",
        "rz_carry_rate", "rz_target_rate", "exp_rush_td", "exp_rec_td",
        "offense_pct", "rushing_yards", "receiving_yards"]


def features(games):
    d = games.copy()
    d["is_home"] = (d["team"] == d["home_team"]).astype(float)
    d["team_total"] = d["total_line"] / 2 - d["spread_line"] / 2 * np.where(
        d["is_home"] == 1, 1, -1)
    g = d.groupby("player_id", sort=False)
    for c in ROLL:
        for w in WINDOWS:
            d[f"{c}_m{w}"] = g[c].transform(
                lambda s, w=w: s.astype(float).shift(1).rolling(w, min_periods=1).mean())
    d["career_td_rate"] = g["tds"].transform(
        lambda s: s.shift(1).expanding().mean())
    d["games_seen"] = g.cumcount()
    d["y"] = (d["tds"] > 0).astype(int)
    return d


def main():
    eng = create_engine(DATABASE_URL, future=True)
    games, props = load(eng)
    d = features(games).merge(
        props[["player_id", "game_date", "best_price", "books"]],
        on=["player_id", "game_date"], how="inner")
    price = d["best_price"].astype(float)
    d["market"] = np.where(price > 0, 100.0 / (price + 100.0),
                           -price / (-price + 100.0))
    d = d.sort_values("game_date").reset_index(drop=True)
    cols = ([f"{c}_m{w}" for c in ROLL for w in WINDOWS]
            + ["career_td_rate", "games_seen", "team_total", "is_home",
               "total_line", "spread_line"])
    d = d.dropna(subset=["market"])
    print(f"{len(d)} priced anytime touchdown props, "
          f"{d['game_date'].min().date()} to {d['game_date'].max().date()}, "
          f"{d['y'].mean():.1%} scored\n")
    if len(d) < 1200:
        raise SystemExit("not enough priced touchdown props to split three ways")

    a, b = int(len(d) * 0.5), int(len(d) * 0.75)
    tr, cal, te = d.iloc[:a], d.iloc[a:b], d.iloc[b:]
    m = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_depth=4,
        min_samples_leaf=40, l2_regularization=1.0, random_state=0)
    m.fit(tr[cols], tr["y"])

    def model_p(x):
        return m.predict_proba(x[cols])[:, 1]

    lr = LogisticRegression(C=1.0).fit(
        np.c_[logit(cal["market"]), logit(model_p(cal))], cal["y"])
    both = lr.predict_proba(np.c_[logit(te["market"]), logit(model_p(te))])[:, 1]

    y = te["y"].to_numpy()
    rows = [("market only", te["market"].to_numpy()),
            ("model only", model_p(te)),
            ("both", both)]
    print(f"{'':14}{'log loss':>10}{'says':>8}{'scored':>8}")
    for name, p in rows:
        print(f"{name:<14}{log_loss(y, p):>10.4f}{np.mean(p):>8.1%}{y.mean():>8.1%}")
    print(f"\nweights: market {lr.coef_[0][0]:+.2f}, model {lr.coef_[0][1]:+.2f}")

    base = log_loss(y, te["market"].to_numpy())
    best = log_loss(y, both)
    print(f"\n{'BEATS the price by ' + format(base - best, '.4f') if best < base else 'does not beat the price'}"
          f"   on {len(te)} props")

    # Where the money would be: bet when the model says the price is wrong.
    pay = np.where(te["best_price"] > 0, te["best_price"] / 100.0,
                   100.0 / -te["best_price"])
    profit = np.where(y == 1, pay, -1.0)
    print(f"\n{'edge threshold':<16}{'bets':>6}{'hit':>8}{'roi':>8}")
    for thr in (0.0, 0.02, 0.05):
        pick = both - te["market"].to_numpy() > thr
        if pick.sum() >= 20:
            print(f"{'model over price ' + format(thr, '.0%'):<16}{int(pick.sum()):>6}"
                  f"{y[pick].mean():>8.1%}{profit[pick].mean():>+8.1%}")


if __name__ == "__main__":
    main()
