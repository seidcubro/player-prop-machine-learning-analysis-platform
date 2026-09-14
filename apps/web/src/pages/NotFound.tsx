/**
 * Anything that is not a route.
 *
 * There was no catch-all, so an unknown path rendered the shell with an empty
 * body: no message, no heading, nothing to click. A stale link or a typo got a
 * blank page that looked like the site was broken rather than like the address
 * was wrong. `vercel.json` rewrites every unmatched path to index.html so the
 * router can handle deep links, which means the router is the only thing that
 * can answer this.
 */
import { Link } from "react-router-dom";
import PageTitle from "../components/PageTitle";

export default function NotFound() {
  return (
    <>
      <PageTitle lead="Page" accent="not found" />
      <div className="ps-empty">
        <p>
          The link may be out of date, or the address may have a typo in it.
        </p>
        <p style={{ marginTop: "0.75rem" }}>
          <Link to="/">Signals</Link> · <Link to="/projections">Projections</Link>{" "}
          · <Link to="/record">Track Record</Link> ·{" "}
          <Link to="/players">Players</Link> · <Link to="/faq">How It Works</Link>
        </p>
      </div>
    </>
  );
}
