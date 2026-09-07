/**
 * Edges dashboard, the core PropSignal screen.
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
import Avatar from "../components/Avatar";
import Select from "../components/Select";
import Glossary from "../components/Glossary";
import Pager from "../components/Pager";
import PageTitle from "../components/PageTitle";
import { fullDateTime, kickoff, price as fmtOdds } from "../lib/format";
import { MARKET_ORDER, isYesNo, marketLabel } from "../lib/markets";
import {
  fetchEdges,
  fetchEdgesSummary,
  type EdgesSummary,
  type EdgeTier,
  type PropEdge,
} from "../api";

const TIERS: EdgeTier[] = ["elite", "strong", "medium", "small"];
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
            They now run through a calibration fitted on 5,791 graded results.
          </p>
          <p>
            <strong>The tiers rank.</strong> On out-of-sample graded picks,
            elite returns +4.7% per unit, down through strong, medium and small
            at &minus;4.5%. Before the fix elite made money while strong and
            medium each lost more than 5%, which is not a ranking.
          </p>
          <p>
            <strong>The under side is where the money is.</strong> Choosing on
            2023&ndash;24 and verifying on a 2025 season never used to choose,
            unders returned +4.4% per unit with a slate-clustered 95% interval
            of [+1.8%, +7.2%]. Elite unders, one per player-game, returned
            +7.1%. The over side lost in both periods and no slice of it
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
  }, [market, minTier, side, debouncedSearch, sort, order, page, bestOnly, exactTier]);

  // Reset to page 0 whenever a filter changes.
  useEffect(() => {
    setPage(0);
  }, [market, minTier, side, debouncedSearch, bestOnly, exactTier]);

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

  // Unfiltered board size, summed from the per-tier summary.
  const allTiersTotal = useMemo(() => {
    if (!summary) return null;
    return Object.values(summary.by_tier).reduce((a, b) => a + b, 0);
  }, [summary]);
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

  function sortLabel(key: string, label: string) {
    const active = sort === key;
    return (
      <button
        onClick={() => toggleSort(key)}
        aria-sort={active ? (order === "desc" ? "descending" : "ascending") : undefined}
        aria-label={`Sort by ${label}`}
      >
        {label} {active ? (order === "desc" ? "▾" : "▴") : ""}
      </button>
    );
  }

  return (
    <>
      <PageTitle lead="Today&rsquo;s" accent="Signals" />
      <p className="ps-tagline">
        Sportsbook lines against the model&rsquo;s own number. <strong>EV</strong>{" "}
        is the column that matters: the model&rsquo;s probability minus the
        break-even the price demands. Anything at or below zero is a bet the
        price already covers.
      </p>
      <p className="ps-tagline">
        <strong>Model</strong> is the median outcome, not the average, because
        the pick is chosen from the same distribution.{" "}
        <strong>Edge</strong> is how far that median clears the line{" "}
        <em>in the direction of the pick</em>, so green means the model&rsquo;s
        own number backs the bet and red means it does not and the bet rests on
        the price alone. Both happen, and the difference is worth seeing.
        Anytime touchdown settles yes or no rather than at a number, so it has
        no median to compare and its edge reads n/a.{" "}
        <a href="/faq">How It Works</a>.
      </p>

      {/* Definitions sit above the board, collapsed. Terms like EV and
          break-even are used here as if they were common knowledge, and a
          number nobody can interpret is worse than no number. */}
      <Glossary />

      <section className="ps-statgrid" aria-label="Signal summary">
        {/*
          The tier cards are filters, not decoration.
          Clicking one applies it and clicking it again clears it, which is what
          a number labelled "Elite" sitting above a filterable table implies it
          should do.
        */}
        <div className="ps-stat">
          <div className="label">Total Signals</div>
          {/*
            The summary count, not `total`.

            `total` is however many rows came back under the current filters, so
            with the tier filter on "elite" the card read "Total 17" next to
            "Elite 17" and looked like every signal on the board was elite. The
            summary endpoint is unfiltered, which is what a headline should be.
          */}
          <div className="value">{allTiersTotal ?? "..."}</div>
          {/*
            Say how much of the slate is priced.
            A count with no denominator is a mystery: 57 reads as a thin week
            until you know it came from five games out of sixteen. A low ratio
            points at the odds sync, not at the model.
          */}
          <div className="sub">
            {showingFiltered
              ? `${total} match your filters`
              : cov
                ? `${cov.games_priced} of ${cov.games_upcoming} games priced`
                : "across the slate"}
          </div>
        </div>

        <button
          type="button"
          className="ps-stat"
          aria-pressed={exactTier === "elite"}
          onClick={() => setExactTier(exactTier === "elite" ? "" : "elite")}
        >
          <div className="label">Elite</div>
          <div className="value green">{summary?.by_tier.elite ?? "..."}</div>
          <div className="sub">highest expected value</div>
        </button>

        <button
          type="button"
          className="ps-stat"
          aria-pressed={exactTier === "strong"}
          onClick={() => setExactTier(exactTier === "strong" ? "" : "strong")}
        >
          <div className="label">Strong</div>
          <div className="value">{summary?.by_tier.strong ?? "..."}</div>
          <div className="sub">next tier down</div>
        </button>

        <button
          type="button"
          className="ps-stat"
          aria-pressed={bestOnly}
          onClick={() => setBestOnly((v) => !v)}
        >
          <div className="label">Best Bets</div>
          <div className="value amber">{bestCount ?? "..."}</div>
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
        <table className="ps-table">
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
              <th>{sortLabel("player_name", "Player")}</th>
              <th>Market</th>
              <th>{sortLabel("line", "Line")}</th>
              <th>{sortLabel("projection", "Model")}</th>
              <th>{sortLabel("edge", "Edge")}</th>
              <th>{sortLabel("expected_value", "EV")}</th>
              <th>{sortLabel("win_prob", "Win %")}</th>
              <th>Pick</th>
              <th>Tier</th>
              <th>Book</th>
            </tr>
          </thead>
          <tbody>
            {edges.map((e, i) => {
              const over = e.recommended_side === "over";
              return (
                <tr
                  key={e.id}
                  className="ps-row-in"
                  // Staggered entrance, capped at twelve rows. Past that the
                  // delay stops reading as motion and starts reading as lag.
                  style={{ animationDelay: `${Math.min(i, 12) * 26}ms` }}
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
                    Show the real sign, not the side's sign.

                    This used to print `Math.abs(raw_edge)` with a plus for
                    overs and a minus for unders, so the sign only ever told you
                    which side was picked, which the Pick column already says.
                    `raw_edge` is now a signed quantity: how far the median
                    clears the line in the direction of the pick. Positive means
                    the model's own number supports the pick. Negative means it
                    does not, and the bet is being justified by the price alone,
                    which is legitimate but worth being able to see.
                  */}
                  <td
                    data-label="Edge"
                    className={`num ${
                      isYesNo(e.market_code) ? "" : e.raw_edge >= 0 ? "pos" : "neg"
                    }`}
                    title={
                      isYesNo(e.market_code)
                        ? "A yes-or-no market has no median to clear the line, so the edge lives entirely in the EV column."
                        : e.raw_edge >= 0
                          ? "The median clears the line in the direction of this pick."
                          : "The median does not support this pick. It qualifies on price alone, so check the EV column."
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
                    className={`num ${(e.expected_value ?? 0) > 0 ? "pos" : "neg"}`}
                    title="Model probability minus the break-even this price demands. Anything at or below zero is a bet the price already covers."
                  >
                    {e.expected_value === null
                      ? "-"
                      : `${e.expected_value > 0 ? "+" : ""}${(e.expected_value * 100).toFixed(1)}%`}
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
                      {e.recommended_side}
                    </span>
                  </td>
                  <td data-label="Tier">
                    <span className={`tier tier-${e.edge_tier}`}>{e.edge_tier}</span>
                    {e.best_bet && (
                      <span
                        className="tier best-chip"
                        title="The one selection verified profitable on a season never used to choose it: top tier, under side, one pick per player-game. +7.1% per unit, 95% interval [+1.6%, +13.2%]."
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
          <div className="ps-empty">No edges match these filters.</div>
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

      <Pager
        total={total}
        page={page}
        pageSize={PAGE_SIZE}
        noun="signal"
        onPage={setPage}
        updatedAt={summary?.last_updated ? fullDateTime(summary.last_updated) : null}
      />

      {/*
        The honesty panel lives at the bottom.
        It is context for the table above it, and sitting between the heading and
        the data it read like a warning label stapled to the front of the page.
      */}
      <CalibrationNotice />
    </>
  );
}
