# Deployment

This is the reasoning. The commands are in `deploy/README.md`, and the files
they refer to are in `deploy/`: a production compose file, a Caddyfile, systemd
timers, and a database migration script.

## What goes where

Three pieces, and they don't all belong in the same place.

| piece | where | why |
|---|---|---|
| React frontend | Vercel | Static build. Free tier is fine, custom domain included. |
| FastAPI + Postgres | a small VPS or Fly/Render | Needs a persistent database. Vercel's serverless model is wrong for this. |
| Training pipeline | my machine, or the same VPS on a cron | Runs weekly, not on request. Nothing user-facing depends on it being up. |

Don't try to put the API on Vercel. It needs Postgres with about 400k feature
rows, it loads joblib model artifacts off disk, and the weekly retrain takes
tens of minutes. That's a long-lived process with a disk, not a lambda.

## Before anything is public

**Set `ADMIN_TOKEN`.** Four POST endpoints rewrite the database or spend Odds
API credits:

```
POST /api/v1/jobs/build_features
POST /api/v1/jobs/attach_labels
POST /api/v1/odds/sync/events
POST /api/v1/odds/sync/player_props
```

`sync/player_props` bills per event per market. An unscoped call is over 2,000
credits. With no token set these return 503, which is deliberate: an
unconfigured deploy should refuse to spend money rather than let the internet
spend it. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The pipeline scripts read `ADMIN_TOKEN` from the environment and send it as
`X-Admin-Token`. They fail immediately if it isn't set.

**Set `WEB_ORIGINS`** to the deployed frontend's origin, or every browser call
is blocked by CORS and the site comes up empty with nothing useful in the server
logs. `localhost:5173` is always allowed for local work.

**Check `.env` is not in the repo.** It holds the Odds API key.

```bash
git check-ignore -v .env
```

## Vercel

Frontend only. In the Vercel project settings:

- Root directory: `apps/web`
- Framework preset: Vite (auto-detected)
- Environment variable: `VITE_API_BASE=https://api.priorline.io/api/v1`

`VITE_API_BASE` is read at build time, not runtime, so changing it needs a
redeploy.

`apps/web/vercel.json` handles the rest. The rewrite rule matters: React Router
does client-side routing, so a hard refresh on `/projections` asks Vercel for a
file that doesn't exist. The rewrite sends everything that isn't a built asset
to `index.html` and lets the router sort it out.

For preview deployments, each one gets its own subdomain, so add
`WEB_ORIGIN_REGEX` on the API rather than listing them by hand:

```
WEB_ORIGIN_REGEX=https://priorline-.*\.vercel\.app
```

## Custom domain

Point the apex and `www` at Vercel for the frontend. Put the API on a subdomain
(`api.priorline.io`) with its own TLS, then set `VITE_API_BASE` to it and add
it to `WEB_ORIGINS`. Keeping them on separate hostnames means the API can move
later without touching the frontend.

## Keeping it current

`scripts/scheduled_update.sh` is the whole in-season loop, and the systemd
timers in `deploy/systemd` run it:

| timer | mode | when | credits |
|---|---|---|---|
| closing | `--closing` | hourly | 9 per game, only when a slate is inside 90 minutes |
| early | `--early` | Friday 09:00 | 9 per unpriced game inside the window |
| daily | `--daily` | 08:00 | about 145 |
| board | `--board` | 17:00 | free |
| weekly | `--weekly` | Tuesday 01:00 | free |

`early` is the one that is easy to leave switched off, and it is the reason
closing line value has been measuring nothing. CLV is the difference between the
price a pick was taken at and the price it closed at, and 90% of props in the
database have exactly one snapshot against them, so open and close are the same
row by construction and every pick scores zero. Buying on Friday and capturing
hourly through kickoff is what produces two ends to measure between. Enable it
with the rest of them.

Every timer sets `Persistent=true`, which is the part that matters. A run the
machine was down for happens when it comes back instead of being skipped
silently. Windows Task Scheduler defaults to the opposite, which is why the
laptop captured no closing lines at all for Week 1: it slept through both
mornings and nothing retried.

The closing capture matters more than it looks. A price not captured before
kickoff is gone unless I pay the archive rate for it later, and closing line
value converges far faster than win and loss does.

Both scripts end with `audit_freshness.py`, which exits non-zero if anything is
stale or inconsistent. Don't ignore it. It exists because the dashboard once
published 260 edges on games from the 2023 season and nothing noticed.

## What the site claims, and what it does not

This section used to say hold the edges page back, because in Week 1 the win
probabilities read about ten points high on the under side and no calibration
can anticipate a season nobody has played yet. That gap is still there and still
visible: the live 2026 picks claim 58% and have hit 48%.

What changed is what the site does about it. Only the elite tier is published,
which is the one tier that has made money in every season it has run, and the
claimed figure sits beside the actual one on the track record and on the landing
page rather than being quietly omitted. A reader is told the model is wrong
often, by how much, and which tiers lost money.

So it ships. The thing to keep watching is the weekly retrain pulling current
games into the calibration window, which is what closes the gap, around Week 4
or 5. If the published tier stops beating its break-even, the honest move is to
publish nothing rather than to widen the filter until something qualifies.
