import { rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
await Promise.all([
  rm(join(root, "dist"), { force: true, recursive: true }),
  rm(join(root, "release"), { force: true, recursive: true }),
]);
