from pathlib import Path
import re

repo = Path(".")

players_path = repo / "services" / "api" / "app" / "routes" / "players.py"
jobs_path = repo / "services" / "api" / "app" / "routes" / "jobs.py"

players_text = players_path.read_text(encoding="utf-8-sig")
jobs_text = jobs_path.read_text(encoding="utf-8-sig")

new_projection_ml = '''@router.get("/players/{player_id}/projection_ml")
def projection_ml(
    player_id: int,
    market_code: str = Query(...),
    lookback: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    player = _get_player_row(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    external_id = player["external_id"]
    if not external_id:
        raise HTTPException(
            status_code=400, detail="Player missing external_id")

    m = db.execute(
        text("SELECT id, code FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()
    if not m:
        raise HTTPException(
            status_code=404, detail=f"Unknown market_code: {market_code}")
    market_id = int(m["id"])

    am = db.execute(
        text(
            """
            SELECT model_name, lookback, artifact_path
            FROM active_models
            WHERE market_id = :market_id
            """
        ),
        {"market_id": market_id},
    ).mappings().first()
    if not am:
        raise HTTPException(
            status_code=404, detail=f"No active model for market_code: {market_code}")

    model_name = am["model_name"]
    artifact_path = am["artifact_path"]

    if int(am["lookback"]) != int(lookback):
        raise HTTPException(
            status_code=400,
            detail=f"Active model lookback is {am['lookback']} but request lookback is {lookback}",
        )

    if not artifact_path or not os.path.exists(artifact_path):
        raise HTTPException(
            status_code=500, detail=f"Model artifact not found at: {artifact_path}")

    meta_path = os.path.splitext(artifact_path)[0] + ".json"
    if not os.path.exists(meta_path):
        raise HTTPException(
            status_code=500, detail=f"Model metadata not found at: {meta_path}")

    with open(meta_path, "r", encoding="utf-8") as f:
        model_meta = json.load(f)

    feature_order = model_meta.get("feature_cols") or model_meta.get("feature_columns")
    if not feature_order:
        raise HTTPException(
            status_code=500, detail=f"Model metadata missing feature_cols in: {meta_path}")

    feat = db.execute(
        text(
            """
            SELECT
              as_of_game_date,
              opponent,
              mean,
              stddev,
              weighted_mean,
              trend,
              COALESCE(recs_mean, 0.0) AS recs_mean,
              COALESCE(recs_trend, 0.0) AS recs_trend,
              extra_features
            FROM player_market_features
            WHERE player_id = :external_id
              AND market_id = :market_id
              AND lookback = :lookback
            ORDER BY as_of_game_date DESC
            LIMIT 1
            """
        ),
        {"external_id": external_id, "market_id": market_id, "lookback": lookback},
    ).mappings().first()

    if not feat:
        raise HTTPException(
            status_code=404,
            detail=f"No features found for player external_id={external_id}, market={market_code}, lookback={lookback}",
        )

    features_obj = {
        "mean": float(feat["mean"] or 0.0),
        "stddev": float(feat["stddev"] or 0.0),
        "weighted_mean": float(feat["weighted_mean"] or 0.0),
        "trend": float(feat["trend"] or 0.0),
        "recs_mean": float(feat["recs_mean"] or 0.0),
        "recs_trend": float(feat["recs_trend"] or 0.0),
    }

    extra = feat["extra_features"] or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    elif not isinstance(extra, dict):
        extra = {}

    for k, v in extra.items():
        try:
            features_obj[str(k)] = float(v or 0.0)
        except Exception:
            features_obj[str(k)] = 0.0

    if "targets_mean" in features_obj and "team_pass_attempts" in features_obj:
        denom = float(features_obj.get("team_pass_attempts", 0.0) or 0.0)
        numer = float(features_obj.get("targets_mean", 0.0) or 0.0)
        features_obj["target_share"] = 0.0 if denom == 0.0 else max(0.0, min(1.0, numer / denom))

    X = [[float(features_obj.get(col, 0.0)) for col in feature_order]]

    pipe = joblib.load(artifact_path)
    pred = float(pipe.predict(X)[0])

    db.execute(
        text(
            """
            INSERT INTO ml_projections
              (player_id, market_code, model_name, lookback, as_of_game_date, opponent, prediction, features, artifact_path)
            VALUES
              (:player_id, :market_code, :model_name, :lookback, :as_of_game_date, :opponent, :prediction, CAST(:features AS jsonb), :artifact_path)
            ON CONFLICT (player_id, market_code, model_name, lookback, as_of_game_date)
            DO UPDATE SET
              prediction = EXCLUDED.prediction,
              features = EXCLUDED.features,
              artifact_path = EXCLUDED.artifact_path,
              created_at = NOW()
            """
        ),
        {
            "player_id": player_id,
            "market_code": market_code,
            "model_name": model_name,
            "lookback": lookback,
            "as_of_game_date": feat["as_of_game_date"],
            "opponent": feat["opponent"],
            "prediction": pred,
            "features": json.dumps(features_obj),
            "artifact_path": artifact_path,
        },
    )
    db.commit()

    return {
        "ok": True,
        "player_id": player_id,
        "external_id": external_id,
        "player_name": player["display_name"],
        "market_code": market_code,
        "model_name": model_name,
        "lookback": lookback,
        "as_of_game_date": str(feat["as_of_game_date"]),
        "opponent": feat["opponent"],
        "prediction": pred,
        "features": features_obj,
        "artifact_path": artifact_path,
    }


'''

