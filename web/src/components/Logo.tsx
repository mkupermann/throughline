/** A compact typographic T/L ligature: one stem carries the line forward. */
export function Logo({ size = 28, title = "Throughline" }: {
  size?: number; title?: string | null;
}) {
  return <svg width={size} height={size} viewBox="0 0 32 32" fill="none"
    xmlns="http://www.w3.org/2000/svg" role={title ? "img" : "presentation"}
    aria-label={title ?? undefined} aria-hidden={title ? undefined : true}>
    {title && <title>{title}</title>}
    <path d="M4 6h24v5H18v10h10v5H13V11H4V6Z" fill="currentColor" />
  </svg>;
}
