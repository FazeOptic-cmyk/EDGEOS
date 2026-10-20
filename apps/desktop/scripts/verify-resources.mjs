import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { access, mkdir, readdir, stat, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, sep } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const resourcesRoot = join(root, "resources");
const webServer = join(resourcesRoot, "web", "server.js");
const apiExecutable = join(resourcesRoot, "api", "edgeos-api.exe");
const manifestFile = join(root, "dist", "src", "resource-integrity.json");
const required = [webServer, apiExecutable];

const missing = [];
for (const path of required) {
  try {
    await access(path);
  } catch {
    missing.push(path);
  }
}

if (missing.length) {
  console.error("Windows installer resources are not staged:");
  for (const path of missing) console.error(`- ${path}`);
  console.error("Build the Next.js standalone output and PyInstaller API before running dist:win.");
  process.exitCode = 1;
} else {
  const files = [];

  async function walk(directory) {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const absolute = join(directory, entry.name);
      if (entry.isSymbolicLink()) {
        throw new Error(`Refusing to package a symbolic-link resource: ${absolute}`);
      }
      if (entry.isDirectory()) await walk(absolute);
      else if (entry.isFile()) files.push(absolute);
    }
  }

  await walk(join(resourcesRoot, "web"));
  await walk(join(resourcesRoot, "api"));
  files.sort((left, right) => left.localeCompare(right, "en"));

  const entries = [];
  for (const file of files) {
    const hash = createHash("sha256");
    await new Promise((resolveHash, reject) => {
      const stream = createReadStream(file);
      stream.once("error", reject);
      stream.on("data", (chunk) => hash.update(chunk));
      stream.once("end", resolveHash);
    });
    const details = await stat(file);
    entries.push({
      path: relative(resourcesRoot, file).split(sep).join("/"),
      bytes: details.size,
      sha256: hash.digest("hex"),
    });
  }

  await mkdir(dirname(manifestFile), { recursive: true });
  await writeFile(manifestFile, `${JSON.stringify({
    schemaVersion: 1,
    algorithm: "sha256",
    files: entries,
  }, null, 2)}\n`, "utf8");
  console.log(`Verified ${entries.length} staged files and wrote signed integrity manifest: ${manifestFile}`);
}
