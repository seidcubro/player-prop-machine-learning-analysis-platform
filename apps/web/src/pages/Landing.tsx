/**
 * The front page is the book, open.
 *
 * A sportsbook keeps a book and does not show it to you. This site keeps its
 * own and the front page is that book lying open: the posted account on the
 * left leaf, every tier, won against needed, losses in red; and on the right
 * leaf whatever is open for the coming slate.
 *
 * Nothing on this page is a typed figure. Every number is read from the record
 * or the board when the page loads, and a number that fails to load is left
 * blank rather than replaced with a remembered one. The site once advertised a
 * return the record contradicted, and that is how it happened.
 *
 * The page leads with whether the picks were right. Won and needed sit side by
 * side, the difference is the verdict, and units are the small column at the
 * end, because a pick is judged on being correct against the price it was
 * offered at before it is judged on anything else.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchEdges,
  fetchEdgesSummary,
  fetchProjections,
  fetchSeasonRecord,
  fetchTodayTiers,
  fetchTierRecord,
  type EdgesSummary,
  type Projection,
  type PropEdge,
  type SeasonRecord,
  type TodayTiers,
  type TierRecord,
} from "../api";
import { Acct, Arrow } from "../components/Acct";
import { PriorCurve } from "../components/PriorCurve";
import { kickoff, pct, price } from "../lib/format";
import {
  displayProjection,
  marketLabel,
  marketLabelShort,
  sideLabel,
} from "../lib/markets";

const TIER_ORDER = ["elite", "strong", "medium", "small"];
const TIER_NAME: Record<string, string> = {
  elite: "Elite",
  strong: "Strong",
  medium: "Medium",
  small: "Small",
};

type Totals = {
  picks: number;
  hit: number | null;
  needed: number | null;
  units: number | null;
};

/** Picks-weighted, so a tier of fifty picks cannot move the total like one of
 *  three thousand. */
function total(rows: TierRecord[]): Totals {
  const graded = rows.filter((r) => r.picks > 0);
  const picks = graded.reduce((a, r) => a + r.picks, 0);
  if (!picks) return { picks: 0, hit: null, needed: null, units: null };
  const w = (f: (r: TierRecord) => number | null) =>
    graded.reduce((a, r) => a + (f(r) ?? 0) * r.picks, 0) / picks;
  return {
    picks,
    hit: w((r) => r.hit_rate),
    needed: w((r) => r.breakeven),
    units: graded.reduce((a, r) => a + (r.units ?? 0), 0),
  };
}

function diff(hit: number | null, needed: number | null): number | null {
  return hit === null || needed === null ? null : hit - needed;
}

function sortTiers(rows: TierRecord[]): TierRecord[] {
  const rank = (t: string) => {
    const i = TIER_ORDER.indexOf(t);
    return i === -1 ? TIER_ORDER.length : i;
  };
  return [...rows]
    .filter((r) => r.picks > 0)
    .sort((a, b) => rank(a.edge_tier) - rank(b.edge_tier));
}

function Stamp({ tier }: { tier: string }) {
  const known = TIER_NAME[tier];
  if (!known) return <span className="ld-untiered">No tier recorded</span>;
  return <span className={`pl-stamp is-${tier}`}>{known}</span>;
}

