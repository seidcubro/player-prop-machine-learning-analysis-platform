/**
 * One played game, opened from the game log: what we projected against what
 * actually happened, and whether the pick won.
 *
 * This is the answer to the first question anybody asks about a projection,
 * and until now the site could not give it. The board only ever showed the
 * upcoming slate, and those rows are wiped on every rebuild, so a number
 * disappeared at exactly the moment it became checkable. A player we projected
 * but never bet left no trace at all.
 *
 * Markets with no pick are shown anyway. "We said 6.5 catches, he caught 9, we
 * did not bet it" is the honest line, and hiding it would make the page a
 * highlight reel of the bets we happened to place.
 */
import { useEffect, useState } from "react";
import { fetchPlayerGameDetail, type GameDetail } from "../api";
import { marketLabel } from "../lib/markets";

function fmt(v: number | null | undefined, dp = 1) {
  return v === null || v === undefined ? "-" : Number(v).toFixed(dp);
}

export default function GameBreakdown({
  playerId,
  gameDate,
}: {
  playerId: number;
  gameDate: string;
}) {
  const [data, setData] = useState<GameDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    fetchPlayerGameDetail(playerId, gameDate)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [playerId, gameDate]);

  if (loading) return <div className="ps-empty">Loading that game...</div>;
  if (err) return <div className="ps-empty">Could not load that game: {err}</div>;
  if (!data) return null;

  if (data.markets.length === 0) {
    return (
      <div className="ps-note">
        No stored projection for this game yet. Projections have only been kept
        since the history table was added, and older games are filled in by a
        backfill that has not run for this player.
      </div>
    );
  }

  return (
    <div className="ps-tablewrap">
      <table className="ps-table is-matrix">
        <caption className="sr-only">Projection against result</caption>
        <thead>
          <tr>
            <th scope="col">Market</th>
            <th scope="col" className="num">Our number</th>
            <th scope="col" className="num">Actual</th>
            <th scope="col" className="num">Miss</th>
            <th scope="col" className="num">Line</th>
            <th scope="col">Pick</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {data.markets.map((m) => {
            const ours = m.p50 ?? m.projection;
            const miss =
              ours !== null && m.actual !== null ? m.actual - ours : null;
            const pick = m.pick;
            return (
              <tr key={m.market_code}>
                <td data-label="Market">{marketLabel(m.market_code)}</td>
                <td data-label="Our number" className="num">{fmt(ours)}</td>
                <td data-label="Actual" className="num">{fmt(m.actual)}</td>
                <td
                  data-label="Miss"
                  className={`num ${miss === null ? "" : miss > 0 ? "pos" : "neg"}`}
                  title="Actual minus our number. Positive means he beat what we said."
                >
                  {miss === null ? "-" : `${miss > 0 ? "+" : ""}${miss.toFixed(1)}`}
                </td>
                <td data-label="Line" className="num">
                  {pick ? fmt(pick.line) : "-"}
                </td>
                <td data-label="Pick">
                  {pick ? (
                    <>
                      <span className={`tier tier-${pick.edge_tier}`}>
                        {pick.edge_tier}
                      </span>{" "}
                      {pick.recommended_side}
                      {pick.price_american !== null && (
                        <span className="ps-dim">
                          {" "}
                          {pick.price_american > 0 ? "+" : ""}
                          {pick.price_american}
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="ps-dim">not bet</span>
                  )}
                </td>
                <td data-label="Result">
                  {!pick ? (
                    <span className="ps-dim">-</span>
                  ) : pick.hit === null ? (
                    "push"
                  ) : pick.hit ? (
                    <span className="pos">won</span>
                  ) : (
                    <span className="neg">lost</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
