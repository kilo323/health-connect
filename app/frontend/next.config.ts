import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  // Static export only in production; dev mode uses the live server
  ...(isDev ? {} : { output: "export", distDir: "dist" }),

  // In dev, proxy /api requests to the FastAPI backend
  async rewrites() {
    if (!isDev) return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
