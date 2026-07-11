/**
 * PropSignal logo — vector recreation of the final brand mark:
 * a thin circular ring, an ECG/"W" waveform piercing through it,
 * and a directional signal beam emitting from the upper right.
 *
 * Built as inline SVG (not the raster brand PNGs) so it stays crisp in the
 * navbar, at favicon sizes, and in a future mobile app. The raster originals
 * remain the marketing assets.
 */

type LogoProps = {
  /** Pixel size of the (square) icon. */
  size?: number;
  /** Show the emitted beam (brand rule: keep it on primary + favicon). */
  beam?: boolean;
  /** Accessible title; pass "" for decorative usage next to visible text. */
  title?: string;
};

export default function Logo({ size = 32, beam = true, title = "PropSignal" }: LogoProps) {
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
          <stop offset="0%" stopColor="#1d7a4c" />
          <stop offset="100%" stopColor="#6ef2a6" />
        </linearGradient>
        <linearGradient id="ps-beam" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#6ef2a6" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#6ef2a6" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="ps-wave" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#2aa866" />
          <stop offset="50%" stopColor="#6ef2a6" />
          <stop offset="100%" stopColor="#2aa866" />
        </linearGradient>
      </defs>

      {beam && (
        <polygon points="60,34 98,2 74,42" fill="url(#ps-beam)" />
      )}

      <circle
        cx="50"
        cy="56"
        r="36"
        fill="none"
        stroke="url(#ps-ring)"
        strokeWidth="5"
        strokeLinecap="round"
        /* ring is broken where the waveform pierces it, like the brand mark */
        strokeDasharray="150 16 40 16"
        strokeDashoffset="118"
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
