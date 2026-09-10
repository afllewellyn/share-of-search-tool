/** @type {import('next').NextConfig} */
const nextConfig = {
  // The page only ever calls /api/*. In development that proxies to the Flask
  // app started with `python api/index.py`. On Vercel every /api/* path is
  // rewritten to the one Python Function (api/index.py); Flask sees the
  // original path and routes it itself. Same shape as Vercel's nextjs-flask
  // template.
  async rewrites() {
    const flask = process.env.NODE_ENV === "development" ? "http://127.0.0.1:5328/api/:path*" : "/api/";
    return [{ source: "/api/:path*", destination: flask }];
  },
};

export default nextConfig;
