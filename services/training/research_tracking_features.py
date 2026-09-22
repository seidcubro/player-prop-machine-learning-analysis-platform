"""Do Next Gen Stats and PFR's advanced tables improve the projections? No.

Kept as a record of a negative result, so the next person to notice these
tables sitting unread does not spend a day finding out the same thing.

The idea was sound. The feature build reads the box score, snap counts,
play-by-play and team form, and four tables were never touched. On the
player-games a sportsbook actually prices, the coverage is there:

    PFR advanced receiving    95% of receiving props
    NGS passing               97% of passing props
    NGS rushing               35-40% of rushing props
    NGS receiving             36% of receiving props

So twenty-two features were added to build_features: separation, share of
intended air yards, catch rate and yards after catch over expectation; rush
yards over expected per attempt, rush percentage over expected, eight-defender
rate; time to throw, completion percentage over expectation, aggressiveness,
air yards to the sticks; broken tackles, drop rate, yards before and after
contact. Each rolled to a mean and a trend over the lookback window, exactly
as the play-context features are.

They land where expected: 42% of receptions rows carry the NGS receiving
features, 93% of passing rows carry the NGS passing ones. And they change
nothing.

    receptions   enet_v2 (current)      MAE 1.2083   R2 0.4466
                 same model, +features  MAE 1.2092   R2 0.4463
                 HistGB, +features      MAE 1.2125   R2 0.4471

    pass_yds     hgb_v1 (current)       MAE 62.05    R2 0.3889
                 same model, +features  MAE 62.46    R2 0.3900

The elastic net gives every one of the new features a coefficient of exactly
zero, which is the clearest statement available: with L1 in the loss, they do
not pay for themselves. A tree model, which can use sparse non-linear features
a linear model cannot, lands in the same place.

The reason is redundancy rather than irrelevance. The pipeline already carries
target share, air yards per target, yards after catch, red-zone usage and snap
share from play-by-play and snap counts. Separation and air-yards share are
largely the same information arriving by a different road.

What this does not rule out:

  - routes run, from the participation table, which is the one genuinely new
    usage signal. It stops at 2025, so a model trained on it cannot be served
    this season. Revisit if nflverse backfills 2026.
  - these features for touchdown markets, which were not tested here.
  - defensive matchup detail at the individual level, which nothing here has.

Reverted from jobs.py after measuring. The features cost time on every build
and storage on every row, and they buy nothing.
"""
