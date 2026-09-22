/**
 * A title, a description and a canonical URL per page.
 *
 * This is a single-page app, so index.html is served for every route and every
 * page inherited the home page's head. That is not a cosmetic problem. The
 * canonical tag said `https://priorline.io/` on all eight routes, which is a
 * direct instruction to a search engine that the projections, the track record
 * and the model write-up are duplicates of the home page and should not be
 * indexed separately. Seven pages of the site were asking not to be found.
 *
 * Googlebot renders JavaScript, so setting these from React is enough: it runs
 * the page and reads the head afterwards. Other crawlers, including the ones
 * behind link previews in Slack, iMessage and X, read the raw HTML and never
 * run anything, which is why index.html keeps a sensible default set for the
 * site as a whole. These override them for whoever waits.
 *
 * No library. react-helmet and its successors exist for server rendering and
 * for merging tags across a nested tree, neither of which applies to one tag
 * of each kind set once per route.
 */

import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const SITE = (import.meta.env.VITE_SITE_URL as string | undefined)?.replace(/\/$/, "")
  ?? "https://priorline.io";

export type PageMeta = { title: string; description: string; noindex?: boolean };

/** Written for a person searching, not for a keyword counter. Every one of
 *  these says what the page actually shows, because a description that
 *  oversells is a bounce, and a bounce is worse than a lower ranking. */
export const PAGES: Record<string, PageMeta> = {
  "/": {
    title: "PriorLine | NFL player prop projections vs sportsbook lines",
    description:
      "A machine-learning model projects every NFL skill player and quarterback, "
      + "then prices those projections against real sportsbook lines to show where "
      + "the two disagree. Free, with the full track record published.",
  },
  "/signals": {
    title: "Today's NFL prop signals | PriorLine",
    description:
      "Where the model and the sportsbook disagree most on today's NFL player "
      + "props, with the model's chance of each side hitting and the price it "
      + "needs to be worth taking.",
  },
  "/projections": {
    title: "NFL player prop projections this week | PriorLine",
    description:
      "Projected receptions, receiving and rushing yards, passing yards, attempts "
      + "and completions for every NFL skill player and quarterback this week, "
      + "beside the posted line.",
  },
  "/record": {
    title: "Track record: how often PriorLine is right | PriorLine",
    description:
      "Every published pick, graded against what actually happened. Hit rate, "
      + "return and calibration by confidence tier, including the weeks the model "
      + "lost money.",
  },
  "/faq": {
    title: "How PriorLine works | PriorLine",
    description:
      "How the projections are built, in plain English and in detail: the data, "
      + "the models, how a projection becomes a probability, and what the model "
      + "is measurably bad at.",
  },
  "/model": {
    title: "The model behind PriorLine | PriorLine",
    description:
      "The machine-learning stack behind the projections: features, model "
      + "families per market, calibration, and the validation rule a change has "
      + "to clear before it ships.",
  },
  "/players": {
    title: "NFL player search | PriorLine",
    description:
      "Search any NFL skill player or quarterback for their projections, recent "
      + "usage and how the model has scored them this season.",
  },
};

/** A player page is one of hundreds, so it is named at render time. */
export function playerMeta(name: string): PageMeta {
  return {
    title: `${name} prop projections and usage | PriorLine`,
    description:
      `${name}'s projected props this week, recent usage and snap share, and how `
      + "the model's projections have scored against the posted lines.",
  };
}

function set(selector: string, attr: string, value: string) {
  const el = document.head.querySelector(selector);
  if (el) el.setAttribute(attr, value);
}

/**
 * Apply a page's meta. Pass nothing on a route that sets its own later (a
 * player page waits for the name to load) and the path's entry is used.
 */
export function usePageMeta(meta?: PageMeta) {
  const { pathname } = useLocation();
  useEffect(() => {
    const m = meta ?? PAGES[pathname];
    if (!m) return;
    document.title = m.title;
    set('meta[name="description"]', "content", m.description);
    set('meta[property="og:title"]', "content", m.title);
    set('meta[property="og:description"]', "content", m.description);
    set('meta[name="twitter:title"]', "content", m.title);
    set('meta[name="twitter:description"]', "content", m.description);
    // The whole reason this file exists.
    set('link[rel="canonical"]', "href", SITE + pathname);
    set('meta[property="og:url"]', "content", SITE + pathname);

    /*
     * A missing page here answers 200, not 404, because Vercel rewrites every
     * unmatched path to index.html so the router can handle it. A search
     * engine reads that as a real page and files a mistyped or dead URL
     * alongside the real ones. There is no status code to fix from inside the
     * app, so the 404 page says not to index it instead. Created on demand:
     * index.html carries no robots tag, and the normal case should not ship
     * one.
     */
    let robots = document.head.querySelector('meta[name="robots"]');
    if (m.noindex) {
      if (!robots) {
        robots = document.createElement("meta");
        robots.setAttribute("name", "robots");
        document.head.appendChild(robots);
      }
      robots.setAttribute("content", "noindex, follow");
    } else if (robots) {
      robots.remove();
    }
  }, [pathname, meta?.title, meta?.description, meta?.noindex]);
}
