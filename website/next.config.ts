import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // website/ is its own project; tell Turbopack so it does not pick the repository root.
  turbopack: { root: __dirname },
  reactStrictMode: true,
  experimental: {
    // One 404 document for every unmatched URL, rendered in the language of the URL.
    globalNotFound: true,
  },
  poweredByHeader: false,
  images: {
    // The site ships no raster images; everything is SVG or CSS.
    unoptimized: true,
  },
};

export default nextConfig;
