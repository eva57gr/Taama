import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Let LAN devices use the dev server (HMR, fonts) when it listens on 0.0.0.0.
  allowedDevOrigins: ["10.*.*.*", "172.*.*.*", "192.168.*.*", "127.0.0.1"],
};

export default nextConfig;
