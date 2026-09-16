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
      <h2>{q}</h2>
      {children}
    </div>
  );
}

export default function Faq() {
  const [audience, setAudience] = useState<Audience>("plain");

  return (
    <>
      <div className="ps-hero">
        <PageTitle lead="How PriorLine" accent="Works" />
        <p>
          Two versions of the same answers. Neither one is the sales pitch: the
          numbers below are what the model has actually done, including where it
          has been wrong. For the full method, read{" "}
          <Link to="/model">Inside the Model</Link>.
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
          whether the real number lands over or under. PriorLine predicts what
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
            <strong>Edge</strong> is our number minus the line, always in that
            order. Green means our number is above the line, which is why the
            pick is the over. Red means it is below, and the pick is the under.
            The pick always follows the sign, so the two can never disagree.
          </li>
          <li>
            <strong>EV</strong> is the one that matters. It is what a unit
            staked returns on average at this price, so +10% means a dollar bet
            repeatedly returns ten cents. Zero or below means the price already
            covers our estimate.
          </li>
          <li>
            EV is not the same as beating the price on probability alone. A
            three point edge is worth twice as much at +150 as it is at
            &minus;280, because the winning bets pay twice as much. The tiers
            are still cut on the probability edge, since that is the quantity
            their thresholds were measured against.
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
          It is the selection that has been tested on a season the model had
          never seen and still made money: the strongest picks, on the under
          side, one per player per game. Over that test it returned about 4.4%
          per unit bet, with a 95% interval of [+0.2%, +8.2%].
        </p>
        <p>
          That number used to read 6.6%, on a sample that quietly excluded night
          games. Sunday and Monday night kickoffs fall after midnight UTC, and a
          date conversion elsewhere in the pipeline dropped them from the test
          set. Putting them back added about a thousand picks and cut the
          measured return by a third. Night games are not a random sample of
          football, and the edge is smaller once they count.
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
            on&rdquo;, which excluded half the players who actually receive a
            rushing line: 49.7% of the 181 players priced on it sit below the
            threshold that proxy used. rush_yds was claiming 73.4% against an
            actual 52.1%.
          </li>
          <li>
            <strong>Isotonic regression on graded results.</strong> The
            published probability still ran fourteen points high, and the filter
            at the time ranked on probability minus break-even, so an inflated
            probability meant the &ldquo;highest EV&rdquo; selection was picking
            negative-EV bets. Out of sample this moved Brier from 0.2706 to
            0.2511.
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
        <p>
          Verified on the 2025 holdout, slate-clustered 95% intervals.
        </p>
        <div className="ps-tablewrap">
          <table className="ps-table is-matrix">
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
                <td className="num">5,532</td>
                <td className="num">+1.0%</td>
                <td className="num">[&minus;1.5%, +3.3%]</td>
              </tr>
              <tr>
                <td>Unders only</td>
                <td className="num">4,099</td>
                <td className="num">+2.0%</td>
                <td className="num">[&minus;0.6%, +4.2%]</td>
              </tr>
              <tr>
                <td>Elite + strong unders</td>
                <td className="num">3,133</td>
                <td className="num pos">+3.0%</td>
                <td className="num">[+0.4%, +5.5%]</td>
              </tr>
              <tr>
                <td>Elite unders, one per player-game</td>
                <td className="num">1,540</td>
                <td className="num pos">+4.4%</td>
                <td className="num">[+0.3%, +8.2%]</td>
              </tr>
              <tr>
                <td>Overs only</td>
                <td className="num">1,433</td>
                <td className="num neg">&minus;1.8%</td>
                <td className="num">[&minus;8.7%, +5.8%]</td>
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
            <strong>The touchdown projections have never been graded.</strong>
            Not once. Books do not post split rushing or receiving touchdown
            markets, and the anytime market is held off the board, so there is
            no line to score those numbers against and no row in the record for
            any of them. Every other market on the projections page has been
            checked against outcomes hundreds or thousands of times. The
            touchdown columns have been checked zero times, and should be read
            as an untested output of the same pipeline rather than as something
            with a record behind it.
          </li>
          <li>
            <strong>pass_td quantiles are degenerate</strong> below the median,
            because touchdowns are a small integer count. A Poisson survival
            alternative was tested twice and lost on Brier both times.
          </li>
          <li>
            <strong>Only two markets have a corrected range.</strong> Measured
            on the player-games a sportsbook actually posted a line for, the
            published range was too wide at the bottom and its median sat near
            the 44th percentile rather than the 50th: outcomes fell below the
            stated 10th percentile about 5% of the time and above the 90th about
            13%. Receiving yards and receptions are corrected for that, one
            quantile level at a time, and only where the correction beat the
            published value on a held-out season. Every other market still shows
            the uncorrected range, because there are not yet enough priced games
            in its history to fit one. They join as the record grows.
          </li>
          <li>
            <strong>Rush attempt quantiles do not condition on the player at
            the bottom of the range.</strong> The 10th percentile comes out at
            zero carries for every player, because 12% of running back game rows
            are players who dressed and never touched the ball. Coverage looks
            right in aggregate, 0.098 against a target of 0.10, but that is an
            average of over-covering low usage backs at 0.56 and under-covering
            starters at 0.06. For the highest usage games the actual 10th
            percentile is one carry. Refitting on the priced population only was
            tested and moved the error rather than removing it. No published
            line falls below the 10th percentile, so this affects the displayed
            range and not any pick.
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
