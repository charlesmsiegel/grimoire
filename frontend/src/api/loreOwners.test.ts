import { beforeEach, expect, test, vi } from "vitest";
import { loreOwnerOptions } from "./loreOwners";

vi.mock("./client", () => ({
  api: { listCharacters: vi.fn(), listPCs: vi.fn(), listEntities: vi.fn() },
}));
import { api } from "./client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCharacters as any).mockResolvedValue([{ id: "tanaka", name: "Tanaka" }]);
  (api.listPCs as any).mockResolvedValue([{ id: "hero", name: "Hero" }]);
  (api.listEntities as any).mockResolvedValue([{ id: "old-dojo", name: "Old Dojo" }]);
});

test("collects characters, pcs, locations as owner refs", async () => {
  const opts = await loreOwnerOptions("w");
  expect(opts).toEqual([
    { ref: "characters:tanaka", label: "Tanaka", kind: "characters" },
    { ref: "pcs:hero", label: "Hero", kind: "pcs" },
    { ref: "locations:old-dojo", label: "Old Dojo", kind: "locations" },
  ]);
  expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w" }, "locations");
});
