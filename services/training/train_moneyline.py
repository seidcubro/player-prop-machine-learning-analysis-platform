from __future__ import annotations

import json
import os
from pathlib import Path

import joblib
import pandas as pd
from sqlalchemy import create_engine, text
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score


POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "app")
POSTGRES_USER = os.getenv("POSTGRES_USER", "app")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "app")

LOOKBACK = int(os.getenv("LOOKBACK", "5"))
MODEL_NAME = os.getenv("MODEL_NAME", "rf_moneyline_v1")
ARTIFACT_DIR = os.getenv("ARTIFACT_DIR", "/artifacts")

DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

FEATURE_COLS = [
    "is_home_num",
    "team_win_rate_mean",
    "team_point_diff_mean",
    "team_point_diff_trend",
    "team_points_mean",
    "team_points_allowed_mean",
    "team_pass_yards_mean",
    "team_rush_yards_mean",
    "team_turnovers_mean",
    "opp_win_rate_mean",
    "opp_point_diff_mean",
    "opp_points_mean",
    "opp_points_allowed_mean",
    "opp_pass_yards_mean",
    "opp_rush_yards_mean",
    "opp_turnovers_mean",
    "matchup_point_diff_edge",
    "matchup_points_edge",
]


def _trend(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(values) / n
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, values))
    den = sum((xi - x_mean) ** 2 for xi in x)
    return 0.0 if den == 0 else num / den