players_pattern = r'@router\.get\("/players/\{player_id\}/projection_ml"\)\ndef projection_ml\((?:.|\n)*?\n\n@router\.get\("/players/\{player_id\}/projection_history"\)'
players_repl = new_projection_ml + '@router.get("/players/{player_id}/projection_history")'
players_text_new, players_count = re.subn(players_pattern, players_repl, players_text, count=1)

if players_count != 1:
    raise SystemExit("Failed to replace projection_ml block in players.py")

new_build_features = '''@router.post("/jobs/build_features")
def build_features(
    market_code: str,
    lookback: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    m = _get_market(db, market_code)

    if not m["is_active"]:
        raise HTTPException(status_code=400, detail=f"Market is inactive: {market_code}")
    if not m["train_enabled"]:
        raise HTTPException(status_code=400, detail=f"Training disabled for market: {market_code}")
    if m["scope"] != "player":
        raise HTTPException(status_code=400, detail=f"Only player-scoped markets are supported here. Got: {m['scope']}")
    if m["target_kind"] != "regression":
        raise HTTPException(status_code=400, detail=f"Only regression markets are supported here. Got: {m['target_kind']}")

    stat_field = _safe_identifier(str(m["stat_field"]))
    if not _column_exists(db, "player_game_stats_app", stat_field):
        raise HTTPException(
            status_code=400,
            detail=f"stat_field '{stat_field}' does not exist in player_game_stats_app",
        )

    eligible_positions = set(_as_text_array(m["eligible_positions"]))
    feature_family = str(m["feature_family"] or "").strip().lower()
    upstream_cols = _get_safe_upstream_markets(db, market_code)

    select_upstream_sql = ""
    for code, col in upstream_cols:
        alias = _safe_identifier(code)
        select_upstream_sql += f", COALESCE(pgs.{col}, 0)::float8 AS {alias}"

    sql = f"""
        SELECT
          pgs.player_id,
          pgs.team,
          pgs.position,
          pgs.game_date,
          pgs.opponent,
          COALESCE(pgs.{stat_field}, 0)::float8 AS y,
          COALESCE(top.team_pass_attempts, 0)::float8 AS team_pass_attempts,
          COALESCE(tdr.opp_rec_yds_allowed, 0)::float8 AS opp_rec_yds_allowed,
          COALESCE(tdr.opp_targets_allowed, 0)::float8 AS opp_targets_allowed,
          COALESCE(tdrr.opp_rec_yds_allowed_rolling, 0)::float8 AS opp_rec_yds_allowed_rolling,
          COALESCE(tdrr.opp_targets_allowed_rolling, 0)::float8 AS opp_targets_allowed_rolling
          {select_upstream_sql}
        FROM player_game_stats_app pgs
        LEFT JOIN team_offense_pass top
          ON top.team = pgs.team
         AND top.game_date = pgs.game_date
        LEFT JOIN team_defense_rec tdr
          ON tdr.team = pgs.opponent
        LEFT JOIN team_defense_rec_rolling tdrr
          ON tdrr.defense_team = pgs.opponent
         AND tdrr.game_date = pgs.game_date
        WHERE pgs.game_date IS NOT NULL
          AND pgs.opponent IS NOT NULL
        ORDER BY pgs.player_id, pgs.game_date
    """
    rows = db.execute(text(sql)).mappings().all()

    by_player = {}
    for r in rows:
        pos = r["position"]
        if eligible_positions and pos not in eligible_positions:
            continue
        by_player.setdefault(r["player_id"], []).append(r)

    upsert_sql = text(
        """
        INSERT INTO player_market_features
          (
            player_id,
            market_id,
            as_of_game_date,
            opponent,
            lookback,
            mean,
            stddev,
            weighted_mean,
            trend,
            recs_mean,
            recs_trend,
            extra_features
          )
        VALUES
          (
            :player_id,
            :market_id,
            :as_of_game_date,
            :opponent,
            :lookback,
            :mean,
            :stddev,
            :weighted_mean,
            :trend,
            :recs_mean,
            :recs_trend,
            CAST(:extra_features AS jsonb)
          )
        ON CONFLICT (player_id, market_id, as_of_game_date, opponent, lookback)
        DO UPDATE SET
          mean = EXCLUDED.mean,
          stddev = EXCLUDED.stddev,
          weighted_mean = EXCLUDED.weighted_mean,
          trend = EXCLUDED.trend,
          recs_mean = EXCLUDED.recs_mean,
          recs_trend = EXCLUDED.recs_trend,
          extra_features = EXCLUDED.extra_features
        """
    )

    upserts = 0

    for player_id, games in by_player.items():
        ys = [float(g["y"] or 0.0) for g in games]

        for i in range(len(games)):
            if i < lookback:
                continue

            window_games = games[i - lookback:i]
            window = ys[i - lookback:i]

            mu = _mean(window)
            sd = _stddev_pop(window)
            wmu = _weighted_mean_recent(window)
            tr = _trend_slope(window)

            aux_mean = None
            aux_trend = None

            if feature_family == "receiving":
                aux_window = [
                    float(g.get("recs", g.get("receptions", 0.0)) or 0.0)
                    for g in window_games
                ]
                if aux_window:
                    aux_mean = _mean(aux_window)
                    aux_trend = _trend_slope(aux_window)

            elif feature_family == "rushing":
                aux_window = [
                    float(g.get("carries", 0.0) or 0.0)
                    for g in window_games
                ]
                if aux_window:
                    aux_mean = _mean(aux_window)
                    aux_trend = _trend_slope(aux_window)

            elif feature_family == "passing":
                aux_window = [
                    float(g.get("pass_attempts", 0.0) or 0.0)
                    for g in window_games
                ]
                if aux_window:
                    aux_mean = _mean(aux_window)
                    aux_trend = _trend_slope(aux_window)

            extra_features = {}
            for code, _col in upstream_cols:
                vals = [float(g.get(code, 0.0) or 0.0) for g in window_games]
                if vals:
                    extra_features[f"{code}_mean"] = _mean(vals)
                    extra_features[f"{code}_trend"] = _trend_slope(vals)

            if feature_family == "receiving":
                targets_window = [float(g.get("targets", 0.0) or 0.0) for g in window_games]
                if targets_window:
                    extra_features["targets_weighted_mean"] = _weighted_mean_recent(targets_window)

                extra_features["receiving_yards_mean"] = mu

                if targets_window:
                    ypt_vals = [
                        (y / t) if t not in (None, 0, 0.0) else 0.0
                        for y, t in zip(window, targets_window)
                    ]
                    extra_features["yards_per_target_mean"] = _mean(ypt_vals)

                team_pass_window = [float(g.get("team_pass_attempts", 0.0) or 0.0) for g in window_games]
                if team_pass_window:
                    extra_features["team_pass_attempts"] = _mean(team_pass_window)
                    extra_features["team_pass_attempts_trend"] = _trend_slope(team_pass_window)

                opp_rec_vals = []
                opp_targets_vals = []
                for g in window_games:
                    opp_rec_roll = float(g.get("opp_rec_yds_allowed_rolling", 0.0) or 0.0)
                    opp_rec_base = float(g.get("opp_rec_yds_allowed", 0.0) or 0.0)
                    opp_targets_roll = float(g.get("opp_targets_allowed_rolling", 0.0) or 0.0)
                    opp_targets_base = float(g.get("opp_targets_allowed", 0.0) or 0.0)

                    opp_rec_vals.append(opp_rec_roll if opp_rec_roll > 0 else opp_rec_base)
                    opp_targets_vals.append(opp_targets_roll if opp_targets_roll > 0 else opp_targets_base)

                if opp_rec_vals:
                    extra_features["opp_rec_yds_allowed"] = _mean(opp_rec_vals)
                if opp_targets_vals:
                    extra_features["opp_targets_allowed"] = _mean(opp_targets_vals)

            db.execute(
                upsert_sql,
                {
                    "player_id": player_id,
                    "market_id": m["id"],
                    "as_of_game_date": games[i]["game_date"],
                    "opponent": games[i]["opponent"],
                    "lookback": lookback,
                    "mean": mu,
                    "stddev": sd,
                    "weighted_mean": wmu,
                    "trend": tr,
                    "recs_mean": aux_mean,
                    "recs_trend": aux_trend,
                    "extra_features": json.dumps(extra_features),
                },
            )
            upserts += 1

    db.commit()

    return {
        "ok": True,
        "market_code": market_code,
        "market_id": m["id"],
        "stat_field": stat_field,
        "feature_family": feature_family,
        "lookback": lookback,
        "eligible_positions": sorted(list(eligible_positions)),
        "upstream_features_used": [code for code, _ in upstream_cols],
        "upserts": upserts,
    }


'''

jobs_pattern = r'@router\.post\("/jobs/build_features"\)\ndef build_features\((?:.|\n)*?\n\n@router\.post\("/jobs/attach_labels"\)'
jobs_repl = new_build_features + '@router.post("/jobs/attach_labels")'
jobs_text_new, jobs_count = re.subn(jobs_pattern, jobs_repl, jobs_text, count=1)

if jobs_count != 1:
    raise SystemExit("Failed to replace build_features block in jobs.py")

players_path.write_text(players_text_new, encoding="utf-8")
jobs_path.write_text(jobs_text_new, encoding="utf-8")

print("Patched players.py and jobs.py successfully.")
