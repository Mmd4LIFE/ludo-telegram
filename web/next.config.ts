import type { NextConfig } from "next";

// Static export served by nginx at the site root — zero runtime, no Node server.
// (Same model as the poker app: build -> tar webout -> deploy/deploy-web.sh)
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  typescript: { ignoreBuildErrors: true },
};

export default nextConfig;
