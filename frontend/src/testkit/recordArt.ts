import { vi } from "vitest";
import type { ArtRecordKind, EntityScope, GalleryImage, RecordArtImage, RecordArtRead, RecordArtTarget } from "../api/types";
import type { RecordArtAdapter } from "../components/art/types";

export function artTarget(kind: ArtRecordKind = "characters", scope: EntityScope["kind"] = "world"): RecordArtTarget {
  const common = { scope: { kind: scope, id: "Realm" }, record_id: kind === "characters" ? "Seraphine" : kind === "pcs" ? "Mara" : "Saltmarch" };
  return kind === "characters" || kind === "pcs" ? { ...common, kind, version: "main" } : { ...common, kind };
}
export function artImage(target: RecordArtTarget, name = "avatar", changes: Partial<RecordArtImage> = {}): RecordArtImage {
  const description = { scope: "shared" as const, text: "A portrait", described: true, conflicts: [] };
  return { name, ext: "png", v: name + "-1", image_id: "shared", url: "/api/art/" + name + "?v=1",
    thumb: "/api/art/" + name + "?w=160&v=1", description: description.text, described: true,
    description_available: true, description_rev: name + "-caption-1",
    effective_description: description, write_target_description: description,
    width: 800, height: 1200, focus: 50, provenance: target.scope.kind,
    placement: { surface: "record", target, name, expected_v: name + "-1" }, ...changes };
}
export function artRead(target = artTarget(), images = [artImage(target), artImage(target, "gallery_1")]): RecordArtRead {
  return { target, images, world_originals: [], operations: ["append", "reuse", "promote", "focus", "description", "remove",
    ...(target.scope.kind === "world" ? ["draft_description" as const] : [])] };
}
export function artAdapter(target = artTarget(), data = artRead(target)) {
  const adapter = {
    target, read: vi.fn<RecordArtAdapter["read"]>().mockResolvedValue(data),
    append: vi.fn<RecordArtAdapter["append"]>().mockResolvedValue({ name: "gallery_2", ext: "png", v: "new", image_id: "new" }),
    reuse: vi.fn<RecordArtAdapter["reuse"]>().mockResolvedValue({ status: "ok", created: { name: "gallery_2", ext: "png", v: "new", image_id: "new" } }),
    remove: vi.fn<RecordArtAdapter["remove"]>().mockResolvedValue({ ok: true }),
    promote: vi.fn<RecordArtAdapter["promote"]>().mockResolvedValue({ ok: true }),
    setFocus: vi.fn<RecordArtAdapter["setFocus"]>().mockResolvedValue({ ok: true }),
    setDescription: vi.fn<RecordArtAdapter["setDescription"]>().mockResolvedValue({ status: "ok" }),
    finishDescription: vi.fn<RecordArtAdapter["finishDescription"]>().mockResolvedValue({ status: "ok" }),
    ...(target.scope.kind === "world" ? { draftDescription: vi.fn<NonNullable<RecordArtAdapter["draftDescription"]>>().mockResolvedValue("A map") } : {}),
  };
  return adapter;
}

export function galleryRow(image: RecordArtImage): GalleryImage {
  return { kind: image.placement.target.kind, id: image.placement.target.record_id, vid: image.placement.target.version ?? "",
    name: image.name, record_name: image.placement.target.record_id, ext: image.ext, url: image.url, thumb: image.thumb,
    description: image.description, described: image.described, image_id: image.image_id, v: image.v,
    placement: image.placement, provenance: image.provenance };
}
