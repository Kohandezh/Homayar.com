import Link from "next/link";
import { VoiceMark } from "./VoiceMark";

export function Logo({ locale, brand, tagline }: { locale: string; brand: string; tagline?: string }) {
  return (
    <Link href={`/${locale}`} className="inline-flex items-center gap-2.5 rounded-full py-1" aria-label={brand}>
      <span className="relative inline-grid size-9 place-items-center rounded-[10px] bg-accent text-on-accent shadow-[0_0_24px_-4px_rgb(var(--t-glow)/0.7)]" aria-hidden="true">
        <VoiceMark size={20} />
      </span>
      <span className="flex flex-col leading-tight">
        <span className="text-lg font-bold tracking-tight">{brand}</span>
        {tagline ? <span className="label hidden text-[10px] text-ink-3 sm:block">{tagline}</span> : null}
      </span>
    </Link>
  );
}
