"""Fit, validate and write the vacated-volume parameters.

See vacated_volume.py for what the adjustment does and why. This measures it:
on every regular-season game from 2022 on where an established player was
missing, it predicts each teammate's share of team volume, fitted on seasons
before the last complete one and scored on that one.

    A  his trailing share, unchanged. What the model effectively does now.
    D  the vacated share handed to the named successor first (quarterbacks and
       running backs), the rest split by trailing share and position, every
       share clipped to [0, 1].

A pool is written as accepted only if D beats A on the holdout by more than
MIN_GAIN, the same noise rule the calibrators use. Unaccepted pools are written
too, marked as such, so the audit can say which corrections are live.

Then the whole chain is checked in yards, not shares: for the same teammates,
the trailing average of passing, rushing and receiving yards against that
average raised to the level the new share implies, at the player's own rate,
which is exactly what serving does. Adding the inherited volume on top instead
counted it twice and was measurably worse. A pool must improve in yards as well
as in shares to be accepted: carries improved its shares by 6.8% and made
rushing yards 1.8% worse, and is not applied. A share that
improves and a projection that does not would mean the conversion is wrong, and
that is worth knowing before it reaches the board.

Every teammate who played is scored, including those with no trailing share.
An earlier version dropped them, which is exactly who inherits a quarterback's
volume, and it graded the third-stringer instead.

"Absent" is measured here, not read off the injury report: an established
player who played for the team in one of its previous two games and did not
play anywhere this week. Serving reads the report instead, because that is all
that is known before kickoff.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import vacated_volume as vv

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))

MIN_GAIN = 0.002
GRID_L = np.round(np.arange(0.0, 1.31, 0.05), 2)
GRID_G = np.array([0.0, 1.0, 3.0, 10.0, 30.0])
YARDS = {"attempts": "passing_yards", "carries": "rushing_yards",
         "targets": "receiving_yards"}
RATE_WINDOW = 16


def load(engine):
    games = pd.read_sql(
        text(
            """
            SELECT player_id, season, week, game_date, team, position,
                   COALESCE(targets, 0)         AS targets,
                   COALESCE(carries, 0)         AS carries,
                   COALESCE(attempts, 0)        AS attempts,
                   COALESCE(passing_yards, 0)   AS passing_yards,
                   COALESCE(rushing_yards, 0)   AS rushing_yards,
                   COALESCE(receiving_yards, 0) AS receiving_yards
            FROM player_game_stats_app
            WHERE season_type = 'REG' AND team IS NOT NULL
            """
        ),
        engine,
    ).sort_values(["player_id", "game_date"]).reset_index(drop=True)
    depth = pd.read_sql(
        text(
            """
            SELECT player_id, season, week::int AS week, team, position,
                   MIN(depth_team) AS rank
            FROM depth_charts
            WHERE depth_team IS NOT NULL AND position IN ('QB', 'RB')
            GROUP BY player_id, season, week::int, team, position
            """
        ),
        engine,
    )
    return games, depth


def absences(d, pool):
    _, threshold = vv.POOLS[pool]
    tw = d[["season", "team", "week"]].drop_duplicates().sort_values(
        ["season", "team", "week"])
    tw["prev1"] = tw.groupby(["season", "team"])["week"].shift(1)
    tw["prev2"] = tw.groupby(["season", "team"])["week"].shift(2)
    last = d[["player_id", "season", "team", "week", "position", "trail_after"]]
    played = set(zip(d["player_id"], d["season"], d["week"]))
    parts = [tw.dropna(subset=[b]).merge(last.rename(columns={"week": b}),
                                         on=["season", "team", b])
             for b in ("prev1", "prev2")]
    c = pd.concat(parts, ignore_index=True)
    c = c[c["trail_after"] >= threshold]
    c = c[[(p, s, w) not in played for p, s, w in
           zip(c["player_id"], c["season"], c["week"])]]
    c = (c.sort_values("prev1")
          .drop_duplicates(["player_id", "season", "team", "week"], keep="last"))
    return c[["player_id", "season", "team", "week", "position", "trail_after"]]


def successors(out, depth):
    """The next healthy player at each absent QB's or RB's position.

    That week's chart if it exists, else the latest earlier one.
    """
    out = out[out["position"].isin(vv.SUCCESSOR_POSITIONS)]
    absent = set(zip(out["player_id"], out["season"], out["team"], out["week"]))
    rows = []
    for r in out.itertuples(index=False):
        ch = depth[(depth["season"] == r.season) & (depth["team"] == r.team)
                   & (depth["position"] == r.position) & (depth["week"] <= r.week)]
        if ch.empty:
            continue
        ch = ch[ch["week"] == ch["week"].max()]
        ch = ch[[(p, r.season, r.team, r.week) not in absent for p in ch["player_id"]]]
        if ch.empty:
            continue
        s = ch.sort_values("rank").iloc[0]
        rows.append((r.season, r.team, r.week, s["player_id"], r.trail_after))
    return pd.DataFrame(rows, columns=["season", "team", "week", "player_id", "succ_vac"])


def evaluation_frame(raw, depth, pool):
    """Every teammate who played in a game with an absence, ready to score."""
    positions, _ = vv.POOLS[pool]
    key = ["season", "team", "week"]
    d = vv.with_shares(raw, pool)

    # Per-opportunity yards before each game, over the last RATE_WINDOW games,
    # and the team's trailing volume before each game. Both shifted, so nothing
    # about the game being predicted leaks into its own prediction.
    ycol = YARDS[pool]
    g = d.groupby("player_id", sort=False)
    d["y_trail"] = g[ycol].transform(
        lambda s: s.shift(1).rolling(vv.TRAIL, min_periods=vv.MIN_PRIOR).mean())
    d["num_prior"] = g[ycol].transform(
        lambda s: s.shift(1).rolling(RATE_WINDOW, min_periods=1).sum())
    d["den_prior"] = g[pool].transform(
        lambda s: s.shift(1).rolling(RATE_WINDOW, min_periods=1).sum())
    tv = (d.drop_duplicates(key).sort_values(["team", "game_date"])[key + ["team_total"]])
    tv["team_vol"] = tv.groupby("team")["team_total"].transform(
        lambda s: s.shift(1).rolling(vv.TRAIL, min_periods=1).mean())
    d = d.merge(tv[key + ["team_vol"]], on=key, how="left")

    out = absences(d, pool)
    vac = out.groupby(key)["trail_after"].sum().rename("vacated").reset_index()
    pos = (out.sort_values("trail_after").drop_duplicates(key, keep="last")
              [key + ["position"]].rename(columns={"position": "out_pos"}))
    ev = (d[d["position"].isin(positions)]
          .merge(vac, on=key, how="inner").merge(pos, on=key, how="left"))
    ev["trail0"] = ev["trail"].fillna(0.0)
    ev["same_pos"] = (ev["position"] == ev["out_pos"]).astype(float)
    suc = successors(out, depth).groupby(key + ["player_id"], as_index=False)["succ_vac"].sum()
    ev = ev.merge(suc, on=key + ["player_id"], how="left")
    ev["succ_vac"] = ev["succ_vac"].fillna(0.0)
    ev["succ_vac_game"] = ev.groupby(key)["succ_vac"].transform("sum")
    return ev, out


def shares(ev, lam, gamma, lam_s):
    return vv.predict_share(
        ev["trail0"].to_numpy(), ev["same_pos"].to_numpy(), ev["succ_vac"].to_numpy(),
        ev["vacated"].to_numpy(), ev["succ_vac_game"].to_numpy(), ev["gid"].to_numpy(),
        lam, gamma, lam_s)


def mae(y, p):
    return float(np.mean(np.abs(p - y))) if len(y) else float("nan")


def main():
    eng = create_engine(DATABASE_URL, future=True)
    raw, depth = load(eng)
    seasons = sorted(raw["season"].unique())
    complete = [s for s in seasons if s < max(seasons)]
    if len(complete) < 2:
        print("need two complete seasons to fit and score; nothing written")
        (ARTIFACTS / vv.ARTIFACT).write_text(json.dumps({"pools": {}}), encoding="utf-8")
        return
    holdout = complete[-1]
    key = ["season", "team", "week"]
    print(f"seasons {seasons[0]}-{seasons[-1]}; fitting before {holdout}, "
          f"scoring on {holdout}\n")

    result = {"holdout_season": int(holdout), "pools": {}}
    for pool in vv.POOLS:
        ev, out = evaluation_frame(raw, depth, pool)
        fit = ev[ev["season"] < holdout].copy()
        test = ev[ev["season"] == holdout].copy()
        for f in (fit, test):
            f["gid"] = f.groupby(key).ngroup()
        yf, yt = fit["share"].to_numpy(), test["share"].to_numpy()

        lam, gamma, lam_s = min(
            ((l, g, s) for l in GRID_L for g in GRID_G for s in GRID_L),
            key=lambda p: mae(yf, shares(fit, *p)))
        pa, pd_ = test["trail0"].to_numpy(), shares(test, lam, gamma, lam_s)
        base, new = mae(yt, pa), mae(yt, pd_)
        gain = (base - new) / base if base else 0.0
        is_s = test["succ_vac"].to_numpy() > 0

        # The chain, in yards. Rate shrunk to the position average the same way
        # serving shrinks it, with the average taken from the fitting seasons.
        ycol = YARDS[pool]
        f_pos = fit.groupby("position")[[ycol, pool]].sum()
        prior = (f_pos[ycol] / f_pos[pool].replace(0, np.nan)).to_dict()
        k = vv.PRIOR_N[pool]
        rate = ((test["num_prior"].fillna(0) + k * test["position"].map(prior).fillna(0))
                / (test["den_prior"].fillna(0) + k)).to_numpy()
        y_base = test["y_trail"].fillna(0.0).to_numpy()
        # Raised to the level the new share implies, never added on top, the
        # same rule serving applies. See vacated_volume.py.
        implied = pd_ * test["team_vol"].fillna(0).to_numpy() * rate
        adjusted = pd_ > test["trail0"].to_numpy()
        y_new = np.where(adjusted, np.maximum(y_base, implied), y_base)
        y_true = test[ycol].to_numpy()
        yb, yn = mae(y_true, y_base), mae(y_true, y_new)
        ysb, ysn = mae(y_true[is_s], y_base[is_s]), mae(y_true[is_s], y_new[is_s])

        accepted = bool(gain > MIN_GAIN and yn < yb)
        print(f"== {pool}: {len(out)} absences, {len(test)} teammate-games scored in "
              f"{holdout}, {int(is_s.sum())} named successors")
        print(f"   fitted lam={lam} gamma={gamma:g} successor={lam_s}")
        print(f"   share MAE     unchanged {base:.4f}   adjusted {new:.4f}   {gain:+.1%}")
        print(f"   {ycol:<14}unchanged {yb:7.2f}   adjusted {yn:7.2f}   "
              f"{(yb - yn) / yb if yb else 0:+.1%}")
        if is_s.any():
            print(f"   successors    unchanged {ysb:7.2f}   adjusted {ysn:7.2f}   "
                  f"{(ysb - ysn) / ysb if ysb else 0:+.1%} yards")
        print(f"   {'ACCEPTED' if accepted else 'not applied'}\n")

        result["pools"][pool] = {
            "lam": float(lam), "gamma": float(gamma), "lam_s": float(lam_s),
            "accepted": accepted,
            "holdout_share_gain": float(gain),
            "holdout_yards_mae": [float(yb), float(yn)],
            "holdout_rows": int(len(test)),
        }

    (ARTIFACTS / vv.ARTIFACT).write_text(json.dumps(result, indent=2), encoding="utf-8")
    live = [p for p, v in result["pools"].items() if v["accepted"]]
    print(f"wrote {vv.ARTIFACT}: {', '.join(live) if live else 'no pool'} accepted")


if __name__ == "__main__":
    main()