function listWords(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function projNumber(p: Projection): string {
  const v = displayProjection(p);
  return p.market_code.endsWith("_yds") ? v.toFixed(0) : v.toFixed(1);
}

/**
 * A figure that is added up in front of you.
 *
 * It runs once, when the number first arrives from the record, and it lands on
 * exactly the figure it was given. With motion turned off it is just the figure.
 */
function Count({
  value,
  format,
  delay = 0,
}: {
  value: number | null | undefined;
  format: (v: number) => string;
  delay?: number;
}) {
  const still =
    typeof window === "undefined" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [k, setK] = useState(still ? 1 : 0);
  useEffect(() => {
    if (still || value === null || value === undefined) return;
    let raf = 0;
    const t0 = performance.now() + delay;
    const tick = (now: number) => {
      const u = Math.min(1, Math.max(0, (now - t0) / 1100));
      setK(1 - Math.pow(1 - u, 4));
      if (u < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, delay, still]);
  if (value === null || value === undefined) return <>-</>;
  return (
    <>
      <span aria-hidden="true">{format(value * k)}</span>
      <span className="sr-only">{format(value)}</span>
    </>
  );
}

const whole = (v: number) => Math.round(v).toLocaleString();
const share = (v: number) => pct(v);

/** The stagger index, as a custom property the stylesheet reads. */
function at(i: number): React.CSSProperties {
  return { "--i": i } as React.CSSProperties;
}

/* ------------------------------------------------------------ left leaf */

/**
 * The account on the cover is this season's picks, tiered by the rule the
 * board uses today.
 *
 * It has been two other things. First every season pooled, which was mostly
 * seasons rebuilt after the fact. Then this season with each pick under the
 * tier it was published with, which pooled four tiering rules, three of them
 * retired, and so described a board nobody is offered. What a visitor wants
 * to know is how the board in front of them has done, and this is that:
 * every published pick, at the line and price it was published at, with
 * today's label, each week ranked only on what was known before it.
 *
 * Until that has been stored once, the labels as published stand in.
 */
function PostedAccount({
  tiers,
  season,
  today,
  failed,
}: {
  tiers: TierRecord[] | null;
  season: number | null;
  today: TodayTiers | null;
  failed: boolean;
}) {
  const rows = today ? sortTiers(today.rows) : tiers ? sortTiers(tiers) : [];
  const sum = total(rows);
  const year = today?.season ?? season;
  // The lead line is quoted while elite is ahead of its price. Whether that
  // lead is more than luck is a separate question, answered underneath it in
  // words, from the interval, so the sentence cannot claim more than the
  // picks show.
  const lead = today?.elite && today.elite.hit > today.elite.needed ? today.elite : null;
  return (
    <div className="ld-leaf ld-leaf-left">
      <header className="ld-leaf-head">
        <h2>Posted account</h2>
        <p>
          {year
            ? `Every pick of the ${year} season, by confidence tier`
            : "Every graded pick, by confidence tier"}
        </p>
      </header>
      {lead && (
        <div className="ld-claim">
          <p className="ld-claim-line">
            Elite picks this season were right{" "}
            <b className="ld-claim-won">{pct(lead.hit)}</b> of the time. The
            prices needed <b>{pct(lead.needed)}</b>.
          </p>
          <p className="ld-claim-note">
            {lead.picks.toLocaleString()} picks, weeks {lead.week_first} to{" "}
            {lead.week_last}, tiered by the rule the board uses today, each
            week ranked only on what was known before it.
            {lead.lo <= 0 && (
              <>
                {" "}That is {((lead.hit - lead.needed) * 100).toFixed(1)}{" "}
                points clear of the price, and on this many picks a lead that
                size could still be luck.
              </>
            )}
          </p>
        </div>
      )}
      {failed ? (
        <p className="ld-leaf-note">
          The record did not load. Nothing is shown in its place, because a
          remembered figure is how a site ends up quoting a number its own
          record contradicts. The full account is on the{" "}
          <Link to="/record">record page</Link>.
        </p>
      ) : (
        <div className="ld-scroll">
          <table className="ld-ledger" aria-busy={tiers === null && today === null}>
            <caption className="sr-only">
              Graded picks by tier: how many, the share that won, the share
              that had to win at the prices offered, the difference in
              percentage points, and units won or lost.
            </caption>
            <thead>
              <tr>
                <th scope="col">Tier</th>
                <th scope="col" className="num">Picks</th>
                <th scope="col" className="num">Won</th>
                <th scope="col" className="num">Needed</th>
                <th scope="col" className="num">Difference</th>
                <th scope="col" className="num ld-minor">Units</th>
              </tr>
            </thead>
            <tbody>
              {tiers === null && today === null &&
                TIER_ORDER.map((t) => (
                  <tr key={t} className="is-pending">
                    <th scope="row"><Stamp tier={t} /></th>
                    <td className="num" colSpan={5}>
                      <span className="acct is-blank">reading the record</span>
                    </td>
                  </tr>
                ))}
              {rows.map((r, i) => (
                <tr key={r.edge_tier} className="ld-row" style={at(i)}>
                  <th scope="row"><Stamp tier={r.edge_tier} /></th>
                  <td className="num">
                    <Count value={r.picks} format={whole} delay={i * 70 + 200} />
                  </td>
                  <td className="num ld-won">
                    <Count value={r.hit_rate} format={share} delay={i * 70 + 200} />
                  </td>
                  <td className="num">{pct(r.breakeven)}</td>
                  <td className="num">
                    <Acct value={diff(r.hit_rate, r.breakeven)} scale={100} plus />
                  </td>
                  <td className="num ld-minor">
                    <Acct value={r.units} plus />
                  </td>
                </tr>
              ))}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="ld-row ld-total" style={at(rows.length)}>
                  <th scope="row">All picks</th>
                  <td className="num">
                    <Count value={sum.picks} format={whole} delay={rows.length * 70 + 200} />
                  </td>
                  <td className="num ld-won">
                    <Count value={sum.hit} format={share} delay={rows.length * 70 + 200} />
                  </td>
                  <td className="num">{pct(sum.needed)}</td>
                  <td className="num">
                    <Acct value={diff(sum.hit, sum.needed)} scale={100} plus />
                  </td>
                  <td className="num ld-minor">
                    <Acct value={sum.units} plus />
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
      {!failed && (
        <dl className="ld-key">
          <div>
            <dt>Won</dt>
            <dd>The share of picks that were correct.</dd>
          </div>
          <div>
            <dt>Needed</dt>
            <dd>
              The share that had to be correct, at the prices offered, just to
              finish level.
            </dd>
          </div>
          <div>
            <dt>Difference</dt>
            <dd>Won minus needed, in points. Brackets mean a loss.</dd>
          </div>
        </dl>
      )}
      {!failed && rows.length > 0 && (
        <p className="ld-leaf-note">
          {today
            ? "Every pick here was published before kickoff, at the line and "
              + "price shown at the time. The tier is the one today's rule "
              + "gives it. "
            : "Every pick here was published before kickoff and graded after "
              + "the games. "}
          Earlier seasons, rebuilt after the fact, are{" "}
          <Link to="/record">on the record</Link>.
        </p>
      )}
    </div>
  );
}

/* ----------------------------------------------------------- right leaf */

function OpenEntries({
  edges,
  pencil,
  summary,
}: {
  edges: PropEdge[] | null;
  pencil: Projection[];
  summary: EdgesSummary | null;
}) {
  const cov = summary?.coverage ?? null;
  const priced = edges !== null && edges.length > 0;
  const next = summary?.next_update ?? null;

  let status = "Reading the board";
  if (edges !== null) {
    if (priced) {
      status = cov
        ? `${cov.games_priced} of ${cov.games_upcoming} upcoming games priced`
        : "Priced and open";
    } else if (cov && cov.games_in_progress > 0) {
      status = "A game is in progress, so its entries have closed";
    } else {
      status = "No lines posted yet for the next slate";
    }
  }

  return (
    <div className="ld-leaf ld-leaf-right">
      <header className="ld-leaf-head">
        <h2>{priced || edges === null ? "Open entries" : "Pencilled in"}</h2>
        <p>{status}</p>
      </header>

      {priced && (
        <ol className="ld-entries">
          {edges!.map((e, i) => (
            <li key={e.id} className="ld-entry" style={at(i)}>
              <span className="ld-entry-who">
                {e.player_id ? (
                  <Link to={`/players/${e.player_id}`}>{e.player_name}</Link>
                ) : (
                  e.player_name
                )}
                <small>{marketLabel(e.market_code)}</small>
              </span>
              <span className="ld-entry-call">
                {sideLabel(e.market_code, e.recommended_side)}
                {e.line !== null ? ` ${e.line}` : ""}
                <small>{price(e.price_american)}</small>
              </span>
              <Stamp tier={e.edge_tier} />
            </li>
          ))}
        </ol>
      )}

      {edges !== null && !priced && pencil.length > 0 && (
        <>
          <ol className="ld-entries is-pencil">
            {pencil.map((p, i) => (
              <li
                key={`${p.player_id}-${p.market_code}`}
                className="ld-entry"
                style={at(i)}
              >
                <span className="ld-entry-who">
                  {p.app_player_id ? (
                    <Link to={`/players/${p.app_player_id}`}>{p.player_name}</Link>
                  ) : (
                    p.player_name
                  )}
                  <small>{[p.team, p.opponent].filter(Boolean).join(" v ")}</small>
                </span>
                <span className="ld-entry-call">
                  {projNumber(p)}
                  <small>{marketLabelShort(p.market_code)}</small>
                </span>
              </li>
            ))}
          </ol>
          <p className="ld-leaf-note">
            These are projections, not picks. A pick needs a line to disagree
            with, and no book has posted one yet. They stay in pencil until one
            does.
          </p>
        </>
      )}

      {edges !== null && !priced && pencil.length === 0 && (
        <p className="ld-leaf-note">
          Nothing is open and no projections are published for the next slate
          yet. This leaf fills in when the next build runs.
        </p>
      )}

      <p className="ld-leaf-foot">
        {next?.at ? <span>Next build {kickoff(next.at)}</span> : <span />}
        <Link to={priced ? "/signals" : "/projections"}>
          {priced ? "The whole board" : "Every projection"} <Arrow />
        </Link>
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- page */

/** Each of these is in the method page's own list of what was thrown out. */
const STRUCK: Array<[string, string]> = [
  [
    "Predict the line's error instead of the stat",
    "No better than a coin flip in any market.",
  ],
  [
    "Trust the bigger names in the lower tiers",
    "Ran backwards. The biggest names did worst.",
  ],
  [
    "Lean on primetime games",
    "Worse than the rest of the slate, not better.",
  ],
  [
    "Use a heavier gradient-boosting library",
    "Caught memorising its training data and lost every market.",
  ],
  [
    "Recommend anytime touchdown bets",
    "The book's own prices rank likely scorers better than the model does. Projected, never recommended.",
  ],
  [
    "Price every market from one game simulator",
    "It reproduces how teammates move together and still has not beaten the simpler models on probability.",
  ],
];

export default function Landing() {
  const [tiers, setTiers] = useState<TierRecord[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [season, setSeason] = useState<number | null>(null);
  const [today, setToday] = useState<TodayTiers | null>(null);
  const [seasons, setSeasons] = useState<SeasonRecord[] | null>(null);
  const [summary, setSummary] = useState<EdgesSummary | null>(null);
  const [edges, setEdges] = useState<PropEdge[] | null>(null);
  const [pencil, setPencil] = useState<Projection[]>([]);

  useEffect(() => {
    let dead = false;
    // The newest season with published picks is the account on the cover.
    // If there is none yet, the pooled record stands in and says what it is.
    fetchSeasonRecord("live")
      .then((r) => {
        const latest =
          r.seasons.reduce((m, s) => (s.picks > 0 ? Math.max(m, s.season) : m), 0) ||
          null;
        if (dead) return undefined;
        setSeason(latest);
        return latest ? fetchTierRecord(latest, "live") : fetchTierRecord();
      })
      .then((r) => r && !dead && setTiers(r.tiers))
      .catch(() => !dead && setFailed(true));
    fetchTodayTiers()
      .then((r) => !dead && setToday(r))
      .catch(() => undefined);
    fetchSeasonRecord("all", "elite")
      .then((r) => !dead && setSeasons(r.seasons))
      .catch(() => !dead && setSeasons([]));
    fetchEdgesSummary()
      .then((r) => !dead && setSummary(r))
      .catch(() => undefined);
    fetchEdges({ limit: 8 })
      .then((r) => {
        if (dead) return;
        setEdges(r.edges);
        if (r.edges.length > 0) return;
        // Nothing priced: fall back to what the model thinks of the next
        // slate, one market at a time so the list is not eight receivers.
        Promise.all(
          ["rec_yds", "rush_yds", "pass_yds"].map((m) =>
            fetchProjections({
              market_code: m,
              starters_only: true,
              sort: "projection",
              order: "desc",
              limit: 3,
            })
              .then((p) => p.projections)
              .catch(() => [] as Projection[]),
          ),
        ).then((lists) => {
          if (dead) return;
          const out: Projection[] = [];
          for (let i = 0; i < 3; i++) {
            for (const l of lists) if (l[i]) out.push(l[i]);
          }
          setPencil(out.slice(0, 8));
        });
      })
      .catch(() => !dead && setEdges([]));
    return () => {
      dead = true;
    };
  }, []);

  const published = summary?.published_tiers ?? [];
  const bet = summary?.bet_tiers ?? [];
  const seasonRows = (seasons ?? [])
    .filter((s) => s.picks > 0)
    .sort((a, b) => b.season - a.season);

  // Sections below the book arrive as they are scrolled to. The page is
  // complete without this: nothing is hidden until the observer exists, and
  // it is never created for anyone who has asked for less motion.
  const root = useRef<HTMLDivElement>(null);
  const seasonCount = seasonRows.length;
  useEffect(() => {
    const el = root.current;
    if (!el || !("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    el.classList.add("is-armed");
    const io = new IntersectionObserver(
      (hits) => {
        for (const h of hits) {
          if (!h.isIntersecting) continue;
          h.target.classList.add("is-in");
          io.unobserve(h.target);
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.08 },
    );
    el.querySelectorAll("[data-reveal]:not(.is-in)").forEach((n) => io.observe(n));
    return () => io.disconnect();
  }, [seasonCount]);

  return (
    <div className="ld" ref={root}>
      <section className="ld-cover">
        <div className="ld-cover-in">
        <div className="ld-intro">
          <h1 className="ld-head">
            <span className="ld-hl"><span>Our prior.</span></span>{" "}
            <span className="ld-hl"><span className="ld-head-line">Their line.</span></span>
          </h1>
          <p className="ld-lede">
            I project every NFL player prop the sportsbooks post, pick a side,
            and grade every pick against the price it was offered at. A
            sportsbook keeps its book closed. This one is open, including the
            parts written in red.
          </p>
          <div className="ld-cta">
            <Link className="ld-btn" to="/signals">
              Open today&rsquo;s page <Arrow />
            </Link>
            <Link className="ld-link" to="/record">
              Audit the record <Arrow />
            </Link>
          </div>
        </div>
        <PriorCurve />
        </div>
      </section>

      <section className="ld-book" aria-label="The book, open">
        <PostedAccount
          tiers={tiers}
          season={season}
          today={today}
          failed={failed}
        />
        <OpenEntries edges={edges} pencil={pencil} summary={summary} />
      </section>

      <section className="ld-sec" aria-labelledby="ld-how" data-reveal>
        <h2 id="ld-how">How an entry gets written</h2>
        <ol className="ld-steps">
          <li data-reveal>
            <h3>Project</h3>
            <p>
              Every eligible player gets a predicted range in each of eleven
              markets, built from play-by-play, usage, depth charts, the
              opponent, the venue and the Vegas total and spread. A range and
              not a single number, because sixty yards give or take ten is a
              different claim from sixty give or take forty.
            </p>
          </li>
          <li data-reveal>
            <h3>Compare</h3>
            <p>
              When a book posts a line, the middle of that range is set against
              it. Above the line is an over, below it is an under. The middle
              and not the average, because yardage is skewed and the average
              sits above what a player typically does.
            </p>
          </li>
          <li data-reveal>
            <h3>Rank</h3>
            <p>
              Inside each market, the picks on the board are ranked by how far
              the projection sits from the line, as a share of the line. The
              top fifth is stamped elite. A market whose ranking has not held
              up on games it was not fitted to has no elite. Everything else
              on the board is then ordered by how likely its own price says it
              is to land: the top of that is strong, the next medium, and the
              rest small.
              {published.length > 0 && bet.length > 0 && (
                <>
                  {" "}The board shows {listWords(published)}, and only{" "}
                  {listWords(bet)} is put forward as a pick.
                </>
              )}
            </p>
          </li>
          <li data-reveal>
            <h3>Grade</h3>
            <p>
              After the games every pick is marked right or wrong, then scored
              against the share that had to win at the odds on offer. Both
              numbers go in the book, whichever way they came out.
            </p>
          </li>
        </ol>
        <p className="ld-more">
          <Link to="/model">The full method, with what failed</Link>
          <Link to="/faq">The same thing in plain English</Link>
        </p>
      </section>

      {seasonRows.length > 0 && (
        <section className="ld-sec" aria-labelledby="ld-seasons" data-reveal>
          <h2 id="ld-seasons">The elite tier, season by season</h2>
          <p className="ld-sec-lede">
            A pooled total can hide a bad year. These are the same picks split
            by the season they were made in, and by whether they were published
            before kickoff or rebuilt afterwards.
          </p>
          <div className="ld-scroll">
            <table className="ld-ledger ld-ledger-wide">
              <thead>
                <tr>
                  <th scope="col">Season</th>
                  <th scope="col">How it was recorded</th>
                  <th scope="col" className="num">Picks</th>
                  <th scope="col" className="num">Won</th>
                  <th scope="col" className="num">Needed</th>
                  <th scope="col" className="num">Difference</th>
                  <th scope="col" className="num ld-minor">Units</th>
                </tr>
              </thead>
              <tbody>
                {seasonRows.map((s, i) => (
                  <tr key={`${s.season}-${s.source}`} className="ld-row" style={at(i)}>
                    <th scope="row">{s.season}</th>
                    <td>
                      {s.source === "live"
                        ? "Published before kickoff"
                        : "Rebuilt from earlier seasons"}
                    </td>
                    <td className="num">{s.picks.toLocaleString()}</td>
                    <td className="num ld-won">{pct(s.hit_rate)}</td>
                    <td className="num">{pct(s.breakeven)}</td>
                    <td className="num">
                      <Acct value={diff(s.hit_rate, s.breakeven)} scale={100} plus />
                    </td>
                    <td className="num ld-minor">
                      <Acct value={s.units} plus />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="ld-sec" aria-labelledby="ld-struck" data-reveal>
        <h2 id="ld-struck">Struck from the book</h2>
        <p className="ld-sec-lede">
          Ideas I tested, wanted to work, and crossed out. They stay on the
          page, because a method that only lists what worked is an
          advertisement.
        </p>
        <ul className="ld-struck">
          {STRUCK.map(([idea, why]) => (
            <li key={idea} data-reveal>
              <s><span className="ld-idea">{idea}</span></s>
              <span>{why}</span>
            </li>
          ))}
        </ul>
        <p className="ld-more">
          <Link to="/model#findings">All of them, with the numbers</Link>
        </p>
      </section>

      <section className="ld-sec ld-not" aria-labelledby="ld-not" data-reveal>
        <h2 id="ld-not">What this is not</h2>
        <div className="ld-not-grid">
          <p>
            It is not a tipping service. Nothing here is sold, there is no
            account to open, and no link on this site goes to a sportsbook. I
            earn nothing if you bet and nothing if you do not.
          </p>
          <p>
            It is not a promise. The model is wrong often, the book is usually
            closer to the result than I am, and most of what it publishes has
            not beaten the price. The account above says exactly how much.
          </p>
          <p>
            It is not advice. It is one person&rsquo;s research, published with
            its losses. If you bet, that decision and its cost are yours. If
            betting has stopped being fun,{" "}
            <Link to="/responsible-gambling">help is free and private</Link>.
          </p>
        </div>
        <div className="ld-cta">
          <Link className="ld-btn" to="/signals">
            Open today&rsquo;s page <Arrow />
          </Link>
          <Link className="ld-link" to="/record">
            Audit the record <Arrow />
          </Link>
        </div>
      </section>
    </div>
  );
}
