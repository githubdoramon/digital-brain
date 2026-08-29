/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    // Glasses videos can exceed Next's default 10 MB middleware body limit.
    middlewareClientMaxBodySize: "1gb",
  },
};

export default nextConfig;
