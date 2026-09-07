/**
 * Player headshot, at the resolution it is actually displayed at.
 *
 * Two problems, both fixed here.
 *
 * **The images were soft.** nflverse gives Cloudinary URLs shaped like
 * `.../image/private/f_auto,q_auto/league/<id>` with no size in the transform,
 * so the CDN serves whatever default it likes and the browser then scales that
 * up into a 64px circle on a 2x display. Cloudinary reads its transforms out of
 * the path, so asking for an exact width, a square crop and face gravity gets a
 * sharp, correctly-framed image instead of a blurry one. A `srcSet` covers 1x
 * and 2x so retina screens get real pixels rather than an upscale.
 *
 * **The monogram bled through.** The initials used to render underneath every
 * photo, always, and headshots are transparent PNGs, so a coloured letterform
 * showed around each player's head. The monogram now renders only when there is
 * no usable image, and it is neutral grey: a placeholder is not information.
 */

import { useEffect, useMemo, useState } from "react";

const SIZE_PX: Record<string, number> = { "": 42, lg: 64, xl: 128 };

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * Ask the CDN for a specific size, cropped to the face.
 *
 * The originals are enormous. Tee Higgins' headshot is a 1.4 MB PNG, and the
 * page was pulling dozens of those at once and then scaling them down into
 * 42px circles, which is why avatars sat blank for seconds and looked soft when
 * they finally arrived. The same image with a size transform is 3.6 KB.
 *
 * `c_fill` with `g_face` keeps the head centred at any aspect ratio, which
 * matters in a circle.
 *
 * Both URL shapes have to be handled: 21k players are served from
 * `/image/private/` and 4k from `/image/upload/`. Matching only the first is
 * what left a third of the avatars pulling full-size originals. Anything that
 * is not a recognisable Cloudinary path is returned untouched rather than
 * mangled.
 */
const CLOUDINARY = /\/image\/(private|upload)\//;

function sized(url: string, px: number): string {
  const m = CLOUDINARY.exec(url);
  if (!m) return url;
  const head = url.slice(0, m.index + m[0].length);
  const rest = url.slice(m.index + m[0].length);
  const slash = rest.indexOf("/");
  if (slash === -1) return url;
  const existing = rest.slice(0, slash);
  // The segment straight after the marker is the transform, and it always
  // contains an underscore (`f_auto`). If it does not, this URL has no
  // transform segment and the size has to be inserted rather than replacing
  // whatever is there.
  const tail = existing.includes("_") ? rest.slice(slash + 1) : rest;
  return `${head}f_auto,q_auto:good,w_${px},h_${px},c_fill,g_face/${tail}`;
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

  // A recycled component instance (paging a list, for example) has to retry the
  // new URL rather than stay stuck on the previous player's failure.
  useEffect(() => setFailed(false), [src]);

  const px = SIZE_PX[size] ?? 42;
  const srcs = useMemo(() => {
    if (!src) return null;
    return { one: sized(src, px), two: sized(src, px * 2) };
  }, [src, px]);

  const showImage = Boolean(srcs) && !failed;

  return (
    <div className={`ps-avatar ${size}`.trim()} aria-hidden="true">
      {!showImage && initials(name)}
      {showImage && srcs && (
        <img
          src={srcs.one}
          srcSet={`${srcs.one} 1x, ${srcs.two} 2x`}
          width={px}
          height={px}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}
