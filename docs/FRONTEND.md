
# Frontend (React)

Location: `apps/web`

## Current state

Three routed pages under a shared shell (`App.tsx`: sticky navbar + `<Outlet/>`):

- **`EdgesDashboard.tsx` (`/`, the core screen)** — sportsbook line vs. model projection
  vs. edge vs. win probability, per row. Summary stat cards (`GET /edges/summary`),
  debounced player search, market/tier/side filters, sortable columns, pagination
  (`GET /edges`). Desktop renders a dense table; below 760px the same table collapses
  into stacked labeled cards via CSS only (`data-label` attributes).
- `PlayersSearch.tsx` (`/players`) and `PlayerDetail.tsx` (`/players/:id`) — the original
  player browse/projection pages, now living under the shared shell.

## Brand / design system

PropSignal is dark-mode-native ("trading terminal for props"). All theming lives in
`:root` custom properties in `src/index.css` — surfaces, text tiers, the signal palette
(green = value/over, red = avoid/under), and edge-tier badge colors. A future mobile app
or theme pass should only touch tokens, not components.

The logo is an inline-SVG recreation of the brand mark (`src/components/Logo.tsx`:
circle ring + "W" waveform + emitted beam) so it stays crisp at navbar/favicon sizes;
`public/favicon.svg` is the flat high-contrast tile variant. The raster brand PNGs
(from the branding session) are marketing assets and are not yet in the repo.

## Accessibility (maintain these when changing the UI)

- All text/background pairs meet WCAG AA; contrast ratios are annotated next to each
  token in `index.css`.
- `:focus-visible` outlines everywhere (restyle, never remove); sortable headers are
  real `<button>`s with `aria-sort`/`aria-label`; nav uses `aria-current`; the table has
  a visually-hidden `<caption>`; pager status is `aria-live`; decorative logo instances
  are `aria-hidden`.
- `prefers-reduced-motion` disables transitions.

## Dev

```bash
cd apps/web
npm install
npm run dev
```

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
