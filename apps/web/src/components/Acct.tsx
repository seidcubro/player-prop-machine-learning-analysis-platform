/**
 * A figure written the way a ledger writes it.
 *
 * A loss goes in red ink and in parentheses. The parentheses are the part that
 * matters: they are how an accountant marks a negative so that it survives a
 * photocopy, and here they are what keeps the sign from being carried by
 * colour alone. A screen reader hears "minus" instead of the brackets.
 */

export function Acct({
  value,
  digits = 1,
  scale = 1,
  suffix = "",
  plus = false,
}: {
  value: number | null | undefined;
  digits?: number;
  /** 100 turns a share into percentage points. */
  scale?: number;
  suffix?: string;
  /** Write a leading plus on gains. Off where the column head already says it. */
  plus?: boolean;
}) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="acct is-blank">&middot;&middot;&middot;</span>;
  }
  const v = value * scale;
  const body = Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  // Rounds to zero: neither a gain nor a loss, so neither colour.
  if (Number(Math.abs(v).toFixed(digits)) === 0) {
    return <span className="acct">{body}{suffix}</span>;
  }
  if (v < 0) {
    return (
      <span className="acct is-loss">
        <span className="sr-only">minus </span>
        <span aria-hidden="true">(</span>
        {body}
        {suffix}
        <span aria-hidden="true">)</span>
      </span>
    );
  }
  return (
    <span className="acct is-gain">
      {plus ? "+" : ""}
      {body}
      {suffix}
    </span>
  );
}

/** A small drawn arrow. Not a typed glyph, so it inherits weight and colour. */
export function Arrow({ dir = "right" }: { dir?: "right" | "up" | "down" }) {
  const rot = dir === "up" ? -90 : dir === "down" ? 90 : 0;
  return (
    <svg
      className="pl-arrow"
      viewBox="0 0 16 16"
      width="1em"
      height="1em"
      aria-hidden="true"
      focusable="false"
      style={rot ? { transform: `rotate(${rot}deg)` } : undefined}
    >
      <path
        d="M2 8h11M9 3.5 13.5 8 9 12.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="square"
      />
    </svg>
  );
}
