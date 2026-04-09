from pathlib import Path

path = Path(r".\jobs\ingestion\app\etl\nflverse_ingest.py")
text = path.read_text(encoding="utf-8-sig")

old_block = '''def ingest_ff_opportunity(seasons: Iterable[int]):
    seasons_list = list(seasons)
    df = _as_pandas(nflreadpy.load_ff_opportunity(seasons_list))
    print(f"  ff_opportunity raw: {len(df)} rows")
'''

new_block = '''def sync_targets_from_pbp():
    """
    Permanent target fix:
    Use aggregated play-by-play receiver rows as the source of truth for targets.
    In pbp_player_game, receiver rows use total_plays = number of pass targets for that player-game.
    Merge those targets back into player_game_stats, then refresh player_game_stats_app.
    """
    with _engine().begin() as conn:
        updated = conn.execute(text("""
            UPDATE player_game_stats pgs
            SET targets = src.targets
            FROM (
                SELECT
                    player_id,
                    game_id,
                    CAST(total_plays AS FLOAT) AS targets
                FROM pbp_player_game
                WHERE player_id IS NOT NULL
                  AND game_id IS NOT NULL
                  AND total_plays IS NOT NULL
                  AND total_plays > 0
            ) AS src
            WHERE pgs.player_id = src.player_id
              AND pgs.game_id = src.game_id
        """))
        print(f"  sync_targets_from_pbp: updated {updated.rowcount} player_game_stats rows")

        conn.execute(text("DROP TABLE IF EXISTS player_game_stats_app"))
        conn.execute(text("CREATE TABLE player_game_stats_app AS SELECT * FROM player_game_stats"))
        print("  sync_targets_from_pbp: rebuilt player_game_stats_app")


def ingest_ff_opportunity(seasons: Iterable[int]):
    seasons_list = list(seasons)
    df = _as_pandas(nflreadpy.load_ff_opportunity(seasons_list))
    print(f"  ff_opportunity raw: {len(df)} rows")
'''

if old_block not in text:
    raise SystemExit("Could not find insertion point before ingest_ff_opportunity()")

text = text.replace(old_block, new_block, 1)

old_run_block = '''    print("--- PBP aggregated ---")
    ingest_pbp_aggregated(seasons)

    print("Ingestion complete.")
'''

new_run_block = '''    print("--- PBP aggregated ---")
    ingest_pbp_aggregated(seasons)

    print("--- sync targets from PBP into player_game_stats ---")
    sync_targets_from_pbp()

    print("Ingestion complete.")
'''

if old_run_block not in text:
    raise SystemExit("Could not find run() PBP block")

text = text.replace(old_run_block, new_run_block, 1)

path.write_text(text, encoding="utf-8")
print("Patched nflverse_ingest.py successfully.")
