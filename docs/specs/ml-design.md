# ML Design Spec

Last updated: 2026-09-06

Read `docs/ML_PIPELINE.md` first if you're touching this. It has the bug history,
and most of what's wrong with a change you're about to make has probably already
been wrong once.

## Architecture

Market-scoped, keyed by `(market_code, model_name, lookback)`.

There's no single production model family. Each market gets whichever family won
the bakeoff on expanding-window folds:

| market | family |
|---|---|
| rush_att | ridge |
| rush_yds, recs | elasticnet |
| rec_yds | extra trees |
| pass_att, pass_yds, pass_completions, rush_td, rec_td | random forest |
| pass_td | poisson |

Three markets are linear. That wasn't the plan, it's what won.

`active_models` is the source of truth for which model serves a market.
`build_prop_edges.py` reads it. It used to have filenames hardcoded, which meant
retraining had no effect on anything the site showed.

## Features

Full list in `docs/ML_PIPELINE.md`. Everything is reproducible from
`player_game_stats_app` plus the supporting nflverse tables via
`POST /jobs/build_features`.

Position eligibility comes from `prop_markets.eligible_positions` and is enforced
in `train.py` and `eval.py`. It was missing for most of this project's history,
which made cross-market R² comparisons meaningless (a rushing model looked great
because it correctly predicted zero for offensive linemen).

## Labels

`label_actual` on `player_market_features`, filled by
`POST /jobs/attach_labels` once the game has been played. NULL for future games.

## Inference

One path. `build_prop_edges.py` loads the active model, builds a feature vector
from the player's most recent feature row, **refreshes everything known before
kickoff** for the actual upcoming game, predicts, reads P(over) off the quantile
models, compares to the line, writes `prop_edges`.

That refresh step is the part to be careful with. Depth chart, injuries, Vegas
line, weather, venue and opponent all describe the game being predicted, not
whatever game the stored row was about. `audit_freshness.py` fails the build if
any of them stops being refreshed.

There used to be a second path, `projection_ml`, which built its own projection
with no freshness correction and disagreed with what the site served. It's
removed.

Failure modes handled: market not found, no matching feature row, artifact
missing, active model metadata missing. All of them skip the prop rather than
guessing.

## Uncertainty

`train_quantiles.py` fits q10 through q90 per market. The fitted quantiles are
CDF points, so P(over line) is interpolated straight off them and clipped away
from 0 and 1. No prop is a certainty.

Quantile bundles must share the point model's exact feature space. Building them
from a different model throws at inference, and `audit_freshness.py` checks it.

## Evaluation

`eval.py` reports MAE, RMSE, R², bias (positive means overprediction), broken
down by position and by label magnitude, plus lift over a naive weighted-mean
baseline. Reports land in
`services/training/artifacts/evals/{model}_{market}_lb{lookback}_eval.json`.

For TD markets it also reports Brier and log loss on P(at least one), because R²
on a rare count says almost nothing about whether the probability is right.

`SPLIT_MODE=season` holds out the most recent full season. Use it. The default
trailing-fraction split always lands mid-season, so September never gets tested,
and September is when every rolling window is built from last season's games.

Trust `eval.py` over whatever `train.py` prints. Training's split can differ
subtly, and `eval.py` exists specifically to catch bias and leakage that a plain
R² won't surface.

## What the numbers actually mean

R² against actuals is not the goal. The goal is beating a sportsbook line, and
those are different problems.

`backtest_season.py` is the honest test: train on prior seasons, predict a whole
season, grade against real captured lines. 2025 came out at 51.5% against a 53.9%
break-even, which is a losing model. See the README.

Two things to keep in mind when reading any R² in this repo:

Break-even on these props is roughly 53.9%, not the 52.4% that -110 implies.
Prop vig is heavier than standard.

The largest disagreements with the line are the worst bets. Three separate tests
found this. Don't rank picks by disagreement size and call the top of that list
high confidence.
