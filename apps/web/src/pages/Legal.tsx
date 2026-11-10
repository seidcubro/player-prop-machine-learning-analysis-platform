/**
 * The fine print: terms, privacy, responsible gambling, accessibility.
 *
 * These are written to be read, in the same voice as the rest of the site,
 * and they describe what the site actually does. The privacy page in
 * particular is a description of the code and the server config as they stand:
 * no accounts, no cookies, no analytics, one access log. If any of that
 * changes, this file changes in the same commit.
 *
 * I am not a lawyer and these pages have not been reviewed by one. They are an
 * honest account, not a legal shield.
 */

import { Link } from "react-router-dom";

/** The date these four pages were last changed. One date, because they are
 *  edited together. */
const UPDATED = "9 November 2026";

const CONTACT = (import.meta.env.VITE_CONTACT_EMAIL as string | undefined)?.trim();

/** Where to write. Set at build time, so no address is committed to the repo.
 *  Until one is set the sentence says so plainly instead of printing a dead
 *  link. */
function Contact({ about }: { about: string }) {
  if (CONTACT) {
    return (
      <p>
        To get in touch about {about}, write to{" "}
        <a href={`mailto:${CONTACT}`}>{CONTACT}</a>.
      </p>
    );
  }
  return (
    <p>
      A contact address for {about} is being set up and will be published on
      this page.
    </p>
  );
}

function Dated() {
  return <p className="pl-prose-dated">Last updated {UPDATED}</p>;
}

/* ------------------------------------------------------------------ terms */

export function Terms() {
  return (
    <article className="pl-prose">
      <h1>Terms of use</h1>
      <Dated />
      <p>
        PriorLine is a free research site that publishes statistical
        projections for NFL players and compares them with the lines
        sportsbooks post. By using it you agree to what follows. If you do not
        agree, please do not use the site.
      </p>

      <h2>What this site is</h2>
      <p>
        Information and nothing else. Every number here is the output of a
        statistical model, published for research and entertainment. None of
        it is betting advice, financial advice, or a recommendation that you
        place any bet. The word pick on this site means the side of a line the
        model landed on. It does not mean I am telling you to bet it.
      </p>

      <h2>What this site is not</h2>
      <p>
        PriorLine is not a sportsbook, a betting operator, a tipster service,
        or an agent for one. It does not take bets, hold money, or place
        anything on your behalf. It has no accounts and nothing to buy. It
        carries no sportsbook links and takes no affiliate payments, so I earn
        nothing whether you bet or not.
      </p>

      <h2>No promise of accuracy or results</h2>
      <p>
        The model is wrong often, and the <Link to="/record">track record</Link>{" "}
        shows exactly how often. Past results do not predict future ones.
        Lines and prices are shown as they were when last retrieved and move
        constantly, so the price you are offered may be different or gone.
        Injuries, late scratches, weather and role changes can make a
        projection stale before kickoff.
      </p>
      <p>
        The site and everything on it are provided as they are, with no
        warranty of any kind, express or implied, including accuracy,
        completeness, availability, or fitness for any purpose.
      </p>

      <h2>You are responsible for what you do</h2>
      <p>
        If you bet, that is your decision, made at your own risk, and you are
        responsible for any loss. To the fullest extent the law allows, I am
        not liable for any loss or damage that comes from using this site or
        relying on anything published on it.
      </p>
      <p>
        You are also responsible for knowing the law where you are. Sports
        betting is illegal in some places and age-restricted everywhere it is
        legal. This site is intended for adults who are at least 21, or the
        legal betting age where they live if that is higher, and who are in a
        place where sports betting is lawful. Nothing here is an invitation to
        bet where it is not.
      </p>

      <h2>Names, teams and data</h2>
      <p>
        PriorLine is independent. It is not affiliated with, endorsed by, or
        sponsored by the National Football League, any of its teams, the NFL
        Players Association, or any sportsbook. Team names, player names and
        player photographs are used only to identify who a projection is
        about, and all trademarks belong to their owners. Game data comes from
        the public nflverse project and prices from a commercial odds feed.
      </p>

      <h2>Using the site fairly</h2>
      <p>
        Read it, link to it, quote it with a credit. Please do not hammer it
        with automated requests, try to break it, or republish the projections
        wholesale as your own. Requests are rate limited and abusive traffic
        may be blocked.
      </p>

      <h2>Changes</h2>
      <p>
        I may change the site or these terms at any time, and the date at the
        top will change with them. Continuing to use the site after a change
        means you accept the new version. If part of these terms turns out to
        be unenforceable, the rest still applies.
      </p>

      <Contact about="these terms" />
    </article>
  );
}

