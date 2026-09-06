/**
 * Inline sparkline of a player's recent games against the prop line.
 *
 * A row of numbers tells you the average; the shape tells you whether he is a
 * steady 60-yard back or alternates 15 and 110. That distinction changes the bet
 * and is invisible in a mean, so it is drawn rather than summarised.
 *
 * The line is drawn as a threshold: points above it are wins for the over,
 * points below are wins for the under, and they are coloured accordingly.
 */

type Props = {
  values: number[];
  /** Prop line, drawn as a dashed threshold when supplied. */
  line?: number | null;
  width?: number;
  height?: number;
  /** Which side the model recommends, tints the fill toward that outcome. */
  side?: "over" | "under";
};

export default function Sparkline({
  values,
  line = null,
  width = 96,
  height = 28,
  side = "over",
}: Props) {
  if (values.length < 2) return null;

  const pad = 3;
  const lo = Math.min(...values, line ?? Infinity);
  const hi = Math.max(...values, line ?? -Infinity);
  // A flat series would divide by zero; give it a nominal range so it draws
  // as a centred straight line instead of collapsing.
  const span = hi - lo || 1;

  const x = (i: number) => pad + (i * (width - pad * 2)) / (values.length - 1);
  const y = (v: number) => height - pad - ((v - lo) / span) * (height - pad * 2);

  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const area = `${path} L${x(values.length - 1)},${height} L${x(0)},${height} Z`;

  const stroke = side === "over" ? "var(--green)" : "var(--red)";
  const gid = `spark-${side}-${values.length}-${Math.round(lo)}-${Math.round(hi)}`;

  return (
    <svg
      className="ps-spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Last ${values.length} games: ${values.join(", ")}`}
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>

      {line !== null && line !== undefined && (
        <line
          x1={0}
          x2={width}
          y1={y(line)}
          y2={y(line)}
          stroke="var(--text-faint)"
          strokeWidth="1"
          strokeDasharray="3 3"
        />
      )}

      <path d={area} fill={`url(#${gid})`} />
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {values.map((v, i) => {
        const beat = line === null || line === undefined ? null : v > line;
        return (
          <circle
            key={i}
            cx={x(i)}
            cy={y(v)}
            r={i === values.length - 1 ? 2.6 : 1.7}
            fill={
              beat === null
                ? stroke
                : beat
                  ? "var(--green)"
                  : "var(--red)"
            }
          />
        );
      })}
    </svg>
  );
}
