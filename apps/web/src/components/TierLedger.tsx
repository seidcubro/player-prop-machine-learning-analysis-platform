/**
 * What each tier on this board has actually returned.
 *
 * The board shows three tiers and recommends one. That distinction is the
 * whole point of showing the other two, and it is only worth anything if it is
 * impossible to miss, so this sits directly above the table and states each
 * tier's real record: picks graded, hit rate, return per unit.
 *
 * Every figure is read from the record. None of it is typed in, for the same
 * reason the disclaimer's numbers are not: a performance claim that cannot
 * update is a claim that will eventually be false.
 *
 * The honest summary of what it prints, as of writing: elite returns a little
 * over four percent across four seasons and is positive in every one of them.
 * Strong sits on the waterline, half a tenth of a point below break-even.
 * Medium loses. Those are not three grades of good bet, they are one bet and
 * two things the model happens to think, which is what the labels say.
 */

import { useEffect, useState } from "react";
import { fetchTierRecord, type TierRecord } from "../api";

const LABEL: Record<string, string> = {
  elite: "Elite",
  strong: "Strong",
  medium: "Medium",
  small: "Small",
};

function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "···";
  return `${(v * 100).toFixed(digits)}%`;
}

function signed(v: number | null | undefined): string {
  if (v === null || v === undefined) return "···";
  return `${v >= 0 ? "+" : "\u2212"}${Math.abs(v * 100).toFixed(1)}%`;
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
    fetchTierRecord()
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

  return (
    <section className="ps-ledger" aria-label="What each tier has returned">
      <h2>Three tiers on this board. One of them is a bet.</h2>
      <p>
        {betting.map((r) => LABEL[r.edge_tier] ?? r.edge_tier).join(" and ")} is
        the only tier that has made money, so it is the only one offered as
        something to stake.{" "}
        {context.map((r) => LABEL[r.edge_tier] ?? r.edge_tier).join(" and ")}{" "}
        {context.length === 1 ? "is" : "are"} shown because{" "}
        {context.length === 1 ? "it is" : "they are"} what the model thinks, not
        because {context.length === 1 ? "it is" : "they are"} worth backing.
        Every figure below is the graded record, wins and losses included.
      </p>

      <div className="ps-ledger-rows">
        {rows.map((r) => {
          const isBet = bet.includes(r.edge_tier);
          return (
            <div
              className={`ps-ledger-row${isBet ? " is-bet" : ""}`}
              key={r.edge_tier}
            >
              <span className={`tier tier-${r.edge_tier}`}>
                {LABEL[r.edge_tier] ?? r.edge_tier}
              </span>
              <span className="verdict">
                {isBet ? "offered as a bet" : "shown, not offered"}
              </span>
              <span className="fig">
                <b className={(r.roi ?? 0) >= 0 ? "pos" : "neg"}>
                  {signed(r.roi)}
                </b>
                <small>per unit</small>
              </span>
              <span className="fig">
                <b>{pct(r.hit_rate)}</b>
                <small>hit, claimed {pct(r.model_predicted)}</small>
              </span>
              <span className="fig">
                <b>{r.picks.toLocaleString()}</b>
                <small>graded</small>
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
