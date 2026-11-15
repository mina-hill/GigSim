import type { NextConfig } from "next";

const backend = (process.env.GIGBID_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const config: NextConfig = {
  output: "standalone",
  ...(process.env.GIGBID_NEXT_AUTH === "1" ? { distDir: ".next/local-auth" } : {}),
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` },
      { source: "/health/deps", destination: `${backend}/health/deps` },
    ];
  },
};

export default config;
