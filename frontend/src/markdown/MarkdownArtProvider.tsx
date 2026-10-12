import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useId, type ReactNode } from "react";
import { useArtNavigation } from "../components/art/ArtNavigationProvider";
import { ImageViewer } from "../components/art/ImageViewer";
import { REMOVED_IMAGE_NOTICE, type ImageViewerCloseReason, type ImageDescriptor } from "../components/art/types";

type Selection = { occurrence: string; images: ImageDescriptor[]; initialKey: string; opener: HTMLElement };
type CollectionState = { members: string[]; selected: string; failed: Set<string>;
  status: "loading" | "ready" | "fallback"; fallbackFailed: boolean };
type ArtContext = { enabled: boolean; id: string; collections: Map<string, CollectionState>;
  open: (occurrence: string, images: ImageDescriptor[], key: string, opener: HTMLElement) => void;
  update: (occurrence: string, images: ImageDescriptor[]) => void;
  retain: (occurrence: string) => void; remove: (occurrence: string) => void };
const MarkdownArtContext = createContext<ArtContext | null>(null);
const SourceOffsetContext = createContext({ offset: 0, documentKey: "" });

/** Block-local parser offsets become document positions, including when a late
 * reference definition replaces the split parse with a single whole parse. */
export function MarkdownArtOffset({ offset, documentKey = "", children }: { offset: number; documentKey?: string; children: ReactNode }) {
  const value = useMemo(() => ({ offset, documentKey }), [offset, documentKey]);
  return <SourceOffsetContext.Provider value={value}>{children}</SourceOffsetContext.Provider>;
}
export const useMarkdownArtOffset = () => useContext(SourceOffsetContext);

/** Ownership is outside rendered image nodes and stream blocks. A true context
 * change retires the viewer; updating prose leaves the same owner mounted. */
export function MarkdownArtProvider({ enabled, contextKey, children }: { enabled: boolean; contextKey: string; children: ReactNode }) {
  return <ArtOwner key={contextKey} enabled={enabled}>{children}</ArtOwner>;
}
function ArtOwner({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const id = useId(), documentRegion = useRef<HTMLDivElement>(null);
  const navigation = useArtNavigation(), lastLocation = useRef(navigation.locationKey);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const collections = useRef(new Map<string, CollectionState>());
  const occurrences = useRef(new Map<string, number>()), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const close = useCallback((reason?: ImageViewerCloseReason) => {
    setSelection(null); setNotice(reason === "unavailable" ? REMOVED_IMAGE_NOTICE : null);
  }, []);
  const open = useCallback((occurrence: string, images: ImageDescriptor[], key: string, opener: HTMLElement) => {
    if (!enabled || !images.some((image) => image.key === key)) return;
    navigation.requestArtTransition(() => {
      setNotice(null); setSelection({ occurrence, images, initialKey: key, opener });
    });
  }, [enabled, navigation]);
  const update = useCallback((occurrence: string, images: ImageDescriptor[]) => {
    setSelection((old) => old?.occurrence === occurrence ? { ...old, images } : old);
  }, []);
  const retain = useCallback((occurrence: string) => {
    occurrences.current.set(occurrence, (occurrences.current.get(occurrence) ?? 0) + 1);
  }, []);
  const remove = useCallback((occurrence: string) => {
    occurrences.current.set(occurrence, (occurrences.current.get(occurrence) ?? 1) - 1);
    // A paragraph becoming a heading unmounts/remounts an img in one commit.
    // Retire only if its source occurrence was not registered again by the end
    // of that commit; this also preserves collection choice and failed members.
    queueMicrotask(() => {
      if (!mounted.current || occurrences.current.get(occurrence)) return;
      occurrences.current.delete(occurrence); collections.current.delete(occurrence);
      setSelection((old) => old?.occurrence === occurrence ? { ...old, images: [] } : old);
    });
  }, []);
  const context = useMemo(() => ({ enabled, id, collections: collections.current, open, update, retain, remove }),
    [enabled, id, open, update, retain, remove]);
  useEffect(() => { if (!enabled) close(); }, [enabled, close]);
  useEffect(() => {
    if (lastLocation.current === navigation.locationKey) return;
    lastLocation.current = navigation.locationKey;
    // Equivalent URLs can resolve to the same document context. Close only the
    // viewer after accepted navigation; inline collection choices stay intact.
    close();
  }, [navigation.locationKey, close]);
  const fallbackFocus = selection && Array.from(document.querySelectorAll<HTMLButtonElement>("[data-markdown-occurrence]"))
    .find((node) => node.dataset.markdownOccurrence === selection.occurrence);
  return <MarkdownArtContext.Provider value={context}>
    {enabled ? <div ref={documentRegion} role="region" aria-label="Art document" tabIndex={-1}>{children}</div> : children}
    {notice && <p role="status">{notice}</p>}
    {enabled && selection && <ImageViewer images={selection.images} initialKey={selection.initialKey}
      opener={selection.opener} fallbackFocus={fallbackFocus || documentRegion.current} onClose={close} />}
  </MarkdownArtContext.Provider>;
}
export const useMarkdownArt = () => useContext(MarkdownArtContext);

/** Collections retain committed state across structural renderer remounts.
 * Greeting opt-outs still use only their original local component lifetime. */
export function useMarkdownCollectionState(occurrence: string) {
  const art = useMarkdownArt();
  const [state, setState] = useState<CollectionState>(() => art?.enabled && art.collections.get(occurrence) || {
    members: [], selected: "", failed: new Set(), status: "loading", fallbackFailed: false,
  });
  useEffect(() => { if (art?.enabled) art.collections.set(occurrence, state); }, [art, occurrence, state]);
  return [state, setState] as const;
}

/** Occurrence identity and byte/member metadata have different lifetimes.
 * Updating available members must not unregister the occurrence and close it. */
export function useMarkdownArtOccurrence(occurrence: string, images: ImageDescriptor[]) {
  const art = useMarkdownArt();
  useEffect(() => { if (art?.enabled) art.update(occurrence, images); }, [art, occurrence, images]);
  useEffect(() => {
    if (!art?.enabled) return;
    art.retain(occurrence);
    return () => art.remove(occurrence);
  }, [art, occurrence]);
  return art?.enabled ? (key: string, opener: HTMLElement) => art.open(occurrence, images, key, opener) : null;
}