def build_features(engine) -> int:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  game_date,
                  team,
                  opponent,
                  is_home,
                  win_flag,
                  point_diff,
                  team_points,
                  opp_points,
                  pass_attempts,
                  pass_completions,
                  passing_yards,
                  passing_tds,
                  interceptions_thrown,
                  rush_attempts,
                  rushing_yards,
                  rushing_tds
                FROM team_game_stats
                ORDER BY game_date, team
                """
            )
        ).mappings().all()

        df = pd.DataFrame(rows)
        if df.empty:
            return 0

        df["game_date"] = pd.to_datetime(df["game_date"])
        df = df.sort_values(["team", "game_date"]).reset_index(drop=True)

        conn.execute(
            text("DELETE FROM game_moneyline_features WHERE lookback = :lookback"),
            {"lookback": LOOKBACK},
        )

        inserts = 0

        teams = sorted(df["team"].dropna().unique().tolist())
        by_team = {team: df[df["team"] == team].sort_values("game_date").to_dict("records") for team in teams}

        for team in teams:
            games = by_team[team]
            for i in range(LOOKBACK, len(games)):
                current = games[i]
                team_window = games[i - LOOKBACK:i]

                opp_team = current["opponent"]
                opp_games_all = by_team.get(opp_team, [])
                opp_window = [
                    g for g in opp_games_all
                    if pd.Timestamp(g["game_date"]) < pd.Timestamp(current["game_date"])
                ][-LOOKBACK:]

                if len(team_window) < LOOKBACK or len(opp_window) < LOOKBACK:
                    continue

                team_win_rate = [float(g["win_flag"] or 0) for g in team_window]
                team_point_diff = [float(g["point_diff"] or 0) for g in team_window]
                team_points = [float(g["team_points"] or 0) for g in team_window]
                team_points_allowed = [float(g["opp_points"] or 0) for g in team_window]
                team_pass_yards = [float(g["passing_yards"] or 0) for g in team_window]
                team_rush_yards = [float(g["rushing_yards"] or 0) for g in team_window]
                team_turnovers = [float(g["interceptions_thrown"] or 0) for g in team_window]

                opp_win_rate = [float(g["win_flag"] or 0) for g in opp_window]
                opp_point_diff = [float(g["point_diff"] or 0) for g in opp_window]
                opp_points = [float(g["team_points"] or 0) for g in opp_window]
                opp_points_allowed = [float(g["opp_points"] or 0) for g in opp_window]
                opp_pass_yards = [float(g["passing_yards"] or 0) for g in opp_window]
                opp_rush_yards = [float(g["rushing_yards"] or 0) for g in opp_window]
                opp_turnovers = [float(g["interceptions_thrown"] or 0) for g in opp_window]

                row = {
                    "game_date": pd.Timestamp(current["game_date"]).date(),
                    "team": team,
                    "opponent": opp_team,
                    "is_home": current["is_home"],
                    "lookback": LOOKBACK,
                    "target_win_flag": int(current["win_flag"] or 0),
                    "team_win_rate_mean": sum(team_win_rate) / len(team_win_rate),
                    "team_point_diff_mean": sum(team_point_diff) / len(team_point_diff),
                    "team_point_diff_trend": _trend(team_point_diff),
                    "team_points_mean": sum(team_points) / len(team_points),
                    "team_points_allowed_mean": sum(team_points_allowed) / len(team_points_allowed),
                    "team_pass_yards_mean": sum(team_pass_yards) / len(team_pass_yards),
                    "team_rush_yards_mean": sum(team_rush_yards) / len(team_rush_yards),
                    "team_turnovers_mean": sum(team_turnovers) / len(team_turnovers),
                    "opp_win_rate_mean": sum(opp_win_rate) / len(opp_win_rate),
                    "opp_point_diff_mean": sum(opp_point_diff) / len(opp_point_diff),
                    "opp_points_mean": sum(opp_points) / len(opp_points),
                    "opp_points_allowed_mean": sum(opp_points_allowed) / len(opp_points_allowed),
                    "opp_pass_yards_mean": sum(opp_pass_yards) / len(opp_pass_yards),
                    "opp_rush_yards_mean": sum(opp_rush_yards) / len(opp_rush_yards),
                    "opp_turnovers_mean": sum(opp_turnovers) / len(opp_turnovers),
                    "matchup_point_diff_edge": (sum(team_point_diff) / len(team_point_diff)) - (sum(opp_point_diff) / len(opp_point_diff)),
                    "matchup_points_edge": (sum(team_points) / len(team_points)) - (sum(opp_points_allowed) / len(opp_points_allowed)),
                }

                conn.execute(
                    text(
                        """
                        INSERT INTO game_moneyline_features (
                          game_date, team, opponent, is_home, lookback, target_win_flag,
                          team_win_rate_mean, team_point_diff_mean, team_point_diff_trend,
                          team_points_mean, team_points_allowed_mean, team_pass_yards_mean,
                          team_rush_yards_mean, team_turnovers_mean, opp_win_rate_mean,
                          opp_point_diff_mean, opp_points_mean, opp_points_allowed_mean,
                          opp_pass_yards_mean, opp_rush_yards_mean, opp_turnovers_mean,
                          matchup_point_diff_edge, matchup_points_edge, updated_at
                        )
                        VALUES (
                          :game_date, :team, :opponent, :is_home, :lookback, :target_win_flag,
                          :team_win_rate_mean, :team_point_diff_mean, :team_point_diff_trend,
                          :team_points_mean, :team_points_allowed_mean, :team_pass_yards_mean,
                          :team_rush_yards_mean, :team_turnovers_mean, :opp_win_rate_mean,
                          :opp_point_diff_mean, :opp_points_mean, :opp_points_allowed_mean,
                          :opp_pass_yards_mean, :opp_rush_yards_mean, :opp_turnovers_mean,
                          :matchup_point_diff_edge, :matchup_points_edge, NOW()
                        )
                        ON CONFLICT (game_date, team, opponent, lookback)
                        DO UPDATE SET
                          target_win_flag = EXCLUDED.target_win_flag,
                          team_win_rate_mean = EXCLUDED.team_win_rate_mean,
                          team_point_diff_mean = EXCLUDED.team_point_diff_mean,
                          team_point_diff_trend = EXCLUDED.team_point_diff_trend,
                          team_points_mean = EXCLUDED.team_points_mean,
                          team_points_allowed_mean = EXCLUDED.team_points_allowed_mean,
                          team_pass_yards_mean = EXCLUDED.team_pass_yards_mean,
                          team_rush_yards_mean = EXCLUDED.team_rush_yards_mean,
                          team_turnovers_mean = EXCLUDED.team_turnovers_mean,
                          opp_win_rate_mean = EXCLUDED.opp_win_rate_mean,
                          opp_point_diff_mean = EXCLUDED.opp_point_diff_mean,
                          opp_points_mean = EXCLUDED.opp_points_mean,
                          opp_points_allowed_mean = EXCLUDED.opp_points_allowed_mean,
                          opp_pass_yards_mean = EXCLUDED.opp_pass_yards_mean,
                          opp_rush_yards_mean = EXCLUDED.opp_rush_yards_mean,
                          opp_turnovers_mean = EXCLUDED.opp_turnovers_mean,
                          matchup_point_diff_edge = EXCLUDED.matchup_point_diff_edge,
                          matchup_points_edge = EXCLUDED.matchup_points_edge,
                          updated_at = NOW()
                        """
                    ),
                    row,
                )
                inserts += 1

        return inserts


def train_model(engine):
    df = pd.read_sql(
        text(
            """
            SELECT *
            FROM game_moneyline_features
            WHERE lookback = :lookback
            ORDER BY game_date
            """
        ),
        engine,
        params={"lookback": LOOKBACK},
    )

    if df.empty:
        raise RuntimeError("No rows found in game_moneyline_features")

    df["game_date"] = pd.to_datetime(df["game_date"])
    df["is_home_num"] = df["is_home"].fillna(False).astype(int)

    split_date = df["game_date"].quantile(0.75)
    train_df = df[df["game_date"] < split_date].copy()
    test_df = df[df["game_date"] >= split_date].copy()

    X_train = train_df[FEATURE_COLS].fillna(0.0)
    y_train = train_df["target_win_flag"].astype(int)

    X_test = test_df[FEATURE_COLS].fillna(0.0)
    y_test = test_df["target_win_flag"].astype(int)

    model = RandomForestClassifier(
        n_estimators=400,
        max_depth=8,
        min_samples_leaf=4,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    pred = (proba >= 0.5).astype(int)

    metrics = {
        "accuracy": float(accuracy_score(y_test, pred)),
        "log_loss": float(log_loss(y_test, proba)),
        "roc_auc": float(roc_auc_score(y_test, proba)),
    }

    artifact_dir = Path(ARTIFACT_DIR)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = artifact_dir / f"{MODEL_NAME}_lb{LOOKBACK}.joblib"
    meta_path = artifact_dir / f"{MODEL_NAME}_lb{LOOKBACK}.json"

    joblib.dump(model, artifact_path)

    meta = {
        "model_name": MODEL_NAME,
        "market_code": "moneyline",
        "market_name": "Game Moneyline",
        "lookback": LOOKBACK,
        "model_type": type(model).__name__,
        "feature_cols": FEATURE_COLS,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "train_date_min": str(train_df["game_date"].min().date()),
        "train_date_max": str(train_df["game_date"].max().date()),
        "test_date_min": str(test_df["game_date"].min().date()),
        "test_date_max": str(test_df["game_date"].max().date()),
        **metrics,
        "artifact_path": str(artifact_path),
        "feature_importances": {
            col: float(val) for col, val in zip(FEATURE_COLS, model.feature_importances_)
        },
    }

    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


if __name__ == "__main__":
    engine = create_engine(DATABASE_URL, future=True)
    inserts = build_features(engine)
    print(f"FEATURE BUILD COMPLETE: {inserts} rows")
    meta = train_model(engine)
    print("TRAINING COMPLETE")
    print(json.dumps(meta, indent=2))