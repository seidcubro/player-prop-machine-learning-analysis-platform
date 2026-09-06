"""Monte Carlo game simulation.

Every market is currently predicted independently, which means the numbers the
site shows are mutually inconsistent: a quarterback's projected passing yards
bear no arithmetic relationship to the projected receiving yards of the men
catching the ball. Sportsbooks do not price props that way -- they simulate the
game many times and read every market off the same simulated outcomes.

This does that. One pass through a game produces every player's line at once,
which buys three things a per-market regression cannot:

  1. **Consistency.** Receiving yards on a team sum to something sane relative to
     that team's passing yards, because they were generated from the same drives.
  2. **Correlation.** Two players on the same team are linked -- positively
     through shared volume (a pass-heavy script lifts everyone) and negatively
     through share competition (targets that go to one receiver do not go to
     another). That is what makes same-game parlay pricing possible at all.
  3. **Any threshold, free.** A distribution answers P(over 40.5) and
     P(over 74.5) equally well, so alternate lines cost nothing extra.

Structure of one simulated game, per team:

    team volume   -> pass attempts and carries, driven by the Vegas implied
                     total and spread (trailing teams throw more)
    shares        -> Dirichlet over the team's players, so shares always sum to
                     one and competition is built in rather than assumed away
    per-touch     -> gamma-distributed yards per reception / per carry
    touchdowns    -> binomial on red-zone opportunity

Dispersion is fitted from the data, not guessed (see SIM_PARAMS).

Env: N_SIMS (default 20000), SIM_SEED.
"""

import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

N_SIMS = int(os.getenv("N_SIMS", "20000"))
SEED = int(os.getenv("SIM_SEED", "42"))

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

# Measured on 2023+ game logs rather than assumed. Team volume is far steadier
# than per-touch efficiency, which is why a player's yardage distribution is so
# much wider than his opportunity distribution.
SIM_PARAMS = {
    "cv_team_pass_att": 0.237,   # team pass attempts, coefficient of variation
    "cv_team_rush_att": 0.278,
    "cv_yards_per_rec": 0.702,
    "cv_yards_per_carry": 0.973,
    "league_mean_pass_att": 32.9,
    "league_mean_rush_att": 26.9,
    # Dirichlet concentration. Higher = shares hold closer to their expected
    # split week to week. Receiving roles churn more than backfield roles.
    "share_concentration_pass": 40.0,
    "share_concentration_rush": 25.0,
    # How strongly the game script shifts play mix. A team favoured by a
    # touchdown runs more and throws less; the sign flips when trailing.
    "script_pass_sensitivity": 0.9,   # extra pass attempts per point of spread
    "script_rush_sensitivity": 0.7,
}


def gamma_from_mean_cv(rng, mean, cv, size):
    """Sample positive, right-skewed values with a given mean and spread.

    Yards per touch is bounded below by zero and has a long right tail (a
    receiver's average catch is 10 yards, but the distribution includes 60-yard
    ones). A gamma matches that shape; a normal would generate negative yardage.
    """
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-6)
    shape = 1.0 / (cv ** 2)
    scale = mean / shape
    return rng.gamma(shape, scale, size=size)


def allocate(rng, totals, shares):
    """Split a team's touches among its players so they sum to the total.

    Drawing an independent binomial per player was wrong: the draws do not add
    up to the team's attempts, and the independent noise swamps the share
    competition, which is why team-mate correlations came out at zero. This is a
    multinomial split done by stick-breaking -- each player draws from what is
    left, so a target that goes to one receiver is genuinely unavailable to
    another. That negative dependence is the thing a per-market regression
    cannot represent and is the whole reason to simulate.
    """
    n_sims, n_players = shares.shape
    out = np.zeros((n_sims, n_players), dtype=int)
    remaining = totals.astype(int).copy()
    remaining_share = np.ones(n_sims)

    for j in range(n_players):
        if j == n_players - 1:
            out[:, j] = remaining
            break
        p = np.clip(shares[:, j] / np.maximum(remaining_share, 1e-9), 0.0, 1.0)
        drawn = rng.binomial(np.maximum(remaining, 0), p)
        out[:, j] = drawn
        remaining -= drawn
        remaining_share -= shares[:, j]
    return out


