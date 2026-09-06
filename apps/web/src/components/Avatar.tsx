/**
 * Player headshot with an initials fallback.
 *
 * Headshot URLs come from the NFL CDN via nflverse and are missing for a small
 * tail of players (and occasionally 404 for the rest). The initials are always
 * rendered underneath, and a failed image load simply hides the <img>, so a
 * broken photo degrades to a clean monogram instead of an empty box.
 */

import { useEffect, useState } from "react";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function Avatar({
  name,
  src,
  size = "",
}: {
  name: string;
  src?: string | null;
  size?: "" | "lg" | "xl";
}) {
  const [failed, setFailed] = useState(false);

  // A recycled component instance (e.g. paging a list) must retry the new URL
  // rather than stay stuck on a previous player's failure.
  useEffect(() => setFailed(false), [src]);

  return (
    <div className={`ps-avatar ${size}`.trim()} aria-hidden="true">
      {initials(name)}
      {src && !failed && (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
      )}
    </div>
  );
}
