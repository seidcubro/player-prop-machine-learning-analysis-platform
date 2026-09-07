/**
 * Every date, time and number the site renders.
 *
 * These were formatted inline in six different files with six different option
 * objects, so the same kickoff appeared as "Thu, Sep 10, 8:35 PM" in one place,
 * "Sep 10" in another and a raw "2026-09-07 04:15" in the pager. Centralising
 * them means one change fixes every screen and two components cannot drift.
 *
 * Times render in the viewer's own zone with the zone abbreviation shown,
 * because a kickoff time with no zone is ambiguous and this is a product where
 * being an hour out matters.
 */

/** A date the API sent as `YYYY-MM-DD`, or a full ISO timestamp. */
function toDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  // A bare `YYYY-MM-DD` parses as UTC midnight, which renders as the previous
  // day for anyone behind UTC. Anchoring it to local midnight keeps the
  // calendar date the API meant.
  const d = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "September 7, 2026" */
export function fullDate(value: string | null | undefined): string {
  const d = toDate(value);
  if (!d) return "-";
  return d.toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/** "September 7, 2026 at 11:27 AM EST" */
export function fullDateTime(value: string | null | undefined): string {
  const d = toDate(value);
  if (!d) return "-";
  const date = d.toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return `${date} at ${time}`;
}

/**
 * "Thu, Sep 10 · 8:35 PM EST"
 *
 * The compact form for table rows, where the full month name would push the
 * column too wide. The weekday is kept because on an NFL slate it carries real
 * information: Thursday, Sunday and Monday games are different propositions.
 */
export function kickoff(value: string | null | undefined): string {
  const d = toDate(value);
  if (!d) return "";
  const date = d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return `${date} · ${time}`;
}

/** "Sep 10" for dense contexts with no room for a time. */
export function shortDate(value: string | null | undefined): string {
  const d = toDate(value);
  if (!d) return "-";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** American odds, always signed. */
export function price(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value > 0 ? `+${value}` : String(value);
}

/** A percentage, always signed. Used where the sign is the point. */
export function signedPct(value: number | null | undefined, dp = 1): string {
  if (value === null || value === undefined) return "-";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(dp)}%`;
}

/** A percentage with no forced sign. */
export function pct(value: number | null | undefined, dp = 1): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(dp)}%`;
}
