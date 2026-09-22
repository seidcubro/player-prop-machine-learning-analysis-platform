"""Every pick, one at a time: what happened, and why it was right or wrong.

A season record says the board went 48%. It does not say whether the misses
were bad projections, players who left in the first quarter, games that turned
into blowouts, or coin flips that landed the other way. Those have completely
different fixes, and two of them are not model problems at all.

So this takes each graded pick and reconstructs the game around it: the snaps
the player actually played against his recent norm, the volume he saw against
his norm, what he did with that volume, and the score. Then it assigns a cause
and prints one line per pick, followed by the causes ranked by the units they
cost.

The causes, in the order they are tested, because an earlier one explains a
later one:

    left early        he played far fewer snaps than usual. Nothing about the
                      projection was wrong; he was hurt or benched.
    role change       normal snaps, but his share of the work moved a long way
                      from his recent norm. Somebody else's absence or a game
                      plan we could not see.
    game script       the game was decided by three scores. Teams stop
                      throwing when ahead and stop running when behind, and
                      every volume projection assumes a competitive game.
    efficiency        he got the volume we projected and did more or less with
                      it than anyone could have known. A 60 yard catch on four
                      targets is not a modelling failure.
    coin flip         the result landed within a whisker of the line. This is
                      what a 53% edge looks like from the inside and it is not
                      a mistake.
    model wrong       none of the above: normal snaps, normal usage, normal
                      efficiency, competitive game, and the number was still
                      well off. This is the bucket worth working on.

Usage:  MARKET=all WEEKS=2026-09-01:2026-09-30 python research_pick_autopsy.py
        DETAIL=0 to print only the summary.
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
SINCE, UNTIL = (os.getenv("WEEKS", "2026-09-01:2026-12-31").split(":") + [""])[:2]
DETAIL = os.getenv("DETAIL", "1") != "0"

# The volume a market depends on, and how close counts as a coin flip.
VOLUME = {
    "recs": "targets", "rec_yds": "targets", "rec_td": "targets",
    "rush_yds": "carries", "rush_att": "carries", "rush_td": "carries",
    "pass_yds": "attempts", "pass_att": "attempts",
    "pass_completions": "attempts", "pass_td": "attempts",
    "any_td": "touches",
}
STAT = {
    "recs": "receptions", "rec_yds": "receiving_yards", "rec_td": "receiving_tds",
    "rush_yds": "rushing_yards", "rush_att": "carries", "rush_td": "rushing_tds",
    "pass_yds": "passing_yards", "pass_att": "attempts",
    "pass_completions": "completions", "pass_td": "passing_tds",
    "any_td": "any_tds",
}
CLOSE = {"recs": 0.6, "rec_yds": 6.0, "rush_yds": 6.0, "rush_att": 0.6,
         "pass_yds": 12.0, "pass_att": 1.2, "pass_completions": 1.2,
         "pass_td": 0.4, "rec_td": 0.4, "rush_td": 0.4, "any_td": 0.4}


def load(engine):
    picks = pd.read_sql(
        text(
            """
            SELECT r.player_id, r.player_name, r.game_date, r.market_code,
                   r.line, r.projection, r.projection_median, r.recommended_side,
                   r.edge_tier, r.win_prob, r.price_american, r.actual, r.hit,
                   r.home_team, r.away_team
            FROM prop_edge_results r
            WHERE r.hit IS NOT NULL AND r.game_date BETWEEN :a AND :b
            ORDER BY r.game_date, r.player_name, r.market_code
            """
        ),
        engine, params={"a": SINCE, "b": UNTIL},
    )
    games = pd.read_sql(
        text(
            """
            SELECT g.player_id, g.game_date, g.team, g.opponent, g.position,
                   COALESCE(g.targets,0) targets, COALESCE(g.carries,0) carries,
                   COALESCE(g.attempts,0) attempts,
                   COALESCE(g.receptions,0) receptions,
                   COALESCE(g.receiving_yards,0) receiving_yards,
                   COALESCE(g.receiving_tds,0) receiving_tds,
                   COALESCE(g.rushing_yards,0) rushing_yards,
                   COALESCE(g.rushing_tds,0) rushing_tds,
                   COALESCE(g.passing_yards,0) passing_yards,
                   COALESCE(g.completions,0) completions,
                   COALESCE(g.passing_tds,0) passing_tds,
                   COALESCE(g.any_tds,0) any_tds,
                   COALESCE(g.carries,0) + COALESCE(g.targets,0) touches,
                   s.offense_pct
            FROM player_game_stats_app g
            LEFT JOIN snap_counts s
                   ON s.player_id = g.player_id AND s.season = g.season AND s.week = g.week
            WHERE g.season_type = 'REG'
            ORDER BY g.player_id, g.game_date
            """
        ),
        engine,
    )
    scores = pd.read_sql(
        text(
            """
            SELECT game_date, home_team, away_team, home_score, away_score
            FROM nfl_games WHERE home_score IS NOT NULL
            """
        ),
        engine,
    )
    for d in (picks, games, scores):
        d["game_date"] = pd.to_datetime(d["game_date"])
    return picks, games, scores


def with_norms(games: pd.DataFrame) -> pd.DataFrame:
    """Each player-game beside what that player normally does."""
    g = games.sort_values(["player_id", "game_date"]).groupby("player_id", sort=False)
    out = games.copy()
    for col in ("targets", "carries", "attempts", "touches", "offense_pct"):
        out[f"norm_{col}"] = g[col].transform(
            lambda s: s.shift(1).rolling(5, min_periods=1).mean())
    return out


def classify(p, row, score):
    """Why this pick landed where it did. Returns (cause, note)."""
    market = p["market_code"]
    vol_col = VOLUME.get(market, "touches")
    stat_col = STAT.get(market)
    line, actual = float(p["line"]), float(p["actual"])
    won = bool(p["hit"])

    vol = float(row.get(vol_col, 0) or 0)
    norm_vol = float(row.get(f"norm_{vol_col}") or 0)
    # Snap share arrives as a fraction, not a percentage. Comparing 0.95
    # against a threshold of 25 meant the "left early" test never fired once
    # and every player who got hurt in the first quarter was filed as a role
    # change, which is a different problem with a different fix.
    snaps = row.get("offense_pct")
    norm_snaps = row.get("norm_offense_pct")
    snaps = float(snaps) * 100 if snaps is not None and float(snaps) <= 1.5 else snaps
    norm_snaps = (float(norm_snaps) * 100 if norm_snaps is not None
                  and float(norm_snaps) <= 1.5 else norm_snaps)

    bits = []
    if norm_vol:
        bits.append(f"{vol_col} {vol:.0f} vs usual {norm_vol:.1f}")
    if snaps is not None and norm_snaps:
        bits.append(f"snaps {float(snaps):.0f}% vs {float(norm_snaps):.0f}%")
    if score is not None:
        bits.append(f"game {score}")
    note = "; ".join(bits)

    if snaps is not None and norm_snaps and float(norm_snaps) > 25 \
            and float(snaps) < 0.6 * float(norm_snaps):
        return "left early", note
    if norm_vol >= 3 and (vol > 1.6 * norm_vol or vol < 0.5 * norm_vol):
        return "role change", note
    if score is not None and abs(score[0] - score[1]) >= 17:
        return "game script", note
    if abs(actual - line) <= CLOSE.get(market, 1.0):
        return "coin flip", note
    # Volume was normal, so whatever happened came from what he did with it.
    if norm_vol and vol and stat_col and market not in ("recs", "rush_att", "pass_att"):
        per = actual / vol if vol else 0
        norm_per = None
        if norm_vol:
            norm_per = float(row.get(f"norm_{stat_col}") or 0) / norm_vol if row.get(f"norm_{stat_col}") else None
        if norm_per and (per > 1.5 * norm_per or per < 0.6 * norm_per):
            return "efficiency", note
    # Nothing unusual happened: normal snaps, normal usage, a competitive
    # game, ordinary efficiency, and a result that was not close to the line.
    # Whether this pick won or lost comes down to the projection, which makes
    # this the only bucket where the model is on trial. Deliberately not split
    # by outcome: doing that produces a "model wrong" bucket that is 0% by
    # construction and tells you nothing.
    return "clean read", note


def profit(row):
    price = float(row["price_american"])
    win = price / 100.0 if price > 0 else 100.0 / -price
    return win if row["hit"] else -1.0


def main():
    eng = create_engine(DATABASE_URL, future=True)
    picks, games, scores = load(eng)
    if picks.empty:
        raise SystemExit(f"no graded picks between {SINCE} and {UNTIL}")
    games = with_norms(games)
    for col in ("receptions", "receiving_yards", "rushing_yards", "passing_yards",
                "completions", "passing_tds", "receiving_tds", "rushing_tds"):
        games[f"norm_{col}"] = games.sort_values(["player_id", "game_date"]).groupby(
            "player_id", sort=False)[col].transform(
            lambda s: s.shift(1).rolling(5, min_periods=1).mean())

    gi = games.set_index(["player_id", "game_date"])
    si = {}
    for r in scores.itertuples(index=False):
        si[(r.game_date, r.home_team)] = (r.home_score, r.away_score)
        si[(r.game_date, r.away_team)] = (r.away_score, r.home_score)

    picks["profit"] = picks.apply(profit, axis=1)
    rows = []
    for p in picks.to_dict("records"):
        key = (p["player_id"], p["game_date"])
        row = gi.loc[key].to_dict() if key in gi.index else {}
        if isinstance(row.get("team"), pd.Series):   # duplicate rows, take one
            row = {k: (v.iloc[0] if hasattr(v, "iloc") else v) for k, v in row.items()}
        score = si.get((p["game_date"], row.get("team")))
        cause, note = classify(p, row, score)
        rows.append({**p, "cause": cause, "note": note})

    df = pd.DataFrame(rows)
    if DETAIL:
        print(f"{'date':<11}{'player':<20}{'market':<10}{'pick':<12}"
              f"{'line':>6}{'model':>7}{'actual':>7}  {'W/L':<4}{'cause':<13}why")
        for r in df.itertuples(index=False):
            med = r.projection_median if r.projection_median is not None else r.projection
            print(f"{str(r.game_date)[:10]:<11}{r.player_name[:19]:<20}{r.market_code:<10}"
                  f"{r.recommended_side + ' ' + str(r.line):<12}{r.line:>6.1f}{med:>7.1f}"
                  f"{r.actual:>7.1f}  {'WON' if r.hit else 'LOST':<4}{r.cause:<13}{r.note}")
        print()

    print("causes, by what they cost:\n")
    print(f"{'cause':<14}{'picks':>6}{'won':>6}{'hit rate':>10}{'units':>8}{'roi':>8}")
    agg = df.groupby("cause").agg(
        picks=("hit", "size"), won=("hit", "sum"),
        units=("profit", "sum"), roi=("profit", "mean")).sort_values("units")
    for c, r in agg.iterrows():
        print(f"{c:<14}{int(r.picks):>6}{int(r.won):>6}{r.won / r.picks:>10.1%}"
              f"{r.units:>+8.1f}{r.roi:>+8.1%}")
    print(f"{'TOTAL':<14}{len(df):>6}{int(df['hit'].sum()):>6}"
          f"{df['hit'].mean():>10.1%}{df['profit'].sum():>+8.1f}{df['profit'].mean():>+8.1%}")

    print("\nnet units by market, worst first:")
    by_m = df.groupby("market_code").agg(
        n=("hit", "size"), won=("hit", "sum"),
        units=("profit", "sum")).sort_values("units")
    for m, r in by_m.iterrows():
        print(f"  {m:<18}{int(r.n):>4} picks{r.won / r.n:>8.1%} won{r.units:>+8.1f} units")

    clean = df[df["cause"] == "clean read"]
    if len(clean):
        print("\nthe clean reads, where the projection is on trial:")
        for side, r in clean.groupby("recommended_side").agg(
                n=("hit", "size"), won=("hit", "sum"),
                units=("profit", "sum")).iterrows():
            print(f"  {side:<8}{int(r.n):>4} picks{r.won / r.n:>8.1%} won"
                  f"{r.units:>+8.1f} units")

    if len(clean):
        print("\nthe ten clean reads that cost the most:")
        for r in clean.nsmallest(10, "profit").itertuples(index=False):
            med = r.projection_median if r.projection_median is not None else r.projection
            print(f"  {str(r.game_date)[:10]} {r.player_name[:18]:<19}{r.market_code:<9}"
                  f"{r.recommended_side} {r.line:<6.1f} model {med:>6.1f}  actual "
                  f"{r.actual:>6.1f}   {r.note}")


if __name__ == "__main__":
    main()
