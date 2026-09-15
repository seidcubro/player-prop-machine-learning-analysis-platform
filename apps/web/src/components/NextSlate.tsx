/**
 * What the model thinks about the next slate, for when nothing is priced yet.
 *
 * Prices are bought close to kickoff because the Odds API bills per event per
 * market, so for most of the week there is no upcoming priced game and the
 * signals table has nothing in it. An empty table is an accurate answer to
 * "which bets are worth taking right now" and a terrible answer to "is this
 * site working", and it throws away the fact that every player in the next
 * slate is already projected.
 *
 * So the board falls back to the model's own numbers, with no line beside them
 * because there is no line yet. That is the honest framing: these are
 * projections, not picks, and the distinction is the whole product.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Avatar from "./Avatar";
import { fetchProjections, type Projection } from "../api";
import { marketLabel } from "../lib/markets";

/** Markets worth leading with: the ones books price most often. */
const PREVIEW_MARKETS = ["rec_yds", "rush_yds", "pass_yds"] as const;

function fmt(v: number, market: string): string {
  return market.endsWith("_yds") ? v.toFixed(0) : v.toFixed(1);
}

export default function NextSlate() {
  const [rows, setRows] = useState<Projection[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all(
      PREVIEW_MARKETS.map((m) =>
        fetchProjections({
          market_code: m,
          starters_only: true,
          sort: "projection",
          order: "desc",
          limit: 4,
        })
          .then((r) => r.projections)
          .catch(() => [] as Projection[]),
      ),
    )
      .then((lists) => {
        if (cancelled) return;
        // Interleave, so the panel leads with one of each market rather than
        // four receivers before the first quarterback.
        const out: Projection[] = [];
        for (let i = 0; i < 4; i++) {
          for (const list of lists) if (list[i]) out.push(list[i]);
        }
        setRows(out);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading || rows.length === 0) return null;

  const day = rows.find((r) => r.game_date)?.game_date ?? null;

  return (
    <div className="ps-nextslate">
      <div className="head">
        <div>
          <h3>Next up</h3>
          <p>
            No lines posted yet, so there is nothing to bet against. These are
            the model&rsquo;s own numbers for
            {day
              ? ` ${new Date(day + "T12:00:00").toLocaleDateString(undefined, {
                  weekday: "long",
                  month: "short",
                  day: "numeric",
                })}`
              : " the next slate"}
            . Picks appear here once a sportsbook prices the game.
          </p>
        </div>
        <Link className="ps-linkbtn" to="/projections">
          Every player
        </Link>
      </div>

      <div className="rows">
        {rows.map((r) => (
          <Link
            key={`${r.player_id}-${r.market_code}`}
            to={r.app_player_id ? `/players/${r.app_player_id}` : "/projections"}
            className="row"
          >
            <Avatar name={r.player_name} src={r.headshot} size="" />
            <div className="who">
              <div className="name">{r.player_name}</div>
              <div className="sub">
                {r.team ?? ""}
                {r.opponent ? ` vs ${r.opponent}` : ""}
              </div>
            </div>
            <div className="market">{marketLabel(r.market_code)}</div>
            <div className="num">{fmt(r.p50 ?? r.projection, r.market_code)}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
