# PropSignal

I built this to answer one question before I bet: is this line wrong?

It pulls every NFL player prop the books are offering, projects what the player
will actually do, and shows me the gap. Where the gap is big enough, that's a
bet. At least that was the idea. Read the backtest section before you believe
any of it.

```
nflverse game data
  -> rolling and situational features per player per market
  -> one model per market
  -> quantile models for the outcome distribution
  -> sportsbook lines from The Odds API
  -> edge = projection vs line, win probability off the distribution
  -> API -> dashboard
```

Postgres, FastAPI, scikit-learn, React, Docker.

## Does it work?

Depends what you ask it to do. The head-on version, project every player and bet
wherever I disagree with the book, loses money. I trained on 2022 through 2024,
ran the whole 2025 season blind, and graded 6,736 picks against real lines:
51.5% hit rate against a 53.9% break-even, -4.3% ROI. Prop vig is heavier than
the -110 textbook number, and my biggest disagreements with the line were my
worst bets.

Then I stopped grading and started dissecting, and the loss wasn't symmetric.

**My under picks beat their break-even in four of six markets. My over picks
lost everywhere. And I was picking overs 60% of the time.**

Two causes, stacked:

1. **Books shade prop lines toward the over.** Blind unders hit above 50% raw in
   every market, and the shade grows with the size of the name: raw under hit
   rates on the top third of lines run 54-58%. Casual money bets stars to go
   over, and the books lean into it. This is the classic square-bet pattern and
   my data reproduces it cleanly.
2. **My models predicted the mean. A line sits at the median.** Yardage stats
   are right-skewed, so the mean is always above the median, and a
   mean-predicting model hallucinates over-value on nearly every prop. My real
   signal was being aimed at the structurally losing side.

The proof there was signal underneath: my unders beat *blind* unders by 2 to 5
points in every market. To say under, the model had to overcome its own upward
bias first.

So I rebuilt side selection and then spent as much effort trying to break the
result as I did finding it. `stress_test_edge.py` is that attack, and it changed
two of my conclusions.

The first thing it killed was the model's role. Blind star-unders hit 55.4%.
Model-filtered ones hit 55.8%. That 0.4 point difference is noise, so **the edge
is structural, not predictive**. My models aren't what makes this work. Saying
otherwise would have been the easiest thing in the world to get wrong, because I
wanted the model to matter.

The second was my significance claim. I originally computed standard errors per
pick, which assumes 6,000 independent bets. They aren't independent, picks on the
same slate share weather and game scripts. Bootstrapping by slate instead:

| variant | edge | 95% CI |
|---|---|---|
| star unders, best price | +3.3% | [+0.2%, +6.1%] |
| star unders, average price | +2.0% | [-1.0%, +4.8%] |
| 2023/24 star unders, best price | +7.4% | [-0.4%, +14.2%] |

It barely clears zero, and only when I take the best available price. At average
prices it isn't significant at all. The independent-season check crosses zero on
its own too, since it's only twelve slates. Line shopping isn't a bonus on top of
this strategy, it's half of it.

What survived is the mechanism, and it survived the test I trust most. Sorting by
line size gives a clean gradient:

| line tier | edge |
|---|---|
| top 25% | +3.3% |
| top 50% | +2.4% |
| bottom 50% | -1.3% |
| bottom 25% | -3.1% |

Data-mined patterns don't usually line up in a straight dose response like that.
The bigger the name, the harder the public backs the over, the more the book
shades it, the more the under is worth. Week to week, 13 of 20 slates were
positive with a median slate edge of +4.3%.

It also concentrates in counting stats, which makes sense: receptions +7.6%,
rush attempts +5.4%, rushing yards +2.2%, receiving yards +1.3%, and passing
yards **-10.0%**, which is why that market is excluded. People bet their star to
catch passes, not to fall short.

So the dashboard flags unders on top-quartile lines in those four markets. Not
because a model likes them, but because that's where the market's own bias is
measurable. It's a plausible small edge, not a proven one, and I'm tracking
closing line value going forward to find out which.

The general lesson cost me four failed experiments to learn: I couldn't
out-predict the market, and neither could a residual model given the market's
own number. The edge wasn't in better projections. It was in market structure,
picking the right side of a known public bias, filtering with a median instead
of a mean, and always taking the best price.

## Models

The family for each market is picked by `bakeoff.py`, not by me guessing.

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

### How the family gets picked

