# Training Layout

Fifty-odd files in one directory, and only about a third of them run on a
schedule. This says which is which, because the difference is not obvious from
the filenames and I have gone looking for it more than once.

## The weekly pipeline

These are what `scripts/scheduled_update.sh` actually invokes, in order. If one
of them breaks, the site goes stale.

| file | job |
|---|---|
| `train.py` | fit the point model for one market |
| `train_quantiles.py` | fit the quantile ensemble, same feature space |
| `eval.py` | score the point model on a held-out split |
| `eval_stale_role.py` | fit the correction for a player whose role just changed |
| `build_projections.py` | project every eligible player, priced or not |
| `build_prop_edges.py` | price those projections against the book and tier them |
| `grade_edges.py` | settle played picks against the box score |
| `eval_clv.py` | score picks against the closing line |
| `audit_freshness.py` | the gate, exits non-zero when anything is stale |

## Calibration

Fitted from graded results, read at serving time. Each writes a JSON artifact
and each has an apply module beside it, so a missing artifact means the
correction is skipped rather than the build failing.

| fitter | applier | corrects |
|---|---|---|
| `fit_probability_calibrator.py` | (read in `build_prop_edges`) | P(over) |
| `fit_interval_calibrator.py` | `interval_calibration.py` | the published range |
| `fit_median_anchor.py` | `median_anchor.py` | the median against the point projection |
| `fit_spread_calibrator.py` | `spread_calibration.py` | how far predictions spread |

`odds_markets.py` maps the provider's market keys to this project's codes and is
imported by both the API and the edge builder, which is why it lives here and is
mounted read-only into the API's container.

## Backfills and recovery

Run by hand, usually once, usually after something went wrong.

`backfill_historical_odds.py`, `backfill_projection_history.py`,
`backfill_track_record.py`, `recover_live_picks.py`.

`backtest_season.py`, `backtest_quantile_ev.py` and `backtest_sides.py` rebuild
the record from scratch on refit models. `bakeoff.py` picks the model family per
market.

`eval_strategy.py` is the odd one here: it is run by hand, but it regenerates
`apps/web/src/lib/published-record.json`, which is every performance figure the
site states as fact and which `audit_freshness.py` check [16] compares against
the database. Rerun it after anything that rebuilds `prop_edge_results`, or the
audit will tell you the site and the record disagree.

## Analysis

Everything named `eval_*` beyond the pipeline four and `eval_strategy.py`, plus
`simulate.py`, `stress_test_edge.py`, `find_conditional_edges.py`,
`market_disagreement.py`, `under_asymmetry.py`, `diagnose_overproj.py`,
`debug_context.py`, `projection_log.py`, `train_residual.py` and
`train_td_probability.py`.

None of these run on a schedule and none of them are imported by anything that
does. They are kept on purpose. Most of this project's decisions are decisions
to *not* do something, and the script that proved it is the only record of why.
`train_residual.py` is the clearest case: predicting the line's error instead of
the stat is an obvious idea, it comes up again every few months, and the file is
the answer to it. Deleting them would leave the conclusions in my head and the
repo looking like nothing was ever tried.

If one of these turns into something the pipeline needs, it moves up this page
and into `scheduled_update.sh`. Until then it is research, and it is labelled as
research so nobody deploys it by accident.
