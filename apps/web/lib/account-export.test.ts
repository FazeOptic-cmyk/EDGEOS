import { describe, expect, it, vi } from "vitest";
import {
  ACCOUNT_EXPORT_TABLES,
  collectAccountExport,
  type AccountExportClient,
} from "./account-export";

function clientFor(
  records: Record<string, readonly unknown[]>,
  failingTable?: string,
): { client: AccountExportClient; ranges: Array<[string, string, number, number]> } {
  const ranges: Array<[string, string, number, number]> = [];
  return {
    ranges,
    client: {
      from: (table) => ({
        select: () => ({
          order: (column) => ({
            range: async (from, to) => {
              ranges.push([table, column, from, to]);
              if (table === failingTable) return { data: null, error: { message: "hidden database detail" } };
              return { data: [...(records[table] ?? [])].slice(from, to + 1), error: null };
            },
          }),
        }),
      }),
    },
  };
}

describe("collectAccountExport", () => {
  it("reads beyond one PostgREST page in deterministic key order", async () => {
    const profiles = Array.from({ length: 501 }, (_, id) => ({ id }));
    const { client, ranges } = clientFor({ profiles });

    const result = await collectAccountExport(client, "user-1", 500);

    expect(result.profiles).toEqual(profiles);
    expect(ranges.filter(([table]) => table === "profiles")).toEqual([
      ["profiles", "id", 0, 499],
      ["profiles", "id", 500, 999],
    ]);
    for (const [table, orderColumn] of ACCOUNT_EXPORT_TABLES.slice(1)) {
      expect(ranges).toContainEqual([table, orderColumn, 0, 499]);
    }
  });

  it("fails the whole export instead of labeling a partial file complete", async () => {
    const { client } = clientFor({}, "paper_fills");
    const clock = vi.spyOn(Date.prototype, "toISOString");

    await expect(collectAccountExport(client, "user-1")).rejects.toThrow(
      "Account export stopped before completion at paper_fills. No partial export was downloaded.",
    );
    expect(clock).toHaveBeenCalled();
    clock.mockRestore();
  });
});
