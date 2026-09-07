/**
 * What the terms on this site mean.
 *
 * The board uses EV, edge, tiers and break-even as if they were common
 * knowledge. They are not, and a number nobody can interpret is worse than no
 * number: it either gets ignored or it gets trusted for the wrong reason.
 *
 * Rendered as a collapsible strip so it costs nothing once you know the terms,
 * and as a shared component so a definition cannot drift between the pages that
 * use it.
 */

import { useState } from "react";

type Term = { term: string; short: string; body: React.ReactNode };

const TERMS: Term[] = [
  {
    term: "EV",
    short: "Expected value",
    body: (
      <>
        Our estimated chance of winning, minus the chance the price is charging
        you for. A pick at +8% EV is one where we think the real probability is
        eight points better than the price implies. <strong>Zero or below
        means the price already covers it</strong>, and that bet is not worth
        making regardless of how likely it looks.
      </>
    ),
  },
  {
    term: "Break-even",
    short: "The win rate a price demands",
    body: (
      <>
        How often you must win just to stop losing money at a given price. At
        &minus;200 it is 66.7%; at &minus;110, 52.4%; at +150, only 40%. This is
        why a 60% chance can be a bad bet and a 45% chance can be a good one.
      </>
    ),
  },
  {
    term: "Line",
    short: "The sportsbook's number",
    body: (
      <>
        The threshold you are betting over or under, with the price beneath it.
        Lines are half-numbers so there is no tie.
      </>
    ),
  },
  {
    term: "Model",
    short: "Our median projection",
    body: (
      <>
        The middle of what we expect: half the time the player goes over it,
        half under. Deliberately the median and not the average, because on
        markets like receiving yards the average sits 25 to 35% above the middle
        and comparing an average to a line is how you end up recommending the
        wrong side.
      </>
    ),
  },
  {
    term: "Edge",
    short: "Model minus line, in the pick's direction",
    body: (
      <>
        How far our number clears the line for the side we picked.{" "}
        <span className="pos">Green</span> means our own projection backs the
        bet. <span className="neg">Red</span> means it does not, and the bet
        rests on the price being generous. Both are legitimate; the difference is
        worth seeing.
      </>
    ),
  },
  {
    term: "Win %",
    short: "Our estimated chance",
    body: (
      <>
        The probability we assign to the side we picked, after calibration
        against thousands of graded results. Not a promise, and on its own it
        says nothing without the price next to it.
      </>
    ),
  },
  {
    term: "Tiers",
    short: "Elite, Strong, Medium, Small",
    body: (
      <>
        Bands of expected value. On graded out-of-sample picks they rank in
        order, elite returning the most and small the least, which is the check
        that they mean anything at all. Overs are held to roughly double the bar
        because the over side has not been profitable in any season tested.
      </>
    ),
  },
  {
    term: "Best Bet",
    short: "The verified selection",
    body: (
      <>
        The one configuration that made money on a season it was never chosen
        on: top tier, under side, one pick per player per game. Roughly +7% per
        unit over that test. The rest of the board is research.
      </>
    ),
  },
  {
    term: "Value",
    short: "The structural over-shade",
    body: (
      <>
        An under on a top-quartile line, where sportsbooks shade numbers upward
        because the public buys overs. This one needs no model at all and held
        across 2023, 2024 and 2025, though it has been shrinking each season.
      </>
    ),
  },
];

export default function Glossary() {
  const [open, setOpen] = useState(false);
  return (
    <aside className="ps-glossary">
      <button
        type="button"
        className="ps-glossary-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        What do these columns mean?
      </button>
      {open && (
        <dl className="ps-glossary-body">
          {TERMS.map((t) => (
            <div className="ps-glossary-item" key={t.term}>
              <dt>
                {t.term}
                <span className="ps-glossary-short">{t.short}</span>
              </dt>
              <dd>{t.body}</dd>
            </div>
          ))}
        </dl>
      )}
    </aside>
  );
}