/* ---------------------------------------------------------------- privacy */

export function Privacy() {
  return (
    <article className="pl-prose">
      <h1>Privacy</h1>
      <Dated />
      <p>
        The short version: this site does not know who you are and is not
        trying to find out. There are no accounts, no cookies, no advertising,
        and no analytics or tracking scripts of any kind.
      </p>

      <h2>What is not collected</h2>
      <ul>
        <li>No name, email address, or login. There is nothing to sign up for.</li>
        <li>No cookies, and nothing saved in your browser&rsquo;s storage.</li>
        <li>
          No analytics, advertising pixels, fingerprinting, or third-party
          trackers. Fonts are served from this site, not from a font network.
        </li>
        <li>No payment details. Nothing is sold.</li>
        <li>Nothing is sold, rented, or shared with anyone for marketing.</li>
      </ul>

      <h2>What is collected</h2>
      <p>
        Like every website, the servers behind this one see the requests your
        browser sends them.
      </p>
      <ul>
        <li>
          <b>The data server.</b> When a page loads its numbers, the server
          that answers writes a standard access log line: your IP address, the
          time, what was requested, and the browser description your browser
          sent. The log is kept only to keep the service running and to
          diagnose faults and abuse. It rotates by size, and old files are
          deleted automatically as new ones are written. It is never combined
          with anything else or used to build a profile.
        </li>
        <li>
          <b>Rate limiting.</b> To stop one visitor from overloading the
          service, the server counts requests per IP address for a minute at a
          time. That count lives in memory and is not stored.
        </li>
        <li>
          <b>The web host.</b> The pages themselves are delivered by Vercel,
          which processes your IP address and request details to deliver them
          and keep its network secure, under its own privacy policy.
        </li>
      </ul>

      <h2>Requests your browser makes elsewhere</h2>
      <p>
        Player photographs are loaded directly from the NFL&rsquo;s public
        image servers. When a page shows a photo, your browser asks that
        server for it, and that server sees your IP address the way any
        website would. No other outside service is contacted.
      </p>
      <p>
        Links on the responsible gambling page lead to independent help
        organisations. Once you follow one, their privacy practices apply, not
        mine.
      </p>

      <h2>Your rights</h2>
      <p>
        Depending on where you live, laws such as the GDPR in Europe and the
        CCPA in California give you rights over personal data, including to
        ask what is held about you and to have it deleted. The only thing held
        here that could relate to you is an IP address in a rotating server
        log, which I cannot tie to a person. If you still want to ask about
        it, I will answer. Your browser&rsquo;s Do Not Track and Global
        Privacy Control signals need no special handling here, because there
        is no tracking or sale of data for them to switch off.
      </p>

      <h2>Children</h2>
      <p>
        This site is for adults of legal betting age. It is not directed at
        anyone under 21 and does not knowingly collect information from
        children.
      </p>

      <h2>Changes</h2>
      <p>
        If the site ever adds accounts, analytics, or anything else that
        touches personal data, this page will be rewritten before that ships,
        and the date at the top will change.
      </p>

      <Contact about="privacy" />
    </article>
  );
}

/* ---------------------------------------------------- responsible gambling */

