import { beforeEach, expect, test, vi } from "vitest";
import { loreOwnerOptions } from "./loreOwners";

vi.mock("./client", () => ({
  api: {
    listCharacters: vi.fn(), listPCs: vi.fn(), listEntities: vi.fn(),
    actorImageUrl: (sc: { kind: string; id: string }, k: string, a: string, v: string, n: string) =>
      `/api/worlds/${sc.id}/${k}/${a}/versions/${v}/images/${n}`,
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
  const opts = await loreOwnerOptions({ kind: "world", id: "w" });
  expect(opts).toEqual([
    { ref: "characters:tanaka", label: "Tanaka", kind: "characters" },
    { ref: "pcs:hero", label: "Hero", kind: "pcs" },
    { ref: "locations:old-dojo", label: "Old Dojo", kind: "locations" },
  ]);
  expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w" }, "locations");
});

test("actors with avatars get an avatar url; others get none", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "maren", name: "Maren", default_version: "v1", has_avatar: true, versions: [] },
    { id: "hedde", name: "Hedde", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  // A PC resolves its own art under `pcs`, not the character folder (#219) --
  // before PCs had images at all, every owner chip for one showed initials.
  (api.listPCs as any).mockResolvedValue([
    { id: "wren", name: "Wren", tags: [], default_version: "v2", has_avatar: true, versions: [] },
    { id: "brack", name: "Brack", tags: [], default_version: "v1", has_avatar: false, versions: [] },
  ]);
  (api.listEntities as any).mockResolvedValue([]);
  const opts = await loreOwnerOptions({ kind: "world", id: "w" });
  expect(opts.find((o) => o.ref === "characters:maren")?.avatar)
    .toBe("/api/worlds/w/characters/maren/versions/v1/images/avatar");
  expect(opts.find((o) => o.ref === "characters:hedde")?.avatar).toBeUndefined();
  expect(opts.find((o) => o.ref === "pcs:wren")?.avatar)
    .toBe("/api/worlds/w/pcs/wren/versions/v2/images/avatar");
  expect(opts.find((o) => o.ref === "pcs:brack")?.avatar).toBeUndefined();
});