The obvious approach is to try a few models and keep whichever wins on a test
split. That's how you ship noise. With ten candidates and one split, one of them
wins by luck.

So every candidate gets scored on the same expanding-window folds. Train on
everything before a cut, test on the block after it, walk the cut forward. A
challenger only replaces the incumbent if it beats it by more than one standard
error of the fold-to-fold difference. Last run that rule took four changes and
rejected three where the gain was inside the noise.

Two results I didn't expect.

Linear models won three markets. Ridge beats every tree on rush attempts at
R² 0.747, and elasticnet wins rushing yards and receptions, both with basically
no gap between train and test. Rushing volume is close to linear in carry share
and opponent form, so a forest just adds variance. I only found this because I
threw ridge in as a sanity check, not expecting it to place.

LightGBM lost everywhere. Worse than a plain random forest, with train-minus-test
gaps of 0.34 to 0.51. It was memorizing. The bakeoff prints that gap for every
candidate and flags anything over 0.35, which is what caught it.

### How much room is left

I split each market's variance into the part that differs between players
(learnable) and the part that's game-to-game noise for the same player (not
learnable). The ratio gives the R² a model would get if it knew every player's
true average perfectly.

For receiving yards, the noise (442.7) is bigger than the signal (346.2). More
than half the variation in that market is unpredictable. Five markets score
*above* that ceiling, which means the context features are picking up
matchup-level information beyond player identity.

So the ceiling is a floor, not a cap. There's room left, but it's in
game-specific information: late injury news, weather, actual game plans. Most of
that is stuff I don't have, not a modeling technique I'm missing.

### Win probability

The number next to a projection matters more than the projection. It used to
come from assuming a bell curve with a hand-capped standard deviation, which
produced things like 95% confidence on a rushing prop. Grading old picks showed
every bucket was 14 to 29 points overconfident.

Now I fit quantiles (q10 through q90) per market and read P(over) off the
predicted distribution, so the spread is learned per player instead of assumed.
A boom-or-bust receiver gets a wide interval. A steady possession back gets a
narrow one.

| quoted | hit rate before | after |
|---|---|---|
| ~0.96 -> 0.90 | 0.667 | 1.000 (n=2) |
| ~0.83 | 0.583 | 0.800 |
| ~0.74 | 0.591 | 0.659 |
| ~0.57 | 0.434 | 0.528 |

## Monte Carlo

Every market used to be predicted on its own, so a quarterback's projected
passing yards had no arithmetic relationship to the receiving yards of the men
catching the ball. `simulate.py` runs 20,000 games per matchup and reads every
market off the same simulated outcomes.

Per team: team volume from the Vegas total and spread, Dirichlet shares split by
stick-breaking so touches sum to the team total, gamma per-touch yards, and the
QB's passing yards derived as the sum of his receivers' yards. Dispersion is
fitted from 2023+ logs, not guessed.

I checked it against reality instead of against my assumptions, which is good
because my assumption was wrong:

| | simulated | real |
|---|---|---|
| QB pass yds vs lead receiver | +0.510 | +0.697 |
| WR1 vs WR2 rec yds | -0.001 | +0.041 |

I expected teammates to correlate strongly negative since they compete for
targets. They don't. Shared team volume pushes them together and target
competition pushes them apart, and in real games those cancel almost exactly.
The simulator getting ~0 was correct. Don't "fix" it.

It's not wired into the served edges yet. It reproduces the correlation
structure but I haven't shown it beats the quantile models on P(over), and that
test needs graded results.

## Features

Per player, per market:

Rolling production over the last five games: mean, weighted mean, stddev, trend,
plus median, trimmed mean, min and max so one blowup game doesn't drag
everything, and a season-to-date baseline to revert toward.

Role: snap share, depth chart rank, how that rank has changed since the window,
days since last game, teammate injuries at the same position.

Play context from play-by-play: red zone targets and carries, third down usage,
shotgun rate, air yards, YAC, EPA per play, in-game win probability, pace.

Opponent defense by position group. What this defense actually gives up to
running backs specifically, not a team total. A team can look fine overall and
still be soft against receiving backs, and the team number hides exactly the
matchup the line is priced on.

Game and venue: spread, total, implied team total, temperature, wind, indoor or
not, turf or grass, home or away, rest days, divisional game.

## Staleness

This is what bit me hardest, so it gets a guard.

