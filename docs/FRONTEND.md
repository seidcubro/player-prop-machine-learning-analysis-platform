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
| `/` | `Landing.tsx` | What the product is, with the tier ledger read live from the record. |
| `/signals` | `EdgesDashboard.tsx` | This week's board. Sportsbook line against the model's number, tiered by rank within each market. |
| `/projections` | `Projections.tsx` | Every eligible player with a game this week, priced or not. This is the model's actual output. |
| `/record` | `TrackRecord.tsx` | How the picks have done, by season, with a calibration curve and leaderboards. |
| `/faq` | `Faq.tsx` | How it works, in plain English and for data scientists. |
| `/model` | `Model.tsx` | The full method, for a reader who wants the detail. |
| `/players`, `/players/:id` | `PlayersSearch.tsx`, `PlayerDetail.tsx` | Browse and profile. |
| `/terms`, `/privacy`, `/responsible-gambling`, `/accessibility` | `Legal.tsx` | The four legal pages. Edited together, dated together. |

## Shared components

`Select` replaces every native `<select>`. The OS widget renders with its own
chrome, which on ledger paper is a grey popup in the system font, and no CSS on
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

**The site is a ledger, and everything follows from that.** Pages are ruled
paper (`--bg`), the masthead and footer are dark bookcloth (`--cloth`) stamped in
gold, type is ink (`--text`), and the accent is the binding's green (`--accent`). Corners
are square. Headings are Besley, a Clarendon, and everything else is Archivo,
both self-hosted through `@fontsource-variable`.

**Colour carries meaning, or it is not used.** Red means a loss, and a loss is
also written in brackets (`components/Acct.tsx`), so the meaning never rests on
colour alone. Green means won or over. Amber marks the value pattern. Tier
labels are rubber stamps (`.pl-stamp`), each in its own ink.

**A total gets a double rule.** The heavy rule over a hairline is what a
bookkeeper strikes under a final figure. It sits under totals, under the second
half of the tagline, and under the P in the mark.

The mark is a Besley Black capital P standing on that double rule
(`components/Logo.tsx`). There is no green and no red in it: a logo should not
borrow a semantic colour.

`scripts/build_brand.py` generates the whole asset pack from that one outline:
the logo set, favicons, app icons, profile pictures, banners, the link preview
and every post template. It writes them to `marketing/`, which is ignored by
git: the pack, the plan for using it and the font files the script reads all
live on my own machine and not in the repository. The script also copies the
icons and the link preview into `apps/web/public`, which is tracked, so the
site and the social accounts cannot drift apart. Running it on a fresh clone
needs Besley and Archivo static files in `marketing/fonts` first.

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
