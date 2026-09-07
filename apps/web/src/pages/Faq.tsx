/**
 * How the thing works, twice.
 *
 * Two audiences with genuinely different questions. Someone deciding whether to
 * trust a number wants to know what it means and how often it has been right.
 * Someone evaluating the method wants the model families, the validation
 * discipline and the places it fails.
 *
 * Writing one document for both produces something that serves neither, so
 * there are two, and a toggle. Both say the same things and neither is the
 * marketing version: the numbers here are the measured ones including the ones
 * that are unflattering.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import PageTitle from "../components/PageTitle";

type Audience = "plain" | "technical";

function QA({ q, children }: { q: string; children: React.ReactNode }) {
  return (
    <div className="ps-qa">
      <h3>{q}</h3>
      {children}
    </div>
  );
}

export default function Faq() {
  const [audience, setAudience] = useState<Audience>("plain");

  return (
    <>
      <div className="ps-hero">
        <PageTitle lead="How PropSignal" accent="Works" />
        <p>
          Two versions of the same answers. Neither one is the sales pitch: the
          numbers below are what the model has actually done, including where it
          has been wrong.
        </p>
      </div>

      <div className="ps-segmented" role="tablist" aria-label="Explanation depth">
        <button
          role="tab"
          aria-selected={audience === "plain"}
          className={audience === "plain" ? "active" : ""}
          onClick={() => setAudience("plain")}
        >
          Plain English
        </button>
        <button
          role="tab"
          aria-selected={audience === "technical"}
          className={audience === "technical" ? "active" : ""}
          onClick={() => setAudience("technical")}
        >
          For Data Scientists
        </button>
      </div>

      {audience === "plain" ? <Plain /> : <Technical />}
    </>
  );
}

function Plain() {
  return (
    <div className="ps-faq">
      <QA q="What is this?">
        <p>
          A sportsbook posts a line for a player, say 4.5 receptions. You bet
          whether the real number lands over or under. PropSignal predicts what
          each player will actually do, compares that to the posted line, and
          shows you where the two disagree.
        </p>
      </QA>

      <QA q="How does it make a prediction?">
        <p>
          It looks at what a player has done recently, how big their role is,
          who they are playing, where the game is, what the weather is doing,
          and what Las Vegas expects the game to look like. Roughly seventy
          pieces of information per player per week, all of them known before
          kickoff.
        </p>
        <p>
          It learned the relationship between those inputs and real outcomes
          from four seasons of NFL games. Nothing is hand-picked by me each
          week.
        </p>
      </QA>

      <QA q="What do the columns mean?">
        <ul>
          <li>
            <strong>Line</strong> is the sportsbook&rsquo;s number and the price
            beneath it.
          </li>
          <li>
            <strong>Model</strong> is the middle of what we expect. Half the
            time the player goes over it, half the time under.
          </li>
          <li>
            <strong>Edge</strong> is how far our number sits from the line, in
            the direction of the pick. Green means our own number backs the bet.
            Red means it does not, and the bet only makes sense because the price
            is generous.
          </li>
          <li>
            <strong>EV</strong> is the one that matters. It is how much better
            our estimated chance is than the chance the price is charging you
            for. Zero or below means the price already covers it.
          </li>
        </ul>
      </QA>

      <QA q="Why is a 60% chance not automatically a good bet?">
        <p>
          Because the price decides how often you have to win. At &minus;200 you
          need to win 67% of the time just to break even, so a 60% pick loses
          money. At +150 you only need 40%, so the same 60% pick is excellent.
          That is why EV, not the win percentage, is what the board is sorted
          on.
        </p>
      </QA>

      <QA q="What is a Best Bet?">
        <p>
          It is the only selection that has been tested on a season the model
          had never seen and still made money: the strongest picks, on the under
          side, one per player per game. Over that test it returned about 7% per
          unit bet.
        </p>
        <p>
          The rest of the board is research. Best Bets are the part that has
          actually been checked.
        </p>
      </QA>

      <QA q="Why are almost all of them unders?">
        <p>
          Because that is where the mistake is. Most people betting player props
          are betting overs, so sportsbooks nudge those numbers up until the
          over stops being worth it. Across three seasons the over side lost
          money and the under side made it. We did not choose that, we measured
          it.
        </p>
      </QA>

      <QA q="How often is it right?">
        <p>
          The full board is close to break-even. The Best Bets selection is
          ahead. Every number on the{" "}
          <Link to="/record">Track Record</Link> page is graded against what
          really happened, including the losing seasons, and you can filter it
          by season and by market.
        </p>
      </QA>

      <QA q="Should I bet my rent on this?">
        <p>
          No. The measured advantage is a few percent. That is real, and it is
          also small enough that a bad month proves nothing and a good month
          proves nothing either. It takes thousands of bets before the edge
          separates from luck, and the effect has been getting smaller each
          season as the market gets sharper.
        </p>
        <p>
          This is a research tool that shows its own record honestly. It is not
          a guarantee, and anybody offering you one of those is lying.
        </p>
      </QA>
    </div>
  );
}

function Technical() {
  return (
    <div className="ps-faq">
      <QA q="Pipeline">
        <p>
          nflverse play-by-play, snap counts, depth charts, injuries and Next Gen
          Stats into Postgres. Rolling and situational features per player per
          market at a five-game lookback, about seventy per row, all restricted
          to information available before kickoff. Odds from The Odds API across
          three books.
        </p>
      </QA>

      <QA q="Models">
        <p>
          Per-market family selection rather than one architecture everywhere,
          chosen on expanding-window time-series cross-validation with a
          one-standard-error rule so noise does not get promoted. Linear models
          win three markets; gradient boosting overfits several and loses.
          Currently ridge, elastic net, extra trees, and histogram gradient
          boosting with Poisson deviance for the count markets.
        </p>
        <p>
          Quantile ensembles (q10 through q90) give a predicted CDF per
          player-game, so P(over) is read off a real distribution rather than an
          assumed Gaussian around a point estimate.
        </p>
      </QA>

      <QA q="Calibration, which is where most of the work went">
        <p>
          Three separate corrections, each fixing a measured failure:
        </p>
        <ul>
          <li>
            <strong>Probability integral transform on the quantile CDF</strong>,
            fitted on a held-out season rather than cross-validated folds. Folds
            land mid-season, so a model scored against its own era looks
            calibrated and the map comes back as the identity.
          </li>
          <li>
            <strong>Fitted on the priced population.</strong> An early version
            used a snap-share proxy for &ldquo;players books post lines
            on&rdquo;, which excluded 43% of players who actually receive a
            rushing line. rush_yds was claiming 73.4% against an actual 52.1%.
          </li>
          <li>
            <strong>Isotonic regression on graded results.</strong> The
            published probability still ran fourteen points high, and since EV
            is probability minus break-even, an inflated probability meant the
            &ldquo;highest EV&rdquo; filter was selecting negative-EV bets.
            Out of sample this moved Brier from 0.2706 to 0.2511.
          </li>
        </ul>
        <p>
          Isotonic rather than Platt because the error varies with the claimed
          probability rather than being a constant shift.
        </p>
      </QA>

      <QA q="Does the model actually rank?">
        <p>
          AUC of the win probability against realised outcomes is 0.547 overall
          (pass_yds 0.589, pass_td 0.578, receptions 0.559). Modest, but clear of
          0.50, which is what made recalibration worth doing rather than
          hopeless. That was the prerequisite question: with no discrimination,
          no monotone correction helps and the tiers would be decoration.
        </p>
      </QA>

      <QA q="Validation discipline">
        <ul>
          <li>
            Models refit per season on strictly earlier data. The track record
            for 2024 uses nothing from 2024 onward.
          </li>
          <li>
            Bootstraps resample <em>whole slates</em>, not picks. 71.5% of
            player-games carry more than one pick and receptions and receiving
            yards on the same player are strongly correlated, so a pick-level
            bootstrap makes every interval far too narrow.
          </li>
          <li>
            Best price per side at the line the offering book posted. Averaging
            American odds is invalid: the mean of &minus;130 and +100 is
            &minus;15, implying a payout nobody offered.
          </li>
          <li>
            Strategies chosen on 2023&ndash;24 and verified on 2025, which is
            never used for selection.
          </li>
        </ul>
      </QA>

      <QA q="Results">
        <p>Verified on the 2025 holdout, slate-clustered 95% intervals:</p>
        <div className="ps-tablewrap">
          <table className="ps-table">
            <thead>
              <tr>
                <th>Selection</th>
                <th className="num">n</th>
                <th className="num">ROI</th>
                <th className="num">95% CI</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Board as published</td>
                <td className="num">4,652</td>
                <td className="num">+0.9%</td>
                <td className="num">[&minus;1.7%, +4.0%]</td>
              </tr>
              <tr>
                <td>Unders only</td>
                <td className="num">2,884</td>
                <td className="num pos">+4.4%</td>
                <td className="num">[+1.8%, +7.2%]</td>
              </tr>
              <tr>
                <td>Elite unders, one per player-game</td>
                <td className="num">783</td>
                <td className="num pos">+7.1%</td>
                <td className="num">[+1.6%, +13.2%]</td>
              </tr>
              <tr>
                <td>Overs only</td>
                <td className="num">1,768</td>
                <td className="num neg">&minus;4.7%</td>
                <td className="num">[&minus;10.3%, +1.4%]</td>
              </tr>
            </tbody>
          </table>
        </div>
      </QA>

      <QA q="Known limitations">
        <ul>
          <li>
            <strong>No closing line value yet.</strong> The odds history holds
            one snapshot per event, so there is nothing to measure line movement
            against. CLV converges far faster than win/loss and is the standard
            evidence of edge; capture is running going forward.
          </li>
          <li>
            <strong>Week 1 is the worst week of the year.</strong> Every feature
            row is built from games played months ago, and no calibration can
            anticipate a regime it has not observed. It tightens as the weekly
            retrain picks up current games.
          </li>
          <li>
            <strong>Season drift.</strong> Per-row production falls roughly 3% a
            year, so models trained on older seasons read high.
          </li>
          <li>
            <strong>The effect is shrinking.</strong> The over-shade edge went
            from +10.4% in 2024 to +2.6% in 2025. That is what a market getting
            sharper looks like.
          </li>
          <li>
            <strong>pass_td quantiles are degenerate</strong> below the median,
            because touchdowns are a small integer count. A Poisson survival
            alternative was tested twice and lost on Brier both times.
          </li>
        </ul>
      </QA>

      <QA q="What would falsify this?">
        <p>
          Sustained negative closing line value would, and faster than a losing
          balance would. If the picks consistently fail to beat the number the
          market settles on, the edge is not real regardless of what any
          individual week returns. That is the measurement I most want and do not
          yet have.
        </p>
      </QA>
    </div>
  );
}
