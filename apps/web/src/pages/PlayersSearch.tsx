/**
 * Player directory page.
 *
 * This page fetches a (bounded) list of players from the API and applies a simple
 * client-side filter based on the search query. If you later implement a true
 * server-side search endpoint (e.g., /players/search?q=...), you can swap the
 * filtering logic to reduce bandwidth and improve perceived performance.
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPlayers } from "../api";
import type { Player } from "../api";

/**
 * Render a searchable list of players.
 *
 * Behavior:
 * - On initial load, fetches up to 500 players (configurable) from the backend.
 * - As the user types, filters the in-memory list by name/team/position.
 *
 * Failure modes:
 * - If the API is down or returns a non-2xx response, an error message is shown.
 * - If the list is empty (no matches), a "No results" row is rendered.
 */
export default function PlayersSearch() {
  const [query, setQuery] = useState("");
  const [players, setPlayers] = useState<Player[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const q = useMemo(() => query.trim().toLowerCase(), [query]);

  const filtered = useMemo(() => {
    if (!q) return players;
    return players.filter((p) => {
      const name = `${p.first_name} ${p.last_name}`.toLowerCase();
      const team = (p.team ?? "").toLowerCase();
      const pos = (p.position ?? "").toLowerCase();
      return name.includes(q) || team.includes(q) || pos.includes(q);
    });
  }, [players, q]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoading(true);
      setErr(null);
      try {
        // Fetch a reasonably large page for client-side filtering.
        // If your player table grows, replace this with a server-side search endpoint.
        const data = await fetchPlayers(500, 0);
        if (!cancelled) setPlayers(data);
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
  }, []);

  return (
    <div style={{ maxWidth: 900, margin: "40px auto", padding: 16 }}>
      <h1 style={{ fontSize: 28, marginBottom: 8 }}>Player Search</h1>
      <p style={{ marginTop: 0, opacity: 0.8 }}>
        Filter by name, team, or position. (API: /players)
      </p>

      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="e.g., jefferson, allen, WR, MIN"
        style={{
          width: "100%",
          padding: "12px 14px",
          fontSize: 16,
          borderRadius: 10,
          border: "1px solid #333",
          marginTop: 10,
          marginBottom: 18,
        }}
      />

      {loading && <div>Loading…</div>}
      {err && <div style={{ color: "crimson" }}>{err}</div>}

      <div style={{ border: "1px solid #333", borderRadius: 12, overflow: "hidden" }}>
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

        {filtered.map((p) => (
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

        {!loading && filtered.length === 0 && (
          <div style={{ padding: 12, opacity: 0.8 }}>No results.</div>
        )}
      </div>
    </div>
  );
}
