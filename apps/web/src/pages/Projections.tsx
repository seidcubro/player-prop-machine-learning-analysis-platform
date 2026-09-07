/**
 * Projections for the whole slate.
 *
 * The edges screen only shows players a sportsbook has priced, which is a few
 * dozen. This shows every skill player and quarterback with a game, which is the
 * actual output of the model. Line or no line, there's a number for everyone.
 *
 * Each row carries the predicted range, not just the point estimate. A back
 * projected for 60 yards with a 20 to 110 spread is a different proposition from
 * one projected for 60 with a 45 to 75 spread, and the point estimate alone
 * hides that completely.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Avatar from "../components/Avatar";
import Select from "../components/Select";
import Pager from "../components/Pager";
import PageTitle from "../components/PageTitle";
import { shortDate as fmtDate } from "../lib/format";
import { fetchProjections, type Projection } from "../api";
import { MARKET_OPTIONS, marketLabel } from "../lib/markets";

const PAGE_SIZE = 50;


const POSITIONS = ["", "QB", "RB", "WR", "TE"];

/** Counting stats read better with a decimal, yardage as whole numbers. */
function fmt(v: number | null, market: string): string {
  if (v === null || v === undefined) return "-";
  return market.endsWith("_yds") || market.endsWith("_att") ||
    market === "pass_completions"
    ? v.toFixed(market.endsWith("_yds") ? 0 : 1)
    : v.toFixed(1);
}

/**
 * Draw the predicted range as a bar, with the point estimate marked.
 *
 * Reading five percentile columns as numbers is work. Seeing that one player's
 * band is twice as wide as another's is instant.
 */
function RangeBar({ p: row }: { p: Projection }) {
  const lo = row.p10;
  const hi = row.p90;
  if (lo === null || hi === null || hi <= lo) return <span className="matchup">-</span>;

  const span = hi - lo;
  const mid = ((row.projection - lo) / span) * 100;
  return (
    <span className="ps-range" title={`p10 ${lo.toFixed(0)} to p90 ${hi.toFixed(0)}`}>
      <span className="ps-range-track">
        <span className="ps-range-fill" />
        <span
          className="ps-range-dot"
          style={{ left: `${Math.max(0, Math.min(100, mid))}%` }}
        />
      </span>
      <span className="ps-range-nums">
        {lo.toFixed(0)}-{hi.toFixed(0)}
      </span>
    </span>
  );
}

export default function Projections() {
  const [rows, setRows] = useState<Projection[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [market, setMarket] = useState("rec_yds");
  const [position, setPosition] = useState("");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [startersOnly, setStartersOnly] = useState(false);
  const [page, setPage] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => setPage(0), [market, position, debounced, startersOnly]);

  const reqId = useRef(0);
  useEffect(() => {
    const mine = ++reqId.current;
    setLoading(true);
    setErr(null);
    fetchProjections({
      market_code: market,
      position: position || undefined,
      search: debounced || undefined,
      starters_only: startersOnly,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((res) => {
        if (mine !== reqId.current) return;
        setRows(res.projections);
        setTotal(res.total);
      })
      .catch((e) => {
        if (mine !== reqId.current) return;
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (mine === reqId.current) setLoading(false);
      });
  }, [market, position, debounced, startersOnly, page]);


  return (
    <>
      <div className="ps-hero">
        <PageTitle lead="Weekly" accent="Projections" />
        <p>
          Every skill player and quarterback with a game this week, whether or not
          a sportsbook has posted a line. The range is the model's 10th to 90th
          percentile, not a guess at the spread.
        </p>
      </div>

      <div className="ps-filters">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search player..."
          aria-label="Search players"
        />
        <Select
          label="Market"
          value={market}
          onChange={setMarket}
          minWidth={200}
          options={MARKET_OPTIONS.map((m) => ({ value: m.code, label: m.label }))}
        />
        <Select
          label="Position"
          value={position}
          onChange={setPosition}
          minWidth={160}
          options={POSITIONS.map((p) => ({
            value: p,
            label: p || "All positions",
          }))}
        />
        <label className="ps-toggle">
          <input
            type="checkbox"
            checked={startersOnly}
            onChange={(e) => setStartersOnly(e.target.checked)}
          />
          Starters only
        </label>
      </div>

      {err && <div className="ps-empty">Could not load projections: {err}</div>}

      <div className="ps-tablewrap">
        <div className="ps-tablescroll">
        <table className="ps-table">
          <caption className="sr-only">
            Model projections for every eligible player
          </caption>
          <thead>
            <tr>
              <th scope="col">Player</th>
              <th scope="col">Game</th>
              <th scope="col">{marketLabel(market)}</th>
              <th scope="col">Range (p10-p90)</th>
              <th scope="col">Depth</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${r.player_id}-${r.market_code}-${r.game_date}`}
                className="ps-row-in"
                style={{ animationDelay: `${Math.min(i, 12) * 26}ms` }}
              >
                <td data-label="Player">
                  <div className="ps-ident">
                    <Avatar name={r.player_name} src={r.headshot} />
                    <div className="ps-ident-text">
                      {r.app_player_id ? (
                        <Link to={`/players/${r.app_player_id}`} className="ps-ident-name"
                              title={r.player_name}>
                          {r.player_name}
                        </Link>
                      ) : (
                        <span className="ps-ident-name" title={r.player_name}>
                          {r.player_name}
                        </span>
                      )}
                      <div className="matchup">
                        {r.position}
                        {r.team ? ` · ${r.team}` : ""}
                      </div>
                    </div>
                  </div>
                </td>
                <td data-label="Game">
                  <div className="ps-gamedate">{fmtDate(r.game_date)}</div>
                  <div className="matchup">vs {r.opponent ?? "-"}</div>
                </td>
                <td data-label={marketLabel(market)} className="num">
                  <strong>{fmt(r.projection, r.market_code)}</strong>
                </td>
                <td data-label="Range">
                  <RangeBar p={r} />
                </td>
                <td data-label="Depth">
                  {r.depth_rank ? (
                    <span className={`pos-chip${r.is_starter ? " starter" : ""}`}>
                      {r.depth_rank === 1 ? "starter" : `#${r.depth_rank}`}
                    </span>
                  ) : (
                    <span className="matchup">-</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        {loading && <div className="ps-empty">Loading...</div>}
        {/* No script names in user-facing copy. Telling a visitor to run
            build_projections.py is an instruction they cannot follow and a
            detail they should not have to know. */}
        {!loading && !err && rows.length === 0 && (
          <div className="ps-empty">
            No projections match these filters.
          </div>
        )}
      </div>

      <Pager
        total={total}
        page={page}
        pageSize={PAGE_SIZE}
        noun="projection"
        onPage={setPage}
      />
    </>
  );
}
