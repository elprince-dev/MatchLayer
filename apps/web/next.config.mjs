/** @type {import('next').NextConfig} */
// Standalone output is required by `infra/docker/web.Dockerfile` (§11.2 of the
// phase-1-foundation design): the production image copies `.next/standalone`
// and runs `node apps/web/server.js`, which only exists when this option is on.
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
