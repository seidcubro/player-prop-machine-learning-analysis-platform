# PriorLine marketing plan

Written 9 November 2026, in Week 10 of the season. Figures quoted here are from
the record on that date and will be out of date by the time I read this again.
Every post takes its numbers from [priorline.io/record](https://priorline.io/record)
on the day it goes out, never from this file.

## The position

**A sportsbook keeps its book closed. This one is open.**

Every other picks account sells certainty: a record with the losses trimmed
off, a hit rate with no price beside it, a screenshot of the one parlay that
landed. I cannot out-shout that and I should not try. What I have that they
cannot copy is the thing they are hiding: every pick graded, every tier
published, losses written in red, and the share I needed to win printed next to
the share I won.

So the brand is a ledger, and the marketing is the ledger being kept in public.
The product is not "picks that win". The product is "picks you can check".

One sentence, for bios and intros:

> NFL player prop projections, graded against the price they were offered at.
> The whole record is posted, including the losses.

## What I can and cannot say

The record on 9 November 2026:

| | Picks | Won | Needed |
|---|---:|---:|---:|
| Elite | 3,094 | 52.7% | 51.6% |
| Everything graded | 11,480 | 52.0% | 53.3% |

That is the honest material. It supports some claims and forbids others.

**I can say**

- Elite picks have won more often than their prices needed, and by how much.
- The board as a whole has not, and by how much.
- Every pick is graded and the record is public.
- How the method works, in as much detail as anyone wants.

**I cannot say**

- That this makes money. Elite is a point ahead of its price. That is a small
  edge, not a proven one.
- "Winning", "profitable", "beat the books", "lock", "guaranteed", "can't lose",
  "risk free", or any hit rate without the needed rate beside it.
- Anything about a week, a market or a streak under about 500 picks as if it
  meant something. Small samples have burned this project twice.
- Anything that tells someone to bet, how much to bet, or to bet now.

If a post would not survive being read out next to the record page, it does not
go out.

## Rules every post follows

1. **Hit rate leads, the price sits beside it.** "Won 52.7%, needed 51.6%."
   Units, if they appear at all, are smaller and underneath.
2. **Losses get posted with the same care as wins.** There is a template for it
   (`templates/in-red`). A losing week skipped is the brand broken.
3. **The notice is on everything.** "21+. Projections, not betting advice.
   Gambling problem? Call 1-800-GAMBLER." It is baked into every template
   footer. Do not crop it out.
4. **No sportsbook links, no affiliate codes, no promo offers.** Ever. This is
   a product commitment, and it is also what keeps most of the advertising
   rules below from applying.
5. **No urgency.** No countdowns, no "last chance", no "tail before it moves".
6. **Voice.** First person, plain, blunt. No exclamation marks, no em-dashes,
   no emoji, no hype words. Short sentences. Say the number.
7. **Every market is covered and none is hidden.** If a market is doing badly,
   that is a post, not a secret.
8. **Never tag, quote or use the name of a player in a way that looks like an
   endorsement,** and never use team logos, league marks or player photos in a
   post. The templates are type and rules only, on purpose.

## Who this is for

1. **The bettor who has been burned by a tout.** They have paid for picks, seen
   the record quietly edited, and stopped trusting anyone. The open ledger is
   aimed straight at them.
2. **The numbers bettor.** Reads Reddit threads about closing line value, keeps
   a spreadsheet, wants a second opinion to check their own against. Wants the
   method, and will read `/model`.
3. **The data and engineering crowd.** Not customers. They are the LinkedIn and
   Hacker News audience, they share well-made honest work, and they are where
   credibility comes from.

## The week

The NFL week sets the rhythm. Five posts a week on the main accounts is enough,
and the same five every week is the point: a ledger is a habit.

| Day | Post | Template | Notes |
|---|---|---|---|
| Tuesday | **The account.** Last week graded: picks, won, needed, the difference. | `ledger` or `in-red` | The anchor post. Goes out win or lose, same time every week. If the week was short of its price, it is the `in-red` template and it says so in the first line. |
| Wednesday | **One thing explained.** A term, a step of the method, or a finding. | `explainer` carousel, or a text thread | Evergreen. This is what gets saved and shared. |
| Thursday | **The board is up.** How many picks this week, how many are elite, link to the board. | `statement` or a board screenshot | No individual pick is pushed. The board is the product. |
| Saturday | **One entry.** A single elite pick, with the projection, the line and the reason. | `entry` | One, not ten. Chosen before the result is known and graded on Tuesday whatever happens. |
| Sunday or Monday | **Nothing during games.** | | No live reactions, no victory laps. The grade comes on Tuesday. |

Off-season (February to August): one post a week. The method, the season in
review, what changed in the model and why, what was tested and thrown away.

## Post types

Each maps to a folder in `marketing/templates`.

| Type | What it is | When | Template |
|---|---|---|---|
| **Posted account** | The tier table: picks, won, needed, difference. | Weekly, and at season milestones. | `ledger/` |
| **In red** | A losing week or a losing tier, stated plainly. | Whenever it is true. | `in-red/` |
| **Entry** | One pick: our prior, their line, the side. | Once a week, before kickoff. | `entry/` |
| **Statement** | One sentence on dark cloth. The position, restated. | Sparingly. Launches, pinned posts, covers. | `statement/` |
| **Explainer** | A carousel, one idea per slide. | Weekly. | `explainer/` |
| **Correction** | Something I got wrong, what it cost, what changed. | When it happens. | `in-red/` or plain text |
| **Method note** | A finding from the research, with the table. | Fortnightly, mostly X and LinkedIn. | `landscape/` blank, or a screenshot of the doc |
| **Receipt** | A screenshot of the live record page, dated. | When someone asks "what is your record". | No template. The site is the template. |

Explainer topics that are already written, because they are pages on the site:
what a break-even is and why it is above 50%; why hit rate without a price is a
flattering number; what a median is and why a line is one; why the biggest
disagreements with the book are usually my mistake; why overs lose; what a tier
is a promise about; how a pick gets graded.

## Platform by platform

### X

The main channel. This is where prop bettors already are.

- **Handle and name:** PriorLine. Bio: the one-sentence position, then
  "21+. Not betting advice." Link: priorline.io.
- **Profile:** `social/profile/profile-cloth-400.png`.
  **Header:** `social/banners/x-header-1500x500.png`.
- **Pinned post:** the statement card plus three lines: what this is, the
  current elite won and needed figures, the link to the record.
- **Format:** text first. A table typed out in plain text reads well and gets
  quoted. Images are `landscape/` 1600x900 or the square templates.
- **Tuesday account** is a thread: the week, then by tier, then the one thing
  I learned. First post carries the image.
- **Replies are the growth.** Answer method questions in full. When someone
  posts a too-good record, do not dunk. When someone asks for a pick, link the
  board.
- **Do not:** quote-tweet touts, post live during games, post "we hit" clips,
  run polls about what to bet.

### Instagram

Slower, more visual, younger. The templates were built for this.

- **Profile:** `profile-cloth-1080.png`. Bio as above. Link: priorline.io.
- **Grid:** portrait 1080x1350. Alternate paper and cloth so the grid reads as
  pages of one book: roughly three paper posts to one cloth statement.
- **Tuesday:** the `ledger` template, exported fresh from
  `scripts/build_brand.py --api ...` so the figures are live and dated.
- **Wednesday:** the `explainer` carousel. Five slides is right. The four
  written ones in `templates/explainer` are the launch carousel.
- **Stories:** `templates/story`. The Thursday "board is up" and the Saturday
  entry go here with a link sticker. Keep type inside the middle of the frame;
  the templates already leave the top and bottom clear.
- **Reels:** optional, and only one kind: a slow screen recording of the record
  page being scrolled, no voice, captions on. No talking head, no music with
  lyrics.
- **Captions:** two or three sentences and the notice. No hashtag walls. Three
  at most: #NFL #PlayerProps #SportsBetting.

### LinkedIn

A different audience and a different story. Here it is not a betting product,
it is an engineering one: a machine-learning system that publishes its own
failures.

- **Personal profile, not a company page, does the work.** People follow
  people. The company page exists so the name resolves.
  Banners: `linkedin-personal-1584x396.png`, `linkedin-company-1128x191.png`.
- **Posts:** one a week, long form, written. The best material is already in
  `docs/MODEL.md` under "Lessons from the bugs" and "Tested and rejected":
  the calibration that was 14 to 29 points overconfident, the residual model
  that turned out to be a coin flip, the day spent chasing the wrong half of a
  fraction, the filter that deleted the exact rows the fix was for.
- **Shape of a post:** what I believed, what I measured, what it cost, what I
  changed. End on the number. No lesson-in-bold at the bottom.
- **Never post picks here.** Link to the method, not the board.
- **Image:** `landscape/statement-linkedin-1200x627.png`, or a chart from the
  docs.

### Reddit

High value and easy to get banned from. Treat it as a place to be useful, not
a place to post.

- Read the rules of each subreddit first. Most betting subreddits ban
  self-promotion outside a weekly thread, and some ban it entirely.
- Contribute answers on method (break-even maths, calibration, closing line
  value) with no link. Put the site in the profile and nowhere else.
- One honest write-up per season, in a subreddit that allows it and in its
  format: the full record, what lost, what I learned. Text post, tables typed
  out, link at the bottom.
- Never post from a second account. Never ask anyone to upvote.

### Threads and Bluesky

Mirror the X posts by hand, a few hours later. Same assets. No separate
strategy until one of them shows it deserves it.

### YouTube and TikTok

Not yet. Video is a second job, and TikTok is restrictive about gambling
content. The channel art exists (`youtube-channel-2560x1440.png`) so the name
can be claimed and parked. Revisit after the season if a weekly five-minute
"the account" screen recording looks sustainable.

### The site itself, and search

The cheapest growth is the pages that already exist.

- Each explainer topic is a real question people type into a search engine.
  The FAQ and the model page already answer them; keep those pages current.
- The link preview is now `og-image.png`, so a pasted link shows the brand
  instead of a bare URL.
- A weekly dated record page that can be linked to is worth more than any
  post. If I build one thing for marketing, it is a permanent URL per week.

### Email

Not until there is a decision on accounts. When there is: one email a week, on
Tuesday, and it is the account. Double opt-in, a working unsubscribe link and a
postal address in the footer are legal requirements under CAN-SPAM, not
niceties.

## Launch sequence

The accounts start empty, so the first two weeks are fixed.

1. **Day 0.** Claim the handle everywhere, including the places I will not use
   yet. Upload profile pictures and banners. Write the bios. Pin nothing yet.
2. **Day 1.** The statement card: "A sportsbook keeps its book closed. This one
   is open." Pin it.
3. **Day 2.** The posted account, live figures, with the losing tiers visible.
4. **Day 3 to 6.** The four-slide explainer, one step a day on X, all at once
   as a carousel on Instagram.
5. **Day 7.** The first Tuesday account. From here the weekly rhythm runs.
6. **Week 2.** The first LinkedIn essay. Start with the calibration story.

## Paid advertising

My recommendation is to spend nothing on ads this season.

The practical reason is that elite is one point ahead of its price. Paying to
bring in people on the strength of that is paying to bring in people who will
be disappointed. Organic growth on an honest record costs nothing and attracts
the readers who want what this actually is.

The second reason is that gambling-adjacent advertising is restricted almost
everywhere, and the rules are written for operators and affiliates. As I
understand them today:

- **Meta (Instagram, Facebook):** online gambling and gaming ads need written
  permission in advance, and are age and country targeted.
- **Google:** gambling-related ads need certification, state by state.
- **X:** gambling content ads need prior authorisation and are limited by
  country.
- **TikTok:** gambling promotion is broadly prohibited.
- **LinkedIn:** gambling ads are prohibited.
- **Reddit:** gambling ads are restricted and need approval.

PriorLine takes no bets and links to no sportsbook, which may put it outside
some of those definitions. That is a question for each platform's review and
not one to assume the answer to. Check the current policy before spending a
dollar, because these change.

## The legal floor

I am not a lawyer and none of this is legal advice. Before any paid campaign,
any email list, or any paid tier, a lawyer who knows US gaming advertising
should read the site and this plan.

What the plan already does, and why:

- **21+ and the helpline on everything.** Several states require a
  problem-gambling message on sports betting advertising. Carrying it
  everywhere is simpler than tracking which.
- **No targeting of minors.** No youth-skewed platforms or formats, no cartoon
  styling, no student or campus angles.
- **No "risk free", no guarantees.** Regulators have acted on that wording.
- **Results claims are true, current and complete.** The FTC's standard for any
  advertising claim is that it is truthful and substantiated. Quoting the won
  rate with the needed rate, and linking the full record, is how that is met.
- **No endorsements to disclose.** There are no affiliates and no paid
  promoters. If that ever changes, FTC disclosure rules apply from that day.
- **No league, team or player property.** No logos, no uniforms, no player
  photos in any marketing asset.

## What to measure

Follower counts are not the measure. In order:

1. **Visits to `/record`.** If people are checking the ledger, the position is
   landing.
2. **Returning visitors on Tuesday and Thursday.** A habit is the goal.
3. **Saves and shares on explainers.** These are the posts that travel.
4. **Replies that ask about method.** The right audience asks how, not who.

The site has no analytics today, and adding any means updating the privacy
page first. A privacy-respecting, cookie-free counter is the only kind that
fits the brand.

## Things I will not do

- Delete or hide a losing post.
- Post a winning slip.
- Sell picks through DMs, Telegram or Discord.
- Take money from a sportsbook, in any form.
- Buy followers, engagement or reviews.
- Invent a testimonial, a customer count or a press mention.
