"""Does telling the model where the season ends fix the early-season board?

research_early_season.py asked a version of this in September 2026 and answered
no. That answer was wrong, and the reason it was wrong is one line of it:

    MIN_PREV_GAMES = 6
    # Only players whose last season says they have a role: enough games to
    # have a season average, so a rookie or a fringe player does not decide
    # the fit.

That filter removes the players the correction exists for. A rookie who played
sixty snaps in December and opened September as the starter is not noise to be
excluded from the fit, he is the entire phenomenon. So is a man on a new team,
and so is a starter who sat out Week 18. The test was run on the population the
correction cannot help and concluded it does not help.

Here is what the defect looks like on a live board. Week 3, 2026, 92 published
picks, 87 of them unders:

    player              this season   last December   projected   line
    TreVeyon Henderson    76 ry           16.3           43.8      41.5
    Dontayvion Wicks      73.5 recy        8.3           35.6      44.5
    Rashod Bateman        44 recy         10             33.9      43.5
    Lamar Jackson         37 ry           14             25.8      38.5
    Bhayshul Tuten        14 carries       3.3           10.9      12.5

The projection lands between the two seasons every time, because the window is
five games long and holds three of last December in Week 3. Eight of the 92
picks had a window spanning a team the player had left.

y_season_mean and y_season_n already carry this season on its own and the model
ignores them. That is not mysterious. recs, rush_yds and rush_att are served by
linear models, and a linear model cannot express "trust this season's average
when there is little of it and it disagrees with the window": a coefficient is
the same coefficient at every value of n. Trees can learn the interaction and
largely do not, because from Week 6 the two numbers are nearly identical and the
split earns nothing on the bulk of the rows.

So this names the structure instead of hoping it is inferred, and tests it the
only way that counts: through the model that actually ships, on seasons it was
never fitted to, on the rows where the window crosses the boundary.

    incumbent     the market's live family on its current feature columns
    + boundary    the same family, same rows, plus
                  cur_season_mean, cur_season_n     this season alone
                  prev_season_mean, prev_season_n   all of last season's
                                                    regular games, not its tail
                  window_prev_frac                  how much of the window
                                                    belongs to a finished season
                  window_post_frac                  how much of it is postseason
                  window_same_team_frac             how much of it was this team

Fitted on every season up to HOLDOUT-1 and scored on HOLDOUT, twice: on the
early rows where the window crosses the boundary, which is what this is for, and
on everything, to confirm it does no harm to the 80% of the season that was
already fine. Reported again on role-changers alone, because a gain concentrated
there is the gain worth having and an average over all rows would hide it.

The features are computed here from box scores rather than read from
extra_features, so this can run before the feature tables are rebuilt. The
definitions match the ones added to jobs.py; if those change, change these.
"""

import os

import numpy as np
import pandas as pd

os.environ.setdefault("MARKET_CODE", "recs")
os.environ.setdefault("LOOKBACK", "5")

import train as tr  # noqa: E402  (env must be set before import)
from psycopg2.extras import RealDictCursor  # noqa: E402

# The live family per market, as of September 2026. Kept explicit rather than
# read from active_models so a local experiment cannot quietly change what the
# comparison calls the incumbent.
MARKETS = {
    "recs": "enet_v2", "rec_yds": "rf_default", "rush_yds": "enet_v2",
    "rush_att": "ridge_v2", "pass_yds": "hgb_v1", "pass_att": "hgb_v1",
    "pass_completions": "hgb_v1",
}
NEW = ["cur_season_mean", "cur_season_n", "prev_season_mean", "prev_season_n",
       "window_prev_frac", "window_post_frac", "window_same_team_frac"]
WINDOW = 5
HOLDOUT = 2025
# Fewer games than the window means last season is still inside it. This is the
# population the change is for, and it is arithmetic rather than a threshold.
THIN = WINDOW - 1
# A role changed if this season's rate is half again last season's, or half it.
CHANGE_HI, CHANGE_LO = 1.5, 0.67


