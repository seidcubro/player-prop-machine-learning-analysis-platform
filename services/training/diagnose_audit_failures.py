"""Work out whether the two failing audit checks are real or self-inflicted.

The board and daily runs have been failing on the same two lines for days:

    pass_yds: not refreshed -> ['opp_pass_attempts_trend', 'opp_yards_per_attempt_trend']
    opponent feature barely tracks the real opponent (corr=0.49, mean abs diff=9.5)

Both are the kind of failure that can mean either "the board is wrong" or "the
check measures something the builder never computed". The opponent check has
already been corrected once for exactly that, so it gets measured rather than
reasoned about. Three quantities, side by side:

    stored       what player_market_features actually holds
    builder_now  opp_pos_form recomputed here with the builder's own SQL,
                 joined the builder's way (on the BOX SCORE's opponent)
    audit_truth  the audit's recomputation, keyed on the FEATURE ROW's
                 opponent column

stored vs builder_now disagreeing means the stored rows are from an older
generation. builder_now vs audit_truth disagreeing means the check is arguing
with itself. The opponent column is part of the table's unique key, so one
player-game can hold two rows under two spellings of the same team, and only one
of them keys the right defense.

Writes nothing. Run it, read it, then decide what to change.

    docker compose run --rm -T trainer python diagnose_audit_failures.py
"""

import json
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)

DEAD = ("opp_pass_attempts_trend", "opp_yards_per_attempt_trend")


def head(s):
    print()
    print(s)
    print("-" * len(s))


def active_models(engine):
    head("[1] active models, and which of them name features nothing writes")
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT m.code, m.id AS market_id, a.model_name, a.lookback "
            "FROM active_models a JOIN prop_markets m ON m.id = a.market_id "
            "ORDER BY m.code"
        )).mappings().all()

    for a in rows:
        meta = ARTIFACTS / f"{a['model_name']}_{a['code']}_lb{a['lookback']}.json"
        if not meta.exists():
            print(f"  {a['code']:<18} {a['model_name']:<22} lb{a['lookback']}  META MISSING")
            continue
        cols = json.loads(meta.read_text())["feature_cols"]
        bad = [d for d in DEAD if d in cols]
        note = f"carries {bad}" if bad else ""
        print(f"  {a['code']:<18} {a['model_name']:<22} lb{a['lookback']}  "
              f"{len(cols):>3} cols  {note}")

    # Is there a cleaner sibling already on disk for anything that carries them?
    for a in rows:
        meta = ARTIFACTS / f"{a['model_name']}_{a['code']}_lb{a['lookback']}.json"
        if not meta.exists():
            continue
        if not any(d in json.loads(meta.read_text())["feature_cols"] for d in DEAD):
            continue
        print(f"\n  siblings for {a['code']} lb{a['lookback']} without those columns:")
        for p in sorted(ARTIFACTS.glob(f"*_{a['code']}_lb{a['lookback']}.json")):
            try:
                cols = json.loads(p.read_text())["feature_cols"]
            except Exception:
                continue
            if not cols:
                continue
            flag = "DEAD" if any(d in cols for d in DEAD) else "clean"
            fitted = p.with_suffix(".joblib").exists()
            print(f"    {p.stem:<42} {len(cols):>3} cols  {flag:<5} "
                  f"{'joblib present' if fitted else 'no joblib'}")

    # And are those names written by anything at all, in any feature row?
    with engine.connect() as c:
        for d in DEAD:
            n = c.execute(text(
                "SELECT count(*) FROM player_market_features "
                "WHERE extra_features ? :k"
            ), {"k": d}).scalar()
            recent = c.execute(text(
                "SELECT max(as_of_game_date) FROM player_market_features "
                "WHERE extra_features ? :k"
            ), {"k": d}).scalar()
            print(f"  {d:<30} present on {n:>8,} feature rows, newest {recent}")


