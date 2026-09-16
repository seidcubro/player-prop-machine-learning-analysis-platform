/**
 * The front door.
 *
 * The board used to be the landing page, which meant a stranger's first screen
 * was 186 words of prose explaining expected value, with the first actual
 * number 715 pixels down a 900 pixel fold. The tool was doing the job of the
 * pitch, and doing it defensively, because it had to explain itself before it
 * could show anything.
 *
 * So this leads with the only thing that earns anyone's attention: the record.
 * Every figure on this page is read from the API at load, not typed in, because
 * a landing page that states a performance number it cannot back is the exact
 * thing this project has spent its life avoiding. If the numbers move, the page
 * moves with them.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Logo from "../components/Logo";
import { asPct, countWord, useHeadlineRecord } from "../lib/headline-record";
import {
  fetchEdgesSummary,
  fetchSeasonRecord,
  fetchTierRecord,
  type EdgesSummary,
  type SeasonRecord,
  type TierRecord,
} from "../api";

function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "···";
  return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(digits)}%`;
}

export default function Landing() {
  const [tiers, setTiers] = useState<TierRecord[] | null>(null);
  const [seasons, setSeasons] = useState<SeasonRecord[] | null>(null);
  const [summary, setSummary] = useState<EdgesSummary | null>(null);
  const rec = useHeadlineRecord();

  useEffect(() => {
    let dead = false;
    Promise.allSettled([
      fetchTierRecord(),
      // Elite only, matching the number quoted at the top of the page.
      fetchSeasonRecord("all", "elite"),
      fetchEdgesSummary(),
    ]).then(([t, s, e]) => {
      if (dead) return;
      if (t.status === "fulfilled") setTiers(t.value.tiers);
      if (s.status === "fulfilled") setSeasons(s.value.seasons);
      if (e.status === "fulfilled") setSummary(e.value);
    });
    return () => {
      dead = true;
    };
  }, []);

  const elite = tiers?.find((t) => t.edge_tier === "elite") ?? null;
  const live = summary?.by_tier?.elite ?? 0;
  const cov = summary?.coverage ?? null;

  // Seasons the top tier finished in profit, which is the claim being made.
  const eliteSeasons = (seasons ?? []).filter((s) => (s.picks ?? 0) > 0);

  return (
    <div className="ps-landing">
      <section className="hero">
        {/* The mark and the name together. The icon alone reads as decoration;
            the lockup is what a visitor recognises as a brand. */}
        <div className="mark">
          <Logo size={64} title="" />
          <span className="word">
            Prior<span className="sig">Line</span>
          </span>
        </div>

        <h1>
          Our prior.<span className="accent"> Their line.</span>
        </h1>

        <p className="lede">
          A model projects every skill player in the NFL, then prices those
          projections against what the sportsbooks are offering. When the two
          disagree by enough to beat the price, that is a signal. When they do
          not, this page says so.
        </p>

        <div className="cta">
          <Link className="primary" to="/signals">
            {live > 0
              ? `See today's ${live} signal${live === 1 ? "" : "s"}`
              : "See today's board"}
          </Link>
          <Link className="secondary" to="/record">
            Read the track record
          </Link>
        </div>

        {/* The live state, stated plainly. A quiet day is not hidden. */}
        <p className="status">
          {live > 0 ? (
            <>
              <span className="dot live" aria-hidden="true" />
              {live} elite signal{live === 1 ? "" : "s"} on the board right now
            </>
          ) : cov && cov.games_priced > 0 ? (
            <>
              <span className="dot idle" aria-hidden="true" />
              Nothing clears the bar today, across {cov.games_priced} priced
              game{cov.games_priced === 1 ? "" : "s"}
            </>
          ) : (
            <>
              <span className="dot idle" aria-hidden="true" />
              No games priced yet. The board fills as kickoff approaches
            </>
          )}
        </p>
      </section>

      {/* The record, which is the only reason to trust any of it. */}
      <section className="proof ps-balance" aria-label="Published record">
        <div className="stat">
          <div className="value">{elite ? pct(elite.roi, 1) : "···"}</div>
          <div className="label">return per unit</div>
          <div className="note">top tier, at the price offered</div>
        </div>
        <div className="stat">
          <div className="value">
            {elite ? elite.picks.toLocaleString() : "···"}
          </div>
          <div className="label">graded picks</div>
          <div className="note">every one scored against the box score</div>
        </div>
        <div className="stat">
          <div className="value">
            {elite?.units != null
              ? `${elite.units >= 0 ? "+" : "−"}${Math.abs(
                  elite.units,
                ).toFixed(0)}`
              : "···"}
          </div>
          <div className="label">units</div>
          <div className="note">flat one unit a bet</div>
        </div>
      </section>

      <section className="how" aria-label="How it works">
        <h2>Three things, in order</h2>
        <ol className="ps-balance is-prose">
          <li>
            <h3>Project the player</h3>
            <p>
              Not last week&rsquo;s box score. Usage, role, snap share, the
              defence being faced, the venue, the weather, the number Vegas has
              on the game. Opponent strength alone is about a third of the
              weight in the rushing model.
            </p>
          </li>
          <li>
            <h3>Price it against the book</h3>
            <p>
              A projection is worthless until it meets a price. The model turns
              its own distribution into a probability, then subtracts the
              break-even the odds demand. What is left is the edge, and most of
              the time it is negative.
            </p>
          </li>
          <li>
            <h3>Recommend almost none of it</h3>
            <p>
              Four tiers are graded, three reach the board and one is offered
              as a bet. Elite is the only tier that has made money; the others
              are shown with their real return printed beside them, because
              what the model thinks and what is worth staking are two different
              questions. Every tier stays on the track record, losses and all.
            </p>
          </li>
        </ol>
        <p className="sub">
          The full method, including every idea that was tested and thrown out,
          is in <Link to="/model">Inside the Model</Link>.
        </p>
      </section>

      {eliteSeasons.length > 0 && (
        <section className="seasons" aria-label="Record by season">
          <h2>The top tier, every season it has run</h2>
          <p className="sub">
            Elite picks only, which is the tier this site publishes and the
            number quoted above. Seasons before this one are reconstructed with
            models refit on earlier data only, so nothing here has seen the
            season it is being judged on. The current season is live and is the
            thinnest sample of the four.
          </p>
          <div className="grid ps-balance">
            {eliteSeasons.map((s) => (
              <div className="season" key={`${s.season}-${s.source}`}>
                <div className="yr">{s.season}</div>
                <div
                  className={`roi ${(s.roi ?? 0) >= 0 ? "up" : "down"}`}
                >
                  {pct(s.roi, 1)}
                </div>
                <div className="meta">
                  {s.picks.toLocaleString()} picks &middot;{" "}
                  {s.source === "live" ? "published live" : "reconstructed"}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="honest" aria-label="What this is not">
        <h2>What it is not</h2>
        <p>
          It is not a tip sheet and it does not promise anything. The model is
          wrong often.{" "}
          {/* Fetched, like every other number on this page. These two were
              typed in, which made them correct until the next grading run and
              a false claim after it. */}
          {rec ? (
            <>
              Across every graded pick it has hit <b>{asPct(rec.hit)}</b> while
              claiming {asPct(rec.claimed)}, and {countWord(rec.losing)} of its{" "}
              {countWord(rec.tiers)} tiers have lost money.{" "}
            </>
          ) : (
            <>Whole tiers of it have lost money. </>
          )}
          All of that is on the <Link to="/record">track record</Link>, because
          a record that only showed the good weeks would not be a record.
        </p>
        {/* Deliberately not the "use your own judgment" paragraph. The
            site-wide disclaimer renders immediately under this section and
            says exactly that, so writing it here too put the same three
            sentences on screen twice inside one fold. This says the other
            half: what the bar costs you on a normal day. */}
        <p>
          Most days it has nothing to say. Thirteen games can be priced and not
          one of them clear the bar, and on those days the board says so rather
          than finding you something to bet. That is the whole discipline of
          it. The filtering is the tool&rsquo;s job; knowing the football is
          still yours.
        </p>
        <div className="cta">
          <Link className="primary" to="/signals">
            Go to the board
          </Link>
          <Link className="secondary" to="/projections">
            Or just browse the projections
          </Link>
        </div>
      </section>
    </div>
  );
}
