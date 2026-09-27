import type { Dictionary } from "@/i18n/en";
import type { Locale } from "@/lib/i18n";
import { appUrl } from "@/lib/site";
import { ActiveLink } from "./ActiveLink";
import { LangSwitcher } from "./LangSwitcher";
import { Logo } from "./Logo";
import { MobileMenu } from "./MobileMenu";
import { primaryNav } from "./nav";

export function Header({ locale, dict }: { locale: Locale; dict: Dictionary }) {
  const items = primaryNav(dict);
  const secondary = [
    { label: dict.nav.contact, path: "/contact" },
    { label: dict.nav.transparency, path: "/transparency" },
    { label: dict.nav.privacy, path: "/legal/privacy" },
    { label: dict.nav.terms, path: "/legal/terms" },
  ];
  return (
    <header className="site-header">
      <div className="container-x flex h-full items-center justify-between gap-3">
        <Logo locale={locale} brand={dict.brand} tagline={dict.tagline} />

        <nav aria-label={dict.nav.home} className="hidden items-center gap-1 lg:flex">
          {items.map((item) => (
            <ActiveLink key={item.key} href={`/${locale}${item.path}`} className="nav-link text-sm">
              {item.label}
            </ActiveLink>
          ))}
        </nav>

        <div className="flex items-center gap-2">
          <LangSwitcher locale={locale} label={dict.common.switchLanguage} shortLabel={dict.common.switchLanguageShort} />
          <a href={appUrl} className="btn btn-secondary hidden text-sm md:inline-flex" data-testid="login-link">
            {dict.common.login}
          </a>
          <a href={appUrl} className="btn btn-primary hidden text-sm sm:inline-flex" data-testid="start-link">
            {dict.common.start}
          </a>
          <MobileMenu
            locale={locale}
            items={items}
            secondary={secondary}
            openLabel={dict.common.menu}
            closeLabel={dict.common.closeMenu}
            appUrl={appUrl}
            loginLabel={dict.common.login}
          />
        </div>
      </div>
    </header>
  );
}
