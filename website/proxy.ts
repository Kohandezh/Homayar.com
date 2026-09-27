import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { defaultLocale, isLocale } from "./lib/i18n";

/**
 * Locale routing.
 * "/"            -> "/fa"
 * "/product"     -> "/fa/product"
 * "/de/anything" -> "/fa/de/anything" (which then renders the Persian 404 page)
 * "/fa/..." and "/en/..." pass through, with the locale exposed to the 404 page as a request header.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const first = pathname.split("/")[1] ?? "";
  if (isLocale(first)) {
    // The global 404 page reads this header to pick its language.
    const headers = new Headers(request.headers);
    headers.set("x-locale", first);
    return NextResponse.next({ request: { headers } });
  }

  const url = request.nextUrl.clone();
  url.pathname = `/${defaultLocale}${pathname === "/" ? "" : pathname}`;
  return NextResponse.redirect(url, 307);
}

export const config = {
  // Skip the API, Next internals, metadata files and anything that looks like a file.
  matcher: ["/((?!api|_next|sitemap\\.xml|robots\\.txt|.*\\..*).*)"],
};
