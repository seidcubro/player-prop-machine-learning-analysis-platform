# ML Pipeline

## Goals

- Generate market-specific projections that can be compared against sportsbook lines.
- Train reproducible model artifacts with honest, held-out evaluation.
- Ground every change in real data — don't add clamps/magic constants to paper over a
  symptom without first confirming the actual cause (see "History" below).

## Stages

### 1) Ingestion

`jobs/ingestion/app/etl/nflverse_ingest.py` populates `player_game_stats_app`, `nfl_games`,
`snap_counts`, `ff_opportunity`, `injuries`, `depth_charts`, NGS tables, etc. from nflverse.

### 2) Feature engineering

`POST /api/v1/jobs/build_features?market_code=X&lookback=N`
(`services/api/app/routes/jobs.py`) computes, per player and per game, features from a
strictly-prior lookback window (never the target game itself):

**Base features** (every market): `mean`, `stddev`, `weighted_mean` (weights `1..n`, most
recent game weighted highest), `trend` (OLS slope over the window), `aux_mean`/`aux_trend`
(a market-specific secondary stat, e.g. receptions for `rec_yds`).

**Market-specific engineered features** (flattened into `extra_features` JSONB): target
share, yards per target/carry/attempt, opponent defensive rates (rolling allowed
yards/targets/attempts), team pass/rush volume.

**Cross-cutting features added after the original overprojection investigation**:
- `snap_share_mean`/`trend` — rolling `snap_counts.offense_pct`, a role/opportunity signal
  that shows up before it fully shows up in a box-score sample.
- `exp_{rec,rush,pass}_yards`/`exp_receptions` (`mean`/`trend`) — nflverse's own
  `ff_opportunity` expected-usage model (expected production from play context — air yards,
  red zone role — not realized production). ~85-99% coverage for skill positions.
- `team_implied_total`, `team_spread`, `game_total_line`, `game_wind`, `game_temp`,
  `game_div_game` — the **current game's** pre-game Vegas spread/total/weather from
  `nfl_games` (100% coverage 2022-2025), computed for the target game itself, not averaged
  over the lookback window. This is known before kickoff — the same information a
  sportsbook line is priced from — so it is not leakage, and it's a far more direct signal
  of a specific game's likely script/volume than any historical rolling average.
- `injury_questionable`/`doubtful`/`out` — player's own current-week injury report flags.
  Sparse coverage (~15%, since only injured players get listed); contributed very little on
  its own.

Positions are filtered per-market via `prop_markets.eligible_positions` (e.g. `{QB}` for
passing markets, `{QB,RB,WR,FB}` for rushing, `{WR,TE,RB,FB}` for receiving) — see History.

`POST /api/v1/jobs/attach_labels` fills `label_actual` once a game's actual result is known.

### 3) Training (`services/training/train.py`)

For each `(market_code, model_name, lookback)`:
- load `player_market_features` joined to `players` (for position filtering)
- flatten `extra_features` JSON into the feature matrix (`_build_feature_dataframe`)
- time-ordered train/test split (never shuffled — this is a forecasting problem)
- train `RandomForestRegressor` (default) or `GradientBoostingRegressor` (if `MODEL_NAME`
  starts with `gb`) — hyperparameters are env-configurable (`N_ESTIMATORS`, `MAX_DEPTH`,
  `MIN_SAMPLES_LEAF`, `MIN_SAMPLES_SPLIT`, `MAX_FEATURES`, `LEARNING_RATE`, `SUBSAMPLE`) for
  tuning experiments
- optional `TARGET_TRANSFORM=log1p` (see History — currently unused; it made things worse)
- write `{model_name}_{market_code}_lb{lookback}.joblib`/`.json`, update
  `trained_models`/`active_models`

### 4) Evaluation (`services/training/eval.py`)

Independent of training's own internal split — rebuilds the exact same feature matrix
(`build_feature_matrix`, mirrors `train.py`), applies `target_transform` if the model's
metadata calls for it, and reports MAE/RMSE/R²/**bias** (mean of prediction - actual) against
a naive `weighted_mean` baseline, broken down by position and label-magnitude bucket. This is
the tool that caught two real bugs (see History) — always trust `eval.py`'s numbers over
training's own printed metrics or anecdotal spot-checks.

### 5) Edge calculation (`services/training/build_prop_edges.py`)

Loads sportsbook player props, matches each to the single most-recent
`player_market_features` row as of the event date, predicts, blends 30% model / 70%
`weighted_mean`, clamps to within one stddev of `weighted_mean`, compares to the line,
computes win probability (normal approximation) and edge tier, writes `prop_edges`.

## Current model performance

See the table in the root `README.md` — kept in one place to avoid drift.

## History — real bugs found and fixed, in order

The "rec_yds massively overprojects" problem blocked this project for months across 13+
model versions before these were found (all confirmed against live data, not just code
reading):

1. **Stale opponent-based row selection** (`build_prop_edges.py`) — searched a player's
   entire history for any row matching the upcoming opponent, with no recency bound, and
   excluded the correct same-day row via strict `<` instead of `<=`. Whenever a player faced
   a repeat opponent, this could grab a feature snapshot from a different season/team/hot
   streak. Fixed by always taking the single most-recent row `<= event_date`.
2. **`player_market_features.team` was NULL for every market except `rec_yds`** — silently
   zeroed out edge generation for every other market via the team sanity-check.
   `db/backfills/fix_team_final.sql` already had the correct general fix (joining
   `player_game_stats_app`, the table the live pipeline actually uses, on
   `player_id`+`as_of_game_date`, no market restriction); it had only ever been run
   once, before other markets' rows existed.
3. **`eval.py` itself was broken** — joined `players p ON p.id = pmf.player_id` instead of
   `p.external_id` (a hard SQL type error), and never expanded `extra_features` JSON, so it
   could never have evaluated any current model correctly even once the join was fixed.
4. **The `log1p` target transform was actively harmful**, not just insufficient — once
   `eval.py` worked, an apples-to-apples comparison (identical features/hyperparameters,
   only the transform differs) showed R² 0.26 and bias -8.86 (log1p) vs. R² 0.36 and bias
   +0.42 (no transform). Averaging in log-space then inverse-transforming is a biased
   estimator for a right-skewed, zero-inflated stat like receiving yards.
5. **`prop_markets.eligible_positions` was empty for every market**, and `train.py` had no
   position filtering at all — every model trained on rows from all 25 positions, including
   ones that trivially never produce the stat (a defensive lineman always rushing for 0
   yards). `eval.py` separately hardcoded a skill-position filter but only for `rec_yds`,
   which is why rec_yds looked uniquely bad next to other markets whose R² was inflated by a
   flood of trivially-correct zero rows. Fixed by populating `eligible_positions` from real
   nonzero-stat counts and wiring the filter into both scripts.

**Net effect**: rec_yds was never uniquely broken. It was, for most of this project's
history, the only market being evaluated honestly.

## Untried ideas for further R² improvement

- Teammate-injury-driven target-share reallocation (WR1 out -> WR2 usage spikes) — needs
  joining `injuries` + `depth_charts` across a team's roster, not just a player's own status.
- `depth_charts.depth_team` (starter/backup rank) as a feature.
- A real hyperparameter search (grid/Optuna) instead of a handful of manual configs.
- NGS tracking tables (`avg_separation`, `avg_cushion`, `rush_yards_over_expected`, CPOE)
  have very low coverage (~3-7% of rows) — likely only usable for a recent-seasons-only
  model, not the full historical training set.
- Poisson/count regression for TD markets instead of plain regression.
