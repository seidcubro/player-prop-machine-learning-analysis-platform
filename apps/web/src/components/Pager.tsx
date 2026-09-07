/**
 * The one pager.
 *
 * Three pages had three of these, written separately, and they disagreed about
 * everything: "41 signals · page 1 of 2", "1-48 of 758", and "50 projections,
 * page 1 of 12". Different word order, different separators, some with arrows
 * on the buttons and some without. A shared component means the count reads the
 * same way wherever you are, and a fix lands everywhere at once.
 *
 * The count is the headline because it answers the question people actually
 * have. The range and page position are secondary, and the timestamp is
 * smallest, because it only matters when it is stale.
 */

export default function Pager({
  total,
  page,
  pageSize,
  noun,
  onPage,
  updatedAt,
}: {
  total: number;
  /** Zero-based, as the pages themselves store it. */
  page: number;
  pageSize: number;
  /** Singular. Pluralised below, so callers do not each invent their own. */
  noun: string;
  onPage: (next: number) => void;
  /** Optional, already formatted for display. */
  updatedAt?: string | null;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);

  return (
    <div className="ps-pager">
      <span aria-live="polite">
        <strong>{total.toLocaleString()}</strong>{" "}
        {total === 1 ? noun : `${noun}s`}
        {pages > 1 && (
          <span className="ps-pager-stamp">
            showing {from.toLocaleString()} to {to.toLocaleString()} · page{" "}
            {page + 1} of {pages}
          </span>
        )}
        {updatedAt && <span className="ps-pager-stamp">updated {updatedAt}</span>}
      </span>

      <span className="ps-pager-controls">
        <button onClick={() => onPage(Math.max(0, page - 1))} disabled={page === 0}>
          ← Prev
        </button>
        <button
          onClick={() => onPage(Math.min(pages - 1, page + 1))}
          disabled={page >= pages - 1}
        >
          Next →
        </button>
      </span>
    </div>
  );
}
