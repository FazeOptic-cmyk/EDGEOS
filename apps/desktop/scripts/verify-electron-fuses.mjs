import { FuseV1Options, FuseVersion, getCurrentFuseWire } from "@electron/fuses";
import { resolve } from "node:path";

const executableArgument = process.argv[2];
if (!executableArgument) {
  console.error("Usage: node verify-electron-fuses.mjs <EDGEOS.exe>");
  process.exit(2);
}

const wire = await getCurrentFuseWire(resolve(executableArgument));
if (wire.version !== FuseVersion.V1) throw new Error(`Unsupported Electron fuse wire version: ${wire.version}`);

const disabled = 48;
const enabled = 49;
const expected = new Map([
  [FuseV1Options.RunAsNode, disabled],
  [FuseV1Options.EnableCookieEncryption, enabled],
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable, disabled],
  [FuseV1Options.EnableNodeCliInspectArguments, disabled],
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation, enabled],
  [FuseV1Options.OnlyLoadAppFromAsar, enabled],
  [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, enabled],
  [FuseV1Options.GrantFileProtocolExtraPrivileges, disabled],
]);

const configuredOptions = Object.keys(wire)
  .filter((key) => /^\d+$/u.test(key))
  .map(Number);
if (configuredOptions.length !== expected.size || configuredOptions.some((option) => !expected.has(option))) {
  throw new Error(`Electron exposes ${configuredOptions.length} fuse states, but EDGEOS explicitly verifies ${expected.size}`);
}

for (const [option, expectedState] of expected) {
  if (wire[option] !== expectedState) {
    throw new Error(`Electron fuse ${FuseV1Options[option]} is ${wire[option]}, expected ${expectedState}`);
  }
}

console.log("Verified all eight Electron fuse states in the packaged executable.");
