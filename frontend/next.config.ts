import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root to this app so an unrelated lockfile in the
  // user's home directory isn't mistaken for the project root.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
