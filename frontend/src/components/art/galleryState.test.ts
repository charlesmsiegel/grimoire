import { describe, expect, test } from "vitest";
import type { GalleryImage } from "../../api/types";
import { createGalleryStateStore, galleryHistoryKey, filterGallery, galleryImageDescriptor, galleryImageKey } from "./galleryState";
const image: GalleryImage = { kind: "characters", id: "Seraphine", vid: "older", name: "gallery_1",
  record_name: "Seraphine", url: "/api/art?v=1", thumb: "/api/art?w=160&v=1", v: "1",
  ext: "png", described: false, description: "A map of Saltmarch" };
test("state is isolated by scope and history entry and belongs to one store", () => {
  const store = createGalleryStateStore(), key = galleryHistoryKey("world:Realm", "first");
  store.set(key, { tab: "gallery", kind: "characters", search: "map", unfinished: true, appearedOnly: false, selected: galleryImageKey(image), scroll: 251 });
  expect(store.get(key)?.scroll).toBe(251);
  expect(store.get(galleryHistoryKey("campaign:Realm", "first"))).toBeUndefined();
  expect(store.get(galleryHistoryKey("world:Realm", "second"))).toBeUndefined();
  expect(createGalleryStateStore().get(key)).toBeUndefined();
});
test("record/version/description search composes with every existing filter", () => {
  const filters = { kind: null, search: "MAP", unfinished: true, appearedOnly: true };
  expect(filterGallery([image], filters, new Set(["characters/Seraphine"]))).toEqual([image]);
  expect(filterGallery([image], { ...filters, search: "OLDER" }, new Set(["characters/Seraphine"]))).toEqual([image]);
  expect(filterGallery([image], { ...filters, search: "serAPHINE" }, new Set(["characters/Seraphine"]))).toEqual([image]);
  expect(filterGallery([image], { ...filters, kind: "locations" }, new Set(["characters/Seraphine"]))).toEqual([]);
  expect(filterGallery([image], filters, new Set())).toEqual([]);
  expect(filterGallery([{ ...image, described: true }], filters, new Set(["characters/Seraphine"]))).toEqual([]);
});
describe.each(["world", "campaign"] as const)("typed %s destinations", (kind) => {
  test.each(["characters", "pcs", "locations", "items", "lore", "groups", "creatures"])("%s opens Art", (recordKind) => {
    const descriptor = galleryImageDescriptor({ ...image, kind: recordKind }, { kind, id: "Realm" }, "Realm");
    expect(descriptor?.recordDestination).toContain("/" + recordKind + "/Seraphine?tab=art");
    expect(descriptor?.recordDestination?.includes("v=older")).toBe(["characters", "pcs"].includes(recordKind));
  });
});
test("library destinations open their existing managers; unsupported and greeting kinds have no common descriptor", () => {
  const world = galleryImageDescriptor({ ...image, kind: "world" }, { kind: "world", id: "Realm" }, "Realm");
  expect(world?.recordDestination).toBe("/worlds/Realm/images?view=world");
  const campaign = galleryImageDescriptor({ ...image, kind: "campaign" }, { kind: "campaign", id: "Mara" }, "Realm");
  expect(campaign?.recordDestination).toBe("/worlds/Realm/images?for=Mara&view=campaign-library");
  expect(campaign?.destinationLabel).toBe("Manage images");
  expect(galleryImageDescriptor({ ...image, kind: "greetings" }, { kind: "world", id: "Realm" }, "Realm")).toBeNull();
  expect(galleryImageDescriptor({ ...image, kind: "unknown" }, { kind: "world", id: "Realm" }, "Realm")?.recordDestination).toBeUndefined();
});
test("shared objects keep distinct placement occurrences", () => {
  expect(galleryImageKey(image)).not.toBe(galleryImageKey({ ...image, vid: "default" }));
});

test("entity gallery labels and searches omit their internal default version", () => {
  const entity = { ...image, kind: "locations", vid: "default", record_name: "Saltmarch" };
  expect(galleryImageDescriptor(entity, { kind: "world", id: "Realm" }, "Realm")?.label).toBe("Saltmarch / gallery_1");
  expect(filterGallery([entity], { kind: null, search: "default", unfinished: false, appearedOnly: false }, null)).toEqual([]);
});

describe("removal questions (viewer removal design §1.2)", () => {
  const world = { kind: "world" as const, id: "Realm" }, campaign = { kind: "campaign" as const, id: "Mara" };
  const placed = (scope: typeof world | typeof campaign, kind = "characters", name = "gallery_3"): GalleryImage => ({
    ...image, kind, name, record_name: "Seraphine", provenance: scope.kind === "world" ? "world" : "campaign",
    placement: { surface: "record", name, expected_v: "1",
      target: kind === "characters" ? { scope, kind: "characters", record_id: "Seraphine", version: "older" }
        : { scope, kind: "locations", record_id: "Seraphine" } },
  });
  test("a world-scoped record placement is removed", () => {
    expect(galleryImageDescriptor(placed(world), world, "Realm")?.removal?.question).toBe("Remove Seraphine's gallery 3?");
    expect(galleryImageDescriptor(placed(world, "characters", "avatar"), world, "Realm")?.removal?.question)
      .toBe("Remove Seraphine's avatar?");
    expect(galleryImageDescriptor(placed(world, "locations", "avatar"), world, "Realm")?.removal?.question)
      .toBe("Remove Seraphine's primary image?");
  });
  test.each(["campaign", "inherited"] as const)("a campaign-scoped %s record placement is hidden in the campaign", (provenance) => {
    expect(galleryImageDescriptor({ ...placed(campaign), provenance }, campaign, "Realm")?.removal?.question)
      .toBe("Hide Seraphine's gallery 3 only in this campaign? Any inherited world image in this slot is hidden too.");
  });
  test("campaign library rows say whether the world keeps the picture", () => {
    const row = { ...image, kind: "campaign", id: "", vid: "", name: "harbour", record_name: "Library" };
    expect(galleryImageDescriptor({ ...row, provenance: "inherited" }, campaign, "Realm")?.removal?.question)
      .toBe("Hide harbour in this campaign? The world keeps it.");
    expect(galleryImageDescriptor({ ...row, provenance: "campaign" }, campaign, "Realm")?.removal?.question)
      .toBe("Remove harbour from this campaign's library?");
  });
  test("a world library row is deleted for every campaign, with or without provenance", () => {
    const row = { ...image, kind: "world", id: "", vid: "", name: "coastline", record_name: "World library" };
    expect(galleryImageDescriptor(row, world, "Realm")?.removal?.question)
      .toBe("Delete coastline from the world library? Every campaign on this world loses it.");
    expect(galleryImageDescriptor({ ...row, provenance: "world" }, world, "Realm")?.removal?.question)
      .toBe("Delete coastline from the world library? Every campaign on this world loses it.");
  });
  test("greetings, world originals and record rows without a version or placement are not removable", () => {
    expect(galleryImageDescriptor({ ...placed(world), kind: "greetings" }, world, "Realm")).toBeNull();
    expect(galleryImageDescriptor({ ...placed(campaign), provenance: "world-original" }, campaign, "Realm")?.removal).toBeUndefined();
    expect(galleryImageDescriptor({ ...placed(world), v: undefined }, world, "Realm")?.removal).toBeUndefined();
    expect(galleryImageDescriptor({ ...placed(world), placement: undefined }, world, "Realm")?.removal).toBeUndefined();
  });
});
