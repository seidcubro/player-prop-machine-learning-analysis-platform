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

import { displayProjection, marketLabel } from "../lib/markets";
import TdCompare from "./TdCompare";

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
  /*
   * A market with no spread still gets the row.
   *
   * Touchdown markets come back with a tenth of a score and an interval of
   * nothing, so this returned null and that card alone lost its bottom two
   * lines. In a grid of equal-height cards that reads as one card having
   * failed to load, and the fix is not to hide the answer but to state it: the
   * model has this player at a single outcome.
   */
  if (lo === null || hi === null || hi <= lo) {
    return (
      <div className="ps-wp-range is-flat">
        <span className="ps-wp-track" />
        <span className="ps-wp-nums">no spread to speak of</span>
      </div>
    );
  }
  const pos = ((displayProjection(p) - lo) / (hi - lo)) * 100;
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
        <h2>This Week</h2>
        <div className="ps-empty">Loading projections...</div>
      </div>
    );
  }
  if (err) {
    return (
      <div className="ps-section">
        <h2>This Week</h2>
        <div className="ps-empty">Could not load projections: {err}</div>
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="ps-section">
        <h2>This Week</h2>
        <div className="ps-empty">
          No game scheduled for this player in the next two weeks.
        </div>
      </div>
    );
  }

  const game = rows[0];

  return (
    <div className="ps-section">
      <h2>This Week</h2>
      <p className="ps-tagline">
        The model's projection for every market this player's position can
        produce, whether or not a sportsbook has posted a line.
        {game.opponent ? ` ${fmtDate(game.game_date)} vs ${game.opponent}.` : ""}
      </p>
      <div className="ps-wp-grid">
        {rows.map((r) => (
          <div
            className="ps-wp-card"
            key={`${r.market_code}-${r.game_date}`}
          >
            <div className="ps-wp-label">
              {marketLabel(r.market_code)}
            </div>
            <div className="ps-wp-value">
              {fmt(displayProjection(r), r.market_code)}
            </div>
            {/* Anytime touchdown gets the model and the book side by side
                instead of a range, which for a yes-or-no market is just 0 to
                1. Never a pick: see TdCompare. */}
            {r.market_code === "any_td" ? <TdCompare r={r} /> : <Range p={r} />}
          </div>
        ))}
      </div>
    </div>
  );
}
