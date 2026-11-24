/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const api = process.env.WOLF_API_URL || "http://127.0.0.1:8080";
    return [
      {
        source: "/api/governance/:path*",
        destination: `${api}/governance/api/v1/:path*`,
      },
      {
        source: "/api/platform/:path*",
        destination: `${api}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
