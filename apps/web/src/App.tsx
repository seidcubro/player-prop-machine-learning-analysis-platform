/**
 * App shell: a masthead across the top and the page underneath.
 *
 * The navigation used to be an icon rail down the left side that turned into a
 * bottom tab bar on phones, which is the layout of a software dashboard. This is
 * a research site people read, so it is laid out like one: the name on the
 * left, the sections as words on the right, and the current page underlined.
 * On a phone the name takes its own line and the sections scroll sideways
 * beneath it.
 */

import { Link, NavLink, Outlet } from "react-router-dom";
import Logo from "./components/Logo";
import { asPct, useHeadlineRecord } from "./lib/headline-record";

const NAV = [
  { to: "/signals", label: "Signals" },
  { to: "/projections", label: "Projections" },
  { to: "/record", label: "Track Record" },
  { to: "/faq", label: "How It Works" },
  { to: "/players", label: "Players" },
];

export default function App() {
  // The two figures the disclaimer quotes about the model's own accuracy.
  const rec = useHeadlineRecord();
  return (
    <div className="ps-shell">
      <header className="ps-mast">
        <div className="ps-mast-inner">
          <Link to="/" className="ps-mast-brand" aria-label="PriorLine home">
            <Logo size={30} title="" />
            <span>
              Prior<span className="sig">Line</span>
            </span>
          </Link>
          <nav className="ps-mast-nav" aria-label="Main">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} className="ps-mast-link">
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="ps-main">
        <Outlet />

        {/*
          On every page, not buried in a FAQ nobody opens.

          This is a research tool. It publishes a model's opinion next to a
          sportsbook's price and it is wrong a great deal of the time, which the
          track record says out loud. Anyone reading it as a tip sheet is using
          it in the one way that will lose them money, and saying so plainly is
          both the honest thing and the thing that keeps this an analysis site
          rather than a tipster service.
        */}
        <footer className="ps-disclaimer">
          <p>
            <b>PriorLine is a research tool, not a tip sheet.</b> Every number
            here is a model estimate against a sportsbook&rsquo;s price. Nothing
            on this site is a prediction, a guarantee, or advice to place a bet,
            and no outcome is promised. The model is wrong often.{" "}
            {/* Read from the record, not typed in. See lib/headline-record. */}
            {rec ? (
              <>
                It has hit <b>{asPct(rec.hit)}</b> of its graded picks while
                claiming {asPct(rec.claimed)}, and whole tiers of it have lost
                money.
              </>
            ) : (
              <>Whole tiers of it have lost money.</>
            )}{" "}
            The{" "}
            <Link to="/record">track record</Link> shows all of it, including
            the parts that do not flatter it.
          </p>
          <p>
            It is built to be used with your own judgment, not instead of it. It
            does not know that a starter is being eased back from injury, that a
            team has changed how it uses a player, or what the weather turned
            into an hour before kickoff. Those are yours to bring.
          </p>
          <p className="fine">
            21+ where legal. Nothing here is financial advice. Not affiliated
            with, endorsed by, or partnered with any sportsbook. Odds are shown
            as last retrieved and move constantly; always check the current
            price at the book. If gambling stops being fun, call{" "}
            <b>1-800-522-4700</b> or visit{" "}
            <a href="https://www.ncpgambling.org/help-treatment/"
               target="_blank" rel="noopener noreferrer">
              ncpgambling.org
            </a>
            . Bet only what you can afford to lose.
          </p>
        </footer>
      </main>
    </div>
  );
}
