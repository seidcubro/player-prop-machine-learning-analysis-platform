"""How many games does every model here never see because nothing happened in them?

The grader was leaving out players who took the field and recorded nothing,
and that is fixed. But the grader was only the last place the hole showed.
The game log itself has no row for a receiver who played and was never thrown
to, and everything is built from the game log:

    training    the label is the stat line, so a game with no stat line is not
                a training row. The models learn what a player does in the
                games where he does something. For a backup tight end that
                reads high, and it reads high on exactly the 0.5 and 1.5 lines
                the elite tier likes.
    features    his five-game window skips the blank games, so his recent form
                is the form of his good days.
    backtest    backfill_track_record.py joins to labelled rows, so the 8,548
                rows every calibrator and tier rule is fitted on are missing
                the same losses the live record was.

This counts it. For each season: skill-position player-games with a snap on
offense, how many have no row in the game log, and how many of those belong to
a player the books priced that season. Then the same for rows that exist but
carry no value for the stat a prop would settle on.

If the priced count is a few dozen a season, this is a footnote. If it is
hundreds, the repair is to write those games into the log as zeros at
ingestion and rebuild, and it comes before any more tuning.

Read-only.

    docker compose run --rm training python diagnose_zero_games.py
"""

import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = pd.read_sql(text("""
        SELECT sc.season, sc.position,
               COUNT(*) AS played,
               COUNT(*) FILTER (WHERE s.player_id IS NULL) AS no_row,
               COUNT(*) FILTER (WHERE s.player_id IS NULL
                                  AND pr.player_id IS NOT NULL) AS no_row_priced,
               COUNT(*) FILTER (WHERE pr.player_id IS NOT NULL) AS played_priced,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY sc.offense_snaps)
                   FILTER (WHERE s.player_id IS NULL) AS median_snaps
        FROM snap_counts sc
        LEFT JOIN (
            SELECT DISTINCT player_id, season, week FROM player_game_stats_app
        ) s ON s.player_id = sc.player_id AND s.season = sc.season
           AND s.week = sc.week
        LEFT JOIN (
            SELECT DISTINCT player_id, season FROM prop_edge_results
        ) pr ON pr.player_id = sc.player_id AND pr.season = sc.season
        WHERE sc.offense_snaps > 0 AND sc.season >= 2021
          AND sc.position IN ('QB', 'RB', 'WR', 'TE', 'FB')
        GROUP BY 1, 2 ORDER BY 1, 2
    """), eng)
    print("Player-games with a snap on offense and no row in the game log.")
    print(f"  {'season':<8}{'pos':<5}{'played':>8}{'no row':>8}{'share':>8}"
          f"{'priced':>9}{'of priced games':>17}{'median snaps':>14}")
    for r in d.itertuples():
        share = r.no_row / r.played if r.played else 0
        ps = r.no_row_priced / r.played_priced if r.played_priced else 0
        ms = "" if pd.isna(r.median_snaps) else f"{r.median_snaps:.0f}"
        print(f"  {r.season:<8}{r.position:<5}{r.played:>8}{r.no_row:>8}"
              f"{share:>8.1%}{r.no_row_priced:>9}{ps:>17.1%}{ms:>14}")
    tot = d.groupby("season")[["played", "no_row", "no_row_priced",
                               "played_priced"]].sum()
    print("\n  by season")
    for season, r in tot.iterrows():
        print(f"  {season:<8}{'all':<5}{int(r.played):>8}{int(r.no_row):>8}"
              f"{r.no_row / r.played:>8.1%}{int(r.no_row_priced):>9}"
              f"{r.no_row_priced / max(r.played_priced, 1):>17.1%}")

    n = pd.read_sql(text("""
        SELECT s.season,
               COUNT(*) FILTER (WHERE s.position IN ('WR', 'TE', 'RB')
                                  AND s.receptions IS NULL) AS no_receptions,
               COUNT(*) FILTER (WHERE s.position = 'RB'
                                  AND s.carries IS NULL) AS no_carries,
               COUNT(*) AS rows
        FROM player_game_stats_app s
        WHERE s.season >= 2021 AND s.position IN ('QB', 'RB', 'WR', 'TE', 'FB')
        GROUP BY 1 ORDER BY 1
    """), eng)
    print("\nRows that exist but hold no value for the stat a prop settles on "
          "(these are skipped the same way).")
    print(f"  {'season':<8}{'rows':>8}{'catches blank':>15}{'carries blank':>15}")
    for r in n.itertuples():
        print(f"  {r.season:<8}{r.rows:>8}{r.no_receptions:>15}{r.no_carries:>15}")

    print("\n'priced' counts a player the record holds at least one prop for "
          "in that season, so it is the part that touches the board. 'of "
          "priced games' is the share of those players' games that are "
          "missing.")


if __name__ == "__main__":
    main()
