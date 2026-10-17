"""Why do the board and the projections page disagree about one player?

Audit check [10] says they do, on Tyler Huntley:

    pass_completions  board 17.9994  page 19.1266
    pass_att          board 26.8042  page 27.4000
    pass_td           board  1.0000  page  1.1498

The site contradicting itself in public is worse than either number being
slightly off, so this finds out which step diverged rather than guessing.

pass_td is already explained by reading the code: build_prop_edges rebuilds the
touchdown markets from a distribution centred on the point projection
(COUNT_MARKETS, count_distribution) so the probability and the median cannot
contradict each other, and build_projections does not. That is a real
divergence by construction and both pages should do the same thing.

The other two are not explained by anything in the source. Both paths call the
same loader, the same apply_current_context, the same vacated-volume factor
(whose guard in build_projections is redundant, since factor() already returns
1.0 when level() is None). So the suspicion is upstream of all of it: that the
two are reading DIFFERENT FEATURE ROWS for the same player and game.

player_market_features is unique on
(player_id, market_id, as_of_game_date, opponent, lookback), with opponent in
the key. One player-game can therefore hold several rows under several
spellings or several opponents, and the board matches odds to features by name
while the page iterates features directly. If they land on different rows they
will disagree forever and no amount of correcting the corrections will fix it.

Read-only.

    docker compose run --rm -e PLAYER="Tyler Huntley" training \\
        python diagnose_board_vs_page.py
"""

import os

import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

PLAYER = os.getenv("PLAYER", "Tyler Huntley")


def show(title, df):
    print()
    print(title)
    print("-" * len(title))
    if df.empty:
        print("  (nothing)")
        return
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(df.to_string(index=False))


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    print(f"player: {PLAYER}")

    # player_projections carries player_id and player_name together, and its
    # player_id is TEXT while other tables disagree about that, so the id is
    # taken from here rather than from a join that may not type-match.
    ids = pd.read_sql(text("""
        SELECT DISTINCT player_id, player_name, position, team
        FROM player_projections WHERE player_name ILIKE :n
    """), eng, params={"n": PLAYER})
    show("who the name resolves to", ids)
    if ids.empty:
        print("  not in player_projections; nothing to compare")
        return
    pids = [str(x) for x in ids["player_id"].tolist()]

    # Every feature row this player has for an upcoming game, per market and
    # lookback. More than one row per (market, lookback) is the bug.
    feats = pd.read_sql(text("""
        SELECT m.code AS market, f.lookback, f.as_of_game_date, f.opponent,
               f.mean, f.weighted_mean, f.stddev
        FROM player_market_features f
        JOIN prop_markets m ON m.id = f.market_id
        WHERE f.player_id::text = ANY(:pids)
        ORDER BY m.code, f.lookback, f.as_of_game_date DESC
        LIMIT 60
    """), eng, params={"pids": pids})
    show("feature rows, newest first", feats)

    dupes = (feats.groupby(["market", "lookback", "as_of_game_date"]).size()
             .reset_index(name="rows").query("rows > 1"))
    if not dupes.empty:
        show("SAME player, market, lookback and date under more than one row "
             "(opponent is in the unique key, so these are distinct rows the "
             "two builders can pick between)", dupes)
    else:
        print("\n  no duplicate feature rows, so both builders see the same one")

    board = pd.read_sql(text("""
        SELECT market_code, line, projection, projection_median, win_prob,
               edge_tier, recommended_side, bookmaker_key
        FROM prop_edges
        WHERE player_name ILIKE :n
        ORDER BY market_code
    """), eng, params={"n": PLAYER})
    show("what the board published", board)

    page = pd.read_sql(text("""
        SELECT market_code, projection, p50 AS projection_median,
               projection_raw, p50_raw, game_date, model_name
        FROM player_projections
        WHERE player_name ILIKE :n
        ORDER BY market_code
    """), eng, params={"n": PLAYER})
    show("what the projections page published", page)

    if not board.empty and not page.empty:
        merged = board[["market_code", "projection", "projection_median"]].merge(
            page[["market_code", "projection", "projection_median"]],
            on="market_code", suffixes=("_board", "_page"))
        merged["proj_diff"] = merged["projection_board"] - merged["projection_page"]
        merged["median_diff"] = (merged["projection_median_board"]
                                 - merged["projection_median_page"])
        show("side by side", merged)
        print()
        print("  Reading it: if proj_diff is zero and median_diff is not, the")
        print("  point projection agrees and only the ladder diverged, which")
        print("  points at the count-market rebuild or the interval correction.")
        print("  If proj_diff is non-zero too, they are reading different rows")
        print("  or different models and the duplicate table above says which.")


if __name__ == "__main__":
    main()
