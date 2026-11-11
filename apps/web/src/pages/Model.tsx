/**
 * Inside the model: the long version, for people who want the whole method.
 *
 * How It Works answers "can I trust this number". This page answers "how was it
 * built, and how do you know it works": the data, the models, the calibration,
 * the validation discipline and the ideas that were tested and thrown out.
 *
 * The track record here is read from the API on load, the same rule the landing
 * page follows, because a performance figure typed into a page goes stale the
 * first time a week is graded. Research findings are different: they are
 * measurements taken on a fixed date and are labelled as such, since the point
 * of them is what was concluded, not the running total.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PageTitle from "../components/PageTitle";
import { Acct } from "../components/Acct";
import {
  fetchSeasonRecord,
  fetchTierRecord,
  type SeasonRecord,
  type TierRecord,
} from "../api";
import { asPct, useHeadlineRecord } from "../lib/headline-record";

const TIER_LABEL: Record<string, string> = {
  elite: "Elite",
  strong: "Strong",
  medium: "Medium",
  small: "Small",
  none: "No tier",
};

const TIER_STATUS: Record<string, string> = {
  elite: "put forward as a pick",
  strong: "shown, not put forward",
  medium: "shown, not put forward",
  small: "graded, not shown",
  none: "graded, not shown",
};

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "···" : asPct(v);
}

/** Won minus needed. Null when either side is missing, never a guessed zero. */
function gap(
  won: number | null | undefined,
  needed: number | null | undefined,
): number | null {
  if (won === null || won === undefined) return null;
  if (needed === null || needed === undefined) return null;
  return won - needed;
}

const CONTENTS: [string, string][] = [
  ["short", "The short version"],
  ["terms", "Vocabulary"],
  ["pipeline", "The pipeline"],
  ["data", "Data"],
  ["features", "Features"],
  ["models", "Models"],
  ["probability", "From projection to probability"],
  ["calibration", "Calibration"],
  ["tiers", "Edge, gap and tiers"],
  ["example", "A worked example"],
  ["validation", "How it is validated"],
  ["findings", "What the research found"],
  ["record", "The record"],
  ["infrastructure", "Infrastructure"],
  ["lessons", "Lessons from the bugs"],
  ["limits", "Limitations"],
];

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="ps-qa" id={id} aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>{title}</h2>
      {children}
    </section>
  );
}

