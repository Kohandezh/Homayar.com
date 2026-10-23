import type { NextConfig } from "next";
import { initOpenNextCloudflareForDev } from "@opennextjs/cloudflare";

const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next",
  turbopack: { root: __dirname },
  reactStrictMode: true,
  experimental: {
    globalNotFound: true,
  },
  poweredByHeader: false,
  images: {
    unoptimized: true,
  },
};

export default nextConfig;

// Gives `next dev` the Worker bindings (D1, send_email) and the `.dev.vars` secrets,
// so `getCloudflareContext()` works in route handlers.
// https://opennext.js.org/cloudflare/get-started
initOpenNextCloudflareForDev();
