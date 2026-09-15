# Putting this on the internet

Three pieces in three places, for three different reasons.

| piece | where | cost |
|---|---|---|
| React frontend | Vercel | free |
| API, Postgres, scheduled jobs | one small server | about $4 to $6 a month |
| Domain | any registrar | about $12 a year |

Roughly $60 a year, most of it the domain.

## Why not all on one managed platform

The weekly retrain trains eleven markets, each a point model and five quantile
models, and takes about an hour of real CPU. On a serverless platform that is
either impossible or the most expensive thing in the account. On a $4 server it
is free, because the server is already paid for and idle at one in the morning.

Everything else follows from that. Once there is a box, Postgres and the API may
as well live on it, and the only piece that genuinely benefits from a CDN is the
static frontend, which is what Vercel is for.

## The server

Anything with 2 vCPU and 4GB will do. Hetzner CX22 is about 4 euro a month,
DigitalOcean and Vultr are around 12 dollars for the same shape.

4GB is not arbitrary, and neither is the swap file below.

The measured numbers, rather than guesses: the heaviest model retrain peaks at
166MB, which is nothing. The daily ingest peaks at about 3.6GB, because it holds
several seasons of play-by-play while it aggregates them. That used to be 4.2GB
and would not fit at all; projecting the columns each step actually reads
brought it down, with the output verified identical row for row.

3.6GB against 4GB of RAM has no headroom once the operating system, Docker and
Postgres are counted. Swap covers the gap. A once-a-day batch job touching swap
for a minute is fine; the same job being killed by the out-of-memory reaper at
eight in the morning is not.

```bash
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Batch work, not a latency-sensitive service. Let it swap rather than die.
sysctl -w vm.swappiness=10
echo 'vm.swappiness=10' >> /etc/sysctl.conf
```

```bash
# on the server, as root
adduser --disabled-password --gecos "" priorline
usermod -aG docker priorline
mkdir -p /opt/priorline /etc/priorline /var/log/priorline
chown -R priorline:priorline /opt/priorline /var/log/priorline

# the code
git clone <your repo> /opt/priorline
cd /opt/priorline
```

## Secrets

`/etc/priorline/env`, readable only by root and the service user:

```
POSTGRES_PASSWORD=<long random string>
ADMIN_TOKEN=<python -c "import secrets; print(secrets.token_urlsafe(32))">
ODDS_API_KEY=<from the-odds-api.com>
ODDS_MIN_CREDITS=500
API_DOMAIN=api.priorline.io
ACME_EMAIL=<your email, for certificate expiry notices>
WEB_ORIGINS=https://priorline.io,https://www.priorline.io
WEB_ORIGIN_REGEX=https://priorline-.*\.vercel\.app
API_DOCS=on
```

`API_DOCS=off` serves neither the interactive docs nor the schema. On is a
reasonable default here, since the schema is the clearest description of what
the API does, but it does advertise the write endpoints, including the one that
bills the Odds quota. They are behind the shared secret either way.

```bash
chmod 640 /etc/priorline/env
chown root:priorline /etc/priorline/env
```

Only the daily run is allowed to spend credits:

```bash
echo "ODDS=1" > /etc/priorline/daily.env
```

No file for the other modes, so they cannot spend anything even if the schedule
changes. `--closing` is the exception by design: it buys the slate about to
kick off and nothing else.

## Bring it up

```bash
cd /opt/priorline
set -a; . /etc/priorline/env; set +a
docker compose -f deploy/docker-compose.prod.yml up -d --build
COMPOSE="docker compose -f deploy/docker-compose.prod.yml" sh deploy/apply_migrations.sh
```

Order matters. `apply_migrations.sh` runs after the restore, never before it:
`db/init.sql` creates nine tables and the running system has forty, the rest
built by the ingest on first use. Against an empty schema the migrations have
nothing to alter and eight of them fail. The script checks for that and says so
rather than failing obscurely.

`apply_migrations.sh` is what carries schema changes to the server. On a
database restored from a dump it records every existing migration as applied
without running it, because the state is already there and the oldest files can
no longer execute against the current schema. After that it applies anything
new, in filename order, each file and its bookkeeping in one transaction.

