/**
 * The two figures the site quotes about itself, read from the record.
 *
 * The disclaimer at the foot of every page and the "what it is not" section on
 * the landing page both say how often the model has been right and how often it
 * claimed it would be. Those were typed in. They were accurate on the day they
 * were typed and they go stale the moment a week is graded, which on a public
 * site is a false claim sitting inside the paragraph whose entire job is not
 * making false claims.
 *
 * So they are fetched. One call, cached for the session, because the disclaimer
 * renders on every route and none of this changes between them.
 *
 * When the fetch fails the figures are dropped rather than substituted. The
 * sentence is written to read correctly without them: "the model is wrong
 * often" is the claim, and the numbers are the evidence. A missing number is a
 * quieter failure than a wrong one.
 */

import { useEffect, useState } from "react";
import { fetchTierRecord } from "../api";

export type HeadlineRecord = {
  /** Picks-weighted hit rate across every graded pick. */
  hit: number;
  /** What the model said its chances were, weighted the same way. */
  claimed: number;
  /** Tiers that finished below break-even, and how many there are. */
  losing: number;
  tiers: number;
};

let pending: Promise<HeadlineRecord | null> | null = null;

function load(): Promise<HeadlineRecord | null> {
  if (pending) return pending;
  pending = fetchTierRecord()
    .then(({ tiers }) => {
      const graded = tiers.filter((t) => t.picks > 0);
      const picks = graded.reduce((a, t) => a + t.picks, 0);
      if (!picks) return null;
      const hits = graded.reduce((a, t) => a + (t.hit_rate ?? 0) * t.picks, 0);
      const said = graded.reduce(
        (a, t) => a + (t.model_predicted ?? 0) * t.picks,
        0,
      );
      return {
        hit: hits / picks,
        claimed: said / picks,
        losing: graded.filter((t) => (t.roi ?? 0) < 0).length,
        tiers: graded.length,
      };
    })
    .catch(() => null);
  return pending;
}

export function useHeadlineRecord(): HeadlineRecord | null {
  const [rec, setRec] = useState<HeadlineRecord | null>(null);
  useEffect(() => {
    let dead = false;
    load().then((r) => !dead && setRec(r));
    return () => {
      dead = true;
    };
  }, []);
  return rec;
}

/** Whole numbers. A percentage with a decimal reads as a precision claim. */
export function asPct(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

/** "three of its four", rather than a bare pair of digits mid-sentence. */
const WORDS = ["none", "one", "two", "three", "four", "five", "six"];
export function countWord(n: number): string {
  return WORDS[n] ?? String(n);
}
