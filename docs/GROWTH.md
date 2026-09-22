# Getting PriorLine in front of people

Everything here is written for the site as it actually is in September 2026: a
free research tool with no accounts, no payments, no outbound sportsbook links
and a published track record that includes the losing weeks. That last part is
the marketing, and I should stop treating it as a liability.

## What I am selling

Not picks. There are ten thousand accounts selling picks and every one of them
claims 70%. The record page says the model wins 52.6% of the time and that the
edge, where there is one, is small and specific. That is a worse pitch and a
better product, and it is the only thing here a competitor cannot copy in an
afternoon, because copying it means publishing their own losses.

So the line is: **the only prop model that shows you its losing weeks.**

Everything below is downstream of that.

## 0. Measure first

There is no analytics on the site at all, which means every number in this plan
is currently unfalsifiable. Nothing else in here is worth doing until that is
fixed.

Umami on the Hetzner box, behind the Caddy config that is already there. Self
hosted, no cookie banner, no third party reading my traffic, and it costs a
container. The alternatives are Plausible at about $9 a month and Vercel
Analytics, both fine, neither worth the money while the box is sitting there.

Four numbers, checked weekly:

| number | why it is the one |
| --- | --- |
| weekly uniques | the only honest size measure |
| /signals views per visit | whether the board is the product or the landing page is |
| return rate week over week | a prop site lives or dies on Thursday and Sunday repeats |
| median session length on /record | whether anybody reads the honest part |

## 1. Search

Done as of this commit: per page titles and canonicals, a sitemap, structured
data, and a 404 that asks not to be indexed. Before this, seven of the eight
pages were telling Google they were duplicates of the home page.

What I still have to do myself, because it needs an account:

1. Google Search Console, `https://search.google.com/search-console`. Add
   `priorline.io` as a **Domain** property, not a URL prefix, so it covers
   every subdomain and both protocols at once.
2. It will give me a TXT record. Add it at the registrar, wait, hit verify.
3. Submit `https://priorline.io/sitemap.xml` under Sitemaps.
4. Bing Webmaster Tools takes an import straight from Search Console. Five
   minutes, and Bing is what ChatGPT's browsing leans on.

Then leave it alone for three weeks. Indexing is slow and checking daily
teaches me nothing.

The search traffic worth wanting is long tail and it is player shaped: people
type "puka nacua receiving yards prediction", not "nfl prop model". Every
player page is a landing page for a query like that, which is why the player
pages now name themselves in the title and why `/players` is in the sitemap
even though the individual pages are not. If I ever want to be serious about
this, the next step is a short generated paragraph of real prose on each player
page, because a page that is entirely numbers ranks badly no matter how good
the numbers are.

## 2. The thing that actually spreads

A weekly post, published the same day every week, in the voice the site
already uses. Two halves:

- **Here is what the model likes this week and why**, in football words.
- **Here is how last week's went, including the ones that were wrong.**

The second half is the hook. Nobody in this space publishes it. A running,
public, unedited record is the single most shareable thing I have, and it
compounds: week 12 is more persuasive than week 3 purely because it is the
twelfth in a row.

Where it goes, in order of how much they will tolerate a link:

- **r/fantasyfootball, r/sportsbook, r/dfsports.** Post the content, not the
  link. Both subs will ban a link drop and both will upvote a real writeup with
  the numbers in the post. The site gets found from the profile.
- **X / Twitter.** The record screenshot is the post. Short, one image, no
  thread.
- **Discord.** Fantasy and betting servers are where this kind of thing gets
  used daily rather than read once. Slower, stickier, and the only channel that
  produces actual repeat visitors.
- **LinkedIn**, for the different audience: the project as engineering work.
  That post is already written and does not belong in the same voice as the
  others.

One post a week that I actually publish beats a content calendar I abandon in
October. The season is 18 weeks. That is the whole commitment.

## 3. What not to do

- No paid ads. The cost per click on anything with "betting" in it is brutal
  and I have nothing to sell at the end of the funnel.
- No affiliate links to sportsbooks. It is the obvious way to make money here
  and it destroys the one claim the site has, which is that it has no stake in
  which side I take. It also changes the app store answer below from hard to
  very hard.
- No Discord server of my own yet. An empty server is worse than no server.
- No email list until something is worth mailing weekly.

## 4. A phone app

Short answer: a real app is harder than it looks and mostly for reasons that
have nothing to do with code. There are three routes.

### Install the website (a PWA)

A manifest and a service worker, both small, and the site becomes installable
from the browser on Android and iOS: home screen icon, own window, no browser
chrome. No review, no developer account, no fee, ships when I push.

What it does not get: a store listing, push notifications on iOS below 16.4,
and the credibility of being findable in a store.

This is the right first move and it is maybe a day of work.

### Wrap the site (Capacitor)

Capacitor puts the existing React app in a native shell and produces real iOS
and Android builds. Cheap in engineering terms, roughly a week including the
icon and splash screen work.

The problem is review, not code. Apple's guideline 4.2 rejects apps that are
"a repackaged website", and a wrapper with no native capability is exactly the
thing it names. Getting through it means the app has to do something the
website cannot: push notifications when a line moves is the obvious candidate
and is genuinely useful, offline access to today's board is a second.

Then there is the sports betting question. PriorLine does not take bets, does
not hold money and does not link to a sportsbook, so it is not real money
gaming under Apple's 5.3 or Google's real money gambling policy, and those are
the rules that require an operator's licence. What it does get is the
adjacent treatment: a 17+ age rating, closer scrutiny, a reviewer who may ask
what the app is for, and in some regions a listing that is geographically
restricted. The problem gambling helpline in the footer and the absence of
affiliate links are both genuinely helpful here, which is another reason not to
add affiliate links.

Expect a rejection or two. Expect to answer questions in writing. Budget a
month of calendar time even though it is a week of work, and $99 a year for
Apple plus $25 once for Google.

### Build it native (React Native / Expo)

Months, not weeks, and a second codebase to keep in step with the first. The
only reason to do this is if the phone becomes the primary way people use the
site, and I will know that from the analytics in section 0 rather than by
guessing now.

### What I am actually going to do

PWA now. Capacitor in the offseason, and only if the weekly numbers say the
phone matters, and only with push notifications built first so there is an
honest answer to guideline 4.2.

## 5. The order

1. Umami on the box. Nothing else is measurable without it.
2. Search Console and Bing, this week, because indexing takes weeks to start.
3. The weekly post, every week of the season without missing one.
4. PWA manifest.
5. Reassess in January with four months of real numbers.
