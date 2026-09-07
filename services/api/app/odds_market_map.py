"""Which sportsbook market keys the live odds sync asks for.

Every key here costs credits on every event, every sync, so the list is the
answer to one question: does a book actually post this, and does something
downstream read it? Three entries failed that test.

**pass_yds was missing entirely.** Passing yards is one of the largest markets on
the board, `prop_markets` has it, models are trained on it and the edge builder
looks for it -- and the live sync never requested it. The only reason there is
any passing-yards history at all is that `backfill_historical_odds.py` names the
key directly in its own list, which is where 3,288 snapshot rows came from
against 0 live rows. The key works; it was simply never asked for.

**rush_td and rec_td came back empty every time.** Zero rows in
`odds_player_props` and zero in `odds_snapshots` across 2023, 2024 and 2025.
Books do not split touchdowns by type for ordinary players; they post an anytime
touchdown scorer market instead. Requesting keys that never return anything is
paying for empty responses, so they are gone. Their models still run and still
produce projections, which is fine -- there is just nothing to price against.

**pass_ints was being bought and never read.** 62 rows came back and no row in
`prop_markets` matches it, so nothing in the system could consume it.

Net effect: the same number of markets per request as before, minus two that
return nothing and one nothing reads, plus the one large market that was
missing.

`player_anytime_td` is now requested. It needed a `prop_markets` row, an
`any_tds` column on player_game_stats_app (rushing plus receiving), and a model
before it was worth the credits; all three are in place.
"""

ODDS_API_MARKET_MAP = {
    "pass_att": "player_pass_attempts",
    "pass_completions": "player_pass_completions",
    "pass_yds": "player_pass_yds",
    "pass_td": "player_pass_tds",
    "rush_att": "player_rush_attempts",
    "rush_yds": "player_rush_yds",
    "recs": "player_receptions",
    "rec_yds": "player_reception_yds",
    # Anytime touchdown scorer. This is the market books actually post for
    # touchdowns, and the one the split rush_td/rec_td keys never returned.
    "any_td": "player_anytime_td",
}
