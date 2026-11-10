# PriorLine design system

Recorded on 9 November 2026 from the shipped code. The tokens live in
apps/web/src/index.css and the mark in apps/web/src/components/Logo.tsx. If this
file and the code disagree, the code is right and this file is stale.

## The idea

PriorLine is a ledger. A sportsbook keeps its book closed, and this one is open,
so the site looks like an account book: ruled paper, a red double margin rule,
ink, a dark cloth cover stamped in gold. Nothing on the page is there for
decoration that an accountant would not recognise.

The line is "Our prior. Their line." The second half is italic and sits on a
double rule.

## Colour

| Token | Hex | Role |
|---|---|---|
| paper (--bg) | #e4ecdc | Page ground |
| leaf | #eef3e8 | Raised panels and table grounds |
| rule (--border) | #a9c2b5 | Hairlines |
| ink (--text) | #18213b | Body and headings on paper |
| ink-dim | #4a556f | Secondary text on paper |
| binding green (--accent) | #14483b | Links, the italic half of a heading, focus. The cloth, lightened enough to read as a colour on paper. |
| red | #a31b1b | A loss. Nothing else. |
| margin red | #c9585a | The double margin rule. Never text. |
| cloth (--cloth) | #0f1a17 | Masthead and footer |
| gold | #cda64e | The mark and type on cloth. Never on paper. |

Green means won or over. Amber marks the value pattern. No colour carries a
meaning by itself: a loss is red and bracketed, a win is green and signed, a
tier is coloured and named.

## Type

- Besley, a Clarendon, at 800 and 900 for headings, the wordmark and the
  figures a page exists to show. 500 italic for the second half of a heading.
- Archivo for everything else. Labels are Archivo condensed, bold, capitals,
  letterspaced.
- Figures in tables are tabular and right aligned.

Both are self-hosted through @fontsource-variable. No font is fetched from a
third party.

## Shape and rule

- Every corner is square. The radius tokens are all zero.
- A total gets a double rule: a heavy rule over a hairline.
- Sections are separated by hairlines, not by cards or shadows.
- There are no gradients, no glass, no glows and no drop shadows.

## Components that carry the identity

- Acct (components/Acct.tsx): a signed figure. Negative values are red, in
  brackets, and read as "minus" to a screen reader.
- TierLedger (components/TierLedger.tsx): the posted account. Picks, won,
  needed, difference, read live from the record.
- Tier stamps (.pl-stamp): a tier name set as a rubber stamp in its own ink.
- The mark (components/Logo.tsx): a Besley Black capital P on the double rule.
  Three cuts by size, so the rules survive at 16 pixels.

## Voice

First person, plain, blunt. No exclamation marks, no em-dashes, no urgency.
Hit rate leads and the rate the price needed sits beside it. Figures are read
from the live record and never typed into static text.

## Accessibility

The target is WCAG 2.2 AA. Text is checked against 4.5:1 on paper and on cloth.
Every interactive target is at least 24 pixels. Focus is a visible 2 pixel
outline. Motion is small everywhere except the front page, which opens the way
a book is made: the name is stamped, the rule is struck, the account is posted.
Every element is visible without it, and all of it is removed under
prefers-reduced-motion.

## What is not allowed

Gold on paper, green on cloth, blue anywhere, rounded corners, a second accent colour, a
sportsbook logo, a team or league mark, a countdown, a figure without its
source.
