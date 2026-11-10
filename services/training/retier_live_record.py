"""Put the elite label back on the live picks that were ranked elite and then demoted.

From 28 October 2026 the board tiered by rank within each market, and a step
that ran afterwards pushed every elite pick whose published probability sat
under its price down to strong (see demote_dead_bets in build_prop_edges). The
picks were published and graded, so the outcomes in the record are right. The
label on them is not: weeks 8 and 9 graded almost no elite pick, and strong
hit 64% because it was holding them.

This undoes that one step and nothing else. Side, line, price, projection and
result are not touched, and the only move it can make is strong to elite.

The first version of this did more, and it was wrong to. It re-ranked every
archived row under today's rule and wrote whatever came out. On the first dry
run that moved 27 picks from small to elite, and none of those were the bug:

    Thursday games. A Wednesday board carries one priced game, too few rows in
    any market to rank, so those picks were published as small. Ranked later
    beside Sunday's rows, some of them come out elite.

    rush_yds. Until 4 November it was ranked widest gap first. It is ranked
    narrowest first now, so the picks it published as small before that date
    are the ones today's rule calls elite.

    The shares. Elite was the top 10% until 8 November and the top 20% after.

Every one of those is a pick that was published under one label and would be
filed in the record under a better one, by a rule partly fitted on its own
result. The record says what was published. So each row is ranked the way its
own build ranked it:

    Its board is the rows of that market and week that were still upcoming at
    the build that froze it. History is refreshed until kickoff, so every row
    frozen by the same build shares an archived_at, and the rows that build
    could see are the ones archived at or after it.

    The elite share and the direction are read off the stored tiers rather
    than assumed from commit dates, because I deploy by hand and a commit time
    is only a floor. The demotion moved elite to strong and nothing else, so
    elite plus strong was 30% of a ranked market under the old shares and 40%
    under the new ones whether or not the bug ran. The direction is whichever
    end of the gap the stored elite and strong rows sit at.

    A row is promoted only if it is stored as strong, its probability did not
    beat its price (the only rows the bug touched), and it ranks inside the
    elite share of its board.

It is still not exact. The rows a build ranked beside a Thursday game were
revised until their own kickoffs, and the archive keeps the last revision, so
the later rows are ranked here on a gap that may have moved. The check on that
prints every run: how many of the rows this ranks as elite were stored as
elite or strong. If the reconstruction is right, nearly all of them.

Dry run by default.

    docker compose run --rm training python retier_live_record.py
    docker compose run --rm -e WRITE=1 training python retier_live_record.py
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp
import gap_tier

SINCE = os.getenv("SINCE", "2026-10-28")
TIERS = ["elite", "strong", "medium", "small"]
# Used only when a build froze too few rows to read the rule off them.
SHARE_CHANGED = pd.Timestamp(os.getenv("SHARE_CHANGED", "2026-11-08 15:40"), tz="UTC")
DIRECTION_FITTED = pd.Timestamp(os.getenv("DIRECTION_FITTED", "2026-11-04 16:13"), tz="UTC")
MIN_TO_INFER = 20


def norm(s):
    return (s.fillna("").str.lower().str.replace(".", "", regex=False)
            .str.replace("-", " ", regex=False).str.strip())


def nfl_week(dates):
    # Thursday to Monday is one slate. Shift back two days so Monday night is
    # filed with the Sunday before it and not with the Thursday after.
    iso = (pd.to_datetime(dates) - pd.Timedelta(days=2)).dt.isocalendar()
    return iso["year"].astype(str) + "-w" + iso["week"].astype(str).str.zfill(2)


def needed(price):
    p = pd.to_numeric(price, errors="coerce")
    return np.where(p > 0, 100.0 / (p + 100.0),
                    np.where(p < 0, (-p) / ((-p) + 100.0), np.nan))


def elite_share_at(board, when):
    """10% or 20%, read off what the build stored."""
    ranked = board.groupby("market_code")["edge_tier"].transform(
        lambda s: (s != "small").any())
    b = board[ranked]
    if len(b) < MIN_TO_INFER:
        return 0.20 if when >= SHARE_CHANGED else 0.10
    top = b["edge_tier"].isin(["elite", "strong"]).mean()
    return 0.20 if top >= 0.35 else 0.10


def direction_at(rows, market, when):
    """Which end of the gap this build treated as the confident one."""
    fitted = (gap_tier.v2_thresholds().get(market) or {}).get("direction", "high")
    if fitted == "high":
        return "high"
    top = rows[rows["edge_tier"].isin(["elite", "strong"])]["gap_share"]
    rest = rows[rows["edge_tier"] == "small"]["gap_share"]
    if len(top) < 3 or len(rest) < 3:
        return fitted if when >= DIRECTION_FITTED else "high"
    return "low" if top.mean() < rest.mean() else "high"


def rank_as_built(h):
    """Mark the rows each build ranked inside its elite share.

    Takes the archive, returns it with `was_elite` set, and a line per build
    for the printout. Split out so it can be run on a made-up archive.
    """
    h = h.copy()
    h["was_elite"] = False
    builds = []
    for (week, when), frozen in h.groupby(["board", "archived_at"]):
        board = h[(h["board"] == week) & (h["archived_at"] >= when)]
        share = elite_share_at(board, when)
        n_top = 0
        for market, rows in board.groupby("market_code"):
            known = rows[rows["gap_share"].notna()]
            if len(known) < gap_tier.MIN_RANKABLE:
                continue
            if not (known["edge_tier"] != "small").any():
                continue  # the build did not rank this market
            low = direction_at(known, market, when) == "low"
            order = known.sort_values("gap_share", ascending=low,
                                      kind="mergesort")
            top = order.index[:int(round(len(order) * share))]
            mine = [i for i in top if i in frozen.index]
            h.loc[mine, "was_elite"] = True
            n_top += len(mine)
        builds.append((week, when, len(frozen), len(board), share, n_top))
    return h, builds


def demoted(h):
    """The rows the bug touched: ranked elite, stored strong, price not beaten.

    negative_ev can be null on a row archived before the column existed, and a
    null is not evidence the bug skipped it.
    """
    neg = h["negative_ev"].astype("boolean")
    return h["was_elite"] & (h["edge_tier"] == "strong") & (neg.isna() | neg)


def main():
    if not gap_tier.v2_thresholds():
        raise SystemExit("no gap_tiers.json in the artifact directory, so "
                         "there is no rule to relabel with")
    eng = create_engine(bp.DATABASE_URL, future=True)

    h = pd.read_sql(text("""
        SELECT player_name, market_code, bookmaker_key, bookmaker_title, line,
               gap_share, edge_tier, negative_ev, commence_time, archived_at,
               (commence_time AT TIME ZONE 'America/New_York')::date AS game_date
        FROM prop_edges_history
        WHERE commence_time >= :since
    """), eng, params={"since": SINCE})
    if h.empty:
        raise SystemExit(f"no archived board rows since {SINCE}")
    h["board"] = nfl_week(h["game_date"])
    h["archived_at"] = pd.to_datetime(h["archived_at"], utc=True)
    h["gap_share"] = pd.to_numeric(h["gap_share"], errors="coerce")

    h, builds = rank_as_built(h)

    print("BUILDS THAT FROZE ROWS")
    print(f"  {'week':<10}{'frozen at (UTC)':<19}{'rows':>6}{'board':>7}"
          f"{'elite share':>13}{'ranked elite':>14}")
    for week, when, n, nb, share, n_top in builds:
        print(f"  {week:<10}{when:%m-%d %H:%M}{'':<8}{n:>6}{nb:>7}"
              f"{share:>13.0%}{n_top:>14}")
    print()

    top = h[h["was_elite"]]
    print("THE CHECK: rows this ranks inside the elite share, by the tier stored")
    counts = top["edge_tier"].value_counts()
    for t in TIERS:
        print(f"  {t:<8}{int(counts.get(t, 0)):>6}")
    ok = int(counts.get("elite", 0) + counts.get("strong", 0))
    if len(top):
        print(f"  {ok / len(top):.0%} were stored as elite or strong. The lower "
              f"that is, the less this reconstruction can be trusted.")
    print()

    promote = demoted(h)
    h["new_tier"] = h["edge_tier"]
    h.loc[promote, "new_tier"] = "elite"
    print(f"{int(promote.sum())} archived row(s) were ranked elite and demoted "
          f"to strong.")
    print()

    h["key"] = norm(h["player_name"])
    h["line"] = h["line"].astype(float).round(2)

    r = pd.read_sql(text("""
        SELECT player_id, player_name, game_date, market_code, bookmaker_title,
               line, edge_tier, hit, price_american
        FROM prop_edge_results
        WHERE source = 'live' AND game_date >= :since
    """), eng, params={"since": SINCE})
    if r.empty:
        raise SystemExit(f"no live record rows since {SINCE}")
    r["key"] = norm(r["player_name"])
    r["line"] = r["line"].astype(float).round(2)
    r["board"] = nfl_week(r["game_date"])

    # Same prop, same book, same line, same slate. The stat line can be dated a
    # day either side of kickoff, which is why this joins on the slate.
    cols = ["key", "market_code", "bookmaker_title", "line", "board"]
    lookup = h.drop_duplicates(subset=cols)[cols + ["new_tier"]]
    d = r.merge(lookup, on=cols, how="left")
    missing = int(d["new_tier"].isna().sum())
    d = d[d["new_tier"].notna()].copy()
    # The record row keeps its own stored tier unless it is one of the demoted.
    d["new_tier"] = np.where(
        (d["edge_tier"] == "strong") & (d["new_tier"] == "elite"),
        "elite", d["edge_tier"])
    print(f"{len(r):,} live record rows since {SINCE}, "
          f"{len(d):,} matched to an archived board row, {missing} not matched "
          f"and left alone")
    print()

    d["hitf"] = d["hit"].astype(float)
    d["needed"] = needed(d["price_american"])
    g = d[d["hitf"].notna()]
    for label, col in (("AS STORED", "edge_tier"), ("RESTORED", "new_tier")):
        print(f"{label}, graded rows only")
        print(f"  {'tier':<8}{'picks':>7}{'won':>8}{'needed':>9}{'diff':>8}")
        for t in TIERS:
            s = g[g[col] == t]
            if s.empty:
                print(f"  {t:<8}{0:>7}")
                continue
            won, need = s["hitf"].mean(), s["needed"].mean()
            print(f"  {t:<8}{len(s):>7}{won:>8.1%}{need:>9.1%}{won - need:>+8.1%}")
        print()

    change = d[d["edge_tier"] != d["new_tier"]]
    hist = h[promote]
    if os.getenv("WRITE") != "1":
        print(f"{len(change):,} record row(s) and {len(hist):,} archived row(s) "
              f"would change. Nothing written. WRITE=1 to relabel them.")
        return
    with eng.begin() as conn:
        # History first. The grader copies the tier from history on every run,
        # so a label fixed only in the record is overwritten the next morning.
        for row in hist.itertuples(index=False):
            conn.execute(text("""
                UPDATE prop_edges_history SET edge_tier = 'elite'
                WHERE player_name = :n AND market_code = :m
                  AND bookmaker_key = :b AND commence_time = :c
                  AND edge_tier = 'strong'
            """), {"n": row.player_name, "m": row.market_code,
                   "b": row.bookmaker_key, "c": row.commence_time})
        for row in change.itertuples(index=False):
            conn.execute(text("""
                UPDATE prop_edge_results SET edge_tier = 'elite'
                WHERE source = 'live' AND player_id = :p
                  AND game_date = :d AND market_code = :m
                  AND edge_tier = 'strong'
            """), {"p": row.player_id, "d": row.game_date,
                   "m": row.market_code})
    print(f"relabelled {len(change):,} record row(s) and {len(hist):,} "
          f"archived row(s).")


if __name__ == "__main__":
    main()
