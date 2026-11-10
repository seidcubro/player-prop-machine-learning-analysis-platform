"""Put the tiers back on the live picks that were ranked and then demoted.

From 28 October 2026 the board tiered by rank within each market, and a step
that ran afterwards pushed every elite pick whose published probability sat
under its price down to strong (see demote_dead_bets in build_prop_edges). The
picks were published and graded, so the outcomes in the record are right. The
label on them is not: weeks 8 and 9 graded no elite pick at all, and strong
hit 64% because it was holding them.

This recomputes the label and nothing else. For every board since the rank
rule went live it takes the rows as they were archived in prop_edges_history,
with the gap_share that was stored at the time, and runs them through
gap_tier.tiers_by_rank, which is the function the board runs. Side, line,
price, projection and result are not touched.

Three things this is not.

It is not exact. A build ranks everything it priced, and the archive keeps one
final row per prop, so a Thursday game is ranked here beside Sunday rows it may
have been ranked without. Same population production describes as one board,
not the same instant.

It uses today's shares. The rule ran at 10/20/30 until 8 November and 20/20/20
after. The site describes 20/20/20, so the record is relabelled to the rule
the site describes instead of to two rules nobody can tell apart on the page.

It cannot see a pick that refuse_contradicted_picks demoted on purpose. That
only fires on a player with two to four games this season, which by week 8 is
a handful, and they are promoted back here along with the rest.

Dry run by default. It prints what would move and how each tier would have
scored, and writes nothing.

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


def main():
    if not gap_tier.v2_thresholds():
        raise SystemExit("no gap_tiers.json in the artifact directory, so "
                         "there is no rule to relabel with")
    eng = create_engine(bp.DATABASE_URL, future=True)

    h = pd.read_sql(text("""
        SELECT player_name, market_code, bookmaker_title, line, gap_share,
               (commence_time AT TIME ZONE 'America/New_York')::date AS game_date
        FROM prop_edges_history
        WHERE commence_time >= :since AND gap_share IS NOT NULL
    """), eng, params={"since": SINCE})
    if h.empty:
        raise SystemExit(f"no archived board rows with a gap since {SINCE}")
    h["board"] = nfl_week(h["game_date"])
    h["new_tier"] = "small"
    for (m, _), idx in h.groupby(["market_code", "board"]).groups.items():
        h.loc[idx, "new_tier"] = gap_tier.tiers_by_rank(
            h.loc[idx, "gap_share"].tolist(), m)
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
    print(f"{len(r):,} live record rows since {SINCE}, "
          f"{len(d):,} matched to an archived board row, {missing} not matched "
          f"and left alone")
    print()

    print("WHAT MOVES (rows: stored tier, columns: recomputed)")
    table = pd.crosstab(d["edge_tier"], d["new_tier"]).reindex(
        index=TIERS, columns=TIERS, fill_value=0)
    print(table.to_string())
    print()

    d["hitf"] = d["hit"].astype(float)
    d["needed"] = needed(d["price_american"])
    g = d[d["hitf"].notna()]
    for label, col in (("AS STORED", "edge_tier"), ("RECOMPUTED", "new_tier")):
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
    if os.getenv("WRITE") != "1":
        print(f"{len(change):,} row(s) would change. Nothing written. WRITE=1 "
              f"to relabel them.")
        return
    with eng.begin() as conn:
        for row in change.itertuples(index=False):
            conn.execute(text("""
                UPDATE prop_edge_results SET edge_tier = :t
                WHERE source = 'live' AND player_id = :p
                  AND game_date = :d AND market_code = :m
            """), {"t": row.new_tier, "p": row.player_id,
                   "d": row.game_date, "m": row.market_code})
    print(f"relabelled {len(change):,} row(s).")


if __name__ == "__main__":
    main()
