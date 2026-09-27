import Link from "next/link";

export function Logo({ locale, brand, tagline }: { locale: string; brand: string; tagline?: string }) {
  return (
    <Link href={`/${locale}`} className="inline-flex items-center gap-2.5 rounded-full py-1" aria-label={brand}>
      <span className="relative inline-grid size-9 place-items-center rounded-[10px] bg-amber text-on-amber shadow-[0_0_24px_-4px_rgba(245,158,11,0.7)]" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
          <path d="M4 12v2M8 8v8M12 5v14M16 8v8M20 11v3" />
        </svg>
      </span>
      <span className="flex flex-col leading-tight">
        <span className="text-lg font-bold tracking-tight">{brand}</span>
        {tagline ? <span className="mono hidden text-[10px] font-medium text-ink-3 sm:block">{tagline}</span> : null}
      </span>
    </Link>
  );
}
