/**
 * Prop cards for a single player.
 *
 * The dashboard answers "what are today's best bets?" across everyone; this
 * answers "what should I do with *this* player?", one card per market, each
 * showing the book's line against the model's projection, the edge between
 * them, and how confident the model is.
 *
 * A player's props arrive from several bookmakers with slightly different
 * lines, so cards are grouped by market and only the strongest edge per market
 * is shown, with the remaining books listed underneath for line shopping.
 */

import { useEffect, useState } from "react";
import Sparkline from "./Sparkline";
import { fetchEdges, fetchPlayerGames, type PropEdge, type PlayerGame } from "../api";

const MARKET_LABELS: Record<string, string> = {
  rec_yds: "Receiving Yards",
  recs: "Receptions",
  rec_td: "Receiving TDs",
  rush_yds: "Rushing Yards",
  rush_att: "Rush Attempts",
  rush_td: "Rushing TDs",
  pass_yds: "Passing Yards",
  pass_att: "Pass Attempts",
  pass_completions: "Completions",
  pass_td: "Passing TDs",
};

function fmtPrice(p: number | null): string {
  if (p === null || p === undefined) return "-";
  return p > 0 ? `+${p}` : String(p);
}

/** Decimals that suit the market: yardage reads as whole numbers, counts don't. */
function fmtNum(v: number, market: string): string {
  return market.endsWith("_yds") ? v.toFixed(0) : v.toFixed(1);
}

/**
 * Keep the single highest-conviction edge per market.
 *
 * Ordering is by |edge| rather than win probability so the card matches how the
 * dashboard ranks rows, and ties fall back to the book offering the better price.
 */
function bestPerMarket(edges: PropEdge[]): Map<string, PropEdge[]> {
  const byMarket = new Map<string, PropEdge[]>();
  for (const e of edges) {
    const list = byMarket.get(e.market_code) ?? [];
    list.push(e);
    byMarket.set(e.market_code, list);
  }
  for (const [, list] of byMarket) {
    list.sort((a, b) => Math.abs(b.raw_edge) - Math.abs(a.raw_edge));
  }
  return byMarket;
}

/** Which game-log field backs each market, for the recent-form sparkline. */
const MARKET_STAT: Record<string, keyof PlayerGame> = {
  rec_yds: "receiving_yards",
  recs: "receptions",
  rush_yds: "rushing_yards",
  rush_att: "rush_attempts",
  pass_yds: "passing_yards",
  pass_td: "passing_tds",
};

export default function PropCards({
  playerName,
  playerId,
}: {
  playerName: string;
  playerId?: number;
}) {
  const [games, setGames] = useState<PlayerGame[]>([]);
  const [edges, setEdges] = useState<PropEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // Recent games power the form sparkline. Failure is non-fatal: the cards
  // still answer the bet question without it.
  useEffect(() => {
    if (!playerId) return;
    let cancelled = false;
    fetchPlayerGames(playerId, 8)
      .then((g) => !cancelled && setGames(g))
      .catch(() => !cancelled && setGames([]));
    return () => {
      cancelled = true;
    };
  }, [playerId]);

  useEffect(() => {
    if (!playerName.trim()) return;
    let cancelled = false;
    setLoading(true);
    setErr(null);

    fetchEdges({ search: playerName, limit: 100, sort: "edge", order: "desc" })
      .then((res) => {
        if (!cancelled) setEdges(res.edges);
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
  }, [playerName]);

  if (loading) {
    return (
      <div className="ps-section">
        <h3>Live Props</h3>
        <div className="ps-empty">Loading props...</div>
      </div>
    );
  }

  if (err) {
    return (
      <div className="ps-section">
        <h3>Live Props</h3>
        <div className="ps-empty">Could not load props: {err}</div>
      </div>
    );
  }

  const byMarket = bestPerMarket(edges);

  if (byMarket.size === 0) {
    return (
      <div className="ps-section">
        <h3>Live Props</h3>
        <div className="ps-empty">
          No sportsbook props posted for this player in the current odds snapshot.
        </div>
      </div>
    );
  }

  return (
    <div className="ps-section">
      <h3>Live Props</h3>
      <div className="ps-propgrid">
        {[...byMarket.entries()].map(([market, list]) => {
          const top = list[0];
          const over = top.recommended_side === "over";
          const others = list.slice(1);

          return (
            <div
              key={market}
              className={`ps-propcard ${over ? "over" : "under"}`}
            >
              <div className="market">
                <h4>{MARKET_LABELS[market] ?? market}</h4>
                <span className={`tier tier-${top.edge_tier}`}>
                  {top.edge_tier}
                </span>
              </div>

              <div className="nums">
                <div>
                  <div className="label">Line</div>
                  <div className="value">
                    {top.line === null ? "-" : fmtNum(top.line, market)}
                  </div>
                </div>
                <div>
                  <div className="label">Model</div>
                  <div className="value">
                    {fmtNum(top.projection, market)}
                  </div>
                </div>
                <div>
                  <div className="label">Edge</div>
                  <div className={`value ${over ? "green" : "red"}`}>
                    {top.raw_edge >= 0 ? "+" : ""}
                    {fmtNum(top.raw_edge, market)}
                  </div>
                </div>
              </div>

              <div className="rec">
                <span>
                  <span className={over ? "side-over" : "side-under"}>
                    {top.recommended_side}
                  </span>{" "}
                  {top.bookmaker_title ?? top.bookmaker_key}{" "}
                  {fmtPrice(top.price_american)}
                </span>
                {top.win_prob !== null && (
                  <span className="prob">
                    <span className="prob-bar">
                      <span style={{ width: `${top.win_prob * 100}%` }} />
                    </span>
                    {(top.win_prob * 100).toFixed(0)}%
                  </span>
                )}
              </div>

              {(() => {
                const key = MARKET_STAT[market];
                if (!key) return null;
                // Oldest-to-newest so the line reads left to right.
                const vals = [...games]
                  .reverse()
                  .map((g) => Number(g[key] ?? 0))
                  .filter((v) => Number.isFinite(v));
                if (vals.length < 3) return null;
                const hits =
                  top.line === null
                    ? null
                    : vals.filter((v) =>
                        over ? v > top.line! : v < top.line!,
                      ).length;
                return (
                  <div className="form">
                    <div>
                      <div className="caption">Last {vals.length}</div>
                      {hits !== null && (
                        <div className="hits">
                          <b>
                            {hits}/{vals.length}
                          </b>{" "}
                          hit this side
                        </div>
                      )}
                    </div>
                    <Sparkline
                      values={vals}
                      line={top.line}
                      side={over ? "over" : "under"}
                    />
                  </div>
                );
              })()}

              {others.length > 0 && (
                <div className="rec" style={{ borderTop: "none", paddingTop: 4 }}>
                  <span>
                    Other books:{" "}
                    {others
                      .map(
                        (o) =>
                          `${o.bookmaker_title ?? o.bookmaker_key} ${
                            o.line === null ? "-" : fmtNum(o.line, market)
                          }`,
                      )
                      .join(" · ")}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
