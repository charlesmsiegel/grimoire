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
