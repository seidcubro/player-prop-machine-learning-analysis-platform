# PropSignal web

React + TypeScript + Vite. This is the frontend only. The projections, edges and
player data all come from the FastAPI service in `services/api`.

## Running it

The API has to be up first:

```bash
docker compose up -d api postgres
```

Then:

```bash
npm install
npm run dev
```

Vite proxies `/api` to `http://localhost:8000` in dev, so nothing needs
configuring locally.

## Pages

| route | what it shows |
|---|---|
| `/` | Edges. Props where the model and the book disagree, ranked by expected value against the offered price. |
| `/projections` | Every skill player and QB with a game this week, whether or not a book has posted a line. This is the actual output of the model. |
| `/players` | Search. |
| `/players/:id` | One player's history, form and projections. |

## Talking to a deployed API

`VITE_API_BASE` overrides the default. It's read at build time, not runtime, so
changing it needs a rebuild:

```bash
VITE_API_BASE=https://api.example.com/api/v1 npm run build
```

The API also has to allow the browser origin, or every request is blocked by
CORS and the page comes up empty. See `WEB_ORIGINS` in `docs/DEPLOYMENT.md`.

## Conventions

All HTTP goes through `src/api.ts`. Don't build URLs in components: the base URL,
query strings and error handling live in one place so there's one thing to change
when the API moves.
