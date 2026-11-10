/**
 * Track record: how the picks have actually done, season by season.
 *
 * This page exists because the old per-player record was two games from one
 * afternoon in December 2023 and still printed a hit rate next to them. The
 * backfill now reconstructs every season we hold closing lines for, using models
 * refit on strictly earlier data, which is thousands of graded picks instead of
 * 283. Every figure here is fetched from the API and computed from the graded
 * picks on each request, so none of it can go stale the way a typed-in number
 * does. (apps/web/src/lib/published-record.json is NOT read by this page or
 * any other; it is only the snapshot audit check [16] compares the database
 * against. Nothing on the site is served from it.)
 *
 * The page is built to be checkable rather than flattering:
 *
 *  - **Predicted sits next to actual, everywhere.** If the model says 66% and
 *    hits 53%, that gap is the most important number on the site and it is not
 *    going to be buried.
 *  - **Sample size is always visible.** A rate without its n invites exactly the
 *    mistake the old page made.
 *  - **ROI, not just hit rate.** 55% at -200 loses money; 48% at +150 makes it.
 *  - **Backtested and live are never silently merged.** They are different
 *    claims and the source selector says which is on screen.
 *  - **The worst-read players are shown too.** Where the model is reliably
 *    wrong is as useful as where it is right, and hiding it would make this a
 *    highlight reel.
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Avatar from "../components/Avatar";
import Select from "../components/Select";
import PageTitle from "../components/PageTitle";

import { marketLabel } from "../lib/markets";
import {
  fetchCalibration,
  fetchLeaders,
  fetchMarketRecord,
  fetchSeasonRecord,
  fetchTierRecord,
  type CalibrationBucket,
  type LeaderRow,
  type MarketRecord,
  type RecordSource,
  type SeasonRecord,
  type TierRecord,
} from "../api";

/**
 * When the tier rule last changed, for the caveat under the tier table.
 *
 * A date in a sentence is a fact that goes stale silently, so it lives here as
 * one constant rather than being typed into the paragraph. Update it in the
 * same commit that changes how tiers are assigned, which currently means
 * `gap_tier.py`.
 */
const TIER_RULE_CHANGED = "8 November 2026";

const pct = (v: number | null | undefined, dp = 1) =>
  v === null || v === undefined ? "-" : `${(v * 100).toFixed(dp)}%`;

