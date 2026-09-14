/**
 * A page title in the wordmark's own shape: plain text, then the brand gradient.
 *
 * "PriorLine" sets the pattern. "Prior" is white and "Line" carries the
 * cyan-to-violet ramp, so a heading that gradients every word competes with the
 * logo instead of echoing it. Splitting the title the same way makes the page
 * read as part of the same system, and it keeps the emphasis on the noun that
 * actually names the page.
 */

export default function PageTitle({
  lead,
  accent,
}: {
  /** The plain half. Rendered in normal text colour. */
  lead: string;
  /** The half that carries the gradient. Usually the page's actual noun. */
  accent: string;
}) {
  return (
    <h1 className="ps-title">
      {lead}{" "}
      <span className="ps-title-accent">{accent}</span>
    </h1>
  );
}