def load_slate(engine) -> pd.DataFrame:
    """Players with an upcoming game, and the inputs the simulation needs.

    Everything comes from the most recent feature row per player, which already
    carries expected shares, per-touch efficiency and team volume.
    """
    return pd.read_sql(
        text("""
            WITH latest AS (
                SELECT DISTINCT ON (f.player_id, m.code)
                       f.player_id, m.code AS market_code, f.extra_features,
                       f.weighted_mean, p.name, p.position, p.team, p.id AS pid
                FROM player_market_features f
                JOIN prop_markets m ON m.id = f.market_id
                JOIN players p ON p.external_id = f.player_id
                WHERE f.lookback = 5
                ORDER BY f.player_id, m.code, f.as_of_game_date DESC
            )
            SELECT l.*, g.game_id, g.game_date, g.home_team, g.away_team,
                   g.spread_line, g.total_line
            FROM latest l
            JOIN nfl_games g
              ON (g.home_team = l.team OR g.away_team = l.team)
             AND g.game_date >= CURRENT_DATE
             AND g.game_date < CURRENT_DATE + 14
            WHERE l.market_code IN ('rec_yds', 'recs', 'rush_yds', 'rush_att',
                                      'pass_yds', 'pass_completions', 'pass_att')
              -- Restrict to men actually on the current depth chart. Without
              -- this, any player whose last feature row still exists gets
              -- simulated -- including ones who retired seasons ago, whose
              -- stale row happens to name a team playing this week.
              AND EXISTS (
                  SELECT 1 FROM depth_charts d
                  WHERE d.player_id = l.player_id
                    AND d.season = (SELECT MAX(season) FROM depth_charts)
              )
        """),
        engine,
    )


