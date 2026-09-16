/**
 * Edges dashboard, the core PriorLine screen.
 *
 * Answers one question per row: "should I bet this or not?"
 * Line vs. model projection vs. edge vs. win probability, color-coded,
 * filterable by market/tier/side, sortable, paginated.
 *
 * Desktop: dense trading-terminal table. Mobile (<760px): the same table
 * collapses into stacked cards via CSS (data-label attributes).
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import NextSlate from "../components/NextSlate";
import Avatar from "../components/Avatar";
import Select from "../components/Select";
import Glossary from "../components/Glossary";
import TierLedger from "../components/TierLedger";
import Pager from "../components/Pager";
import PageTitle from "../components/PageTitle";
import { fullDateTime, kickoff, price as fmtOdds } from "../lib/format";

import { MARKET_ORDER, isYesNo, marketLabel, sideLabel } from "../lib/markets";
import {
  fetchEdges,
  fetchEdgesSummary,
  type EdgesSummary,
  type EdgeTier,
  type PropEdge,
} from "../api";

// Tiers the board publishes, matching PUBLISHED_TIERS in routes/edges.py.
//
// Medium and small are still computed, stored and graded, and the tier table on
// the track record page needs them there to show why they are not published:
// across 7,125 graded picks medium returned -5.1% and small -4.3%, against
// elite's +4.0%, and on live Week 1 picks strong returned -16.2% against
// elite's +1.1%. Offering them as filters here would just be options that
// always come back empty.
const TIERS: EdgeTier[] = ["elite"];
const PAGE_SIZE = 50;

function fmtGameDate(e: PropEdge): string {
  // Prefer the full kickoff timestamp so the time renders in the viewer's own
  // zone. `game_date` is the UTC calendar date, which pushes any night game to
  // the following day, Monday Night Football displayed as Tuesday.
  return kickoff(e.commence_time ?? e.game_date);
}

/**
 * Collapse the same player+market across bookmakers into one row.
 *
 * The books all price the same prop, so listing each separately showed the
 * identical pick two or three times and pushed genuinely different edges off
 * the first page. The strongest edge leads; the rest are kept so the row can
 * still show where the best number is.
 */
/*
 * Deduplication moved to the API.
 *
 * This used to collapse rows by player, market and game in the browser, which
 * meant the server's count and the visible row count described different
 * things: "Elite 5" sat above three rows, because two of those props were
 * priced at two books. Paging was worse, since a page boundary could land in
 * the middle of a prop. The API now returns one row per prop with the other
 * books in `alts`.
 */

function fmtMatchup(e: PropEdge): string {
  if (!e.home_team || !e.away_team) return "";
  return `${e.away_team} @ ${e.home_team}`;
}


/**
 * What the numbers on this page are, and what they are not.
 *
 * The model projects a distribution and reads a probability off it. That
 * probability is not a track record, and early in a season it is measurably
 * optimistic on the under side: every feature row is built from games played
 * months ago, and no calibration can correct for a season that has not happened
 * yet. Saying so on the page is the difference between a research tool and a
 * tout.
 */
/**
 * A headline count, distinguishing "still loading" from "none".
 *
 * These read `count ?? "..."`, and the summary endpoint leaves a tier out
 * entirely when nothing is in it. So a night with no strong signals showed the
 * loading ellipsis under the Strong card forever, which looks like the page is
 * broken rather than like the honest answer, which is zero.
 */
function statValue(n: number | null | undefined, loading: boolean) {
  if (n !== null && n !== undefined) return n;
  return loading ? "…" : 0;
}

