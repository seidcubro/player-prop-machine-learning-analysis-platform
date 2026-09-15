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

Two things about the fit are load-bearing and were both wrong at first.

The calibration map is measured on a **held-out season**, not on cross-validated
folds. `TimeSeriesSplit` puts its boundaries wherever the row count falls, which
is mid-season, and a quantile model scored against its own era looks calibrated,
so the map came back as the identity while the model covered 25 to 32% of
outcomes below its own q10 on a fresh season.

It is measured on the **priced population**, taken from three seasons of odds
history rather than a snap-share proxy. The proxy excluded 43% of players who
actually receive a rushing line, because committee backs are priced constantly,
so the map was fitted on bell-cow backs and applied to everyone. rush_yds was
claiming 73.4% against an actual 52.1%.

Ships trained on all rows. The holdout exists to measure, not to cripple the
artifact, so the models are refit on everything before being saved.

### 5b. Probability calibration

`fit_probability_calibrator.py` fits isotonic regression on graded results and
`build_prop_edges.py` applies it before choosing a side.

This exists because the quantile map was not enough: the published probability
still ran 14 points high, and expected value is probability minus break-even, so
an inflated probability inflated EV by the same amount and "EV > 10%" was really
selecting bets at about -4% EV.

Fitted on **P(over)**, not on the recommended side's probability. The latter is
almost always above 0.5, so a curve fitted on it never sees the lower half of the
range, and pushing a 0.2 through it produced a board of 89 unders out of 93.

Isotonic rather than Platt because the error varies with the claimed probability
instead of being a constant shift.

### 5d. Markets I project but do not price

`any_td` is built and shown on the projections page and never appears on the
board.

The rate model is fine. Held out on 5,827 player-games it predicted a 0.190
chance of scoring against an actual 0.187, and its ordering matches the market:
Derrick Henry 1.10, Kyren Williams 1.00, Jahmyr Gibbs 0.99. What is not fine is
the price. Across the sixteen priced games the book's implied probabilities add
up to 5.54 scorers per game against the 4.04 that actually score, a 37%
overround, and no pick in this market has ever been graded because the snapshot
table holds no historical price for it.

At that margin the EV filter can only ever clear on longshots, and the
favourite-longshot bias puts most of the margin on exactly those prices. So it
selected backup running backs at +800 and called them the best value on the
board. The list is in `SUPPRESSED_MARKETS` in `build_prop_edges.py`, and it
comes off once a season of closing prices has been collected and graded.

### 5e. Injury reports have to be from this season

The injury lookup had no recency bound, so it took each player's most recent
report ever and applied it as current. In September that meant January.
Thirty-five players on the board carried a flag from the previous season,
thirteen of them "Out": Bo Nix on the ankle he broke in the playoffs, Jayden
Daniels on an elbow, Nico Collins on a concussion. Two were still carrying
"Questionable" from 2024. All of them were healthy and starting.

Nothing failed, because a stale flag is indistinguishable from a fresh one. The
query is bounded to the current season now, and a player with no report this
season carries no injury rather than repeating an old one. Teams do not publish
until the Wednesday of game week, so an empty result through the preseason is
the normal state. `audit_freshness.py` check 12 warns if that is still true once
games are inside a few days.

### 5c. Count markets do not use quantiles

`pass_td`, `rush_td`, `rec_td` and `any_td` derive P(over) and the median from a
Poisson built on the point model's rate.

Quantile regression on a 0-to-4 integer reproduces the population shape rather
than a particular player's. Stafford's last ten games were 3, 0, 3, 4, 2, 3, 2,
3, 2, 3 and the point model projected 2.61, which matches; the quantile CDF then
put P(under 1.5) at 0.53 and the board recommended the under. A Poisson on 2.61
puts it at 0.265.

That is a contradiction inside our own output rather than a question of which
method scores better: no distribution over non-negative integers has a mean of
2.61 and a median of 1.

Honest caveat: on graded picks the two are within noise, because the odds history
contains no pick above a 2.0 projected rate and cannot adjudicate the case that
prompted the change. The argument is internal consistency plus the fact that the
point model is the validated one.

### 5f. The median is anchored to the point projection where that helps

The point model and the quantile models are different families and they behave
differently at the edge of the data. Most markets use a linear point model
(`enet_v2` for rush_yds), which extrapolates. The ladder is a
`GradientBoostingRegressor` per level, and a tree cannot predict past the leaves
it was fitted on, so it saturates.

