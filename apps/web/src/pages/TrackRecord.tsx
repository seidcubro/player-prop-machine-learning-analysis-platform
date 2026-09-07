/**
 * Track record: how the picks have actually done, season by season.
 *
 * This page exists because the old per-player record was two games from one
 * afternoon in December 2023 and still printed a hit rate next to them. The
 * backfill now reconstructs every season we hold closing lines for, using models
 * refit on strictly earlier data, which is 6,529 graded picks instead of 283.
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

const MARKET_LABELS: Record<string, string> = {
  rec_yds: "Receiving Yards",
  recs: "Receptions",
  rec_td: "Receiving TDs",
  rush_yds: "Rushing Yards",
  rush_att: "Rush Attempts",
  rush_td: "Rushing TDs",
  pass_yds: "Passing Yards",
  pass_att: "Pass Attempts",
  pass_completions: "Completions",
  pass_td: "Passing TDs",
  any_td: "Anytime TD",
};

const pct = (v: number | null | undefined, dp = 1) =>
  v === null || v === undefined ? "-" : `${(v * 100).toFixed(dp)}%`;

const signed = (v: number | null | undefined, dp = 1) =>
  v === null || v === undefined ? "-" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(dp)}%`;

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
  const totals = useMemo(() => {
    const rows = season ? seasons.filter((s) => s.season === season) : seasons;
    const picks = rows.reduce((a, r) => a + r.picks, 0);
    const units = rows.reduce((a, r) => a + (r.units ?? 0), 0);
    const hits = rows.reduce((a, r) => a + (r.hit_rate ?? 0) * r.picks, 0);
    const claimed = rows.reduce((a, r) => a + (r.model_predicted ?? 0) * r.picks, 0);
    return {
      picks,
      units,
      hit_rate: picks ? hits / picks : null,
      predicted: picks ? claimed / picks : null,
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

      {err && <div className="ps-empty">Could not load the record: {err}</div>}

      <section className="ps-statgrid" aria-label="Headline record">
        <div className="ps-stat">
          <div className="label">Graded Picks</div>
          <div className="value">{loading ? "..." : totals.picks.toLocaleString()}</div>
          <div className="sub">{season ? `${season} season` : "all seasons"}</div>
        </div>
        <div className="ps-stat">
          <div className="label">Hit Rate</div>
          <div className="value">{loading ? "..." : pct(totals.hit_rate)}</div>
          <div className="sub">model claimed {pct(totals.predicted)}</div>
        </div>
        <div className="ps-stat">
          <div className="label">Return</div>
          {/* ROI is a result, so green and red are earned here. The Elite
              count on the edges page is a category count, which is why that
              one is deliberately not green. */}
          <div className={`value ${(totals.roi ?? 0) > 0 ? "pos" : "neg"}`}>
            {loading ? "..." : signed(totals.roi)}
          </div>
          <div className="sub">per unit staked, at the offered price</div>
        </div>
        <div className="ps-stat">
          <div className="label">Units</div>
          <div className="value">
            {loading ? "..." : `${totals.units > 0 ? "+" : ""}${totals.units.toFixed(1)}`}
          </div>
          <div className="sub">flat 1u stakes</div>
        </div>
      </section>

      <section className="ps-section">
        <h2>By Season</h2>
        <div className="ps-tablewrap">
          <div className="ps-tablescroll">
            <table className="ps-table">
              <thead>
                <tr>
                  <th>Season</th>
                  <th>Source</th>
                  <th className="num">Picks</th>
                  <th className="num">Players</th>
                  <th>Claimed vs Actual</th>
                  <th className="num">Hit</th>
                  <th className="num">ROI</th>
                </tr>
              </thead>
              <tbody>
                {seasons.map((s, i) => (
                  <tr key={`${s.season}-${s.source}`} className="ps-row-in"
                      style={{ animationDelay: `${Math.min(i, 10) * 26}ms` }}>
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
                    <td data-label="Hit" className="num">{pct(s.hit_rate)}</td>
                    <td data-label="ROI" className={`num ${(s.roi ?? 0) > 0 ? "pos" : "neg"}`}>
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
          <div className="ps-tablewrap">
            <table className="ps-table">
              <thead>
                <tr>
                  <th>Tier</th>
                  <th className="num">Picks</th>
                  <th className="num">Claimed</th>
                  <th className="num">Hit</th>
                  <th className="num">ROI</th>
                </tr>
              </thead>
              <tbody>
                {tiers.map((t) => (
                  <tr key={t.edge_tier}>
                    <td data-label="Tier">
                      <span className={`tier tier-${t.edge_tier}`}>{t.edge_tier}</span>
                    </td>
                    <td data-label="Picks" className="num">{t.picks.toLocaleString()}</td>
                    <td data-label="Claimed" className="num">{pct(t.model_predicted)}</td>
                    <td data-label="Hit" className="num">{pct(t.hit_rate)}</td>
                    <td data-label="ROI" className={`num ${(t.roi ?? 0) > 0 ? "pos" : "neg"}`}>
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
            <table className="ps-table">
              <thead>
                <tr>
                  <th>Market</th>
                  <th className="num">Picks</th>
                  <th>Claimed vs Actual</th>
                  <th className="num">Claimed</th>
                  <th className="num">Hit</th>
                  <th className="num">ROI</th>
                </tr>
              </thead>
              <tbody>
                {markets.map((m, i) => (
                  <tr key={m.market_code} className="ps-row-in"
                      style={{ animationDelay: `${Math.min(i, 10) * 26}ms` }}>
                    <td data-label="Market">
                      {MARKET_LABELS[m.market_code] ?? m.market_code}
                    </td>
                    <td data-label="Picks" className="num">{m.picks.toLocaleString()}</td>
                    <td data-label="Claimed vs Actual">
                      <ClaimVsReality predicted={m.model_predicted} actual={m.hit_rate} />
                    </td>
                    <td data-label="Claimed" className="num">{pct(m.model_predicted)}</td>
                    <td data-label="Hit" className="num">{pct(m.hit_rate)}</td>
                    <td data-label="ROI" className={`num ${(m.roi ?? 0) > 0 ? "pos" : "neg"}`}>
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
          <div className="ps-leader ps-row-in" key={r.player_id}
               style={{ animationDelay: `${Math.min(i, 10) * 30}ms` }}>
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
