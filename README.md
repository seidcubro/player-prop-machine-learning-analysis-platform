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

No. Not yet, and I'd rather say that here than bury it.

I trained a model on 2022 through 2024, ran it on the entire 2025 season, and
graded every pick against the real lines I pulled from the archive. 6,736 picks:

| | |
|---|---|
| hit rate | 51.5% |
| break-even at the price offered | 53.9% |
| edge | -2.4% |
| ROI | -4.3% |
| units | -292 |

Every market came out negative or flat. rush_yds +0.2%, pass_td -0.1%,
rush_att -1.5%, recs -2.5%, rec_yds -3.6%, pass_yds -5.7%.

Three things I learned from that:

**Prop vig is heavier than people assume.** Everyone quotes 52.4% as break-even
because that's what -110 implies. The real number across these props was 53.9%.
Any claim of edge has to clear that, not the textbook figure.

**My biggest disagreements with the line are my worst bets.** Sorted by how far
my projection sits from the line, the widest bucket hit 50.0% against a 53.1%
break-even. That's backwards from how the dashboard tiers picks, which ranks
"elite" by disagreement size. Three separate tests found the same thing.

**Line shopping beats modeling.** Taking the best price across books instead of
the average is worth +2.6% per unit staked. That's bigger than any model
improvement I got out of this project, and the feature was already built.

The model isn't useless. It beats a rolling five-game average, which is the
baseline I set for it, and it lands closer to the result than the line does on
46.5% of props (up from 43.8% when I was using the raw average). It just doesn't
clear the vig.

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
