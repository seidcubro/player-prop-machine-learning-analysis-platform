from pathlib import Path

path = Path(r".\services\api\app\routes\players.py")
text = path.read_text(encoding="utf-8-sig")

if "_clean_feature_payload(" not in text:
    insert_after = "router = APIRouter()\n"
    helper = '''

def _clean_feature_payload(feature_map: dict, meta: dict) -> dict:
    feature_cols = list(meta.get("feature_cols") or [])
    if not feature_cols:
        return dict(feature_map)

    cleaned = {}
    for col in feature_cols:
        if col in feature_map:
            cleaned[col] = feature_map[col]
    return cleaned


def _meta_summary(meta: dict) -> dict:
    return {
        "market_name": meta.get("market_name"),
        "feature_family": meta.get("feature_family"),
        "base_feature_cols": list(meta.get("base_feature_cols") or []),
        "extra_feature_cols": list(meta.get("extra_feature_cols") or []),
        "upstream_features_used": [
            c for c in list(meta.get("extra_feature_cols") or [])
            if c.endswith("_mean") or c.endswith("_trend")
        ],
        "model_metrics": {
            "mae": meta.get("mae"),
            "rmse": meta.get("rmse"),
            "r2": meta.get("r2"),
        },
    }

'''
    text = text.replace(insert_after, insert_after + helper)

text = text.replace(
    '''    return {
        "ok": True,
        "player_id": player["id"],
        "external_id": external_id,
        "player_name": player["name"],
        "market_code": market_code,
        "model_name": model_name,
        "lookback": lookback,
        "as_of_game_date": feat["as_of_game_date"],
        "opponent": feat["opponent"],
        "prediction": pred,
        "features": features,
        "artifact_path": artifact_path,
    }''',
    '''    cleaned_features = _clean_feature_payload(features, meta)
    meta_info = _meta_summary(meta)

    return {
        "ok": True,
        "player_id": player["id"],
        "external_id": external_id,
        "player_name": player["name"],
        "market_code": market_code,
        "market_name": meta_info["market_name"],
        "feature_family": meta_info["feature_family"],
        "model_name": model_name,
        "lookback": lookback,
        "as_of_game_date": feat["as_of_game_date"],
        "opponent": feat["opponent"],
        "prediction": pred,
        "features": cleaned_features,
        "base_feature_cols": meta_info["base_feature_cols"],
        "extra_feature_cols": meta_info["extra_feature_cols"],
        "upstream_features_used": meta_info["upstream_features_used"],
        "model_metrics": meta_info["model_metrics"],
        "artifact_path": artifact_path,
    }'''
)

text = text.replace(
    '''                    "features": row["features"],''',
    '''                    "features": _clean_feature_payload(row["features"], meta),'''
)

text = text.replace(
    '''    return {
        "ok": True,
        "rows": rows,
    }''',
    '''    meta_info = _meta_summary(meta)
    return {
        "ok": True,
        "market_name": meta_info["market_name"],
        "feature_family": meta_info["feature_family"],
        "base_feature_cols": meta_info["base_feature_cols"],
        "extra_feature_cols": meta_info["extra_feature_cols"],
        "upstream_features_used": meta_info["upstream_features_used"],
        "model_metrics": meta_info["model_metrics"],
        "rows": rows,
    }'''
)

path.write_text(text, encoding="utf-8")
print("Patched players.py cleanup")