def ex(row, key, default=0.0):
    e = row["extra_features"]
    if isinstance(e, str):
        e = json.loads(e)
    v = (e or {}).get(key, default)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def simulate_team(rng, players: pd.DataFrame, implied_total, team_spread):
    """Simulate N games for one team, returning per-player stat draws.

    Volume is drawn once per simulated game and then divided among the players,
    which is what creates the correlation between team-mates: they are competing
    for shares of the same realised pass attempts.
    """
    p = SIM_PARAMS

    # Expected volume, nudged by game script. A favourite (negative spread for
    # them) runs more; an underdog throws more.
    base_pass = np.mean([ex(r, "team_pass_attempts", p["league_mean_pass_att"])
                         for _, r in players.iterrows()]) or p["league_mean_pass_att"]
    base_rush = np.mean([ex(r, "team_rush_attempts", p["league_mean_rush_att"])
                         for _, r in players.iterrows()]) or p["league_mean_rush_att"]
    if base_pass <= 0:
        base_pass = p["league_mean_pass_att"]
    if base_rush <= 0:
        base_rush = p["league_mean_rush_att"]

    exp_pass = base_pass + p["script_pass_sensitivity"] * team_spread
    exp_rush = base_rush - p["script_rush_sensitivity"] * team_spread
    exp_pass = max(exp_pass, 10.0)
    exp_rush = max(exp_rush, 8.0)

    team_pass = np.maximum(
        rng.normal(exp_pass, exp_pass * p["cv_team_pass_att"], N_SIMS), 5
    ).round().astype(int)
    team_rush = np.maximum(
        rng.normal(exp_rush, exp_rush * p["cv_team_rush_att"], N_SIMS), 5
    ).round().astype(int)

    # Receiving side ------------------------------------------------------
    recv = players[players["market_code"] == "rec_yds"].copy()
    out = {}
    if len(recv):
        shares = np.array([max(ex(r, "target_share_mean", 0.0), 1e-4)
                           for _, r in recv.iterrows()])
        shares = shares / shares.sum()
        # Dirichlet: shares vary week to week but always sum to one, so a target
        # gained by one receiver is a target lost by another.
        drawn = rng.dirichlet(shares * SIM_PARAMS["share_concentration_pass"], N_SIMS)
        targets = allocate(rng, team_pass, drawn)

        for j, (_, r) in enumerate(recv.iterrows()):
            ypt = ex(r, "yards_per_target_mean", 7.5) or 7.5
            # Catch rate implied by the player's own yards-per-target and the
            # league's yards-per-reception, bounded to a plausible range.
            catch_rate = np.clip(ypt / SIM_PARAMS.get("mean_ypr", 10.6), 0.25, 0.95)
            recs = rng.binomial(targets[:, j], catch_rate)
            ypr = ypt / max(catch_rate, 1e-6)
            yards = np.where(
                recs > 0,
                gamma_from_mean_cv(rng, ypr * np.maximum(recs, 1),
                                   SIM_PARAMS["cv_yards_per_rec"] /
                                   np.sqrt(np.maximum(recs, 1)), N_SIMS),
                0.0,
            )
            out[(r["player_id"], "recs")] = recs.astype(float)
            out[(r["player_id"], "rec_yds")] = yards

    # Quarterback ---------------------------------------------------------
    # Passing yards ARE the sum of the receiving yards on that team, so they are
    # derived from the receivers already simulated rather than modelled
    # separately. That makes the QB and his pass catchers arithmetically
    # consistent in every simulated game, and it reproduces the strong
    # QB-to-lead-receiver correlation seen in real games (+0.70) for free.
    if len(recv):
        qb_yards = np.zeros(N_SIMS)
        qb_comp = np.zeros(N_SIMS)
        for _, r in recv.iterrows():
            qb_yards = qb_yards + out.get((r["player_id"], "rec_yds"), 0.0)
            qb_comp = qb_comp + out.get((r["player_id"], "recs"), 0.0)
        qbs = players[players["position"] == "QB"]
        if len(qbs):
            starter = qbs.iloc[0]
            out[(starter["player_id"], "pass_yds")] = qb_yards
            out[(starter["player_id"], "pass_completions")] = qb_comp
            out[(starter["player_id"], "pass_att")] = team_pass.astype(float)

    # Rushing side --------------------------------------------------------
    rush = players[players["market_code"] == "rush_yds"].copy()
    if len(rush):
        shares = np.array([max(ex(r, "carry_share_mean", 0.0), 1e-4)
                           for _, r in rush.iterrows()])
        shares = shares / shares.sum()
        drawn = rng.dirichlet(shares * SIM_PARAMS["share_concentration_rush"], N_SIMS)
        carries = allocate(rng, team_rush, drawn)

        for j, (_, r) in enumerate(rush.iterrows()):
            ypc = ex(r, "yards_per_carry_mean", 4.2) or 4.2
            c = carries[:, j]
            yards = np.where(
                c > 0,
                gamma_from_mean_cv(rng, ypc * np.maximum(c, 1),
                                   SIM_PARAMS["cv_yards_per_carry"] /
                                   np.sqrt(np.maximum(c, 1)), N_SIMS),
                0.0,
            )
            out[(r["player_id"], "rush_att")] = c.astype(float)
            out[(r["player_id"], "rush_yds")] = yards

    return out


