"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { otherLocale, type Locale } from "@/lib/i18n";
import { Icon } from "@/components/ui/Icon";

/** Links to the same page in the other language. */
export function LangSwitcher({
  locale,
  label,
  shortLabel,
  className = "",
}: {
  locale: Locale;
  label: string;
  shortLabel: string;
  className?: string;
}) {
  const pathname = usePathname();
  const target = otherLocale(locale);
  const rest = pathname.replace(/^\/(fa|en)(?=\/|$)/, "");
  const href = `/${target}${rest}`;
  return (
    <Link
      href={href}
      hrefLang={target}
      lang={target}
      aria-label={label}
      title={label}
      data-testid="lang-switcher"
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-sm font-semibold text-ink transition-colors hover:border-ink-2 hover:bg-sand ${className}`}
    >
      <Icon name="globe" size={16} />
      <span>{shortLabel}</span>
    </Link>
  );
}
