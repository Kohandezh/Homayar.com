import Link from "next/link";

/** Word mark with a small waveform mark, drawn inline so it works in both languages and needs no image request. */
export function Logo({ locale, brand, tagline }: { locale: string; brand: string; tagline?: string }) {
  return (
    <Link href={`/${locale}`} className="inline-flex items-center gap-2.5 rounded-full py-1" aria-label={brand}>
      <span className="inline-grid size-9 place-items-center rounded-[10px] bg-ink-teal text-cream" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          <path d="M4 12v2M8 8v8M12 5v14M16 8v8M20 11v3" />
        </svg>
      </span>
      <span className="flex flex-col leading-tight">
        <span className="text-lg font-bold tracking-tight">{brand}</span>
        {tagline ? <span className="hidden text-[11px] font-medium text-ink-2 sm:block">{tagline}</span> : null}
      </span>
    </Link>
  );
}
