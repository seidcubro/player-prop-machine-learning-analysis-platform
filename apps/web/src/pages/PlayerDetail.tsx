/**
 * Player detail page.
 *
 * Displays:
 * - player identity metadata (team/position)
 * - latest baseline projection (if present in `projections`)
 * - latest ML projection (generated on demand via `/projection_ml`)
 * - recent ML projection history (rows stored in `ml_projections`)
 *
 * This page is intentionally tolerant of missing data:
 * - If baseline projections are not generated yet, it shows a friendly message.
 * - If ML artifacts/features are not available yet, it explains the required pipeline steps.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fetchPlayer,
  fetchProjectionBaseline,
  fetchProjectionML,
  fetchMLProjectionHistory,
  type Player,
  type Projection,
  type MLProjection,
  type MLProjectionRow,
} from "../api";

/**
 * Default market selection heuristic used by the UI.
 *
 * The backend supports multiple markets, but the UI starts with a sensible default
 * based on position so the page is immediately useful without requiring extra UI
 * controls.
 */
const DEFAULT_MARKET_BY_POS: Record<string, string> = {
  WR: "rec_yds",
  TE: "rec_yds",
  RB: "rush_yds",
  QB: "pass_yds",
};

/**
 * Render the player detail experience.
 *
 * Data flow:
 * - Load player metadata (`/players/{id}`)
 * - Attempt baseline projection (`/projection_baseline`)
 * - Attempt ML projection + history (`/projection_ml`, `/ml_projections`)
 *
 * Failure modes:
 * - If the player id is invalid, an inline error is shown.
 * - If optional endpoints fail, the page renders without that section.
 */
