"""Hand an absent player's volume to the teammates who inherit it.

The models project a player from his own last five games, so they cannot see a
teammate's absence. When Seattle ruled out Sam Darnold for Week 2, Drew Lock was
projected for 14.5 pass attempts and 124.7 passing yards, a backup's numbers,
while the book priced him as the starter at 204.5. The board could only withhold
the pick.

This measures who inherits and by how much, and adjusts the projection.

Volume comes in three pools, one per kind of market: targets (receptions and
receiving yards and touchdowns), carries (rushing), and pass attempts (passing).
For each pool a player has a trailing share of his team's volume. When an
established player is ruled out, the share he leaves is handed on:

    first, a fraction lam_s goes to his named successor, the next healthy player
    at his position on the current depth chart. Quarterbacks and running backs,
    where one person takes over the job.

    the rest, scaled by an absorption rate lam, is split across the teammates
    expected to play, weighted by their own trailing shares and more heavily
    toward the absent player's position (gamma).

A share cannot pass 100%, so every predicted share is clipped to [0, 1]. That
clip is what makes the quarterback case work: the fitted successor fraction
runs above 1 and the clip turns "inherits it all" into exactly all.

Fitted and validated in fit_vacated_volume.py, on seasons before the last
complete one and scored on that one, with the same rule as every other
correction here: a pool that does not beat leaving shares unchanged is not
applied. On 2025, the successor's share of pass attempts was predicted to within
0.033 against 0.343 unchanged; carries 0.151 against 0.185.

A share becomes a projection as a level, in volume, not as a ratio:

    implied = new share * team volume * the player's rate
    new projection = the larger of the model's projection and implied

A level, not an increment. The first version added the inherited volume on top
of the model's projection and gave Drew Lock 36 pass attempts, against a team
that averages 29: the model had already credited him with 14.5, more than his
26% trailing share implies, because he finished Week 1 in relief. Adding on top
counted that twice. Raising to the implied level counts it once, and for a
teammate who inherits a little it comes to the same thing as adding.

The rate is his own per-opportunity production (completion rate, yards per
attempt, yards per carry, catch rate, yards per target, touchdowns per
opportunity), pulled toward his position's average when his sample is small.
A ratio would explode on exactly the players this is for: a backup with a 3%
share taking over a 95% one is a 30x ratio applied to a number built on mop-up
snaps.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

POOLS = {
    # pool: (positions drawing from it, share that makes a player established
    #        enough for his absence to vacate anything)
    "targets": (("WR", "TE", "RB", "FB"), 0.10),
    "carries": (("RB", "FB", "QB", "WR"), 0.20),
    "attempts": (("QB",), 0.50),
}
SUCCESSOR_POSITIONS = ("QB", "RB")
TRAIL = 5
MIN_PRIOR = 2

# Which pool drives each market, and which per-opportunity rate turns volume
# into that market's number. None means the market is the volume itself.
MARKETS = {
    "pass_att": ("attempts", None),
    "pass_completions": ("attempts", ("completions", "attempts")),
    "pass_yds": ("attempts", ("passing_yards", "attempts")),
    "pass_td": ("attempts", ("passing_tds", "attempts")),
    "rush_att": ("carries", None),
    "rush_yds": ("carries", ("rushing_yards", "carries")),
    "rush_td": ("carries", ("rushing_tds", "carries")),
    "recs": ("targets", ("receptions", "targets")),
    "rec_yds": ("targets", ("receiving_yards", "targets")),
    "rec_td": ("targets", ("receiving_tds", "targets")),
}

# Pseudo-opportunities of the position average mixed into a player's own rate.
# A backup quarterback's yards per attempt on 40 mop-up throws is not his rate
# as a starter; 150 attempts of the average pulls it most of the way home.
PRIOR_N = {"attempts": 150.0, "carries": 60.0, "targets": 40.0}

# A projection is never multiplied by more than this. The largest real case is a
# backup quarterback taking the job, about 2.5 to 3x on passing yards.
MAX_FACTOR = 5.0

ARTIFACT = "vacated_volume.json"


# ---------------------------------------------------------------- shared maths

def with_shares(df: pd.DataFrame, stat: str) -> pd.DataFrame:
    """Each player-game's share of team volume, before and after that game."""
    tot = df.groupby(["season", "week", "team"])[stat].transform("sum")
    d = df.assign(share=np.where(tot > 0, df[stat] / tot.replace(0, np.nan), 0.0),
                  team_total=tot)
    g = d.groupby(["player_id", "team"], sort=False)["share"]
    d["trail"] = g.transform(
        lambda s: s.shift(1).rolling(TRAIL, min_periods=MIN_PRIOR).mean())
    d["trail_after"] = g.transform(
        lambda s: s.rolling(TRAIL, min_periods=MIN_PRIOR).mean())
    return d


