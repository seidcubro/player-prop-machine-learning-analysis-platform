import { useEffect, useState } from "react";
import type { BoardSchedule } from "../api";

/**
 * When the board was built and when it is next checked.
 *
 * An empty board and a stale board look identical, and the difference is the
 * whole question a visitor has: is there nothing today, or is this thing
 * broken? A countdown answers it.
 *
 * The times come from the API, which mirrors the systemd timers. Nothing here
 * knows the schedule; it only counts down to what it was handed.
 *
 * The first version was a sentence: a full date, a second clause, a
 * parenthetical. It read like a log line. This is a status strip instead, and
 * the explanation of what the next run does lives in the tooltip for whoever
 * wants it.
 */

const ET = "America/New_York";

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    // Once a second, because a countdown that visibly ticks is the point. It
    // pauses while the tab is hidden, so a background tab costs nothing.
    const id = window.setInterval(() => {
      if (!document.hidden) setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

/** "14:32", or "2:14:32" past the hour, or "3d 4h" past a day. */
function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (d > 0) return `${d}d ${h}h`;
  const mm = String(m).padStart(h > 0 ? 2 : 1, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Today's time alone, or the date as well when it was not today. */
function when(iso: string): string {
  const t = new Date(iso);
  const day = (d: Date) => d.toLocaleDateString("en-US", { timeZone: ET });
  const time = t.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: ET,
  });
  if (day(t) === day(new Date())) return time;
  const date = t.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: ET,
  });
  return `${date}, ${time}`;
}

export default function NextUpdate({
  next,
  builtAt,
}: {
  next?: BoardSchedule;
  /** ISO timestamp of the last board build, or null if it has never built. */
  builtAt?: string | null;
}) {
  const target = next ? new Date(next.at).getTime() : NaN;
  const now = useNow(Number.isFinite(target));

  // An API too old to send a schedule still renders the part it does send.
  if (!next && !builtAt) return null;

  const remaining = Number.isFinite(target) ? target - now : null;
  const nextClock =
    next &&
    new Date(next.at).toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: ET,
    });

  return (
    <div className="ps-nextup" role="status">
      <span className="ps-nextup-live" aria-hidden="true" />
      {builtAt && (
        <span className="ps-nextup-part">
          <span className="ps-nextup-key">Updated</span>
          <span className="ps-nextup-val">{when(builtAt)}</span>
        </span>
      )}
      {next && remaining !== null && (
        <span
          className="ps-nextup-part"
          title={`${nextClock} ET: ${next.does}`}
        >
          <span className="ps-nextup-key">Next refresh</span>
          <span className="ps-nextup-val ps-nextup-clock">
            {remaining > 0 ? clock(remaining) : "now"}
          </span>
        </span>
      )}
    </div>
  );
}
