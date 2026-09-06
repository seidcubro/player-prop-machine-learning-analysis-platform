# ML Pipeline

## Rules I hold myself to

Ground every change in real data. Don't add clamps or magic constants to paper
over a symptom before confirming the cause. The receiving yards problem that
blocked this project for months turned out to be plumbing bugs, not the model,
after thirteen model versions spent chasing it.

Validate forward only. Every split is time-ordered. A model that trains on games
that happened after its test set will look excellent and be worthless.

Don't adopt a change that's inside the noise. If a challenger beats the incumbent
by less than one standard error of the fold-to-fold difference, keep the
incumbent.

## Stages

### 1. Ingestion

`jobs/ingestion/app/etl/nflverse_ingest.py` populates `player_game_stats_app`,
`nfl_games`, `snap_counts`, `ff_opportunity`, `injuries`, `depth_charts`,
`pbp_player_game`, NGS and PFR tables.

It loads one season at a time. Loading the whole range in one call meant a single
unavailable season killed the run, and nflverse 404s any season it hasn't
published. Every ingest function also refuses to write when nothing loaded,
because they all TRUNCATE before inserting and an empty load would wipe good data.

### 2. Feature engineering

`POST /api/v1/jobs/build_features?market_code=X&lookback=N`

Computes per player, per game, from a strictly prior window. The target game
never contributes to its own features.

**Rolling production.** `mean`, `stddev`, `weighted_mean` (linear weights, most
recent game heaviest), `trend` (OLS slope), plus `y_median`, `y_trimmed_mean`,
`y_max`, `y_min` so a single blowup game doesn't drag the whole window, and
`y_season_mean` / `y_season_n` as a slower anchor.

**Panel time-series estimates.** `ewma_level` is exponentially weighted over the
player's whole history with a decay fitted on held-out folds (alpha 0.25, a
half-life of about 2.4 games, which is faster than the fixed five-game window
implied). `ewma_shrunk` pulls that toward the position's prior-season mean by
sample size, so a player with three games gets pulled hard and one with sixty
barely moves.

**Role and opportunity.** Snap share, depth chart rank, `depth_rank_delta` (how
the rank has changed since the window), `days_since_last_game`, teammate injury
counts at the same position, target share, carry share, and nflverse's
`ff_opportunity` expected-usage model.

**Play context** from `pbp_player_game`: red zone targets and carries and their
rates, third down targets, shotgun rate, air yards, YAC, EPA per play, in-game
win probability, total plays.

**Opponent defense by position group.** What the opponent gives up to the
player's own position, taken from that opponent's trailing form and shifted one
game back so the game being predicted never feeds it. This used to be averaged
over the lookback window, which measured the defenses the player had just faced
rather than the one he was about to play.

**Game and venue.** Spread, total, implied team total, temperature, wind,
`is_indoor`, `is_turf`, `is_home`, rest days, divisional game. All computed for
the target game, all known before kickoff, so none of it is leakage.

Positions are filtered per market by `prop_markets.eligible_positions`.

`POST /api/v1/jobs/attach_labels` fills `label_actual` once results are known.

### 3. Model selection

`bakeoff.py` scores every candidate on the same expanding-window folds and
applies the one-standard-error rule. Candidates include ridge and elasticnet as
sanity checks, not because I expected them to win. They won three markets.

It reports the train-minus-test R² gap for every candidate and flags anything
over 0.35. That's what caught LightGBM memorizing.

### 4. Training

`train.py`. Prefix picks the family. Linear models get wrapped in a
StandardScaler pipeline, since a penalized linear model on unscaled features just
regularizes whichever columns happen to have big units.

Writes a `.joblib` plus a metadata `.json` recording feature columns, and updates
`trained_models` and `active_models`.

### 5. Quantiles

`train_quantiles.py` fits q10/q25/q50/q75/q90 per market. The fitted quantiles
are CDF points, so P(over) is read straight off them. Uncertainty comes out
per-row from the features instead of one capped sigma applied to everyone.

Resolves the feature space from `active_models`, not from an env var. An unset
`MODEL_NAME` used to fall back to a legacy model with a much smaller feature set,
and the mismatch only surfaced later at inference.

### 6. Evaluation

`eval.py`. Time-ordered split, position filter from the database, bias check,
lift against a rolling weighted-mean baseline. `SPLIT_MODE=season` holds out the
most recent full season, which is the only split that actually tests September.
The default trailing-fraction split always lands mid-season, so early-season
behavior went unevaluated for a long time.

For TD markets it also reports Brier score and log loss on P(at least one),
because R² on a rare count is a bad guide to whether the probability is right.

### 7. Backtest

`backtest_season.py` is the real test. Trains on seasons strictly before the
target season, predicts the whole season, grades every pick against lines
captured from the archive, and reports hit rate against the break-even implied by
the actual price.

Result for 2025: 6,736 picks, 51.5% hit rate, 53.9% break-even, -4.3% ROI.

Three bugs I had to fix in that script before the number meant anything, each of
which manufactured fake profit:

Grading under bets at the over's price. On passing TDs the over can be +150 while
the under is -180. Using one price for both turned a losing model into +23% ROI.

Averaging American odds. They're two scales spliced at plus/minus 100 with a
discontinuity between them. The mean of -130 and +100 is -15, which implies a
6.7x payout no book ever offered. Convert to decimal first.

Using the rolling weighted mean as "our projection" instead of the model. That
measured the baseline, not the product.

## Model performance

Evaluated with `eval.py`, positions filtered, bias is mean(prediction - actual).

| Market | Model | R² | Bias |
|---|---|---|---|
| rush_att | ridge | 0.75 | -0.02 |
| rush_yds | elasticnet | 0.60 | -0.14 |
| recs | elasticnet | 0.44 | +0.09 |
| pass_att | random forest | 0.42 | +0.89 |
| rec_yds | extra trees | 0.40 | +0.65 |
| pass_yds | random forest | 0.39 | +9.50 |
| pass_completions | random forest | 0.38 | +1.07 |
| rush_td | random forest | 0.17 | 0.00 |
| pass_td | poisson | 0.16 | +0.04 |
| rec_td | random forest | 0.11 | 0.00 |

All beat a rolling five-game weighted average, which is the bar I set.

TD markets sit low on R² and always will. Touchdowns are rare and close to
binary, so R² is the wrong lens. What matters there is whether the probability is
calibrated.

## History

Things that were wrong, and what they actually were.

**The receiving yards overprojection** was five separate bugs, none of them the
model: stale opponent-based row selection in the edge builder, a NULL `team`
column that silently zeroed out every market except `rec_yds`, a broken join in
`eval.py` that meant the evaluation tool had never once run, a log1p target
transform that made things worse (Jensen's inequality on a right-skewed
zero-inflated stat), and empty `eligible_positions` so every model trained on all
25 positions including linemen.

**The blend and clamp.** The edge builder served `0.3 * model + 0.7 *
weighted_mean`, clamped into `weighted_mean +/- stddev`. Never measured.
`eval_blend.py` showed it threw away more than half the model's edge over the
naive baseline.

**Overconfidence.** Win probability came from a Gaussian with a hand-capped
sigma. Grading showed every bucket 14 to 29 points overconfident, and two tiers
were losing money while being advertised at 62% and 57%.

**Stale context.** Every pre-kickoff feature was read off a stored row that could
be eight months old. Covered in ARCHITECTURE.md.

**Opponent features described the wrong teams.** Averaged over the window, so
they measured strength of schedule while being named and used as a matchup
signal.

**Traded players dropped silently.** The team sanity check keyed on the player's
historical team, which inverted its purpose.
