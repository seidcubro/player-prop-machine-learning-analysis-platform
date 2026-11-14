/**
 * How each tier on this board has actually done.
 *
 * The board shows several tiers and puts one forward. That distinction is the
 * whole point of showing the others, and it is only worth anything if it is
 * impossible to miss, so this sits directly above the table and posts each
 * tier's real account: picks graded, the share that won, and the share that
 * had to win at the prices offered.
 *
 * It leads with whether the picks were right. Won sits beside needed and the
 * difference is the verdict. Units are the small column at the end, because a
 * tier is a promise about accuracy before it is anything else.
 *
 * Every figure is read from the record. None of it is typed in, and neither
 * is the sentence about which tiers cleared their price: it is worked out
 * from the rows, so it cannot go on claiming something the record has stopped
 * supporting.
 */

import { useEffect, useState } from "react";
import { fetchTierRecord, fetchTodayTiers, type TierRecord } from "../api";
import { Acct } from "./Acct";

const LABEL: Record<string, string> = {
  elite: "Elite",
  strong: "Strong",
  medium: "Medium",
  small: "Small",
};

const COUNT = ["No", "One", "Two", "Three", "Four"];

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "";
  return `${(v * 100).toFixed(1)}%`;
}

function diff(r: TierRecord): number | null {
  return r.hit_rate === null || r.breakeven === null
    ? null
    : r.hit_rate - r.breakeven;
}

function names(rows: TierRecord[]): string {
  const n = rows.map((r) => LABEL[r.edge_tier] ?? r.edge_tier);
  if (n.length <= 1) return n.join("");
  return `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}`;
}

export default function TierLedger({
  published,
  bet,
}: {
  published: string[];
  bet: string[];
}) {
  const [tiers, setTiers] = useState<TierRecord[] | null>(null);

  useEffect(() => {
    let dead = false;
    // Each tier's account under the rule that is assigning the badges on
    // this board. The labels as published are the fallback, for a database
    // where that has not been stored yet.
    fetchTodayTiers()
      .catch(() => null)
      .then((t) => (t ? { tiers: t.rows } : fetchTierRecord()))
      .then((r) => !dead && setTiers(r.tiers))
      .catch(() => !dead && setTiers([]));
    return () => {
      dead = true;
    };
  }, []);

  if (!tiers || tiers.length === 0) return null;

  const rows = published
    .map((t) => tiers.find((r) => r.edge_tier === t))
    .filter((r): r is TierRecord => Boolean(r) && (r as TierRecord).picks > 0);
  if (rows.length === 0) return null;

  const betting = rows.filter((r) => bet.includes(r.edge_tier));
  const context = rows.filter((r) => !bet.includes(r.edge_tier));
  const cleared = rows.filter((r) => (diff(r) ?? -1) > 0);
  const short = rows.filter((r) => (diff(r) ?? 0) <= 0);

  return (
    <section className="ps-ledger" aria-labelledby="ps-ledger-h">
      <h2 id="ps-ledger-h">
        {COUNT[rows.length] ?? rows.length} {rows.length === 1 ? "tier" : "tiers"}{" "}
        on this board.{" "}
        {betting.length > 0 && (
          <>
            {names(betting)} {betting.length === 1 ? "is" : "are"} the{" "}
            {betting.length === 1 ? "one" : "ones"} put forward.
          </>
        )}
      </h2>
      <p>
        A tier is judged on one thing: whether its picks won more often than
        the prices required.{" "}
        {cleared.length > 0
          ? `${names(cleared)} ${cleared.length === 1 ? "has" : "have"} cleared that mark over the graded record.`
          : "No tier on this board has cleared that mark over the graded record."}{" "}
        {short.length > 0 &&
          `${names(short)} ${short.length === 1 ? "has" : "have"} not.`}{" "}
        {context.length > 0 && (
          <>
            {names(context)} {context.length === 1 ? "is" : "are"} shown
            because {context.length === 1 ? "it is" : "they are"} what the
            model thinks, not because{" "}
            {context.length === 1 ? "it is" : "they are"} put forward as picks.
          </>
        )}
      </p>

      <div className="ld-scroll">
        <table className="ld-ledger ps-ledger-table">
          <caption className="sr-only">
            The graded record of each tier shown on this board.
          </caption>
          <thead>
            <tr>
              <th scope="col">Tier</th>
              <th scope="col">On this board</th>
              <th scope="col" className="num">Picks</th>
              <th scope="col" className="num">Won</th>
              <th scope="col" className="num">Needed</th>
              <th scope="col" className="num">Difference</th>
              <th scope="col" className="num ld-minor">Units</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isBet = bet.includes(r.edge_tier);
              return (
                <tr key={r.edge_tier} className={isBet ? "is-bet" : undefined}>
                  <th scope="row">
                    <span className={`pl-stamp is-${r.edge_tier}`}>
                      {LABEL[r.edge_tier] ?? r.edge_tier}
                    </span>
                  </th>
                  <td>{isBet ? "Put forward as a pick" : "Shown, not put forward"}</td>
                  <td className="num">{r.picks.toLocaleString()}</td>
                  <td className="num ld-won">{pct(r.hit_rate)}</td>
                  <td className="num">{pct(r.breakeven)}</td>
                  <td className="num">
                    <Acct value={diff(r)} scale={100} plus />
                  </td>
                  <td className="num ld-minor">
                    <Acct value={r.units} plus />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/*
        Folded, not removed. Every visitor needs this once and nobody needs it
        twice, and left open it pushed the first pick on the board below the
        fold on a laptop.
      */}
      <details className="ps-ledger-more">
        <summary>How to read these numbers</summary>
        <p className="ps-ledger-key">
          <b>Won</b> is how often the pick was correct. <b>Needed</b> is how
          often it had to be correct, at the prices it was offered at, just to
          finish level. The sportsbook&rsquo;s cut is inside every price, which
          is why needed sits above 50%. <b>Difference</b> is won minus needed,
          in points, and a figure in brackets is a shortfall. <b>Units</b> is
          the running total if every pick had been staked at one unit, where a
          unit is whatever a single flat stake is to you.
        </p>
      </details>
    </section>
  );
}
