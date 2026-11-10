/**
 * A page title in two voices: upright ink, then the italic in ledger blue.
 *
 * The second half is the noun that names the page, set in the italic of the
 * same serif and in the blue a bookkeeper uses for the entry that matters. One
 * colour, no gradient. A heading that decorates every word competes with the
 * masthead instead of sitting under it.
 */

export default function PageTitle({
  lead,
  accent,
}: {
  /** The upright half, in ink. */
  lead: string;
  /** The italic half, in ledger blue. Usually the page's actual noun. */
  accent: string;
}) {
  return (
    <h1 className="ps-title">
      {lead}{" "}
      <span className="ps-title-accent">{accent}</span>
    </h1>
  );
}
