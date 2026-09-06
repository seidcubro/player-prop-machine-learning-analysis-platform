/**
 * Player browse.
 *
 * Defaults to offensive players with recent game activity, the ~600 people
 * who actually have prop markets, rather than the full 25k-row historical
 * table, which stretches back decades and opened on long-retired nose tackles.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Avatar from "../components/Avatar";
import { fetchPlayersPaged, type Player } from "../api";

const PAGE_SIZE = 48;
const OFFENSE = "QB,RB,WR,TE,FB";

const POSITION_FILTERS = [
  { label: "All", value: OFFENSE },
  { label: "QB", value: "QB" },
  { label: "RB", value: "RB,FB" },
  { label: "WR", value: "WR" },
  { label: "TE", value: "TE" },
];

export default function PlayersSearch() {
  const [players, setPlayers] = useState<Player[]>([]);
  const [total, setTotal] = useState<number | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [positions, setPositions] = useState(OFFENSE);
  const [page, setPage] = useState(0);

  // Debounce so typing does not fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => setPage(0), [debounced, positions]);

  const reqId = useRef(0);
  useEffect(() => {
    const mine = ++reqId.current;
    setLoading(true);
    setErr(null);

    fetchPlayersPaged({
      search: debounced || undefined,
      positions,
      active_only: true,
      include_total: true,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((res) => {
        // Ignore a slow response that a newer query has already superseded.
        if (mine !== reqId.current) return;
        setPlayers(res.players);
        setTotal(res.total);
      })
      .catch((e) => {
        if (mine !== reqId.current) return;
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (mine === reqId.current) setLoading(false);
      });
  }, [debounced, positions, page]);

  const shown = useMemo(
    () => ({
      from: total === 0 ? 0 : page * PAGE_SIZE + 1,
      to: Math.min((page + 1) * PAGE_SIZE, total ?? 0),
    }),
    [page, total],
  );

  const lastPage = total ? Math.max(0, Math.ceil(total / PAGE_SIZE) - 1) : 0;

  return (
    <div>
      <div className="ps-hero">
        <h1>Players</h1>
        <p>
          Every skill-position player with an active role. Open one for live
          props, projections, and how our picks have actually done.
        </p>
      </div>

      <div className="ps-filters">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or team..."
          aria-label="Search players"
        />
        <select
          value={positions}
          onChange={(e) => setPositions(e.target.value)}
          aria-label="Filter by position"
        >
          {POSITION_FILTERS.map((p) => (
            <option key={p.label} value={p.value}>
              {p.label === "All" ? "All positions" : p.label}
            </option>
          ))}
        </select>
      </div>

      {err && <div className="ps-empty">Could not load players: {err}</div>}

      {loading && players.length === 0 && (
        <div className="ps-empty">Loading players...</div>
      )}

      {!loading && !err && players.length === 0 && (
        <div className="ps-empty">
          No players match “{search}”.
        </div>
      )}

      {players.length > 0 && (
        <>
          <div className="ps-playergrid">
            {players.map((p) => {
              const name = p.name ?? p.display_name ?? "Unknown";
              return (
                <Link key={p.id} to={`/players/${p.id}`} className="ps-playercard">
                  <Avatar name={name} src={p.headshot} />
                  <div>
                    <div style={{ fontWeight: 600 }}>{name}</div>
                    <div className="meta">
                      {p.position ?? "-"}
                      {p.team ? ` · ${p.team}` : ""}
                      {p.jersey_number ? ` · #${p.jersey_number}` : ""}
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>

          <div className="ps-pager">
            <span>
              {shown.from}–{shown.to}
              {total !== undefined ? ` of ${total}` : ""}
            </span>
            <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
              Prev
            </button>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={total !== undefined && page >= lastPage}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
