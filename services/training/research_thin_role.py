"""Two questions from Thursday night, Commanders at Giants, 12 November 2026.

I posted Van Jefferson over 1.5 catches as an elite pick. He caught none. Two
things were visible beforehand and I did not weigh them:

    thin        he had two games this season. Eight of the ten games behind the
                number were from Tennessee in 2025.
    borrowed    his one good game (7 targets, 5 catches) came the week Treylon
                Burks was out. Burks was back on Thursday.

One pick proves nothing. research_role_expansion.py already asked whether a
changing role can be seen ahead of time and refused the filter twice, because
the picks it wanted to drop were the best on the board. So this does not build
a filter. It asks the narrower thing the record can answer: across every
graded pick, do picks made on a thin or borrowed history hit less often, and
is it the over side that pays for it?

    1. How many games had he played this season before the pick?
    2. How many with the team he was on that day?
    3. Was a team-mate at his position out for his last game and not out for
       this one (the role he showed was partly someone else's)?

Each is cut by side, and again for the elite tier alone.

The second question is about the record itself. Jefferson's pick is not in it.
grade_edges.py grades a pick only when the player has a stat line for the game,
and a receiver who plays and is never thrown to has none. An over on a man who
played and did nothing is a loss. An under is a win. Neither is being counted.
Part two lists every posted pick this season that was never graded and uses
the snap counts to split them into "played, recorded nothing" (should be
graded at zero) and "did not play" (void, correctly left out).

Read-only.

    docker compose run --rm training python research_thin_role.py
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SINCE = int(os.getenv("SINCE_SEASON", "2023"))
NOW = int(os.getenv("SEASON", "2026"))
NORM = "lower(replace(replace({c}, '.', ''), '-', ' '))"


def rate(d):
    n = len(d)
    if n == 0:
        return "      none"
    p = d["hit"].mean()
    half = 1.96 * np.sqrt(p * (1 - p) / n)
    return f"{p:>6.1%} on {n:<6}[{p - half:.1%}, {p + half:.1%}]"


def table(d, col, title):
    print(f"\n  {title}")
    print(f"    {'':<14}{'over':<34}{'under':<34}")
    for key in d[col].cat.categories:
        part = d[d[col] == key]
        print(f"    {str(key):<14}{rate(part[part.side == 'over']):<34}"
              f"{rate(part[part.side == 'under']):<34}")


def history(eng):
    """Every graded pick with what was behind it on the day."""
    picks = pd.read_sql(text("""
        SELECT r.player_id, r.game_date, r.season, r.source, r.market_code,
               r.recommended_side AS side, r.edge_tier AS tier, r.hit
        FROM prop_edge_results r
        WHERE r.hit IS NOT NULL AND r.season >= :since
    """), eng, params={"since": SINCE})
    games = pd.read_sql(text("""
        SELECT player_id, season, week, game_date, team, position
        FROM player_game_stats_app
        WHERE season >= :since - 1 AND season_type = 'REG'
    """), eng, params={"since": SINCE})
    out = pd.read_sql(text("""
        SELECT team, position, season, week, COUNT(*) AS n_out
        FROM injuries
        WHERE report_status IN ('Out', 'Doubtful')
          AND team IS NOT NULL AND position IS NOT NULL
        GROUP BY 1, 2, 3, 4
    """), eng)

    for f in (picks, games):
        f["game_date"] = pd.to_datetime(f["game_date"])
    games = (games.drop_duplicates(["player_id", "game_date"])
                  .sort_values(["player_id", "game_date"]))
    games["games_season"] = games.groupby(["player_id", "season"]).cumcount()
    games["games_team"] = games.groupby(["player_id", "team"]).cumcount()
    games = games.merge(out, on=["team", "position", "season", "week"], how="left")
    games["n_out"] = games["n_out"].fillna(0)
    # A team-mate at his position who was out is one fewer than the count for
    # his position that week when he himself was listed. Good enough: he is
    # rarely Out and on the board in the same week.
    games["out_last"] = games.groupby("player_id")["n_out"].shift(1)
    games["season_last"] = games.groupby("player_id")["season"].shift(1)
    games["borrowed"] = ((games["out_last"] > games["n_out"])
                         & (games["season_last"] == games["season"]))

    d = picks.merge(games[["player_id", "game_date", "games_season",
                           "games_team", "borrowed"]],
                    on=["player_id", "game_date"], how="inner")
    d["hit"] = d["hit"].astype(bool)
    return d, len(picks)


def part_one(eng):
    d, n_all = history(eng)
    print(f"PART ONE. {len(d)} of {n_all} graded picks since {SINCE} matched to "
          f"a game log.")
    d["season_band"] = pd.cut(d["games_season"], [-1, 1, 3, 100],
                              labels=["0 to 1", "2 to 3", "4 or more"])
    d["team_band"] = pd.cut(d["games_team"], [-1, 2, 5, 1000],
                            labels=["0 to 2", "3 to 5", "6 or more"])
    d["borrowed_band"] = pd.Categorical(
        np.where(d["borrowed"], "yes", "no"), categories=["yes", "no"])

    for label, part in (("EVERY PICK", d),
                        ("ELITE ONLY", d[d.tier == "elite"]),
                        (f"{NOW} LIVE ONLY", d[(d.season == NOW)
                                               & (d.source == "live")])):
        print(f"\n{label}: {rate(part)}")
        table(part, "season_band", "games played this season before the pick")
        table(part, "team_band", "games played for this team before the pick")
        table(part, "borrowed_band",
              "a team-mate at his position was out last game and is back")

    thin = d[(d.games_team <= 2) & (d.side == "over")]
    print(f"\n  Overs on a player with two or fewer games for his team, by "
          f"market:")
    for m, part in thin.groupby("market_code"):
        print(f"    {m:<18}{rate(part)}")
    print("\n  A band is only worth acting on if it has about 500 picks, its "
          "interval sits clear of the row beside it, and it says the same "
          "thing for every pick and for the elite tier.")


def part_two(eng):
    print(f"\n\nPART TWO. Picks posted in {NOW} that were never graded.")
    missing = pd.read_sql(text(f"""
        WITH posted AS (
            SELECT DISTINCT ON (nm, d, market_code) *
            FROM (
                SELECT id, {NORM.format(c='player_name')} AS nm, player_name,
                       (commence_time AT TIME ZONE 'America/New_York')::date AS d,
                       market_code, recommended_side AS side,
                       edge_tier AS tier, line
                FROM prop_edges_history
                WHERE commence_time >= :since
                  AND commence_time < now() - interval '3 days'
            ) x
            ORDER BY nm, d, market_code, id DESC
        )
        SELECT p.player_name, p.nm, p.d, p.market_code, p.side, p.tier, p.line
        FROM posted p
        WHERE NOT EXISTS (
            SELECT 1 FROM prop_edge_results r
            WHERE r.market_code = p.market_code
              AND {NORM.format(c='r.player_name')} = p.nm
              AND r.game_date BETWEEN p.d - 1 AND p.d + 1)
    """), eng, params={"since": f"{NOW}-08-01"})
    if missing.empty:
        print("  none.")
        return
    ids = pd.read_sql(text(f"""
        SELECT external_id AS player_id, {NORM.format(c='name')} AS nm
        FROM players WHERE external_id IS NOT NULL
    """), eng)
    weeks = pd.read_sql(text("""
        SELECT DISTINCT game_date, week FROM player_game_stats_app
        WHERE season = :s
    """), eng, params={"s": NOW})
    snaps = pd.read_sql(text("""
        SELECT player_id, week, COALESCE(offense_snaps, 0) AS snaps
        FROM snap_counts WHERE season = :s
    """), eng, params={"s": NOW})

    weeks["game_date"] = pd.to_datetime(weeks["game_date"])
    missing["d"] = pd.to_datetime(missing["d"])
    week_of = {}
    for day in missing["d"].drop_duplicates():
        near = weeks[(weeks.game_date - day).abs() <= pd.Timedelta(days=1)]
        week_of[day] = int(near["week"].iloc[0]) if len(near) else None
    missing["week"] = missing["d"].map(week_of)
    loaded = set(snaps["week"].unique())

    best = (snaps.merge(ids, on="player_id")
                 .groupby(["nm", "week"])["snaps"].max().reset_index())
    m = missing.merge(best, on=["nm", "week"], how="left")

    def status(r):
        if pd.isna(r["week"]) or r["week"] not in loaded:
            return "snap counts not loaded for that week"
        if pd.isna(r["snaps"]) or r["snaps"] <= 0:
            return "did not play (void)"
        return "played, no stat line"
    m["status"] = m.apply(status, axis=1)

    print(f"  {len(m)} posted props have no grade.")
    for s, part in m.groupby("status"):
        print(f"    {s:<40}{len(part):>5}   overs {int((part.side == 'over').sum())}"
              f", unders {int((part.side == 'under').sum())}")
    played = m[m.status == "played, no stat line"]
    if played.empty:
        return
    # At zero an over loses and an under wins, whatever the line.
    played = played.assign(hit=played["side"] == "under")
    print(f"\n  Graded at zero, the {len(played)} that played would add:")
    for tier, part in played.groupby("tier"):
        o, u = part[part.side == "over"], part[part.side == "under"]
        print(f"    {tier:<8}{len(o):>4} losing overs, {len(u):>4} winning unders")
    print("\n  The elite and strong ones:")
    top = played[played.tier.isin(["elite", "strong"])].sort_values("d")
    for r in top.itertuples():
        print(f"    {r.d.date()}  {r.player_name:<24}{r.market_code:<18}"
              f"{r.side:<6}{r.line:>6}  {r.tier:<7}{int(r.snaps):>3} snaps")
    print("\n  If the overs outnumber the unders, the posted record flatters "
          "the over side by that many losses, and the fix is to grade a player "
          "who took a snap at zero in grade_edges.py.")


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    part_one(eng)
    part_two(eng)


if __name__ == "__main__":
    main()