Jahmyr Gibbs before a Thursday game, a week after taking 29 carries while his
backups took two each:

| | |
|---|---|
| point projection | 91.0 |
| quantile median | 73.7 |
| ratio | 0.784 |
| median ratio among backs at that projection | 0.932 |

James Cook, projected 67.2 in the same game, had a quantile median of 69.0.
Twenty four yards apart on the point model, two yards apart on the ladder. The
board takes its side from the median, so the one back on the slate nobody else
resembled was being priced like a committee back, and the row published as a
strong best bet.

The answer is not to publish the mean instead. On 430 graded picks in exactly
this situation, a high projection with the two models straddling the line, the
median picked the winning side 53.0% of the time and the mean 47.0%. The side
rule is sound. The number feeding it was not.

So `fit_median_anchor.py` measures the median of actual/projection per market
and per projection band, and the published median is re-read as that fraction of
the point projection. That distribution is stable where it matters: for rush_yds
it runs 0.919, 0.940, 0.929 across the top three bands. Anchoring inherits the
point model's extrapolation and leaves the rest of the ladder alone, clamped
inside the row's own p25 and p75.

Two things it does not do. It never touches the count markets, whose ladder is a
Poisson already built around the point projection. And it never moves the median
without moving the probability: `p_over` is read off the ladder, so the anchored
median is written back into the ladder and the CDF re-read through it. The first
version corrected only the median and produced a row reading "median 83.9, line
88.5, edge -4.6" while still claiming the 57.4% win probability that belonged to
a median of 73.7.

A band ships only where the held-out coverage moves toward 0.50 or the side
accuracy improves, and neither gets worse. Two `rec_yds` bands and the top
`rush_yds` band qualify; everything else is left alone. The rush_yds band is
fitted on 197 rows and validated on 88, which is thin, and it is the one to
re-check first as the record grows.

With it applied, Gibbs reads 83.9 against a line of 88.5 and no longer clears
the expected-value bar, so the board does not publish him at all.

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

## The serving window used to be one game short

Feature rows are keyed by the game they predict. The row dated 2026-01-04 for
Travis Kelce holds the five games *before* that date, and its label is what he
did on 01-04. That is right for training: the window never sees the game it is
predicting.

It was wrong for serving. The newest row that existed for any player was the one
for his last played game, so when the board projected tonight's game it read a
window that stopped one game earlier than it should. Kelce's newest row meant
33.0, covering Nov 23 through Dec 25. His actual last five games through 01-04
average 26.4. The model was handed the older number.

At training the window is always adjacent to the game being predicted. At
serving there was always a one game gap, on every projection the site had ever
published. Measured on 17,872 rec_yds player-games:

| window | MAE | correlation with actual |
|---|---|---|
| adjacent (what training sees) | 17.502 | 0.5945 |
| one game back (what serving saw) | 17.821 | 0.5825 |

It also made the backtested track record optimistic, because the reconstruction
reads training rows, which carry adjacent windows, while live picks never did.

### What fixed it

`build_features` now emits one extra row per player for his next scheduled
game, with the window running through his last played game. Training is
untouched: `label_actual` is filled by `attach_labels` from a real stat line, an
unplayed game has none, and every query feeding training filters on
`label_actual IS NOT NULL`. Verified by row count, 18,563 labeled rec_yds rows
before and after, newest training date still 2026-09-13.

Three things had to come with it.

The row is only written for a game genuinely ahead of today, not merely ahead of
the player's last appearance. A player who has not played since Week 5 because
he is hurt has a next scheduled game in Week 6, which has already been played
without him. The first version wrote 714 rec_yds serving rows of which only 252
were for a game that had not happened, and feature rows are never deleted, so
that clutter would have been permanent.

`days_since_last_game` had to stop reading `as_of_game_date`. Both builders
measured staleness from it, which worked only because that date happened to be
the player's last game. On a serving row it is the upcoming game, so staleness
would have come out as zero for everyone on the board, silently switching off
`is_stale_window`, `stale_role_change` and the stale-role correction that
depends on them. `build_prop_edges.load_last_played` reads the real date from
the stat table, where a game that has not happened cannot appear.

`team` is now written at insert time instead of patched afterwards by
`db/backfills/fix_team_final.sql`. That backfill fills the column by joining the
stat line for the same player and date, and a serving row has no stat line, so
5,287 of them stayed NULL and the freshness audit failed on them.

Kelce's serving row now reads mean 26.40 against DEN with 253 days of
staleness, which is the number his last five games actually average.

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
