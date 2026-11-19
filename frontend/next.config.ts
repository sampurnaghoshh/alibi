import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export (frontend/out): hosted on Vercel (root directory: frontend) or served by the FastAPI
  // backend itself. trailingSlash makes /investigation/ an index.html any static host can serve.
  // The frontend is self-contained (nothing imported from outside frontend/), so Vercel can build it
  // with "frontend" as the root directory.
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  outputFileTracingRoot: __dirname,
  turbopack: {
    root: __dirname,
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;