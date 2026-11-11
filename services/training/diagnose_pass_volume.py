"""Why do the passing numbers read low?

Through nine weeks of 2026 the median I publish sat under what the quarterback
went on to do in eight weeks of nine:

    pass_att           2.7 attempts low
    pass_completions   2.1 completions low
    pass_yds          19.5 yards low

and the board took the under on 84 to 88% of those props while they went over
half the time. Moving the number by its own running miss does not buy accuracy
on the side (season_review.py: 51.2% against 50.6%), so this is not a lost
edge. It is a wrong number on the page, the same way every week, and a number
that is wrong the same way every week has a cause.

Three suspects, and this splits the miss by each so the cause is the one whose
split carries it:

    the window    the model reads a quarterback's last five games. If one of
                  those was a relief appearance or a game he left early, five
                  attempts sit in an average that should be thirty-three. Sam
                  Darnold went into a start with a baseline of two.
    last season   a quarterback with fewer than five games this season has
                  last December in his window (research_season_boundary.py).
    the top       a model fitted on every quarterback row pulls the busiest
                  starters toward the middle. If so the miss grows with the
                  line and is there even on a clean window.

And one thing to try against it. For each prop, the plain average of his last
five games that were real starts (ten or more attempts), nothing else. If that
lands closer to the outcome than the model does, the fix is to hold the model
toward it, which thin_sample.py already knows how to do.

Read-only.

    docker compose run --rm training python diagnose_pass_volume.py
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SEASON = int(os.getenv("SEASON", "2026"))
START = float(os.getenv("START_ATTEMPTS", "10"))
STAT = {"pass_att": "attempts", "pass_completions": "completions",
        "pass_yds": "passing_yards"}


def windows(props, games):
    """For each prop, what his last five games before it looked like.

    Split out so it can be run on a made-up season.
    """
    season_start = pd.Timestamp(f"{SEASON}-08-01")
    by_player = {pid: g.sort_values("game_date")
                 for pid, g in games.groupby("player_id")}
    rows = []
    for r in props.itertuples():
        g = by_player.get(r.player_id)
        prior = g[g["game_date"] < r.game_date] if g is not None else None
        if prior is None or prior.empty:
            rows.append(("no history", np.nan))
            continue
        last5 = prior.tail(5)
        starts = prior[prior["attempts"] >= START].tail(5)
        naive = (float(starts[STAT[r.market_code]].mean())
                 if len(starts) >= 2 else np.nan)
        if (last5["game_date"] < season_start).any():
            kind = "reaches last season"
        elif (last5["attempts"] < START).any():
            kind = "holds a non-start"
        elif len(last5) < 5:
            kind = "under five games"
        else:
            kind = "five clean starts"
        rows.append((kind, naive))
    out = props.copy()
    out["window"] = [k for k, _ in rows]
    out["naive"] = [n for _, n in rows]
    return out


def line(label, s):
    if len(s) < 5:
        return f"  {label:<22}{len(s):>6}   too few"
    miss = s["actual"] - s["p50"]
    return (f"  {label:<22}{len(s):>6}{miss.median():>+12.2f}"
            f"{(s['p50'] - s['line']).median():>+14.2f}"
            f"{(s['actual'] - s['line']).median():>+16.2f}"
            f"{(miss > 0).mean():>13.0%}")


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    props = pd.read_sql(text("""
        SELECT player_id, player_name, game_date, market_code, line, actual,
               projection_median AS p50, projection
        FROM prop_edge_results
        WHERE source = 'live' AND game_date >= :since
          AND market_code IN ('pass_att', 'pass_completions', 'pass_yds')
          AND actual IS NOT NULL AND line IS NOT NULL
          AND projection_median IS NOT NULL
    """), eng, params={"since": f"{SEASON}-08-01"})
    if props.empty:
        raise SystemExit("no graded live passing props")
    props = props.drop_duplicates(
        subset=["player_id", "game_date", "market_code", "line"])
    props["game_date"] = pd.to_datetime(props["game_date"])

    games = pd.read_sql(text("""
        SELECT player_id, game_date, attempts, completions, passing_yards
        FROM player_game_stats_app
        WHERE game_date >= :since AND attempts IS NOT NULL AND attempts > 0
    """), eng, params={"since": f"{SEASON - 1}-08-01"})
    games["game_date"] = pd.to_datetime(games["game_date"])
    for c in ("attempts", "completions", "passing_yards"):
        games[c] = pd.to_numeric(games[c], errors="coerce")
    known = props["player_id"].isin(set(games["player_id"])).mean()
    print(f"{len(props):,} graded passing props, {known:.0%} matched to a "
          f"game log by player id")
    if known < 0.5:
        print("  under half matched, so the ids in the record and the game "
              "log are not the same kind. Nothing below can be trusted.")
    print()

    d = windows(props, games)
    # A quarterback who barely played is not a miss, he is a prop that should
    # not have been graded as a start.
    played = d.merge(
        games[["player_id", "game_date", "attempts"]].rename(
            columns={"attempts": "att_that_day"}),
        on=["player_id", "game_date"], how="left")
    d = played[played["att_that_day"].fillna(0) >= START].copy()
    print(f"{len(d):,} of them in games he threw {START:.0f} or more passes. "
          f"All figures are medians, in the market's own units.")
    print()

    head = (f"  {'':<22}{'props':>6}{'actual-ours':>12}{'ours-line':>14}"
            f"{'actual-line':>16}{'we were low':>13}")
    for m in STAT:
        s = d[d["market_code"] == m]
        if s.empty:
            continue
        print(m.upper())
        print(head)
        print(line("all", s))
        print("  by what his last five games held:")
        for kind in ("five clean starts", "under five games",
                     "holds a non-start", "reaches last season", "no history"):
            print("  " + line(kind, s[s["window"] == kind]))
        print("  by the size of the line:")
        try:
            third = pd.qcut(s["line"], 3, labels=["low third", "middle third",
                                                  "top third"])
            for name in ("low third", "middle third", "top third"):
                print("  " + line(name, s[third == name]))
        except ValueError:
            print("    lines too alike to split")
        n = s[s["naive"].notna()]
        if len(n) >= 20:
            ours = (n["actual"] - n["p50"]).abs()
            plain = (n["actual"] - n["naive"]).abs()
            book = (n["actual"] - n["line"]).abs()
            half = 0.5 * n["p50"] + 0.5 * n["naive"]
            print(f"  against the plain average of his last five starts, on "
                  f"{len(n)} props:")
            print(f"    mean miss   ours {ours.mean():.2f}   plain "
                  f"{plain.mean():.2f}   half and half "
                  f"{(n['actual'] - half).abs().mean():.2f}   the line "
                  f"{book.mean():.2f}")
            print(f"    median of actual minus each   ours "
                  f"{(n['actual'] - n['p50']).median():+.2f}   plain "
                  f"{(n['actual'] - n['naive']).median():+.2f}   half and half "
                  f"{(n['actual'] - half).median():+.2f}")
            for label, est in (("ours", n["p50"]), ("plain", n["naive"]),
                               ("half and half", half)):
                side = est > n["line"]
                ok = n[est != n["line"]]
                right = (side[ok.index] == (ok["actual"] > ok["line"]))
                right = right[ok["actual"] != ok["line"]]
                print(f"    side right taking {label:<14}{right.mean():.1%} "
                      f"of {len(right)}")
        print()

    print("How to read it. If 'five clean starts' is as low as the rest, the "
          "window is not the cause. If the miss grows down the line thirds, "
          "the model is pulling starters to the middle. If plain beats ours on "
          "mean miss, holding the model toward it is the fix.")


if __name__ == "__main__":
    main()
