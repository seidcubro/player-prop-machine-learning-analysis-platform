# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: a person researching their own NFL player props before they bet.** They
arrive at priorline.io from search, usually player-shaped ("puka nacua receiving
yards prediction"), with no account and no prior relationship. They have been sold
picks before by people claiming 70%, so their first job is not finding a bet, it is
deciding whether these numbers can be trusted at all. Design decisions resolve in
their favour when they conflict with density for an expert.

Secondary: the author, using the board as a pre-bet check. This is how the project
started and it still governs the data pages, but it does not win a tie.

The FAQ carries two reading levels (plain English and a data-scientist mode). That is
an existing capability, not a mandate to split every surface two ways.

## Product Purpose

Pull every NFL player prop the sportsbooks are offering, project the outcome
distribution for each player and market from game data, compare the model's number to
the posted line, choose a side, rank the disagreements by confidence, and publish how
those picks actually did.

The question the product answers, in the author's words in the README: *is this line
wrong?*

Success is not a high hit rate. Success is a visitor being able to tell, from the
site alone, which parts of it beat the price they were offered at and which parts did
not.

## Positioning

**The only prop model that shows you its losing weeks** (`docs/GROWTH.md`).

The mechanism a neighbouring product cannot truthfully copy is not the model. It is
the published graded record, including the tiers that lost money and the seasons with
negative ROI, plus scoring every pick against the break-even implied by the price it
was offered at rather than against a raw hit rate. Copying the claim means publishing
their own losses.

The README states plainly that the head-on version of the product loses money and
that the surviving edge is structural rather than predictive. That self-assessment is
part of the positioning, not a bug in the copy.

## Operating Context

- **NFL weekly cadence.** A slate runs Thursday to Monday and one build prices every
  event that has not kicked off, so the board is a whole slate at once, not a day.
  Traffic concentrates on Thursday and Sunday; the visit happens before kickoff and
  the grading happens after.
- **Data in.** nflverse game data for features; The Odds API for lines. The Odds API
  bills per event per market, so odds syncs are a deliberate, costed action rather
  than something to run speculatively.
- **Where it runs.** Postgres, FastAPI and the training jobs on a single Hetzner box
  under Docker Compose behind Caddy, scheduled by systemd timers. The React app
  deploys separately to Vercel. Deploys are run by hand by the author.
- **Routes:** `/` (landing), `/signals` (this week's board), `/projections` (every
  eligible player, priced or not), `/record` (the graded history), `/faq`, `/model`
  (the full method), `/players` and `/players/:id`, the four legal pages
  (`/terms`, `/privacy`, `/responsible-gambling`, `/accessibility`), plus a 404.

## Capabilities and Constraints

- **Eleven markets, all of them live:** pass yards, attempts, completions and TDs;
  rush yards, attempts and TDs; receptions, receiving yards and TDs; anytime TD.
  Anytime TD is priced as a single Yes with no line, so it has no median to compare
  and the shared market helpers special-case it.
- **Four confidence tiers:** elite, strong, medium, small. A tier is a promise about
  accuracy and they must stay ordered by it. A market with no validated ranking
  publishes every pick as `small`, which is the honest word for "we cannot tell which
  of these beats a coin flip."
- **Vocabulary the UI and the docs share:** the *gap* (model projection against the
  line, measured as a share of the line), *break-even* or *needed* (the implied
  probability with the vig in, which is the share of picks that has to win just to
  stay level), *hit rate*, *units*, *EV*.
- **No accounts, no payments, no email capture today.** Whether that stays true is
  explicitly undecided and was not claimed as a commitment. Future work may introduce
  them; it may not assume they are forbidden, and it may not assume they are coming.
- **No analytics.** As of `docs/GROWTH.md` (September 2026) nothing measures traffic,
  so every audience and engagement number is currently unfalsifiable. Self-hosted
  Umami on the existing box is the recorded intent, not yet done.
- **Model accuracy target, not current state:** the author's stated goal is every
  market correct at least 55% of the time. The live record is well below that in most
  markets. Copy must never present the target as the result.

## Brand Commitments

- **Name:** PriorLine, at priorline.io. An earlier identity under a different name
  is retired and must not resurface.
- **No sportsbook links and no affiliate revenue, permanently.** The site never sends
  traffic to a book. Nothing on it is written to cause a bet, so there is no urgency
  copy, no countdowns, no "lock this in."
- **The record stays published in full, permanently.** Losing weeks, losing tiers and
  negative ROI stay visible. Softening, burying or aggregating away a loss breaks the
  one claim the product is built on.
- **Every market stays covered, permanently.** No market is dropped because it is
  hard to model. A market that cannot be ranked is published as unranked.
- **Voice:** the author's first person. Plain, specific, blunt. It states what failed
  before what worked, quantifies rather than adjectives, and never claims more than
  the data supports. No em-dashes. No exclamation marks. Where a number is quoted in
  copy it is read from the live record, never typed into static text, because that is
  how the site came to advertise a figure the record contradicted.

## Evidence on Hand

Real, and usable in any surface:

- **The graded record.** Live picks in `prop_edge_results`, served by `/record/*`
  (seasons, by market, by tier, each carrying hit rate and the break-even implied by
  the prices paid). Roughly 9,900 graded live picks as of November 2026. These move
  weekly and must be read from the API, not transcribed.
- **A full blind season.** Trained on 2022 to 2024, ran 2025 untouched, graded 6,736
  picks: 51.5% hit rate against a 53.9% break-even, negative ROI (README).
- **The method, written out.** `docs/MODEL.md`, published at `/model`, including the
  ideas that were tested and thrown out.
- **The brand asset pack**, generated from one definition by `scripts/build_brand.py`
  so icons, favicon, avatars, banners and lockups cannot drift apart.

Absent, and not to be invented:

- No customers, testimonials, case studies, press mentions, logos-of-companies, user
  counts, revenue, or pricing. None of these exist in any form.
- No user research, no interviews, no usability testing.
- No traffic data at all until analytics ship.

## Product Principles

1. **Publish the losses.** The record is both the proof and the marketing. No surface
   may read better than the data behind it supports.
2. **Score against the price, not the hit rate.** A pick is only good if it beat the
   number the book needed. A hit rate compared to anything other than its own
   break-even is a flattering number and has already misled this project once.
3. **Cover every market, and say so when a market cannot be called.** Unranked is a
   publishable answer. Hidden and dropped are not.
4. **A tier is a promise.** Labels are ordered by measured accuracy and are only
   claimed after the signal survives being asked twice on data it was not fitted to.
5. **Nothing here exists to make someone bet.** No affiliate path, no urgency, no
   persuasion that outruns the evidence.

## Accessibility & Inclusion

**WCAG 2.2 AA.** The 2.2 additions are the ones that bite on this product: target
size on dense sortable tables and mobile filter rows, visible focus appearance on the
masthead navigation and the custom `Select`, and a non-dragging alternative anywhere a control
is dragged. The app is ink on ledger paper with a dark cloth masthead and footer, so
contrast has to be verified on both grounds, and semantic colour (green
for won or over, red for lost or under) must never be the only carrier of meaning.
