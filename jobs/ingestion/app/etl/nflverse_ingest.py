"""NFL data ingestion - full expanded pipeline.

Sources:
- load_player_stats       : unified weekly stats (offense/defense/ST/kicking)
- load_schedules          : game context, venue, weather, Vegas lines
- load_snap_counts        : snap percentages per player per game
- load_nextgen_stats      : NGS passing/receiving/rushing
- load_pfr_advstats       : PFR advanced passing/rushing/receiving/defense
- load_ftn_charting       : play-level FTN data aggregated to player-game
- load_participation      : play-level participation aggregated to player-game
- load_pbp                : play-by-play aggregated to player-game
- load_ff_opportunity     : expected vs actual opportunity metrics
- load_depth_charts       : weekly depth chart position
- load_rosters_weekly     : weekly roster, status, physical attributes
- load_injuries           : weekly injury report
- load_players            : player directory
"""

import os
from typing import Iterable
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


def _as_pandas(df) -> pd.DataFrame:
    if hasattr(df, "to_pandas"):
        return df.to_pandas()
    return df


def _col(df: pd.DataFrame, name: str, default=None) -> pd.Series:
    return df[name] if name in df.columns else pd.Series([default] * len(df))


def _season_range(start: int, end: int) -> list[int]:
    return list(range(start, end + 1))


