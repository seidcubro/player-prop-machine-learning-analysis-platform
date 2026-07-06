# Data Model Spec

Last updated: 2026-07-05

Database: PostgreSQL. This describes the tables actually in use — an earlier version of
this doc described a `features_{market}`/`labels_{market}`-per-market design that was never
built; the real system uses one unified feature table for every market.

---

## players

- `id` (PK, integer) — internal id, used by API routes (`/players/{id}`).
- `external_id` (text, unique) — nflverse GSIS id (e.g. `00-0033858`). **This is the join key
  used everywhere else** (`player_market_features.player_id`, `snap_counts.player_id`,
  `ff_opportunity.player_id`, etc.) — never `players.id`. Joining on `players.id` was a
  recurring historical bug (it's an `integer`, `external_id` is `text`; the two are unrelated
  values).
- `first_name`, `last_name`, `name`, `position`, `team`.

## prop_markets

Market registry — one row per bettable stat (`rec_yds`, `recs`, `rush_yds`, `pass_att`,
etc.). Key columns:
- `code` (unique, e.g. `"rec_yds"`) — **the join/lookup key used everywhere**, not `id`
  directly and never `market_code` (there is no such column).
- `stat_field` — the `player_game_stats_app` column this market's label comes from.
- `feature_family` — `"receiving"` / `"rushing"` / `"passing"`, drives which market-specific
  features `jobs.py` computes.
- `eligible_positions` (`text[]`) — positions allowed to train/evaluate for this market
  (populated from real nonzero-stat counts, see `db/migrations/populate_eligible_positions.sql`).
- `is_active`, `train_enabled`, `predict_enabled`, `target_kind` (`"regression"`), `scope`
  (`"player"`).

## player_game_stats_app

Raw per-player-per-game box score, sourced from nflverse ingestion. Includes `player_id`
(GSIS id), `game_id`, `season`, `week`, `game_date`, `team`, `opponent`, `position`, and the
full stat line (`targets`, `receptions`, `receiving_yards`, `carries`, `rushing_yards`,
`attempts`, `completions`, `passing_yards`, etc.).

## player_market_features

The feature store — one row per `(player_id, market_id, as_of_game_date, opponent,
lookback)`. `as_of_game_date`/`opponent` are the game being predicted; all feature values are
computed from the strictly-prior lookback window.

- `mean`, `stddev`, `weighted_mean`, `trend` — base rolling features.
- `aux_mean`, `aux_trend` — market-specific secondary stat.
- `team` — the player's team for this game (was NULL for every market except `rec_yds`
  until this was backfilled — see `docs/ML_PIPELINE.md` History).
- `extra_features` (JSONB) — market-specific engineered features, snap share, `ff_opportunity`
  expected usage, current-game Vegas context, injury flags. Flattened into the model's
  feature matrix by `train.py`/`eval.py`/`build_prop_edges.py`.
- `label_actual` — the realized value once the target game has been played (filled by
  `attach_labels`); NULL for future/unplayed games.

## trained_models / active_models

`trained_models` — append-only history of every training run
(`model_name, market_id, lookback, artifact_path, train_rows, test_rows, mae, rmse, r2`).
`active_models` — one row per `market_id`, the currently-selected model for serving.

## odds_events / odds_player_props

Raw sportsbook data synced from The Odds API (`services/api/app/routes/odds.py`).
`odds_events` — game metadata (`provider_event_id`, `commence_time`, `home_team`,
`away_team`). `odds_player_props` — one row per (event, bookmaker, market, player,
outcome), with `line`, `price_american`.

## prop_edges

Output of `build_prop_edges.py`. One row per (event, market, player, bookmaker): `line`,
`projection`, `raw_edge`, `win_prob`, `recommended_side`, `edge_tier`, `model_name`,
`model_r2`. **Not currently served by any API route** — see `docs/API.md`.

## Supporting context tables (all sourced from nflverse ingestion)

- `nfl_games` — schedule + Vegas lines (`spread_line`, `total_line`) + weather
  (`temp`, `wind`) + rest days + `div_game`. 100% coverage for `spread_line`/`total_line`.
- `snap_counts` — `offense_pct`/`defense_pct`/`st_pct` per player-game. ~92% join coverage.
- `ff_opportunity` — nflverse's expected-usage model (`*_exp` columns). ~85-99% coverage for
  skill positions.
- `injuries` — weekly injury report (`report_status`). Sparse coverage (~15%) since only
  injured players are listed.
- `depth_charts`, NGS tables (`ngs_passing`/`ngs_receiving`/`ngs_rushing`) — ingested but not
  yet wired into feature generation (NGS coverage is only ~3-7% of rows, likely too sparse
  for the full historical training set; `depth_charts.depth_team` is a plausible future
  feature, not yet tried).
