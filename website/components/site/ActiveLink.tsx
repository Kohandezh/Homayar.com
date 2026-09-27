"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentProps } from "react";

/** A link that marks itself with aria-current="page" when it points at the current page. */
export function ActiveLink({
  href,
  exact = false,
  children,
  ...rest
}: ComponentProps<typeof Link> & { exact?: boolean }) {
  const pathname = usePathname();
  const target = typeof href === "string" ? href : (href.pathname ?? "");
  const isActive = exact ? pathname === target : pathname === target || pathname.startsWith(`${target}/`);
  return (
    <Link href={href} aria-current={isActive ? "page" : undefined} {...rest}>
      {children}
    </Link>
  );
}