/** One wide table, scrolling in its own box on a phone. */
function Grid({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="ps-tablewrap">
      <table className="ps-table is-matrix">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pipeline() {
  const boxes = [
    ["Ingest", "into Postgres"],
    ["Features", "per player, per game"],
    ["Train", "11 markets"],
    ["Calibrate", "4 layers"],
    ["Price & rank", "gap vs the line"],
    ["Serve", "API to this site"],
  ];
  const gaps = ["stats", "rows", "ladder", "P(win)", "picks"];
  const x = (i: number) => 15 + i * 180;
  return (
    <figure className="ps-model-fig">
      <div className="ps-model-figbox">
        <svg
          viewBox="0 0 1060 350"
          role="img"
          aria-label="Pipeline: nflverse data is ingested, turned into features, used to train eleven market models, corrected by calibration, priced against sportsbook lines and served to the site. Graded results feed back into calibration, and a seventeen-check audit runs on every refresh."
        >
          <defs>
            <marker id="mdl-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
            </marker>
            <marker id="mdl-arA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" className="acc-fill" />
            </marker>
          </defs>

          <text x="15" y="36" className="mono strong">nflverse</text>
          <text x="15" y="52" className="mono faint">stats, rosters, play-by-play</text>
          <text x="800" y="36" textAnchor="middle" className="mono strong">The Odds API</text>
          <text x="800" y="52" textAnchor="middle" className="mono faint">lines and prices</text>
          <line x1="80" y1="62" x2="80" y2="146" stroke="currentColor" strokeWidth="1.4" markerEnd="url(#mdl-ar)" />
          <line x1="800" y1="62" x2="800" y2="146" stroke="currentColor" strokeWidth="1.4" markerEnd="url(#mdl-ar)" />
          <text x="88" y="108" className="mono faint small">daily</text>
          <text x="808" y="108" className="mono faint small">near kickoff</text>

          {boxes.map(([t, s], i) => (
            <g key={t}>
              <rect
                x={x(i)}
                y="150"
                width="130"
                height="60"
                rx="6"
                fill="none"
                stroke="currentColor"
                strokeWidth={i === 3 ? 2 : 1.4}
                className={i === 3 ? "acc-stroke" : undefined}
              />
              <text x={x(i) + 65} y="176" textAnchor="middle" className={`disp${i === 3 ? " acc-fill" : ""}`}>
                {t}
              </text>
              <text x={x(i) + 65} y="196" textAnchor="middle" className="mono faint small">
                {s}
              </text>
            </g>
          ))}
          {gaps.map((g, i) => (
            <g key={g}>
              <line x1={x(i) + 130} y1="180" x2={x(i) + 178} y2="180" stroke="currentColor" strokeWidth="1.4" markerEnd="url(#mdl-ar)" />
              <text x={x(i) + 155} y="170" textAnchor="middle" className="mono faint tiny">
                {g}
              </text>
            </g>
          ))}

          <polyline points="980,210 980,258 620,258 620,214" fill="none" strokeWidth="1.8" className="acc-stroke" markerEnd="url(#mdl-arA)" />
          <text x="800" y="276" textAnchor="middle" className="mono acc-fill small">
            games finish, picks graded, calibration refit
          </text>

          <rect x="15" y="300" width="1030" height="36" rx="6" fill="none" stroke="currentColor" strokeWidth="1.2" strokeDasharray="5 4" />
          <text x="530" y="323" textAnchor="middle" className="mono">
            freshness audit: 17 checks, fails the run when anything is stale or inconsistent
          </text>
        </svg>
      </div>
      <figcaption>
        The pipeline. Timers drive it on a schedule. The highlighted loop is why the
        system improves during a season: every settled pick becomes data for the
        calibration layer.
      </figcaption>
    </figure>
  );
}

export default function Model() {
  const [tiers, setTiers] = useState<TierRecord[] | null>(null);
  const [seasons, setSeasons] = useState<SeasonRecord[] | null>(null);
  const head = useHeadlineRecord();

  useEffect(() => {
    let dead = false;
    Promise.allSettled([fetchTierRecord(), fetchSeasonRecord("all", "elite")]).then(
      ([t, s]) => {
        if (dead) return;
        if (t.status === "fulfilled") setTiers(t.value.tiers);
        if (s.status === "fulfilled") setSeasons(s.value.seasons);
      },
    );
    return () => {
      dead = true;
    };
  }, []);

  const elite = tiers?.find((t) => t.edge_tier === "elite") ?? null;
  const graded = tiers?.reduce((a, t) => a + t.picks, 0) ?? null;
  const tierOrder = ["elite", "strong", "medium", "small", "none"];
  const tierRows = (tiers ?? [])
    .filter((t) => t.picks > 0)
    .sort((a, b) => tierOrder.indexOf(a.edge_tier) - tierOrder.indexOf(b.edge_tier));

  return (
    <>
      <div className="ps-hero">
        <PageTitle lead="Inside the" accent="Model" />
        <p>
          The full method: where the data comes from, how every projection is
          built, how it becomes a probability, how that probability is kept
          honest, and how the whole thing is tested. The ideas that failed are
          here too, because most of what shaped this project was deciding what
          not to ship. For the short version, see{" "}
          <Link to="/faq">How It Works</Link>.
        </p>
      </div>

      <div className="ps-model-stats" aria-label="At a glance">
        <div>
          <b>11</b>
          <span>prop markets modelled</span>
        </div>
        <div>
          <b>{graded === null ? "···" : graded.toLocaleString()}</b>
          <span>picks graded against box scores</span>
        </div>
        <div>
          <b>{elite ? `${pct(elite.hit_rate)} / ${pct(elite.breakeven)}` : "···"}</b>
          <span>elite picks won, against what their prices needed</span>
        </div>
        <div>
          <b>{head ? `${asPct(head.hit)} / ${asPct(head.claimed)}` : "···"}</b>
          <span>all picks won, against what the model claimed</span>
        </div>
      </div>

      <nav className="ps-model-toc" aria-label="On this page">
        {CONTENTS.map(([id, label]) => (
          <a key={id} href={`#${id}`}>
            {label}
          </a>
        ))}
      </nav>

      <div className="ps-faq">
        <Section id="short" title="The short version">
          <p>
            PriorLine predicts how many yards, catches, carries and touchdowns
            each NFL player will produce, compares that to the number a
            sportsbook has set, and flags the rare cases where the gap is large
            enough to overcome the sportsbook&rsquo;s built-in margin.
          </p>
          <p>
            A book posts a line such as &ldquo;over or under 3.5
            receptions&rdquo;. The model estimates the full range of outcomes for
            that player, turns it into a probability of each side winning, and
            subtracts the probability the price requires just to break even.
            What remains is the expected value, which is shown as a grade for
            the price. The tier is decided separately: inside each market the
            night&rsquo;s props are ranked by how far the model sits from the
            line, and the top fifth is Elite. The rest of the board is ordered
            by how likely each pick&rsquo;s own price says it is to land, and
            the top of that is Strong, then Medium. Every pick is graded
            against the real result, in every tier, and Elite is the only one
            put forward.
          </p>
          <div className="ps-model-callout">
            <p className="k">The central finding</p>
            <p>
              The model does <strong>not out-predict the sportsbook</strong>.
              The book&rsquo;s own line lands closer to what players actually do
              than this model&rsquo;s number does, and that is measured further
              down this page. What the model is used for is narrower: finding
              the props where the two numbers are furthest apart, in the markets
              where that distance has been shown to rank which picks win. The
              record below is the test of whether that is worth anything, and it
              is published whether it passes or not.
            </p>
          </div>
        </Section>

        <Section id="terms" title="Vocabulary">
          <Grid
            head={["Term", "Meaning"]}
            rows={[
              [<strong>Player prop</strong>, "A bet on one player's statistic rather than the game result."],
              [<strong>Line</strong>, "The threshold the book sets, usually a half number such as 3.5 so there is no tie."],
              [<strong>American odds</strong>, "−110 means risk 110 to win 100. +122 means risk 100 to win 122."],
              [<strong>Break-even</strong>, "The win rate a price requires to lose nothing over time. −110 needs 52.4%, +122 needs 45.0%."],
              [<strong>Vig</strong>, "The book's margin. Both sides' implied probabilities add to more than 100%."],
              [<strong>Unit</strong>, "A standard stake, so results are comparable regardless of bet size."],
              [<strong>Expected value</strong>, "The model's probability that a side wins, minus the break-even for its price."],
              [<strong>ROI</strong>, "Average profit per unit staked. +4% means 100 staked returned 104 on average."],
              [<strong>Closing line value</strong>, "Whether the price taken beat the final price before kickoff. It separates skill from luck much faster than win and loss."],
            ]}
          />
          <pre className="ps-model-formula">{`break-even at −X   =  X / (X + 100)
break-even at +X   =  100 / (X + 100)
expected value     =  P(side wins) − break-even`}</pre>
        </Section>

        <Section id="pipeline" title="The pipeline">
          <p>
            Six stages, run on a schedule, with one feedback loop that matters more
            than any individual model.
          </p>
          <Pipeline />
          <Grid
            head={["Layer", "Technology"]}
            rows={[
              ["Database", "PostgreSQL"],
              ["Ingestion, features, API", "Python, FastAPI"],
              ["Models, calibration, pricing, grading", "scikit-learn, pandas, SciPy"],
              ["This site", "React, TypeScript, Vite, served from a CDN"],
              ["HTTPS", "Caddy, with automatically renewed certificates"],
              ["Scheduling", "systemd timers sharing a single lock"],
              ["Packaging", "Docker Compose"],
            ]}
          />
        </Section>

        <Section id="data" title="Data">
          <ul>
            <li>
              <strong>nflverse</strong>, the open NFL data project: weekly player
              stats, play-by-play, rosters, depth charts, snap counts, injury
              reports, schedules with Vegas spreads and totals, weather and venue,
              and nflverse&rsquo;s own model of expected yards and touchdowns
              based on where each touch happened.
            </li>
            <li>
              <strong>The Odds API</strong>: live prop lines and prices from
              DraftKings, FanDuel and BetMGM. It bills per game per market, which
              is why prices are bought close to kickoff rather than all week.
            </li>
          </ul>
          <p>
            About 1.3 million rows in total, including roughly 160,000 engineered
            training rows and an append-only history of every price captured. That
            price history matters most: game statistics can be rebuilt from
            nflverse in minutes, but a price not captured before kickoff is gone.
          </p>
        </Section>

        <Section id="features" title="Features">
          <p>
            Each training row describes one player before one game, using only
            information that existed before that game. The lookback is the
            player&rsquo;s previous five games, giving roughly 60 to 80 features per
            market.
          </p>
          <Grid
            head={["Family", "Examples"]}
            rows={[
              ["Recent production", "mean, weighted mean, median, trimmed mean, min, max, spread, trend, season-to-date"],
              ["Role", "snap share, depth chart rank and its change, carry share, target share, teammates injured at the position"],
              ["Play context", "red-zone targets and carries, third-down targets, air yards, yards after catch, EPA per play, expected touchdowns"],
              ["Opponent", "what this defence recently allows to this specific position group"],
              ["Game and venue", "spread, total, implied team total, temperature, wind, roof, surface, home or away, rest, injury status"],
            ]}
          />
          <p>
            Two rules matter. Anything knowable before kickoff (opponent, depth
            chart, injuries, weather, the line) is read fresh for the upcoming
            game, never from a stored row that could be months old. And opponent
            features describe the <em>actual</em> opponent, not an average of the
            defences a player happened to face recently.
          </p>
        </Section>

        <Section id="models" title="Models">
          <p>
            Every market has its own point model for the central estimate and a
            quantile ensemble for the spread of outcomes. The model family is
            chosen by a bakeoff, not by preference.
          </p>
          <Grid
            head={["Market", "Model family", "Outcome distribution"]}
            rows={[
              ["Rush attempts", "Ridge regression", "quantile"],
              ["Rushing yards", "Elastic net", "quantile"],
              ["Receptions", "Elastic net", "quantile"],
              ["Receiving yards", "Random forest", "quantile"],
              ["Passing yards, attempts, completions", "Histogram gradient boosting", "quantile"],
              ["Passing touchdowns", "Poisson regression", "Poisson"],
              ["Rushing and receiving touchdowns", "Random forest", "Poisson"],
              ["Anytime touchdown", "Random forest", "Poisson, projected but never priced"],
            ]}
          />
          <h3>How a family is chosen</h3>
          <ul>
            <li>
              Every candidate is scored on the same five expanding-window folds:
              train on everything before a date, test on the block after it, move
              the date forward.
            </li>
            <li>
              A challenger replaces the current model only if it wins by more than
              one standard error of the fold-to-fold difference. With ten
              candidates and a single test, one wins by luck.
            </li>
            <li>
              Any model whose training score beats its test score by more than
              0.35 R² is flagged as memorising. Gradient-boosting library LightGBM
              was caught this way and lost every market.
            </li>
          </ul>
          <p>
            Simple linear models won three markets. Rushing volume is close to
            linear in carry share and opponent form, so a forest only adds
            variance.
          </p>
          <p>
            There is also a hard ceiling. Splitting receiving yards into the part
            that differs between players and the part that varies game to game for
            the same player, the noise is larger than the signal: most of the
            variation cannot be predicted from who the player is.
          </p>
        </Section>

        <Section id="probability" title="From projection to probability">
          <p>
            A projection on its own cannot be bet. Sixty yards with a range of 20
            to 110 is a different proposition from sixty with a range of 45 to 75.
            So the model predicts a distribution.
          </p>
          <p>
            For yardage and volume markets, five gradient-boosted models are
            trained at the 10th, 25th, 50th, 75th and 90th percentiles. Together
            they form a ladder for each player, and the line is placed on that
            ladder to read off a probability:
          </p>
          <pre className="ps-model-formula">{`P(under) = where the line falls on [q10, q25, q50, q75, q90]
P(over)  = 1 − P(under)
side     = over if the median is above the line, otherwise under`}</pre>
          <p>
            <strong>Why the median rather than the average.</strong> Yardage is
            right-skewed, so the average sits above the typical result. Comparing a
            line to the average invents over-value on almost every prop.
          </p>
          <p>
            <strong>Touchdowns</strong> are small whole numbers, where quantile
            regression reproduces the population&rsquo;s shape rather than the
            player&rsquo;s. Those markets use a Poisson distribution whose mean is
            the model&rsquo;s projection.
          </p>
        </Section>

        <Section id="calibration" title="Calibration">
          <p>
            Calibration means a stated probability matches reality: of all picks
            called 60%, about 60% should win. The first version of the model
            claimed 95% on some rushing props, and every confidence band was 14 to
            29 points too high. Four correction layers now sit between the models
            and the board, each fitted on graded results and each kept only if it
            improved a held-out period.
          </p>
          <Grid
            head={["Layer", "Corrects", "How"]}
            rows={[
              ["Probability calibrator", "P(over)", "Isotonic regression per market. The error changes shape with the claimed probability, and isotonic follows any shape while never reversing the order of two picks."],
              ["Interval calibrator", "the published range", "Measures where real outcomes land inside the ladder and re-reads each level. Accepted level by level."],
              ["Median anchor", "the median", "Re-reads the median as a fitted fraction of the point projection, kept inside the middle half of the range."],
              ["Spread calibrator", "how widely predictions spread", "Currently corrects nothing, which is correct: no market improved on its raw prediction out of sample."],
            ]}
          />
          <p>
            <strong>Why this matters.</strong> Expected value is
            computed from the probability. If the probability is fourteen points too
            high, so is every expected value, and a rule that looks like it selects
            positive bets is really selecting negative ones. The model already ranked
            picks in roughly the right order; the scale was wrong. Correcting it
            is what stopped the board claiming more than it had earned.
          </p>
          {head && (
            <p>
              The gap has not fully closed and the site says so. Across every
              graded pick the model has claimed {asPct(head.claimed)} and hit{" "}
              {asPct(head.hit)}.
            </p>
          )}
        </Section>

        <Section id="tiers" title="Edge, gap and tiers">
          <pre className="ps-model-formula">{`edge = model median − book line            (the sign picks the side)
gap  = |model median − book line| / line   (the distance, as a share of the line)`}</pre>
          <p>
            A tier is a position on the night&rsquo;s board, not a threshold.
            Inside each market every priced prop is ranked by its gap, and the
            top fifth is Elite. Everything else, in every market, is then
            ranked together by the chance its own price implies for the side
            picked.
          </p>
          <Grid
            head={["Tier", "How it is chosen", "On the site"]}
            rows={[
              ["Elite", "top fifth of its market, by gap", "put forward as a pick"],
              ["Strong", "top of the rest, by price", "shown, not put forward"],
              ["Medium", "next of the rest, by price", "shown, not put forward"],
              ["Small", "everything else", "graded, not shown"],
            ]}
          />
          <p>
            Strong and medium were bands of the gap until week 10 of 2026. The
            gap separates picks at the top of a market and stops at about two
            fifths down, so medium was cut from where it had nothing left to
            say and hit 47.9% over nine weeks against 51.0% for small. Cut on
            the price instead, the same nine weeks read 65.0%, 62.3%, 52.9% and
            49.8% from elite to small. Strong and medium are the book&rsquo;s
            own favourites among our picks. They land more often and they do
            not beat their price; only elite has.
          </p>
          <p>
            The gap is divided by the line because two yards of disagreement on
            a 15-yard line is a different claim from two yards on an 80-yard
            line. In raw units the same measurement ranks nothing.
          </p>
          <h3>Three guards on the ranking</h3>
          <ul>
            <li>
              <strong>A market has to earn it.</strong> A market is ranked only
              where the gap has separated winners from losers on graded picks,
              asked twice on two halves of the record. A market that has not
              shown that publishes every pick as Small, which is the honest
              label for &ldquo;nothing here tells these apart&rdquo;.
            </li>
            <li>
              <strong>The direction is measured, not assumed.</strong> In most
              markets the widest gaps are the confident end. On rushing yards it
              has run the other way, with the narrowest disagreements winning
              most often, so a market can be ranked from either end and the
              record decides which.
            </li>
            <li>
              <strong>A thin board is not ranked.</strong> A market needs ten
              priced props on the board before a rank means anything. Below
              that, everything in it is Small.
            </li>
          </ul>
          <h3>Why the cut is a share and not a number</h3>
          <p>
            The first version used fixed gap thresholds fitted on past seasons.
            Applied to a live board whose gaps sat lower, they selected almost
            nothing: one week published no Elite picks at all. Whether the gap
            ranks accuracy is a claim about football and can be tested. Where to
            cut is a choice about how many picks to call Elite, and a share of
            the board answers it without assuming two seasons share a scale.
          </p>
          <h3>What happened to expected value</h3>
          <p>
            Tiers used to be bands of expected value. That was replaced because
            expected value did not rank accuracy: its highest bucket won less
            often than its middle, since the largest values sit at the longest
            prices rather than on the likeliest outcomes. It is still computed
            for every pick and shown as the Value grade, so the price is on the
            board. It no longer decides the tier.
          </p>
          <p>
            A Best Bet is narrower again: an Elite or Strong pick on the under
            side, one per player per game, at the best price on offer. What the
            board displays and what it puts forward are controlled separately,
            so widening one can never quietly widen the other. Anytime touchdown
            is projected on player pages but never priced: the market&rsquo;s
            own prices rank likely scorers better than the model does.
          </p>
        </Section>

        <Section id="example" title="A worked example">
          <p>
            <em>Illustrative numbers, not a current pick.</em> A book offers a
            receiver at under 4.5 receptions for +110. The model&rsquo;s median is
            3.9, below the line, so the side is the under.
          </p>
          <pre className="ps-model-formula">{`gap                 = |3.9 − 4.5| / 4.5  = 13.3% of the line
rank that night     = 6th of 40 reception props
top fifth of 40     = the first 8        = Elite
needed at +110      = 100 / 210          = 47.6%`}</pre>
          <p>
            The price does not move the tier. It sets what the pick has to do:
            at +110 it must win 47.6% of the time to finish level, and the same
            pick at &minus;150 would need 60%. That is why the record leads with
            how often a tier won and puts what it needed directly beside it.
          </p>
          <p>
            Two details change how much weight a pick like this deserves. If the
            model&rsquo;s median sits below the line but its average sits above it,
            the model barely has an opinion. And volume stats such as carries
            depend heavily on game script, which the model sees only through the
            Vegas spread and total.
          </p>
        </Section>

        <Section id="validation" title="How it is validated">
          <p>
            This is the part that separates a real model from a lucky backtest.
          </p>
          <ul>
            <li>
              <strong>Forward only.</strong> Models are trained on earlier seasons
              and scored on a season they never saw. The historical record was
              rebuilt with models refit on earlier data only.
            </li>
            <li>
              <strong>Out of sample, always.</strong> Anything measured on data a
              model trained on flatters it. Calibration is fitted on picks made by
              models that had not seen those games.
            </li>
            <li>
              <strong>A noise rule.</strong> A change is adopted only if it beats
              the current version by more than one standard error.
            </li>
            <li>
              <strong>Placebo controls.</strong> A proposed correction is also
              applied to a randomised signal. If the placebo seems to help too, the
              result is not trusted.
            </li>
            <li>
              <strong>Season by season.</strong> A strategy has to be positive in
              each season separately, not just in the pooled total.
            </li>
            <li>
              <strong>Uncertainty by weekend.</strong> Picks on the same slate share
              weather and game scripts, so confidence intervals are bootstrapped by
              slate rather than by pick.
            </li>
          </ul>
          <p>
            One result stands out as evidence the mechanism is real. Sorted by the
            size of the line, the edge on unders rises in a clean gradient, which
            data-mined patterns rarely do:
          </p>
          <Grid
            head={["Line size", "Edge on unders"]}
            rows={[
              ["Largest quarter", <span className="pos">+3.3%</span>],
              ["Largest half", <span className="pos">+2.4%</span>],
              ["Smallest half", <span className="neg">−1.3%</span>],
              ["Smallest quarter", <span className="neg">−3.1%</span>],
            ]}
          />
          <p>
            The bigger the name, the harder the public backs the over, the more the
            book shades it, and the more the under is worth.
          </p>
        </Section>

        <Section id="findings" title="What the research found">
          <p className="ps-model-dated">Measured on the graded record, September 2026.</p>
          <h3>The market predicts better than the model</h3>
          <p>
            At every level of disagreement, the book&rsquo;s line lands closer to
            the real outcome than the model&rsquo;s median, and the gap grows as the
            disagreement grows.
          </p>
          <Grid
            head={["Average disagreement", "Model error", "Line error", "Closer"]}
            rows={[
              ["0.3", "1.64", "1.64", "tie"],
              ["3.8", "14.05", "13.68", "book"],
              ["7.6", "21.00", "20.14", "book"],
              ["21.7", "36.02", "31.95", "book, by 4.1"],
            ]}
          />
          <p>
            That is why the edge is described as structural. The profitable picks
            come from being right about which way the book leans, not from out
            predicting it.
          </p>
          <h3>Ideas tested and rejected</h3>
          <Grid
            head={["Idea", "Outcome"]}
            rows={[
              ["Predicting the line's error instead of the stat", "No better than a coin flip in any market."],
              ["Taking the best price across books at the same line", "A better price existed on about 1% of picks. Worth +0.03%."],
              ["A mid-range expected value band on overs", "Large gains on seasons of 55 and 88 picks, shrinking to +3.3% on the one large season."],
              ["Tiering on expected value", "Did not rank accuracy. The highest values sat at the longest prices, and the top bucket won less often than the middle."],
              ["Fixed gap thresholds fitted on past seasons", "Did not transfer to a live board. One week published no Elite picks."],
              ["Rescuing the strong tier with filters", "Strong misses break-even by 0.08 points against a 1.64-point standard error. Nothing to filter toward."],
              ["Star players within the lower tiers", "Ran backwards: the biggest names did worst."],
              ["Primetime games", "Worse than the rest, not better."],
              ["Correcting the median to a true midpoint", "Fixed a real calibration flaw and erased nearly all the profit. The low median is what finds the unders."],
              ["Line-price bands, vacated roles, last-game form, book disagreement", "All failed the season-by-season test."],
            ]}
          />
          <h3>Ideas that held</h3>
          <ul>
            <li>Choosing sides from the median rather than the average.</li>
            <li>Isotonic calibration, which made expected value honest.</li>
            <li>
              Ranking by the gap as a share of the line, inside each market, in
              the direction that market was measured in.
            </li>
            <li>
              Expected touchdowns as a feature, which improved touchdown prediction
              by close to 1% in log loss across seeds and held up with an extra
              game of lag.
            </li>
          </ul>
          <h3>Touchdowns specifically</h3>
          <p>
            Treated as the yes-or-no question they are and scored with log loss and
            AUC, every model family landed within a hundredth of the same accuracy,
            and a plain logistic regression tied a random forest. The limit was the
            information available, not the choice of model.
          </p>
          <h3>A game simulator</h3>
          <p>
            A Monte Carlo simulator plays each matchup 20,000 times so that every
            market comes from one consistent outcome: team volume from the Vegas
            total and spread, touches shared out between players, and a
            quarterback&rsquo;s passing yards built from his receivers&rsquo;. It
            reproduces real correlations between teammates, but it has not beaten
            the quantile models on probability, so it is not used for pricing.
          </p>
        </Section>

        <Section id="record" title="The record">
          <p>
            Read live from the graded record. Won is how often the picks were
            right. Needed is how often they had to be right at the prices
            offered. Difference is one minus the other, and a figure in brackets
            is a shortfall.
          </p>
          {tierRows.length > 0 ? (
            <Grid
              head={["Tier", "Picks", "Won", "Needed", "Difference", "Units", "Status"]}
              rows={tierRows.map((t) => [
                <strong>{TIER_LABEL[t.edge_tier] ?? t.edge_tier}</strong>,
                t.picks.toLocaleString(),
                <strong>{pct(t.hit_rate)}</strong>,
                pct(t.breakeven),
                <Acct value={gap(t.hit_rate, t.breakeven)} scale={100} plus />,
                <Acct value={t.units} plus />,
                TIER_STATUS[t.edge_tier] ?? "",
              ])}
            />
          ) : (
            <p className="ps-model-dated">Loading the record…</p>
          )}
          {seasons && seasons.length > 0 && (
            <>
              <h3>The elite tier, season by season</h3>
              <Grid
                head={["Season", "Source", "Picks", "Won", "Needed", "Difference"]}
                rows={seasons.map((s) => [
                  String(s.season),
                  s.source === "live" ? "published live" : "reconstructed",
                  s.picks.toLocaleString(),
                  <strong>{pct(s.hit_rate)}</strong>,
                  pct(s.breakeven),
                  <Acct value={gap(s.hit_rate, s.breakeven)} scale={100} plus />,
                ])}
              />
            </>
          )}
          <p>
            Reconstructed seasons use models refit on earlier data only. For
            context, the naive approach of betting every disagreement lost 4.3%
            across the full 2025 season: unders beat their break-even in most
            markets while overs lost everywhere. Ranking the board and putting
            forward only the top of it exists to do better than that, not because
            the model predicts better than the market.
          </p>
          <p>
            Full detail by season, market and player is on the{" "}
            <Link to="/record">Track Record</Link>.
          </p>
        </Section>

        <Section id="infrastructure" title="Infrastructure">
          <ul>
            <li>
              This site is a static app on a CDN. The API, database and scheduled
              jobs run on a single server, because they need a persistent database,
              model files on disk and hour-long retraining runs.
            </li>
            <li>
              The database is never exposed publicly. The API accepts browser
              requests only from this site, and the endpoints that rebuild data or
              buy odds require a secret token.
            </li>
            <li>
              Odds spending is capped: prices are bought only close to kickoff,
              already-priced games are skipped, and every run stops before the
              account drops below a floor.
            </li>
          </ul>
          <Grid
            head={["When (Eastern)", "What runs"]}
            rows={[
              ["Hourly", "If a game kicks off within 90 minutes, buy its prices and rebuild the board"],
              ["Daily 8am", "New results, features, grading, calibration, prices for the next three days"],
              ["Daily 5pm", "Latest injury reports, projections and the board"],
              ["Friday 9am", "Price Sunday's games so the board is live from Friday"],
              ["Tuesday 1am", "Retrain every market after Monday night is graded"],
            ]}
          />
          <p>
            Every run ends with a seventeen-check freshness audit. It confirms every
            game-day feature is actually refreshed, opponent features describe the
            real opponent, each model&rsquo;s range matches its inputs, the board and
            projections agree, injury reports are current, kickoff dates match the
            schedule, and every figure this site states as fact matches the
            database. Any failure flags the run.
          </p>
        </Section>

        <Section id="lessons" title="Lessons from the bugs">
          <Grid
            head={["What went wrong", "What it taught"]}
            rows={[
              ["Months spent tuning a receiving-yards model that was actually broken by data plumbing: mismatched rows, a missing team column, a faulty evaluation script.", "Check the pipes before the model."],
              ["Projections were served from a window one game older than the models were trained on.", "Training and serving must see identical inputs."],
              ["A check meant to catch traded players was the thing silently dropping them.", "A safeguard can invert its own purpose."],
              ["Two team codes never matched the data source's spelling.", "Normalise identifiers at the boundary."],
              ["Expected-touchdown data was loaded and never used, because touchdowns weren't one of the feature categories.", "Dispatch logic can hide unused data."],
              ["Closing line value read zero for months because most props had only one captured price.", "Confirm a metric can move before trusting it."],
              ["A database restore reported success while one critical table failed to load.", "A success message is not verification."],
            ]}
          />
        </Section>

        <Section id="limits" title="Limitations">
          <ul>
            <li>
              The edge is small. Its confidence interval clears zero only at the
              best available price.
            </li>
            <li>
              Calibration lags early in each season, since no correction can
              anticipate a season nobody has played yet.
            </li>
            <li>
              The model has no late information: game-time decisions, changed game
              plans or weather an hour before kickoff.
            </li>
            <li>
              Most of the record is reconstructed. The live sample is still small.
            </li>
            <li>
              League production drifts downward a little each year, so older
              training seasons read slightly high.
            </li>
          </ul>
          <p>
            PriorLine is a research tool, not a tip sheet and not financial advice.
            It is built to be used alongside your own judgment.
          </p>
        </Section>
      </div>
    </>
  );
}
