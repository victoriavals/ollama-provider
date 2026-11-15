import type { NextConfig } from "next";

const config: NextConfig = {
  output: "export",
  basePath: "/admin/ui",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
};
export default config;