const signed = (v: number | null | undefined, dp = 1) =>
  v === null || v === undefined ? "-" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(dp)}%`;

/**
 * Did this row's hit rate beat the rate it needed?
 *
 * Colouring the hit rate against break-even rather than against 50% is the
 * whole point of putting the two side by side. 52% looks like a win and is a
 * loss at -130; 49% looks like a loss and is a win at +120. The colour says
 * which, and the Needed column next to it says why, so neither number has to
 * be taken on trust.
 *
 * No colour when either side is missing. A grey number is a quieter failure
 * than a green one that was guessed.
 */
const beat = (r: { hit_rate: number | null; breakeven: number | null }) =>
  r.hit_rate === null || r.breakeven === null
    ? ""
    : r.hit_rate > r.breakeven
      ? "pos"
      : "neg";

/**
 * Predicted against actual, as a reliability curve.
 *
 * A perfectly calibrated model traces the diagonal. Bars below it are
 * overconfidence, which is the direction this model errs, and seeing the shape
 * of that gap is worth more than any single summary number.
 */
function CalibrationChart({ buckets }: { buckets: CalibrationBucket[] }) {
  if (buckets.length === 0) {
    return <div className="ps-empty">Not enough graded picks to plot yet.</div>;
  }
  const W = 520;
  const H = 260;
  const pad = 40;
  const x = (v: number) => pad + ((v - 0.5) / 0.5) * (W - pad * 2);
  const y = (v: number) => H - pad - ((v - 0.3) / 0.7) * (H - pad * 2);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ps-chart" role="img"
         aria-label="Predicted win probability against actual hit rate">
      {[0.4, 0.6, 0.8, 1.0].map((g) => (
        <g key={g}>
          <line x1={pad} x2={W - pad} y1={y(g)} y2={y(g)}
                stroke="var(--border)" strokeWidth="1" />
          <text x={pad - 8} y={y(g) + 4} textAnchor="end"
                fill="var(--text-faint)" fontSize="10">
            {Math.round(g * 100)}%
          </text>
        </g>
      ))}
      {/* Perfect calibration. Everything is read against this line. */}
      <line x1={x(0.5)} y1={y(0.5)} x2={x(1)} y2={y(1)}
            stroke="var(--text-faint)" strokeWidth="1.5" strokeDasharray="4 4" />
      <text x={W - pad} y={y(1) - 8} textAnchor="end"
            fill="var(--text-faint)" fontSize="10">
        Perfectly Calibrated
      </text>

      <polyline
        points={buckets.map((b) => `${x(b.predicted)},${y(b.actual)}`).join(" ")}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {buckets.map((b) => (
        <g key={b.bucket}>
          <circle cx={x(b.predicted)} cy={y(b.actual)} r="4.5"
                  fill="var(--cyan)" stroke="var(--bg)" strokeWidth="2">
            <title>
              {`predicted ${pct(b.predicted)}, actual ${pct(b.actual)}, ${b.picks} picks`}
            </title>
          </circle>
        </g>
      ))}
      <text x={W / 2} y={H - 8} textAnchor="middle"
            fill="var(--text-faint)" fontSize="10">
        Model&rsquo;s Predicted Win Probability
      </text>
    </svg>
  );
}

/** A horizontal bar pair: what the model claimed, and what happened. */
function ClaimVsReality({ predicted, actual }: { predicted: number | null; actual: number | null }) {
  const p = predicted ?? 0;
  const a = actual ?? 0;
  return (
    <div className="ps-cvr" title={`model said ${pct(p)}, actually ${pct(a)}`}>
      <span className="ps-cvr-row">
        <span className="ps-cvr-track">
          <span className="ps-cvr-fill claim" style={{ width: `${p * 100}%` }} />
        </span>
        <span className="ps-cvr-num">{pct(p, 0)}</span>
      </span>
      <span className="ps-cvr-row">
        <span className="ps-cvr-track">
          <span className="ps-cvr-fill real" style={{ width: `${a * 100}%` }} />
        </span>
        <span className="ps-cvr-num">{pct(a, 0)}</span>
      </span>
    </div>
  );
}

export default function TrackRecord() {
  const [seasons, setSeasons] = useState<SeasonRecord[]>([]);
  const [markets, setMarkets] = useState<MarketRecord[]>([]);
  const [tiers, setTiers] = useState<TierRecord[]>([]);
  const [best, setBest] = useState<LeaderRow[]>([]);
  const [worst, setWorst] = useState<LeaderRow[]>([]);
  const [buckets, setBuckets] = useState<CalibrationBucket[]>([]);

  const [season, setSeason] = useState<number | null>(null);
  const [source, setSource] = useState<RecordSource>("all");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    Promise.all([
      fetchSeasonRecord(source),
      fetchMarketRecord(season, source),
      fetchTierRecord(season, source),
      fetchLeaders({ season, source, direction: "best", limit: 8 }),
      fetchLeaders({ season, source, direction: "worst", limit: 8 }),
      fetchCalibration(season, source),
    ])
      .then(([s, m, t, b, w, c]) => {
        if (cancelled) return;
        setSeasons(s.seasons);
        setMarkets(m.markets);
        setTiers(t.tiers);
        setBest(b.leaders);
        setWorst(w.leaders);
        setBuckets(c.buckets);
      })
      .catch((e) => !cancelled && setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [season, source]);

  const seasonOptions = useMemo(() => {
    const years = [...new Set(seasons.map((s) => s.season))].sort((a, b) => b - a);
    return [
      { value: "", label: "All seasons" },
      ...years.map((y) => ({ value: String(y), label: `${y} season` })),
    ];
  }, [seasons]);

  // Totals for the headline row. Summed rather than averaged, because averaging
  // per-season rates would weight a 611-pick season the same as a 5,291-pick one.
  // The live/backtest split behind whatever is on screen. The season rows
  // carry a source, so the composition of the merged view is already known
  // without another request.
  const sourceSplit = useMemo(() => {
    const rows = season ? seasons.filter((s) => s.season === season) : seasons;
    const live = rows
      .filter((r) => r.source === "live")
      .reduce((a, r) => a + r.picks, 0);
    const backtest = rows
      .filter((r) => r.source !== "live")
      .reduce((a, r) => a + r.picks, 0);
    return { live, backtest, total: live + backtest };
  }, [seasons, season]);

  const totals = useMemo(() => {
    const rows = season ? seasons.filter((s) => s.season === season) : seasons;
    const picks = rows.reduce((a, r) => a + r.picks, 0);
    const units = rows.reduce((a, r) => a + (r.units ?? 0), 0);
    const hits = rows.reduce((a, r) => a + (r.hit_rate ?? 0) * r.picks, 0);
    const claimed = rows.reduce((a, r) => a + (r.model_predicted ?? 0) * r.picks, 0);
    const needed = rows.reduce((a, r) => a + (r.breakeven ?? 0) * r.picks, 0);
    return {
      picks,
      units,
      hit_rate: picks ? hits / picks : null,
      predicted: picks ? claimed / picks : null,
      breakeven: picks ? needed / picks : null,
      roi: picks ? units / picks : null,
    };
  }, [seasons, season]);

  return (
    <>
      <div className="ps-hero">
        <PageTitle lead="Track" accent="Record" />
        <p>
          Every pick the model has made, graded against what actually happened.
          Seasons before this one are reconstructed with models refit on earlier
          data only, so nothing here has seen the season it is being judged on.
        </p>
      </div>

      <section className="ps-filters" aria-label="Filters">
        <Select
          label="Season"
          value={season === null ? "" : String(season)}
          onChange={(v) => setSeason(v ? Number(v) : null)}
          minWidth={180}
          options={seasonOptions}
        />
        <Select
          label="Source"
          value={source}
          onChange={(v) => setSource(v as RecordSource)}
          minWidth={210}
          options={[
            { value: "all", label: "All picks" },
            { value: "backtest", label: "Backtested only" },
            { value: "live", label: "Published live only" },
          ]}
        />
      </section>

      {/* What "All picks" is actually made of.
        *
        * This page says backtested and live are never silently merged, and then
        * defaults to the view that merges them. The season table labels each
        * row, but the tier table, the calibration plot, the market breakdown
        * and the leaderboards do not, and the mix is nothing like even: the
        * reconstructed rows outnumber the published ones by more than a hundred
        * to one, so "All picks" reads as a live record while being almost
        * entirely a backtest. Saying so is cheaper than removing the view. */}
      {source === "all" && !loading && sourceSplit.total > 0 && (
        <p className="ps-note">
          All picks is {sourceSplit.backtest.toLocaleString()} backtested and{" "}
          {sourceSplit.live.toLocaleString()} published live. The two are
          different claims; use the source selector to see either on its own.
        </p>
      )}

      {err && <div className="ps-empty">Could not load the record: {err}</div>}

      {/* Hit rate leads, and everything else is subordinate to it.
        *
        * This used to be four equal cards and Return sat level with Hit Rate,
        * which made the page answer a question nobody starts with. The thing a
        * person wants to know about a prediction site is whether the
        * predictions were right. That is the hit rate, so it gets the size.
        *
        * It cannot stand completely alone, because a hit rate with no
        * reference is not a verdict: 55% at -200 loses money and 48% at +150
        * makes it. So the number it had to beat sits immediately beside it, in
        * the same unit, measured on the prices we were actually offered rather
        * than an assumed -110. Two percentages, one above the other, and which
        * one is larger is the whole result.
        *
        * Return and units are still here and still unrounded, one line down in
        * smaller type, because they are the honest accounting and burying them
        * would be the same sin as leading with them. The FAQ explains what a
        * unit is, since "+1.9%" means nothing without it. */}
      <section className="ps-leadstat" aria-label="Headline record">
        <div className="ps-leadstat-main">
          <div className="label">Hit Rate</div>
          <div
            className={`value ${
              totals.hit_rate !== null && totals.breakeven !== null
                ? totals.hit_rate > totals.breakeven
                  ? "pos"
                  : "neg"
                : ""
            }`}
          >
            {loading ? "..." : pct(totals.hit_rate)}
          </div>
          <div className="sub">
            {loading
              ? " "
              : `${totals.picks.toLocaleString()} graded picks, ${
                  season ? `${season} season` : "all seasons"
                }`}
          </div>
        </div>
        <div className="ps-leadstat-against">
          <div className="label">Needed To Break Even</div>
          <div className="value">{loading ? "..." : pct(totals.breakeven)}</div>
          <div className="sub">at the prices we were offered</div>
        </div>
        <div className="ps-leadstat-side">
          <div className="ps-leadstat-item">
            <span className="k">Model claimed</span>
            <span className="v">{loading ? "..." : pct(totals.predicted)}</span>
            <span className="n">what it said its own chances were</span>
          </div>
          <div className="ps-leadstat-item">
            <span className="k">Return</span>
            {/* ROI is a result, so green and red are earned here. The Elite
                count on the edges page is a category count, which is why that
                one is deliberately not green. */}
            <span className={`v ${(totals.roi ?? 0) > 0 ? "pos" : "neg"}`}>
              {loading ? "..." : signed(totals.roi)}
            </span>
            <span className="n">
              per unit staked. <Link to="/faq#units">What is a unit?</Link>
            </span>
          </div>
          <div className="ps-leadstat-item">
            <span className="k">Units</span>
            <span className="v">
              {loading ? "..." : `${totals.units > 0 ? "+" : ""}${totals.units.toFixed(1)}`}
            </span>
            <span className="n">flat 1u stakes, no compounding</span>
          </div>
        </div>
      </section>

      <section className="ps-section">
        <h2>By Season</h2>
        <div className="ps-tablewrap">
          <div className="ps-tablescroll">
            <table className="ps-table is-stats">
              <thead>
                <tr>
                  <th>Season</th>
                  <th>Source</th>
                  <th className="num">Picks</th>
                  <th className="num">Players</th>
                  <th>Claimed vs Actual</th>
                  <th className="num">Hit</th>
                  <th className="num">Needed</th>
                  <th className="num is-quiet">ROI</th>
                </tr>
              </thead>
              <tbody>
                {seasons.map((s) => (
                  <tr key={`${s.season}-${s.source}`}>
                    <td data-label="Season"><strong>{s.season}</strong></td>
                    <td data-label="Source">
                      <span className={`tier ${s.source === "live" ? "tier-strong" : "tier-small"}`}>
                        {s.source}
                      </span>
                    </td>
                    <td data-label="Picks" className="num">{s.picks.toLocaleString()}</td>
                    <td data-label="Players" className="num">{s.players}</td>
                    <td data-label="Claimed vs Actual">
                      <ClaimVsReality predicted={s.model_predicted} actual={s.hit_rate} />
                    </td>
                    <td data-label="Hit" className={`num ${beat(s)}`}>{pct(s.hit_rate)}</td>
                    <td data-label="Needed" className="num is-quiet">{pct(s.breakeven)}</td>
                    <td data-label="ROI" className={`num is-quiet ${(s.roi ?? 0) > 0 ? "pos" : "neg"}`}>
                      {signed(s.roi)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!loading && seasons.length === 0 && (
            <div className="ps-empty">No graded picks yet.</div>
          )}
        </div>
      </section>

      <div className="ps-split">
        <section className="ps-section">
          <h2>Is the Model Honest About Itself?</h2>
          <p className="ps-tagline">
            Each point is a group of picks. If the model were calibrated they
            would sit on the dashed line. Below it means it claimed more
            confidence than it earned.
          </p>
          <div className="ps-panel">
            <CalibrationChart buckets={buckets} />
          </div>
        </section>

        <section className="ps-section">
          <h2>Do the Tiers Mean Anything?</h2>
          <p className="ps-tagline">
            If elite does not beat small, the labels are decoration. This is the
            check.
          </p>
          {/* The one thing this table cannot say for itself.
            *
            * Every other number on this page is a fact about a pick: it won or
            * it did not. A tier is a label, and the rule that assigns it has
            * been changed more than once. Each row was labelled by whatever
            * rule was live on the day it was published, so this table pools
            * picks graded under rules we no longer use, and the "Elite" row is
            * not a statement about the Elite badge on today's board.
            *
            * Saying so costs one sentence. Silently rewriting old labels under
            * the current rule would cost more than that: it would claim a
            * record for picks the board never actually recommended, which is
            * the one thing a track record must not do. So the labels stay as
            * published and the caveat goes on the page. */}
          <p className="ps-note">
            Each pick carries the tier it was published with. The tiering rule
            has changed during the life of this record, most recently on{" "}
            {TIER_RULE_CHANGED}, so these rows pool picks labelled by more than
            one rule and do not describe the badge you see on today&rsquo;s
            board. Old labels are never rewritten: doing that would claim a
            record for picks we did not actually make.
          </p>
          <div className="ps-tablewrap">
            <table className="ps-table is-stats">
              <thead>
                <tr>
                  <th>Tier</th>
                  <th className="num">Picks</th>
                  <th className="num">Claimed</th>
                  <th className="num">Hit</th>
                  <th className="num">Needed</th>
                  <th className="num is-quiet">ROI</th>
                </tr>
              </thead>
              <tbody>
                {tiers.map((t) => (
                  <tr key={t.edge_tier}>
                    <td data-label="Tier">
                      <span className={`tier tier-${t.edge_tier}`}>{t.edge_tier === "none" ? "no tier" : t.edge_tier}</span>
                    </td>
                    <td data-label="Picks" className="num">{t.picks.toLocaleString()}</td>
                    <td data-label="Claimed" className="num">{pct(t.model_predicted)}</td>
                    <td data-label="Hit" className={`num ${beat(t)}`}>{pct(t.hit_rate)}</td>
                    <td data-label="Needed" className="num is-quiet">{pct(t.breakeven)}</td>
                    <td data-label="ROI" className={`num is-quiet ${(t.roi ?? 0) > 0 ? "pos" : "neg"}`}>
                      {signed(t.roi)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="ps-section">
        <h2>By Market</h2>
        <div className="ps-tablewrap">
          <div className="ps-tablescroll">
            <table className="ps-table is-stats">
              <thead>
                <tr>
                  <th>Market</th>
                  <th className="num">Picks</th>
                  <th>Claimed vs Actual</th>
                  <th className="num">Claimed</th>
                  <th className="num">Hit</th>
                  <th className="num">Needed</th>
                  <th className="num is-quiet">ROI</th>
                </tr>
              </thead>
              <tbody>
                {markets.map((m) => (
                  <tr key={m.market_code}>
                    <td data-label="Market">
                      {marketLabel(m.market_code)}
                    </td>
                    <td data-label="Picks" className="num">{m.picks.toLocaleString()}</td>
                    <td data-label="Claimed vs Actual">
                      <ClaimVsReality predicted={m.model_predicted} actual={m.hit_rate} />
                    </td>
                    <td data-label="Claimed" className="num">{pct(m.model_predicted)}</td>
                    <td data-label="Hit" className={`num ${beat(m)}`}>{pct(m.hit_rate)}</td>
                    <td data-label="Needed" className="num is-quiet">{pct(m.breakeven)}</td>
                    <td data-label="ROI" className={`num is-quiet ${(m.roi ?? 0) > 0 ? "pos" : "neg"}`}>
                      {signed(m.roi)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <div className="ps-split">
        <LeaderBoard title="Read Best" rows={best} tone="pos"
          blurb="Players whose props the model has graded most accurately. Minimum 20 picks, because a perfect record on three picks is noise." />
        <LeaderBoard title="Read Worst" rows={worst} tone="neg"
          blurb="Where the model is reliably wrong. Just as useful to know, and hiding it would make this a highlight reel." />
      </div>
    </>
  );
}

function LeaderBoard({
  title,
  rows,
  tone,
  blurb,
}: {
  title: string;
  rows: LeaderRow[];
  tone: "pos" | "neg";
  blurb: string;
}) {
  return (
    <section className="ps-section">
      <h2>{title}</h2>
      <p className="ps-tagline">{blurb}</p>
      <div className="ps-leaderlist">
        {rows.map((r, i) => (
          <div className="ps-leader" key={r.player_id}>
            <span className="ps-leader-rank">{i + 1}</span>
            <Avatar name={r.player_name} src={r.headshot} />
            <div className="ps-leader-text">
              {r.app_player_id ? (
                <Link to={`/players/${r.app_player_id}`} className="ps-ident-name">
                  {r.player_name}
                </Link>
              ) : (
                <span className="ps-ident-name">{r.player_name}</span>
              )}
              <div className="matchup">
                {r.position}
                {r.team ? ` · ${r.team}` : ""} · {r.picks} picks
              </div>
            </div>
            <span className={`ps-leader-rate ${tone}`}>{pct(r.hit_rate, 0)}</span>
          </div>
        ))}
        {rows.length === 0 && (
          <div className="ps-empty">Not enough graded picks yet.</div>
        )}
      </div>
    </section>
  );
}
