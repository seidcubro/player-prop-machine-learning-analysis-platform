"""Does the board pass a football sanity check, whatever the metrics say?

`audit_freshness.py` asks whether the numbers were computed from current data.
This asks a different question: whether they are believable to somebody who
watches football. Those are not the same question and the second one has been
failing while the first one passed.

The case for a test like this is made best by Mecha (2026), whose xScore paper
pre-specifies canonical NFL scenarios with expected probability ranges before
looking at any model output, and treats a violation as invalidating:

    "A model can achieve a low Brier score on average while producing
    implausible probability estimates in specific high-leverage situations
    that are underrepresented in the test data."

That is precisely what happened here. Week 3 of 2026: the point models were
within 1% of calibrated when bucketed by prediction, every freshness check
passed, and the board projected Sam Darnold for 20.8 pass attempts as
Seattle's healthy listed starter, Lamar Jackson for 24 rushing yards against a
38.5 line, and TreVeyon Henderson for 43.8 against his own 76-yard average. No
aggregate metric noticed, because on twenty thousand rows those are a rounding
error. They are not a rounding error on a board with ninety picks.

So: ranges written down in advance, from football rather than from our own
output. Each one is deliberately wide enough that a reasonable projection
passes and only an indefensible one fails. A starting quarterback throwing 20
times is not a bold projection, it is a broken one.

    [1]  a healthy listed starting QB throws 24 to 45 times
    [2]  a healthy listed starting QB is projected 170 to 320 passing yards
    [3]  a healthy listed RB1 gets 8 to 24 carries
    [4]  a healthy listed WR1 catches 3 to 9
    [5]  nobody is projected above 1.25x his own career best
    [6]  a player ruled out is not projected at all
    [7]  within a team, the listed RB1 is projected at least as many carries
         as the RB2
    [8]  within a team, the listed QB1 throws more than the QB2
    [9]  with three or more games this season and no change of role, a
         projection is not less than half nor more than double the player's
         own season average

Checks [7] and [8] are ordering checks, which Mecha also leans on: "the
directional ordering across scenarios remains correct, which is the more
structurally important check". An ordering violation needs no threshold to
argue about. If the chart says a man is the starter and the model says he is
the backup, one of them is wrong and it is not the chart.

Check [9] is the only one that refers to our own data rather than to football,
and it is bounded on both sides on purpose. A model that merely reproduced the
season average would pass it trivially and fail [1] through [8] the moment a
role changed, which is the whole reason the others are there.

Exits non-zero on a violation so the pipeline stops rather than publishing.
Prints every violation with the numbers, because a count alone cannot be acted
on. FAIL_ON_PLAUSIBILITY=0 downgrades it to a report for a first run, when the
question is how bad things are rather than whether to ship.
"""

import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
FAIL = os.getenv("FAIL_ON_PLAUSIBILITY", "1") != "0"

# Written from football, before reading the board. Wide on purpose: these are
# bounds on the indefensible, not forecasts.
STARTER_RANGES = {
    ("QB", "pass_att"): (24.0, 45.0),
    ("QB", "pass_yds"): (170.0, 320.0),
    ("RB", "rush_att"): (8.0, 24.0),
    ("WR", "recs"): (3.0, 9.0),
}
# How far a projection may sit from the player's own season average once he has
# enough of a season to have one and his role has not moved.
SEASON_LO, SEASON_HI = 0.5, 2.0
# Two, not three, and the reason is the whole point of this file.
#
# It was three, which sounded conservative and made checks [1-4], [7-8] and [9]
# report "all clear" on the Week 3 2026 board. Nobody has three games in Week 3:
# 917 players had exactly two and none had more, so every check that needed a
# season average silently checked nothing and the audit passed by examining an
# empty set. An inert test that prints a pass is worse than no test, because the
# pass gets believed. Two games is also enough to say who is getting the ball,
# which is all these checks ask of it.
MIN_GAMES_FOR_SEASON_CHECK = 2
CAREER_MAX_MULTIPLE = 1.25
# A career best means nothing on a career of two appearances. Kyle Trask's best
# is nine attempts because he has barely played, so projecting him for thirteen
# as a starter is correct rather than absurd, and the first version of [5]
# flagged it along with Frank Gore Jr. exceeding a career best of one carry.
MIN_CAREER_GAMES = 8
# A floor in the units of the market, so a small absolute number cannot trip a
# ratio test however large the ratio looks. Used by [5] and by [9].
#
# [9] without this reported 78 violations on the Week 3 board and 73 of them were
# sampling noise: Dawson Knox at 20.1 receiving yards against a "season average"
# of 6.0, which over two games is one twelve-yard afternoon and one zero. A
# factor-of-two band is a reasonable demand of a real average and a meaningless
# one of a number like that. With the floor, what survives is the handful where
# the disagreement is about a player who is actually producing.
MARKET_FLOOR = {
    "pass_att": 15.0, "pass_yds": 120.0, "pass_completions": 10.0,
    "rush_att": 6.0, "rush_yds": 30.0, "recs": 3.0, "rec_yds": 30.0,
}

