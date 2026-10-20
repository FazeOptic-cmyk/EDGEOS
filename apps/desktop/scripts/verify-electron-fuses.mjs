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
// Electron 44.8.0 adds wasm_trap_handlers at wire index 8 (default enabled).
// @electron/fuses 1.8.0 reads it but does not expose its enum or write it.
// Preserve and explicitly verify that default; never silently ignore new fuses.
// https://github.com/electron/electron/blob/v44.8.0/build/fuses/fuses.json5
const wasmTrapHandlers = FuseV1Options.WasmTrapHandlers ?? 8;
const expected = new Map([
  [FuseV1Options.RunAsNode, disabled],
  [FuseV1Options.EnableCookieEncryption, enabled],
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable, disabled],
  [FuseV1Options.EnableNodeCliInspectArguments, disabled],
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation, enabled],
  [FuseV1Options.OnlyLoadAppFromAsar, enabled],
  [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, disabled],
  [FuseV1Options.GrantFileProtocolExtraPrivileges, disabled],
  [wasmTrapHandlers, enabled],
]);

const configuredOptions = Object.keys(wire)
  .filter((key) => /^\d+$/u.test(key))
  .map(Number);
if (configuredOptions.length !== expected.size || configuredOptions.some((option) => !expected.has(option))) {
  throw new Error(`Electron exposes ${configuredOptions.length} fuse states, but EDGEOS explicitly verifies ${expected.size}`);
}

for (const [option, expectedState] of expected) {
  if (wire[option] !== expectedState) {
    const name = option === wasmTrapHandlers ? "WasmTrapHandlers" : FuseV1Options[option];
    throw new Error(`Electron fuse ${name} is ${wire[option]}, expected ${expectedState}`);
  }
}

console.log(`Verified all ${expected.size} Electron fuse states in the packaged executable.`);
