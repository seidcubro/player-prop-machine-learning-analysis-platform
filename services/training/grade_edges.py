"""Grade historical prop edges against what actually happened.

Every edge carries a predicted win probability. Grading is what makes that
number accountable: match each past edge to the player's real stat line for
that game, decide whether the recommended side won, and store it. That gives
both a per-player track record for the profile page and the aggregate
calibration check -- do picks quoted at 80% actually win 80% of the time?

Name matching is the fiddly part: sportsbook feeds spell the same player
several ways ("TJ Hockenson" vs "T.J. Hockenson"), so names are normalised the
same way `build_prop_edges.py` does before joining.

Kickoff times are UTC, so a Sunday-night game lands on the following calendar
day. Matching therefore allows a +/- 1 day window and prefers the closest game.
"""

import os

import pandas as pd
from sqlalchemy import create_engine, text

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


def norm_name(name: str) -> str:
    """Match `build_prop_edges.py`'s normalisation exactly."""
    return " ".join((name or "").lower().replace(".", "").replace("-", " ").split())


GRADE_SQL = """
INSERT INTO prop_edge_results (
    edge_id, player_id, player_name, game_date, market_code, line, projection,
    projection_median,
    recommended_side, win_prob, edge_tier, actual, hit,
    home_team, away_team, bookmaker_title, price_american
)
-- One row per player/game/market. Every sportsbook prices the same prop, so an
-- ungraded join records the identical pick three times and inflates the sample.
-- Keep the strongest edge, which is the one the site actually surfaces.
SELECT DISTINCT ON (g.player_id, g.game_date, e.market_code)
    e.id,
    g.player_id,
    e.player_name,
    g.game_date,
    e.market_code,
    e.line,
    e.projection,
    e.projection_median,
    e.recommended_side,
    e.win_prob,
    e.edge_tier,
    g.actual,
    CASE
        WHEN g.actual = e.line THEN NULL                       -- push
        WHEN e.recommended_side = 'over'  THEN g.actual > e.line
        WHEN e.recommended_side = 'under' THEN g.actual < e.line
        ELSE NULL
    END AS hit,
    e.home_team,
    e.away_team,
    e.bookmaker_title,
    e.price_american
FROM prop_edges e
JOIN LATERAL (
    SELECT
        pgs.player_id,
        pgs.game_date,
        CASE m.stat_field
            WHEN 'receiving_yards' THEN pgs.receiving_yards
            WHEN 'receptions'      THEN pgs.receptions
            WHEN 'receiving_tds'   THEN pgs.receiving_tds
            WHEN 'rushing_yards'   THEN pgs.rushing_yards
            WHEN 'carries'         THEN pgs.carries
            WHEN 'rushing_tds'     THEN pgs.rushing_tds
            WHEN 'passing_yards'   THEN pgs.passing_yards
            WHEN 'attempts'        THEN pgs.attempts
            WHEN 'completions'     THEN pgs.completions
            WHEN 'passing_tds'     THEN pgs.passing_tds
        END::float8 AS actual
    FROM player_game_stats_app pgs
    JOIN players p ON p.external_id = pgs.player_id
    JOIN prop_markets m ON m.code = e.market_code
    WHERE lower(replace(replace(p.name, '.', ''), '-', ' ')) =
          lower(replace(replace(e.player_name, '.', ''), '-', ' '))
      AND pgs.game_date BETWEEN (e.commence_time AT TIME ZONE 'UTC')::date - 1
                            AND (e.commence_time AT TIME ZONE 'UTC')::date + 1
    ORDER BY abs(pgs.game_date - (e.commence_time AT TIME ZONE 'UTC')::date)
    LIMIT 1
) g ON TRUE
WHERE e.commence_time IS NOT NULL
  AND g.actual IS NOT NULL
ORDER BY g.player_id, g.game_date, e.market_code,
         abs(e.projection - e.line) DESC, e.id
ON CONFLICT (player_id, game_date, market_code) DO UPDATE SET
    edge_id   = EXCLUDED.edge_id,
    line      = EXCLUDED.line,
    projection = EXCLUDED.projection,
    projection_median = EXCLUDED.projection_median,
    recommended_side = EXCLUDED.recommended_side,
    win_prob  = EXCLUDED.win_prob,
    edge_tier = EXCLUDED.edge_tier,
    actual    = EXCLUDED.actual,
    hit       = EXCLUDED.hit,
    home_team = EXCLUDED.home_team,
    away_team = EXCLUDED.away_team,
    bookmaker_title = EXCLUDED.bookmaker_title,
    price_american  = EXCLUDED.price_american,
    graded_at = NOW();
"""


def main():
    engine = create_engine(DATABASE_URL, future=True)
    with engine.begin() as conn:
        n_edges = conn.execute(text("SELECT count(*) FROM prop_edges")).scalar()
        conn.execute(text(GRADE_SQL))
        graded = conn.execute(text("SELECT count(*) FROM prop_edge_results")).scalar()

        print(f"graded {graded}/{n_edges} edges")

        print("\n-- calibration: does a quoted win probability hold up? --")
        rows = conn.execute(text("""
            SELECT
              width_bucket(win_prob, 0.5, 1.0, 5) AS b,
              round(min(win_prob)::numeric, 2) AS lo,
              round(max(win_prob)::numeric, 2) AS hi,
              count(*) AS n,
              round(avg(win_prob)::numeric, 3) AS predicted,
              round(avg(CASE WHEN hit THEN 1.0 ELSE 0.0 END)::numeric, 3) AS actual_hit_rate
            FROM prop_edge_results
            WHERE hit IS NOT NULL
            GROUP BY b ORDER BY b
        """)).mappings().all()
        print(f"{'range':<16}{'n':>6}{'predicted':>12}{'actual':>10}")
        for r in rows:
            print(f"{str(r['lo']) + '-' + str(r['hi']):<16}{r['n']:>6}"
                  f"{float(r['predicted']):>12.3f}{float(r['actual_hit_rate']):>10.3f}")

        print("\n-- hit rate by tier --")
        rows = conn.execute(text("""
            SELECT edge_tier, count(*) n,
                   round(avg(CASE WHEN hit THEN 1.0 ELSE 0.0 END)::numeric, 3) hit_rate,
                   round(avg(win_prob)::numeric, 3) avg_predicted
            FROM prop_edge_results WHERE hit IS NOT NULL
            GROUP BY edge_tier ORDER BY avg_predicted DESC
        """)).mappings().all()
        for r in rows:
            print(f"  {r['edge_tier']:<8} n={r['n']:<5} hit={float(r['hit_rate']):.3f} "
                  f"predicted={float(r['avg_predicted']):.3f}")

        print("\n-- hit rate by market --")
        rows = conn.execute(text("""
            SELECT market_code, count(*) n,
                   round(avg(CASE WHEN hit THEN 1.0 ELSE 0.0 END)::numeric, 3) hit_rate
            FROM prop_edge_results WHERE hit IS NOT NULL
            GROUP BY market_code ORDER BY n DESC
        """)).mappings().all()
        for r in rows:
            print(f"  {r['market_code']:<18} n={r['n']:<5} hit={float(r['hit_rate']):.3f}")


if __name__ == "__main__":
    main()
