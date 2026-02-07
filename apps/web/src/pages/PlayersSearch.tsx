import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchPlayers } from "../api";
import type { Player } from "../api";

export default function PlayersSearch() {
  const [query, setQuery] = useState("");
  const [players, setPlayers] = useState<Player[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const q = useMemo(() => query.trim(), [query]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoading(true);
      setErr(null);
      try {
        const data = await fetchPlayers(q.length ? q : undefined);
        if (!cancelled) setPlayers(data);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message ?? "Error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    const t = setTimeout(run, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q]);

  return (
    <div style={{ maxWidth: 900, margin: "40px auto", padding: 16 }}>
      <h1 style={{ fontSize: 28, marginBottom: 8 }}>Player Search</h1>
      <p style={{ marginTop: 0, opacity: 0.8 }}>
        Search by name, team, or position. (API: /players)
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

        {!loading && players.length === 0 && (
          <div style={{ padding: 12, opacity: 0.8 }}>No results.</div>
        )}
      </div>
    </div>
  );
}
