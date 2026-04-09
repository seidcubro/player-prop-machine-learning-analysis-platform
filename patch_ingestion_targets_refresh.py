from pathlib import Path

path = Path(r".\jobs\ingestion\app\etl\nflverse_ingest.py")
text = path.read_text(encoding="utf-8-sig")

old = '''        conn.execute(text("DROP TABLE IF EXISTS player_game_stats_app"))
        conn.execute(text("CREATE TABLE player_game_stats_app AS SELECT * FROM player_game_stats"))
        print("  sync_targets_from_pbp: rebuilt player_game_stats_app")
'''

new = '''        conn.execute(text("TRUNCATE TABLE player_game_stats_app"))
        conn.execute(text("INSERT INTO player_game_stats_app SELECT * FROM player_game_stats"))
        print("  sync_targets_from_pbp: refreshed player_game_stats_app")
'''

if old not in text:
    raise SystemExit("Could not find old player_game_stats_app rebuild block")

text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("Patched player_game_stats_app refresh logic successfully.")
