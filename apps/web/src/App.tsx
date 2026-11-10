/**
 * App shell: the binding the pages sit in.
 *
 * The masthead and the footer are the cloth cover of a ledger, with the name
 * stamped in gold, and everything between them is paper. The sections run
 * across the top as words, the current one underlined, and on a phone the name
 * takes its own line and the sections scroll sideways beneath it.
 *
 * The footer carries the things that have to be on every page: what this site
 * is not, who it is not connected to, where to get help, and the fine print.
 */

import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import Logo from "./components/Logo";
import { asPct, useHeadlineRecord } from "./lib/headline-record";
import { usePageMeta } from "./lib/page-meta";

const NAV = [
  { to: "/signals", label: "Signals" },
  { to: "/projections", label: "Projections" },
  { to: "/record", label: "Track Record" },
  { to: "/faq", label: "How It Works" },
  { to: "/players", label: "Players" },
];

const FOOT = [
  {
    head: "The book",
    links: [
      { to: "/signals", label: "Today's signals" },
      { to: "/projections", label: "Every projection" },
      { to: "/record", label: "Track record" },
      { to: "/players", label: "Player search" },
    ],
  },
  {
    head: "The method",
    links: [
      { to: "/faq", label: "How it works" },
      { to: "/model", label: "Inside the model" },
    ],
  },
  {
    head: "The fine print",
    links: [
      { to: "/terms", label: "Terms of use" },
      { to: "/privacy", label: "Privacy" },
      { to: "/responsible-gambling", label: "Responsible gambling" },
      { to: "/accessibility", label: "Accessibility" },
    ],
  },
];

/** A ledger numbers its leaves. These are this one's. */
const FOLIO: Record<string, number> = {
  "/": 1,
  "/signals": 2,
  "/projections": 3,
  "/record": 4,
  "/faq": 5,
  "/model": 6,
  "/players": 7,
  "/terms": 8,
  "/privacy": 9,
  "/responsible-gambling": 10,
  "/accessibility": 11,
};

export default function App() {
  // The two figures the footer quotes about the model's own accuracy.
  const rec = useHeadlineRecord();
  // Title, description and canonical for whichever route is showing. A page
  // that names itself (a player) calls this again with its own.
  usePageMeta();
  const { pathname } = useLocation();
  const folio = FOLIO[pathname] ?? (pathname.startsWith("/players/") ? 7 : null);

  return (
    <div className="ps-shell">
      <a className="pl-skip" href="#page">
        Skip to the page
      </a>
      <header className="ps-mast">
        <div className="ps-mast-inner">
          <Link to="/" className="ps-mast-brand" aria-label="PriorLine home">
            <Logo size={30} title="" />
            <span>PriorLine</span>
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

      <main className="ps-main" id="page" tabIndex={-1}>
        <Outlet />
      </main>

      <footer className="pl-foot">
        <div className="pl-foot-inner">
          <div className="pl-foot-top">
            <div className="pl-foot-brand">
              <Link to="/" className="pl-foot-name" aria-label="PriorLine home">
                <Logo size={44} title="" />
                <span>PriorLine</span>
              </Link>
              <p>Our prior. Their line. Every pick graded, every loss left in.</p>
            </div>
            <nav className="pl-foot-nav" aria-label="Footer">
              {FOOT.map((col) => (
                <div key={col.head}>
                  <h2>{col.head}</h2>
                  <ul>
                    {col.links.map((l) => (
                      <li key={l.to}>
                        <Link to={l.to}>{l.label}</Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </nav>
          </div>

          {/*
            On every page, not buried in a FAQ nobody opens.

            This is a research tool. It publishes a model's opinion next to a
            sportsbook's price and it is wrong a great deal of the time, which
            the track record says out loud. Anyone reading it as a tip sheet is
            using it in the one way that will lose them money.
          */}
          <div className="pl-foot-note">
            <p>
              <b>PriorLine is a research tool, not a tip sheet.</b> Every number
              here is a model estimate set against a sportsbook&rsquo;s price.
              Nothing on this site is a prediction, a guarantee, or advice to
              place a bet, and no outcome is promised. The model is wrong often.{" "}
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
              The <Link to="/record">track record</Link> shows all of it,
              including the parts that do not flatter it.
            </p>
            <p>
              It is built to be used with your own judgment, not instead of it.
              It does not know that a starter is being eased back from injury,
              that a team has changed how it uses a player, or what the weather
              turned into an hour before kickoff. Those are yours to bring.
            </p>
          </div>

          <div className="pl-foot-rg">
            <span className="pl-foot-age" aria-label="For adults aged 21 and over">
              21+
            </span>
            <p>
              For adults of legal betting age where sports betting is legal.
              If gambling stops being fun, call <b>1-800-522-4700</b>, any hour,
              free and confidential, or visit{" "}
              <a
                href="https://www.ncpgambling.org/help-treatment/"
                target="_blank"
                rel="noopener noreferrer"
              >
                ncpgambling.org
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
              . Bet only what you can afford to lose.{" "}
              <Link to="/responsible-gambling">More help and tools</Link>.
            </p>
          </div>

          <div className="pl-foot-base">
            <p>
              &copy; {new Date().getFullYear()} PriorLine. Independent. No
              sportsbook pays for anything here and no link on this site leads
              to one. Not affiliated with, endorsed by, or sponsored by the
              National Football League, its teams, the NFL Players Association,
              or any sportsbook. Team and player names are used only to
              identify them. Prices are shown as last retrieved and move
              constantly.
            </p>
            {folio !== null && (
              <p className="pl-foot-folio" aria-hidden="true">
                Folio {folio}
              </p>
            )}
          </div>
        </div>
      </footer>
    </div>
  );
}
