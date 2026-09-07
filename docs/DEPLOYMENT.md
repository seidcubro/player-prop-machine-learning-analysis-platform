# Deployment

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
- Environment variable: `VITE_API_BASE=https://your-api-host/api/v1`

`VITE_API_BASE` is read at build time, not runtime, so changing it needs a
redeploy.

`apps/web/vercel.json` handles the rest. The rewrite rule matters: React Router
does client-side routing, so a hard refresh on `/projections` asks Vercel for a
file that doesn't exist. The rewrite sends everything that isn't a built asset
to `index.html` and lets the router sort it out.

For preview deployments, each one gets its own subdomain, so add
`WEB_ORIGIN_REGEX` on the API rather than listing them by hand:

```
WEB_ORIGIN_REGEX=https://.*-yourname\.vercel\.app
```

## Custom domain

Point the apex and `www` at Vercel for the frontend. Put the API on a subdomain
(`api.yourdomain.com`) with its own TLS, then set `VITE_API_BASE` to it and add
it to `WEB_ORIGINS`. Keeping them on separate hostnames means the API can move
later without touching the frontend.

## Keeping it current

`scripts/weekly_update.sh` is the whole in-season loop. Twice a week:

```bash
sh scripts/weekly_update.sh                # Tuesday: grade, refresh, retrain
sh scripts/weekly_update.sh --close-only   # Sunday: capture closing lines
```

The Sunday run matters more than it looks. A closing line I don't capture is
gone unless I pay the archive rate for it later.

Both scripts end with `audit_freshness.py`, which exits non-zero if anything is
stale or inconsistent. Don't ignore it. It exists because the dashboard once
published 260 edges on games from the 2023 season and nothing noticed.

## What not to deploy yet

The edges page. As of Week 1 2026 the projections are sound but the win
probabilities read about 10 points high on the under side, because no calibration
can anticipate a season that hasn't been played. It sorts itself out once the
weekly retrain has current games in its window, around Week 4 or 5.

Ship the projections. Hold the edges until the numbers earn it.
