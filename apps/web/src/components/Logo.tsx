/**
 * PropSignal logo, vector recreation of the final brand mark:
 * a broken circular ring with an ECG waveform running through it. The ring
 * opens where the waveform crosses it, so the signal reads as passing through.
 *
 * Built as inline SVG (not the raster brand PNGs) so it stays crisp in the
 * navbar, at favicon sizes, and in a future mobile app. The raster originals
 * remain the marketing assets.
 */

type LogoProps = {
  /** Pixel size of the (square) icon. */
  size?: number;
  /** Accessible title; pass "" for decorative usage next to visible text. */
  title?: string;
};

export default function Logo({ size = 32, title = "PropSignal" }: LogoProps) {
  const decorative = title === "";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      role={decorative ? undefined : "img"}
      aria-hidden={decorative ? true : undefined}
      focusable="false"
    >
      {!decorative && <title>{title}</title>}
      <defs>
        <linearGradient id="ps-ring" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="55%" stopColor="#4d7cfe" />
          <stop offset="100%" stopColor="#a855f7" />
        </linearGradient>
        <linearGradient id="ps-wave" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#4d7cfe" />
          <stop offset="50%" stopColor="#a855f7" />
          <stop offset="100%" stopColor="#4d7cfe" />
        </linearGradient>
      </defs>

      {/*
        The ring, as two arcs rather than a dashed circle.

        It opens at exactly 0 and 180 degrees, where the horizontal waveform
        crosses it, so the signal reads as passing through and the two gaps are
        symmetric. The old version used strokeDasharray, which left a 24 degree
        gap on one side and a 2 degree nick on the other and just looked broken.

        The beam that used to sit at the upper right is gone: it was a
        part-transparent triangle that the gradient rendered in dark violet
        against a dark ground, so it read as a chip out of the mark at every
        size.
      */}
      <path
        d="M 84.93 64.71 A 36 36 0 0 1 15.07 64.71"
        fill="none"
        stroke="url(#ps-ring)"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <path
        d="M 15.07 47.29 A 36 36 0 0 1 84.93 47.29"
        fill="none"
        stroke="url(#ps-ring)"
        strokeWidth="5"
        strokeLinecap="round"
      />

      {/* ECG "W" waveform, flatline in -> pulse -> flatline out */}
      <path
        d="M 4 56 L 30 56 L 38 34 L 46 74 L 54 24 L 62 70 L 68 48 L 72 56 L 96 56"
        fill="none"
        stroke="url(#ps-wave)"
        strokeWidth="5.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
