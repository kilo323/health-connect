import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  // Static export only in production; dev mode uses the live server
  ...(isDev ? {} : { output: "export", distDir: "dist" }),

  turbopack: {},

  // On Windows bind mounts, inotify doesn't fire, so webpack's watcher falls
  // back to polling the whole tree. Exclude heavy, non-source dirs to keep CPU
  // down in the dev container. Extend Next's default ignored regex rather than
  // replacing it with globs — glob patterns make Watchpack scan every directory
  // up to the drive root (and trip over C:\hiberfil.sys, pagefile.sys, etc.).
  webpack: (config) => {
    if (isDev) {
      // Single regex: webpack's schema doesn't allow an array of RegExps.
      // Same as Next's default (node_modules, .git, .next) plus dist.
      config.watchOptions = {
        ...config.watchOptions,
        ignored:
          /^((?:[^/]*(?:\/|$))*)(\.(git|next)|node_modules|dist)(\/((?:[^/]*(?:\/|$))*)(?:$|\/))?/,
      };
    }
    return config;
  },

  // In dev, proxy /api requests to the FastAPI backend
  async rewrites() {
    if (!isDev) return [];
    const backendPort = process.env.BACKEND_PORT || "8000";
    return [
      {
        source: "/api/:path*",
        destination: `http://localhost:${backendPort}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