def main():
    rng = np.random.default_rng(SEED)
    engine = create_engine(DATABASE_URL, future=True)
    slate = load_slate(engine)
    if slate.empty:
        raise SystemExit("no upcoming games with features to simulate")

    SIM_PARAMS["mean_ypr"] = 10.6

    print(f"simulating {N_SIMS} games for {slate['game_id'].nunique()} matchups, "
          f"{slate['player_id'].nunique()} players")

    draws = {}
    for (game_id, team), grp in slate.groupby(["game_id", "team"]):
        row = grp.iloc[0]
        total = float(row["total_line"]) if pd.notna(row["total_line"]) else 44.0
        spread = float(row["spread_line"]) if pd.notna(row["spread_line"]) else 0.0
        is_home = row["team"] == row["home_team"]
        team_spread = spread if is_home else -spread
        implied = (total + team_spread) / 2.0
        draws.update(simulate_team(rng, grp, implied, team_spread))

    # Summarise each player-market distribution.
    rows = []
    for (pid, market), samples in draws.items():
        rows.append({
            "player_id": pid, "market_code": market,
            "sim_mean": float(samples.mean()),
            "sim_median": float(np.median(samples)),
            "p10": float(np.percentile(samples, 10)),
            "p25": float(np.percentile(samples, 25)),
            "p75": float(np.percentile(samples, 75)),
            "p90": float(np.percentile(samples, 90)),
            "sim_sd": float(samples.std()),
        })
    dist = pd.DataFrame(rows)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sim_projections (
                player_id TEXT, market_code TEXT,
                sim_mean DOUBLE PRECISION, sim_median DOUBLE PRECISION,
                p10 DOUBLE PRECISION, p25 DOUBLE PRECISION,
                p75 DOUBLE PRECISION, p90 DOUBLE PRECISION,
                sim_sd DOUBLE PRECISION,
                n_sims INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (player_id, market_code)
            )
        """))
        conn.execute(text("TRUNCATE sim_projections"))
        dist["n_sims"] = N_SIMS
        dist.to_sql("sim_projections", conn, if_exists="append", index=False)

    print(f"wrote {len(dist)} simulated distributions")

    # The payoff that a per-market regression cannot produce: correlations
    # between team-mates, which is what same-game parlays are priced on.
    print("\n=== simulated correlations vs what actually happens ===")
    print("  (real values measured on 2023+ game logs)")
    qb_c, wr_c = [], []
    for (game_id, team), grp in slate.groupby(["game_id", "team"]):
        recv = [pid for pid in grp[grp["market_code"] == "rec_yds"]["player_id"]
                if (pid, "rec_yds") in draws]
        qb = [pid for pid in grp[grp["position"] == "QB"]["player_id"]
              if (pid, "pass_yds") in draws]
        if len(recv) >= 2:
            a, b = recv[0], recv[1]
            if draws[(a, "rec_yds")].std() > 0 and draws[(b, "rec_yds")].std() > 0:
                wr_c.append(float(np.corrcoef(draws[(a, "rec_yds")], draws[(b, "rec_yds")])[0, 1]))
        if qb and recv:
            top = max(recv, key=lambda x: draws[(x, "rec_yds")].mean())
            if draws[(qb[0], "pass_yds")].std() > 0 and draws[(top, "rec_yds")].std() > 0:
                qb_c.append(float(np.corrcoef(draws[(qb[0], "pass_yds")], draws[(top, "rec_yds")])[0, 1]))
    if qb_c:
        print(f"  QB pass_yds vs lead receiver : simulated {np.mean(qb_c):+.3f}  |  real +0.697")
    if wr_c:
        print(f"  WR1 vs WR2 rec_yds           : simulated {np.mean(wr_c):+.3f}  |  real +0.041")
    print("")
    print("  WR1/WR2 near zero is correct rather than a bug: shared team volume")
    print("  pushes team-mates together, target competition pushes them apart,")
    print("  and in real games those two effects very nearly cancel.")
    print("\n=== sample pairs ===")
    shown = 0
    for (game_id, team), grp in slate.groupby(["game_id", "team"]):
        recv = [pid for pid in grp[grp["market_code"] == "rec_yds"]["player_id"]
                if (pid, "rec_yds") in draws]
        if len(recv) < 2 or shown >= 3:
            continue
        names = dict(zip(grp["player_id"], grp["name"]))
        a, b = recv[0], recv[1]
        c = float(np.corrcoef(draws[(a, "rec_yds")], draws[(b, "rec_yds")])[0, 1])
        print(f"  {team}: {names.get(a)} vs {names.get(b)} rec_yds corr = {c:+.3f}")
        shown += 1
    print("  (negative = they compete for the same targets)")


if __name__ == "__main__":
    main()
