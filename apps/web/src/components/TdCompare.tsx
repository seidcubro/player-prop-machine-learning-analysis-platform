import type { Projection } from "../api";
import { price as fmtOdds } from "../lib/format";

/**
 * Anytime touchdown: the model and the book, side by side, and nothing else.
 *
 * No pick, no tier, no difference score, no green or red. Every one of those
 * would read as a recommendation, and this market has earned none: the book's
 * own prices rank scorers better than this model does, and betting where the
 * two disagreed lost 25% over 57 bets. What a reader can use is both numbers
 * in one place, so that is all this shows.
 */
export default function TdCompare({ r }: { r: Projection }) {
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  const model = r.td_model_prob ?? null;
  const book = r.td_book_prob ?? null;
  return (
    <div className="ps-tdc">
      <div className="ps-tdc-row">
        <span className="ps-tdc-key">Model</span>
        <span className="ps-tdc-track">
          <span className="ps-tdc-bar is-model" style={{ width: model != null ? pct(model) : 0 }} />
        </span>
        <span className="ps-tdc-val">{model != null ? pct(model) : "-"}</span>
      </div>
      <div className="ps-tdc-row">
        <span className="ps-tdc-key">Book</span>
        <span className="ps-tdc-track">
          <span className="ps-tdc-bar is-book" style={{ width: book != null ? pct(book) : 0 }} />
        </span>
        <span className="ps-tdc-val">
          {book != null ? pct(book) : "no line"}
        </span>
      </div>
      {r.td_book_price != null && (
        <div className="ps-tdc-price">
          {fmtOdds(r.td_book_price)} at {r.td_book ?? "best book"}
        </div>
      )}
    </div>
  );
}
