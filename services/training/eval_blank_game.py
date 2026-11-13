"""Would knowing how often a player draws a blank have fixed the picks it cost?

diagnose_zero_games.py counted the games the game log leaves out because the
player took the field and recorded nothing: 12 to 14 percent of all skill
player-games every season, a quarter of tight end games, and 5 to 6 percent of
the games of players the books price (12 percent for priced tight ends).

Every model here is fitted on the games that are in the log. So what it
predicts is what he does given that he does something, and the win
probability on the board is conditional on that too. The honest probability
has one more term:

    over wins   = (1 - blank) x model's over
    under wins  = blank + (1 - blank) x model's under

where blank is the chance he plays and records nothing. For a starter that is
about zero and nothing changes. For a backup tight end priced at over 0.5
catches it is the whole bet.

Rebuilding the log, the features and every model with those games written in
as zeros is the thorough repair and it is days of work with its own risks.
This tests the cheap one first. For every graded live pick this season, blank
is estimated from the player's own last ten games with a snap, before that
game, pulled toward the rate for priced players at his position in earlier
seasons. Nothing from the game itself or after it is used.

Three questions:

    1. Does the estimate separate anything? Over picks cut by blank chance: if
       the high band loses far more than the board's probability said, the
       estimate carries information the board lacks.
    2. Is the corrected probability more honest? Predicted against achieved,
       and the Brier score, before and after.
    3. What would it have done to the elite overs? How many fall under the
       rate their price needed once corrected, and how those did.

If all three say yes, the correction goes into build_prop_edges.py ahead of
the ranking, so a pick like this is ranked on what it is worth. The pick stays
on the board either way.

Read-only.

    docker compose run --rm training python eval_blank_game.py
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

NOW = int(os.getenv("SEASON", "2026"))
WINDOW = int(os.getenv("BLANK_WINDOW", "10"))
PRIOR = float(os.getenv("BLANK_PRIOR_GAMES", "5"))


def implied(price):
    p = pd.to_numeric(price, errors="coerce")
    out = np.where(p < 0, -p / (-p + 100.0), 100.0 / (p + 100.0))
    return np.where(np.isnan(out), 110.0 / 210.0, out)


def load(eng):
    picks = pd.read_sql(text("""
        SELECT player_id, game_date, market_code, line::float8 AS line,
               recommended_side AS side, edge_tier AS tier,
               win_prob::float8 AS wp, hit, price_american
        FROM prop_edge_results
        WHERE season = :s AND source = 'live' AND hit IS NOT NULL
          AND win_prob IS NOT NULL
    """), eng, params={"s": NOW})
    cal = pd.read_sql(text("""
        SELECT DISTINCT season, week, game_date FROM player_game_stats_app
        WHERE season = :s
    """), eng, params={"s": NOW})
    snaps = pd.read_sql(text("""
        SELECT sc.player_id, sc.season, sc.week, MAX(sc.position) AS position,
               BOOL_OR(s.player_id IS NOT NULL) AS has_row
        FROM snap_counts sc
        LEFT JOIN (
            SELECT DISTINCT player_id, season, week FROM player_game_stats_app
        ) s ON s.player_id = sc.player_id AND s.season = sc.season
           AND s.week = sc.week
        WHERE sc.offense_snaps > 0 AND sc.season >= :s - 2
          AND sc.player_id IS NOT NULL
        GROUP BY 1, 2, 3
    """), eng, params={"s": NOW})
    return picks, cal, snaps


def blank_rates(snaps, priced_ids):
    """Each player's chance of a blank going into each game, from before it."""
    s = snaps.sort_values(["player_id", "season", "week"]).copy()
    s["blank"] = (~s["has_row"].astype(bool)).astype(float)
    base = (s[(s.season < NOW) & s.player_id.isin(priced_ids)]
            .groupby("position")["blank"].mean())
    overall = float(s[(s.season < NOW) & s.player_id.isin(priced_ids)]["blank"].mean())
    by = s.groupby("player_id")["blank"]
    s["seen"] = by.transform(
        lambda x: x.shift(1).rolling(WINDOW, min_periods=1).count()).fillna(0)
    s["blanks"] = by.transform(
        lambda x: x.shift(1).rolling(WINDOW, min_periods=1).sum()).fillna(0)
    s["base"] = s["position"].map(base).fillna(overall)
    s["p_blank"] = (s["blanks"] + PRIOR * s["base"]) / (s["seen"] + PRIOR)
    return s[["player_id", "season", "week", "p_blank", "seen", "base"]], base


