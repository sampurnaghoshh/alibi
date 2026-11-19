import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Static export (frontend/out): the FastAPI backend serves it, so one URL serves the frontend,
  // /live and /api/*. trailingSlash makes /investigation/ an index.html the backend can serve as is.
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  outputFileTracingRoot: path.join(__dirname, ".."),
  turbopack: {
    root: path.join(__dirname, ".."),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;