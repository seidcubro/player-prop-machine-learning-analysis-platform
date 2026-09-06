# API

HTTP API from `services/api`. Everything below except `/health` is under
`/api/v1`. Local base URL is `http://localhost:8000`.

## Health

`GET /health` returns `{"status": "ok", "service": "api"}`.

## Edges

`GET /edges`

The main one. Returns computed edges, meaning sportsbook line against model
projection, with win probability, recommended side and tier.

| param | default | what it does |
|---|---|---|
| `market_code` | | filter to one market |
| `min_tier` | | small, medium, strong or elite |
| `side` | | over or under |
| `search` | | player name, case insensitive |
| `upcoming_only` | `true` | hide games that already kicked off |
| `sort` | `edge` | edge, win_prob, line, projection, commence_time, player_name |
| `order` | `desc` | asc or desc |
| `limit` | 50 | max 500 |
| `offset` | 0 | |

`upcoming_only` defaults to true because the dashboard is for deciding what to
bet, not browsing finished slates. Pass false to look at history.

Each row carries the kickoff date, the player's headshot and their internal id.
Player names aren't unique (there's a Josh Allen at QB for Buffalo and another at
center for Tampa Bay), so resolution prefers a player whose position can actually
produce that market's stat, then the one with more game history.

`GET /edges/summary`

Counts per market and per tier for the dashboard header, scoped to upcoming games
so it agrees with the table underneath it.

## Players

`GET /players`

| param | default | what it does |
|---|---|---|
| `search` | | name, team or position |
| `positions` | | comma separated whitelist, e.g. `QB,RB,WR,TE,FB` |
| `active_only` | `false` | only players with game activity in the last two seasons |
| `include_total` | `false` | include a total count |
| `limit` | 50 | max 500 |
| `offset` | 0 | |

The site passes `positions=QB,RB,WR,TE,FB` and `active_only=true`. The full table
holds 25,000 players going back decades, which is useless to browse.

`GET /players/{id}`

Full profile row: name, position, team, headshot, jersey, height, weight,
college, years of experience, status.

`GET /players/{id}/games?limit=10`

Recent game log.

`GET /players/{id}/edge_history?limit=50`

Graded track record. Every past pick with what the player actually did and
whether the recommended side won, plus a summary comparing realised hit rate
against the average win probability that was quoted.

Pushes (result landed exactly on the line) are stored with `hit = null` and left
out of the hit rate, same as a sportsbook settles them.

Deduplicated to one row per game and market. Every book prices the same prop, so
an ungrouped list would show the identical pick three times and inflate the
sample.

## Feature jobs

`POST /jobs/build_features?market_code=rec_yds&lookback=5`

Builds rolling and contextual features for every eligible player and game into
`player_market_features`.

`POST /jobs/attach_labels?market_code=rec_yds&lookback=5`

Fills `label_actual` once results are known.

## Odds sync

`POST /odds/sync/events`

Pulls the event list. One credit.

`POST /odds/sync/player_props?days_ahead=8&limit=`

Pulls player props for games kicking off in the next `days_ahead` days.

Scoped on purpose. The Odds API bills per event per market, so an unbounded loop
over a full 272-game season is thousands of credits for lines that mostly aren't
posted yet. Books put player props up two to four days before kickoff.

Idempotent. There's a unique index on
`(provider_event_id, bookmaker_key, market_key, player_name, outcome_name)` and
the write is an upsert, so re-syncing refreshes lines instead of duplicating them.

## Removed

`/players/{id}/projection_ml`, `/projection_history` and `/projection_baseline`
are gone. Two of them defaulted to model names that no longer exist and returned
404 on every call. The third built its own projection with no freshness
correction, so it disagreed with the edges the site actually served. Nothing in
the frontend used any of them.

`/odds/sync/historical_events` and `/odds/sync/historical_player_props` are gone
too. Both started with `DELETE FROM odds_events`, so pulling a historical slate
wiped the upcoming one. Use
`services/training/backfill_historical_odds.py`, which writes to the append-only
`odds_snapshots` table and prices a pull before spending anything.
