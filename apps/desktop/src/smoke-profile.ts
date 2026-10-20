import { posix, win32 } from "node:path";

export interface SmokeTestConfiguration {
  nonce: string;
  userData: string;
}

export function resolveSmokeTestConfiguration(
  argv: readonly string[],
  profileOverride: string | undefined,
  platform: NodeJS.Platform = process.platform,
): SmokeTestConfiguration | null {
  const prefix = "--edgeos-smoke-test=";
  const argument = argv.find((value) => value.startsWith(prefix));
  const nonce = argument?.slice(prefix.length);
  // A leftover environment variable must never change an ordinary app launch.
  if (!nonce || !/^[A-Za-z0-9-]{20,100}$/u.test(nonce)) return null;

  const invalidProfile = (): never => {
    throw new Error("Packaged Electron smoke tests require EDGEOS_SMOKE_USER_DATA_DIR to be an absolute, non-root directory.");
  };
  if (!profileOverride || /[\u0000-\u001f]/u.test(profileOverride)) return invalidProfile();

  const paths = platform === "win32" ? win32 : posix;
  const userData = paths.normalize(profileOverride);
  const root = paths.parse(userData).root;
  if (!paths.isAbsolute(userData)) return invalidProfile();
  if (platform === "win32") {
    // isAbsolute also accepts current-drive paths such as \profile. Only a
    // drive-qualified path or complete UNC share is independent of the cwd.
    if (!/^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+(?:\\|$))/u.test(userData)
      || /^\\\\[?.]\\/u.test(userData)
      || /[<>:"|?*]/u.test(userData.replace(/^[A-Za-z]:/u, ""))) {
      return invalidProfile();
    }
    const components = userData.slice(root.length).split("\\").filter(Boolean);
    // Win32 strips trailing dots/spaces and treats device names specially;
    // accepting them could resolve to a different directory or even the root.
    if (components.some((component) => /[. ]$/u.test(component)
      || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/iu.test(component))) {
      return invalidProfile();
    }
  }
  const trimSeparators = (value: string): string => platform === "win32"
    ? value.replace(/[\\/]+$/u, "") : value.replace(/\/+$/u, "");
  if (trimSeparators(userData) === trimSeparators(root)) return invalidProfile();
  return { nonce, userData };
}
