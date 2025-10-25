import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    BACKEND_API_BASE: process.env.BACKEND_API_BASE ?? "http://localhost:8000",
  },
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "lh3.googleusercontent.com",
        pathname: "/**",
      },
    ],
  },
};

export default nextConfig;