export default function PlayerDetail() {
  const { id } = useParams();
  const playerId = Number(id);

  const [player, setPlayer] = useState<Player | null>(null);

  const [baseline, setBaseline] = useState<Projection | null>(null);
  const [ml, setMl] = useState<MLProjection | null>(null);
  const [mlRows, setMlRows] = useState<MLProjectionRow[]>([]);

  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const market_code = useMemo(() => {
    const pos = (player?.position ?? "").toUpperCase();
    return DEFAULT_MARKET_BY_POS[pos] ?? "rec_yds";
  }, [player?.position]);

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setLoading(true);
      setErr(null);

      try {
        const p = await fetchPlayer(playerId);
        if (cancelled) return;
        setPlayer(p);

        const market = DEFAULT_MARKET_BY_POS[(p.position ?? "").toUpperCase()] ?? "rec_yds";

        // Baseline projection (stored row; may not exist yet)
        try {
          const b = await fetchProjectionBaseline({
            playerId,
            market_code: market,
            model_name: "baseline_v1",
          });
          if (!cancelled) setBaseline(b);
        } catch {
          if (!cancelled) setBaseline(null);
        }

        // ML projection (runs inference, stores row; may fail if artifacts/features missing)
        try {
          const m = await fetchProjectionML({
            playerId,
            market_code: market,
            lookback: 5,
          });
          if (!cancelled) setMl(m);
        } catch {
          if (!cancelled) setMl(null);
        }

        // ML history (optional)
        try {
          const rows = await fetchMLProjectionHistory({
            playerId,
            market_code: market,
            model_name: "ridge_v1",
            lookback: 5,
            limit: 20,
          });
          if (!cancelled) setMlRows(rows);
        } catch {
          if (!cancelled) setMlRows([]);
        }
      } catch (e: any) {
        if (!cancelled) setErr(e?.message ?? "Error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    if (!Number.isFinite(playerId)) {
      setErr("Invalid player id");
      return;
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [playerId]);

  return (
    <div style={{ maxWidth: 900, margin: "40px auto", padding: 16 }}>
      <Link to="/">← Back</Link>

      {loading && <div style={{ marginTop: 16 }}>Loading…</div>}
      {err && <div style={{ marginTop: 16, color: "crimson", whiteSpace: "pre-wrap" }}>{err}</div>}

      {player && (
        <div style={{ marginTop: 16 }}>
          <div style={{ border: "1px solid #333", borderRadius: 12, padding: 16 }}>
            <h1 style={{ fontSize: 28, margin: 0 }}>
              {player.first_name} {player.last_name}
            </h1>
            <div style={{ marginTop: 8, opacity: 0.85, display: "flex", gap: 18, flexWrap: "wrap" }}>
              <div><b>Team:</b> {player.team ?? "-"}</div>
              <div><b>Position:</b> {player.position ?? "-"}</div>
              <div><b>DB ID:</b> {player.id}</div>
              <div><b>Market:</b> {market_code}</div>
            </div>
          </div>

          <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "1fr", gap: 16 }}>
            {/* Baseline */}
            <div style={{ border: "1px solid #333", borderRadius: 12, padding: 16 }}>
              <h3 style={{ marginTop: 0 }}>Baseline Projection</h3>
              {!baseline && (
                <div style={{ opacity: 0.8 }}>
                  No baseline projection found for this player/market yet.
                </div>
              )}
              {baseline && (
                <>
                  <div style={{ opacity: 0.85, marginBottom: 10 }}>
                    <b>Game:</b> {baseline.game_date} vs {baseline.opponent} •{" "}
                    <b>Model:</b> {baseline.model_name ?? baseline.model ?? "baseline_v1"}
                  </div>
                  <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
                    <div><b>Mean:</b> {Number(baseline.mean).toFixed(1)}</div>
                    <div><b>Std Dev:</b> {Number(baseline.stddev).toFixed(1)}</div>
                    <div>
                      <b>Over %:</b>{" "}
                      <span
                        style={{
                          fontWeight: 800,
                          color:
                            baseline.p_over >= 0.55
                              ? "limegreen"
                              : baseline.p_over <= 0.45
                              ? "crimson"
                              : "gold",
                        }}
                      >
                        {(baseline.p_over * 100).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                </>
              )}
            </div>

            {/* ML */}
            <div style={{ border: "1px solid #333", borderRadius: 12, padding: 16 }}>
              <h3 style={{ marginTop: 0 }}>ML Projection (Ridge)</h3>
              {!ml && (
                <div style={{ opacity: 0.8 }}>
                  No ML projection found yet. To enable this market, run:
                  <div style={{ marginTop: 8 }}>
                    <code>POST /jobs/build_features</code> → <code>POST /jobs/attach_labels</code> → training
                  </div>
                </div>
              )}
              {ml && (
                <>
                  <div style={{ opacity: 0.85, marginBottom: 10 }}>
                    <b>As of:</b> {ml.as_of_game_date} vs {ml.opponent} •{" "}
                    <b>Market:</b> {ml.market_code} • <b>Lookback:</b> {ml.lookback} •{" "}
                    <b>Model:</b> {ml.model_name}
                  </div>

                  <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
                    <div><b>Prediction:</b> {Number(ml.prediction).toFixed(1)}</div>
                    <div><b>mean:</b> {Number(ml.features.mean).toFixed(1)}</div>
                    <div><b>stddev:</b> {Number(ml.features.stddev).toFixed(2)}</div>
                    <div><b>weighted_mean:</b> {Number(ml.features.weighted_mean).toFixed(1)}</div>
                    <div><b>trend:</b> {Number(ml.features.trend).toFixed(3)}</div>
                  </div>
                </>
              )}
            </div>

            {/* ML history */}
            <div style={{ border: "1px solid #333", borderRadius: 12, padding: 16 }}>
              <h3 style={{ marginTop: 0 }}>ML Projection History</h3>
              {mlRows.length === 0 && <div style={{ opacity: 0.8 }}>No rows yet.</div>}
              {mlRows.length > 0 && (
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ textAlign: "left" }}>
                        <th style={{ padding: 10, borderBottom: "1px solid #222" }}>As Of</th>
                        <th style={{ padding: 10, borderBottom: "1px solid #222" }}>Pred</th>
                        <th style={{ padding: 10, borderBottom: "1px solid #222" }}>Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mlRows.map((r, idx) => (
                        <tr key={idx}>
                          <td style={{ padding: 10, borderBottom: "1px solid #111" }}>{r.as_of_game_date}</td>
                          <td style={{ padding: 10, borderBottom: "1px solid #111" }}>{Number(r.prediction).toFixed(2)}</td>
                          <td style={{ padding: 10, borderBottom: "1px solid #111" }}>{r.created_at}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
