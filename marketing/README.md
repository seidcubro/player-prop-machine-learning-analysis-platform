# PriorLine brand pack

Everything in this folder except this file, the plan and the fonts is generated
by one script from one definition of the mark:

```
python scripts/build_brand.py --api https://api.priorline.io/api/v1
```

Run it again whenever the record should be refreshed in the ledger templates.
Without `--api` it builds everything except the live ledger, rather than print
a number I typed in. It needs Pillow and fontTools.

The plan for using all of this is in [MARKETING_PLAN.md](MARKETING_PLAN.md).

## The identity in one paragraph

PriorLine is a ledger. The mark is a Clarendon capital P standing on the double
rule a bookkeeper strikes under a final total. It is stamped in gold on dark
bookcloth, or written in ink on ruled paper. Losses are written in red, in
brackets. The line is "Our prior. Their line."

## Colour

| Name | Hex | Used for |
|---|---|---|
| Cloth | `#0f1a17` | Dark ground. Masthead, footer, statement cards, icons. |
| Gold | `#cda64e` | The mark and type on cloth. Never on paper. |
| Paper | `#e4ecdc` | Light ground. |
| Ink | `#18213b` | The mark and type on paper. |
| Binding green | `#14483b` | The accent on paper. "Their line." Links. |
| Red | `#a31b1b` | Losses, and only losses. |
| Margin red | `#c9585a` | The double margin rule on paper. Decoration only, never text. |
| Rule | `#a9c2b5` | Hairlines on paper. |

Gold on paper and green on cloth both fail contrast. Do not use them. There is
no blue and no second red: the only colours on cloth are gold and cream.

## Type

- **Besley** (800 and 900, and 500 italic) for headings, the wordmark and
  figures that are the point of the page.
- **Archivo** for everything else. Labels are Archivo at 78% width, bold,
  capitals, letterspaced.

Both are under the SIL Open Font License, which allows this use. The static
files are in `fonts/` with the licence note.

## What is here

```
logo/
  svg/   mark, wordmark, horizontal and stacked lockups
  png/   the same at 256, 1024 and 2048
icons/
  favicon.svg, favicon.ico, favicon-16 to 512
  apple-touch-icon.png, android-chrome-192/512, android-maskable-512
  site.webmanifest
  ios/   app-icon-1024 (App Store), the paper alternate, and the full size set
social/
  profile/   cloth and paper, 400 and 1080
  banners/   X, LinkedIn company and personal, Facebook, YouTube
  og-image-1200x630.png   the link preview
templates/
  ledger/      the tier table. "live" is filled from the record and dated
  entry/       one pick. "layout" shows where things go, with placeholders
  statement/   one sentence on cloth
  in-red/      the losing week
  explainer/   a five-slide carousel on how a pick gets made
  story/       1080x1920
  landscape/   1600x900 for X, 1200x627 for LinkedIn
fonts/
```

Each template comes as a worked version and a blank. The blank has the ground,
the margin rule and the footer, and nothing else. Open it in any editor, set
type in Besley and Archivo, and it will match.

Logo colourways: `gold-on-cloth` and `ink-on-paper` have their ground built in.
`gold`, `ink`, `black` and `white` are transparent.

## Using the mark

- **Two versions, by size.** The full mark has a heavy rule and a hairline.
  Under about 48 pixels the hairline disappears, so the favicons use a cut with
  heavier rules, and the 16 pixel favicon keeps a single rule. The script picks
  the right one. Do not scale the full mark down by hand.
- **Clear space.** Leave at least the height of the double rule, top to bottom,
  empty on every side.
- **Profile pictures are for circles.** The mark sits inside the middle 62% so
  no network crops it.
- **The iOS icon is square and opaque.** The system rounds the corners. Do not
  round them first.

Do not: recolour the mark outside the six colourways, put it on a photograph,
add a shadow or a glow, set it at an angle, stretch it, rebuild the wordmark in
another typeface, or separate the P from its rules.

## The notice

Every template carries this line in the footer, and it stays:

> 21+. Projections, not betting advice. Gambling problem? Call 1-800-GAMBLER.

## Placeholders

The `entry` and `in-red` layouts use "Player Name", "00.0" and "Week 0" on
purpose. No template ships with an invented pick or an invented result. The
only real figures in this folder are in the `ledger-live` files, and those are
dated on their face.
