# Scripts Layout

## scripts/scheduled_update.sh

The scheduled refresh. Four modes, each a superset of the one above it, and the
only entry point anything automated should call.

| mode | what it does | credits | runtime |
|---|---|---|---|
| `--closing` | buys prices for a slate kicking off inside 90 minutes, rebuilds the board | 9 per game, nothing when no slate is near | under a minute |
| `--board` | injuries and depth charts, projections, board, audit | free | about 5 minutes |
| `--daily` | the above plus full ingest, features, grading, recalibration | free, or a full sync with `ODDS=1` | about 10 minutes |
| `--weekly` | the above plus retraining every market | free | about an hour |

Exits non-zero if `audit_freshness.py` fails, so a scheduler can alert on it.

### What the odds actually cost

Billing is per event per market. Nine live markets, so one game is nine credits
and a blanket eight-day pull of a sixteen-game week is 144.

`--closing` asks the schedule what is imminent instead of buying everything. A
90 minute window against an hourly run catches every kickoff 75 to 85 minutes
out, and it will not buy the same game twice: the query skips any event already
snapshotted in the last two hours, so the second run that still sees the game in
its window costs nothing.

Week 1 costs 9 + 9 + 72 + 36 + 9 + 9, which is 144 credits for a closing price
on every game of the week, the same as one unscoped sync.

Across the 272 game regular season that is **2,448 credits for a closing capture
on every game**, roughly 490 a month. The daily board refresh is the expensive
half at 144 a day.

Closing line value converges far faster than win and loss does, and a price not
captured before kickoff cannot be bought back at anything like the same cost.
That is why the cheap job is the one that matters.

### Guardrails

- Odds spending is opt-in per run: `ODDS=1`, or `--closing`, and nothing else.
- `ODDS_MIN_CREDITS` in `.env` is a floor. The client refuses a call once the
  balance drops below it rather than draining the account on a run nobody is
  watching.
- The season range is always the full history. Every nflverse loader truncates
  before writing, so a narrow range deletes the seasons outside it. The ingest
  refuses to do that unless `ALLOW_NARROW_RANGE=1`.
- `--weekly` retrains each market as the family already in `active_models`.
  `train.py` defaults to `rf_default` and writes to `active_models`, so a loop
  that does not pass a name would retrain everything as a random forest and
  repoint the platform at it.

## scripts/windows

`run_update.cmd` is the Task Scheduler entry point. It waits for Docker, loads
`.env`, runs one mode, and appends to `logs/update-YYYYMMDD.log`.

Credits are only spent when the second argument is the word `odds`:

```
schtasks /Create /TN "PriorLine Daily"   /TR "...\run_update.cmd --daily odds" /SC DAILY  /ST 08:00 /F
schtasks /Create /TN "PriorLine Closing" /TR "...\run_update.cmd --closing"    /SC HOURLY /ST 08:05 /F
schtasks /Create /TN "PriorLine Board PM" /TR "...\run_update.cmd --board"   /SC DAILY  /ST 17:00 /F
schtasks /Create /TN "PriorLine Weekly"  /TR "...\run_update.cmd --weekly"    /SC WEEKLY /D TUE /ST 01:00 /F
```

The weekly retrain takes about an hour: eleven markets, each a point model, an
evaluation and five quantile models, measured at 33 minutes for the training
itself plus the ingest and rebuild around it. It runs at one in the morning so
it is finished well before the eight o'clock refresh rather than competing with
it for the same containers.

Do not edit `scheduled_update.sh` while a run is in flight. `sh` reads a script
incrementally, so a change shifts the byte offset under the running interpreter
and it dies with a syntax error in a file that is perfectly valid.

Docker Desktop has to be running and the machine has to be awake. That is the
reason to move this to a host that is always on.

## scripts/weekly_update.sh, scripts/refresh_pipeline.sh

The earlier manual runners, kept because they are what the docs and my own
habits refer to. `scheduled_update.sh` is what the scheduler calls.

## scripts/dev

Developer convenience scripts for local work.

## scripts/pipeline

End-to-end pipeline runners and smoke-test flows (`run_market_pipeline.ps1`).

Note: one-off patch scripts that regex-rewrote source files during past debugging
sessions (`scripts/patches/`, root `patch_*.py`) were removed in the 2026-07-05 repo
cleanup once confirmed already-applied and unreferenced. If you need to make a
similar targeted fix in the future, prefer a normal editor change or a small,
reviewable diff over a standalone patch script.
