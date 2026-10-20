import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createPackage } from "@electron/asar";

const require = createRequire(import.meta.url);
// Initialize the public entrypoint before its internal matcher module: builder's
// CommonJS dependency graph otherwise exposes a partially initialized export.
require("app-builder-lib");
const { copyFiles, getFileMatchers } = require("app-builder-lib/out/fileMatcher.js");
const { load } = require("js-yaml");
const configuration = load(await readFile(new URL("../electron-builder.yml", import.meta.url), "utf8"));
const resourceVerifier = fileURLToPath(new URL("./verify-resources.mjs", import.meta.url));
const packagedVerifier = fileURLToPath(new URL("./verify-packaged-asar.mjs", import.meta.url));

function run(script, ...args) {
  const result = spawnSync(process.execPath, [script, ...args], { encoding: "utf8" });
  assert.ifError(result.error);
  assert.equal(result.signal, null);
  return result;
}

async function inventory(root, directory = root) {
  const paths = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolute = join(directory, entry.name);
    if (entry.isDirectory()) paths.push(...await inventory(root, absolute));
    else if (entry.isFile()) paths.push(relative(root, absolute).split(sep).join("/"));
    else assert.fail(`Unexpected non-file fixture resource: ${absolute}`);
  }
  return paths.sort();
}

async function withFixture(action) {
  const directory = await mkdtemp(join(tmpdir(), "edgeos-resource-copy-test-"));
  try {
    const project = join(directory, "desktop project");
    const source = join(project, "resources");
    const packaged = join(directory, "packaged resources");
    const archiveSource = join(directory, "archive source");
    const archive = join(directory, "app.asar");
    const payloads = new Map([
      ["web/server.js", Buffer.from('require("next");\n')],
      ["web/package.json", Buffer.from('{"name":"fixture-web"}\n')],
      ["web/.next/server/app/page.js", Buffer.from('module.exports = "page";\n')],
      ["web/.next/static/chunks/app.js", Buffer.from('console.log("asset");\n')],
      ["web/.hidden/config.json", Buffer.from('{"hidden":true}\n')],
      ["web/node_modules/next/package.json", Buffer.from('{"name":"next"}\n')],
      ["web/node_modules/next/dist/server/next.js", Buffer.from('module.exports = "next";\n')],
      ["web/node_modules/@scope/runtime/index.js", Buffer.from('module.exports = "scoped";\n')],
      ["web/node_modules/next/node_modules/nested/index.js", Buffer.from('module.exports = "nested";\n')],
      ["web/apps/web/node_modules/leaf/index.js", Buffer.from('module.exports = "leaf";\n')],
      ["api/edgeos-api.exe", Buffer.from([0x4d, 0x5a, 0x00, 0xff, 0x0d, 0x0a, 0x80])],
    ]);
    for (const [path, bytes] of payloads) {
      const file = join(source, ...path.split("/"));
      await mkdir(dirname(file), { recursive: true });
      await writeFile(file, bytes);
    }
    // The staging descriptor lives outside the two runtime payload directories.
    await writeFile(join(source, "desktop-bundle.json"), '{"fixture":true}\n');
    const fixtureVerifier = join(project, "scripts", "verify-resources.mjs");
    await mkdir(dirname(fixtureVerifier), { recursive: true });
    await copyFile(resourceVerifier, fixtureVerifier);
    const stagedResult = run(fixtureVerifier);
    assert.equal(stagedResult.status, 0, stagedResult.stderr);

    const manifestFile = join(project, "dist", "src", "resource-integrity.json");
    const manifest = JSON.parse(await readFile(manifestFile, "utf8"));
    assert.deepEqual(manifest.files.map(({ path }) => path).sort(), [...payloads.keys()].sort());
    await mkdir(join(archiveSource, "dist", "src"), { recursive: true });
    await writeFile(join(archiveSource, "dist", "src", "main.js"), "// fixture entrypoint\n");
    await copyFile(manifestFile, join(archiveSource, "dist", "src", "resource-integrity.json"));
    await createPackage(archiveSource, archive);

    const copy = async (config) => {
      const matchers = getFileMatchers(config, "extraResources", packaged, {
        defaultSrc: project,
        globalOutDir: join(project, "release"),
        customBuildOptions: config.win ?? {},
        macroExpander: (value) => value,
      });
      await copyFiles(matchers, null, false);
    };
    await action({ archive, packaged, payloads, copy });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("builder copies the configured web/API payload exactly, including standalone dependencies", async () => {
  await withFixture(async ({ archive, packaged, payloads, copy }) => {
    await copy(configuration);
    assert.deepEqual(await inventory(packaged), [...payloads.keys()].sort());
    for (const [path, bytes] of payloads) {
      assert.deepEqual(await readFile(join(packaged, ...path.split("/"))), bytes, path);
    }
    const result = run(packagedVerifier, archive, packaged);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /11 resource hashes and exact packaged payload bytes/u);
  });
});

test("the original web matcher omits root node_modules and fails the real integrity verifier", async () => {
  await withFixture(async ({ archive, packaged, payloads, copy }) => {
    const originalConfiguration = structuredClone(configuration);
    originalConfiguration.extraResources = [
      { from: "resources/web", to: "web", filter: ["**/*"] },
      ...originalConfiguration.extraResources.filter((mapping) => mapping.from === "resources/api"),
    ];
    await copy(originalConfiguration);
    const actualPaths = new Set(await inventory(packaged));
    const missing = [...payloads.keys()].filter((path) => !actualPaths.has(path)).sort();
    assert.deepEqual(missing, [...payloads.keys()].filter((path) => path.startsWith("web/node_modules/")).sort());
    assert.ok(actualPaths.has("web/.next/server/app/page.js"));
    assert.ok(actualPaths.has("web/apps/web/node_modules/leaf/index.js"));
    const result = run(packagedVerifier, archive, packaged);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /resource set does not exactly match/u);
    assert.match(result.stderr, /web\/node_modules\/next\/package\.json/u);
  });
});