function CalibrationNotice() {
  const [open, setOpen] = useState(false);
  return (
    <aside className="ps-notice">
      <button
        type="button"
        className="ps-notice-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        How much should you trust these numbers?
      </button>
      {open && (
        <div className="ps-notice-body">
          <p>
            The projections are solid. Measured against Week 1 lines they land
            within a few percent of the market on receptions, receiving yards
            and passing touchdowns.
          </p>
          <p>
            The probabilities were the weak part and they have been fixed. The
            model ranks bets correctly, it was just claiming numbers it had not
            earned: 65% where reality was 52%. That mattered more than a wrong
            number on screen, because expected value is the probability minus
            the price&rsquo;s break-even, so an inflated probability meant the
            &ldquo;highest EV&rdquo; filter was quietly selecting losing bets.
            They now run through a calibration fitted on 7,125 graded results.
          </p>
          <p>
            <strong>Only the top tier is recommended.</strong> On out-of-sample
            graded picks elite returns +4.0% per unit, and it is the only tier
            that does: strong sits within a tenth of a point of break-even,
            medium at &minus;5.2% and small at &minus;4.3%. Strong and medium
            are on the board as context, marked as such and never as picks. Week 1 said the same thing on live picks,
            elite at +1.1% against strong at &minus;16.2%. The lower tiers are
            still graded and still on the track record, because a tier table
            that hid its losers would not be worth reading, but they are not
            offered here as bets.
          </p>
          <p>
            <strong>The under side is where the money is.</strong> Choosing on
            2023&ndash;24 and verifying on a 2025 season never used to choose,
            unders returned +2.0% per unit with a slate-clustered 95% interval
            of [&minus;0.6%, +4.2%], which does not clear zero on its own.
            Narrowing to elite and strong unders returns +3.0% with an interval
            of [+0.4%, +5.5%], and elite unders one per player-game returns
            +4.4% at [+0.3%, +8.2%]. Those two clear zero; the broad under side
            does not. The over side lost in both periods and no slice of it
            survived, so overs are held to roughly double the bar before they
            can reach a top tier. Books shade props toward the over because that
            is what the public buys, which is exactly where the price is worst.
          </p>
          <p>
            None of this is a guarantee. A three-point edge on a few thousand
            picks a season takes years to separate from luck, and the effect has
            been shrinking as the market sharpens.
          </p>
        </div>
      )}
    </aside>
  );
}