violations: list[str] = []
checked = 0


def report(check: str, msg: str) -> None:
    violations.append(f"[{check}] {msg}")


def main() -> None:
    global checked
    eng = create_engine(DATABASE_URL, future=True)

    proj = pd.read_sql(text("""
        SELECT pp.player_name, pp.player_id, pp.team, pp.position,
               pp.market_code, pp.projection, pp.depth_rank, pp.is_starter,
               pp.game_date
        FROM player_projections pp
        WHERE pp.projection IS NOT NULL
    """), eng)
    if proj.empty:
        print("no projections on the board; nothing to check")
        return

    # Who is ruled out, for [6], and who has moved on the chart, for [9].
    out = pd.read_sql(text("""
        SELECT DISTINCT i.player_id
        FROM injuries i
        WHERE i.report_status IN ('Out', 'Doubtful')
          AND i.season = (SELECT MAX(season) FROM injuries)
          AND i.week = (SELECT MAX(week) FROM injuries
                         WHERE season = (SELECT MAX(season) FROM injuries))
    """), eng)
    ruled_out = set(out["player_id"])

    # This season's own production per player and market, and the career best.
    hist = pd.read_sql(text("""
        SELECT g.player_id, g.season,
               COALESCE(g.attempts, 0)::float8        AS pass_att,
               COALESCE(g.passing_yards, 0)::float8   AS pass_yds,
               COALESCE(g.completions, 0)::float8     AS pass_completions,
               COALESCE(g.carries, 0)::float8         AS rush_att,
               COALESCE(g.rushing_yards, 0)::float8   AS rush_yds,
               COALESCE(g.receptions, 0)::float8      AS recs,
               COALESCE(g.receiving_yards, 0)::float8 AS rec_yds
        FROM player_game_stats_app g
        WHERE g.season_type = 'REG'
    """), eng)
    current_season = int(hist["season"].max()) if not hist.empty else None
    cur = hist[hist["season"] == current_season]
    markets = ["pass_att", "pass_yds", "pass_completions",
               "rush_att", "rush_yds", "recs", "rec_yds"]
    season_mean = cur.groupby("player_id")[markets].mean()
    season_n = cur.groupby("player_id").size()
    career_max = hist.groupby("player_id")[markets].max()
    career_n = hist.groupby("player_id").size()
    print(f"season {current_season}: "
          f"{int((season_n >= MIN_GAMES_FOR_SEASON_CHECK).sum())} of "
          f"{len(season_n)} players have at least "
          f"{MIN_GAMES_FOR_SEASON_CHECK} games, which is what the role checks "
          f"need to fire")

    # ---------------------------------------------------------------- [1]-[4]
    #
    # Who the starter is has to be answered before it can be checked, and
    # depth_rank alone does not answer it. It is MIN(depth_team) across every
    # position grouping a player appears in, so a fullback listed first among
    # fullbacks and a gadget receiver listed first among returners both come
    # back as "1": the first version of this check flagged Patrick Ricard and
    # Braxton Berrios as a failing RB1 and WR1, and gave Philadelphia two RB1s.
    #
    # So the lead man at a position is the one with the most volume this season
    # among that team's players who are on the board, which is a fact about who
    # has the job rather than an opinion about what he will do. The range
    # itself still comes from football. Requiring three games keeps a Week 2
    # sample from anointing anybody.
    VOLUME_STAT = {"QB": "pass_att", "RB": "rush_att", "WR": "recs"}
    for (pos, market), (lo, hi) in STARTER_RANGES.items():
        rows = proj[(proj["position"] == pos)
                    & (proj["market_code"] == market)
                    & (~proj["player_id"].isin(ruled_out))]
        vol = VOLUME_STAT[pos]
        for tm, g in rows.groupby("team"):
            cands = []
            for _, r in g.iterrows():
                pid = r["player_id"]
                if pid not in season_mean.index:
                    continue
                if int(season_n.get(pid, 0)) < MIN_GAMES_FOR_SEASON_CHECK:
                    continue
                cands.append((float(season_mean.loc[pid, vol]), r))
            if not cands:
                continue
            lead_vol, r = max(cands, key=lambda c: c[0])
            # A team whose leading man at a position is barely used does not
            # have a starter at it in any meaningful sense: a committee, or a
            # position group the board barely covers. Nothing to assert.
            if lead_vol < {"QB": 15.0, "RB": 6.0, "WR": 2.5}[pos]:
                continue
            checked += 1
            p = float(r["projection"])
            if not (lo <= p <= hi):
                report("1-4", f"{r['player_name']} ({pos}1, {tm}, leads the "
                              f"team at {lead_vol:.1f} {vol}/game) {market} "
                              f"{p:.1f}, outside {lo:g} to {hi:g}")

    # ------------------------------------------------------------------- [5]
    for _, r in proj.iterrows():
        m = r["market_code"]
        if m not in markets or r["player_id"] not in career_max.index:
            continue
        if int(career_n.get(r["player_id"], 0)) < MIN_CAREER_GAMES:
            continue
        best = float(career_max.loc[r["player_id"], m])
        if best <= 0 or best < MARKET_FLOOR.get(m, 0.0):
            continue
        checked += 1
        p = float(r["projection"])
        if p > best * CAREER_MAX_MULTIPLE:
            report("5", f"{r['player_name']} {m} {p:.1f} exceeds "
                        f"{CAREER_MAX_MULTIPLE:g}x his career best of {best:.1f}")

    # ------------------------------------------------------------------- [6]
    for _, r in proj[proj["player_id"].isin(ruled_out)].iterrows():
        checked += 1
        report("6", f"{r['player_name']} is Out or Doubtful and carries a "
                    f"{r['market_code']} projection of {float(r['projection']):.1f}")

    # ---------------------------------------------------------------- [7],[8]
    #
    # Ordering, which needs no threshold to argue about. Ranked by this
    # season's volume for the same reason as above, so this asks whether the
    # model agrees with itself about who has the job: the man getting the
    # touches should be projected for more of them than the man behind him.
    for market, pos, vol, label in (
        ("rush_att", "RB", "rush_att", "the lead back's carries"),
        ("pass_att", "QB", "pass_att", "the starting QB's attempts"),
        ("recs", "WR", "recs", "the lead receiver's catches"),
    ):
        sub = proj[(proj["market_code"] == market) & (proj["position"] == pos)
                   & (~proj["player_id"].isin(ruled_out))]
        for tm, g in sub.groupby("team"):
            ranked = []
            for _, r in g.iterrows():
                pid = r["player_id"]
                if pid not in season_mean.index:
                    continue
                if int(season_n.get(pid, 0)) < MIN_GAMES_FOR_SEASON_CHECK:
                    continue
                ranked.append((float(season_mean.loc[pid, vol]), r))
            if len(ranked) < 2:
                continue
            ranked.sort(key=lambda c: -c[0])
            (v1, r1), (v2, r2) = ranked[0], ranked[1]
            # Only when the season says one of them clearly has the job. A
            # genuine committee splitting work evenly carries no ordering to
            # violate, and demanding one would be inventing a fact.
            if v1 < v2 * 1.5 or v1 <= 0:
                continue
            checked += 1
            p1, p2 = float(r1["projection"]), float(r2["projection"])
            if p1 < p2:
                report("7-8", f"{tm}: {label} inverted. "
                              f"{r1['player_name']} {p1:.1f} projected behind "
                              f"{r2['player_name']} {p2:.1f}, though he leads "
                              f"{v1:.1f} to {v2:.1f} per game this season")

    # ------------------------------------------------------------------- [9]
    for _, r in proj.iterrows():
        m = r["market_code"]
        pid = r["player_id"]
        if m not in markets or pid not in season_mean.index:
            continue
        if int(season_n.get(pid, 0)) < MIN_GAMES_FOR_SEASON_CHECK:
            continue
        if pid in ruled_out:
            continue
        avg = float(season_mean.loc[pid, m])
        if avg <= 0 or avg < MARKET_FLOOR.get(m, 0.0):
            continue
        checked += 1
        p = float(r["projection"])
        if p < avg * SEASON_LO or p > avg * SEASON_HI:
            report("9", f"{r['player_name']} {m} {p:.1f} against a season "
                        f"average of {avg:.1f} over "
                        f"{int(season_n.get(pid, 0))} games")

    # ---------------------------------------------------------------- verdict
    print(f"plausibility: {checked} assertion(s) checked on "
          f"{len(proj)} projection(s)")
    if not violations:
        print("all clear: the board is football-plausible on every check")
        return

    by_check: dict[str, int] = {}
    for v in violations:
        key = v.split("]")[0].lstrip("[")
        by_check[key] = by_check.get(key, 0) + 1
    print(f"\n{len(violations)} violation(s): "
          + ", ".join(f"[{k}] x{n}" for k, n in sorted(by_check.items())))
    for v in violations:
        print(f"  {v}")

    print("\nWhat each check means:\n"
          "  [1-4] a healthy listed starter projected outside the range a\n"
          "        starter at his position actually produces\n"
          "  [5]   projected above his own career best by a wide margin\n"
          "  [6]   projected at all while ruled out\n"
          "  [7-8] projected behind the man he is listed ahead of\n"
          "  [9]   projected far from his own production this season, with\n"
          "        enough of a season behind him for that to be a real\n"
          "        disagreement rather than a small sample")

    if FAIL:
        sys.exit(1)
    print("\nFAIL_ON_PLAUSIBILITY=0, so reporting without failing.")


if __name__ == "__main__":
    main()
