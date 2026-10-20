// @vitest-environment node
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import FileSystemCache from "next/dist/server/lib/incremental-cache/file-system-cache";
import { nodeFs } from "next/dist/server/lib/node-fs-methods";
import { getRouteCacheKey } from "next/dist/server/lib/route-cache-key";
import type { IncrementalCacheKind } from "next/dist/server/response-cache";
import type { RouteKind } from "next/dist/server/route-kind";
import { afterEach, describe, expect, it, vi } from "vitest";

async function loadConfig(desktopBuild: string | undefined) {
  vi.stubEnv("NEXT_PUBLIC_DESKTOP_BUILD", desktopBuild);
  vi.resetModules();
  return (await import("./next.config")).default;
}

async function snapshotFiles(root: string, directory = root): Promise<Record<string, string>> {
  const files: Record<string, string> = {};
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) Object.assign(files, await snapshotFiles(root, path));
    else files[relative(root, path)] = (await readFile(path)).toString("base64");
  }
  return files;
}

describe("desktop resource cache policy", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it.each([undefined, "false"])("preserves hosted disk caching with desktop flag %s", async (flag) => {
    const config = await loadConfig(flag);
    expect(config.experimental).not.toHaveProperty("isrFlushToDisk");
  });

  it("serves packaged static routes across restarts without changing their resource tree", async () => {
    const config = await loadConfig("true");
    const resources = await mkdtemp(join(tmpdir(), "edgeos-web-cache-"));
    const serverDistDir = join(resources, ".next", "server");
    try {
      const appDirectory = join(serverDistDir, "app");
      await mkdir(appDirectory, { recursive: true });
      const routes = ["index", "settings"].map((name) => {
        const pathname = name === "index" ? "/" : `/${name}`;
        const owner = {
          kind: "APP_PAGE" as RouteKind.APP_PAGE,
          sourceRoute: `/(dashboard)${pathname === "/" ? "" : pathname}/page`,
        };
        return { name, owner, key: getRouteCacheKey(pathname, owner) };
      });
      for (const { name, owner, key } of routes) {
        await writeFile(join(appDirectory, `${name}.html`), `<main>${name}</main>`);
        await writeFile(join(appDirectory, `${name}.rsc`), `packaged ${name} payload`);
        await writeFile(join(appDirectory, `${name}.meta`), JSON.stringify({
          routeCache: { key, owner, isFallback: false },
        }));
      }
      const packagedFiles = await snapshotFiles(resources);
      for (let launch = 0; launch < 2; launch++) {
        const cache = new FileSystemCache({
          fs: nodeFs,
          serverDistDir,
          // Match Next's default when our config does not override the option.
          flushToDisk: config.experimental?.isrFlushToDisk ?? true,
          revalidatedTags: [],
          _requestHeaders: {},
        });
        for (const { name, key } of routes) {
          const response = await cache.get(key, {
            kind: "APP_PAGE" as IncrementalCacheKind.APP_PAGE, isFallback: false,
          });
          expect(response?.value).toMatchObject({ html: `<main>${name}</main>` });
        }
        expect(await snapshotFiles(resources)).toEqual(packagedFiles);
      }
    } finally {
      await rm(resources, { recursive: true, force: true });
    }
  });
});
