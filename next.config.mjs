/** @type {import('next').NextConfig} */
const nextConfig = {
  // In development, Next.js proxies /api/* to the Flask app started with
  // `python api/run.py`. On Vercel the rewrite is skipped: api/*.py is
  // served natively as a Python Function at the same path.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:5328/api/:path*" }];
  },
};

export default nextConfig;
