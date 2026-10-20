import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve, win32 } from "node:path";
import { fileURLToPath } from "node:url";
import { Script } from "node:vm";
import test from "node:test";
import { createPackage, getRawHeader } from "@electron/asar";

const verifier = fileURLToPath(new URL("./verify-packaged-asar.mjs", import.meta.url));
const verifierSource = await readFile(verifier, "utf8");
const require = createRequire(import.meta.url);
const filesystemPath = require.resolve("@electron/asar/lib/filesystem.js");
const filesystemSource = await readFile(filesystemPath, "utf8");
const filesystemRequire = createRequire(filesystemPath);
const { readFileSync: readAsarFileSync } = require("@electron/asar/lib/disk.js");

async function withFixture(action, { omitManifest = false, omitMain = false, updateManifest } = {}) {
  const directory = await mkdtemp(join(tmpdir(), "edgeos-asar-test-"));
  try {
    const source = join(directory, "app source");
    const resources = join(directory, "packaged resources");
    const archive = join(directory, "EDGEOS app.asar");
    const payloads = [
      ["web/server.js", 'module.exports = "web";\n'],
      ["web/.next/static/chunks/example.js", 'console.log("nested asset");\n'],
      ["api/edgeos-api.exe", "MZ fixture API executable\n"],
    ];
    const manifest = { schemaVersion: 1, algorithm: "sha256", files: [] };
    for (const [path, content] of payloads) {
      const bytes = Buffer.from(content);
      const absolute = join(resources, ...path.split("/"));
      await mkdir(dirname(absolute), { recursive: true });
      await writeFile(absolute, bytes);
      manifest.files.push({ path, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") });
    }
    if (updateManifest) updateManifest(manifest);
    await mkdir(join(source, "dist", "src"), { recursive: true });
    if (!omitMain) await writeFile(join(source, "dist", "src", "main.js"), "// fixture entrypoint\n");
    if (!omitManifest) {
      await writeFile(join(source, "dist", "src", "resource-integrity.json"), JSON.stringify(manifest));
    }
    await createPackage(source, archive);
    await action({ archive, resources, manifest });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

function verify(archive, resources) {
  const args = [verifier, archive, resources].filter((value) => value !== undefined);
  const result = spawnSync(process.execPath, args, { encoding: "utf8" });
  assert.ifError(result.error);
  assert.equal(result.signal, null);
  return result;
}

function windowsFilesystem(archive) {
  // Run the pinned dependency's real traversal code with Windows path semantics,
  // even on a Linux test runner. All non-path dependency imports remain real.
  const module = { exports: {} };
  const load = new Script(`(function(require, module, exports) {\n${filesystemSource}\n})`, {
    filename: filesystemPath,
  }).runInThisContext();
  load((name) => name === "path" ? win32 : filesystemRequire(name), module, module.exports);
  const filesystem = new module.exports.Filesystem(archive);
  const { header, headerSize } = getRawHeader(archive);
  filesystem.setHeader(header, headerSize);
  // The archive lives on the host filesystem; only its internal path traversal
  // is simulated. Reading the packed bytes still uses ASAR's actual disk code.
  filesystem.getRootPath = () => archive;
  return filesystem;
}

async function verifyWithWindowsAsar(archive, source = verifierSource) {
  const filesystem = windowsFilesystem(archive);
  const messages = [];
  // Execute the production CLI body unchanged, supplying its imports explicitly
  // so path.sep and ASAR traversal can use Windows semantics without mutating
  // global Node builtins or requiring experimental ESM mocking flags.
  const body = source.replace(/^import .+;\r?$/gmu, "");
  await new Script(`(async () => {\n${body}\n})()`, { filename: verifier }).runInNewContext({
    createHash, createReadStream, lstat, readdir, stat, isAbsolute, relative, resolve,
    sep: win32.sep,
    process: { argv: [process.execPath, verifier, archive] },
    console: { log: (message) => messages.push(message) },
    listPackage: (file) => {
      assert.equal(file, archive);
      return filesystem.listFiles();
    },
    extractFile: (file, entry) => {
      assert.equal(file, archive);
      return readAsarFileSync(filesystem, entry, filesystem.getFile(entry));
    },
  });
  return messages.join("\n");
}

test("verifies a real ASAR manifest and the exact packaged resource bytes", async () => {
  await withFixture(async ({ archive, resources }) => {
    for (const root of [undefined, resources]) {
      const result = verify(archive, root);
      assert.equal(result.status, 0, result.stderr);
      assert.match(result.stdout, /Verified packaged ASAR integrity manifest with 3 resource hashes/u);
      assert.equal(result.stdout.includes("and exact packaged payload bytes"), root !== undefined);
    }
  });
});

test("production verifier uses Windows ASAR paths and reproduces the old slash-path failure", async () => {
  await withFixture(async ({ archive }) => {
    const filesystem = windowsFilesystem(archive);
    assert.ok(filesystem.listFiles().includes("\\dist\\src\\resource-integrity.json"));
    assert.throws(() => filesystem.getFile("dist/src/resource-integrity.json"), /was not found in this archive/u);
    assert.ok(filesystem.getFile("dist\\src\\resource-integrity.json"));
    assert.match(await verifyWithWindowsAsar(archive), /Verified packaged ASAR integrity manifest with 3 resource hashes/u);

    const oldSource = verifierSource.replace(
      '["dist", "src", "resource-integrity.json"].join(sep)',
      '"dist/src/resource-integrity.json"',
    );
    assert.notEqual(oldSource, verifierSource, "the regression must restore the original extraction argument");
    await assert.rejects(
      verifyWithWindowsAsar(archive, oldSource),
      /"dist\/src\/resource-integrity\.json" was not found in this archive/u,
    );
  });
});

for (const [option, path] of [["omitManifest", "resource-integrity.json"], ["omitMain", "main.js"]]) {
  test(`rejects an archive missing ${path}`, async () => {
    await withFixture(async ({ archive, resources }) => {
      const result = verify(archive, resources);
      assert.equal(result.status, 1);
      assert.ok(result.stderr.includes(`Packaged ASAR is missing dist/src/${path}`), result.stderr);
    }, { [option]: true });
  });
}

test("rejects modified resource bytes even when the file size is unchanged", async () => {
  await withFixture(async ({ archive, resources }) => {
    await writeFile(join(resources, "web", "server.js"), 'module.exports = "bad";\n');
    const result = verify(archive, resources);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Packaged resource failed its size\/hash check: web\/server\.js/u);
  });
});

for (const mutation of ["extra", "missing"]) {
  test(`rejects a packaged resource set with an ${mutation === "extra" ? "extra" : "absent"} file`, async () => {
    await withFixture(async ({ archive, resources }) => {
      if (mutation === "extra") await writeFile(join(resources, "web", "undeclared.js"), "extra");
      else await rm(join(resources, "web", ".next", "static", "chunks", "example.js"));
      const result = verify(archive, resources);
      assert.equal(result.status, 1);
      assert.match(result.stderr, /resource set does not exactly match/u);
      if (mutation === "extra") {
        assert.match(result.stderr, /Declared files: 3; packaged files: 4/u);
        assert.ok(result.stderr.includes('Unexpected (1): ["web/undeclared.js"]'), result.stderr);
        assert.ok(result.stderr.includes("Missing (0): []"), result.stderr);
      } else {
        assert.match(result.stderr, /Declared files: 3; packaged files: 2/u);
        assert.ok(result.stderr.includes('Missing (1): ["web/.next/static/chunks/example.js"]'), result.stderr);
        assert.ok(result.stderr.includes("Unexpected (0): []"), result.stderr);
      }
    });
  });
}

for (const path of ["../outside.js", "web/../server.js", "web\\server.js", "/web/server.js"]) {
  test(`rejects invalid manifest path ${JSON.stringify(path)}`, async () => {
    await withFixture(async ({ archive, resources }) => {
      const result = verify(archive, resources);
      assert.equal(result.status, 1);
      assert.match(result.stderr, /manifest contains an invalid entry/u);
    }, { updateManifest: (manifest) => { manifest.files[0].path = path; } });
  });
}

test("rejects duplicate manifest paths", async () => {
  await withFixture(async ({ archive, resources }) => {
    const result = verify(archive, resources);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /manifest contains duplicate path web\/server\.js/u);
  }, { updateManifest: (manifest) => { manifest.files.push({ ...manifest.files[0] }); } });
});

test("requires an explicit archive path", () => {
  const result = verify();
  assert.equal(result.status, 2);
  assert.match(result.stderr, /Usage:/u);
});
