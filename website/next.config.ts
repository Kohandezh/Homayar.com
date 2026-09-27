import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
