
# Frontend (React)

Location: `apps/web`

## Responsibilities

- player browse/search
- player detail pages with projection tabs
- call the API via `apps/web/src/api.ts`

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
