/**
 * What the terms on this site mean.
 *
 * Written for someone who watches football, not someone who prices
 * derivatives. The board used to lead with expected value, which is the right
 * quantity and a useless label: nobody outside betting knows what "+3.5% EV"
 * is, and it reads as small whatever it is. It now leads with the chance a
 * pick lands and a letter grade for the price, and these definitions follow
 * the same order.
 *
 * A number nobody can interpret is worse than no number: it either gets
 * ignored or trusted for the wrong reason.
 *
 * Rendered as a collapsible strip so it costs nothing once you know the terms,
 * and as a shared component so a definition cannot drift between the pages that
 * use it.
 */

import { useState } from "react";

type Term = { term: string; short: string; body: React.ReactNode };

const TERMS: Term[] = [
  {
    term: "Chance",
    short: "How often a pick like this lands",
    body: (
      <>
        Not what the model hopes. What picks at this confidence and this price
        have actually done once graded. The model on its own claimed 66% and
        won 53%, so the number you see is corrected against the record rather
        than reported raw. The small mark on the bar is the number this price
        needs you to beat.
      </>
    ),
  },
  {
    term: "Value",
    short: "A grade for the price",
    body: (
      <>
        <strong>A</strong> means our numbers put the bet six cents or more
        ahead per dollar staked. <strong>B</strong> is three cents or more.{" "}
        <strong>C</strong> is anything above zero. <strong>D</strong> means the price already covers whatever edge we
        think we have, and the bet is not worth making however likely it looks.
        Hover the grade for what 100 dollars returns on average.
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
        why a 60% chance can be a bad bet and a 45% chance can be a good one,
        and it is the mark on the Chance bar.
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
    short: "Model minus line, always in that order",
    body: (
      <>
        Our number minus the book&rsquo;s.{" "}
        <span className="pos">Green</span> means our number sits above the line,
        which is why the pick is the over. <span className="neg">Red</span> means
        it sits below, and the pick is the under. The pick always follows the
        sign, so the two can never contradict each other.
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
        A ranking, not a score. Inside each market the board is ordered by how
        far our number sits from the line, as a share of the line. The top
        fifth is Elite, then Strong, then Medium, and the rest is Small. Only
        Elite is put forward. A market where that distance has not been shown
        to separate winners from losers is not ranked, and everything in it is
        Small.
      </>
    ),
  },
  {
    term: "Best Bet",
    short: "Elite or Strong unders, one per game",
    body: (
      <>
        An Elite or Strong pick on the under side, one per player per game, at
        the best price on offer. Unders only, because the over side is where
        this project has lost the most in every period tested. Graded like
        everything else, on the Track Record.
      </>
    ),
  },
  {
    term: "Value flag",
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
