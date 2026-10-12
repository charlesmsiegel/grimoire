import { createContext, createElement, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ART_RECORD_KINDS, type EntityScope, type GalleryImage, type RecordArtTarget } from "../../api/types";
import { artRecordHref, sectionHref } from "../../worldPaths";
import { localImageDownloadUrl } from "./recordArtAdapter";
import { removalQuestion, type RemovalSubject } from "./removal";
import type { ImageDescriptor } from "./types";

export type GalleryState = { tab: "gallery" | "queue" | "world" | "campaign-library"; kind: string | null;
  search: string; unfinished: boolean; appearedOnly: boolean; selected: string | null; scroll: number };
export function createGalleryStateStore() {
  const entries = new Map<string, GalleryState>();
  return { get: (key: string) => entries.get(key), set: (key: string, value: GalleryState) => entries.set(key, { ...value }) };
}
const GalleryStateContext = createContext<ReturnType<typeof createGalleryStateStore> | null>(null);
/** This provider lives inside App's store-generation remount, outside routed
 * pages. Back restores presentation, while switching the store forgets it. */
export function GalleryStateProvider({ children }: { children: ReactNode }) {
  const [store] = useState(createGalleryStateStore);
  return createElement(GalleryStateContext.Provider, { value: store }, children);
}
export const galleryHistoryKey = (scope: string, history: string) => JSON.stringify([scope, history]);
export const galleryImageKey = (image: GalleryImage) => JSON.stringify([image.kind, image.id, image.vid, image.name]);
export function useGalleryState(scope: string) {
  const location = useLocation(), context = useContext(GalleryStateContext);
  const fallback = useRef(createGalleryStateStore()), store = context ?? fallback.current;
  const key = galleryHistoryKey(scope, location.key);
  const [state, setState] = useState<GalleryState>(() => store.get(key) ?? {
    tab: new URLSearchParams(location.search).get("view") === "world" ? "world"
      : new URLSearchParams(location.search).get("view") === "campaign-library" ? "campaign-library" : "gallery",
    kind: null, search: "", unfinished: false, appearedOnly: false, selected: null, scroll: 0,
  });
  useEffect(() => { store.set(key, state); }, [store, key, state]);
  return [state, setState] as const;
}
export function galleryNeedsAttention(image: GalleryImage): boolean {
  return image.kind === "greetings" ? image.subjects == null : !image.described;
}
export function filterGallery(images: GalleryImage[], filters: Pick<GalleryState, "kind" | "search" | "unfinished" | "appearedOnly">,
  appeared: Set<string> | null): GalleryImage[] {
  const search = filters.search.trim().toLocaleLowerCase();
  return images.filter((image) => (!filters.kind || image.kind === filters.kind)
    && (!filters.unfinished || galleryNeedsAttention(image))
    && (!filters.appearedOnly || !appeared || appeared.has(image.kind + "/" + image.id))
    && (!search || [image.record_name, image.kind === "characters" || image.kind === "pcs" ? image.vid : "", image.name, image.description].some((text) => text.toLocaleLowerCase().includes(search))));
}
/** The record a gallery row's art hangs off, in the scope the gallery is read
 *  for; null for library and greeting rows, which hang off no record. */
export function galleryRecordTarget(image: GalleryImage, scope: EntityScope): RecordArtTarget | null {
  if (!(ART_RECORD_KINDS as readonly string[]).includes(image.kind)) return null;
  return image.kind === "characters" || image.kind === "pcs"
    ? { scope, kind: image.kind, record_id: image.id, version: image.vid }
    : { scope, kind: image.kind as Exclude<RecordArtTarget["kind"], "characters" | "pcs">, record_id: image.id };
}
const galleryPrimaryLabel = (image: GalleryImage) =>
  image.kind === "characters" || image.kind === "pcs" ? "Avatar" as const : "Primary image" as const;
/** What removing this row would do, or null where the gallery cannot remove it:
 *  a greeting, a world original seen from a campaign, a record row the server
 *  sent no version or placement for (the delete is guarded by both), and a
 *  campaign library row outside a campaign. */
function galleryRemoval(image: GalleryImage, scope: EntityScope): RemovalSubject | null {
  if ((ART_RECORD_KINDS as readonly string[]).includes(image.kind)) {
    if (!image.v || image.placement?.surface !== "record" || image.provenance === "world-original") return null;
    return { surface: "record", scope: scope.kind, record: image.record_name, name: image.name,
      primaryLabel: galleryPrimaryLabel(image) };
  }
  if (image.kind === "campaign" && scope.kind === "campaign")
    return { surface: "campaign-library", inherited: image.provenance === "inherited", name: image.name };
  if (image.kind === "world") return { surface: "world-library", name: image.name };
  return null;
}
export function galleryImageDescriptor(image: GalleryImage, scope: EntityScope, wid: string): ImageDescriptor | null {
  if (image.kind === "greetings") return null;
  let recordDestination: string | undefined;
  const library = image.kind === "world" || image.kind === "campaign";
  const target = galleryRecordTarget(image, scope);
  if (target) {
    recordDestination = artRecordHref(target);
  } else if (image.kind === "world") {
    recordDestination = sectionHref({ kind: "world", id: wid }, { kind: "section", at: "images", view: "world" });
  } else if (image.kind === "campaign" && scope.kind === "campaign") {
    recordDestination = sectionHref({ kind: "world", id: wid }, { kind: "section", at: "images", forCampaign: scope.id, view: "campaign-library" });
  }
  const removal = galleryRemoval(image, scope);
  return { key: galleryImageKey(image), originalUrl: image.url, thumbnailUrl: image.thumb,
    label: image.record_name + " / " + (image.kind === "characters" || image.kind === "pcs" ? image.vid + " / " + image.name : image.name),
    alt: image.description || image.record_name + " — " + image.name, version: image.v ?? image.url,
    description: image.description, described: image.described, imageId: image.image_id,
    primaryLabel: galleryPrimaryLabel(image),
    role: library ? "library" : image.name === "avatar" ? "primary" : "gallery",
    provenance: image.provenance, recordDestination, destinationLabel: library ? "Manage images" : undefined,
    downloadUrl: localImageDownloadUrl(image.url), ...(removal ? { removal: { question: removalQuestion(removal) } } : {}) };
}
