"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import type { NavItem } from "./nav";

export function BottomTabBar({ locale, items, label }: { locale: string; items: NavItem[]; label: string }) {
  const pathname = usePathname();
  const base = `/${locale}`;
  return (
    <nav aria-label={label} className="tabbar lg:hidden" data-testid="bottom-tab-bar">
      <ul className="grid h-full grid-cols-5">
        {items.map((item) => {
          const href = `${base}${item.path}`;
          const active = item.path === "" ? pathname === base : pathname === href || pathname.startsWith(`${href}/`);
          return (
            <li key={item.key} className="min-w-0">
              <Link href={href} className="tab-item" aria-current={active ? "page" : undefined}>
                <span className="tab-icon">
                  <Icon name={item.icon} size={22} />
                </span>
                <span className="truncate">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
