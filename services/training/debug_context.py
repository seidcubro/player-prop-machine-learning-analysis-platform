"""Show what the model actually sees for one player's upcoming prop."""
import os, json, datetime
import pandas as pd, joblib
from pathlib import Path
from sqlalchemy import create_engine, text
import build_prop_edges as bp

NAME = os.getenv("PLAYER", "Woody Marks")
MARKET = os.getenv("MARKET", "rush_yds")

engine = create_engine(bp.DATABASE_URL, future=True)
ctx = bp.load_current_context(engine)
art = Path(bp.ARTIFACT_DIR)
meta, model = bp.load_model_meta(art, MARKET, engine)
quant = bp.load_quantile_bundle(art, MARKET, int(meta.get("lookback", 5)))

row = pd.read_sql(text("""
    SELECT f.*, p.name, p.team AS current_team
    FROM player_market_features f JOIN players p ON p.external_id=f.player_id
    JOIN prop_markets m ON m.id=f.market_id
    WHERE p.name=:n AND m.code=:c AND f.lookback=5
    ORDER BY f.as_of_game_date DESC LIMIT 1
"""), engine, params={"n": NAME, "c": MARKET}).iloc[0]

extra = row["extra_features"]
if isinstance(extra, str):
    extra = json.loads(extra)

base = {k: row.get(k, 0.0) for k in
        ["mean","stddev","weighted_mean","trend","aux_mean","aux_trend","recs_mean","recs_trend"]}
feats = {}
for c in meta["feature_cols"]:
    v = base[c] if c in base else extra.get(c, 0.0)
    try:
        f = float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        f = 0.0
    feats[c] = f

before = dict(feats)
event_date = datetime.date(2026, 9, 13)
feats["_last_game_date"] = row["as_of_game_date"]
bp.apply_current_context(feats, ctx, row["player_id"], row["current_team"], event_date)
feats.pop("_last_game_date", None)

print(f"\n{NAME} / {MARKET} / event {event_date}  team={row['current_team']}")
print(f"in depth index: {row['player_id'] in ctx['depth'].index}")
print(f"game found for (team,date): {(row['current_team'], event_date) in ctx['games']}")
print("\nchanged by the override:")
for k in sorted(before):
    if abs(before[k] - feats[k]) > 1e-9:
        print(f"  {k:<28}{before[k]:>10.3f} -> {feats[k]:>10.3f}")

print(f"\nprojection before: {max(0.0, float(model.predict(pd.DataFrame([before]))[0])):.1f}")
print(f"projection after : {max(0.0, float(model.predict(pd.DataFrame([feats]))[0])):.1f}")