def row(d, col):
    n = len(d)
    if n == 0:
        return "none"
    return (f"{d['hit'].mean():>6.1%} on {n:<5} board said {d['wp'].mean():>5.1%}"
            f"  corrected {d[col].mean():>5.1%}  needed {d['need'].mean():>5.1%}")


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    picks, cal, snaps = load(eng)
    picks["game_date"] = pd.to_datetime(picks["game_date"])
    cal["game_date"] = pd.to_datetime(cal["game_date"])
    cal = cal.drop_duplicates("game_date")
    rates, base = blank_rates(snaps, set(picks["player_id"]))

    d = picks.merge(cal, on="game_date", how="left").merge(
        rates, on=["player_id", "season", "week"], how="left")
    lost = int(d["p_blank"].isna().sum())
    d = d.dropna(subset=["p_blank"]).copy()
    d["hit"] = d["hit"].astype(bool)
    d["need"] = implied(d["price_american"])
    over = d["side"] == "over"
    d["adj"] = np.where(over, d["wp"] * (1 - d["p_blank"]),
                        d["p_blank"] + (1 - d["p_blank"]) * d["wp"])

    print(f"{len(d)} graded live picks in {NOW} matched to a snap history, "
          f"{lost} not matched and left out.")
    print("Blank rate for priced players in earlier seasons, by position: "
          + ", ".join(f"{p} {v:.1%}" for p, v in base.items()))

    print("\n1. DOES THE ESTIMATE SEPARATE ANYTHING?")
    bands = [(0, 0.03, "under 3%"), (0.03, 0.08, "3 to 8%"),
             (0.08, 0.15, "8 to 15%"), (0.15, 0.30, "15 to 30%"),
             (0.30, 1.01, "30% and up")]
    for side in ("over", "under"):
        print(f"  {side}s, by chance of a blank")
        for lo, hi, name in bands:
            part = d[(d.side == side) & (d.p_blank >= lo) & (d.p_blank < hi)]
            print(f"    {name:<12}{row(part, 'adj')}")

    print("\n2. IS THE CORRECTED PROBABILITY MORE HONEST?")
    print(f"  {'':<22}{'picks':>7}{'hit':>8}{'board':>8}{'corrected':>11}"
          f"{'Brier board':>13}{'Brier corrected':>17}")
    cuts = [("every pick", d), ("overs", d[over]), ("unders", d[~over]),
            ("catches over, line 0.5", d[over & (d.market_code == "recs")
                                         & (d.line < 1)]),
            ("catches over, line 1.5", d[over & (d.market_code == "recs")
                                         & (d.line > 1) & (d.line < 2)]),
            ("elite", d[d.tier == "elite"]),
            ("elite overs", d[over & (d.tier == "elite")])]
    for name, part in cuts:
        if part.empty:
            continue
        y = part["hit"].astype(float)
        print(f"  {name:<22}{len(part):>7}{y.mean():>8.1%}{part['wp'].mean():>8.1%}"
              f"{part['adj'].mean():>11.1%}{((part['wp'] - y) ** 2).mean():>13.4f}"
              f"{((part['adj'] - y) ** 2).mean():>17.4f}")
    print("  A lower Brier score is better. The corrected column should sit "
          "closer to the hit column than the board column does.")

    print("\n3. WHAT WOULD IT HAVE DONE TO THE TOP TIERS?")
    for tier in ("elite", "strong", "medium"):
        part = d[over & (d.tier == tier)]
        if part.empty:
            continue
        below = part[part["adj"] < part["need"]]
        kept = part[part["adj"] >= part["need"]]
        was = part[part["wp"] < part["need"]]
        print(f"  {tier} overs: {len(part)} picks, {part['hit'].mean():.1%}. "
              f"The board had {len(was)} of them under the rate their price "
              f"needed.")
        print(f"    corrected under the needed rate   {row(below, 'adj')}")
        print(f"    corrected at or above it          {row(kept, 'adj')}")
    print("\nIf the picks the correction marks down lost and the ones it "
          "leaves alone did not, it belongs in the builder ahead of the "
          "ranking. Read it against the counts: under about 500 picks it is "
          "a direction, and the interval on any one band is wide.")


if __name__ == "__main__":
    main()
