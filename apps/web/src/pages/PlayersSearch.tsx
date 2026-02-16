/**
 * Player directory page (server-side search + pagination).
 *
 * This page calls:
 *   GET /api/v1/players?search=...&limit=...&offset=...&include_total=true
 *
 * Why:
 * - The player table is league-scale now; client-side filtering is no longer viable.
 * - Pagination and server-side search keep the UI fast and bandwidth-light.
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPlayersPaged } from "../api";
import type { Player } from "../api";

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);

  return debounced;
}

export default function PlayersSearch() {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);

  const [players, setPlayers] = useState<Player[]>([]);
  const [total, setTotal] = useState<number | null>(null);

  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const q = useMemo(() => debouncedQuery.trim(), [debouncedQuery]);

  // Reset to first page when search changes
  useEffect(() => {
    setOffset(0);
  }, [q]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoading(true);
      setErr(null);
      try {
        const resp = await fetchPlayersPaged({
          search: q || null,
          limit,
          offset,
          include_total: true,
        });
        if (cancelled) return;
        setPlayers(resp.players);
        setTotal(typeof resp.total === "number" ? resp.total : null);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message ?? "Error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [q, limit, offset]);

  const canPrev = offset > 0;
  const canNext = total !== null ? offset + limit < total : players.length === limit;

  const start = total === null ? null : Math.min(offset + 1, total);
  const end = total === null ? null : Math.min(offset + players.length, total);

  return (
    <div style={{ maxWidth: 900, margin: "40px auto", padding: 16 }}>
      <h1 style={{ fontSize: 28, marginBottom: 8 }}>Player Search</h1>
      <p style={{ marginTop: 0, opacity: 0.8 }}>
        Server-side search + pagination. (API: /players?search=...&limit=...&offset=...)
      </p>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 10 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g., warren, jefferson, allen, WR, MIN"
          style={{
            flex: 1,
            padding: "12px 14px",
            fontSize: 16,
            borderRadius: 10,
            border: "1px solid #333",
          }}
        />

        <select
          value={limit}
          onChange={(e) => setLimit(parseInt(e.target.value, 10))}
          style={{
            padding: "10px 12px",
            borderRadius: 10,
            border: "1px solid #333",
            background: "transparent",
            color: "inherit",
          }}
        >
          <option value={25}>25</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
        </select>
      </div>

      <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div style={{ opacity: 0.85 }}>
          {total !== null ? (
            <span>
              Showing {start}–{end} of {total}
            </span>
          ) : (
            <span>Showing {players.length}</span>
          )}
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => setOffset((o) => Math.max(0, o - limit))}
            disabled={!canPrev || loading}
            style={{
              padding: "8px 10px",
              borderRadius: 10,
              border: "1px solid #333",
              background: "transparent",
              color: "inherit",
              opacity: !canPrev || loading ? 0.5 : 1,
              cursor: !canPrev || loading ? "not-allowed" : "pointer",
            }}
          >
            Prev
          </button>
          <button
            onClick={() => setOffset((o) => o + limit)}
            disabled={!canNext || loading}
            style={{
              padding: "8px 10px",
              borderRadius: 10,
              border: "1px solid #333",
              background: "transparent",
              color: "inherit",
              opacity: !canNext || loading ? 0.5 : 1,
              cursor: !canNext || loading ? "not-allowed" : "pointer",
            }}
          >
            Next
          </button>
        </div>
      </div>

      {loading && <div style={{ marginTop: 12 }}>Loading...</div>}
      {err && <div style={{ marginTop: 12, color: "crimson" }}>{err}</div>}

      <div style={{ border: "1px solid #333", borderRadius: 12, overflow: "hidden", marginTop: 14 }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "2fr 1fr 1fr 120px",
            padding: 12,
            fontWeight: 700,
            borderBottom: "1px solid #333",
          }}
        >
          <div>Name</div>
          <div>Team</div>
          <div>Pos</div>
          <div></div>
        </div>

        {players.map((p) => (
          <div
            key={p.id}
            style={{
              display: "grid",
              gridTemplateColumns: "2fr 1fr 1fr 120px",
              padding: 12,
              borderBottom: "1px solid #222",
              alignItems: "center",
            }}
          >
            <div>
              {p.first_name} {p.last_name}
            </div>
            <div>{p.team ?? "-"}</div>
            <div>{p.position ?? "-"}</div>
            <div>
              <Link to={`/players/${p.id}`}>View</Link>
            </div>
          </div>
        ))}

        {!loading && !err && players.length === 0 && (
          <div style={{ padding: 12, opacity: 0.8 }}>No results.</div>
        )}
      </div>
    </div>
  );
}



