/**
 * The PriorLine mark: a Clarendon capital P standing on an accounting double
 * rule.
 *
 * The double rule is what a bookkeeper strikes under a final total, and it is
 * where the phrase "the bottom line" comes from. The P is drawn from Besley, the
 * same face the headlines are set in, as an outline rather than as text so the
 * mark renders before the font has loaded and at favicon sizes.
 *
 * It takes its colour from the text around it. On the cloth masthead that is
 * the gold the name is stamped in; on paper it is the writing ink.
 */

type LogoProps = {
  /** Pixel size of the (square) icon. */
  size?: number;
  /** Accessible title; pass "" for decorative usage next to visible text. */
  title?: string;
};

/** Besley Black capital P, in font units (2000 to the em, y up). */
export const P_OUTLINE =
  "M214 1230Q249 1230 259.5 1223.5Q270 1217 270 1182V334Q270 294 262 282Q254 270 212 270H96Q65 270 57.5 261Q50 252 50 220V46Q50 15 59 7.5Q68 0 98 0H954Q989 0 994.5 11Q1000 22 1000 56V220Q1000 252 994 261Q988 270 956 270H792Q758 270 749 277.5Q740 285 740 318V508Q740 540 746 550Q752 560 784 560H880Q1016 560 1138.5 587.5Q1261 615 1356 672.5Q1451 730 1505.5 821.5Q1560 913 1560 1040Q1560 1168 1505.5 1256Q1451 1344 1356 1397.5Q1261 1451 1138.5 1475.5Q1016 1500 880 1500H106Q72 1500 61 1492Q50 1484 50 1448V1278Q50 1244 60 1237Q70 1230 104 1230ZM782 830Q754 830 747 838Q740 846 740 874V1186Q740 1212 745 1221Q750 1230 776 1230H840Q939 1230 999.5 1187Q1060 1144 1060 1040Q1060 937 999.5 883.5Q939 830 840 830Z";

export default function Logo({ size = 32, title = "PriorLine" }: LogoProps) {
  const decorative = title === "";
  return (
    <svg
      className="pl-mark"
      width={size}
      height={size}
      viewBox="0 0 100 100"
      role={decorative ? undefined : "img"}
      aria-hidden={decorative ? true : undefined}
      focusable="false"
    >
      {!decorative && <title>{title}</title>}
      <path
        d={P_OUTLINE}
        fill="currentColor"
        transform="translate(19.98 68) scale(0.0373 -0.0373)"
      />
      {/* The double rule: a heavy stroke and a hairline, as struck under a total. */}
      <rect className="pl-mark-rule" x="14" y="75.5" width="72" height="5.5" fill="currentColor" />
      <rect className="pl-mark-rule" x="14" y="85" width="72" height="2.2" fill="currentColor" />
    </svg>
  );
}
