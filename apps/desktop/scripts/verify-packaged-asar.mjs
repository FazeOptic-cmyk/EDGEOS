import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, readdir, stat } from "node:fs/promises";
import { extractFile, listPackage } from "@electron/asar";
import { isAbsolute, relative, resolve, sep } from "node:path";

const archiveArgument = process.argv[2];
const resourcesArgument = process.argv[3];
if (!archiveArgument) {
  console.error("Usage: node verify-packaged-asar.mjs <app.asar> [packaged-resources-root]");
  process.exit(2);
}

const archive = resolve(archiveArgument);
const archiveEntries = new Set(listPackage(archive).map((entry) => entry.replace(/^[/\\]/u, "").replaceAll("\\", "/")));
for (const required of ["dist/src/main.js", "dist/src/resource-integrity.json"]) {
  if (!archiveEntries.has(required)) throw new Error(`Packaged ASAR is missing ${required}`);
}

// @electron/asar 3.x splits archive directory names on the host's path.sep.
// Keep slash-normalized inventory keys above, but use native separators here.
const manifest = JSON.parse(extractFile(archive, ["dist", "src", "resource-integrity.json"].join(sep)).toString("utf8"));
if (manifest.schemaVersion !== 1 || manifest.algorithm !== "sha256" || !Array.isArray(manifest.files) || manifest.files.length === 0) {
  throw new Error("Packaged resource-integrity manifest has an invalid structure");
}

const declaredPaths = new Set();
for (const entry of manifest.files) {
  if (!entry || typeof entry.path !== "string" || !entry.path || entry.path.includes("\\") ||
      isAbsolute(entry.path) || entry.path.split("/").some((part) => !part || part === "." || part === "..") ||
      !Number.isSafeInteger(entry.bytes) || entry.bytes < 0 || !/^[a-f0-9]{64}$/u.test(entry.sha256)) {
    throw new Error("Packaged resource-integrity manifest contains an invalid entry");
  }
  if (declaredPaths.has(entry.path)) throw new Error(`Packaged resource-integrity manifest contains duplicate path ${entry.path}`);
  declaredPaths.add(entry.path);
}
for (const required of ["web/server.js", "api/edgeos-api.exe"]) {
  if (!declaredPaths.has(required)) throw new Error(`Packaged resource-integrity manifest does not cover ${required}`);
}

async function hashFile(path) {
  return new Promise((resolveHash, reject) => {
    const hash = createHash("sha256");
    const stream = createReadStream(path);
    stream.once("error", reject);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.once("end", () => resolveHash(hash.digest("hex")));
  });
}

if (resourcesArgument) {
  const resourcesRoot = resolve(resourcesArgument);
  const actualPaths = new Set();
  const walk = async (directory) => {
    for (const item of await readdir(directory, { withFileTypes: true })) {
      const absolute = resolve(directory, item.name);
      if ((await lstat(absolute)).isSymbolicLink()) throw new Error(`Packaged resource unexpectedly uses a symbolic link: ${absolute}`);
      if (item.isDirectory()) await walk(absolute);
      else if (item.isFile()) actualPaths.add(relative(resourcesRoot, absolute).split(sep).join("/"));
    }
  };
  await walk(resolve(resourcesRoot, "web"));
  await walk(resolve(resourcesRoot, "api"));

  if (actualPaths.size !== declaredPaths.size || [...actualPaths].some((path) => !declaredPaths.has(path))) {
    throw new Error("Packaged web/API resource set does not exactly match the ASAR integrity manifest");
  }

  for (const entry of manifest.files) {
    const absolute = resolve(resourcesRoot, ...entry.path.split("/"));
    const relativePath = relative(resourcesRoot, absolute);
    if (!relativePath || relativePath.startsWith(`..${sep}`) || isAbsolute(relativePath)) {
      throw new Error(`Packaged resource path escapes the resource root: ${entry.path}`);
    }
    const details = await stat(absolute);
    const digest = await hashFile(absolute);
    if (!details.isFile() || details.size !== entry.bytes || digest !== entry.sha256) {
      throw new Error(`Packaged resource failed its size/hash check: ${entry.path}`);
    }
  }
}

console.log(`Verified packaged ASAR integrity manifest with ${manifest.files.length} resource hashes${resourcesArgument ? " and exact packaged payload bytes" : ""}.`);
