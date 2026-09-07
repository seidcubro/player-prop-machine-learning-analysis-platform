# Frontend

React + TypeScript + Vite in `apps/web`. See `apps/web/README.md` for running it
and `docs/DEPLOYMENT.md` for shipping it.

## Pages

All under one shell (`App.tsx`), which is a fixed left rail on desktop and a
bottom bar below 960px. The rail moved off the top because these are dense data
pages: a top bar costs about 70px of every screen forever, and a pill row does
not scale past four items.

| route | file | what it is |
|---|---|---|
| `/` | `EdgesDashboard.tsx` | Today's Signals. Sportsbook line against the model's number, ranked by expected value. |
| `/projections` | `Projections.tsx` | Every eligible player with a game this week, priced or not. This is the model's actual output. |
| `/record` | `TrackRecord.tsx` | How the picks have done, by season, with a calibration curve and leaderboards. |
| `/faq` | `Faq.tsx` | How it works, in plain English and for data scientists. |
| `/players`, `/players/:id` | `PlayersSearch.tsx`, `PlayerDetail.tsx` | Browse and profile. |

## Shared components

`Select` replaces every native `<select>`. The OS widget renders with its own
chrome, which on a dark panel is a white popup in the system font, and no CSS on
the element reaches the list it opens. This keeps the keyboard handling and ARIA
roles a native select gives for free.

`Pager` is the only pagination. Three pages had three of these and they
disagreed about word order, separators and whether the buttons had arrows.

`PageTitle` splits a heading into a plain lead and a gradient noun, the same
shape as the wordmark. Gradienting a whole heading competes with the logo
instead of echoing it.

`Glossary` defines EV, break-even and the rest above the board. `Avatar`,
`Logo`, `Sparkline`, `PropCards`, `WeekProjections` and `TrackRecord` (the
per-player one) are the rest.

`lib/markets.ts` owns market codes, their names and their order. Six files each
had their own copy and two of them had no entry for anytime touchdown, so those
views would have printed a raw `any_td` the day that market went live. It also
holds `isYesNo`, because anytime touchdown is priced as a single Yes and has no
median to compare against a line.

`lib/format.ts` owns every date, time and number. These were formatted inline in
six files with six different option objects, so the same kickoff rendered three
different ways. Times carry the zone, because a kickoff without one is ambiguous.

## Design system

Tokens live in `:root` in `src/index.css`. One rule matters more than the rest:

**Colour carries meaning, or it is not used.** Green means over or won. Red means
under or lost. Amber means the value pattern. Everything structural is blue,
violet or slate. An earlier pass painted surfaces, borders, brand, logo and tiers
all green, which meant green stopped saying anything and a winning row did not
register.

The brand ramp is cyan → blue → violet, matching the mark. There is no green in
the logo for the same reason: a logo should not borrow a semantic colour.

`scripts/build_brand.py` generates the whole asset pack (icons, favicon,
avatars, banners, lockups) from one definition, so they cannot drift apart.

## Things that will break if you are not careful

**Row heights.** The table pins rows at 84px. The identity cell stacks three
lines beside a 42px avatar and the tier column can carry three chips, so both
have fixed line-heights that add up to less than the row. Adding a fourth chip
or a fourth line will push rows out of alignment.

**Fixed table layout.** `.ps-table:has(colgroup)` is `table-layout: fixed` with a
declared `min-width`. Under fixed layout an `auto` column only gets what the
fixed ones leave, so an undeclared width collapses the column to nothing.

**The mobile table is not a table.** Below 960px the element, its body and its
colgroup all become blocks so the grid rows can fill the screen. Leaving it as
`display: table` sized it to a 97px intrinsic width and truncated every name.

**A missing market key is silent.** Nothing throws when a label is absent; the
column just prints a code, or a filter quietly omits a market. That is why the
map is in one file, and why `marketLabel` falls back to the raw code rather than
to an empty string. `services/training/odds_markets.py` is the same idea on the
other side, and `audit_freshness.py` check [9] fails the run if the two services
disagree about which markets exist.

**Deduplication is server-side.** The API returns one row per prop with the other
books in `alts`. Doing it in the browser made the counts, the pagination and the
rows describe different things.

## Accessibility

Maintain these when changing the UI:

- Text and background pairs meet WCAG AA.
- `:focus-visible` outlines everywhere. Restyle, never remove.
- Sortable headers are real buttons with `aria-sort`. The custom `Select` is a
  proper `listbox` with full keyboard control. Tables carry a visually hidden
  `<caption>` and the pager is `aria-live`.
- Decorative logo instances are `aria-hidden`.
- `prefers-reduced-motion` disables every transition and animation.
