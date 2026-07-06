
# Frontend (React)

Location: `apps/web`

## Current state

Two pages: `PlayersSearch.tsx` (search) and `PlayerDetail.tsx` (recent games + baseline/ML
projection). Both only call `projection_ml`/`projection_baseline`/`fetchPlayerGames` — there
is **no concept of a sportsbook line, an edge, or a recommendation anywhere in the frontend**.
This is the actual product goal (compare model projections to sportsbook lines) and it does
not exist in the UI yet. See `docs/API.md` "The gap: no prop_edges route" — that's the
blocker, not anything frontend-specific.

## Next step (not yet built)

Once a `GET /edges`-style route exists (see `docs/API.md`), add:
- an `Edges`/`Dashboard`-style page listing `prop_edges` rows, filterable by market and
  edge tier, showing line vs. projection vs. win probability vs. recommended side
- a corresponding `fetchPropEdges()` in `apps/web/src/api.ts`, following the existing
  `fetch*` function pattern (base URL resolution, `qs()` helper, typed response)

This deserves its own focused design pass (layout, filtering UX, styling direction) rather
than being bolted on ad hoc.

## Dev

```bash
cd apps/web
npm install
npm run dev
```

## Notes

If the UI appears out of date relative to backend capabilities:
- ensure API routes and client functions align
- confirm the API base URL and response shapes
- consider regenerating TypeScript types from OpenAPI (future improvement)
