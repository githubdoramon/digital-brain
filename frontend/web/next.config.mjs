/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    // Glasses videos can exceed Next's default 10 MB middleware body limit.
    middlewareClientMaxBodySize: "1gb",
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
