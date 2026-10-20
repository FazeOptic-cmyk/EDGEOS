import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { flipFuses, getCurrentFuseWire } from "@electron/fuses";
import { PlatformPackager } from "app-builder-lib";
import { load } from "js-yaml";

const verifier = fileURLToPath(new URL("./verify-electron-fuses.mjs", import.meta.url));
const builder = load(await readFile(new URL("../electron-builder.yml", import.meta.url), "utf8"));
const fuseConfig = await PlatformPackager.prototype.generateFuseConfig(builder.electronFuses);
// Official Electron 44.8.0 build/fuses/fuses.json5, in wire order. Include the
// ninth fuse even though the locked builder/fuses writer only knows eight.
const stockStates = "101100011";
const packagedStates = "010011001";
const names = [
  "RunAsNode", "EnableCookieEncryption", "EnableNodeOptionsEnvironmentVariable",
  "EnableNodeCliInspectArguments", "EnableEmbeddedAsarIntegrityValidation",
  "OnlyLoadAppFromAsar", "LoadBrowserProcessSpecificV8Snapshot",
  "GrantFileProtocolExtraPrivileges", "WasmTrapHandlers",
];

function binary(states, version = 1, declaredLength = states.length) {
  return Buffer.concat([
    Buffer.from("MZ test fixture dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"),
    Buffer.from([version, declaredLength]),
    Buffer.from(states),
  ]);
}

async function withExecutable(contents, action) {
  const directory = await mkdtemp(join(tmpdir(), "edgeos-fuse-test-"));
  try {
    const executable = join(directory, "EDGEOS fixture.exe");
    await writeFile(executable, contents);
    await action(executable);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

function verify(executable) {
  const result = spawnSync(process.execPath, [verifier, executable], { encoding: "utf8" });
  assert.ifError(result.error);
  assert.equal(result.signal, null);
  return result;
}

test("real builder config preserves and verifies all nine Electron 44.8.0 fuses", async () => {
  // A browser-specific snapshot is NOT shipped in the stock Windows archive.
  // Enabling that fuse makes Electron request a nonexistent file before app JS.
  assert.equal(builder.electronFuses.loadBrowserProcessSpecificV8Snapshot, false);
  await withExecutable(binary(stockStates), async (executable) => {
    await flipFuses(executable, fuseConfig);
    const wire = await getCurrentFuseWire(executable);
    assert.equal(names.map((_, index) => String.fromCharCode(wire[index])).join(""), packagedStates);
    const result = verify(executable);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Verified all 9 Electron fuse states/u);
  });
});

for (const [index, name] of names.entries()) {
  test(`rejects a changed ${name} fuse`, async () => {
    const states = [...packagedStates];
    states[index] = states[index] === "1" ? "0" : "1";
    await withExecutable(binary(states.join("")), async (executable) => {
      const result = verify(executable);
      assert.equal(result.status, 1);
      assert.match(result.stderr, new RegExp(`Electron fuse ${name} is`));
    });
  });
}

for (const [label, contents, error] of [
  ["missing fuse", binary(packagedStates.slice(0, 8)), /exposes 8 fuse states/u],
  ["unknown additional fuse", binary(`${packagedStates}1`), /exposes 10 fuse states/u],
  ["unsupported version", binary(packagedStates, 2), /Unsupported Electron fuse wire version/u],
  ["missing sentinel", Buffer.from("MZ not an Electron executable"), /Could not find sentinel/u],
  ["truncated wire", binary(packagedStates.slice(0, 8), 1, 9), /WasmTrapHandlers is undefined/u],
  ["removed ninth fuse", binary(`${packagedStates.slice(0, 8)}r`), /WasmTrapHandlers is 114/u],
]) {
  test(`rejects ${label}`, async () => {
    await withExecutable(contents, async (executable) => {
      const result = verify(executable);
      assert.equal(result.status, 1);
      assert.match(result.stderr, error);
    });
  });
}

test("requires an explicit executable path", () => {
  const result = spawnSync(process.execPath, [verifier], { encoding: "utf8" });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /Usage:/u);
});
