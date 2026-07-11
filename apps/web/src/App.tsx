/**
 * App shell: sticky navbar (logo + nav) and routed page content.
 */

import { NavLink, Outlet } from "react-router-dom";
import Logo from "./components/Logo";

export default function App() {
  return (
    <div className="ps-shell">
      <header>
        <nav className="ps-nav" aria-label="Main">
          <NavLink to="/" className="ps-nav-brand">
            <Logo size={30} title="" />
            <span>
              Prop<span className="sig">Signal</span>
            </span>
          </NavLink>
          <div className="ps-nav-links">
            <NavLink to="/" end>
              Edges
            </NavLink>
            <NavLink to="/players">Players</NavLink>
          </div>
        </nav>
      </header>
      <main className="ps-main">
        <Outlet />
      </main>
    </div>
  );
}
