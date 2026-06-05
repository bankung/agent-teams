/** @type {import('next').NextConfig} */
const nextConfig = {
  // output: 'standalone' produces a minimal self-contained server bundle under
  // .next/standalone/ — used by web/Dockerfile.prod. The dev image (web/Dockerfile)
  // uses `next dev` which ignores this setting, so the dev flow is unaffected.
  output: 'standalone',
  async rewrites() {
    const apiBase = process.env.INTERNAL_API_URL || 'http://api:8456';
    return [
      { source: '/api/:path*', destination: `${apiBase}/api/:path*` },
      { source: '/health',     destination: `${apiBase}/health` },
    ];
  },
};
module.exports = nextConfig;