Anything known before kickoff (depth chart, injuries, the line, weather, the
opponent) used to be read off the player's most recent stored feature row. In
season that row is a week old and you'd never notice. In September it's eight
months old, and you get a back projected as the starter he was last January.

Houston traded for David Montgomery over the offseason. My own 2026 depth chart
had it right, Montgomery RB1 and Woody Marks RB2, but the edge builder was
reading Marks' January row and projecting him like the workhorse he used to be.

Three more turned up when I went looking:

Opponent features described the wrong teams. They were averaged over the lookback
window, and each window game's value is what *that* game's opponent allowed. So
the matchup signal measured the defenses a player had just faced, not the one he
was about to play. One stored feature read 118.8 where the actual opponent was
giving up 78.6.

Traded players were dropped silently. The sanity check compared the game's teams
against the player's team on his last feature row, which inverts the whole point.
A traded player, exactly what the check existed to catch, got thrown out because
his old team wasn't in the matchup. Montgomery had 12 props up for Houston and
produced zero edges. 94 players were affected.

Two team codes never matched. I had the Rams and Washington as `LAR` and `WSH`.
nflverse writes `LA` and `WAS`.

All of it is enforced now instead of trusted:

```bash
docker compose run --rm training python audit_freshness.py
```

It exits non-zero if any of it comes back. The part that matters is how it checks
whether a feature actually gets refreshed: it seeds every feature with a sentinel
value, runs the refresh against a real upcoming game, and sees what changed. My
first version read the source with a regex and was wrong both ways. It missed
features set through a loop variable and raised ten false alarms on ones that
were fine.

`scripts/refresh_pipeline.sh` runs the whole chain in dependency order and ends
with the audit.

## Running it

```bash
docker compose up -d postgres redis
docker compose build api training
docker compose up -d api
npm run dev --prefix apps/web
```

Full refresh in dependency order, ending with the audit:

```bash
sh scripts/refresh_pipeline.sh
```

Odds sync separately. Books only post player props two to four days before
kickoff, and The Odds API bills per event per market, so the pull is scoped to
the upcoming slate:

```bash
curl -X POST "http://localhost:8000/api/v1/odds/sync/events"
curl -X POST "http://localhost:8000/api/v1/odds/sync/player_props?days_ahead=8"
```

Model artifacts aren't in git. Regenerate them with the refresh script.

## In season

Two runs a week:

```bash
sh scripts/weekly_update.sh
```

Tuesday. Ingests last week's results, rebuilds features, retrains, rebuilds
edges, grades whatever has been played, and ends with the freshness audit.
Retraining weekly is on purpose. Every week adds real games and the record is
the whole point.

```bash
sh scripts/weekly_update.sh --close-only
```

Sunday, about an hour before the first kickoff. Captures the closing lines into
`odds_snapshots`.

The Sunday run matters more than it looks. A closing line I don't capture is gone
unless I pay the archive rate for it later, and the archive costs about 840
credits a slate against roughly 130 for capturing it live. Six times cheaper to
just take the snapshot.

Closing line value is why I bother. Win/loss over a few hundred bets is mostly
variance at a 53% break-even, but whether I consistently take numbers the market
later moves toward settles the question much faster.

```bash
docker compose run --rm training python eval_clv.py
```

## What I'd do next

Feed the line in as a feature and predict the residual instead of the raw stat.
Competing with the market head-on has failed three tests now. Modeling where the
line is wrong is a different and better-posed problem.

Stop betting the biggest disagreements. Three tests say that bucket loses.

Calibrate the displayed probability with isotonic regression against the graded
history, so the number shown is the number that happens.

Fix the interval coverage. The 80% quantile interval actually covers anywhere
from 69.8% to 94.6% depending on market. Conformal prediction gives coverage by
construction.

Automate the weekly refresh and track closing line value. Whether a pick beat the
closing number converges much faster than win-loss does.

## Layout

- `services/training/` training, evaluation, bakeoff, simulation, edge building,
  grading, backtest, freshness audit. Most of the work is here.
- `services/api/` FastAPI backend.
- `jobs/ingestion/` the nflverse pipeline.
- `apps/web/` React frontend.
- `db/` schema, migrations, backfills.
- `docs/` architecture and pipeline notes.

## Author

Seid Cubro. [LinkedIn](https://www.linkedin.com/in/seid-cubro),
[seidcubro.vercel.app](https://seidcubro.vercel.app)
