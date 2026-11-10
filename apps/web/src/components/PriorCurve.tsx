/**
 * The name, drawn.
 *
 * "Our prior" is a predicted range: how likely each outcome is, as a curve.
 * "Their line" is one number a sportsbook posts somewhere along it. A pick is
 * nothing more than which side of the line holds more of the curve. This is
 * that, and the line can be moved.
 *
 * It is an illustration and says so. The curve is a fixed, right-skewed shape,
 * the kind a yardage market has, with no player, no stat and no axis on it, so
 * nothing here can be mistaken for a pick. The two percentages are real
 * arithmetic on that shape: the share of the area either side of the line.
 *
 * The control is a native range input stretched over the drawing. That is what
 * makes it draggable, touchable and usable from a keyboard without rebuilding
 * any of those by hand.
 */

import { useEffect, useRef, useState } from "react";

const W = 1000;
const H = 400;
const BASE = 372;
const PEAK = 318;
const N = 160;
const SHAPE = 3.4;
const SPAN = 11.5;

/** The curve, sampled once. Height as a share of its peak, and the running
 *  share of the area to the left of each sample. */
const { CURVE, AREA, CDF, MEDIAN, MODE } = (() => {
  const ys: number[] = [];
  for (let i = 0; i <= N; i++) {
    const x = (i / N) * SPAN;
    ys.push(Math.pow(x, SHAPE - 1) * Math.exp(-x));
  }
  const top = Math.max(...ys);
  const cdf: number[] = [0];
  for (let i = 1; i <= N; i++) cdf.push(cdf[i - 1] + (ys[i] + ys[i - 1]) / 2);
  const all = cdf[N];
  const pts = ys.map((y, i) => {
    const px = (i / N) * W;
    const py = BASE - (y / top) * PEAK;
    return `${px.toFixed(1)},${py.toFixed(1)}`;
  });
  const norm = cdf.map((c) => c / all);
  return {
    CURVE: `M${pts.join(" L")}`,
    AREA: `M0,${BASE} L${pts.join(" L")} L${W},${BASE} Z`,
    CDF: norm,
    MEDIAN: norm.findIndex((c) => c >= 0.5) / N,
    MODE: ys.indexOf(top) / N,
  };
})();

function shareBelow(t: number): number {
  const f = Math.min(Math.max(t, 0), 1) * N;
  const i = Math.floor(f);
  if (i >= N) return 1;
  return CDF[i] + (CDF[i + 1] - CDF[i]) * (f - i);
}

const MIN = 0.1;
const MAX = 0.82;
/** Where the line rests: a little above the middle of the curve, which is
 *  where a book shades a popular over. */
const REST = MEDIAN + 0.05;

export function PriorCurve() {
  const [t, setT] = useState(REST);
  const [held, setHeld] = useState(false);
  const touched = useRef(false);

  // Until someone takes hold of it the line drifts, because lines move. It
  // stops for good on the first touch, and never starts for anyone who has
  // asked their system for less motion.
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let raf = 0;
    const t0 = performance.now() + 2600;
    const tick = (now: number) => {
      if (touched.current) return;
      const s = Math.max(0, now - t0) / 1000;
      setT(REST + 0.085 * Math.sin(s * 0.55) * Math.min(1, s / 2));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const take = (v: number) => {
    touched.current = true;
    setT(Math.min(MAX, Math.max(MIN, v)));
  };

  const under = shareBelow(t);
  const over = 1 - under;
  const side = under >= over ? "under" : "over";
  const x = t * W;
  const underPct = Math.round(under * 100);
  const overPct = 100 - underPct;

  return (
    <figure className={`pc is-${side}${held ? " is-held" : ""}`}>
      <div
        className="pc-plot"
        onPointerMove={(e) => {
          if (e.pointerType !== "mouse") return;
          const r = e.currentTarget.getBoundingClientRect();
          take((e.clientX - r.left) / r.width);
        }}
      >
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
        >
          <defs>
            <clipPath id="pc-under">
              <rect x="0" y="0" width={x} height={H} />
            </clipPath>
            <clipPath id="pc-over">
              <rect x={x} y="0" width={W - x} height={H} />
            </clipPath>
          </defs>
          <path className="pc-area pc-area-under" d={AREA} clipPath="url(#pc-under)" />
          <path className="pc-area pc-area-over" d={AREA} clipPath="url(#pc-over)" />
          <line className="pc-base" x1="0" y1={BASE} x2={W} y2={BASE} />
          <path className="pc-curve" d={CURVE} />
        </svg>

        <span className="pc-tag pc-tag-prior" style={{ left: `${MODE * 100}%` }}>
          Our prior
        </span>
        <span className="pc-line" style={{ left: `${t * 100}%` }}>
          <span className="pc-tag pc-tag-line">Their line</span>
          <span className="pc-grip" aria-hidden="true" />
        </span>

        <input
          className="pc-range"
          type="range"
          min={0}
          max={1}
          step={0.005}
          value={t}
          aria-label="Move their line"
          aria-valuetext={`Under ${underPct} percent, over ${overPct} percent. The pick is the ${side}.`}
          onChange={(e) => take(Number(e.target.value))}
          onPointerDown={() => setHeld(true)}
          onPointerUp={() => setHeld(false)}
          onPointerCancel={() => setHeld(false)}
        />
      </div>

      <figcaption className="pc-read">
        <span className={`pc-side${side === "under" ? " is-pick" : ""}`}>
          <b>{underPct}%</b> under
          {side === "under" && <em>the pick</em>}
        </span>
        <span className={`pc-side${side === "over" ? " is-pick" : ""}`}>
          <b>{overPct}%</b> over
          {side === "over" && <em>the pick</em>}
        </span>
        <span className="pc-note">
          An illustration, not a pick. Move their line and the side changes.
        </span>
      </figcaption>
    </figure>
  );
}