def ensure_tables():
    """Create all staging tables."""
    statements = [

        # player directory
        """
        CREATE TABLE IF NOT EXISTS nfl_players (
            player_id   TEXT PRIMARY KEY,
            full_name   TEXT,
            position    TEXT,
            team        TEXT
        )
        """,

        # schedules + venue + weather + Vegas
        """
        CREATE TABLE IF NOT EXISTS nfl_games (
            game_id          TEXT PRIMARY KEY,
            season           INT,
            week             INT,
            game_type        TEXT,
            game_date        DATE,
            home_team        TEXT,
            away_team        TEXT,
            home_score       FLOAT,
            away_score       FLOAT,
            result           FLOAT,
            total            FLOAT,
            spread_line      FLOAT,
            total_line       FLOAT,
            home_moneyline   FLOAT,
            away_moneyline   FLOAT,
            roof             TEXT,
            surface          TEXT,
            temp             FLOAT,
            wind             FLOAT,
            overtime         INT,
            home_rest        INT,
            away_rest        INT,
            home_qb_name     TEXT,
            away_qb_name     TEXT,
            div_game         INT
        )
        """,

        # unified weekly player stats
        """
        CREATE TABLE IF NOT EXISTS player_game_stats (
            player_id                   TEXT,
            game_id                     TEXT,
            season                      INT,
            week                        INT,
            season_type                 TEXT,
            game_date                   DATE,
            opponent                    TEXT,
            team                        TEXT,
            position                    TEXT,
            position_group              TEXT,
            passing_yards               FLOAT,
            passing_tds                 FLOAT,
            passing_interceptions       FLOAT,
            attempts                    FLOAT,
            completions                 FLOAT,
            passing_air_yards           FLOAT,
            passing_yards_after_catch   FLOAT,
            passing_first_downs         FLOAT,
            passing_epa                 FLOAT,
            passing_cpoe                FLOAT,
            passing_2pt_conversions     FLOAT,
            sacks_suffered              FLOAT,
            sack_yards_lost             FLOAT,
            sack_fumbles                FLOAT,
            sack_fumbles_lost           FLOAT,
            pacr                        FLOAT,
            rushing_yards               FLOAT,
            rushing_tds                 FLOAT,
            carries                     FLOAT,
            rushing_first_downs         FLOAT,
            rushing_epa                 FLOAT,
            rushing_2pt_conversions     FLOAT,
            rushing_fumbles             FLOAT,
            rushing_fumbles_lost        FLOAT,
            receiving_yards             FLOAT,
            receiving_tds               FLOAT,
            receptions                  FLOAT,
            targets                     FLOAT,
            receiving_air_yards         FLOAT,
            receiving_yards_after_catch FLOAT,
            receiving_first_downs       FLOAT,
            receiving_epa               FLOAT,
            receiving_2pt_conversions   FLOAT,
            receiving_fumbles           FLOAT,
            receiving_fumbles_lost      FLOAT,
            target_share                FLOAT,
            air_yards_share             FLOAT,
            wopr                        FLOAT,
            racr                        FLOAT,
            def_tackles_solo            FLOAT,
            def_tackles_with_assist     FLOAT,
            def_tackle_assists          FLOAT,
            def_tackles_for_loss        FLOAT,
            def_tackles_for_loss_yards  FLOAT,
            def_sacks                   FLOAT,
            def_sack_yards              FLOAT,
            def_qb_hits                 FLOAT,
            def_interceptions           FLOAT,
            def_interception_yards      FLOAT,
            def_pass_defended           FLOAT,
            def_tds                     FLOAT,
            def_fumbles                 FLOAT,
            def_fumbles_forced          FLOAT,
            def_safeties                FLOAT,
            fg_made                     FLOAT,
            fg_att                      FLOAT,
            fg_missed                   FLOAT,
            fg_blocked                  FLOAT,
            fg_long                     FLOAT,
            fg_pct                      FLOAT,
            fg_made_0_19                FLOAT,
            fg_made_20_29               FLOAT,
            fg_made_30_39               FLOAT,
            fg_made_40_49               FLOAT,
            fg_made_50_59               FLOAT,
            fg_made_60_                 FLOAT,
            fg_missed_0_19              FLOAT,
            fg_missed_20_29             FLOAT,
            fg_missed_30_39             FLOAT,
            fg_missed_40_49             FLOAT,
            fg_missed_50_59             FLOAT,
            fg_missed_60_               FLOAT,
            fg_made_distance            FLOAT,
            fg_missed_distance          FLOAT,
            gwfg_made                   FLOAT,
            gwfg_att                    FLOAT,
            gwfg_missed                 FLOAT,
            gwfg_blocked                FLOAT,
            gwfg_distance               FLOAT,
            pat_made                    FLOAT,
            pat_att                     FLOAT,
            pat_missed                  FLOAT,
            pat_blocked                 FLOAT,
            pat_pct                     FLOAT,
            special_teams_tds           FLOAT,
            penalties                   FLOAT,
            penalty_yards               FLOAT,
            fumble_recovery_own         FLOAT,
            fumble_recovery_yards_own   FLOAT,
            fumble_recovery_opp         FLOAT,
            fumble_recovery_yards_opp   FLOAT,
            fumble_recovery_tds         FLOAT,
            punt_returns                FLOAT,
            punt_return_yards           FLOAT,
            kickoff_returns             FLOAT,
            kickoff_return_yards        FLOAT,
            misc_yards                  FLOAT,
            fantasy_points              FLOAT,
            fantasy_points_ppr          FLOAT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # snap counts
        """
        CREATE TABLE IF NOT EXISTS snap_counts (
            player_id       TEXT,
            game_id         TEXT,
            season          INT,
            week            INT,
            team            TEXT,
            opponent        TEXT,
            position        TEXT,
            offense_snaps   FLOAT,
            offense_pct     FLOAT,
            defense_snaps   FLOAT,
            defense_pct     FLOAT,
            st_snaps        FLOAT,
            st_pct          FLOAT,
            pfr_player_id   TEXT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # NGS passing
        """
        CREATE TABLE IF NOT EXISTS ngs_passing (
            player_id                           TEXT,
            season                              INT,
            week                                INT,
            season_type                         TEXT,
            team                                TEXT,
            avg_time_to_throw                   FLOAT,
            avg_completed_air_yards             FLOAT,
            avg_intended_air_yards              FLOAT,
            avg_air_yards_differential          FLOAT,
            aggressiveness                      FLOAT,
            max_completed_air_distance          FLOAT,
            avg_air_yards_to_sticks             FLOAT,
            completion_percentage               FLOAT,
            expected_completion_percentage      FLOAT,
            completion_percentage_above_expectation FLOAT,
            avg_air_distance                    FLOAT,
            max_air_distance                    FLOAT,
            passer_rating                       FLOAT,
            attempts                            INT,
            completions                         INT,
            pass_yards                          FLOAT,
            pass_touchdowns                     INT,
            interceptions                       INT,
            PRIMARY KEY (player_id, season, week)
        )
        """,

        # NGS receiving
        """
        CREATE TABLE IF NOT EXISTS ngs_receiving (
            player_id                           TEXT,
            season                              INT,
            week                                INT,
            season_type                         TEXT,
            team                                TEXT,
            avg_cushion                         FLOAT,
            avg_separation                      FLOAT,
            avg_intended_air_yards              FLOAT,
            percent_share_of_intended_air_yards FLOAT,
            avg_yac                             FLOAT,
            avg_expected_yac                    FLOAT,
            avg_yac_above_expectation           FLOAT,
            catch_percentage                    FLOAT,
            receptions                          INT,
            targets                             INT,
            yards                               FLOAT,
            rec_touchdowns                      INT,
            PRIMARY KEY (player_id, season, week)
        )
        """,

        # NGS rushing
        """
        CREATE TABLE IF NOT EXISTS ngs_rushing (
            player_id                           TEXT,
            season                              INT,
            week                                INT,
            season_type                         TEXT,
            team                                TEXT,
            efficiency                          FLOAT,
            percent_attempts_gte_eight_defenders FLOAT,
            avg_time_to_los                     FLOAT,
            rush_yards_over_expected            FLOAT,
            avg_rush_yards                      FLOAT,
            rush_yards_over_expected_per_att    FLOAT,
            rush_pct_over_expected              FLOAT,
            expected_rush_yards                 FLOAT,
            rush_attempts                       INT,
            rush_yards                          FLOAT,
            rush_touchdowns                     INT,
            PRIMARY KEY (player_id, season, week)
        )
        """,

        # PFR advanced passing
        """
        CREATE TABLE IF NOT EXISTS pfr_adv_passing (
            player_id               TEXT,
            game_id                 TEXT,
            season                  INT,
            week                    INT,
            team                    TEXT,
            opponent                TEXT,
            times_pressured         FLOAT,
            times_pressured_pct     FLOAT,
            times_blitzed           FLOAT,
            times_hit               FLOAT,
            times_hurried           FLOAT,
            times_sacked            FLOAT,
            passing_drops           FLOAT,
            passing_drop_pct        FLOAT,
            passing_bad_throws      FLOAT,
            passing_bad_throw_pct   FLOAT,
            def_times_blitzed       FLOAT,
            def_times_hitqb         FLOAT,
            def_times_hurried       FLOAT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # PFR advanced rushing
        """
        CREATE TABLE IF NOT EXISTS pfr_adv_rushing (
            player_id                           TEXT,
            game_id                             TEXT,
            season                              INT,
            week                                INT,
            team                                TEXT,
            opponent                            TEXT,
            carries                             FLOAT,
            rushing_yards_before_contact        FLOAT,
            rushing_yards_before_contact_avg    FLOAT,
            rushing_yards_after_contact         FLOAT,
            rushing_yards_after_contact_avg     FLOAT,
            rushing_broken_tackles              FLOAT,
            receiving_broken_tackles            FLOAT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # PFR advanced receiving
        """
        CREATE TABLE IF NOT EXISTS pfr_adv_receiving (
            player_id               TEXT,
            game_id                 TEXT,
            season                  INT,
            week                    INT,
            team                    TEXT,
            opponent                TEXT,
            receiving_broken_tackles FLOAT,
            receiving_drop          FLOAT,
            receiving_drop_pct      FLOAT,
            receiving_int           FLOAT,
            receiving_rat           FLOAT,
            rushing_broken_tackles  FLOAT,
            passing_drops           FLOAT,
            passing_drop_pct        FLOAT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # PFR advanced defense
        """
        CREATE TABLE IF NOT EXISTS pfr_adv_defense (
            player_id                   TEXT,
            game_id                     TEXT,
            season                      INT,
            week                        INT,
            team                        TEXT,
            opponent                    TEXT,
            def_targets                 FLOAT,
            def_completions_allowed     FLOAT,
            def_completion_pct          FLOAT,
            def_yards_allowed           FLOAT,
            def_yards_allowed_per_tgt   FLOAT,
            def_yards_allowed_per_cmp   FLOAT,
            def_air_yards_completed     FLOAT,
            def_yards_after_catch       FLOAT,
            def_adot                    FLOAT,
            def_ints                    FLOAT,
            def_receiving_td_allowed    FLOAT,
            def_passer_rating_allowed   FLOAT,
            def_sacks                   FLOAT,
            def_pressures               FLOAT,
            def_missed_tackles          FLOAT,
            def_missed_tackle_pct       FLOAT,
            def_times_blitzed           FLOAT,
            def_times_hitqb             FLOAT,
            def_times_hurried           FLOAT,
            def_tackles_combined        FLOAT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # FTN charting aggregated to player-game
        """
        CREATE TABLE IF NOT EXISTS ftn_charting_game (
            player_id               TEXT,
            game_id                 TEXT,
            season                  INT,
            week                    INT,
            first_read_targets      INT,
            second_read_targets     INT,
            third_read_targets      INT,
            total_charted_targets   INT,
            first_read_target_rate  FLOAT,
            play_action_snaps       INT,
            play_action_pct         FLOAT,
            screen_pass_targets     INT,
            screen_pass_pct         FLOAT,
            qb_out_of_pocket_snaps  INT,
            qb_out_of_pocket_pct    FLOAT,
            motion_snaps            INT,
            motion_pct              FLOAT,
            avg_blitzers_faced      FLOAT,
            avg_box_defenders       FLOAT,
            drops                   INT,
            catchable_targets       INT,
            drop_rate               FLOAT,
            contested_targets       INT,
            is_no_huddle_snaps      INT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # participation aggregated to player-game
        """
        CREATE TABLE IF NOT EXISTS participation_game (
            player_id               TEXT,
            game_id                 TEXT,
            season                  INT,
            week                    INT,
            routes_run              INT,
            pass_snaps              INT,
            run_snaps               INT,
            shotgun_snaps           INT,
            shotgun_pct             FLOAT,
            under_center_snaps      INT,
            under_center_pct        FLOAT,
            man_coverage_snaps      INT,
            man_coverage_pct        FLOAT,
            zone_coverage_snaps     INT,
            zone_coverage_pct       FLOAT,
            avg_defenders_in_box    FLOAT,
            avg_pass_rushers        FLOAT,
            was_pressure_snaps      INT,
            was_pressure_pct        FLOAT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # PBP aggregated to player-game
        """
        CREATE TABLE IF NOT EXISTS pbp_player_game (
            player_id               TEXT,
            game_id                 TEXT,
            season                  INT,
            week                    INT,
            team                    TEXT,
            avg_air_yards_target    FLOAT,
            avg_yac                 FLOAT,
            avg_epa_per_play        FLOAT,
            avg_cp                  FLOAT,
            avg_cpoe                FLOAT,
            red_zone_targets        INT,
            red_zone_target_rate    FLOAT,
            red_zone_carries        INT,
            red_zone_carry_rate     INT,
            third_down_targets      INT,
            third_down_target_rate  FLOAT,
            target_left_pct         FLOAT,
            target_middle_pct       FLOAT,
            target_right_pct        FLOAT,
            run_left_pct            FLOAT,
            run_middle_pct          FLOAT,
            run_right_pct           FLOAT,
            shotgun_pct             FLOAT,
            avg_xyac                FLOAT,
            avg_vegas_wp            FLOAT,
            total_plays             INT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # fantasy opportunity expected vs actual
        """
        CREATE TABLE IF NOT EXISTS ff_opportunity (
            player_id                   TEXT,
            game_id                     TEXT,
            season                      INT,
            week                        INT,
            position                    TEXT,
            team                        TEXT,
            rec_yards_gained            FLOAT,
            rec_yards_gained_exp        FLOAT,
            rec_yards_gained_diff       FLOAT,
            rush_yards_gained           FLOAT,
            rush_yards_gained_exp       FLOAT,
            rush_yards_gained_diff      FLOAT,
            pass_yards_gained           FLOAT,
            pass_yards_gained_exp       FLOAT,
            pass_yards_gained_diff      FLOAT,
            receptions                  FLOAT,
            receptions_exp              FLOAT,
            receptions_diff             FLOAT,
            rec_attempt                 FLOAT,
            rush_attempt                FLOAT,
            pass_attempt                FLOAT,
            rec_touchdown               FLOAT,
            rec_touchdown_exp           FLOAT,
            rush_touchdown              FLOAT,
            rush_touchdown_exp          FLOAT,
            total_fantasy_points        FLOAT,
            total_fantasy_points_exp    FLOAT,
            total_fantasy_points_diff   FLOAT,
            PRIMARY KEY (player_id, game_id)
        )
        """,

        # depth charts
        """
        CREATE TABLE IF NOT EXISTS depth_charts (
            player_id       TEXT,
            season          INT,
            week            FLOAT,
            team            TEXT,
            position        TEXT,
            depth_position  TEXT,
            depth_team      INT,
            game_type       TEXT,
            PRIMARY KEY (player_id, season, week, depth_position)
        )
        """,

        # weekly rosters
        """
        CREATE TABLE IF NOT EXISTS rosters_weekly (
            player_id               TEXT,
            season                  INT,
            week                    INT,
            team                    TEXT,
            position                TEXT,
            depth_chart_position    TEXT,
            status                  TEXT,
            status_description_abbr TEXT,
            jersey_number           TEXT,
            height                  TEXT,
            weight                  FLOAT,
            years_exp               FLOAT,
            pfr_id                  TEXT,
            espn_id                 TEXT,
            pff_id                  TEXT,
            PRIMARY KEY (player_id, season, week)
        )
        """,

        # injury report
        """
        CREATE TABLE IF NOT EXISTS injuries (
            player_id                   TEXT,
            season                      INT,
            week                        INT,
            team                        TEXT,
            position                    TEXT,
            report_primary_injury       TEXT,
            report_secondary_injury     TEXT,
            report_status               TEXT,
            practice_primary_injury     TEXT,
            practice_secondary_injury   TEXT,
            practice_status             TEXT,
            PRIMARY KEY (player_id, season, week)
        )
        """,

        # ID crosswalk
        """
        CREATE TABLE IF NOT EXISTS player_id_crosswalk (
            gsis_id     TEXT PRIMARY KEY,
            pfr_id      TEXT,
            espn_id     TEXT,
            pff_id      TEXT
        )
        """,
    ]

    with _engine().begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt.strip()))
    print("  Tables ensured.")


def ingest_players():
    df = _as_pandas(nflreadpy.load_players())
    out = pd.DataFrame({
        "player_id": df["player_id"] if "player_id" in df.columns else df["gsis_id"],
        "full_name": df["player_display_name"] if "player_display_name" in df.columns else _col(df, "display_name"),
        "position":  _col(df, "position"),
        "team":      _col(df, "team"),
    }).dropna(subset=["player_id"]).drop_duplicates(subset=["player_id"])
    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE nfl_players"))
        out.to_sql("nfl_players", conn, if_exists="append", index=False)
    print(f"  ingest_players: {len(out)} rows")
    
def sync_players_dimension():
    """
    Keep the app-facing `players` dimension in sync with nfl_players.
    - preserves integer players.id
    - uses external_id as the natural key
    - fills real names from nfl_players.full_name
    """
    sql = """
    INSERT INTO players (external_id, first_name, last_name, name, position, team)
    SELECT
        np.player_id AS external_id,
        CASE
            WHEN strpos(trim(np.full_name), ' ') > 0
                THEN split_part(trim(np.full_name), ' ', 1)
            ELSE trim(np.full_name)
        END AS first_name,
        CASE
            WHEN strpos(trim(np.full_name), ' ') > 0
                THEN regexp_replace(trim(np.full_name), '^\\S+\\s+', '')
            ELSE NULL
        END AS last_name,
        trim(np.full_name) AS name,
        np.position,
        np.team
    FROM nfl_players np
    WHERE np.player_id IS NOT NULL
    ON CONFLICT (external_id) DO UPDATE SET
        first_name = EXCLUDED.first_name,
        last_name  = EXCLUDED.last_name,
        name       = EXCLUDED.name,
        position   = EXCLUDED.position,
        team       = EXCLUDED.team;
    """
    with _engine().begin() as conn:
        conn.execute(text(sql))
        # optional cleanup for rows with blank names
        conn.execute(text("""
            UPDATE players
            SET name = external_id
            WHERE name IS NULL OR btrim(name) = '';
        """))
    print("  sync_players_dimension: complete")


def ingest_schedules(seasons: Iterable[int]):
    df = _as_pandas(nflreadpy.load_schedules(list(seasons)))

    def fc(name):
        return _col(df, name)

    out = pd.DataFrame({
        "game_id":        df["game_id"],
        "season":         df["season"],
        "week":           df["week"],
        "game_type":      fc("game_type"),
        "game_date":      pd.to_datetime(df["gameday"], errors="coerce").dt.date,
        "home_team":      df["home_team"],
        "away_team":      df["away_team"],
        "home_score":     fc("home_score"),
        "away_score":     fc("away_score"),
        "result":         fc("result"),
        "total":          fc("total"),
        "spread_line":    fc("spread_line"),
        "total_line":     fc("total_line"),
        "home_moneyline": fc("home_moneyline"),
        "away_moneyline": fc("away_moneyline"),
        "roof":           fc("roof"),
        "surface":        fc("surface"),
        "temp":           fc("temp"),
        "wind":           fc("wind"),
        "overtime":       fc("overtime"),
        "home_rest":      fc("home_rest"),
        "away_rest":      fc("away_rest"),
        "home_qb_name":   fc("home_qb_name"),
        "away_qb_name":   fc("away_qb_name"),
        "div_game":       fc("div_game"),
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

    def c(name):
        return _col(merged, name)

    out = pd.DataFrame({
        "player_id":                    merged["player_id"],
        "game_id":                      merged["game_id"],
        "season":                       merged["season"],
        "week":                         merged["week"],
        "season_type":                  c("season_type"),
        "game_date":                    merged["game_date"],
        "opponent":                     merged["opponent_team"],
        "team":                         merged["team"],
        "position":                     c("position"),
        "position_group":               c("position_group"),
        "passing_yards":                c("passing_yards"),
        "passing_tds":                  c("passing_tds"),
        "passing_interceptions":        c("passing_interceptions"),
        "attempts":                     c("attempts"),
        "completions":                  c("completions"),
        "passing_air_yards":            c("passing_air_yards"),
        "passing_yards_after_catch":    c("passing_yards_after_catch"),
        "passing_first_downs":          c("passing_first_downs"),
        "passing_epa":                  c("passing_epa"),
        "passing_cpoe":                 c("passing_cpoe"),
        "passing_2pt_conversions":      c("passing_2pt_conversions"),
        "sacks_suffered":               c("sacks_suffered"),
        "sack_yards_lost":              c("sack_yards_lost"),
        "sack_fumbles":                 c("sack_fumbles"),
        "sack_fumbles_lost":            c("sack_fumbles_lost"),
        "pacr":                         c("pacr"),
        "rushing_yards":                c("rushing_yards"),
        "rushing_tds":                  c("rushing_tds"),
        "carries":                      c("carries"),
        "rushing_first_downs":          c("rushing_first_downs"),
        "rushing_epa":                  c("rushing_epa"),
        "rushing_2pt_conversions":      c("rushing_2pt_conversions"),
        "rushing_fumbles":              c("rushing_fumbles"),
        "rushing_fumbles_lost":         c("rushing_fumbles_lost"),
        "receiving_yards":              c("receiving_yards"),
        "receiving_tds":                c("receiving_tds"),
        "receptions":                   c("receptions"),
        "targets":                      c("targets"),
        "receiving_air_yards":          c("receiving_air_yards"),
        "receiving_yards_after_catch":  c("receiving_yards_after_catch"),
        "receiving_first_downs":        c("receiving_first_downs"),
        "receiving_epa":                c("receiving_epa"),
        "receiving_2pt_conversions":    c("receiving_2pt_conversions"),
        "receiving_fumbles":            c("receiving_fumbles"),
        "receiving_fumbles_lost":       c("receiving_fumbles_lost"),
        "target_share":                 c("target_share"),
        "air_yards_share":              c("air_yards_share"),
        "wopr":                         c("wopr"),
        "racr":                         c("racr"),
        "def_tackles_solo":             c("def_tackles_solo"),
        "def_tackles_with_assist":      c("def_tackles_with_assist"),
        "def_tackle_assists":           c("def_tackle_assists"),
        "def_tackles_for_loss":         c("def_tackles_for_loss"),
        "def_tackles_for_loss_yards":   c("def_tackles_for_loss_yards"),
        "def_sacks":                    c("def_sacks"),
        "def_sack_yards":               c("def_sack_yards"),
        "def_qb_hits":                  c("def_qb_hits"),
        "def_interceptions":            c("def_interceptions"),
        "def_interception_yards":       c("def_interception_yards"),
        "def_pass_defended":            c("def_pass_defended"),
        "def_tds":                      c("def_tds"),
        "def_fumbles":                  c("def_fumbles"),
        "def_fumbles_forced":           c("def_fumbles_forced"),
        "def_safeties":                 c("def_safeties"),
        "fg_made":                      c("fg_made"),
        "fg_att":                       c("fg_att"),
        "fg_missed":                    c("fg_missed"),
        "fg_blocked":                   c("fg_blocked"),
        "fg_long":                      c("fg_long"),
        "fg_pct":                       c("fg_pct"),
        "fg_made_0_19":                 c("fg_made_0_19"),
        "fg_made_20_29":                c("fg_made_20_29"),
        "fg_made_30_39":                c("fg_made_30_39"),
        "fg_made_40_49":                c("fg_made_40_49"),
        "fg_made_50_59":                c("fg_made_50_59"),
        "fg_made_60_":                  c("fg_made_60_"),
        "fg_missed_0_19":               c("fg_missed_0_19"),
        "fg_missed_20_29":              c("fg_missed_20_29"),
        "fg_missed_30_39":              c("fg_missed_30_39"),
        "fg_missed_40_49":              c("fg_missed_40_49"),
        "fg_missed_50_59":              c("fg_missed_50_59"),
        "fg_missed_60_":                c("fg_missed_60_"),
        "fg_made_distance":             c("fg_made_distance"),
        "fg_missed_distance":           c("fg_missed_distance"),
        "gwfg_made":                    c("gwfg_made"),
        "gwfg_att":                     c("gwfg_att"),
        "gwfg_missed":                  c("gwfg_missed"),
        "gwfg_blocked":                 c("gwfg_blocked"),
        "gwfg_distance":                c("gwfg_distance"),
        "pat_made":                     c("pat_made"),
        "pat_att":                      c("pat_att"),
        "pat_missed":                   c("pat_missed"),
        "pat_blocked":                  c("pat_blocked"),
        "pat_pct":                      c("pat_pct"),
        "special_teams_tds":            c("special_teams_tds"),
        "penalties":                    c("penalties"),
        "penalty_yards":                c("penalty_yards"),
        "fumble_recovery_own":          c("fumble_recovery_own"),
        "fumble_recovery_yards_own":    c("fumble_recovery_yards_own"),
        "fumble_recovery_opp":          c("fumble_recovery_opp"),
        "fumble_recovery_yards_opp":    c("fumble_recovery_yards_opp"),
        "fumble_recovery_tds":          c("fumble_recovery_tds"),
        "punt_returns":                 c("punt_returns"),
        "punt_return_yards":            c("punt_return_yards"),
        "kickoff_returns":              c("kickoff_returns"),
        "kickoff_return_yards":         c("kickoff_return_yards"),
        "misc_yards":                   c("misc_yards"),
        "fantasy_points":               c("fantasy_points"),
        "fantasy_points_ppr":           c("fantasy_points_ppr"),
    }).dropna(subset=["player_id", "game_id"])

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE player_game_stats"))
        # Keep ONLY the columns the platform actually uses.
        # Intentionally exclude fantasy_points / fantasy_points_ppr and other noisy extras.
        keep_cols = [
            "player_id","game_id","season","week","season_type","game_date",
            "opponent","team","position","position_group",

            # PASSING
            "passing_yards","passing_tds","passing_interceptions",
            "attempts","completions","passing_air_yards",
            "passing_yards_after_catch","passing_first_downs",

            # RUSHING
            "rushing_yards","rushing_tds","carries","rushing_first_downs",

            # RECEIVING
            "receiving_yards","receiving_tds","receptions","targets",
            "receiving_air_yards","receiving_yards_after_catch",

            # DEFENSE
            "def_tackles_solo","def_sacks","def_interceptions",

            # KICKING
            "fg_made","fg_att","fg_long",

            # SPECIAL TEAMS
            "punt_return_yards","kickoff_return_yards"
        ]

        existing_keep_cols = [c for c in keep_cols if c in out.columns]
        out = out[existing_keep_cols].copy()
        out.to_sql("player_game_stats", conn, if_exists="append", index=False)
    print(f"  ingest_player_game_stats: {len(out)} rows")


def ingest_snap_counts(seasons: Iterable[int]):
    """Snap counts use pfr_player_id. We join to crosswalk to get gsis_id."""
    df = _as_pandas(nflreadpy.load_snap_counts(list(seasons)))
    print(f"  snap_counts raw: {len(df)} rows")

    with _engine().begin() as conn:
        xwalk = pd.read_sql("SELECT gsis_id, pfr_id FROM player_id_crosswalk WHERE pfr_id IS NOT NULL", conn)

    merged = df.merge(xwalk, left_on="pfr_player_id", right_on="pfr_id", how="left")
    merged = merged.rename(columns={"gsis_id": "player_id"})
    matched = merged["player_id"].notna().sum()
    print(f"  snap_counts crosswalk match: {matched}/{len(merged)}")

    out = pd.DataFrame({
        "player_id":    merged["player_id"],
        "game_id":      merged["game_id"],
        "season":       merged["season"],
        "week":         merged["week"],
        "team":         merged["team"],
        "opponent":     merged["opponent"],
        "position":     _col(merged, "position"),
        "offense_snaps":merged["offense_snaps"],
        "offense_pct":  merged["offense_pct"],
        "defense_snaps":merged["defense_snaps"],
        "defense_pct":  merged["defense_pct"],
        "st_snaps":     merged["st_snaps"],
        "st_pct":       merged["st_pct"],
        "pfr_player_id":merged["pfr_player_id"],
    }).dropna(subset=["player_id", "game_id"])

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE snap_counts"))
        out.to_sql("snap_counts", conn, if_exists="append", index=False)
    print(f"  ingest_snap_counts: {len(out)} rows")


def ingest_ngs(seasons: Iterable[int]):
    seasons_list = list(seasons)

    for stat_type, table in [("passing", "ngs_passing"), ("receiving", "ngs_receiving"), ("rushing", "ngs_rushing")]:
        df = _as_pandas(nflreadpy.load_nextgen_stats(seasons_list, stat_type=stat_type))
        print(f"  ngs_{stat_type} raw: {len(df)} rows")

        df = df.rename(columns={"player_gsis_id": "player_id", "team_abbr": "team"})

        if stat_type == "passing":
            out = pd.DataFrame({
                "player_id":                            df["player_id"],
                "season":                               df["season"],
                "week":                                 df["week"],
                "season_type":                          _col(df, "season_type"),
                "team":                                 df["team"],
                "avg_time_to_throw":                    _col(df, "avg_time_to_throw"),
                "avg_completed_air_yards":              _col(df, "avg_completed_air_yards"),
                "avg_intended_air_yards":               _col(df, "avg_intended_air_yards"),
                "avg_air_yards_differential":           _col(df, "avg_air_yards_differential"),
                "aggressiveness":                       _col(df, "aggressiveness"),
                "max_completed_air_distance":           _col(df, "max_completed_air_distance"),
                "avg_air_yards_to_sticks":              _col(df, "avg_air_yards_to_sticks"),
                "completion_percentage":                _col(df, "completion_percentage"),
                "expected_completion_percentage":       _col(df, "expected_completion_percentage"),
                "completion_percentage_above_expectation": _col(df, "completion_percentage_above_expectation"),
                "avg_air_distance":                     _col(df, "avg_air_distance"),
                "max_air_distance":                     _col(df, "max_air_distance"),
                "passer_rating":                        _col(df, "passer_rating"),
                "attempts":                             _col(df, "attempts"),
                "completions":                          _col(df, "completions"),
                "pass_yards":                           _col(df, "pass_yards"),
                "pass_touchdowns":                      _col(df, "pass_touchdowns"),
                "interceptions":                        _col(df, "interceptions"),
            }).dropna(subset=["player_id"])

        elif stat_type == "receiving":
            out = pd.DataFrame({
                "player_id":                            df["player_id"],
                "season":                               df["season"],
                "week":                                 df["week"],
                "season_type":                          _col(df, "season_type"),
                "team":                                 df["team"],
                "avg_cushion":                          _col(df, "avg_cushion"),
                "avg_separation":                       _col(df, "avg_separation"),
                "avg_intended_air_yards":               _col(df, "avg_intended_air_yards"),
                "percent_share_of_intended_air_yards":  _col(df, "percent_share_of_intended_air_yards"),
                "avg_yac":                              _col(df, "avg_yac"),
                "avg_expected_yac":                     _col(df, "avg_expected_yac"),
                "avg_yac_above_expectation":            _col(df, "avg_yac_above_expectation"),
                "catch_percentage":                     _col(df, "catch_percentage"),
                "receptions":                           _col(df, "receptions"),
                "targets":                              _col(df, "targets"),
                "yards":                                _col(df, "yards"),
                "rec_touchdowns":                       _col(df, "rec_touchdowns"),
            }).dropna(subset=["player_id"])

        else:  # rushing
            out = pd.DataFrame({
                "player_id":                            df["player_id"],
                "season":                               df["season"],
                "week":                                 df["week"],
                "season_type":                          _col(df, "season_type"),
                "team":                                 df["team"],
                "efficiency":                           _col(df, "efficiency"),
                "percent_attempts_gte_eight_defenders": _col(df, "percent_attempts_gte_eight_defenders"),
                "avg_time_to_los":                      _col(df, "avg_time_to_los"),
                "rush_yards_over_expected":             _col(df, "rush_yards_over_expected"),
                "avg_rush_yards":                       _col(df, "avg_rush_yards"),
                "rush_yards_over_expected_per_att":     _col(df, "rush_yards_over_expected_per_att"),
                "rush_pct_over_expected":               _col(df, "rush_pct_over_expected"),
                "expected_rush_yards":                  _col(df, "expected_rush_yards"),
                "rush_attempts":                        _col(df, "rush_attempts"),
                "rush_yards":                           _col(df, "rush_yards"),
                "rush_touchdowns":                      _col(df, "rush_touchdowns"),
            }).dropna(subset=["player_id"])

        with _engine().begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {table}"))
            out.to_sql(table, conn, if_exists="append", index=False)
        print(f"  ingest_ngs_{stat_type}: {len(out)} rows")


def ingest_pfr_advstats(seasons: Iterable[int]):
    seasons_list = list(seasons)

    for stat_type, table in [("pass", "pfr_adv_passing"), ("rush", "pfr_adv_rushing"), ("rec", "pfr_adv_receiving"), ("def", "pfr_adv_defense")]:
        try:
            df = _as_pandas(nflreadpy.load_pfr_advstats(seasons_list, stat_type=stat_type))
            print(f"  pfr_adv_{stat_type} raw: {len(df)} rows")
        except Exception as e:
            print(f"  pfr_adv_{stat_type} FAILED: {e}")
            continue

        with _engine().begin() as conn:
            xwalk = pd.read_sql("SELECT gsis_id, pfr_id FROM player_id_crosswalk WHERE pfr_id IS NOT NULL", conn)

        df = df.merge(xwalk, left_on="pfr_player_id", right_on="pfr_id", how="left")
        df = df.rename(columns={"gsis_id": "player_id"})

        def c(name):
            return _col(df, name)

        if stat_type == "pass":
            out = pd.DataFrame({
                "player_id":            df["player_id"],
                "game_id":              df["game_id"],
                "season":               df["season"],
                "week":                 df["week"],
                "team":                 df["team"],
                "opponent":             df["opponent"],
                "times_pressured":      c("times_pressured"),
                "times_pressured_pct":  c("times_pressured_pct"),
                "times_blitzed":        c("times_blitzed"),
                "times_hit":            c("times_hit"),
                "times_hurried":        c("times_hurried"),
                "times_sacked":         c("times_sacked"),
                "passing_drops":        c("passing_drops"),
                "passing_drop_pct":     c("passing_drop_pct"),
                "passing_bad_throws":   c("passing_bad_throws"),
                "passing_bad_throw_pct":c("passing_bad_throw_pct"),
                "def_times_blitzed":    c("def_times_blitzed"),
                "def_times_hitqb":      c("def_times_hitqb"),
                "def_times_hurried":    c("def_times_hurried"),
            }).dropna(subset=["player_id", "game_id"])

        elif stat_type == "rush":
            out = pd.DataFrame({
                "player_id":                        df["player_id"],
                "game_id":                          df["game_id"],
                "season":                           df["season"],
                "week":                             df["week"],
                "team":                             df["team"],
                "opponent":                         df["opponent"],
                "carries":                          c("carries"),
                "rushing_yards_before_contact":     c("rushing_yards_before_contact"),
                "rushing_yards_before_contact_avg": c("rushing_yards_before_contact_avg"),
                "rushing_yards_after_contact":      c("rushing_yards_after_contact"),
                "rushing_yards_after_contact_avg":  c("rushing_yards_after_contact_avg"),
                "rushing_broken_tackles":           c("rushing_broken_tackles"),
                "receiving_broken_tackles":         c("receiving_broken_tackles"),
            }).dropna(subset=["player_id", "game_id"])

        elif stat_type == "rec":
            out = pd.DataFrame({
                "player_id":                df["player_id"],
                "game_id":                  df["game_id"],
                "season":                   df["season"],
                "week":                     df["week"],
                "team":                     df["team"],
                "opponent":                 df["opponent"],
                "receiving_broken_tackles": c("receiving_broken_tackles"),
                "receiving_drop":           c("receiving_drop"),
                "receiving_drop_pct":       c("receiving_drop_pct"),
                "receiving_int":            c("receiving_int"),
                "receiving_rat":            c("receiving_rat"),
                "rushing_broken_tackles":   c("rushing_broken_tackles"),
                "passing_drops":            c("passing_drops"),
                "passing_drop_pct":         c("passing_drop_pct"),
            }).dropna(subset=["player_id", "game_id"])

        else:  # def
            out = pd.DataFrame({
                "player_id":                df["player_id"],
                "game_id":                  df["game_id"],
                "season":                   df["season"],
                "week":                     df["week"],
                "team":                     df["team"],
                "opponent":                 df["opponent"],
                "def_targets":              c("def_targets"),
                "def_completions_allowed":  c("def_completions_allowed"),
                "def_completion_pct":       c("def_completion_pct"),
                "def_yards_allowed":        c("def_yards_allowed"),
                "def_yards_allowed_per_tgt":c("def_yards_allowed_per_tgt"),
                "def_yards_allowed_per_cmp":c("def_yards_allowed_per_cmp"),
                "def_air_yards_completed":  c("def_air_yards_completed"),
                "def_yards_after_catch":    c("def_yards_after_catch"),
                "def_adot":                 c("def_adot"),
                "def_ints":                 c("def_ints"),
                "def_receiving_td_allowed": c("def_receiving_td_allowed"),
                "def_passer_rating_allowed":c("def_passer_rating_allowed"),
                "def_sacks":                c("def_sacks"),
                "def_pressures":            c("def_pressures"),
                "def_missed_tackles":       c("def_missed_tackles"),
                "def_missed_tackle_pct":    c("def_missed_tackle_pct"),
                "def_times_blitzed":        c("def_times_blitzed"),
                "def_times_hitqb":          c("def_times_hitqb"),
                "def_times_hurried":        c("def_times_hurried"),
                "def_tackles_combined":     c("def_tackles_combined"),
            }).dropna(subset=["player_id", "game_id"])

        with _engine().begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {table}"))
            out.to_sql(table, conn, if_exists="append", index=False)
        print(f"  ingest_pfr_adv_{stat_type}: {len(out)} rows")


def ingest_ftn_charting(seasons: Iterable[int]):
    """Aggregate FTN play-level charting to player-game level via PBP join."""
    seasons_list = list(seasons)

    ftn = _as_pandas(nflreadpy.load_ftn_charting(seasons_list))
    print(f"  ftn_charting raw: {len(ftn)} rows")

    pbp = _as_pandas(nflreadpy.load_pbp(seasons_list))
    print(f"  pbp raw: {len(pbp)} rows")
    pbp_pass = pbp[pbp["pass_attempt"] == 1][["game_id", "play_id", "receiver_player_id", "season", "week"]].copy()
    # season and week come from PBP since FTN only has season/week at file level
    if "season" not in ftn.columns:
        ftn = ftn.drop(columns=["season", "week"], errors="ignore")

    ftn = ftn.rename(columns={"nflverse_game_id": "game_id", "nflverse_play_id": "play_id"})

    ftn = ftn.drop(columns=["season", "week"], errors="ignore")
    joined = ftn.merge(pbp_pass, on=["game_id", "play_id"], how="left")

    # only rows where a receiver was targeted
    targeted = joined.dropna(subset=["receiver_player_id"]).copy()
    targeted["read_thrown"] = pd.to_numeric(targeted["read_thrown"], errors="coerce")

    grp = targeted.groupby(["receiver_player_id", "game_id", "season", "week"])

    agg = pd.DataFrame({
        "first_read_targets":    grp.apply(lambda g: (g["read_thrown"] == 1).sum()),
        "second_read_targets":   grp.apply(lambda g: (g["read_thrown"] == 2).sum()),
        "third_read_targets":    grp.apply(lambda g: (g["read_thrown"] == 3).sum()),
        "total_charted_targets": grp["receiver_player_id"].count(),
        "play_action_snaps":     grp["is_play_action"].sum(),
        "play_action_pct":       grp["is_play_action"].mean(),
        "screen_pass_targets":   grp["is_screen_pass"].sum(),
        "screen_pass_pct":       grp["is_screen_pass"].mean(),
        "qb_out_of_pocket_snaps":grp["is_qb_out_of_pocket"].sum(),
        "qb_out_of_pocket_pct":  grp["is_qb_out_of_pocket"].mean(),
        "motion_snaps":          grp["is_motion"].sum(),
        "motion_pct":            grp["is_motion"].mean(),
        "avg_blitzers_faced":    grp["n_blitzers"].mean() if "n_blitzers" in targeted.columns else pd.Series(dtype=float),
        "avg_box_defenders":     grp["n_defense_box"].mean() if "n_defense_box" in targeted.columns else pd.Series(dtype=float),
        "drops":                 grp["is_drop"].sum(),
        "catchable_targets":     grp["is_catchable_ball"].sum(),
        "contested_targets":     grp["is_contested_ball"].sum(),
        "is_no_huddle_snaps":    grp["is_no_huddle"].sum(),
    }).reset_index()

    agg["first_read_target_rate"] = agg["first_read_targets"] / agg["total_charted_targets"].replace(0, float("nan"))
    agg["drop_rate"] = agg["drops"] / agg["catchable_targets"].replace(0, float("nan"))
    agg = agg.rename(columns={"receiver_player_id": "player_id"})

    out = agg[["player_id", "game_id", "season", "week",
               "first_read_targets", "second_read_targets", "third_read_targets",
               "total_charted_targets", "first_read_target_rate",
               "play_action_snaps", "play_action_pct",
               "screen_pass_targets", "screen_pass_pct",
               "qb_out_of_pocket_snaps", "qb_out_of_pocket_pct",
               "motion_snaps", "motion_pct",
               "avg_blitzers_faced", "avg_box_defenders",
               "drops", "catchable_targets", "drop_rate",
               "contested_targets", "is_no_huddle_snaps"]].copy()

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE ftn_charting_game"))
        out.to_sql("ftn_charting_game", conn, if_exists="append", index=False)
    print(f"  ingest_ftn_charting_game: {len(out)} rows")


def ingest_participation(seasons: Iterable[int]):
    """Aggregate participation play-level data to player-game level."""
    seasons_list = list(seasons)

    df = _as_pandas(nflreadpy.load_participation(seasons_list))
    print(f"  participation raw: {len(df)} rows")

    pbp = _as_pandas(nflreadpy.load_pbp(seasons_list))
    pbp = pbp[pbp["play_type"].isin(["pass", "run"])].copy()

    df = df.rename(columns={"nflverse_game_id": "game_id"})

    pbp_ctx = pbp[["game_id", "play_id", "shotgun", "pass_attempt", "rush_attempt",
                   "yardline_100", "season", "week"]].copy()
    df_joined = df.merge(pbp_ctx, left_on=["game_id", "play_id"], right_on=["game_id", "play_id"], how="left")

    rows = []
    for _, play in df_joined.iterrows():
        if not isinstance(play.get("offense_players"), str):
            continue
        players = play["offense_players"].split(";")
        route = play.get("route")
        formation = play.get("offense_formation", "")
        coverage_man = str(play.get("defense_man_zone_type", "")).upper()
        coverage_type = str(play.get("defense_coverage_type", "")).upper()
        was_pressure = play.get("was_pressure", False)
        defenders_box = play.get("defenders_in_box")
        pass_rushers = play.get("number_of_pass_rushers")
        shotgun = play.get("shotgun", 0)
        pass_att = play.get("pass_attempt", 0)
        rush_att = play.get("rush_attempt", 0)
        yardline = play.get("yardline_100")
        season = play.get("season")
        week = play.get("week")
        game_id = play.get("game_id")

        for pid in players:
            pid = pid.strip()
            if not pid:
                continue
            rows.append({
                "player_id":        pid,
                "game_id":          game_id,
                "season":           season,
                "week":             week,
                "ran_route":        1 if isinstance(route, str) and route else 0,
                "pass_snap":        int(pass_att) if pass_att and pass_att == pass_att else 0,
                "run_snap":         int(rush_att) if rush_att and rush_att == rush_att else 0,
                "shotgun_snap":     int(shotgun) if shotgun and shotgun == shotgun else 0,
                "man_coverage":     1 if "MAN" in coverage_man else 0,
                "zone_coverage":    1 if "ZONE" in coverage_man else 0,
                "defenders_in_box": float(defenders_box) if defenders_box and defenders_box == defenders_box else None,
                "pass_rushers":     float(pass_rushers) if pass_rushers and pass_rushers == pass_rushers else None,
                "was_pressure":     1 if was_pressure else 0,
                "red_zone":         1 if yardline and yardline == yardline and yardline <= 20 else 0,
            })

    play_df = pd.DataFrame(rows)
    if len(play_df) == 0:
        print("  participation: no rows produced")
        return

    agg = play_df.groupby(["player_id", "game_id", "season", "week"]).agg(
        routes_run=("ran_route", "sum"),
        pass_snaps=("pass_snap", "sum"),
        run_snaps=("run_snap", "sum"),
        shotgun_snaps=("shotgun_snap", "sum"),
        man_coverage_snaps=("man_coverage", "sum"),
        zone_coverage_snaps=("zone_coverage", "sum"),
        avg_defenders_in_box=("defenders_in_box", "mean"),
        avg_pass_rushers=("pass_rushers", "mean"),
        was_pressure_snaps=("was_pressure", "sum"),
        total_snaps=("pass_snap", "count"),
    ).reset_index()

    total = agg["pass_snaps"] + agg["run_snaps"]
    agg["shotgun_pct"]       = agg["shotgun_snaps"] / total.replace(0, float("nan"))
    agg["under_center_snaps"]= (agg["pass_snaps"] + agg["run_snaps"]) - agg["shotgun_snaps"]
    agg["under_center_pct"]  = agg["under_center_snaps"] / total.replace(0, float("nan"))
    agg["man_coverage_pct"]  = agg["man_coverage_snaps"] / agg["pass_snaps"].replace(0, float("nan"))
    agg["zone_coverage_pct"] = agg["zone_coverage_snaps"] / agg["pass_snaps"].replace(0, float("nan"))
    agg["was_pressure_pct"]  = agg["was_pressure_snaps"] / agg["pass_snaps"].replace(0, float("nan"))

    out = agg[["player_id", "game_id", "season", "week",
               "routes_run", "pass_snaps", "run_snaps",
               "shotgun_snaps", "shotgun_pct",
               "under_center_snaps", "under_center_pct",
               "man_coverage_snaps", "man_coverage_pct",
               "zone_coverage_snaps", "zone_coverage_pct",
               "avg_defenders_in_box", "avg_pass_rushers",
               "was_pressure_snaps", "was_pressure_pct"]].copy()

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE participation_game"))
        out.to_sql("participation_game", conn, if_exists="append", index=False)
    print(f"  ingest_participation_game: {len(out)} rows")


def ingest_pbp_aggregated(seasons: Iterable[int]):
    """Aggregate PBP to player-game level for targeted metrics."""
    seasons_list = list(seasons)

    pbp = _as_pandas(nflreadpy.load_pbp(seasons_list))
    pbp = pbp[pbp["play_type"].isin(["pass", "run"])].copy()
    print(f"  pbp pass+run rows: {len(pbp)}")

    def c(name):
        return _col(pbp, name)

    pbp["yardline_100"] = pd.to_numeric(c("yardline_100"), errors="coerce")
    pbp["red_zone"]     = (pbp["yardline_100"] <= 20).astype(int)
    pbp["third_down"]   = (c("down") == 3).astype(int)
    pbp["shotgun_f"]    = pd.to_numeric(c("shotgun"), errors="coerce").fillna(0)

    rows = []

    # receiver aggregation
    rec = pbp[pbp["pass_attempt"] == 1].copy()
    rec["air_yards_n"] = pd.to_numeric(_col(rec, "air_yards"), errors="coerce")
    rec["yac_n"]       = pd.to_numeric(_col(rec, "yards_after_catch"), errors="coerce")
    rec["epa_n"]       = pd.to_numeric(_col(rec, "epa"), errors="coerce")
    rec["cp_n"]        = pd.to_numeric(_col(rec, "cp"), errors="coerce")
    rec["cpoe_n"]      = pd.to_numeric(_col(rec, "cpoe"), errors="coerce")
    rec["xyac_n"]      = pd.to_numeric(_col(rec, "xyac_mean_yardage"), errors="coerce")
    rec["vwp_n"]       = pd.to_numeric(_col(rec, "vegas_wp"), errors="coerce")
    rec["pl_left"]     = (_col(rec, "pass_location") == "left").astype(int)
    rec["pl_mid"]      = (_col(rec, "pass_location") == "middle").astype(int)
    rec["pl_right"]    = (_col(rec, "pass_location") == "right").astype(int)

    rec_grp = rec.groupby(["receiver_player_id", "game_id", "season", "week", "posteam"])
    rec_agg = rec_grp.agg(
        avg_air_yards_target=("air_yards_n", "mean"),
        avg_yac=("yac_n", "mean"),
        avg_epa_per_play=("epa_n", "mean"),
        avg_cp=("cp_n", "mean"),
        avg_cpoe=("cpoe_n", "mean"),
        avg_xyac=("xyac_n", "mean"),
        avg_vegas_wp=("vwp_n", "mean"),
        red_zone_targets=("red_zone", "sum"),
        third_down_targets=("third_down", "sum"),
        target_left=("pl_left", "sum"),
        target_middle=("pl_mid", "sum"),
        target_right=("pl_right", "sum"),
        total_plays=("epa_n", "count"),
    ).reset_index()
    rec_agg["red_zone_target_rate"]   = rec_agg["red_zone_targets"] / rec_agg["total_plays"].replace(0, float("nan"))
    rec_agg["third_down_target_rate"] = rec_agg["third_down_targets"] / rec_agg["total_plays"].replace(0, float("nan"))
    tgt_total = rec_agg["target_left"] + rec_agg["target_middle"] + rec_agg["target_right"]
    rec_agg["target_left_pct"]   = rec_agg["target_left"] / tgt_total.replace(0, float("nan"))
    rec_agg["target_middle_pct"] = rec_agg["target_middle"] / tgt_total.replace(0, float("nan"))
    rec_agg["target_right_pct"]  = rec_agg["target_right"] / tgt_total.replace(0, float("nan"))
    rec_agg = rec_agg.rename(columns={"receiver_player_id": "player_id", "posteam": "team"})
    rec_agg["shotgun_pct"] = None
    rec_agg["run_left_pct"] = None
    rec_agg["run_middle_pct"] = None
    rec_agg["run_right_pct"] = None
    rec_agg["red_zone_carries"] = None
    rec_agg["red_zone_carry_rate"] = None
    rows.append(rec_agg)

    # rusher aggregation
    rush = pbp[pbp["rush_attempt"] == 1].copy()
    rush["epa_n"]   = pd.to_numeric(_col(rush, "epa"), errors="coerce")
    rush["vwp_n"]   = pd.to_numeric(_col(rush, "vegas_wp"), errors="coerce")
    rush["rl_left"] = (_col(rush, "run_location") == "left").astype(int)
    rush["rl_mid"]  = (_col(rush, "run_location") == "middle").astype(int)
    rush["rl_right"]= (_col(rush, "run_location") == "right").astype(int)
    rush["sg_f"]    = pd.to_numeric(_col(rush, "shotgun"), errors="coerce").fillna(0)

    rush_grp = rush.groupby(["rusher_player_id", "game_id", "season", "week", "posteam"])
    rush_agg = rush_grp.agg(
        avg_epa_per_play=("epa_n", "mean"),
        avg_vegas_wp=("vwp_n", "mean"),
        red_zone_carries=("red_zone", "sum"),
        run_left=("rl_left", "sum"),
        run_middle=("rl_mid", "sum"),
        run_right=("rl_right", "sum"),
        shotgun_snaps_rush=("sg_f", "sum"),
        total_plays=("epa_n", "count"),
    ).reset_index()
    rush_agg["red_zone_carry_rate"] = rush_agg["red_zone_carries"] / rush_agg["total_plays"].replace(0, float("nan"))
    rl_total = rush_agg["run_left"] + rush_agg["run_middle"] + rush_agg["run_right"]
    rush_agg["run_left_pct"]   = rush_agg["run_left"] / rl_total.replace(0, float("nan"))
    rush_agg["run_middle_pct"] = rush_agg["run_middle"] / rl_total.replace(0, float("nan"))
    rush_agg["run_right_pct"]  = rush_agg["run_right"] / rl_total.replace(0, float("nan"))
    rush_agg["shotgun_pct"]    = rush_agg["shotgun_snaps_rush"] / rush_agg["total_plays"].replace(0, float("nan"))
    rush_agg = rush_agg.rename(columns={"rusher_player_id": "player_id", "posteam": "team"})
    for col in ["avg_air_yards_target", "avg_yac", "avg_cp", "avg_cpoe", "avg_xyac",
                "red_zone_targets", "third_down_targets", "red_zone_target_rate",
                "third_down_target_rate", "target_left_pct", "target_middle_pct", "target_right_pct",
                "target_left", "target_middle", "target_right"]:
        rush_agg[col] = None
    shared_cols = [c for c in rec_agg.columns if c in rush_agg.columns]
    rows.append(rush_agg[shared_cols])

    out = pd.concat(rows, ignore_index=True).dropna(subset=["player_id", "game_id"])
    # deduplicate: receiver rows have more columns, keep them over rusher rows
    out = out.sort_values("avg_air_yards_target", na_position="last")
    out = out.drop_duplicates(subset=["player_id", "game_id"], keep="first")

    final_cols = ["player_id", "game_id", "season", "week", "team",
                  "avg_air_yards_target", "avg_yac", "avg_epa_per_play",
                  "avg_cp", "avg_cpoe", "red_zone_targets", "red_zone_target_rate",
                  "red_zone_carries", "red_zone_carry_rate",
                  "third_down_targets", "third_down_target_rate",
                  "target_left_pct", "target_middle_pct", "target_right_pct",
                  "run_left_pct", "run_middle_pct", "run_right_pct",
                  "shotgun_pct", "avg_xyac", "avg_vegas_wp", "total_plays"]

    out = out[[c for c in final_cols if c in out.columns]].copy()

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE pbp_player_game"))
        out.to_sql("pbp_player_game", conn, if_exists="append", index=False)
    print(f"  ingest_pbp_aggregated: {len(out)} rows")


def ingest_ff_opportunity(seasons: Iterable[int]):
    seasons_list = list(seasons)
    df = _as_pandas(nflreadpy.load_ff_opportunity(seasons_list))
    print(f"  ff_opportunity raw: {len(df)} rows")

    def c(name):
        return _col(df, name)

    out = pd.DataFrame({
        "player_id":                df["player_id"],
        "game_id":                  df["game_id"],
        "season":                   df["season"],
        "week":                     df["week"],
        "position":                 c("position"),
        "team":                     c("posteam"),
        "rec_yards_gained":         c("rec_yards_gained"),
        "rec_yards_gained_exp":     c("rec_yards_gained_exp"),
        "rec_yards_gained_diff":    c("rec_yards_gained_diff"),
        "rush_yards_gained":        c("rush_yards_gained"),
        "rush_yards_gained_exp":    c("rush_yards_gained_exp"),
        "rush_yards_gained_diff":   c("rush_yards_gained_diff"),
        "pass_yards_gained":        c("pass_yards_gained"),
        "pass_yards_gained_exp":    c("pass_yards_gained_exp"),
        "pass_yards_gained_diff":   c("pass_yards_gained_diff"),
        "receptions":               c("receptions"),
        "receptions_exp":           c("receptions_exp"),
        "receptions_diff":          c("receptions_diff"),
        "rec_attempt":              c("rec_attempt"),
        "rush_attempt":             c("rush_attempt"),
        "pass_attempt":             c("pass_attempt"),
        "rec_touchdown":            c("rec_touchdown"),
        "rec_touchdown_exp":        c("rec_touchdown_exp"),
        "rush_touchdown":           c("rush_touchdown"),
        "rush_touchdown_exp":       c("rush_touchdown_exp"),
        "total_fantasy_points":     c("total_fantasy_points"),
        "total_fantasy_points_exp": c("total_fantasy_points_exp"),
        "total_fantasy_points_diff":c("total_fantasy_points_diff"),
    }).dropna(subset=["player_id", "game_id"])

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE ff_opportunity"))
        out.to_sql("ff_opportunity", conn, if_exists="append", index=False)
    print(f"  ingest_ff_opportunity: {len(out)} rows")


def ingest_depth_charts(seasons: Iterable[int]):
    df = _as_pandas(nflreadpy.load_depth_charts(list(seasons)))
    print(f"  depth_charts raw: {len(df)} rows")

    out = pd.DataFrame({
        "player_id":        df["gsis_id"],
        "season":           df["season"],
        "week":             df["week"],
        "team":             df["club_code"],
        "position":         df["position"],
        "depth_position":   df["depth_position"],
        "depth_team":       df["depth_team"],
        "game_type":        _col(df, "game_type"),
    }).dropna(subset=["player_id", "season", "week", "depth_position"])

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE depth_charts"))
        out = out.drop_duplicates(
            subset=["player_id", "season", "week", "depth_position"]
        )
        out.to_sql("depth_charts", conn, if_exists="append", index=False)
    print(f"  ingest_depth_charts: {len(out)} rows")


def ingest_rosters_weekly(seasons: Iterable[int]):
    df = _as_pandas(nflreadpy.load_rosters_weekly(list(seasons)))
    print(f"  rosters_weekly raw: {len(df)} rows")

    out = pd.DataFrame({
        "player_id":            df["gsis_id"],
        "season":               df["season"],
        "week":                 df["week"],
        "team":                 df["team"],
        "position":             _col(df, "position"),
        "depth_chart_position": _col(df, "depth_chart_position"),
        "status":               _col(df, "status"),
        "status_description_abbr": _col(df, "status_description_abbr"),
        "jersey_number":        _col(df, "jersey_number"),
        "height":               _col(df, "height"),
        "weight":               _col(df, "weight"),
        "years_exp":            _col(df, "years_exp"),
        "pfr_id":               _col(df, "pfr_id"),
        "espn_id":              _col(df, "espn_id"),
        "pff_id":               _col(df, "pff_id"),
    }).dropna(subset=["player_id", "season", "week"])

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE rosters_weekly"))
        out.to_sql("rosters_weekly", conn, if_exists="append", index=False)
    print(f"  ingest_rosters_weekly: {len(out)} rows")


def ingest_injuries(seasons: Iterable[int]):
    df = _as_pandas(nflreadpy.load_injuries(list(seasons)))
    print(f"  injuries raw: {len(df)} rows")

    out = pd.DataFrame({
        "player_id":                df["gsis_id"],
        "season":                   df["season"],
        "week":                     df["week"],
        "team":                     df["team"],
        "position":                 _col(df, "position"),
        "report_primary_injury":    _col(df, "report_primary_injury"),
        "report_secondary_injury":  _col(df, "report_secondary_injury"),
        "report_status":            _col(df, "report_status"),
        "practice_primary_injury":  _col(df, "practice_primary_injury"),
        "practice_secondary_injury":_col(df, "practice_secondary_injury"),
        "practice_status":          _col(df, "practice_status"),
    }).dropna(subset=["player_id", "season", "week"])

    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE injuries"))
        out = out.drop_duplicates(
            subset=["player_id", "season", "week"]
        )
        out.to_sql("injuries", conn, if_exists="append", index=False)
    print(f"  ingest_injuries: {len(out)} rows")


def build_crosswalk():
    """Build player_id_crosswalk from rosters_weekly."""
    with _engine().begin() as conn:
        conn.execute(text("TRUNCATE TABLE player_id_crosswalk"))
        conn.execute(text("""
            INSERT INTO player_id_crosswalk (gsis_id, pfr_id, espn_id, pff_id)
            SELECT DISTINCT ON (player_id)
                player_id AS gsis_id,
                pfr_id,
                espn_id::text,
                pff_id::text
            FROM rosters_weekly
            WHERE player_id IS NOT NULL
            ORDER BY player_id, season DESC, week DESC
        """))
    with _engine().connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM player_id_crosswalk")).scalar()
        pfr_count = conn.execute(text("SELECT COUNT(*) FROM player_id_crosswalk WHERE pfr_id IS NOT NULL")).scalar()
    print(f"  crosswalk: {count} players, {pfr_count} with pfr_id")


def run():
    season_start = int(os.getenv("SEASON_START", "2022"))
    season_end   = int(os.getenv("SEASON_END",   "2025"))
    seasons = _season_range(season_start, season_end)
    print(f"Running full ingestion for seasons {season_start}-{season_end}")

    ensure_tables()

    print("--- players ---")
    ingest_players()
    sync_players_dimension()

    print("--- schedules ---")
    ingest_schedules(seasons)

    print("--- player_game_stats ---")
    ingest_player_game_stats(seasons)

    print("--- rosters_weekly (needed for crosswalk) ---")
    ingest_rosters_weekly(seasons)

    print("--- crosswalk ---")
    build_crosswalk()

    print("--- snap_counts ---")
    ingest_snap_counts(seasons)

    print("--- NGS ---")
    ingest_ngs(seasons)

    print("--- PFR advanced stats ---")
    ingest_pfr_advstats(seasons)

    print("--- ff_opportunity ---")
    ingest_ff_opportunity(seasons)

    print("--- depth_charts ---")
    ingest_depth_charts(seasons)

    print("--- injuries ---")
    ingest_injuries(seasons)

    print("--- FTN charting (loads PBP internally) ---")
    ingest_ftn_charting(seasons)

    print("--- participation (loads PBP internally) ---")
    ingest_participation(seasons)

    print("--- PBP aggregated ---")
    ingest_pbp_aggregated(seasons)

    print("Ingestion complete.")


if __name__ == "__main__":
    run()
