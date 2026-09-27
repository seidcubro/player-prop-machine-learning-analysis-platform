"""Can a role expansion be seen before kickoff, and is knowing worth anything?

The loss autopsy found one cause behind half of all losing picks: a player got
both more touches than his norm and more from each of them. Not a long catch, a
role widening inside a game. It is 48.9% of losses against 16.4% of wins, and on
receiving yards 34.0% against 1.0%.

Seid's own view, and it is the right prior: this is largely asking to predict the
future. A coach deciding at halftime to feed his tight end is not in any feed.
But some of it is knowable on Saturday, and the question is how much.

This is deliberately a re-run of `research_role_stability.py`, which asked
something adjacent in September and answered no: it predicted a role *change* at
AUC 0.674 and the picks it refused returned the same as the picks it kept. Three
things differ now and each could change the answer.

    the target      that asked whether volume moved in either direction. This
                    asks for the specific thing that costs money: volume and
                    efficiency both moving against the side we took. A model
                    aimed at the loss is not the same as one aimed at a
                    correlate of it.
    the population  that ran on picks the expected-value filter had chosen. This
                    runs on the gap system's picks, which are a different and
                    better set.
    the features    the season-only window, y_blend, the repaired depth chart
                    and the position indicators did not exist then.

Two questions, and the second is the only one that matters:

    1. Can it be ranked at all? AUC on seasons the fit never saw.
    2. Does refusing the riskiest picks make money? Return on what survives,
       with intervals that resample dates, because the first version of this was
       fooled by a slice that looked good on the seasons that chose it.

A model that ranks beautifully and does not pay is an interesting number, not a
filter. That was the verdict last time and it may well be again.

Verdict, September 2026: not built, and the reason is more useful than the
answer.

Question one is emphatically yes. AUC 0.773 on dates the fit never saw, up from
0.674 for the older and vaguer target. It leans on trailing target share
(+1.09), the size of our own disagreement (-0.78) and teammates ruled out
(+0.39), all of which read like football. Role expansions are visible in advance.

Question two is no, and not by a little:

    kept                picks     hit     break      roi
    everything            512   59.6%     54.4%    +9.9%
    steadiest 90%         460   58.9%     54.4%    +8.5%
    steadiest 75%         384   57.8%     54.4%    +6.9%
    steadiest 50%         256   55.9%     54.1%    +4.0%
    the 25% refused       128   64.8%     54.4%   +18.8%

Refusing the riskiest picks makes the board monotonically worse, and the picks it
wants to throw away are the best on it: 64.8% and +18.8% against a board average
of 59.6% and +9.9%.

The mechanism is the finding. A role expansion is symmetric before kickoff. The
conditions that let a man's role widen against us are the same conditions that
let it widen for us: a high target share, teammates out, a game with room to
move. The label only counts the times it went against us, so the model learns to
spot volatility and volatility is not direction. Refusing it removes at least as
many wins as losses, and here rather more.

So: the losses are explainable and not avoidable. You can see whose role is
liable to move. You cannot see which way, and a filter that acts on the first
without the second is throwing away the games where the same volatility landed in
our favour. Any given Sunday, measured.

Run again only if a directional signal appears, something that says a role will
widen rather than that it might. In-game data would qualify and is not available
before kickoff, which is the whole problem.
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sqlalchemy import create_engine, text

import backtest_gap_system as bt

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
VOLUME = {"recs": "targets", "rec_yds": "targets",
          "rush_yds": "carries", "rush_att": "carries"}
SHOCK = 1.25
HOLDOUT = 2025


def usage(eng):
    """Per player-game: touches, yards, snap share, and who was out."""
    g = pd.read_sql(text("""
        SELECT s.player_id, s.season, s.week, s.game_date, s.team, s.position,
               COALESCE(s.targets, 0)::float8   AS targets,
               COALESCE(s.carries, 0)::float8   AS carries,
               COALESCE(s.receiving_yards, 0)::float8 AS rec_yards,
               COALESCE(s.rushing_yards, 0)::float8   AS rush_yards,
               sc.offense_pct
        FROM player_game_stats_app s
        LEFT JOIN snap_counts sc
               ON sc.player_id = s.player_id AND sc.season = s.season
              AND sc.week = s.week
        WHERE s.season_type = 'REG'
        ORDER BY s.player_id, s.game_date
    """), eng)
    g["game_date"] = pd.to_datetime(g["game_date"])
    by = g.groupby("player_id", sort=False)
    # Everything shifted: nothing about this game may describe itself.
    for col in ("targets", "carries", "offense_pct"):
        g[f"{col}_mean"] = by[col].transform(
            lambda s: s.shift(1).rolling(5, min_periods=2).mean())
        g[f"{col}_sd"] = by[col].transform(
            lambda s: s.shift(1).rolling(5, min_periods=2).std())
    g["snap_last"] = by["offense_pct"].shift(1)
    g["snap_trend"] = g["snap_last"] - g["offense_pct_mean"]
    # Target share within his own team, trailing, which is the cleanest
    # statement of where he sits in the pecking order.
    tot = g.groupby(["team", "season", "week"])["targets"].transform("sum")
    g["tgt_share"] = g["targets"] / tot.replace(0, np.nan)
    g["tgt_share_mean"] = g.groupby("player_id", sort=False)["tgt_share"].transform(
        lambda s: s.shift(1).rolling(5, min_periods=2).mean())
    g["tgt_share_trend"] = (
        g.groupby("player_id", sort=False)["tgt_share"].shift(1)
        - g["tgt_share_mean"])
    g["games_with_team"] = g.groupby(["player_id", "team"], sort=False).cumcount()
    return g


def mates_out(eng):
    return pd.read_sql(text("""
        SELECT team, position, season, week,
               COUNT(*) FILTER (WHERE report_status IN ('Out','Doubtful'))
                   AS mates_out,
               COUNT(*) FILTER (WHERE report_status = 'Questionable')
                   AS mates_quest
        FROM injuries
        WHERE team IS NOT NULL AND position IS NOT NULL
        GROUP BY team, position, season, week
    """), eng)


FEATURES = ["snap_trend", "tgt_share_mean", "tgt_share_trend", "mates_out",
            "mates_quest", "games_with_team", "vol_cv", "snap_cv",
            "week", "spread_abs", "total_line", "line", "z"]


def main():
    eng = create_engine(DATABASE_URL, future=True)
    print("Rebuilding walk-forward picks...\n")
    d = bt.build()
    d = d[d["tier"].isin(["elite", "strong", "medium"])]
    d = d[d["market_code"].isin(VOLUME)].copy()

    g = usage(eng)
    d = d.merge(g, left_on=["player_id", "as_of_game_date"],
                right_on=["player_id", "game_date"], how="inner",
                suffixes=("", "_u"))
    d = d.merge(mates_out(eng), on=["team", "position", "season", "week"],
                how="left")
    d["mates_out"] = d["mates_out"].fillna(0)
    d["mates_quest"] = d["mates_quest"].fillna(0)

    vol = d["market_code"].map(VOLUME)
    actual_vol = np.where(vol == "targets", d["targets"], d["carries"])
    exp_vol = pd.to_numeric(d["aux_mean"], errors="coerce")
    yards = np.where(d["market_code"].isin(["rec_yds", "recs"]),
                     d["rec_yards"], d["rush_yards"])
    vr = actual_vol / exp_vol.replace(0, np.nan)
    er = (yards / np.where(actual_vol > 0, actual_vol, np.nan)) / \
         (d["pred"] / exp_vol.replace(0, np.nan))
    over = d["side"] == "over"
    d["expanded"] = (
        (np.where(over, vr <= 1 / SHOCK, vr >= SHOCK))
        & (np.where(over, er <= 1 / SHOCK, er >= SHOCK))
    ).astype(float)

    d["vol_cv"] = np.where(vol == "targets",
                           d["targets_sd"] / d["targets_mean"].replace(0, np.nan),
                           d["carries_sd"] / d["carries_mean"].replace(0, np.nan))
    d["snap_cv"] = d["offense_pct_sd"] / d["offense_pct_mean"].replace(0, np.nan)

    # The game's own shape, which is the most football-shaped thing available
    # before kickoff: a heavy favourite and a high total are the two public
    # statements about how many plays and how much scoring to expect.
    games = pd.read_sql(text("""
        SELECT season, week, home_team, away_team, spread_line, total_line
        FROM nfl_games
    """), eng)
    home = games.rename(columns={"home_team": "team"})[
        ["season", "week", "team", "spread_line", "total_line"]]
    away = games.rename(columns={"away_team": "team"})[
        ["season", "week", "team", "spread_line", "total_line"]]
    sched = pd.concat([home, away], ignore_index=True).drop_duplicates(
        subset=["season", "week", "team"])
    d = d.merge(sched, on=["season", "week", "team"], how="left")
    d["spread_abs"] = pd.to_numeric(d["spread_line"], errors="coerce").abs()
    d["total_line"] = pd.to_numeric(d["total_line"], errors="coerce")

    d = d.dropna(subset=["expanded"])
    # Split by position in the timeline, not by season.
    #
    # A season split leaves 55 rows to fit on, because the walk-forward backtest
    # only starts producing picks in 2024 and that season contributes fewer than
    # a hundred. Sorting by date and cutting at 60% keeps the fit strictly
    # earlier than the test, which is what matters, while leaving enough of both
    # to mean anything.
    d = d.sort_values("as_of_game_date").reset_index(drop=True)
    cut = int(len(d) * 0.6)
    fit = d.index < cut
    test = ~fit
    X = d[FEATURES].astype(float).fillna(0.0)
    print(f"{len(d)} picks, {d['expanded'].mean():.1%} were a role expansion "
          f"against us.\nFitting on the first {int(fit.sum())} by date "
          f"(through {d.loc[cut - 1, 'as_of_game_date'].date()}), "
          f"scoring on the last {int(test.sum())}.\n")
    if fit.sum() < 200 or test.sum() < 200:
        raise SystemExit("not enough either side of the split")

    m = LogisticRegression(C=1.0, max_iter=3000).fit(X[fit], d["expanded"][fit])
    risk = m.predict_proba(X[test])[:, 1]
    t = d[test].copy()
    t["risk"] = risk

    print("=" * 74)
    print("1. Can it be ranked on seasons the fit never saw?")
    try:
        auc = roc_auc_score(t["expanded"], t["risk"])
        print(f"   AUC {auc:.3f}   (0.50 is a coin flip)")
    except ValueError:
        print("   only one class present; cannot score")
        return
    print("   what it leans on:")
    for f, w in sorted(zip(FEATURES, m.coef_[0]), key=lambda kv: -abs(kv[1]))[:6]:
        print(f"     {f:<18}{w:+.3f}")

    print("\n" + "=" * 74)
    print("2. Does refusing the riskiest picks pay? This is the one that decides.")
    print(f"   {'kept':>22}{'picks':>7}{'hit':>24}{'break':>8}{'roi':>9}")

    def row(label, s):
        if len(s) < 60:
            print(f"   {label:>22}{len(s):>7}   too few")
            return
        w = s["won"].to_numpy()
        rate, lo, hi = S.clustered_bootstrap(
            lambda idx, w=w: float(np.mean(w[idx])), s["cluster"].to_numpy(),
            n_boot=1500)
        print(f"   {label:>22}{len(s):>7}   {rate:>6.1%} [{lo:>5.1%}, {hi:>5.1%}]"
              f"{s['breakeven'].mean():>8.1%}{s['profit'].mean():>+9.1%}")

    row("everything", t)
    for q in (0.9, 0.75, 0.5):
        row(f"steadiest {q:.0%}", t[t["risk"] <= t["risk"].quantile(q)])
    row("the 10% refused", t[t["risk"] > t["risk"].quantile(0.9)])
    row("the 25% refused", t[t["risk"] > t["risk"].quantile(0.75)])

    print("\n   A filter is worth building only if the kept rows beat "
          "'everything'\n   by more than their interval, and the refused rows "
          "are visibly worse.")


if __name__ == "__main__":
    main()
