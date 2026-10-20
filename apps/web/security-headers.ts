function configuredOrigin(rawValue: string | undefined): string | null {
  if (!rawValue) return null;
  try {
    const url = new URL(rawValue);
    if (!new Set(["http:", "https:"]).has(url.protocol) || url.username || url.password) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function contentSecurityPolicy(environment: NodeJS.ProcessEnv = process.env): string {
  const connectSources = new Set(["'self'"]);
  const imageSources = new Set(["'self'", "data:", "blob:"]);
  const apiOrigin = configuredOrigin(environment.NEXT_PUBLIC_API_URL);
  if (apiOrigin) connectSources.add(apiOrigin);

  const supabaseOrigin = configuredOrigin(environment.NEXT_PUBLIC_SUPABASE_URL);
  if (supabaseOrigin) {
    connectSources.add(supabaseOrigin);
    imageSources.add(supabaseOrigin);
    const realtime = new URL(supabaseOrigin);
    realtime.protocol = realtime.protocol === "https:" ? "wss:" : "ws:";
    connectSources.add(realtime.origin);
  }

  const scriptSources = ["'self'", "'unsafe-inline'"];
  if (environment.NODE_ENV !== "production") scriptSources.push("'unsafe-eval'");

  return [
    "default-src 'self'",
    `script-src ${scriptSources.join(" ")}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src ${[...imageSources].join(" ")}`,
    "font-src 'self' data:",
    `connect-src ${[...connectSources].join(" ")}`,
    "worker-src 'self' blob:",
    "media-src 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "manifest-src 'self'",
  ].join("; ");
}

export function productionSecurityHeaders(environment: NodeJS.ProcessEnv = process.env) {
  return [
    { key: "Content-Security-Policy", value: contentSecurityPolicy(environment) },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "X-DNS-Prefetch-Control", value: "off" },
    { key: "X-XSS-Protection", value: "0" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin-allow-popups" },
    { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
    {
      key: "Permissions-Policy",
      value: "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=(), serial=(), hid=()",
    },
  ];
}
