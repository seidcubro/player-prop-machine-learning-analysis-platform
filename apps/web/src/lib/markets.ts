/**
 * Market codes, their names, and the order they belong in.
 *
 * Six files each carried their own copy of this and they had already drifted:
 * the player prop cards and the per-player track record both had no entry for
 * anytime touchdown, so once that market went live those two views would render
 * a raw `any_td` where every other view said "Anytime TD".
 *
 * It is the same failure the backend kept hitting, and for the same reason. A
 * missing key never throws. The label just quietly turns into a code, or the
 * row quietly disappears, and nothing points at the file that is out of date.
 * `services/training/odds_markets.py` is the equivalent on the other side.
 */

/** Board order: receiving, rushing, passing, then touchdowns. */
export const MARKET_ORDER = [
  "rec_yds",
  "recs",
  "rush_yds",
  "rush_att",
  "pass_yds",
  "pass_att",
  "pass_completions",
  "pass_td",
  "any_td",
  "rush_td",
  "rec_td",
] as const;

export type MarketCode = (typeof MARKET_ORDER)[number];

/** Full names, for anywhere with room for them. */
export const MARKET_LABELS: Record<string, string> = {
  rec_yds: "Receiving Yards",
  recs: "Receptions",
  rec_td: "Receiving TDs",
  rush_yds: "Rushing Yards",
  rush_att: "Rush Attempts",
  rush_td: "Rushing TDs",
  pass_yds: "Passing Yards",
  pass_att: "Pass Attempts",
  pass_completions: "Completions",
  pass_td: "Passing TDs",
  any_td: "Anytime TD",
};

/** Abbreviations, for table columns and chips that cannot take the full name. */
export const MARKET_LABELS_SHORT: Record<string, string> = {
  rec_yds: "Rec Yds",
  recs: "Receptions",
  rec_td: "Rec TD",
  rush_yds: "Rush Yds",
  rush_att: "Rush Att",
  rush_td: "Rush TD",
  pass_yds: "Pass Yds",
  pass_att: "Pass Att",
  pass_completions: "Completions",
  pass_td: "Pass TD",
  any_td: "Anytime TD",
};

/** The dropdown options, already in board order. */
export const MARKET_OPTIONS = MARKET_ORDER.map((code) => ({
  code,
  label: MARKET_LABELS[code],
}));

/**
 * Falls back to the raw code rather than to an empty string. A market added to
 * the database before it is added here should look wrong, not look absent.
 */
export function marketLabel(code: string): string {
  return MARKET_LABELS[code] ?? code;
}

export function marketLabelShort(code: string): string {
  return MARKET_LABELS_SHORT[code] ?? MARKET_LABELS[code] ?? code;
}

/**
 * Markets that settle yes or no rather than at a number.
 *
 * Anytime touchdown is priced as a single Yes at an implicit half-point line,
 * so the usual "model's median against the book's line" framing does not apply
 * to it. The median of a scorer who is 30% to score is zero, and zero is below
 * 0.5, so every row rendered a red "-0.5" edge claiming the model contradicted
 * a bet the model had just recommended. The projected rate is the number worth
 * showing, and the edge belongs entirely to the EV column.
 */
export const YES_NO_MARKETS = new Set(["any_td"]);

export function isYesNo(code: string): boolean {
  return YES_NO_MARKETS.has(code);
}
