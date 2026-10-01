# Week 3, 2026: what happened and why

73 published picks, 42.5%, minus 13.62 units. This is the autopsy.

Rerun it any time with

```bash
docker compose run --rm training python autopsy_board.py 2026-09-24 2026-09-28
```

It reads what we actually published, joins the line the books actually offered
and the box score, and grades every priced prop we had a live projection for,
not just the ones that cleared publication. The unpublished ones are the control
group and they are where the answer turned out to be.

- [The number](#the-number)
- [Where we were right](#where-we-were-right)
- [Where we failed](#where-we-failed)
- [The cause](#the-cause)
- [Two smaller things that were also wrong](#two-smaller-things-that-were-also-wrong)
- [What is fixed](#what-is-fixed)
- [What is not fixed and will not be](#what-is-not-fixed-and-will-not-be)

## The number

                       picks      hit   break-even      ROI    units
    ALL PUBLISHED         73    42.5%        52.7%   -18.7%   -13.62

    elite                 10    80.0%        52.8%   +54.1%    +5.41
    strong                18    27.8%        48.9%   -40.7%    -7.32
    medium                45    40.0%        54.2%   -26.0%   -11.71

## Where we were right

**The top tier held.** Elite went 8 for 10. That is the tier the site offers as
a bet and it is the only one that has made money in every season, and it did it
again in a week where everything below it fell apart.

**Receptions.** 27 picks, 63.0%, plus 6.21 units. The best market on the board in
the backtest and the best one live. Receptions is the most predictable thing we
project: a target share is a role, and roles hold week to week.

**Tight ends.** 6 of 7. Small sample, but tight ends are the position where the
window tells you the most, because a tight end's usage is decided by the offense
rather than by the coverage.

## Where we failed

    by market                  picks      hit      units
    rush_yds                      12    16.7%      -8.14
    pass_att                       3     0.0%      -3.00
    pass_completions               3     0.0%      -3.00
    rush_att                      12    41.7%      -2.65
    pass_td                        9    44.4%      -1.79

    by position
    QB                            24    37.5%      -7.45
    RB                            24    37.5%      -7.07
    WR                            18    38.9%      -4.89

Rushing yards went 2 for 12. Quarterbacks went 9 for 24 and lost on every
passing market.

But the number that actually says what went wrong is this one: **70 of the 73
picks were unders.** On a slate where the outcome landed over the line 52% of
the time.

That is not a read on the week. A board that takes the under 96% of the time is
not disagreeing with the book player by player, it is disagreeing with the book
in one direction before it has looked at anybody.

## The cause

The projections were fine. The rule that picks sides from them was not.

    market              n   projection   median     line   actual
    rec_yds           147         32.1     25.7     31.0     35.7
    rush_yds           76         35.8     30.9     33.8     36.8
    pass_yds           27        220.8    214.1    223.2    231.0
    rush_att           46         10.8     10.2     11.3     12.0

    we sat above the line on    projection 53%   median 34%
    the outcome landed above it              52%

Two numbers come out of every projection. The point model produces a
**projection**, fitted to squared error, which targets the conditional mean. The
quantile ensemble produces a **median**, fitted to pinball loss at q=0.5. On
receiving yards the projection sat 1.1 yards above the line and the median sat
5.3 below it. The projection agreed with the book on which side 53% of the time
and the slate went over 52% of the time. The median agreed 34% of the time.

The board ranks on the median.

And the thresholds it ranks against were measured on something else again.
`backtest_gap_system.py`, which is where the 58.6% and the +9.4% ROI in
[FINDINGS.md](FINDINGS.md) and in [MODEL.md](../MODEL.md) come from, computes

    z = (point projection - line) / the spread of the player's recent games

while `build_prop_edges.py` was computing

    z = (quantile median  - line) / (q75 - q25) / 1.349

Two differences, not one, and I only saw the first. Over the three graded weeks
of 2026, on 1,172 priced props with a live projection and a box score, the
production statistic was negative on 71% of them while the outcome landed under
on 49%, so I took the numerator to be the problem and moved the gap onto the
point projection.

Read on. That was the wrong half.

It is worth being clear about what the ladder was doing wrong and what it was
not. A conditional median *belongs* below a conditional mean on a right-skewed
target, and these targets are very right-skewed. That part is correct behaviour.
On top of it the ladder is also mildly miscalibrated: 43% of outcomes landed
below the published p50 against a nominal 50%, and the whole ladder sits low,
with 5% below p10 against 10% and 86% below p90 against 90%. Both things are
true. Neither is an argument for ranking picks on a number the thresholds were
never measured against.

**My first fix for this was wrong, and the correction matters more than the
original.**

I moved `gap_z` onto the point projection, on the strength of the table above.
Then I built `backtest_production.py`, which refits the quantile ladder
walk-forward so the backtest and the board compute the same statistic from the
same objects, and ran all four combinations of numerator and denominator on the
same rows:

    numerator / denominator     picks     hit                 ROI    units
    median     / window          1558   59.1% [56.3, 62.0]  +11.0%  +171.6
    projection / window          1236   59.0% [56.1, 61.9]  +10.3%  +127.9
    median     / quantile         696   57.9% [53.7, 62.2]   +8.0%   +55.5
    projection / quantile         413   57.9% [52.5, 63.1]   +6.1%   +25.0

I had shipped the bottom row. The numerator was never the problem. **The
denominator was**, and it was costing 860 picks and 116 units.

The ladder's median is honest: 47% of outcomes land below it against a nominal
50%. Its q25 is not, at 15% against 25%, and 5% on receptions. q25 is half the
width of the spread the board divides by, so the quantile spread ran 1.4 to 1.9
times the window spread, every z came out that much smaller, and a bar meant to
publish a thousand picks published four hundred.

And the premise of this whole page was a small sample. Those three weeks went
over 52% of the time. The backtest seasons go over **47%**. A board that leans
under is correct about a market that settles under, and the 70-of-73 split was
not a bug announcing itself, it was a real tilt plus a bad week. What the three
weeks were genuinely showing was that something had squeezed the board to a
third of its size, and that was the scale.

So: the gap is centred on the median, as it was, and divided by the standard
deviation of the player's own recent games, which is what every threshold in
`gap_tier.py` was measured against. `GAP_SCALE=quantile` goes back.

Two limits worth stating. The odds history is overwhelmingly one season, 7,299
of 7,726 priced props are 2025. And 2026 to date loses money under all four
configurations, on 350 published picks with an interval that spans break-even,
so none of this is a promise about Week 4.

I also tested whether the early-season thin windows were the problem, since the
window spread in Week 3 is computed from two games. They are not: windows of one
or two games returned 60.7% and +15.7%, the best bucket of the four. No minimum
games guard, because the data refused it.

## Two smaller things that were also wrong

**Backup quarterbacks were being projected as though they played.** Week 3
published Mason Rudolph at 20.6 attempts, 12.8 completions and 82.6 passing
yards, beside Aaron Rodgers at 36.4, 22.1 and 215.9, as though Pittsburgh were
splitting snaps. Rodgers was on no injury report. Rudolph did not take a
meaningful snap.

The edge builder already refused picks on a backup behind a healthy starter.
`build_projections.py` did not, so the player pages carried the numbers anyway.
It now calls the same function, so a quarterback room is a starter and nobody
else until the starter is out or doubtful, at which point the backup inherits
the role and is projected as the starter he has become.

The same file was also the one place that never got the depth chart fix from
September. It was taking `MIN(depth_team)` across every slot including kick and
punt returns, so a returner read as a starter, which is how 11.5% of
player-weeks got the wrong depth rank. It has the offensive-slot filter now,
like the other three places.

**Saquon Barkley at 12.5 carries.** He got 15.

    week   carries   rushing yards   offensive snaps
    1           15              83        39   (71%)
    2            4               9        12   (16%)
    3           15              82        36   (72%)

The window averaged 15, 4 and 15 and got 11.3. But the middle game is not a
light workload, it is an absence: twelve snaps, a sixth of the offense. His
workload in games he plays is 15 carries, every time.

There was already a snap floor in the feature builder and it only applied to
quarterbacks. It applies to everybody now, at 20% rather than the quarterbacks'
35%, because a receiver on a third of the snaps has a real role and a receiver
on a sixth of them has left the game. Measured on 24,748 player-games, a 20%
floor improves the window's correlation with the next game in all six markets
and its mean absolute error in all six; a 35% floor helps the volume markets and
hurts the receiving ones.

While fixing it I found that the feature query coalesced an unknown snap share
to zero, so the 0.8% of player-games where the snap feed lags the box score were
being read as "he did not play" and silently deleted from every window. That is
now a null, which counts.

## What is fixed

| | |
|---|---|
| the board ranked on a spread its thresholds were never measured on | `gap_z` divides by the window spread, which is where they came from |
| backup quarterbacks projected as though they played | same rule as the edge builder, called from the same function |
| kick returners reading as starters on the player pages | offensive-slot filter, matching the other three places |
| a sixteen-percent-snap game counting as a full game in the window | snap floor for every position, not just quarterbacks |
| an unknown snap share read as zero snaps | null stays null |

## What is not fixed and will not be

The ten worst misses across the three graded weeks:

    player              market      projection    line   actual   snaps
    Jordan Love         pass_yds         195.9   240.5    387.0    100%
    Tyler Shough        pass_yds         225.3   244.5    410.0    100%
    Matthew Stafford    pass_yds         247.3   242.5    390.0    100%
    Bryce Young         pass_yds         198.6   208.5    361.0     90%
    Lamar Jackson       pass_yds         202.5   220.5    324.0    100%
    Davante Adams       rec_yds           51.4    64.5    195.0     60%
    Chris Olave         rec_yds           70.1    76.5    182.0     90%

Every one of these is a player who played his normal snaps and then had a game
far beyond anything in his window. Davante Adams went for 195 yards on 60% of
the snaps. No feature built from the previous five games sees that coming,
because nothing that happened in those five games contains it.

This is the same thing the loss autopsy found in September: half of all losses
are a player's role widening inside the game, and role expansion is predictable
in the aggregate (AUC 0.773) but directionless, so refusing those picks throws
away more winners than losers. Any given Sunday.

The honest version of this page is that one bug was costing real money, two more
were making the product look stupid, and the rest of Week 3 was football.
