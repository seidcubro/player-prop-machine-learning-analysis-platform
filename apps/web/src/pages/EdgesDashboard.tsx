/**
 * Edges dashboard — the core PropSignal screen.
 *
 * Answers one question per row: "should I bet this or not?"
 * Line vs. model projection vs. edge vs. win probability, color-coded,
 * filterable by market/tier/side, sortable, paginated.
 *
 * Desktop: dense trading-terminal table. Mobile (<760px): the same table
 * collapses into stacked cards via CSS (data-label attributes).
 */

import { useEffect, useMemo, useState } from "react";
import {
  fetchEdges,
  fetchEdgesSummary,
  type EdgesSummary,
  type EdgeTier,
  type PropEdge,
} from "../api";

const MARKET_LABELS: Record<string, string> = {
  rec_yds: "Receiving Yards",
  recs: "Receptions",
  rec_td: "Receiving TDs",
  rush_yds: "Rushing Yards",
  rush_att: "Rush Attempts",
  rush_td: "Rushing TDs",
  pass_yds: "Passing Yards",
  pass_att: "Pass Attempts",
  pass_completions: "Completions",
  pass_td: "Passing TDs",
};

const TIERS: EdgeTier[] = ["elite", "strong", "medium", "small"];
const PAGE_SIZE = 50;

function fmtPrice(p: number | null): string {
  if (p === null || p === undefined) return "—";
  return p > 0 ? `+${p}` : String(p);
}

function fmtMatchup(e: PropEdge): string {
  if (!e.home_team || !e.away_team) return "";
  return `${e.away_team} @ ${e.home_team}`;
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
  const [sort, setSort] = useState("edge");
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
  }, [market, minTier, side, debouncedSearch, sort, order, page]);

  // Reset to page 0 whenever a filter changes.
  useEffect(() => {
    setPage(0);
  }, [market, minTier, side, debouncedSearch]);

  const markets = useMemo(
    () => summary?.by_market.map((m) => m.market_code) ?? Object.keys(MARKET_LABELS),
    [summary],
  );

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

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
      <h1>Today's Edges</h1>
      <p className="ps-tagline">
        Sportsbook lines vs. PropSignal model projections. Green means the model likes the
        over; red means the under.
      </p>

      <section className="ps-statgrid" aria-label="Edge summary">
        <div className="ps-stat">
          <div className="label">Total Edges</div>
          <div className="value">{summary ? total || "…" : "…"}</div>
        </div>
        <div className="ps-stat">
          <div className="label">Elite</div>
          <div className="value green">{summary?.by_tier.elite ?? "…"}</div>
        </div>
        <div className="ps-stat">
          <div className="label">Strong</div>
          <div className="value">{summary?.by_tier.strong ?? "…"}</div>
        </div>
        <div className="ps-stat">
          <div className="label">Markets</div>
          <div className="value">{summary?.by_market.length ?? "…"}</div>
        </div>
      </section>

      <section className="ps-filters" aria-label="Filters">
        <input
          className="grow"
          type="search"
          placeholder="Search player…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search player"
        />
        <select value={market} onChange={(e) => setMarket(e.target.value)} aria-label="Market">
          <option value="">All markets</option>
          {markets.map((m) => (
            <option key={m} value={m}>
              {MARKET_LABELS[m] ?? m}
            </option>
          ))}
        </select>
        <select
          value={minTier}
          onChange={(e) => setMinTier(e.target.value as EdgeTier | "")}
          aria-label="Minimum tier"
        >
          <option value="">All tiers</option>
          {TIERS.map((t) => (
            <option key={t} value={t}>
              {t === "small" ? "small +" : `${t} +`}
            </option>
          ))}
        </select>
        <select
          value={side}
          onChange={(e) => setSide(e.target.value as "over" | "under" | "")}
          aria-label="Side"
        >
          <option value="">Over &amp; under</option>
          <option value="over">Over only</option>
          <option value="under">Under only</option>
        </select>
      </section>

      {err && (
        <div role="alert" style={{ color: "var(--red)", margin: "12px 0" }}>
          Failed to load edges: {err}
        </div>
      )}

      <div className="ps-tablewrap">
        <table className="ps-table">
          <caption style={{ position: "absolute", clip: "rect(0 0 0 0)" }}>
            Betting edges: sportsbook lines versus model projections
          </caption>
          <thead>
            <tr>
              <th>{sortLabel("player_name", "Player")}</th>
              <th>Market</th>
              <th>{sortLabel("line", "Line")}</th>
              <th>{sortLabel("projection", "Model")}</th>
              <th>{sortLabel("edge", "Edge")}</th>
              <th>{sortLabel("win_prob", "Win %")}</th>
              <th>Pick</th>
              <th>Tier</th>
              <th>Book</th>
            </tr>
          </thead>
          <tbody>
            {edges.map((e) => {
              const over = e.recommended_side === "over";
              return (
                <tr key={e.id}>
                  <td data-label="Player">
                    <strong>{e.player_name}</strong>
                    <div className="matchup">{fmtMatchup(e)}</div>
                  </td>
                  <td data-label="Market">{MARKET_LABELS[e.market_code] ?? e.market_code}</td>
                  <td data-label="Line" className="num">
                    {e.line ?? "—"}{" "}
                    <span className="matchup">({fmtPrice(e.price_american)})</span>
                  </td>
                  <td data-label="Model" className="num">
                    {e.projection.toFixed(1)}
                  </td>
                  <td data-label="Edge" className={`num ${over ? "pos" : "neg"}`}>
                    {over ? "+" : "−"}
                    {Math.abs(e.raw_edge).toFixed(1)}
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
                  </td>
                  <td data-label="Book" className="matchup">
                    {e.bookmaker_title ?? e.bookmaker_key}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!loading && edges.length === 0 && !err && (
          <div className="ps-empty">No edges match these filters.</div>
        )}
        {loading && <div className="ps-empty">Loading…</div>}
      </div>

      <div className="ps-pager">
        <span aria-live="polite">
          {total} edges · page {page + 1} of {pages}
          {summary?.last_updated && ` · updated ${summary.last_updated.slice(0, 16)}`}
        </span>
        <span style={{ display: "flex", gap: 8 }}>
          <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
            ← Prev
          </button>
          <button
            onClick={() => setPage((p) => Math.min(pages - 1, p + 1))}
            disabled={page >= pages - 1}
          >
            Next →
          </button>
        </span>
      </div>
    </>
  );
}