def predict_share(trail0, same_pos, succ_vac, vacated, succ_vac_game, gid,
                  lam, gamma, lam_s):
    """Method D: successor first, then a weighted split, clipped to [0, 1].

    All arguments are aligned arrays over the teammates of one or more games;
    gid numbers the games so the split sums within each one.
    """
    direct = lam_s * succ_vac
    remaining = np.clip(vacated - succ_vac_game * lam_s, 0.0, None)
    w = trail0 * (1.0 + gamma * same_pos)
    wsum = np.bincount(gid, weights=w)[gid]
    split = np.where(wsum > 0, w / np.where(wsum > 0, wsum, 1.0), 0.0)
    return np.clip(trail0 + direct + lam * remaining * split, 0.0, 1.0)


def load_params(artifact_dir: Path) -> dict:
    """Accepted pools only. A missing or empty artifact adjusts nothing."""
    path = Path(artifact_dir) / ARTIFACT
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {pool: p for pool, p in data.get("pools", {}).items() if p.get("accepted")}


# ---------------------------------------------------------------- serving

class VacatedVolume:
    """Current-week shares, absences and rates, ready to adjust projections.

    Built once per build from the box scores, the current injury report and
    the current depth chart. adjust() is then a dictionary lookup.
    """

    def __init__(self, engine, ctx: dict, params: dict):
        self.params = params
        self.adjustments: dict[tuple[str, str], dict] = {}
        self.notes: dict[str, str] = {}
        if not params:
            return

        games = pd.read_sql(
            text(
                """
                SELECT player_id, season, week, game_date, team, position,
                       COALESCE(targets, 0)         AS targets,
                       COALESCE(carries, 0)         AS carries,
                       COALESCE(attempts, 0)        AS attempts,
                       COALESCE(completions, 0)     AS completions,
                       COALESCE(passing_yards, 0)   AS passing_yards,
                       COALESCE(passing_tds, 0)     AS passing_tds,
                       COALESCE(rushing_yards, 0)   AS rushing_yards,
                       COALESCE(rushing_tds, 0)     AS rushing_tds,
                       COALESCE(receptions, 0)      AS receptions,
                       COALESCE(receiving_yards, 0) AS receiving_yards,
                       COALESCE(receiving_tds, 0)   AS receiving_tds
                FROM player_game_stats_app
                WHERE season_type = 'REG' AND team IS NOT NULL
                  AND game_date >= CURRENT_DATE - 600
                """
            ),
            engine,
        ).sort_values(["player_id", "game_date"]).reset_index(drop=True)
        if games.empty:
            return

        depth = pd.read_sql(
            text(
                """
                SELECT DISTINCT ON (player_id) player_id, team, position, rank
                FROM (
                    SELECT player_id, team, position, season, week,
                           MIN(depth_team) AS rank
                    FROM depth_charts
                    WHERE depth_team IS NOT NULL
                    GROUP BY player_id, team, position, season, week
                ) d
                ORDER BY player_id, season DESC, week DESC
                """
            ),
            engine,
        )

        inj = ctx.get("injuries")
        ruled_out = set()
        if inj is not None and len(inj):
            ruled_out = {
                pid for pid, st in inj["report_status"].items()
                if str(st or "").strip() in ("Out", "Doubtful")
            }

        # Each player's current team is his most recent one.
        latest = games.drop_duplicates("player_id", keep="last").set_index("player_id")
        rates = self._rates(games)

        for pool, (positions, threshold) in POOLS.items():
            if pool not in params:
                continue
            p = params[pool]
            d = with_shares(games, pool)
            # His trailing share now: the mean of his last TRAIL games for his
            # current team, including the most recent.
            cur = d.merge(latest[["team"]].rename(columns={"team": "cur_team"}),
                          left_on="player_id", right_index=True)
            cur = cur[cur["team"] == cur["cur_team"]]
            cur = cur.sort_values(["player_id", "game_date"])
            lastk = cur.groupby("player_id").tail(TRAIL)
            agg = lastk.groupby("player_id")["share"].agg(["mean", "count"])
            now = agg["mean"].where(agg["count"] >= MIN_PRIOR).rename("trail")
            team_vol = (cur.drop_duplicates(["team", "season", "week"])
                           .sort_values(["team", "game_date"])
                           .groupby("team").tail(TRAIL)
                           .groupby("team")["team_total"].mean())

            people = latest[["team", "position"]].join(now, how="inner")
            for team, roster in people.groupby("team"):
                out = roster[roster.index.isin(ruled_out)
                             & (roster["trail"] >= threshold)]
                if out.empty:
                    continue
                active = roster[~roster.index.isin(ruled_out)
                                & roster["position"].isin(positions)].copy()
                if active.empty:
                    continue
                vacated = float(out["trail"].sum())
                out_pos = out.sort_values("trail").iloc[-1]["position"]

                # Named successors, from the current chart, for QBs and RBs.
                active["succ_vac"] = 0.0
                for pid, row in out.iterrows():
                    if row["position"] not in SUCCESSOR_POSITIONS:
                        continue
                    chart = depth[(depth["team"] == team)
                                  & (depth["position"] == row["position"])
                                  & ~depth["player_id"].isin(ruled_out)
                                  & (depth["player_id"] != pid)]
                    if chart.empty:
                        continue
                    succ = chart.sort_values("rank").iloc[0]["player_id"]
                    if succ in active.index:
                        active.loc[succ, "succ_vac"] += float(row["trail"])
                    else:
                        # Successor with no recent share for this team, the
                        # Drew Lock case at its most extreme: he has to be in
                        # the pool at zero to inherit anything.
                        pos = row["position"]
                        extra = pd.DataFrame(
                            {"team": [team], "position": [pos], "trail": [0.0],
                             "succ_vac": [float(row["trail"])]}, index=[succ])
                        active = pd.concat([active, extra])

                trail0 = active["trail"].fillna(0.0).to_numpy()
                same = (active["position"] == out_pos).astype(float).to_numpy()
                sv = active["succ_vac"].to_numpy()
                new = predict_share(
                    trail0, same, sv, vacated, np.full(len(active), sv.sum()),
                    np.zeros(len(active), dtype=int),
                    p["lam"], p["gamma"], p["lam_s"])
                tv = float(team_vol.get(team, np.nan))
                if not np.isfinite(tv) or tv <= 0:
                    continue
                names = ", ".join(sorted(out.index))
                for (pid, _), s_old, s_new in zip(active.iterrows(), trail0, new):
                    extra_vol = (s_new - s_old) * tv
                    if extra_vol <= 0.05:
                        continue
                    self.adjustments[(pid, pool)] = {
                        "extra_volume": float(extra_vol),
                        "team_volume": tv,
                        "share_before": float(s_old),
                        "share_after": float(s_new),
                        "position": active.loc[pid, "position"],
                        "rates": rates.get(pid, {}),
                    }
                    self.notes[pid] = f"inherits volume from {names}"

    @staticmethod
    def _rates(games: pd.DataFrame) -> dict:
        """Per-opportunity rates over the last 16 games, shrunk to position."""
        recent = games.groupby("player_id", sort=False).tail(16)
        sums = recent.groupby("player_id").sum(numeric_only=True)
        pos = games.drop_duplicates("player_id", keep="last").set_index("player_id")["position"]
        pos_sums = recent.join(pos.rename("cur_pos"), on="player_id") \
                         .groupby("cur_pos").sum(numeric_only=True)
        out: dict = {}
        for market, (pool, rate) in MARKETS.items():
            if rate is None:
                continue
            num, den = rate
            k = PRIOR_N[pool]
            for pid, row in sums.iterrows():
                p = pos.get(pid)
                if p not in pos_sums.index or pos_sums.at[p, den] <= 0:
                    continue
                prior = pos_sums.at[p, num] / pos_sums.at[p, den]
                val = (row[num] + k * prior) / (row[den] + k)
                out.setdefault(pid, {})[market] = float(val)
        return out

    def covers(self, player_id, market_code: str) -> bool:
        """Whether this player's projection in this market will be adjusted."""
        spec = MARKETS.get(market_code)
        if spec is None:
            return False
        pool, rate = spec
        adj = self.adjustments.get((player_id, pool))
        return bool(adj) and (rate is None or market_code in adj["rates"])

    def factor(self, player_id: str, market_code: str, projection: float) -> float:
        """Multiplier for this player's projection in this market, 1.0 if none.

        A multiplier so it can ride the same path as the stale-role factor:
        applied to the point projection and to every predicted quantile, so the
        range and the win probability move with the number.
        """
        spec = MARKETS.get(market_code)
        if spec is None or projection <= 0:
            return 1.0
        pool, rate = spec
        adj = self.adjustments.get((player_id, pool))
        if not adj:
            return 1.0
        per = 1.0 if rate is None else adj["rates"].get(market_code)
        if per is None:
            return 1.0
        implied = adj["share_after"] * adj["team_volume"] * per
        new = max(projection, implied)
        return float(min(new / projection, MAX_FACTOR))
