import type { NextConfig } from "next";

// Backend URL is read at build/start time. Defaults to local FastAPI.
// In production set BACKEND_URL to wherever the API is reachable from the
// Next.js server (NOT from the browser) — e.g. an internal service hostname.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Proxy /api/* to the FastAPI backend. The browser only ever talks to the
  // Next.js origin, so cross-origin CORS never enters the picture and the
  // frontend has no hardcoded backend URL.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