OPP_SQL = text("""
WITH opp_pos_defense AS (
    SELECT opponent AS defense_team, game_date, position,
           SUM(COALESCE(rushing_yards, 0))::float8 AS pos_rush_yds_allowed
    FROM player_game_stats_app
    WHERE game_date IS NOT NULL
      AND opponent IS NOT NULL
      AND position IS NOT NULL
    GROUP BY opponent, game_date, position
),
opp_pos_form AS (
    SELECT defense_team, game_date, position,
           AVG(pos_rush_yds_allowed) OVER w AS form_pos_rush_yds,
           count(*) OVER w AS games_in_window
    FROM opp_pos_defense
    WINDOW w AS (
        PARTITION BY defense_team, position
        ORDER BY game_date
        ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING
    )
),
r AS (
    SELECT f.player_id,
           f.as_of_game_date,
           f.opponent  AS feature_opp,
           g.opponent  AS box_opp,
           g.position,
           (f.extra_features->>'opp_pos_rush_yds_allowed')::float AS stored
    FROM player_market_features f
    JOIN player_game_stats_app g
      ON g.player_id = f.player_id AND g.game_date = f.as_of_game_date
    WHERE f.market_id = 2 AND f.lookback = 5
      AND f.extra_features ? 'opp_pos_rush_yds_allowed'
      AND g.position = 'RB'
    ORDER BY f.as_of_game_date DESC LIMIT 200
)
SELECT r.*,
       opf.form_pos_rush_yds AS builder_now,
       opf.games_in_window,
       (SELECT AVG(s) FROM (
            SELECT SUM(g2.rushing_yards) AS s
            FROM player_game_stats_app g2
            WHERE g2.opponent = r.feature_opp AND g2.position = 'RB'
              AND g2.game_date < r.as_of_game_date
            GROUP BY g2.game_date
            ORDER BY g2.game_date DESC
            LIMIT 8) t) AS audit_truth
FROM r
LEFT JOIN opp_pos_form opf
  ON opf.defense_team = r.box_opp
 AND opf.game_date = r.as_of_game_date
 AND opf.position = r.position
""")


def opponent_feature(engine):
    head("[2] the opponent feature, measured three ways")
    df = pd.read_sql(OPP_SQL, engine)
    print(f"  rows                  : {len(df)}")
    print(f"  distinct game dates   : {df['as_of_game_date'].nunique()}  "
          f"({df['as_of_game_date'].min()} .. {df['as_of_game_date'].max()})")
    print(f"  distinct defenses     : {df['box_opp'].nunique()}")
    mismatch = df[df["feature_opp"] != df["box_opp"]]
    print(f"  feature opp != box opp: {len(mismatch)}")
    if len(mismatch):
        print("    spellings seen      :",
              sorted({(str(a), str(b)) for a, b in
                      zip(mismatch["feature_opp"], mismatch["box_opp"])})[:12])
    print(f"  builder_now null      : {df['builder_now'].isna().sum()}")
    print(f"  audit_truth null      : {df['audit_truth'].isna().sum()}")

    d = df.dropna(subset=["stored", "builder_now", "audit_truth"])
    if d.empty:
        print("  nothing comparable; stop here and look at the nulls above")
        return
    print(f"\n  comparable rows       : {len(d)}")
    for name in ("stored", "builder_now", "audit_truth"):
        print(f"  {name:<12} mean={d[name].mean():7.2f}  sd={d[name].std():6.2f}  "
              f"min={d[name].min():7.2f}  max={d[name].max():7.2f}")
    print()
    for a, b in (("stored", "builder_now"),
                 ("stored", "audit_truth"),
                 ("builder_now", "audit_truth")):
        corr = d[a].corr(d[b])
        err = (d[a] - d[b]).abs().mean()
        exact = (d[a].round(4) == d[b].round(4)).mean()
        print(f"  {a:<12} vs {b:<12} corr={corr:5.3f}  "
              f"mean|diff|={err:6.2f}  identical={exact:6.1%}")

    print("\n  worst twelve by |stored - builder_now|:")
    w = d.assign(gap=(d["stored"] - d["builder_now"]).abs()) \
         .sort_values("gap", ascending=False).head(12)
    print("    date        pid     pos  featopp boxopp  stored  builder  audit  win")
    for _, r in w.iterrows():
        print(f"    {r['as_of_game_date']}  {r['player_id']:<6}  {r['position']:<3}  "
              f"{str(r['feature_opp']):<7} {str(r['box_opp']):<6}  "
              f"{r['stored']:6.1f}  {r['builder_now']:7.1f}  "
              f"{r['audit_truth']:5.1f}  {int(r['games_in_window'])}")

    # Between-defense spread is the denominator the correlation is judged on. If
    # all 200 rows come from two Sundays, most of them share a defense and the
    # correlation is measuring agreement on a handful of distinct values.
    per_date = d.groupby("as_of_game_date")["box_opp"].nunique()
    print(f"\n  defenses per date     : {per_date.to_dict()}")
    across = d.groupby("box_opp")["builder_now"].mean().std()
    within = d.groupby("box_opp")["builder_now"].std().mean()
    print(f"  sd of builder_now across defenses : {across:.2f}")
    print(f"  mean sd within one defense        : {within:.2f}")


def main():
    engine = create_engine(DATABASE_URL, future=True)
    active_models(engine)
    opponent_feature(engine)
    print()


if __name__ == "__main__":
    main()
