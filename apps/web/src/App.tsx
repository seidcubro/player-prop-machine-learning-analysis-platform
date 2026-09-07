/**
 * App shell: a fixed left rail on desktop, a bottom bar on phones.
 *
 * The nav moved off the top for a practical reason rather than a stylistic one.
 * These are dense data pages, the tables are wide, and vertical space is the
 * scarce resource: a top bar costs about 70px of every screen forever, whereas
 * a left rail costs horizontal space that the layout has to spare. It also
 * scales, which a pill row does not. Four items fit across the top; nine do
 * not, and there are already six.
 *
 * Below 960px the rail becomes a bottom bar, because a phone has the opposite
 * problem and a thumb reaches the bottom of the screen far more easily than the
 * top.
 */

import type { ReactElement } from "react";
import { NavLink, Outlet } from "react-router-dom";
import Logo from "./components/Logo";

type Item = { to: string; label: string; icon: ReactElement; end?: boolean };

/**
 * Icons are inline SVG rather than an icon package.
 *
 * Six glyphs is not worth a dependency, and shipping them inline means they
 * inherit `currentColor` and animate with the link instead of being a separate
 * font or sprite that loads late and pops.
 */
const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.9,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const NAV: Item[] = [
  {
    to: "/",
    label: "Signals",
    end: true,
    icon: (
      <svg viewBox="0 0 24 24" {...stroke}>
        <path d="M3 17l5-6 4 4 5-8" />
        <path d="M17 7h4v4" />
      </svg>
    ),
  },
  {
    to: "/projections",
    label: "Projections",
    icon: (
      <svg viewBox="0 0 24 24" {...stroke}>
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
      </svg>
    ),
  },
  {
    to: "/record",
    label: "Track Record",
    icon: (
      <svg viewBox="0 0 24 24" {...stroke}>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 7v5l3.5 2" />
      </svg>
    ),
  },
  {
    to: "/faq",
    label: "How It Works",
    icon: (
      <svg viewBox="0 0 24 24" {...stroke}>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M9.6 9.2a2.5 2.5 0 1 1 3.3 2.4c-.6.2-.9.7-.9 1.3v.4" />
        <path d="M12 16.6v.01" />
      </svg>
    ),
  },
  {
    to: "/players",
    label: "Players",
    icon: (
      <svg viewBox="0 0 24 24" {...stroke}>
        <circle cx="12" cy="8" r="3.6" />
        <path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" />
      </svg>
    ),
  },
];

export default function App() {
  return (
    <div className="ps-shell">
      {/*
        A narrow-screen header carrying the mark alone.

        On a phone the rail becomes a bottom bar, and a bottom bar has no room
        for a lockup. Without this the product had no name anywhere on screen,
        which is not a state a brand should ever be in. Hidden on desktop, where
        the rail already shows it.
      */}
      <header className="ps-topbar" aria-hidden="true">
        <Logo size={26} title="" />
        <span className="ps-topbar-word">
          Prop<span className="sig">Signal</span>
        </span>
      </header>

      <aside className="ps-rail" aria-label="Main">
        <NavLink to="/" className="ps-rail-brand" aria-label="PropSignal home">
          <Logo size={30} title="" />
          <span className="ps-rail-word">
            Prop<span className="sig">Signal</span>
          </span>
        </NavLink>

        <nav className="ps-rail-nav">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className="ps-rail-link">
              <span className="ps-rail-icon" aria-hidden="true">
                {item.icon}
              </span>
              <span className="ps-rail-label">{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="ps-rail-foot">
          <span className="ps-rail-label">Bet the signal, not the noise</span>
        </div>
      </aside>

      <main className="ps-main">
        <Outlet />
      </main>
    </div>
  );
}
