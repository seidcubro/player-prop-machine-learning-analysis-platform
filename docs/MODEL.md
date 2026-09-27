# Inside the Model

The long version of how PriorLine works: where the data comes from, how a
projection is built, how it becomes a probability, how I keep that probability
honest, and how I test all of it. The ideas that failed are in here too, because
most of what shaped this project was deciding what not to ship.

The same material is on the site at [priorline.io/model](https://priorline.io/model),
where the track record loads live. Figures below are as of September 2026.

- [The short version](#the-short-version)
- [Vocabulary](#vocabulary)
- [The pipeline](#the-pipeline)
- [Data](#data)
- [Features](#features)
- [Models](#models)
- [From projection to probability](#from-projection-to-probability)
- [Calibration](#calibration)
- [Edge, EV and tiers](#edge-ev-and-tiers)
- [A worked example](#a-worked-example)
- [How I validate it](#how-i-validate-it)
- [What the research found](#what-the-research-found)
- [The record](#the-record)
- [Infrastructure](#infrastructure)
- [Lessons from the bugs](#lessons-from-the-bugs)
- [Limitations](#limitations)

## The short version

PriorLine predicts how many yards, catches, carries and touchdowns each NFL
player will produce, compares that to the number a sportsbook has set, and flags
the rare cases where the gap is big enough to beat the sportsbook's margin.

A book posts a line like "over or under 3.5 receptions". The model estimates the
full range of outcomes for that player, turns it into a probability for each
side, and subtracts the probability the price needs just to break even. What's
left is expected value. Four tiers are cut on it, every pick is graded against
the real box score, and only the tier that has made money in every season is
offered as a bet.

**The main finding: the edge is structural, not predictive.** The sportsbook's
line predicts player outcomes slightly better than my model does. The money
comes from a known pricing bias. The public bets stars to go over, books shade
those lines up, and yardage is right-skewed so the typical game lands below the
average. Disciplined unders on inflated lines exploit that. The model's real job
is finding where the inflation is biggest.

## Vocabulary

| Term | Meaning |
|---|---|
| Player prop | A bet on one player's stat rather than the game result. |
| Line | The threshold the book sets, usually a half number like 3.5 so there's no tie. |
| American odds | −110 means risk 110 to win 100. +122 means risk 100 to win 122. |
| Break-even | The win rate a price needs to lose nothing over time. −110 needs 52.4%, +122 needs 45.0%. |
| Vig | The book's margin. Both sides' implied probabilities add up to more than 100%. |
| Unit | A standard stake, so results compare across bet sizes. |
| Expected value | The model's probability that a side wins, minus the break-even for its price. |
| ROI | Average profit per unit staked. +4% means 100 staked came back as 104. |
| Closing line value | Whether the price I took beat the final price before kickoff. It separates skill from luck much faster than win and loss. |

```
break-even at −X   =  X / (X + 100)
break-even at +X   =  100 / (X + 100)
expected value     =  P(side wins) − break-even
```

## The pipeline

```
nflverse ──daily──> Ingest -> Features -> Train -> Calibrate -> Price & tier -> Serve
                                                     ^              ^             |
                                The Odds API ────────┼──near kickoff┘             |
                                                     └── picks graded, refit ─────┘

                  freshness audit: 17 checks on every refresh
```

Six stages on a schedule, with one loop that matters more than any single
model: once games finish, picks get graded and the calibration layer is refit on
them. That loop is why the system gets better as a season goes on.

| Layer | Technology |
|---|---|
| Database | PostgreSQL |
| Ingestion, features, API | Python, FastAPI |
| Models, calibration, pricing, grading | scikit-learn, pandas, SciPy |
| Frontend | React, TypeScript, Vite, on Vercel |
| HTTPS | Caddy with automatic certificates |
| Scheduling | systemd timers sharing one lock |
| Packaging | Docker Compose |

## Data

- **nflverse**: weekly player stats, play-by-play, rosters, depth charts, snap
  counts, injury reports, schedules with Vegas spreads and totals, weather and
  venue, and `ff_opportunity`, nflverse's own model of expected yards and
  touchdowns from where each touch happened.
- **The Odds API**: prop lines and prices from DraftKings, FanDuel and BetMGM.
  It bills per game per market, about 9 credits a game, which is why I buy
  prices near kickoff instead of all week.

| Table | Rows | Holds |
|---|---:|---|
| everything | ~1,264,000 | all live tables |
| `player_market_features` | 162,930 | one engineered training row per player, market and game |
| `player_projection_history` | 139,050 | every projection the site has published |
| `player_game_stats_app` | 76,908 | box scores, used as labels and for grading |
| `odds_snapshots` | 71,473 | append-only price history, the basis for CLV |
| `players` | 25,079 | player directory |
| `prop_edge_results` | 7,145 | every graded pick |

The odds history is the one thing I can't rebuild. Stats and features come back
from nflverse in about twenty minutes. A price I didn't capture before kickoff
is gone unless I pay the archive rate, which is roughly six times the cost of
capturing it live.

## Features

Each training row describes one player before one game, using only what was
known before that game. The lookback is the player's previous five games **of
the current season**, which gives 60 to 80 features per market.

| Family | Examples |
|---|---|
| Recent production | mean, weighted mean, median, trimmed mean, min, max, std dev, trend, season to date |
| Role | snap share, depth chart rank and its change, carry share, target share, teammates injured at the position |
| Play context | red zone targets and carries, third down targets, air yards, YAC, EPA per play, expected touchdowns |
| Opponent | what this defense has recently allowed to this position group specifically |
| Game and venue | spread, total, implied team total, temperature, wind, roof, surface, home or away, rest, injury status |

### The window stops at the start of the season

It used to stop only at the start of a career. For any player with fewer than
five games this season the window reached into last December and weighted it
equally with the games that describe who he is now, and last December is the
worst available description of a player: Week 18 is a bye for half the league's
starters, a playoff rotation is not a regular-season rotation, and a rookie's
December is not the role he was handed in September.

The Week 3 2026 board is what that produced. 92 published picks, 87 of them
unders, and on every player whose role had improved the projection landed between
his two seasons:

| player | this season | last December | projected | line |
|---|---|---|---|---|
| TreVeyon Henderson | 76 rush yds | 16.3 | 43.8 | 41.5 |
| Dontayvion Wicks | 73.5 rec yds | 8.3 | 35.6 | 44.5 |
| Rashod Bateman | 44 rec yds | 10 | 33.9 | 43.5 |
| Bhayshul Tuten | 14 carries | 3.3 | 10.9 | 12.5 |
| Lamar Jackson | 37 rush yds | 14 | 25.8 | 38.5 |

Eight of the 92 had a window spanning a team the player had already left. Every
player whose role improved between seasons read low, and every one of those
became an under.

So the window is this season only, and so is everything derived from it: the
EWMA level, which at Week 3 had put about 56% of its weight on last season, and
the trailing touchdowns-over-expected window, which is now partitioned by season
as well as by player.

Measured properly, which took three attempts to get right. Built both ways and
compared on **identical rows** with the same family and the same split
(`research_window_generations.py`), because the row population changes when the
window does and the first comparison I ran was between two different populations:

| market | crossing | season only | gain | on thin rows |
|---|---|---|---|---|
| rec_yds | 16.4154 | **16.2085** | **+1.3%** | +1.5% |
| recs | 1.2523 | **1.2411** | **+0.9%** | +2.0% |
| rush_yds | 9.8272 | **9.7413** | **+0.9%** | +0.2% |
| rush_att | 1.6185 | **1.6037** | **+0.9%** | +0.6% |
| pass_att | 7.6275 | 7.6179 | +0.1% | −0.6% |
| pass_yds | 65.4587 | 65.8284 | −0.6% | +1.6% |
| pass_completions | 5.1799 | 5.2328 | −1.0% | −4.5% |

Season-only wins on every volume market, and those are 74 of the 92 picks the
broken board published. Passing looks worse and is not measurably worse: 512 test
rows against four thousand, and on the rows this is for its three markets
disagree with each other. `WINDOW_CROSSING_MARKETS` moves a market back in one
variable when there is enough of a season to decide it.

Two earlier measurements that pointed the other way, kept because the reason they
were wrong is the useful part. Compared as plain averages of box scores rather
than through the model, the cross-season window is *closer* on five of seven
markets, and this season anchored to all of last season is better than both by 5
to 7%. Neither survived the model: `research_blend_feature.py` puts `y_blend` at
+0.6% on receptions and negative on two passing markets, on the same rows with
and without it. A signal can be real on its own and redundant beside the depth
chart, the Vegas total and the opponent, which is exactly how this question got
answered wrongly in September.

Three consequences, none of them free:

- **A player with no game this season gets no projection.** Not a low one, none.
- **Week 1 publishes nothing**, because in Week 1 nobody has played a game this
  season. The board fills as the games are played.
- **The floor is one prior game**, down from three, and early-season windows are
  genuinely thin. `y_season_n` rides on every row so the model can see how much
  is behind the window, and the same rule applies to training rows, so a model
  served a two-game window in Week 3 was fitted on the thousands of two-game
  windows that every past September produced rather than meeting its first one in
  production. That symmetry is the point: the previous design trained on
  five-game windows and served two-game ones, which is the same class of
  train/serve mismatch that caused the Hockenson overprojection.

The one thing still allowed to read a finished season is the shrinkage prior,
which pulls a thin window toward what the *position* did rather than toward what
this player used to be. That is a stabiliser, not a claim about his role.

### Held near this season at serving time

The window change is the fix and this is the guarantee. A projection is
defensible when you can say where it came from without apologising for it, and
"he is averaging 76 over two games, so 60" is defensible in a way that "he is
averaging 76 and we project 43.8" is not, whatever produced it. So the published
projection is shrunk toward the player's own current-season average while there
is little of this season to go on:

    published = w * model + (1 − w) * this season's average,   w = n / (n + 2)

Half and half at two games, and nothing at all from five games onward, because by
then the window is made of this season anyway and the model is entitled to its own
opinion. It needs neither a feature rebuild nor a retrain, which is the point: it
corrects the board that is live now. On the Week 3 2026 board it moved 16 of the
worst reads by an average of 5.1 and turned four of them from unders into overs.

The range is shifted by the same amount rather than scaled by the ratio, because
anchoring moves where a distribution sits and says nothing about how wide it is,
and it is applied before P(over) is read so the side and the median come from the
same distribution. The count markets rebuild both from the point and inherit it
that way.

Not backtested, and it changes the median that picks sides, which has cost money
here before ("Correcting the median to a true midpoint"). The exposure is bounded
by construction: inert from five games, so Weeks 2 to 5 only. `thin_sample.applied()`
prints how much it moved on every build, so once the retrain lands the correction
should shrink toward nothing, and if it does not, something upstream is still
wrong.

Two further rules I enforce rather than trust:

1. **Freshness at serving time.** Anything knowable before kickoff (opponent,
   depth chart, injuries, weather, the line) is read for the upcoming game, not
   off the player's last stored row. In September that row can be eight months
   old.
2. **Opponent features describe the actual opponent.** An early version averaged
   what opponents allowed over the player's previous games, which measured
   strength of schedule while being used as a matchup signal. One stored value
   read 118.8 where the real opponent was allowing 78.6.

## Models

Every market has a point model for the central estimate and a quantile ensemble
for the spread of outcomes. The family is picked by a bakeoff, not by me.

| Market | Active model | Family | Distribution |
|---|---|---|---|
| Rush attempts | `ridge_v2` | Ridge | quantile |
| Rushing yards | `enet_v2` | Elastic net | quantile |
| Receptions | `enet_v2` | Elastic net | quantile |
| Receiving yards | `rf_default` | Random forest | quantile |
| Passing yards | `hgb_v1` | Histogram gradient boosting | quantile |
| Pass attempts | `hgb_v1` | Histogram gradient boosting | quantile |
| Completions | `hgb_v1` | Histogram gradient boosting | quantile |
| Passing TDs | `pois_v4` | Poisson regression | Poisson |
| Rushing TDs | `rf_v10` | Random forest | Poisson |
| Receiving TDs | `rf_v10` | Random forest | Poisson |
| Anytime TD | `rf_v10` | Random forest | Poisson, projected but never priced |

How `bakeoff.py` picks a family:

1. Every candidate (ridge, elastic net, random forest, extra trees, gradient
   boosting, histogram gradient boosting, LightGBM, Poisson variants for counts)
   is scored on the same five expanding-window folds. Train on everything before
   a cut, test on the block after it, move the cut forward.
2. A challenger only replaces the current model if it wins by more than one
   standard error of the fold-to-fold difference. With ten candidates and one
   test split, one of them wins by luck.
3. Anything whose train score beats its test score by more than 0.35 R² gets
   flagged as memorizing. LightGBM was caught this way with gaps of 0.34 to 0.51
   and lost every market.

Linear models won three markets. Rushing volume is close to linear in carry
share and opponent form, so a forest just adds variance. I only found that
because I put ridge in as a sanity check.

There's also a hard ceiling. Splitting receiving yards into the variance between
players (learnable) and the variance game to game for the same player (noise),
the noise is 442.7 and the signal is 346.2. More than half of that market can't
be predicted from who the player is.

## From projection to probability

A projection alone can't be bet. Sixty yards with a range of 20 to 110 is a
different thing from sixty with a range of 45 to 75, so the model predicts a
distribution.

For yardage and volume markets, five gradient boosted models are trained with
pinball loss at the 10th, 25th, 50th, 75th and 90th percentiles. Together they
make a ladder for each player, and the line is placed on it to read off a
probability:

```
P(under) = interpolate(line, [q10, q25, q50, q75, q90] -> [.10, .25, .50, .75, .90])
P(over)  = 1 − P(under)
side     = over if the median is above the line, otherwise under
```

**Median, not mean.** Yardage is right-skewed, so the average sits above the
typical game. Comparing a line to the average invents over value on nearly
every prop. Switching to the median is what fixed my side selection.

**Touchdowns** are small whole numbers, and quantile regression on a 0 to 4
variable reproduces the population's shape instead of the player's. It once gave
a median of 1.6 next to a mean of 2.61 on the same row. Count markets use a
Poisson distribution whose mean is the point projection.

## Calibration

Calibration means a stated probability matches what actually happens. My first
version claimed 95% on some rushing props, and every confidence bucket was 14 to
29 points too high. Four correction layers now sit between the models and the
board. Each is fitted on graded results and each only ships if it improved a
held-out period.

| Layer | Corrects | How |
|---|---|---|
| `fit_probability_calibrator.py` | P(over) | Isotonic regression per market, pooled below 250 picks. The error changes shape with the claimed probability, and isotonic follows any shape while never reversing the order of two picks. |
| `fit_interval_calibrator.py` | the published range | Measures where outcomes really land inside the ladder and re-reads each level. Accepted level by level. |
| `fit_median_anchor.py` | the median | Re-reads the median as a fitted fraction of the point projection, clamped between q25 and q75. |
| `fit_spread_calibrator.py` | how widely predictions spread | Currently corrects nothing, which is right: no market beat its raw prediction out of sample. |

**This is where the money actually is.** EV is computed from the probability. If
the probability is 14 points high, every EV is 14 points high, and a rule like
"bet when EV is over 10%" was really picking bets at about −4%. The model
already ranked picks fine (AUC 0.547, clear of the 0.50 coin flip). The scale was
wrong. Isotonic calibration took the board from −0.5% to +0.6% and made the tiers
line up in order for the first time.

The gap hasn't fully closed and the site says so: across every graded pick the
model has claimed 62.5% and hit 52.7%.

A separate stale-role factor marks down players coming back in a smaller role.
I tested it per market with a placebo control, and only receiving yards survived,
with a factor of 0.732.

### What the board publishes

The probability on the board is not the model's own. The model ranks well and
claims too much: on 2025 it rated picks at 70% or better and they won 57%. So
the published figure comes from a logistic fit, per side, on graded picks:

    logit(P) = a + b * logit(model probability) + c * logit(price probability)

Fitting the price in alongside the model is what keeps expected value honest.
Plus money picks win less often than minus money ones by construction, so a
correction that ignores the price rated a +135 pick at 58% when its band only
reaches that across all prices. Predicted EV against realised return, by
quarter, on the 2025 holdout:

    confidence only    -5%/-3%   -0%/+4%   +3%/+4%   +10%/+3%
    with the price     -2%/-1%   -0%/+4%   +2%/+1%    +7%/+4%

The fitted weights say where the model is worth anything:

| side | weight on the model | weight on the price | log loss |
|---|---|---|---|
| under | +0.42 | +0.54 | 0.7233 to 0.6911 |
| over | −0.48 | +0.94 | 0.7108 to 0.6903 |

On unders the model's confidence predicts hits. On overs the weight is
negative: once the price is known, a more confident over is slightly less
likely to land. Overs are still published and still ranked, and the number
beside them is honest about what the model adds, which is nothing.

Tiers, best bets and value flags are still chosen on the model's own edge,
before this correction, because re-selecting them on the corrected probability
tested worse (+3.9% to +2.9%). The correction changes what the board says about
a pick, never which picks it makes. The model's own figure is kept as
`win_prob_model` so a refit never learns from its own output.

One pick never reaches the bet tier, whatever its edge: one this season's own
production contradicts. On two to four games of evidence, a projection that falls
on the opposite side of the line from the player's own current-season average is
shown and labelled rather than recommended. The upper bound is what stops this
second-guessing a full window: with four games or fewer the projection has least
to go on and a contradiction is most likely an error, while by Week 8 a
projection across the line is usually the opponent, the venue or the total doing
their job.

It was written for the season-boundary defect described under Features, and the
window change removed that cause at the source, so it should now fire on almost
nothing. That is why it stays. A projection on the far side of the line from the
player's own season means the features and the projection disagree about the same
player, and there is no longer an innocent explanation for it. The printed count
is the monitor: near zero says the window change is working, and a count that
climbs says something is reading a role the player does not have.

Unlike the tier cuts this was not validated on a backtest, and refusing picks has
failed that test before (`research_role_stability.py`). It ships on the narrower
argument that a pick whose inputs contradict each other is not worth recommending
whichever of them turns out to be right.

## Edge, EV and tiers

```
edge = model median − book line       (the sign picks the side)
EV   = calibrated P(side) − break-even for the price
```

### Tiers are cut on the size of the disagreement, not on the price

They used to be cut on EV, and that rule called a coin flip elite whenever the
coin flip paid well. It did: Baker Mayfield projected 1.5 passing touchdowns into
a 1.5 line at +150 arrived as an elite pick. Correct arithmetic, useless pick.

Ranked on graded picks, the disagreement sorts hit rate and EV does not:

| quintile | by gap (SDs) | by EV |
|---|---|---|
| 1 | 48.7% | 51.4% |
| 2 | 51.1% | 51.9% |
| 3 | 53.8% | 53.7% |
| 4 | 53.6% | 53.6% |
| 5 | **57.3%** | **52.7%** |

EV's top bucket wins *less* than its middle, because the biggest expected values
sit at the longest prices rather than on the likeliest outcomes. And 27% of the
picks that reached a bet tier under that rule sat within a quarter of a standard
deviation of the line, hitting 49.7%.

So the tier is cut on **|z|**, the gap between the median projection and the line
divided by the width of the predicted distribution, `(q75 − q25) / 1.349`.
Standard deviations rather than yards, because two yards of disagreement on a
15-yard line is a different claim from two yards on an 80-yard line.

The over bar is higher, and by a measured amount. Walk-forward over 2024-2025 on
every priced prop rather than only the ones published:

| \|z\| ≥ | unders | overs |
|---|---|---|
| 0.25 | 58.7%, +10.5% | 51.3%, −2.3% |
| 0.50 | **61.1%, +15.0%** | 52.9%, −0.4% |
| 0.75 | 59.7%, +12.8% | 55.3%, +2.4% |
| 1.00 | 57.1%, +8.7% | 57.6%, +5.7% |
| 1.50 | 58.4%, +9.9% | **68.4%, +23.3%** |

Overs are not hopeless; they were cheap at the wrong bar. Held to the under
threshold they became 70% of the board and returned nothing, dragging a +15%
under side down to +4.3% overall.

| Tier | Under needs | Over needs | On the site |
|---|---:|---:|---|
| Elite | \|z\| ≥ 1.0 | \|z\| ≥ 1.5 | offered as a bet |
| Strong | \|z\| ≥ 0.75 | \|z\| ≥ 1.0 | shown, not offered |
| Medium | \|z\| ≥ 0.5 | \|z\| ≥ 0.75 | shown, not offered |
| Small | \|z\| ≥ 0.25 | \|z\| ≥ 0.375 | graded, not shown |

**Receiving yards is held to |z| ≥ 0.75 on both sides.** It was the one market
that lost money, at 52.1% against a 52.7% break-even, and it is the largest by
pick count. The cause is not the model: every family calls the side at about
49.5%, a coin flip, and the ridge that beats the live random forest by 2.1% on
MAE moves side accuracy by nothing. What separates it is where the disagreement
starts paying:

| \|z\| | rec_yds | recs |
|---|---|---|
| 0.25-0.5 | 48.2%, **−4.4%** | 53.2%, +0.8% |
| 0.5-0.75 | 48.3%, **−4.3%** | 57.1%, +4.1% |
| 0.75-1.0 | 57.0%, +4.3% | 55.5%, +0.6% |
| 1.0+ | 55.3%, +2.5% | 63.0%, +7.1% |

Receptions pay from a quarter of a standard deviation; receiving yards lose
steadily until three quarters of one. That is the market rather than the fit:
yards are catches times yards per catch, and the second term has a tail a
five-game window cannot see. One broken tackle is forty yards. With the floor
applied, receiving yards goes from 52.1% and −1.0% to 56.2% and +6.7%, and every
market on the board is profitable:

| market | picks | hit | ROI |
|---|---|---|---|
| recs | 527 | 61.5% | +12.5% |
| rush_att | 242 | 57.9% | +9.3% |
| rec_yds | 322 | 56.2% | +6.7% |
| rush_yds | 153 | 54.9% | +4.7% |

One caveat on the scale itself: only 37-41% of outcomes land inside the predicted
middle-50% band, across every market, where 50% is correct. The quantile ladder
is too narrow everywhere, so every `z` is inflated by roughly the same factor.
The thresholds absorb it because they were measured in the same units, but a `z`
of 1.0 is nearer 0.8 true standard deviations and the number should not be read
as if it were exact.

Walk-forward on 7,835 priced props, the ladder ranks and the two unpublished
tiers lose money:

| tier | picks | hit | break-even | ROI |
|---|---|---|---|---|
| none | 3,627 | 49.3% | 52.2% | −5.2% |
| small | 2,931 | 51.0% | 52.7% | −2.9% |
| medium | 612 | 57.4% | 53.7% | +7.4% |
| strong | 367 | 56.9% | 54.0% | +5.9% |
| **elite** | 298 | **60.7%** | 53.9% | **+13.3%** |

EV is still computed and still published, and a pick the price does not cover is
labelled `negative_ev` rather than demoted: whether −190 is worth paying is the
bettor's question, not the ranking's.

Two guards. A collapsed predicted interquartile range makes the denominator
vanish and the gap explode, so a width floor and a ceiling of 6 SDs both apply.
And the cut points are read off the table above, which means they are fitted to
it: they are kept at round numbers, the 2024 holdout is 94 picks, the over elite
bar rests on 95, and receiving yards (52.1%) and November (52.2%) still lose.

`PUBLISHED_TIERS` and `BET_TIERS` are separate settings, so widening what the
board shows can never quietly widen what it recommends. `GAP_TIERS=0` restores
the EV ladder.

Anytime touchdown is projected but never priced. The market's own prices rank
scorers better than my model does (AUC 0.769 against 0.758), and betting the
model's claimed edges there returned −25% over 57 bets.

## A worked example

Illustrative numbers, not a real pick. A book offers a receiver at under 4.5
receptions for +110. The model's median is 3.9, below the line, so the side is
the under.

```
break-even at +110      = 100 / 210   = 47.6%
calibrated P(under)     =               54.0%
expected value          = 54.0 − 47.6 = +6.4%
tier (under, EV ≥ 6%)   = Elite
```

Two things change how much a pick like that deserves. If the median is below the
line but the mean is above it, the model barely has an opinion and the value is
mostly coming from the price. And volume stats like carries depend on game
script, which the model only sees through the Vegas spread and total.

## How I validate it

- **Forward only.** Models train on earlier seasons and get scored on a season
  they never saw. I rebuilt the historical record with models refit on earlier
  data only.
- **Out of sample, always.** Anything measured on data a model trained on
  flatters it. That's why the interval calibrator fits on walk-forward picks and
  not on the projection history, which was written by models refit on every row.
- **A noise rule.** A change is adopted only if it beats the current version by
  more than one standard error.
- **Placebo controls.** A proposed correction also gets applied to a randomized
  signal. If the placebo seems to work too, I don't trust the result.
- **Symmetric gates.** An early gate demanded better coverage but only tolerated
  side accuracy, and it wrongly rejected a band where coverage was flat and side
  accuracy rose 2.3 points.
- **Season by season.** A strategy has to be positive in each season on its own,
  not just in the pooled total.
- **Uncertainty by slate.** Picks on the same weekend share weather and game
  scripts, so I bootstrap by slate. Doing it per pick treated 6,000 bets as
  independent and overstated everything.

| Variant | Edge | 95% CI, by slate |
|---|---:|---:|
| star unders, best price | +3.3% | [+0.2%, +6.1%] |
| star unders, average price | +2.0% | [−1.0%, +4.8%] |
| 2023–24 star unders, best price | +7.4% | [−0.4%, +14.2%] |

The result I trust most is a dose response. Sorted by line size, the edge on
unders rises in a straight line, which data-mined patterns rarely do:

| Line size | Edge on unders |
|---|---:|
| largest quarter | +3.3% |
| largest half | +2.4% |
| smallest half | −1.3% |
| smallest quarter | −3.1% |

The bigger the name, the harder the public backs the over, the more the book
shades it, the more the under is worth.

**Power, and why the strong tier can't be rescued.** Strong hits 52.84% against a
52.92% break-even, 0.08 points short. The standard error at 933 picks is 1.64
points. Making it profitable needs about one standard error of improvement, so
any filter that seems to find it is fitting noise.

## What the research found

### The market predicts better than I do

At every level of disagreement, the book's line lands closer to the real outcome
than my median, and the gap grows with the disagreement. The comparison is made
inside each band, so it isn't a selection effect.

| Avg disagreement | Model error | Line error | Closer |
|---:|---:|---:|---|
| 0.3 | 1.64 | 1.64 | tie |
| 3.8 | 14.05 | 13.68 | book |
| 7.6 | 21.00 | 20.14 | book |
| 21.7 | 36.02 | 31.95 | book, by 4.1 |

That's what "structural" means. The profitable picks come from being right
about which way the book leans, not from out-predicting it.

### Tested and rejected

| Idea | What happened |
|---|---|
| Residual model, predicting the line's error | Residual R² negative in every market. |
| Line shopping at the same number | A better price existed on 78 of 7,145 picks. Worth +0.03%. |
| An EV band of 0.08 to 0.16 on overs | +17.7% on 55 picks, +10.7% on 88, then +3.3% on the only big season (524). |
| Strong overs | +6.3% overall, carried by seasons of 25 and 37 picks. |
| Filtering strong and medium by market | Best candidate was one season of 73 picks. |
| Star filter on strong and medium unders | Ran backwards: the biggest names went −7.3%. |
| Conviction, distance over interquartile range | No ordering across quintiles. |
| Primetime games | Worse: −15.9% and −12.5%. |
| Correcting the median to a true midpoint | Fixed a real calibration flaw and cut holdout ROI from +1.29% to +0.07%. The low median is the edge. |
| Straddle filter, price bands, vacated role, last-game form, book disagreement | All failed season by season. |
| Tracking data as features (Next Gen Stats, PFR advanced) | 22 features covering 95% of receiving props and 97% of passing ones. The elastic net gave every one a coefficient of exactly zero; a tree model landed in the same place. Receptions MAE 1.2083 to 1.2092. The existing target share, air yards and snap share already carry it. |
| Weighting this season's games more heavily in Weeks 2 to 5 | Beat a plain five game average by 6 to 10%, and made the real model worse: rush attempts −10.1%, receiving yards −0.4%. **Withdrawn, September 2026.** The test required six games of the prior season to enter the fit, so it was run without rookies, fringe players or anyone new to a team, which is the whole population the correction is for. See the season-boundary entry below. |
| The player's own position as a feature | Rushing yards had 81 features and not one said whether the man carrying the ball was a back or a quarterback: the only positional features described what the *opponent* allows. Genuinely absent, plausibly the reason a 37-yard-per-game rushing quarterback read 24. Added, and it moved him 0.2 of a yard. Bias by position was already −0.08 for quarterbacks and −0.06 for backs. Kept, because a model should be able to tell them apart and it costs nothing. |
| Keeping structural zeros out of the fit | Rushing yards trains on 9,491 wide-receiver rows averaging 1.0 yards against 5,919 back rows averaging 34.1, so half the fit is rows whose answer is zero. Training only on the positions a book prices is worth +0.8% on the two rushing markets and nothing on receiving, and it did not touch the compression it was meant to explain. |
| A career-length EWMA level beside a season-only window | Two questions, "what is he doing now" and "who is this player", and only the first should stop at the season boundary. Worth between −0.1% and +0.5% on identical rows, and it made the outliers it was built for worse: Jackson 24.0 → 23.2, Taylor 76.5 → 73.0, Henderson 59.2 → 55.8. |
| A 24% under-read of the top quintile | **Not a finding. My error.** Bucketing by the actual outcome puts the positive noise inside the bucket definition, so the top bucket is under-predicted for any honest model. Bucketed by the *prediction* instead, bias is −1% on rushing yards, −2.7% on receiving yards and −2.7% on receptions at the top. The model is well calibrated and three hypotheses were chased on the strength of an artifact. |
| `y_blend`, this season anchored to all of last season, as a feature | The best estimator of the next game I have measured: +6.1% on receptions and +4.8% on receiving yards against the old window, as a plain average, with the weight fitted on earlier seasons. Worth +0.6% through the model, and negative on two passing markets. The depth chart, the total and the opponent already carry it. Computed and stored anyway, because the serving-time anchor uses it and is measurably better for doing so. |
| A role stability score | Predicts a role change on unseen seasons (AUC 0.674) and does not pay. The picks it flags return +0.1% and the ones it keeps +0.5%. A role change explains a loss after the fact and breaks in our favour just as often. |
| Publishing only the slices that made money | Chosen on 2023 and 2024, applied to 2025: +0.9% against the full board's +1.0%. The slices were noise. |
| Blending the probability toward the price, pooled | Better average log loss, and wrong here: fitted on backfilled rows whose probabilities come from a different calibration path, it drove the weight on overs to zero and buried the strongest band on the board. Replaced by the per side calibration below. |

### What held

- Stopping the rolling window at the start of the season. On identical rows,
  same family, same split: receiving yards +1.3%, receptions +0.9%, rushing
  yards +0.9%, rush attempts +0.9%, and +1.5 to +2.0% on the early-season rows
  the change is for. The passing markets are within noise on a tenth of the
  sample and keep the same treatment. Both cheaper versions of this question
  gave the wrong answer: as plain averages the old window looks better, and as
  an extra feature the blended anchor is worth +0.6% at best.
- Picking sides off the median instead of the mean.
- Averaging four model families instead of picking one, on the rushing markets
  and receiving yards. Measured on rolling origins, three successive held-out
  slices each trained only on what came before: rush attempts +4.9%, rushing
  yards +4.1%, receiving yards +1.0%, positive in every slice. Receptions was
  flat and the quarterback markets were negative in every slice, so they keep
  their single family. A single 75/25 split had said the opposite, confidently,
  which is why the rolling test exists.
- Handing a ruled-out quarterback's workload to his backup. On 2025 the
  successor's share of pass attempts is predicted to within 0.033 against 0.343
  unchanged, and his passing yards to 59 against 83. Running backs and receivers
  were tested the same way and refused: carries improved the share and made
  rushing yards worse, and a missing receiver's targets spread too thinly for
  any method to beat leaving them alone.
- Correcting the published probability with the price as well as the model.
- Isotonic calibration, which made EV honest.
- Stricter cuts for overs than unders.
- Expected touchdowns as a feature. The feature query had been selecting it and
  throwing it away because touchdowns weren't one of the feature families. Rolled
  over each player's own recent games it improved anytime TD log loss from
  0.4268 to 0.4228 and receiving TD from 0.3535 to 0.3507, across three seeds,
  and an extra game of lag didn't weaken it.

### Touchdowns

I score them as classifiers with log loss and AUC. R² on a one-in-five yes/no
outcome is capped far below 1 no matter how good the model is, which is why
these markets used to look like they had headroom they don't.

| Model | Log loss | AUC |
|---|---:|---:|
| base rate | 0.4824 | 0.500 |
| hist gbm, shallow | 0.4265 | 0.739 |
| random forest | 0.4282 | 0.738 |
| Poisson to P(≥1), shipped | 0.4284 | 0.734 |
| logistic regression | 0.4289 | 0.738 |
| LightGBM | 0.4351 | 0.728 |

Every family landed within a hundredth of the same AUC and a plain logistic
regression tied the forest. The limit is the information, not the model.

### The simulator

`simulate.py` plays each matchup 20,000 times so every market comes from one
consistent outcome: team volume from the Vegas total and spread, touches split
with a Dirichlet, per-touch yards from a gamma, and a quarterback's passing yards
built from his receivers'. It reproduces real teammate correlations (QB with lead
receiver +0.51 simulated against +0.70 real, WR1 with WR2 about zero in both). It
isn't used for pricing because it hasn't beaten the quantile models on P(over).

## The record

As of September 2026. The current numbers are on the
[Track Record](https://priorline.io/record).

| Tier | Picks | Hit | Claimed | ROI |
|---|---:|---:|---:|---:|
| Elite | 3,952 | 53.6% | 66.4% | +4.1% |
| Strong | 933 | 52.8% | 59.6% | −0.2% |
| Medium | 1,033 | 50.4% | 58.0% | −5.2% |
| Small | 1,227 | 51.3% | 56.2% | −4.3% |

| Elite by season | Source | Picks | Hit | ROI |
|---|---|---:|---:|---:|
| 2023 | reconstructed | 445 | 53.5% | +3.5% |
| 2024 | reconstructed | 354 | 55.4% | +7.7% |
| 2025 | reconstructed | 3,051 | 53.5% | +3.9% |
| 2026 | published live | 102 | 52.0% | +1.1% |

Elite averaged about 90 picks per game day in 2025, so it's not a thin tier.
For context, betting every disagreement across the whole 2025 season lost 4.3%,
51.5% against a 53.9% break-even. Unders beat break-even in four of six markets
and overs lost everywhere. The published tier is the fix for that imbalance, not
a better predictor.

## Infrastructure

- The frontend is a static app on Vercel at `priorline.io`. The API, database and
  jobs run on one Hetzner server at `api.priorline.io`, because they need a
  persistent database, model files on disk and hour-long retrains.
- Postgres has no public port. The API only accepts browser requests from the
  site, and the four endpoints that rebuild data or buy odds need `ADMIN_TOKEN`.
- Odds spending is capped: prices are bought near kickoff, already-priced games
  are skipped, and every run stops before the account drops below
  `ODDS_MIN_CREDITS`.

| When (Eastern) | Timer | What it does |
|---|---|---|
| hourly at :05 | closing | if a game kicks off within 90 minutes, buy its prices and rebuild the board |
| daily 8:00 | daily | results, features, grading, calibration, prices for the next three days |
| daily 17:00 | board | injury reports, projections and the board, no credits |
| Fri 9:00 | early | price Sunday's slate so the board is live from Friday |
| Tue 1:00 | weekly | retrain every market after Monday night is graded |

Every run ends with `audit_freshness.py`, which exits non-zero on any of its
seventeen checks: every game-day feature is actually refreshed (tested by seeding
sentinel values), opponent features describe the real opponent, quantile bundles
share their point model's features, the board and projections agree, injury
reports are from this season, kickoff dates match the schedule, and every figure
the site states as fact matches the database. Deployment details are in
[`deploy/README.md`](../deploy/README.md).

## Lessons from the bugs

| What went wrong | What it taught me |
|---|---|
| Months tuning a receiving yards model that was actually broken by plumbing: row matching, a null team column, a broken eval script, a harmful log transform, no position filter. | Check the pipes before the model. |
| Every projection was served from a window one game older than training used (MAE 17.82 against 17.50). | Training and serving have to see the same inputs. |
| The check meant to catch traded players was silently dropping them, 94 players in all. | A safeguard can invert its own purpose. |
| `LAR` and `WSH` never matched nflverse's `LA` and `WAS`. | Normalize identifiers at the boundary. |
| Expected touchdown data was loaded and never read. | Dispatch logic can hide unused data. |
| Closing line value read zero for months because 90.6% of props had a single captured price. | Make sure a metric can move before trusting it. |
| On deploy night a restore reported success while the `players` table failed to load. | An exit code of 0 isn't verification. |

## Limitations

- The edge is small. Its confidence interval clears zero only at the best price.
- Calibration lags early in a season. No correction can anticipate a season
  nobody has played.
- No late information: game-time decisions, changed game plans, weather an hour
  before kickoff.
- Most of the record is reconstructed, and the live sample is still small.
- League production drifts down about 3% a year, so older training seasons read
  a little high.

PriorLine is a research tool, not a tip sheet and not financial advice. 21+
where legal. Not affiliated with any sportsbook.
