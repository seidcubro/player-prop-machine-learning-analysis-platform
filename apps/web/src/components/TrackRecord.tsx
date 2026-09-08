/**
 * A player's graded pick history.
 *
 * Every edge the platform publishes carries a win probability. This is the page
 * that holds those numbers to account: each past pick is shown next to what the
 * player actually did, marked won or lost, with the realised hit rate compared
 * against the average probability we quoted. If the model is honest those two
 * numbers should sit close together.
 *
 * Pushes (result landed exactly on the line) are shown but excluded from the
 * hit rate, the same way a sportsbook settles them.
 */

import { useEffect, useState } from "react";
import { fetchEdgeHistory, type EdgeHistory } from "../api";
import { marketLabelShort, sideLabel } from "../lib/markets";

function fmtDate(d: string | null): string {
  if (!d) return "-";
  const dt = new Date(`${d}T00:00:00`);
  return dt.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export default function TrackRecord({ playerId }: { playerId: number }) {
  const [data, setData] = useState<EdgeHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);

    fetchEdgeHistory(playerId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [playerId]);

  if (loading) {
    return (
      <div className="ps-section">
        <h3>Track Record</h3>
        <div className="ps-empty">Loading history...</div>
      </div>
    );
  }

  if (err) {
    return (
      <div className="ps-section">
        <h3>Track Record</h3>
        <div className="ps-empty">Could not load history: {err}</div>
      </div>
    );
  }

  const s = data?.summary;
  const rows = data?.history ?? [];

  if (!s || s.graded === 0) {
    return (
      <div className="ps-section">
        <h3>Track Record</h3>
        <div className="ps-empty">
          No graded picks for this player yet. Results appear once a game we
          posted an edge on has been played.
        </div>
      </div>
    );
  }

  const hitPct = s.hit_rate === null ? null : s.hit_rate * 100;
  const predPct = s.avg_predicted === null ? null : s.avg_predicted * 100;

  return (
    <div className="ps-section">
      <h3>Track Record</h3>

      <div className="ps-statgrid">
        <div className="ps-stat">
          <div className="label">Hit Rate</div>
          <div className="ps-record">
            <span className={`big ${hitPct !== null && hitPct >= 50 ? "value green" : ""}`}>
              {hitPct === null ? "-" : `${hitPct.toFixed(0)}%`}
            </span>
            <span className="wl">
              {s.wins}W–{s.losses}L{s.pushes > 0 ? ` · ${s.pushes} push` : ""}
            </span>
          </div>
        </div>
        <div className="ps-stat">
          <div className="label">Model Predicted</div>
          <div className="value">{predPct === null ? "-" : `${predPct.toFixed(0)}%`}</div>
        </div>
        <div className="ps-stat">
          <div className="label">Graded Picks</div>
          <div className="value">{s.graded}</div>
        </div>
      </div>

      <div className="ps-tablewrap">
        <table className="ps-table">
          <caption className="sr-only">
            Past picks for this player with results
          </caption>
          <thead>
            <tr>
              <th scope="col">Game</th>
              <th scope="col">Market</th>
              <th scope="col">Line</th>
              <th scope="col">Pick</th>
              <th scope="col">Model</th>
              <th scope="col">Actual</th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.edge_id}>
                <td data-label="Game">
                  <div className="ps-gamedate">{fmtDate(r.game_date)}</div>
                  {r.away_team && r.home_team && (
                    <div className="matchup">
                      {r.away_team} @ {r.home_team}
                    </div>
                  )}
                </td>
                <td data-label="Market">{marketLabelShort(r.market_code)}</td>
                <td data-label="Line" className="num">
                  {r.line ?? "-"}
                </td>
                <td data-label="Pick">
                  <span
                    className={
                      r.recommended_side === "over" ? "side-over" : "side-under"
                    }
                  >
                    {sideLabel(r.market_code, r.recommended_side)}
                  </span>
                </td>
                <td data-label="Model" className="num">
                  {r.projection.toFixed(1)}
                </td>
                <td data-label="Actual" className="num">
                  {r.actual === null ? "-" : r.actual.toFixed(0)}
                </td>
                <td data-label="Result">
                  {r.hit === null ? (
                    <span className="hit-push">PUSH</span>
                  ) : r.hit ? (
                    <span className="hit-yes">WON</span>
                  ) : (
                    <span className="hit-no">LOST</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
