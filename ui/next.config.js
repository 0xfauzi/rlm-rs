/** @type {import('next').NextConfig} */
const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8080";
const localstackProxyTarget =
  process.env.LOCALSTACK_PROXY_TARGET ?? "http://localhost:4566";

const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/health/:path*",
        destination: `${apiProxyTarget}/health/:path*`,
      },
      {
        source: "/v1/:path*",
        destination: `${apiProxyTarget}/v1/:path*`,
      },
      {
        source: "/localstack/:path*",
        destination: `${localstackProxyTarget}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
