/** A continuous path: context carried forward across people, tools and sessions. */
export function Logo({ size = 28, title = "Throughline" }: {
  size?: number; title?: string | null;
}) {
  return <svg width={size} height={size} viewBox="0 0 56 56" fill="none"
    xmlns="http://www.w3.org/2000/svg" role={title ? "img" : "presentation"}
    aria-label={title ?? undefined} aria-hidden={title ? undefined : true}>
    {title && <title>{title}</title>}
    <path d="M8 12H40Q44 12 44 16V20Q44 24 40 24H24Q20 24 20 28V36Q20 40 24 40H48" stroke="currentColor" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}
