/**
 * This week's projections for one player, priced or not.
 *
 * The player page only ever rendered `PropCards`, which is driven by
 * `prop_edges` and therefore only exists for players a sportsbook has posted a
 * line on. Books price a few dozen players a week out of roughly a thousand, so
 * opening Justin Jefferson in Week 1 showed "no props posted" and nothing else,
 * on a site whose entire output is a projection for every player.
 *
 * This renders the model's actual output: a number and a range for every market
 * the player's position can produce. `PropCards` still sits above it and
 * handles the priced ones, where there is a line to compare against.
 */

import { useEffect, useState } from "react";
import { fetchPlayerProjections, type Projection } from "../api";
import { fullDate as fmtDate } from "../lib/format";
import { marketLabel } from "../lib/markets";

/** Counting stats read better with a decimal, yardage as whole numbers. */
function fmt(v: number | null | undefined, market: string): string {
  if (v === null || v === undefined) return "-";
  return market.endsWith("_yds") ? v.toFixed(0) : v.toFixed(1);
}

/**
 * The predicted range, drawn.
 *
 * A point estimate on its own hides the thing that matters most: 60 yards with
 * a 20-to-110 spread is a completely different proposition from 60 with a
 * 45-to-75 spread, and only one of those is worth acting on.
 */
function Range({ p }: { p: Projection }) {
  const lo = p.p10;
  const hi = p.p90;
  if (lo === null || hi === null || hi <= lo) return null;
  const pos = ((p.projection - lo) / (hi - lo)) * 100;
  return (
    <div className="ps-wp-range">
      <span className="ps-wp-track">
        <span className="ps-wp-fill" />
        <span
          className="ps-wp-dot"
          style={{ left: `${Math.max(0, Math.min(100, pos))}%` }}
        />
      </span>
      <span className="ps-wp-nums">
        {fmt(lo, p.market_code)} to {fmt(hi, p.market_code)}
      </span>
    </div>
  );
}

export default function WeekProjections({ playerId }: { playerId: number }) {
  const [rows, setRows] = useState<Projection[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    fetchPlayerProjections(playerId)
      .then((res) => !cancelled && setRows(res.projections))
      .catch((e) => !cancelled && setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [playerId]);

  if (loading) {
    return (
      <div className="ps-section">
        <h3>This Week</h3>
        <div className="ps-empty">Loading projections...</div>
      </div>
    );
  }
  if (err) {
    return (
      <div className="ps-section">
        <h3>This Week</h3>
        <div className="ps-empty">Could not load projections: {err}</div>
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="ps-section">
        <h3>This Week</h3>
        <div className="ps-empty">
          No game scheduled for this player in the next two weeks.
        </div>
      </div>
    );
  }

  const game = rows[0];

  return (
    <div className="ps-section">
      <h3>This Week</h3>
      <p className="ps-tagline">
        The model's projection for every market this player's position can
        produce, whether or not a sportsbook has posted a line.
        {game.opponent ? ` ${fmtDate(game.game_date)} vs ${game.opponent}.` : ""}
      </p>
      <div className="ps-wp-grid">
        {rows.map((r, i) => (
          <div
            className="ps-wp-card ps-row-in"
            key={`${r.market_code}-${r.game_date}`}
            style={{ animationDelay: `${Math.min(i, 10) * 30}ms` }}
          >
            <div className="ps-wp-label">
              {marketLabel(r.market_code)}
            </div>
            <div className="ps-wp-value">{fmt(r.projection, r.market_code)}</div>
            <Range p={r} />
          </div>
        ))}
      </div>
    </div>
  );
}
