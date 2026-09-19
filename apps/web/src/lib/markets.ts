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

/**
 * Touchdown counts, where the median throws away the information.
 *
 * For every other market the median is the honest number to show beside a
 * pick. A touchdown count is small enough that its median is always a whole
 * number, so a quarterback expected to throw 1.9 and one expected to throw 1.1
 * both printed "2.0" against a 1.5 line and looked equally confident. They are
 * not, and the win probability already knew it; only the displayed number hid
 * it. On a half-point line the mean and the median agree on the side, so
 * showing the mean here cannot put an over beside a number below the line.
 */
const TD_COUNT_MARKETS = new Set(["pass_td", "rush_td", "rec_td"]);

export function isTdCount(code: string): boolean {
  return TD_COUNT_MARKETS.has(code);
}

/**
 * What to call the side of a bet.
 *
 * A yes-or-no market has no over and no under. The board rendered "OVER" on
 * every anytime touchdown row, which reads as nonsense next to a 0.5 line that
 * only exists because the code needed a number to compare against.
 */
export function sideLabel(marketCode: string, side: string): string {
  if (!isYesNo(marketCode)) return side;
  return side === "over" ? "yes" : "no";
}

/**
 * Markets that are projected but never priced on the board.
 *
 * These decide which number a projection row should lead with. Everywhere a
 * market appears on both pages the median is shown, so the board and the
 * projections page can never print different numbers for the same player. But
 * the median of a touchdown market is an integer that is almost always zero:
 * 99% of anytime-touchdown rows and every single rushing and receiving
 * touchdown row have a median of 0, which told you Justin Jefferson's anytime
 * touchdown projection was 0.0 rather than that he is about a third to score.
 *
 * These three are never priced, so there is no board figure to contradict, and
 * the expected count is the informative number.
 *
 * Passing touchdowns joined them once the board started showing the mean for
 * touchdown counts too (see isTdCount). Its median is a whole number as well,
 * 1 or 2 for nearly every starter, and the rule that both pages print the same
 * figure still holds: they now both print the mean.
 */
export const PROJECTION_ONLY_MARKETS = new Set(["any_td", "rush_td", "rec_td", "pass_td"]);

/** The figure a projection row should lead with. */
export function displayProjection(row: {
  market_code: string;
  projection: number;
  p50?: number | null;
}): number {
  if (PROJECTION_ONLY_MARKETS.has(row.market_code)) return row.projection;
  return row.p50 ?? row.projection;
}
