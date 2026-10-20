const INTERNAL_ORIGIN = "https://edgeos.invalid";
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;
const ENCODED_PATH_SEPARATOR_OR_CONTROL = /%(?:0[0-9a-f]|1[0-9a-f]|2f|5c|7f)/i;

/**
 * Return a same-origin application path suitable for Next.js navigation.
 * Authentication callbacks must never pass an untrusted absolute,
 * protocol-relative, backslash-normalized, or control-character URL to a
 * router or redirect response.
 */
export function safeRedirectPath(
  value: string | null | undefined,
  fallback = "/",
): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return fallback;
  if (value.includes("\\") || CONTROL_CHARACTERS.test(value)) return fallback;

  const rawPath = value.split(/[?#]/, 1)[0];
  if (ENCODED_PATH_SEPARATOR_OR_CONTROL.test(rawPath)) return fallback;

  try {
    const parsed = new URL(value, INTERNAL_ORIGIN);
    if (parsed.origin !== INTERNAL_ORIGIN) return fallback;

    const path = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) {
      return fallback;
    }
    return path;
  } catch {
    return fallback;
  }
}
