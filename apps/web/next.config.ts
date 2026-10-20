import type { NextConfig } from "next";
import { productionSecurityHeaders } from "./security-headers";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  allowedDevOrigins: ["127.0.0.1"],
  // Next 16's experimental CLI checker can lose captured `tsc --showConfig`
  // output under some Node/cross-spawn combinations. TypeScript 5 ships the
  // compiler API, so use Next's documented API checker for deterministic
  // production builds. `npm run typecheck` still runs project-local `tsc`.
  experimental: {
    useTypeScriptCli: false,
  },
  async headers() {
    return [{ source: "/:path*", headers: productionSecurityHeaders() }];
  },
};

export default nextConfig;
