import { createContext, createElement, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ART_RECORD_KINDS, type EntityScope, type GalleryImage, type RecordArtTarget } from "../../api/types";
import { artRecordHref, sectionHref } from "../../worldPaths";
import { localImageDownloadUrl } from "./recordArtAdapter";
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
export function galleryImageDescriptor(image: GalleryImage, scope: EntityScope, wid: string): ImageDescriptor | null {
  if (image.kind === "greetings") return null;
  let recordDestination: string | undefined;
  const library = image.kind === "world" || image.kind === "campaign";
  if ((ART_RECORD_KINDS as readonly string[]).includes(image.kind)) {
    const target: RecordArtTarget = image.kind === "characters" || image.kind === "pcs"
      ? { scope, kind: image.kind, record_id: image.id, version: image.vid }
      : { scope, kind: image.kind as Exclude<RecordArtTarget["kind"], "characters" | "pcs">, record_id: image.id };
    recordDestination = artRecordHref(target);
  } else if (image.kind === "world") {
    recordDestination = sectionHref({ kind: "world", id: wid }, { kind: "section", at: "images", view: "world" });
  } else if (image.kind === "campaign" && scope.kind === "campaign") {
    recordDestination = sectionHref({ kind: "world", id: wid }, { kind: "section", at: "images", forCampaign: scope.id, view: "campaign-library" });
  }
  return { key: galleryImageKey(image), originalUrl: image.url, thumbnailUrl: image.thumb,
    label: image.record_name + " / " + (image.kind === "characters" || image.kind === "pcs" ? image.vid + " / " + image.name : image.name),
    alt: image.description || image.record_name + " — " + image.name, version: image.v ?? image.url,
    description: image.description, described: image.described, imageId: image.image_id,
    primaryLabel: image.kind === "characters" || image.kind === "pcs" ? "Avatar" : "Primary image",
    role: library ? "library" : image.name === "avatar" ? "primary" : "gallery",
    provenance: image.provenance, recordDestination, destinationLabel: library ? "Manage images" : undefined,
    downloadUrl: localImageDownloadUrl(image.url) };
}
