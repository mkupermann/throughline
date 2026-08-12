/**
 * The Throughline mark.
 *
 * The name means the thread that runs through a body of work. The product's
 * claim is that memory scattered across nine separate CLIs is one continuous
 * record, not nine islands — so the mark is one unbroken stroke passing
 * through three nodes that sit at different heights. The stroke never breaks
 * and never stops at a node: it enters, passes through, and carries on past
 * the frame's edge on both sides, because the line is longer than what you can
 * see of it.
 *
 * Why nodes on a line rather than, say, nine dots: nine is a fact about today's
 * adapter list, not about the idea. Three reads as "several" at any size, and
 * survives a 16px favicon where nine would be mud.
 *
 * The rising left-to-right direction is deliberate — it matches the reading
 * direction of every timeline in the product, so the mark and the Timeline
 * surface share a gesture.
 *
 * Sizes it must hold up at:
 *   16px  favicon — stroke and nodes stay separable because the stroke is 2.5
 *         units in a 32-unit box (≈1.25px at 16), and nodes are 3 units across.
 *   28px  sidebar brand mark.
 *   200px marketing / README — the geometry is vector, nothing is bitmapped.
 */
export function Logo({
  size = 28,
  title = "Throughline",
}: {
  size?: number;
  /** Set to null inside a labelled element so the name is not announced twice. */
  title?: string | null;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role={title ? "img" : "presentation"}
      aria-label={title ?? undefined}
      aria-hidden={title ? undefined : true}
    >
      {title && <title>{title}</title>}

      {/* The through-line. Extends past x=0 and x=32 so it reads as a segment
        * of something longer rather than a shape that begins and ends here. */}
      <path
        d="M-2 24 C 6 24, 8 8, 16 8 S 26 20, 34 20"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />

      {/* Three nodes ON the line — the tools it threads through. Filled with
        * the surface colour and ringed in the line colour, so at any size the
        * line visibly passes THROUGH them instead of stopping at them. */}
      <circle cx="4.4" cy="20.4" r="3" fill="var(--brand-node-fill, #08090c)" stroke="currentColor" strokeWidth="2" />
      <circle cx="16" cy="8" r="3.4" fill="var(--brand-node-fill, #08090c)" stroke="currentColor" strokeWidth="2" />
      <circle cx="27.6" cy="18.6" r="3" fill="var(--brand-node-fill, #08090c)" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}