export default function EdgesDashboard() {
  const [summary, setSummary] = useState<EdgesSummary | null>(null);
  const [edges, setEdges] = useState<PropEdge[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [market, setMarket] = useState("");
  const [minTier, setMinTier] = useState<EdgeTier | "">("");
  // One game at a time, which is how a card for tonight gets built.
  const [eventId, setEventId] = useState("");
  const [side, setSide] = useState<"over" | "under" | "">("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("featured");
  const [bestOnly, setBestOnly] = useState(false);
  /*
   * Exact tier, separate from the dropdown's "and up".
   *
   * The cards and the dropdown mean different things and conflating them is
   * what made "Strong 15" show 20 rows: the dropdown filters strong-and-above,
   * which includes every elite signal too.
   */
  const [exactTier, setExactTier] = useState<EdgeTier | "">("");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);

  // Debounce the search box so we don't hammer the API per keystroke.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    fetchEdgesSummary().then(setSummary).catch(() => setSummary(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    fetchEdges({
      market_code: market || null,
      min_tier: (minTier || null) as EdgeTier | null,
      event_id: eventId || null,
      side: (side || null) as "over" | "under" | null,
      search: debouncedSearch || null,
      sort,
      best_bets_only: bestOnly,
      tier: exactTier || null,
      order,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((r) => {
        if (cancelled) return;
        setEdges(r.edges);
        setTotal(r.total);
      })
      .catch((e) => !cancelled && setErr(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [market, minTier, side, debouncedSearch, sort, order, page, bestOnly, exactTier, eventId]);

  // Reset to page 0 whenever a filter changes.
  useEffect(() => {
    setPage(0);
  }, [market, minTier, side, debouncedSearch, bestOnly, exactTier, eventId]);

  const markets = useMemo(
    () => summary?.by_market.map((m) => m.market_code) ?? [...MARKET_ORDER],
    [summary],
  );


  // Client-side filter for the value pattern. It is a property of the rows
  // already fetched rather than a server filter, because the flag is computed
  // per slate and the board is small enough that paging it server-side would
  // add a round trip for no benefit.
  const cov = summary?.coverage ?? null;

  // From the summary, not from the loaded page. Deriving it from `edges` meant
  // the card counted only the rows currently in memory.
  const bestCount = summary?.best_bets ?? null;

  // Which tiers the API is willing to call a bet. Read from the summary rather
  // than hardcoded, so the server stays the single source of that decision.
  const betTiers = summary?.bet_tiers ?? ["elite"];

  // Unfiltered board size, summed from the per-tier summary.
  const allTiersTotal = useMemo(() => {
    if (!summary) return null;
    return Object.values(summary.by_tier).reduce((a, b) => a + b, 0);
  }, [summary]);

  // How many of those the site is actually recommending.
  //
  // The headline card used to be labelled "Elite Signals" over this same
  // unfiltered total, which was true while elite was the only tier on the
  // board and became a lie the moment it was not: a slate with no elite picks
  // and one strong read "Elite Signals 1". The card counts the board and says
  // separately how much of it is a bet.
  const betCount = useMemo(() => {
    if (!summary) return null;
    return betTiers.reduce(
      (a, t) => a + (summary.by_tier[t as keyof typeof summary.by_tier] ?? 0),
      0,
    );
  }, [summary, betTiers]);
  const showingFiltered =
    allTiersTotal !== null && total !== allTiersTotal;

  function toggleSort(key: string) {
    if (sort === key) {
      setOrder(order === "desc" ? "asc" : "desc");
    } else {
      setSort(key);
      setOrder("desc");
    }
  }

  /*
   * The whole header cell, not just the button inside it.
   *
   * `aria-sort` belongs on the element with the columnheader role, which is the
   * `th`. It was on the button, where it is not a valid attribute and every
   * screen reader ignores it, so the table announced no sort state at all while
   * looking like it did.
   */
  function sortableTh(key: string, label: string) {
    const active = sort === key;
    return (
      <th aria-sort={active ? (order === "desc" ? "descending" : "ascending") : "none"}>
        <button onClick={() => toggleSort(key)} aria-label={`Sort by ${label}`}>
          {label} {active ? (order === "desc" ? "▾" : "▴") : ""}
        </button>
      </th>
    );
  }

  return (
    <>
      <PageTitle lead="Today&rsquo;s" accent="Signals" />
      {/*
        One sentence, where there used to be two paragraphs.

        The second of them defined Model and Edge column by column, which is
        word for word what the glossary directly beneath it says, and the
        glossary is a better place for it: collapsed once you know the terms,
        and one click away when you do not. Between the two paragraphs and the
        coverage note, a stranger met 186 words of prose before the first
        number, on a page whose whole job is to show numbers.
      */}
      <p className="ps-tagline">
        Sportsbook lines against the model&rsquo;s own number.{" "}
        <strong>EV</strong> is the column that matters: anything at or below
        zero is a bet the price already covers.{" "}
        <Link to="/faq">How it works</Link>.
      </p>

      {/* Definitions sit above the board, collapsed. Terms like EV and
          break-even are used here as if they were common knowledge, and a
          number nobody can interpret is worse than no number. */}
      <Glossary />

      {/* The board carries more than it recommends, so it has to say which is
          which before the table, not after it. */}
      {summary?.published_tiers && summary.published_tiers.length > 1 && (
        <TierLedger
          published={summary.published_tiers}
          bet={summary.bet_tiers ?? ["elite"]}
        />
      )}

      {/*
        Say why the board is the size it is.

        A sportsbook posts yardage and reception lines on starters and little
        else, so a large share of the players it prices carry nothing but an
        anytime touchdown, which this site projects but does not price. The
        market's own number predicts scorers better than the model does, so
        there is nothing there to beat.
        have no line to beat. Without saying so the board reads as though the
        model has never heard of half the offence.
      */}
      {cov && cov.players_priced > cov.players_with_market && (
        <p className="ps-coverage-note">
          Books posted a line on <b>{cov.players_priced}</b> players tonight, and
          only <b>{cov.players_with_market}</b> of them on a market worth
          publishing. The other {cov.players_priced - cov.players_with_market}{" "}
          carry an anytime touchdown and nothing else, so there is no line to
          beat. Every one of them still has a number on{" "}
          <Link to="/projections">Projections</Link>.
        </p>
      )}

      <section className="ps-statgrid ps-balance" aria-label="Signal summary">
        {/*
          The tier cards are filters, not decoration.
          Clicking one applies it and clicking it again clears it, which is what
          a number labelled "Elite" sitting above a filterable table implies it
          should do.
        */}
        {/*
          Total Signals is the way back.

          The tier cards each clear themselves on a second click, but once a
          filter is on there was nothing that said "show me everything again"
          except finding the dropdown that set it. The headline count is the
          obvious thing to press, so it clears every filter the cards can set
          and reads as pressed whenever none of them are.
        */}
        {/*
          Pressed only when something is filtered, so it reads as "clear this"
          rather than labelling the default view as a filter.

          It was pressed whenever no filter was set, which put an ACTIVE FILTER
          badge on the headline card of a board nobody had filtered.
        */}
        <button
          type="button"
          className="ps-stat is-reset"
          aria-pressed={Boolean(exactTier || bestOnly || minTier || eventId)}
          onClick={() => {
            setExactTier("");
            setBestOnly(false);
            setMinTier("");
            setEventId("");
          }}
        >
          {/*
            Named for the board, not for a tier. It counts every published
            tier, and the line underneath says how many of those are the tier
            being recommended. Naming it "Elite" over that total was correct
            only while elite was the only tier on the board.
          */}
          <div className="label">On The Board</div>
          {/*
            The summary count, not `total`.

            `total` is however many rows came back under the current filters, so
            with the tier filter on "elite" the card read "Total 17" next to
            "Elite 17" and looked like every signal on the board was elite. The
            summary endpoint is unfiltered, which is what a headline should be.
          */}
          <div className="value">{statValue(allTiersTotal, summary === null)}</div>
          {/*
            Say how much of the slate is priced.
            A count with no denominator is a mystery: 57 reads as a thin week
            until you know it came from five games out of sixteen. A low ratio
            points at the odds sync, not at the model.
          */}
          <div className="sub">
            {showingFiltered
              ? `${total} match your filters`
              : betCount === null
              ? "across every published tier"
              : `${betCount} elite, the tier offered as a bet`}
          </div>
        </button>

        {/*
          Games priced, where Elite and Strong used to be.

          Elite is now the only tier the board publishes, so an Elite card
          counted the same props as Total Signals beside it and a Strong card
          read 0 on every slate of the season. What a reader actually needs in
          that space is the denominator: a thin board on a Tuesday is the
          sportsbooks not having posted yet, not the model going quiet.
        */}
        <div className="ps-stat">
          <div className="label">Games Priced</div>
          <div className="value">
            {cov ? cov.games_priced : statValue(null, summary === null)}
          </div>
          <div className="sub">
            {cov
              ? `of ${cov.games_upcoming} on the slate`
              : "waiting on the odds feed"}
          </div>
        </div>

        <button
          type="button"
          className="ps-stat"
          aria-pressed={bestOnly}
          onClick={() => setBestOnly((v) => !v)}
        >
          <div className="label">Best Bets</div>
          <div className="value amber">{statValue(bestCount, summary === null)}</div>
          <div className="sub">the verified selection</div>
        </button>
      </section>

      <section className="ps-filters" aria-label="Filters">
        <input
          className="grow"
          type="search"
          placeholder="Search player..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search player"
        />
        {/* Game first, because a parlay is built for one game. Labelled with
            the signal count so an empty matchup is obvious before selecting
            it. */}
        <Select
          label="Game"
          value={eventId}
          onChange={setEventId}
          minWidth={260}
          options={[
            { value: "", label: "All games" },
            ...(summary?.by_game ?? []).map((g) => ({
              value: g.event_id,
              label: `${g.away_team} @ ${g.home_team} (${g.count})`,
            })),
          ]}
        />
        <Select
          label="Market"
          value={market}
          onChange={setMarket}
          minWidth={196}
          options={[
            { value: "", label: "All markets" },
            ...markets.map((m) => ({ value: m, label: marketLabel(m) })),
          ]}
        />
        <Select
          label="Minimum tier"
          value={minTier}
          onChange={(v) => setMinTier(v as EdgeTier | "")}
          minWidth={160}
          options={[
            { value: "", label: "All tiers" },
            ...TIERS.map((t) => ({
              value: t,
              label: `${t[0].toUpperCase()}${t.slice(1)} and above`,
            })),
          ]}
        />
        <Select
          label="Side"
          value={side}
          onChange={(v) => setSide(v as "over" | "under" | "")}
          minWidth={168}
          options={[
            { value: "", label: "Over & under" },
            { value: "over", label: "Over only" },
            { value: "under", label: "Under only" },
          ]}
        />
      </section>

      {err && (
        <div role="alert" style={{ color: "var(--red)", margin: "12px 0" }}>
          Failed to load edges: {err}
        </div>
      )}

      <div className="ps-tablewrap">
        <div className="ps-tablescroll">
        <table className="ps-table is-board">
          <caption className="sr-only">
            Betting edges: sportsbook lines versus model projections
          </caption>
          {/* Fixed column widths, so paging or filtering cannot make every
              column shift sideways. See .ps-table in index.css. */}
          <colgroup>
            <col className="c-player" />
            <col className="c-market" />
            <col className="c-num" />
            <col className="c-narrow" />
            <col className="c-narrow" />
            <col className="c-narrow" />
            <col className="c-prob" />
            <col className="c-narrow" />
            <col className="c-tier" />
            <col className="c-book" />
          </colgroup>
          <thead>
            <tr>
              {sortableTh("player_name", "Player")}
              <th>Market</th>
              {sortableTh("line", "Line")}
              {/* Sorts on the median, which is the number this column shows.
                  Sorting on the mean put the rows out of the order they were
                  displayed in wherever the two disagreed. */}
              {sortableTh("projection_median", "Model")}
              {sortableTh("edge", "Edge")}
              {sortableTh("ev_per_unit", "EV")}
              {sortableTh("win_prob", "Win %")}
              <th>Pick</th>
              <th>Tier</th>
              <th>Book</th>
            </tr>
          </thead>
          <tbody>
            {edges.map((e) => {
              const over = e.recommended_side === "over";
              // A row the site is not recommending reads differently, because
              // a table where every row looks the same is a table where every
              // row is a pick. The ledger above says which tiers those are;
              // this is the same statement at the level of the row.
              const isBet = betTiers.includes(e.edge_tier);
              return (
                <tr
                  key={e.id}
                  className={isBet ? undefined : "is-context"}
                >
                  <td data-label="Player">
                    <div className="ps-ident">
                      <Avatar name={e.player_name} src={e.headshot} />
                      <div className="ps-ident-text">
                        {e.player_id ? (
                          <Link to={`/players/${e.player_id}`} className="ps-ident-name"
                                title={e.player_name}>
                            {e.player_name}
                          </Link>
                        ) : (
                          <span className="ps-ident-name" title={e.player_name}>
                            {e.player_name}
                          </span>
                        )}
                        <div className="matchup" title={fmtMatchup(e)}>{fmtMatchup(e)}</div>
                        <div className="ps-gamedate">{fmtGameDate(e)}</div>
                      </div>
                    </div>
                  </td>
                  <td data-label="Market">{marketLabel(e.market_code)}</td>
                  <td data-label="Line" className="num">
                    {e.line ?? "-"}{" "}
                    <span className="matchup">({fmtOdds(e.price_american)})</span>
                  </td>
                  <td
                    data-label="Model"
                    className="num"
                    title={
                      e.projection_median !== null
                        ? `median ${e.projection_median.toFixed(1)}, mean ${e.projection.toFixed(1)}`
                        : undefined
                    }
                  >
                    {/*
                      The median, not the mean.

                      The side comes from the predicted distribution, so the
                      number beside it has to come from the same place. On a
                      right-skewed market the mean sits well above the median,
                      which produced rows reading "model 75.6, line 66.5, pick
                      UNDER". The mean stays in the tooltip: it is the right
                      number for a projection, just not for a pick.
                    */}
                    {isYesNo(e.market_code)
                      ? e.projection.toFixed(2)
                      : (e.projection_median ?? e.projection).toFixed(1)}
                  </td>
                  {/*
                    Always the model's number minus the line.

                    This was signed toward whichever side was picked, so the
                    same 4.06 model against a 3.5 line printed +0.56 on an over
                    and -0.56 on an under, and the sign only repeated the Pick
                    column while looking like it disagreed with it. The pick now
                    follows the median too, so a positive edge is always an over
                    and a negative one always an under.
                  */}
                  <td
                    data-label="Edge"
                    className={`num ${
                      isYesNo(e.market_code) ? "" : e.raw_edge >= 0 ? "pos" : "neg"
                    }`}
                    title={
                      isYesNo(e.market_code)
                        ? "A yes-or-no market has no median to clear the line, so the edge lives entirely in the EV column."
                        : "The model's number minus the line. Positive means the model is above the line, which is why the pick is the over."
                    }
                  >
                    {isYesNo(e.market_code) ? (
                      <span className="matchup">n/a</span>
                    ) : (
                      <>
                        {e.raw_edge > 0 ? "+" : e.raw_edge < 0 ? "−" : ""}
                        {Math.abs(e.raw_edge).toFixed(1)}
                      </>
                    )}
                  </td>
                  <td
                    data-label="EV"
                    className={`num ${(e.ev_per_unit ?? 0) > 0 ? "pos" : "neg"}`}
                    title="Expected profit per unit staked, at this price. The column used to print the model's probability minus the break-even instead, which is a probability edge: worth about twice as much on a plus price as on a heavy minus one, and shown as the same number either way. The tiers are still cut on that probability edge, because that is the quantity their thresholds were measured against."
                  >
                    {e.ev_per_unit === null
                      ? "-"
                      : `${e.ev_per_unit > 0 ? "+" : ""}${(e.ev_per_unit * 100).toFixed(1)}%`}
                  </td>
                  <td data-label="Win %">
                    <span className="prob">
                      <span className="prob-bar" aria-hidden="true">
                        <span style={{ width: `${Math.round((e.win_prob ?? 0) * 100)}%` }} />
                      </span>
                      <span className="num">{Math.round((e.win_prob ?? 0) * 100)}%</span>
                    </span>
                  </td>
                  <td data-label="Pick">
                    <span className={over ? "side-over" : "side-under"}>
                      {sideLabel(e.market_code, e.recommended_side)}
                    </span>
                  </td>
                  <td data-label="Tier">
                    <span className={`tier tier-${e.edge_tier}`}>{e.edge_tier}</span>
                    {e.best_bet && (
                      <span
                        className="tier best-chip"
                        title="The one selection verified profitable on a season never used to choose it: top tier, under side, one pick per player-game. +4.4% per unit, 95% interval [+0.3%, +8.2%] across 1,540 picks."
                      >
                        Best
                      </span>
                    )}
                    {e.value_flag && (
                      <span
                        className="tier value-chip"
                        title="Matches the one pattern that held across 2023, 2024 and 2025: the under on a top-quartile line, where books shade toward the public's overs. About +3.6% over break-even at the best price."
                      >
                        Value
                      </span>
                    )}
                  </td>
                  <td data-label="Book">
                    <span className="book-chip best">
                      {e.bookmaker_title ?? e.bookmaker_key} {e.line ?? "-"}
                    </span>
                    {e.alts.map((a) => (
                      <span className="book-chip" key={a.id}>
                        {a.bookmaker_title ?? a.bookmaker_key} {a.line ?? "-"}
                      </span>
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
        {!loading && edges.length === 0 && !err && (
          /* An empty board and an over-narrow filter are different problems and
             used to give the same message. For most of the week the board is
             empty because no game is close enough to kickoff to have been
             priced yet, and telling somebody their filters matched nothing
             sends them hunting through filters that were never the issue. */
          <div className="ps-noboard">
            {/* h2, not h3. These sit directly under the page h1, and skipping a
                level makes a screen reader's heading outline claim a missing
                section above them. */}
            <h2>
              {allTiersTotal !== 0
                ? "No signals match these filters"
                : cov && cov.games_in_progress > 0
                ? "Tonight's board is done"
                : cov && cov.games_priced > 0
                ? "No elite picks today"
                : "No prices posted yet"}
            </h2>
            <p>
              {allTiersTotal === 0
                ? cov && cov.games_in_progress > 0
                  ? `${cov.games_in_progress} game${
                      cov.games_in_progress === 1 ? " is" : "s are"
                    } under way. Picks come down at kickoff so nothing here is ever stale, and the next slate is priced as it approaches.`
                  : cov && cov.games_priced > 0
                  ? `${cov.games_priced} of ${cov.games_upcoming} games are priced and none of them clear the bar. Elite is the only tier that has made money, so on a day like this the honest answer is that there is nothing to bet.`
                  : cov && cov.games_upcoming > 0
                  ? `${cov.games_upcoming} game${
                      cov.games_upcoming === 1 ? "" : "s"
                    } scheduled, none priced yet. Lines are bought close to kickoff, so the board fills in as each game approaches.`
                  : "No games are scheduled in the next eight days."
                : "Widen a filter, or clear them all, to see the rest of the board."}
            </p>
            {allTiersTotal === 0 && (
              /* Somewhere to go. A blank table is a dead end, and the two
                 things worth reading on a quiet day are what the model expects
                 from the next slate and how it has done when it did have an
                 opinion. */
              <div className="actions">
                <Link className="ps-link" to="/projections">
                  Every player&rsquo;s projection
                </Link>
                <Link className="ps-link" to="/record">
                  How the model has done
                </Link>
                <Link className="ps-link" to="/faq">
                  Why a pick has to clear a bar
                </Link>
              </div>
            )}
          </div>
        )}
        {/* An empty board still has something to say: every player in the next
            slate is already projected, so show that rather than a dead table.
            Only when the board is genuinely empty, not when a filter is. */}
        {!loading && edges.length === 0 && !err && allTiersTotal === 0 && (
          <NextSlate />
        )}
        {loading && edges.length === 0 && (
          <div style={{ padding: 1 }}>
            {/* Skeleton rows rather than the word "Loading". The table keeps its
                shape, so the page doesn't collapse and snap back on every
                filter change. */}
            {Array.from({ length: 6 }, (_, i) => (
              <div className="ps-skeleton-row" key={i} />
            ))}
          </div>
        )}
      </div>

      {/* No pager under an empty board. "0 signals" beside a Prev and a Next
          that go nowhere reads as a broken table rather than a quiet day. */}
      {total > 0 && (
      <Pager
        total={total}
        page={page}
        pageSize={PAGE_SIZE}
        noun="signal"
        onPage={setPage}
        updatedAt={summary?.last_updated ? fullDateTime(summary.last_updated) : null}
      />
      )}

      {/*
        The honesty panel lives at the bottom.
        It is context for the table above it, and sitting between the heading and
        the data it read like a warning label stapled to the front of the page.
      */}
      <CalibrationNotice />
    </>
  );
}
