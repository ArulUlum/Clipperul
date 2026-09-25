/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    remotePatterns: [
      { protocol: "http", hostname: "localhost" },
      { protocol: "http", hostname: "minio" },
    ],
  },
  // Docker Desktop's bind mount from a Windows host doesn't reliably deliver inotify events
  // into the Linux container, so webpack's native watcher silently misses file edits — poll
  // instead so `docker compose`'s dev-mode hot reload actually picks up changes.
  webpack: (config) => {
    config.watchOptions = {
      poll: 1000,
      aggregateTimeout: 300,
    };
    return config;
  },
};

module.exports = nextConfig;
