/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    const apiBase = process.env.INTERNAL_API_URL || 'http://api:8456';
    return [
      { source: '/api/:path*', destination: `${apiBase}/api/:path*` },
      { source: '/health',     destination: `${apiBase}/health` },
    ];
  },
};
module.exports = nextConfig;
