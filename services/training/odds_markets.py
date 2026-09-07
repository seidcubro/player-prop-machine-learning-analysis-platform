"""The one mapping from sportsbook market keys to internal market codes.

Eleven files each carried their own copy of this dict and most of them had
drifted. The failure mode is the worst kind: nothing errors, the rows for the
missing key are simply dropped, and the number at the end is quietly too small.

It has bitten this project three times:

  * `eval_clv.py` covered six of nine markets, so passing attempts, completions
    and anytime touchdowns were silently excluded from every closing-line
    result.
  * `build_prop_edges.py` had no `player_anytime_td` entry, so a sync that
    returned 940 anytime-touchdown props across 489 players produced exactly
    zero edges and the market never appeared on the board.
  * The pipeline scripts kept their own market list, so a newly added market was
    never retrained by a weekly run.

One definition, imported everywhere. Adding a market means editing this file and
`services/api/app/odds_market_map.py`, which lives in a different service and
cannot be imported from here. `audit_freshness.py` compares the two and fails if
they disagree, so the duplication cannot rot silently.
"""

# Internal market code -> the key The Odds API uses.
MARKET_TO_ODDS: dict[str, str] = {
    "pass_att": "player_pass_attempts",
    "pass_completions": "player_pass_completions",
    "pass_yds": "player_pass_yds",
    "pass_td": "player_pass_tds",
    "rush_att": "player_rush_attempts",
    "rush_yds": "player_rush_yds",
    "recs": "player_receptions",
    "rec_yds": "player_reception_yds",
    "any_td": "player_anytime_td",
}

# The inverse, which is what most consumers actually want.
ODDS_TO_MARKET: dict[str, str] = {v: k for k, v in MARKET_TO_ODDS.items()}

# Markets books do not post for ordinary players, kept only so historical rows
# and the projections page still resolve. `player_rush_tds` and
# `player_reception_tds` returned zero rows across three full seasons; books
# post anytime touchdown instead.
LEGACY_ODDS_TO_MARKET: dict[str, str] = {
    "player_rush_tds": "rush_td",
    "player_reception_tds": "rec_td",
}

# What a consumer reading historical odds should use: current keys plus the dead
# ones, so old snapshots still map.
ALL_ODDS_TO_MARKET: dict[str, str] = {**ODDS_TO_MARKET, **LEGACY_ODDS_TO_MARKET}

# Outcomes that are small integer counts. These derive P(over) and the median
# from a Poisson on the point model's rate rather than from quantile regression,
# which on a 0-to-4 variable reproduces the population shape instead of the
# player's. See build_prop_edges.count_distribution.
COUNT_MARKETS = frozenset({"pass_td", "rush_td", "rec_td", "any_td"})
