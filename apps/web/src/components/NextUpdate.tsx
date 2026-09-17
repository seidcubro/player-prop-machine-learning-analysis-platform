import { useEffect, useState } from "react";
import type { BoardSchedule } from "../api";

/**
 * When the board is next rebuilt.
 *
 * An empty board and a stale board look identical, and the difference is the
 * whole question a visitor has: is there nothing today, or is this thing
 * broken? A site that says "next check in 22 minutes" has answered it.
 *
 * The times come from the API, which mirrors the systemd timers. Nothing here
 * knows the schedule; it only counts down to what it was handed.
 *
 * The word is "checks", not "updates". The hourly job rebuilds the board when
 * a game is close enough for its prices to have moved and does nothing when
 * none is, so promising an update every hour would be a lie on a Wednesday.
 */

function useCountdown(target: string | undefined): number | null {
  const [remaining, setRemaining] = useState<number | null>(null);

  useEffect(() => {
    if (!target) {
      setRemaining(null);
      return;
    }
    const at = new Date(target).getTime();
    if (Number.isNaN(at)) {
      setRemaining(null);
      return;
    }

    const tick = () => setRemaining(Math.max(0, at - Date.now()));
    tick();

    // Every fifteen seconds, not every second. The display is in minutes, so a
    // per-second timer would re-render sixty times to change nothing, and a
    // countdown is not worth a wakeup a second on a phone.
    const id = window.setInterval(tick, 15_000);
    return () => window.clearInterval(id);
  }, [target]);

  return remaining;
}

function phrase(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "any moment";
  if (minutes === 1) return "in 1 minute";
  if (minutes < 60) return `in ${minutes} minutes`;

  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours < 24) {
    if (rest === 0) return `in ${hours} ${hours === 1 ? "hour" : "hours"}`;
    return `in ${hours}h ${rest}m`;
  }
  const days = Math.round(hours / 24);
  return `in ${days} ${days === 1 ? "day" : "days"}`;
}

export default function NextUpdate({
  next,
  updatedAt,
}: {
  next?: BoardSchedule;
  /** Already formatted for display, or null when the board has never built. */
  updatedAt?: string | null;
}) {
  const remaining = useCountdown(next?.at);

  // An API too old to send a schedule still renders the part it does send,
  // rather than the whole strip vanishing.
  if (!next && !updatedAt) return null;

  const clock =
    next &&
    new Date(next.at).toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: "America/New_York",
    });

  return (
    <div className="ps-nextup">
      {updatedAt && (
        <span className="ps-nextup-part">
          <span className="ps-nextup-key">Board built</span> {updatedAt}
        </span>
      )}
      {next && remaining !== null && (
        <span className="ps-nextup-part">
          <span className="ps-nextup-key">Next check</span>{" "}
          <strong>{phrase(remaining)}</strong>
          <span className="ps-nextup-at">
            {" "}
            ({clock} ET, {next.does})
          </span>
        </span>
      )}
    </div>
  );
}