Run it again after every `git pull` that adds a migration. It is safe to run
when there is nothing to do; it says so and stops.

Point `api.priorline.io` at the server's IP first. Caddy asks Let's Encrypt
for a certificate the first time somebody asks for that hostname, and renews it
on its own. There is no certbot and no renewal cron to forget about.

## Move the data

The odds history is the part that cannot be rebuilt. Game stats and features
regenerate from nflverse in twenty minutes; a closing price not captured before
kickoff is gone.

```bash
# on the laptop
sh deploy/migrate_db.sh dump
sh deploy/migrate_db.sh restore priorline@your.server.ip
```

About 55MB compressed. The script prints row counts afterwards so a half
restore is obvious rather than discovered a week later.

Model artifacts are 82MB and are not in git:

```bash
rsync -av services/training/artifacts/ priorline@your.server.ip:/opt/priorline/services/training/artifacts/
```

## The schedule

```bash
cp deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now priorline@closing.timer priorline@early.timer \
                       priorline@daily.timer priorline@board.timer \
                       priorline@weekly.timer
systemctl list-timers 'priorline@*'
```

`Persistent=true` on every timer, which is the thing Windows Task Scheduler was
missing: a run the machine was down for happens when it comes back rather than
being skipped in silence. That is why the laptop captured no closing lines for
Week 1.

Enable `early` along with the rest. It buys Friday morning, and it is the other
half of the closing capture: a pick with one price snapshot against it has the
same open and close by definition, which is why closing line value has been
reading zero across the board. Friday open plus hourly through kickoff gives it
two numbers to compare. It spends credits without needing an `ODDS=1` file,
because buying prices is the entire purpose of that mode, and it buys only the
games inside its window that have no prices yet.

## Frontend

Vercel, root directory `apps/web`, and two environment variables:

```
VITE_API_BASE=https://api.priorline.io/api/v1
VITE_SITE_URL=https://priorline.io
```

Both are read at build time, so changing either needs a redeploy, and the build
fails with the variable's name rather than shipping without it.
`VITE_SITE_URL` is the site's own origin and builds the Open Graph tags, which
a crawler fetches with no page context and therefore cannot resolve a relative
path against. `apps/web/vercel.json` already handles the SPA rewrite and the
asset caching.

## Check it worked

```bash
curl -s https://api.priorline.io/api/v1/edges/summary | head -c 200
docker compose -f deploy/docker-compose.prod.yml exec -T postgres \
  psql -U app -d app -c "SELECT count(*) FROM prop_edges;"
sudo -u priorline sh -c 'cd /opt/priorline && sh scripts/scheduled_update.sh --board'
```

The last one is the real test: it ends with `audit_freshness.py`, which exits
non-zero if anything is stale or inconsistent.

## What is deliberately not exposed

Postgres has no published port. Reach it through `docker compose exec` or an SSH
tunnel. An open 5432 is how these boxes get found.

The write endpoints answer 503 without `ADMIN_TOKEN` and 401 with the wrong one.
That matters more here than locally: `sync/player_props` bills per event per
market, so an open one is somebody else spending your money.

## Backups

The dump script is the backup. A weekly copy off the box is enough, because
everything except the odds history can be rebuilt from nflverse.

Cron runs with almost no environment, and every variable in the production
compose file is written ${VAR:?...}, so compose refuses to read the file at all
without them. Source the env file first, or the backup fails quietly every
Sunday and nobody finds out until they need it.

```bash
0 3 * * 0 set -a; . /etc/priorline/env; set +a; cd /opt/priorline && \
  docker compose -f deploy/docker-compose.prod.yml \
  exec -T postgres pg_dump -U app -d app -Fc -Z6 > /var/backups/priorline-$(date +\%F).dump
```

## Shipping a change

```bash
cd /opt/priorline
git pull
set -a; . /etc/priorline/env; set +a
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

`--build` is not optional. Neither the API nor the training image mounts source
from the repo; both bake it in at build time, so a pull followed by a plain
`up -d`, or a `restart`, leaves every container running the code it was built
with. The symptom is a fix that is plainly in the file and plainly not in the
behaviour, with no error anywhere. `scripts/scheduled_update.sh` rebuilds the
training image itself for that reason. Nothing rebuilds the API.
