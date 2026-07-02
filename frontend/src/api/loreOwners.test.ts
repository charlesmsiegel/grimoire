import { beforeEach, expect, test, vi } from "vitest";
import { loreOwnerOptions } from "./loreOwners";

vi.mock("./client", () => ({
  api: {
    listCharacters: vi.fn(), listPCs: vi.fn(), listEntities: vi.fn(),
    imageUrl: (w: string, c: string, v: string, n: string) =>
      `/api/worlds/${w}/characters/${c}/versions/${v}/images/${n}`,
  },
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

test("characters with avatars get an avatar url; others get none", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "maren", name: "Maren", default_version: "v1", has_avatar: true, versions: [] },
    { id: "hedde", name: "Hedde", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  const opts = await loreOwnerOptions("w");
  expect(opts.find((o) => o.ref === "characters:maren")?.avatar)
    .toBe("/api/worlds/w/characters/maren/versions/v1/images/avatar");
  expect(opts.find((o) => o.ref === "characters:hedde")?.avatar).toBeUndefined();
});
