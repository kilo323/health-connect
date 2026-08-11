import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  // Static export only in production; dev mode uses the live server
  ...(isDev ? {} : { output: "export", distDir: "dist" }),

  // On Windows bind mounts, inotify doesn't fire, so webpack's watcher falls
  // back to polling the whole tree. Exclude heavy, non-source dirs to keep CPU
  // down in the dev container.
  webpack: (config) => {
    if (isDev) {
      config.watchOptions = {
        ...config.watchOptions,
        ignored: [
          "**/node_modules/**",
          "**/.next/**",
          "**/dist/**",
          "**/.git/**",
        ],
      };
    }
    return config;
  },

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