def load_market(code: str):
    """Labelled feature rows, exactly as research_ensemble.py loads them.

    Returns the frame and the market's own stat_field. That column name is read
    from prop_markets rather than written down here, because the two research
    scripts that wrote it down disagree with each other about passing attempts
    and jobs.py resolves it from the table for the same reason.
    """
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT id, eligible_positions, stat_field FROM prop_markets "
            "WHERE code = %s", (code,))
        m = cur.fetchone()
        if not m:
            return None
        cur.execute(
            """
            SELECT pmf.player_id, p.position, pmf.as_of_game_date, pmf.opponent,
                   pmf.team, pmf.mean, pmf.stddev, pmf.weighted_mean, pmf.trend,
                   pmf.aux_mean, pmf.aux_trend, pmf.extra_features, pmf.label_actual
            FROM player_market_features pmf
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pmf.market_id = %s AND pmf.lookback = %s
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date, pmf.player_id
            """,
            (m["id"], int(os.environ["LOOKBACK"])),
        )
        rows = cur.fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows)
    elig = m.get("eligible_positions")
    if elig:
        df = df[df["position"].isin(list(elig))]
    return df, str(m["stat_field"])


_GAMES: dict = {}


def load_games(stat_field: str) -> pd.DataFrame:
    """Every box score, with the one stat this market is about.

    Cached per stat, because several markets share one (pass_att and
    pass_completions do not, but rush_yds and rush_att read carries alike) and
    the table is the largest thing this script touches.
    """
    if not stat_field.isidentifier():
        raise SystemExit(f"refusing to interpolate {stat_field!r} into SQL")
    if stat_field in _GAMES:
        return _GAMES[stat_field]
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT player_id, season, week, season_type, game_date, team,
                   COALESCE({stat_field}, 0)::float8 AS y
            FROM player_game_stats_app
            WHERE game_date IS NOT NULL
            ORDER BY player_id, game_date
            """
        )
        rows = cur.fetchall()
    g = pd.DataFrame(rows)
    g["game_date"] = pd.to_datetime(g["game_date"])
    _GAMES[stat_field] = g
    return g


def boundary_features(games: pd.DataFrame) -> pd.DataFrame:
    """One row per player-game, describing the window behind it.

    Everything is computed from games strictly before the one being described.
    Written as an explicit pass over each player's history rather than with
    rolling windows, because the window fractions need the identity of the games
    in the window and not just an average over them.
    """
    out = []
    for pid, g in games.groupby("player_id", sort=False):
        g = g.sort_values("game_date")
        seasons = g["season"].to_numpy()
        weeks = g["week"].to_numpy()
        types = g["season_type"].fillna("REG").to_numpy()
        teams = g["team"].to_numpy()
        ys = g["y"].to_numpy(dtype=float)
        dates = g["game_date"].to_numpy()
        for i in range(len(g)):
            season, team = seasons[i], teams[i]
            lo = max(0, i - WINDOW)
            win = slice(lo, i)
            w = i - lo
            cur = ys[:i][seasons[:i] == season]
            prev_mask = (seasons[:i] == season - 1) & (types[:i] == "REG")
            prev = ys[:i][prev_mask]
            row = {
                "player_id": pid,
                "as_of_game_date": dates[i],
                "cur_season_n": float(len(cur)),
                "cur_season_mean": float(cur.mean()) if len(cur) else np.nan,
                "prev_season_n": float(len(prev)),
                "prev_season_mean": float(prev.mean()) if len(prev) else np.nan,
                "window_prev_frac":
                    float((seasons[win] != season).sum()) / w if w else np.nan,
                "window_post_frac":
                    float((types[win] != "REG").sum()) / w if w else np.nan,
                "window_same_team_frac":
                    float((teams[win] == team).sum()) / w if w else np.nan,
                "_week": float(weeks[i]) if not pd.isna(weeks[i]) else np.nan,
            }
            out.append(row)
    d = pd.DataFrame(out)
    d["as_of_game_date"] = pd.to_datetime(d["as_of_game_date"])
    return d


def score(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    mae = float(np.mean(np.abs(y - p)))
    ss = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(((y - p) ** 2).sum()) / ss if ss else float("nan")
    return mae, r2


def fit_and_predict(family, train_df, cols, ytr, test_df):
    model = tr.build_model(family)
    model.fit(train_df[cols], ytr)
    return model.predict(test_df[cols])


def main():
    print(f"Fitting on seasons up to {HOLDOUT - 1}, scoring on {HOLDOUT}.\n"
          f"'thin' means fewer than {WINDOW} games this season, which is the "
          f"same thing as last season still being in the window.\n")
    verdicts = []
    for code, family in MARKETS.items():
        loaded = load_market(code)
        if loaded is None or len(loaded[0]) < 600:
            print(f"{code}: too few labelled rows\n")
            continue
        df, stat_field = loaded
        df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"], errors="coerce")
        df = df[df["as_of_game_date"].notna()].copy()

        bf = boundary_features(load_games(stat_field))
        df = df.merge(bf, on=["player_id", "as_of_game_date"], how="left")
        # A row whose box score could not be matched cannot be judged on this
        # question either way, so it leaves rather than arriving as a zero.
        before = len(df)
        df = df[df["cur_season_n"].notna()].copy()
        if len(df) < 600:
            print(f"{code}: only {len(df)} of {before} rows matched a box score\n")
            continue

        season_of = df["as_of_game_date"].dt.year
        # The season a January game belongs to is the year before it.
        season_of = np.where(df["as_of_game_date"].dt.month <= 2,
                             season_of - 1, season_of)
        df["_season"] = season_of
        train_raw = df[df["_season"] < HOLDOUT]
        test_raw = df[df["_season"] == HOLDOUT]
        if len(train_raw) < 400 or len(test_raw) < 150:
            print(f"{code}: split leaves {len(train_raw)}/{len(test_raw)}, "
                  f"not enough\n")
            continue

        train_df, cols = tr._build_feature_dataframe(train_raw)
        test_df, _ = tr._build_feature_dataframe(test_raw)
        for c in cols:
            if c not in test_df.columns:
                test_df[c] = 0.0
        ytr = train_raw["label_actual"].astype(float).to_numpy()
        yte = test_raw["label_actual"].astype(float).to_numpy()

        # The new columns, median-filled so a player with no prior season is not
        # handed a zero that reads as "produces nothing".
        for side, raw in ((train_df, train_raw), (test_df, test_raw)):
            for c in NEW:
                v = pd.to_numeric(raw[c], errors="coerce")
                side[c] = v.fillna(v.median()).to_numpy()
        cols_new = list(cols) + NEW

        base = fit_and_predict(family, train_df, cols, ytr, test_df)
        plus = fit_and_predict(family, train_df, cols_new, ytr, test_df)

        thin = (test_raw["cur_season_n"] <= THIN).to_numpy()
        ratio = (test_raw["cur_season_mean"]
                 / test_raw["prev_season_mean"].replace(0, np.nan)).to_numpy()
        changed = thin & ((ratio > CHANGE_HI) | (ratio < CHANGE_LO))

        print(f"{code}  ({family})")
        print(f"{'rows':<26}{'n':>6}{'MAE base':>10}{'MAE +bnd':>10}"
              f"{'gain':>8}{'R2 base':>9}{'R2 +bnd':>9}")
        gains = {}
        for label, mask in (("thin window", thin),
                            ("thin + role changed", changed),
                            ("everything", np.ones(len(yte), bool))):
            if mask.sum() < 60:
                print(f"{label:<26}{int(mask.sum()):>6}   too few to judge")
                continue
            b_mae, b_r2 = score(yte[mask], base[mask])
            p_mae, p_r2 = score(yte[mask], plus[mask])
            gain = (b_mae - p_mae) / b_mae * 100 if b_mae else 0.0
            gains[label] = gain
            print(f"{label:<26}{int(mask.sum()):>6}{b_mae:>10.3f}{p_mae:>10.3f}"
                  f"{gain:>+7.1f}%{b_r2:>9.4f}{p_r2:>9.4f}")

        # The noise rule, stated per market rather than left to the reader: a
        # change ships when it helps the rows it was built for and does not cost
        # anything anywhere else.
        t, e = gains.get("thin window"), gains.get("everything")
        if t is None or e is None:
            verdict = "cannot judge"
        elif t > 1.0 and e > -0.5:
            verdict = f"SHIP  thin {t:+.1f}%, overall {e:+.1f}%"
        elif t > 1.0:
            verdict = f"no    helps thin {t:+.1f}% but costs overall {e:+.1f}%"
        else:
            verdict = f"no    thin {t:+.1f}%"
        print(f"  -> {verdict}\n")
        verdicts.append((code, verdict))

    print("=" * 62)
    for code, v in verdicts:
        print(f"{code:<20}{v}")


if __name__ == "__main__":
    main()
