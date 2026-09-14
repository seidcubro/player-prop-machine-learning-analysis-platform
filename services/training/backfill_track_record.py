"""Build a real, multi-season track record instead of one frozen afternoon.

`prop_edge_results` holds 283 graded picks and every one of them is from
2023-12-11, because that is the single slate the live edge builder ever ran
against. So a player page shows two games from two years ago and calls it a
track record, which is worse than showing nothing.

Meanwhile `odds_snapshots` has real closing lines for 2023, 2024 and 2025, and
the models can be refit for each of those seasons using only the seasons before
it. That is enough to reconstruct what the product *would have* published, week
by week, without ever letting a model see the season it is being judged on.

The discipline, because a track record that flatters itself is worthless:

  * **Models are refit per season on strictly earlier data.** Scoring 2024 with
    a model that has seen 2024 would produce a beautiful number and mean
    nothing.
  * **The pick rule is the product's rule.** Calibrated P(over) off the quantile
    CDF, then expected value against the price actually offered, with the same
    tier cuts `build_prop_edges.py` uses. If the site's rule changes, this has
    to change with it.
  * **Rows are tagged `source='backtest'`.** Live picks stay tagged `'live'`.
    They are different things and the table has to say which is which.
  * **Best price per side, one row per player-game-market.** Every book prices
    the same prop; counting all three would triple the sample and make the
    record look far more certain than it is.

Env: SEASONS (default 2023,2024,2025), LOOKBACK, TRUNCATE_BACKTEST=1 to clear
previous backtest rows first (live rows are never touched).
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp
import eval as ev
import train as tr
import train_quantiles as tq
from backtest_quantile_ev import (american_to_decimal, american_to_prob,
                                  cdf_level, load_prices)
from backtest_season import DATABASE_URL, MARKET_MAP

SEASONS = [int(s) for s in os.getenv("SEASONS", "2023,2024,2025").split(",")]

DDL = """
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'live';
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS expected_value DOUBLE PRECISION;
ALTER TABLE prop_edge_results ADD COLUMN IF NOT EXISTS season INTEGER;
CREATE INDEX IF NOT EXISTS idx_edge_results_season ON prop_edge_results (season);
CREATE INDEX IF NOT EXISTS idx_edge_results_source ON prop_edge_results (source);
"""


def tier_for(ev_value: float, side: str = "under") -> str:
    """Exactly the cuts build_prop_edges.py uses.

    If these drift apart the track record stops describing the product, so they
    are deliberately stated identically in both places rather than imported
    across a module boundary that would make the coupling easy to miss.
    """
    cuts = ((0.12, 0.09, 0.06, 0.03) if side == "over"
            else (0.06, 0.04, 0.02, 0.0))
    if ev_value >= cuts[0]:
        return "elite"
    if ev_value >= cuts[1]:
        return "strong"
    if ev_value >= cuts[2]:
        return "medium"
    if ev_value > cuts[3]:
        return "small"
    return "none"


def season_of(dates) -> pd.Series:
    d = pd.to_datetime(pd.Series(dates))
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def fit_for_season(market: str, season: int):
    """Point model, quantiles and calibration, all from seasons before `season`."""
    import json
    engine = create_engine(DATABASE_URL, future=True)
    with engine.connect() as c:
        row = c.execute(text(
            "SELECT a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
        ), {"c": market}).mappings().first()
    if not row:
        return None
    meta_path = os.path.join(
        ev.ARTIFACT_DIR, f"{row['model_name']}_{market}_lb{row['lookback']}.json")
    if not os.path.exists(meta_path):
        return None
    cols = json.load(open(meta_path))["feature_cols"]

    df = ev.load_labeled_rows(cols)
    yr = season_of(df["as_of_game_date"])
    train = df[yr < season]
    if len(train) < 300:
        return None

    X = ev.build_feature_matrix(train, cols)
    y = train[ev.LABEL_COL].astype(float)
    point = tr.build_model(row["model_name"])
    point.fit(X, y)
    quants = {}
    for q in tq.QUANTILES:
        m = tq.build_quantile_model(q)
        m.fit(X, y)
        quants[q] = m
    # The same calibration the product ships, not a different one.
    #
    # This used to call the backtest helper, which fits the map on in-sample
    # training predictions and therefore comes out as almost the identity. The
    # site serves a map measured on a held-out season and restricted to the
    # priced-like population, which corrects in the opposite direction. Using
    # the wrong one here meant the published track record described a model that
    # is not the one running, which makes every number on that page wrong in a
    # way nobody could see.
    seasons = season_of(train["as_of_game_date"])
    cal = tq.fit_calibration_season_holdout(
        X, y, seasons, tq.QUANTILES, priced=tq.priced_like_mask(train, market))
    pit = (np.asarray(cal["grid"], dtype=float),
           np.asarray(cal["level"], dtype=float))
    return point, quants, cols, df, yr, pit, row["model_name"]



def _calibrated_level(qcols: dict, pit, level: float) -> np.ndarray:
    """One published quantile, re-read where the calibration map places it.

    Mirrors calibrated_quantiles() in build_prop_edges.py, vectorised over rows.
    The level lookup does not depend on the row, so it is done once; the value
    lookup interpolates inside each row's own sorted quantile ladder.
    """
    grid, mapped = pit
    levels = sorted(qcols)
    raw_level = float(np.interp(level, mapped, grid))
    ladder = np.sort(np.column_stack([qcols[q] for q in levels]), axis=1)
    return np.array([float(np.interp(raw_level, levels, row)) for row in ladder])


def _calibrated_median(qcols: dict, pit) -> np.ndarray:
    """The median, which is the only level the side rule needs."""
    return _calibrated_level(qcols, pit, 0.5)


def main():
    engine = create_engine(DATABASE_URL, future=True)
    with engine.begin() as conn:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        # Deliberately NOT deleting here.
        #
        # This used to clear the backtested rows up front and rebuild them over
        # the following twenty minutes. Anything that went wrong in between --
        # a failure, a second copy of this script started by accident, a
        # container killed -- left the table holding nothing but live rows, and
        # the track record on the site went blank. That happened.
        #
        # The rebuild now happens in one transaction at the end: the old rows
        # are replaced by the new ones or nothing changes at all. See the write
        # in main() below.
        pass

    ids = pd.read_sql(text(
        "SELECT id AS app_id, external_id, name FROM players "
        "WHERE external_id IS NOT NULL"), engine)

    def key(s):
        return s.str.lower().str.replace(r"[.\-']", "", regex=True).str.strip()

    ids["key"] = key(ids["name"])
    # Names are not unique (there is a Josh Allen at QB and another on a
    # different line), so keep the first and accept it rather than silently
    # attaching a pick to the wrong profile.
    ids = ids.drop_duplicates(subset="key")

    all_rows = []
    for season in SEASONS:
        prices = load_prices(engine, season)
        prices["market_code"] = prices["market_key"].map(MARKET_MAP)
        prices = prices[prices["market_code"].notna()]
        if prices.empty:
            print(f"{season}: no lines")
            continue
        prices["key"] = key(prices["player_name"])

        for market, grp in prices.groupby("market_code"):
            os.environ["MARKET_CODE"] = market
            ev.MARKET_CODE = market
            fit = fit_for_season(market, season)
            if fit is None:
                continue
            point, quants, cols, df, yr, pit, model_name = fit
            test = df[yr == season]
            if test.empty:
                continue
            Xt = ev.build_feature_matrix(test, cols)
            test = test.assign(
                pred=np.clip(point.predict(Xt), 0, None),
                **{f"q{int(q * 100)}": np.clip(quants[q].predict(Xt), 0, None)
                   for q in tq.QUANTILES})

            m = grp.merge(ids, on="key", how="inner").merge(
                test[["player_id", "as_of_game_date", "pred", ev.LABEL_COL]
                     + [f"q{int(q * 100)}" for q in tq.QUANTILES]],
                left_on=["external_id", "game_date"],
                right_on=["player_id", "as_of_game_date"], how="inner",
            ).dropna(subset=["pred", ev.LABEL_COL, "over_am", "under_am"])
            if m.empty:
                continue

            qcols = {q: m[f"q{int(q * 100)}"].to_numpy() for q in tq.QUANTILES}
            grid, emp = pit
            p_under_over_line = np.clip(
                np.interp(cdf_level(qcols, m["over_line"].to_numpy()), grid, emp),
                0.01, 0.99)
            p_over = 1.0 - p_under_over_line
            p_under = np.clip(
                np.interp(cdf_level(qcols, m["under_line"].to_numpy()), grid, emp),
                0.01, 0.99)

            # Bounded the way the live board bounds, so the record describes
            # picks the product can actually make.
            #
            # build_prop_edges caps every published probability at
            # PROB_FLOOR/PROB_CEILING, because nothing in a football prop is 99%
            # to happen and the audit rejects anything outside that range.
            # Without the same cap here the reconstruction published 61 picks
            # claiming over 95%, including a rush_yds under at -2500 quoted at
            # 99.0% that needed 96.2% to break even, showed a positive edge on
            # that basis, and lost. The live board would price that as negative
            # expected value and never show it.
            p_over = np.clip(p_over, bp.PROB_FLOOR, bp.PROB_CEILING)
            p_under = np.clip(p_under, bp.PROB_FLOOR, bp.PROB_CEILING)

            ev_over = p_over - american_to_prob(m["over_am"])
            ev_under = p_under - american_to_prob(m["under_am"])

            # Same side rule the live board uses.
            #
            # This took whichever side had the better expected value, which is
            # not what ships any more: the board picks the side the model's own
            # median favours, and lets the price decide only whether the pick is
            # worth publishing. A record generated under a rule the site no
            # longer follows describes a product nobody can bet.
            #
            # See the side selection in build_prop_edges.py.
            #
            # The median is read through the calibration map, exactly as the
            # live builder reads it. Taking the raw q50 was wrong and loudly so
            # on rush_att: about 15% of running back rows are players who
            # dressed and never carried, the raw quantile regression at level
            # 0.5 collapses onto those structural zeros, and q50 came back as 0
            # for 740 of 740 graded rush_att rows. Every one became "median 0
            # is below a line of 11.5, so bet the under" with an eleven attempt
            # edge behind it, and 547 of them were filed as elite. That is 14.5%
            # of the backtested elite record carrying no model opinion at all.
            #
            # The live board never had this because it inverts the map first.
            # The reconstruction has to do the same or the record describes a
            # different product from the one that ships.
            med50 = _calibrated_median(qcols, pit)
            take_over = med50 > m["over_line"].to_numpy()

            m["recommended_side"] = np.where(take_over, "over", "under")
            m["win_prob"] = np.where(take_over, p_over, p_under)
            m["expected_value"] = np.where(take_over, ev_over, ev_under)
            m["line"] = np.where(take_over, m["over_line"], m["under_line"])
            m["price_american"] = np.where(take_over, m["over_am"], m["under_am"])
            m["decimal"] = american_to_decimal(m["price_american"])
            m["projection"] = m["pred"]
            m["projection_median"] = med50
            # The rest of the walk-forward ladder, kept rather than discarded.
            #
            # These come from models refit on seasons strictly before this one,
            # which makes them the only honest quantiles in the system. They
            # were computed and thrown away, so fit_interval_calibrator had to
            # read the ladder from player_projection_history, whose quantiles
            # come from the active model refit on every season including this
            # one. See db/migrations/add_walkforward_quantiles.sql.
            #
            # Stored calibrated, not raw. The site does not publish the
            # quantile regression's own output: build_prop_edges reads every
            # level through the PIT map first, so the number a reader sees as
            # p90 comes from somewhere else on the ladder. Storing the raw
            # prediction here would mean the interval calibrator fitted on
            # these rows corrected an object the product never shows.
            for _q in (0.10, 0.25, 0.75, 0.90):
                m[f"q{int(_q * 100)}"] = _calibrated_level(qcols, pit, _q)
            m["actual"] = m[ev.LABEL_COL].astype(float)
            m["market_code"] = market
            m["season"] = season
            m["model_name"] = model_name
            m["edge_tier"] = [tier_for(v, sd) for v, sd in
                              zip(m["expected_value"], m["recommended_side"])]

            won = np.where(m["recommended_side"] == "over",
                           m["actual"] > m["line"], m["actual"] < m["line"])
            push = m["actual"] == m["line"]
            m["hit"] = np.where(push, None, won)

            # The live builder drops anything it does not make better than
            # even money, so the record has to as well.
            keep = (m["edge_tier"] != "none") & (~push) & (m["win_prob"] > 0.5)
            m = m[keep]
            if not m.empty:
                all_rows.append(m)
                print(f"  {season} {market}: {len(m)} graded picks")

    if not all_rows:
        raise SystemExit("nothing to write")

    out = pd.concat(all_rows, ignore_index=True)
    # One pick per player-game-market. Three books pricing the same prop is one
    # opinion, not three, and counting them all would make the record look far
    # more certain than it is.
    out = out.sort_values("expected_value", ascending=False).drop_duplicates(
        subset=["external_id", "game_date", "market_code"])

    cols = ["external_id", "app_id", "name", "game_date", "market_code", "line",
            "projection", "projection_median", "recommended_side", "win_prob",
            "expected_value", "edge_tier", "actual", "hit", "season",
            "price_american", "q10", "q25", "q75", "q90"]
    w = out[cols].rename(columns={"external_id": "player_id", "name": "player_name"})
    w = w.drop(columns=["app_id"])
    w["source"] = "backtest"

    # Profit per unit staked, the same quantity the live board now records.
    #
    # expected_value here is the model's probability minus the book's implied
    # probability, which is a probability edge. Multiplying by the decimal odds
    # turns it into what a unit actually returns, and without it the backtested
    # rows cannot be compared with the live ones on the number the board shows.
    price = pd.to_numeric(w["price_american"], errors="coerce")
    dec = 1.0 + np.where(price > 0, price / 100.0, 100.0 / price.abs())
    w["ev_per_unit"] = w["expected_value"] * dec

    # The Best Bet selection, recorded rather than described.
    #
    # This is the rule build_prop_edges applies to the live board: an under, in
    # the elite or strong tier, one per player and market and one per player and
    # game, keeping the best paying row. It is the headline number on the FAQ
    # and it was never written to the results table at all, so the one selection
    # with a verified out-of-sample edge could not be scored against its own
    # record.
    w["best_bet"] = False
    eligible = w[(w["recommended_side"] == "under")
                 & (w["edge_tier"].isin(["elite", "strong"]))]
    if len(eligible):
        keep = (eligible.sort_values("ev_per_unit", ascending=False)
                        .drop_duplicates(subset=["player_name", "market_code"])
                        .drop_duplicates(subset=["player_name", "game_date"]))
        w.loc[keep.index, "best_bet"] = True
    print(f"best-bet flagged {int(w['best_bet'].sum())} of {len(w)} backtested picks")
    # Synthetic ids well clear of the live sequence so the two can never collide.
    w["edge_id"] = -(np.arange(len(w)) + 1)

    # Replace in one transaction, so a failure leaves the old record in place
    # rather than an empty table. The delete and the insert either both happen
    # or neither does.
    with engine.begin() as conn:
        # One rebuild at a time, enforced by the database rather than by me
        # remembering.
        #
        # Two copies of this have now been started by accident twice. The write
        # is a delete followed by an insert, so two of them racing means one
        # sits blocked for twenty minutes and then replaces rows the other just
        # wrote, and the first time it happened the track record on the site
        # went blank. A session-scoped advisory lock is free, is released when
        # the connection closes however the process dies, and turns the second
        # copy into an immediate refusal instead of an hour of contention.
        if not conn.execute(text(
                "SELECT pg_try_advisory_xact_lock(hashtext('backfill_track_record'))"
        )).scalar():
            raise SystemExit(
                "another backfill_track_record is already writing; refusing to "
                "run a second one. Wait for it to finish, or kill it first.")
        if os.getenv("TRUNCATE_BACKTEST", "1") == "1":
            n = conn.execute(text(
                "DELETE FROM prop_edge_results WHERE source = 'backtest'"
            )).rowcount
            print(f"cleared {n} previous backtest rows (live rows untouched)")

        # Never reconstruct a pick that was actually made.
        #
        # The delete above removes backtest rows only, which is deliberate: live
        # grades are the real record and must survive a rebuild. But the table
        # has a unique natural key on player, date and market, so the moment
        # SEASONS includes a season with live rows in it -- next year's default
        # bump does exactly that -- the append hits a duplicate key and the
        # whole transaction rolls back, twenty minutes in, with the table left
        # holding nothing but live rows.
        #
        # Reconstructed rows lose to live ones, because a live row is what the
        # site actually published at a price someone could have taken.
        live = pd.read_sql(text(
            "SELECT player_id, game_date, market_code FROM prop_edge_results "
            "WHERE source = 'live'"), conn)
        if not live.empty:
            key = ["player_id", "game_date", "market_code"]
            w["game_date"] = pd.to_datetime(w["game_date"]).dt.date
            live["game_date"] = pd.to_datetime(live["game_date"]).dt.date
            before = len(w)
            w = w.merge(live.assign(_live=1), on=key, how="left")
            w = w[w["_live"].isna()].drop(columns=["_live"])
            if len(w) < before:
                print(f"dropped {before - len(w)} reconstructed picks that "
                      f"already have a live graded row")
        w.to_sql("prop_edge_results", conn, if_exists="append", index=False,
                 method="multi", chunksize=500)

    print()
    print(f"WROTE {len(w)} backtested picks")
    summary = w.assign(hit=w["hit"].astype(float)).groupby("season").agg(
        picks=("hit", "size"), hit_rate=("hit", "mean"),
        players=("player_id", "nunique"))
    print(summary.round(3).to_string())


if __name__ == "__main__":
    main()
