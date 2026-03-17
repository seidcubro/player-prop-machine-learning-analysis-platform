"""NFL data ingestion from nflverse-style sources."""

from typing import Iterable
import os
import pandas as pd
from sqlalchemy import create_engine, text
import nflreadpy


def _db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return url


def _engine():
    return create_engine(_db_url(), pool_pre_ping=True)


def _as_pandas(df):
    if hasattr(df, "to_pandas"):
        return df.to_pandas()
    return df


def _season_range(start: int, end: int) -> list[int]:
    return list(range(start, end + 1))


def ensure_tables():
    ddl = """
    CREATE TABLE IF NOT EXISTS nfl_players (
      player_id TEXT PRIMARY KEY,
      full_name TEXT,
      position TEXT,
      team TEXT
    );
    CREATE TABLE IF NOT EXISTS nfl_games (
      game_id TEXT PRIMARY KEY,
      season INT,
      week INT,
      game_type TEXT,
      game_date DATE,
      home_team TEXT,
      away_team TEXT
    );
    CREATE TABLE IF NOT EXISTS player_game_stats (
      player_id TEXT,
      game_id TEXT,
      season INT,
      week INT,
      game_date DATE,
      opponent TEXT,
      team TEXT,
      position TEXT,
      passing_yards FLOAT,
      passing_tds FLOAT,
      rushing_yards FLOAT,
      rush_attempts FLOAT,
      receiving_yards FLOAT,
      receptions FLOAT,
      touchdowns FLOAT,
      PRIMARY KEY (player_id, game_id)
    );
    """
    with _engine().begin() as conn:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))


def ingest_players():
    df = _as_pandas(nflreadpy.load_players())
    out = pd.DataFrame({
        "player_id": df["player_id"] if "player_id" in df.columns else df["gsis_id"],
        "full_name": df["player_display_name"] if "player_display_name" in df.columns else df.get("display_name"),
        "position": df.get("position"),
        "team": df.get("team"),
    }).dropna(subset=["player_id"]).drop_duplicates(subset=["player_id"])
    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE nfl_players"))
        out.to_sql("nfl_players", conn, if_exists="append", index=False)
    print(f"  ingest_players: {len(out)} rows")


def ingest_schedules(seasons: Iterable[int]):
    df = _as_pandas(nflreadpy.load_schedules(list(seasons)))
    out = pd.DataFrame({
        "game_id": df["game_id"],
        "season": df["season"],
        "week": df["week"],
        "game_type": df.get("game_type"),
        "game_date": pd.to_datetime(df["gameday"], errors="coerce").dt.date,
        "home_team": df["home_team"],
        "away_team": df["away_team"],
    }).dropna(subset=["game_id"]).drop_duplicates(subset=["game_id"])
    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE nfl_games"))
        out.to_sql("nfl_games", conn, if_exists="append", index=False)
    print(f"  ingest_schedules: {len(out)} rows")


def ingest_player_game_stats(seasons: Iterable[int]):
    stats = _as_pandas(nflreadpy.load_player_stats(list(seasons), summary_level="week"))
    if len(stats) == 0:
        raise RuntimeError("load_player_stats returned 0 rows")
    print(f"  stats rows: {len(stats)}")

    with _engine().begin() as conn:
        games = pd.read_sql("SELECT game_id, game_date FROM nfl_games", conn)

    merged = stats.merge(games, on="game_id", how="left")
    print(f"  after game_date join: {len(merged)} rows, game_date null: {merged['game_date'].isna().sum()}")

    def _col(name):
        return merged[name] if name in merged.columns else pd.Series([None] * len(merged))

    rush_tds = pd.to_numeric(_col("rushing_tds"), errors="coerce").fillna(0)
    rec_tds  = pd.to_numeric(_col("receiving_tds"), errors="coerce").fillna(0)
    pass_tds_n = pd.to_numeric(_col("passing_tds"), errors="coerce").fillna(0)
    touchdowns = rush_tds + rec_tds + pass_tds_n

    out = pd.DataFrame({
        "player_id":       merged["player_id"],
        "game_id":         merged["game_id"],
        "season":          merged["season"],
        "week":            merged["week"],
        "game_date":       merged["game_date"],
        "opponent":        merged["opponent_team"],
        "team":            merged["team"],
        "position":        _col("position"),
        "passing_yards":   _col("passing_yards"),
        "passing_tds":     _col("passing_tds"),
        "rushing_yards":   _col("rushing_yards"),
        "rush_attempts":   _col("carries"),
        "receiving_yards": _col("receiving_yards"),
        "receptions":      _col("receptions"),
        "touchdowns":      touchdowns,
    }).dropna(subset=["player_id", "game_id"])

    print(f"  final rows after dropna: {len(out)}")
    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE player_game_stats"))
        out.to_sql("player_game_stats", conn, if_exists="append", index=False)
    print(f"  ingest_player_game_stats: {len(out)} rows written")


def run():
    season_start = int(os.getenv("SEASON_START", "2022"))
    season_end   = int(os.getenv("SEASON_END",   "2025"))
    seasons = _season_range(season_start, season_end)
    print(f"Running ingestion for seasons {season_start}-{season_end}")
    ensure_tables()
    print("Tables ensured.")
    ingest_players()
    ingest_schedules(seasons)
    ingest_player_game_stats(seasons)
    print("Ingestion complete.")


if __name__ == "__main__":
    run()
