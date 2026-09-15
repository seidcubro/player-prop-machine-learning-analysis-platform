"""Fill in what we projected for games that have already been played.

player_projections holds the upcoming slate and is truncated on every build, so
the moment a game kicks off the number we published for it is gone. The only
surviving record is prop_edge_results, and that needs a sportsbook to have
posted a line we paid for and a pick that cleared the publication filters. For
most players that is nothing: Trey McBride has real projections every week and
not one stored row, because no pick was ever published on him.

This reconstructs the missing history by running each market's active model back
over the feature rows for games already played, which is the same thing eval.py
does to score a model. The features were built from games before each row's own
game, so a reconstructed number is what the model would have said at the time,
not hindsight.

Two honest limits, both worth knowing before reading the output:

  * The models are today's models. A row for a 2023 game is what the current
    model thinks about that game, not what the model of 2023 would have said.
    For "was our projection close to what he did", that is the right question.
    For "what was our live record", use prop_edge_results, which is the real
    published history.

  * Rows are marked with the model that produced them, so a reconstruction can
    always be told apart from a projection that actually shipped.

Going forward build_projections.py records history on every run and none of this
is needed.
"""

import json
import os
from pathlib import Path

import eval as ev
import numpy as np
import pandas as pd
from build_prop_edges import calibrated_quantiles, load_quantile_bundle
from sqlalchemy import create_engine, text

# Built from POSTGRES_* the way every other script here does, with an explicit
# DATABASE_URL still winning if one is set.
#
# The dev fallback this used to carry was postgresql://app:app@postgres, which
# is the development password and is correct only by coincidence: the dev stack
# happens to use it. Production sets POSTGRES_PASSWORD to a random string and
# does not set DATABASE_URL at all, so anything relying on that fallback would
# have failed on the server with an authentication error. Two of these scripts
# run in the weekly pipeline.
DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
LOOKBACK = int(os.getenv("LOOKBACK", "5"))

UPSERT = text("""
    INSERT INTO player_projection_history (
        player_id, player_name, team, opponent, position, market_code,
        game_date, projection, projection_raw, p50_raw,
        p10, p25, p50, p75, p90, model_name,
        depth_rank, projected_at)
    VALUES (:player_id, :player_name, :team, :opponent, :position, :market_code,
            :game_date, :projection, :projection, :p50,
            :p10, :p25, :p50, :p75, :p90, :model_name,
            NULL, NOW())
    ON CONFLICT (player_id, market_code, game_date) DO NOTHING
""")


def main():
    eng = create_engine(DATABASE_URL, future=True)
    actives = pd.read_sql(text("""
        SELECT m.code, a.model_name, a.lookback
        FROM active_models a JOIN prop_markets m ON m.id = a.market_id
        ORDER BY m.code
    """), eng)

    names = pd.read_sql(text("""
        SELECT DISTINCT ON (s.player_id)
               s.player_id, p.name AS player_name, s.position
        FROM player_game_stats_app s
        LEFT JOIN players p ON p.external_id = s.player_id
        ORDER BY s.player_id, s.game_date DESC
    """), eng).set_index("player_id")

    total = 0
    for r in actives.itertuples():
        meta_path = ARTIFACTS / f"{r.model_name}_{r.code}_lb{r.lookback}.json"
        model_path = ARTIFACTS / f"{r.model_name}_{r.code}_lb{r.lookback}.joblib"
        if not meta_path.exists() or not model_path.exists():
            print(f"  {r.code}: no artifact, skipped")
            continue

        import joblib
        cols = json.load(open(meta_path))["feature_cols"]
        model = joblib.load(model_path)

        # Set the module globals, not just the environment.
        #
        # eval.py binds MARKET_CODE and LOOKBACK at import time, so exporting
        # them here does nothing: every market loaded the same rec_yds rows and
        # got the wrong population. Quarterbacks vanished from the passing
        # history and receivers picked up rushing projections they are not
        # eligible for. Assigning the attributes is what actually changes which
        # rows load.
        os.environ["MARKET_CODE"] = r.code
        os.environ["LOOKBACK"] = str(r.lookback)
        ev.MARKET_CODE = r.code
        ev.LOOKBACK = int(r.lookback)
        try:
            df = ev.load_labeled_rows(cols)
        except SystemExit as exc:
            print(f"  {r.code}: {exc}")
            continue
        if df.empty:
            print(f"  {r.code}: no labeled rows")
            continue

        X = ev.build_feature_matrix(df, cols)
        pred = np.clip(model.predict(X), 0, None)

        bundle = load_quantile_bundle(ARTIFACTS, r.code, r.lookback)
        qpred = {}
        if bundle:
            models = bundle.get("models") or {}
            for q, qm in models.items():
                qpred[float(q)] = np.clip(qm.predict(X), 0, None)

        rows = []
        for i, row in enumerate(df.itertuples()):
            meta = names.loc[row.player_id] if row.player_id in names.index else None
            qs = {}
            if qpred:
                raw = {q: float(v[i]) for q, v in qpred.items()}
                qs = calibrated_quantiles(raw, (bundle or {}).get("calibration"))
            rows.append({
                "player_id": row.player_id,
                "player_name": None if meta is None else meta["player_name"],
                "team": getattr(row, "team", None),
                "opponent": getattr(row, "opponent", None),
                "position": getattr(row, "position", None),
                "market_code": r.code,
                "game_date": row.as_of_game_date,
                "projection": float(pred[i]),
                "p10": qs.get(0.10), "p25": qs.get(0.25), "p50": qs.get(0.50),
                "p75": qs.get(0.75), "p90": qs.get(0.90),
                "model_name": r.model_name,
            })

        with eng.begin() as conn:
            for chunk in range(0, len(rows), 1000):
                conn.execute(UPSERT, rows[chunk:chunk + 1000])
        total += len(rows)
        print(f"  {r.code}: {len(rows)} rows ({r.model_name})")

    print(f"\nPROJECTION HISTORY BACKFILLED: {total} rows")


if __name__ == "__main__":
    main()
