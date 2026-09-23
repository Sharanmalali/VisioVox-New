/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `next build` and `next dev` both default to .next, so building while
  // the dev server is live replaces the chunks it is still serving - which
  // surfaces as a webpack MODULE_NOT_FOUND that survives a reload and points
  // nowhere near the cause. `make web-build` sets this to build elsewhere.
  distDir: process.env.NEXT_DIST_DIR ?? '.next',
  // Traced output: only the files actually reached are shipped, which is a
  // fraction of node_modules and what the runtime image copies.
  //
  // Note that `next start` prints a warning under this setting and tells you
  // to run .next/standalone/server.js instead. It does still serve correctly,
  // which is why infra/local/preview.sh keeps using it - but the container
  // runs the standalone server, and that is the supported path.
  output: 'standalone',
  // The workspace root, so tracing follows into packages/.
  outputFileTracingRoot: new URL('../../', import.meta.url).pathname,
  // The API base is read at request time on the server, so the same build can
  // be promoted between environments without rebuilding.
  env: { API_INTERNAL_URL: process.env.API_INTERNAL_URL ?? 'http://localhost:8000' },
};
export default nextConfig;