export function ResponsibleGambling() {
  return (
    <article className="pl-prose">
      <h1>Responsible gambling</h1>
      <Dated />
      <p>
        This site publishes numbers about bets, so it should be straight about
        betting. Most people who bet on sports lose money over time. The
        sportsbook&rsquo;s cut is built into every price, and beating it
        consistently is rare. My own <Link to="/record">record</Link> shows
        how hard it is: most of what this model publishes has not beaten the
        price.
      </p>
      <p>
        Nothing here can make a bet safe. If you choose to bet, treat it as
        paid entertainment, never as income and never as a way out of debt.
      </p>

      <h2>If you bet, some rules that help</h2>
      <ul>
        <li>Decide what you can afford to lose before you start, and stop when it is gone.</li>
        <li>Never chase a loss with a bigger bet.</li>
        <li>Set deposit, loss and time limits in your sportsbook account. Every licensed book has them.</li>
        <li>Do not bet when you are upset, drunk, or trying to fix a bad day.</li>
        <li>Do not borrow money to bet, and do not bet money meant for rent or bills.</li>
        <li>Take breaks. A week without a bet tells you a lot.</li>
      </ul>

      <h2>Signs it has become a problem</h2>
      <ul>
        <li>Betting more than you planned, or more often.</li>
        <li>Hiding it, or lying about how much you have spent.</li>
        <li>Feeling restless or irritable when you try to stop.</li>
        <li>Betting to win back what you lost.</li>
        <li>Borrowing, selling things, or missing payments to keep betting.</li>
        <li>It getting in the way of work, sleep, or the people around you.</li>
      </ul>
      <p>
        If any of that sounds familiar, for you or for someone close to you,
        talking to someone is free and confidential.
      </p>

      <h2>Free, confidential help</h2>
      <ul className="pl-helplines">
        <li>
          <b>National Problem Gambling Helpline</b>
          <span>
            Call <a href="tel:18005224700">1-800-522-4700</a>, any
            hour, anywhere in the United States, or chat at{" "}
            <a href="https://www.ncpgambling.org/help-treatment/" rel="noopener noreferrer">
              ncpgambling.org
            </a>
            .
          </span>
        </li>
        <li>
          <b>1-800-GAMBLER</b>
          <span>
            Call <a href="tel:18004262537">1-800-426-2537</a> or visit{" "}
            <a href="https://www.1800gambler.net/" rel="noopener noreferrer">
              1800gambler.net
            </a>
            .
          </span>
        </li>
        <li>
          <b>Gamblers Anonymous</b>
          <span>
            Meetings in person and online at{" "}
            <a href="https://www.gamblersanonymous.org/" rel="noopener noreferrer">
              gamblersanonymous.org
            </a>
            .
          </span>
        </li>
        <li>
          <b>Gam-Anon</b>
          <span>
            For family and friends of someone who gambles, at{" "}
            <a href="https://www.gam-anon.org/" rel="noopener noreferrer">
              gam-anon.org
            </a>
            .
          </span>
        </li>
        <li>
          <b>988 Suicide and Crisis Lifeline</b>
          <span>
            If you are in crisis or thinking about harming yourself, call or
            text <a href="tel:988">988</a> now.
          </span>
        </li>
      </ul>

      <h2>Stepping away completely</h2>
      <p>
        Every state with legal sports betting runs a self-exclusion programme
        that bars you from its licensed sportsbooks for a period you choose.
        Your state gaming regulator&rsquo;s website has the form, and any
        licensed sportsbook has to honour it. Blocking software such as
        Gamban or BetBlocker can also keep betting sites off your devices.
      </p>

      <h2>Age and the law</h2>
      <p>
        Sports betting is for adults only, 21 or older in most states, and it
        is not legal everywhere. This site is not for anyone under the legal
        age, and nothing on it is an invitation to bet where betting is
        against the law.
      </p>
    </article>
  );
}

/* ---------------------------------------------------------- accessibility */

export function Accessibility() {
  return (
    <article className="pl-prose">
      <h1>Accessibility</h1>
      <Dated />
      <p>
        I want this site to be usable by everyone, including people who use a
        screen reader, a keyboard, a switch, voice control, or a zoomed-in
        screen. The target is the Web Content Accessibility Guidelines (WCAG)
        2.2 at level AA.
      </p>

      <h2>What has been done</h2>
      <ul>
        <li>
          Every page works with a keyboard alone, with a visible focus
          outline and a link at the top that skips straight to the content.
        </li>
        <li>
          Text and controls are checked against the AA contrast ratios on the
          paper and on the dark bands.
        </li>
        <li>
          Colour is never the only signal. A loss is red and also written in
          brackets, with the word minus for screen readers. A win is green and
          also carries a plus sign. Tiers are named, not just coloured.
        </li>
        <li>
          Tables are real tables with labelled headers, so a screen reader can
          say which column and row a number belongs to.
        </li>
        <li>
          Pages reflow down to a 320 pixel wide screen and text can be zoomed
          to 200% without losing content. Wide tables scroll inside their own
          frame.
        </li>
        <li>
          Animation is small, and it is switched off entirely when your device
          asks for reduced motion.
        </li>
        <li>Buttons and links are at least 24 pixels in each direction, and most are 44.</li>
      </ul>

      <h2>Known limits</h2>
      <ul>
        <li>
          This is my own assessment. The site has not been through an
          independent audit or tested with a panel of assistive technology
          users.
        </li>
        <li>
          The widest data tables are dense. They are fully readable by screen
          reader, but moving through them takes time.
        </li>
        <li>
          Player photographs are decorative and hidden from screen readers.
          The player&rsquo;s name is always in text beside them.
        </li>
      </ul>

      <h2>Tell me what is broken</h2>
      <p>
        If something on this site does not work for you, I want to know, and I
        will try to fix it quickly. It helps to say which page, what you were
        trying to do, and what browser or assistive technology you use.
      </p>
      <Contact about="accessibility" />
    </article>
  );
}
