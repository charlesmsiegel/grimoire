import { createPortal } from "react-dom";
import type { ExtraProps } from "react-markdown";
import { localImageDownloadUrl } from "../components/art/recordArtAdapter";
import type { ImageDescriptor } from "../components/art/types";
import { useMarkdownArt, useMarkdownArtOccurrence, useMarkdownArtOffset, useMarkdownCollectionState } from "./MarkdownArtProvider";
import { createContext, useContext, useEffect, useId, useMemo, useState, type ComponentPropsWithoutRef, type ReactNode } from "react";

/** An occurrence owns its selection; the manifest owns only the alternatives.
 * Keeping the renderer and extras context stable lets a streamed paragraph
 * become a closed block without remounting its image or choosing again. */
export const ImageExtrasContext = createContext<((src: string) => ReactNode) | undefined>(undefined);
const COLLECTION = /^\/api\/worlds\/([^/]+)\/image-collections\/([0-9a-f]{32})\/image$/;
type ImageProps = ComponentPropsWithoutRef<"img"> & ExtraProps;

// Linked image controls are portaled beside the anchor. Their React owner
// remains the image occurrence, while the DOM has no nested interactive nodes.
const LinkedControlsContext = createContext<HTMLElement | null | undefined>(undefined);
function LinkedControls({ children }: { children: ReactNode }) {
  const host = useContext(LinkedControlsContext);
  return host === undefined ? children : host ? createPortal(children, host) : null;
}
export function MarkdownImageLink({ node: _node, children, ...props }:
  ComponentPropsWithoutRef<"a"> & { node?: unknown }) {
  const art = useMarkdownArt(), [host, setHost] = useState<HTMLSpanElement | null>(null);
  if (!art?.enabled) return <a {...props}>{children}</a>;
  return <span className="markdown-image-link"><LinkedControlsContext.Provider value={host}>
    <a {...props}>{children}</a>
  </LinkedControlsContext.Provider><span ref={setHost} /></span>;
}
function supported(src: string): boolean {
  if (!src) return false;
  try { return ["http:", "https:"].includes(new URL(src, window.location.origin).protocol); }
  catch { return false; }
}
function descriptor(src: string, alt: string, occurrence: string, member: string | number = "image"): ImageDescriptor {
  return { key: JSON.stringify([occurrence, member]), version: src, originalUrl: src, thumbnailUrl: src,
    label: alt || "Inline image", alt: alt || "Inline image", role: "inline", downloadUrl: localImageDownloadUrl(src) };
}
function Enlarge({ label, occurrence, onOpen }: { label: string; occurrence: string; onOpen: (opener: HTMLElement) => void }) {
  return <LinkedControls><button type="button" className="markdown-art-enlarge" data-markdown-occurrence={occurrence} aria-label={"Enlarge " + (label || "image")}
    onClick={(event) => { event.preventDefault(); event.stopPropagation(); onOpen(event.currentTarget); }}>Enlarge</button></LinkedControls>;
}

function collectionPath(src: string): RegExpMatchArray | null {
  try {
    const url = new URL(src, window.location.origin);
    return url.origin === window.location.origin ? url.pathname.match(COLLECTION) : null;
  } catch { return null; }
}

/** A member is checked against the shape its collection's format promises:
 * format 1 names a library file, format 2 an index into this very collection.
 * Anything else throws and the viewer falls back to `/image`. */
function localMembers(raw: unknown, world: string, id: string): string[] {
  if (!raw || typeof raw !== "object") throw new Error("Invalid image collection");
  const data = raw as { format?: unknown; id?: unknown; members?: unknown };
  if ((data.format !== 1 && data.format !== 2) || !Array.isArray(data.members)
      || !data.members.length || data.members.length > 10000) {
    throw new Error("Invalid image collection");
  }
  // Format 1 names its collection in the body; format 2 carries it too, but an absent one is
  // tolerated since the URL it was fetched from already names the collection.
  if (data.format === 1 ? data.id !== id : data.id !== undefined && data.id !== id) {
    throw new Error("Invalid image collection");
  }
  const format = data.format;
  const libraryPrefix = `/api/worlds/${world}/images/collection-image-`;
  const indexPrefix = `/api/worlds/${world}/image-collections/${id}/members/`;
  const members = data.members.map((member: unknown) => {
    if (typeof member !== "string") throw new Error("Invalid image collection member");
    const url = new URL(member, window.location.origin);
    const valid = url.origin === window.location.origin && (format === 1
      ? url.pathname.startsWith(libraryPrefix) && /^[0-9a-f]{64}$/.test(url.pathname.slice(libraryPrefix.length))
      : url.pathname.startsWith(indexPrefix) && /^(0|[1-9]\d*)$/.test(url.pathname.slice(indexPrefix.length))
        && /^(\?v=.*)?$/.test(url.search) && !url.hash);
    if (!valid) throw new Error("Invalid image collection member");
    return member;
  });
  if (new Set(members).size !== members.length) throw new Error("Duplicate image collection member");
  return members;
}

function CollectionImage({ src, alt = "", extras, occurrence, ...props }:
    ImageProps & { src: string; extras?: (src: string) => ReactNode; occurrence: string }) {
  const [collection, setCollection] = useMarkdownCollectionState(occurrence);
  const { members, selected, failed, status, fallbackFailed } = collection;
  const setSelected = (selected: string) => setCollection((old) => ({ ...old, selected }));
  useEffect(() => {
    if (status !== "loading") return;
    const match = collectionPath(src)!;
    const controller = new AbortController();
    let cancelled = false;
    const path = match[0].slice(0, -"/image".length);
    void fetch(path, { signal: controller.signal, cache: "no-cache" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Image collection unavailable");
        return localMembers(await response.json(), match[1], match[2]);
      })
      .then((items) => {
        if (cancelled) return;
        setCollection((old) => ({ ...old, members: items,
          selected: items[Math.floor(Math.random() * items.length)], status: "ready" }));
      })
      .catch(() => { if (!cancelled) setCollection((old) => ({ ...old, status: "fallback" })); });
    return () => { cancelled = true; controller.abort(); };
  }, [src, status, setCollection]);

  const usable = members.filter((member) => !failed.has(member));
  const current = usable.includes(selected) ? selected : usable[0];
  const index = usable.indexOf(current);
  function move(offset: number) {
    setSelected(usable[(index + offset + usable.length) % usable.length]);
  }
  function reroll() {
    // Uniform among the other members: reroll should visibly do something.
    move(1 + Math.floor(Math.random() * (usable.length - 1)));
  }

  const images = useMemo(() => status === "loading" ? [] : status === "fallback"
    ? fallbackFailed ? [] : [descriptor(src, alt, occurrence, "fallback")]
    : members.filter((member) => !failed.has(member)).map((member) => {
      // Format 2 uses canonical URL indices, which may have gaps. Format 1
      // retains the original manifest index. Failure never renumbers either.
      const canonical = member.match(/\/members\/(\d+)(?:\?|$)/)?.[1] ?? members.indexOf(member);
      return { ...descriptor(member, alt, occurrence, canonical), label: (alt || "Collection") + " / Image " + (Number(canonical) + 1) };
    }), [status, fallbackFailed, src, alt, occurrence, members, failed]);
  const open = useMarkdownArtOccurrence(occurrence, images);
  const currentKey = images.find((image) => image.originalUrl === current)?.key ?? images[0]?.key;
  if (status === "loading") return <span className="img-block image-collection" role="status">Loading images…</span>;
  if (status === "fallback" || !current) {
    return fallbackFailed || status === "ready"
      ? <span className="img-block image-collection"><span role="img" aria-label={alt || "Image unavailable"}>{alt}</span><span>Image collection unavailable</span></span>
      : <span className="img-block image-collection"><img {...props} src={src} alt={alt} onError={() => setCollection((old) => ({ ...old, fallbackFailed: true }))} />
          {open && currentKey && <Enlarge label={alt} occurrence={occurrence} onOpen={(opener) => open(currentKey, opener)} />}</span>;
  }
  return (
    <span className="img-block image-collection">
      <img {...props} src={current} alt={alt} onError={() => setCollection((old) => ({ ...old, failed: new Set([...old.failed, current]) }))} />
      <LinkedControls><span className="image-collection-controls">
        <span aria-live="polite">Image {index + 1} of {usable.length}</span>
        {usable.length > 1 && <>
          <button type="button" aria-label="Previous image" onClick={(event) => { event.preventDefault(); event.stopPropagation(); move(-1); }}>Previous</button>
          <button type="button" aria-label="Next image" onClick={(event) => { event.preventDefault(); event.stopPropagation(); move(1); }}>Next</button>
          <button type="button" aria-label="Reroll image" onClick={(event) => { event.preventDefault(); event.stopPropagation(); reroll(); }}>Reroll</button>
        </>}
      </span></LinkedControls>
      {open && currentKey && <Enlarge label={alt} occurrence={occurrence} onOpen={(opener) => open(currentKey, opener)} />}
      {extras && <span className="img-extras">{extras(current)}</span>}
    </span>
  );
}

export function MarkdownImage({ src = "", alt = "", node, ...props }: ImageProps) {
  const extras = useContext(ImageExtrasContext), fallbackId = useId(), art = useMarkdownArt();
  const { offset, documentKey } = useMarkdownArtOffset();
  // End positions/parents change as Markdown grows. The opening position and
  // source identify this occurrence across reparsing, independently of its DOM.
  const start = node?.position?.start.offset;
  const occurrence = JSON.stringify([art?.id ?? fallbackId, documentKey,
    typeof start === "number" ? offset + start : fallbackId, src]);
  if (collectionPath(src)) return <CollectionImage key={src} {...props} src={src} alt={alt} extras={extras} occurrence={occurrence} />;
  return <PlainImage {...props} src={src} alt={alt} extras={extras} occurrence={occurrence} />;
}
function PlainImage({ src, alt, extras, occurrence, ...props }:
  ImageProps & { src: string; alt: string; extras?: (src: string) => ReactNode; occurrence: string }) {
  const images = useMemo(() => supported(src) ? [descriptor(src, alt, occurrence)] : [], [src, alt, occurrence]);
  const open = useMarkdownArtOccurrence(occurrence, images);
  const image = <img {...props} src={src} alt={alt} />;
  return extras || open && images.length ? <span className="img-block">{image}
    {open && images[0] && <Enlarge label={alt} occurrence={occurrence} onOpen={(opener) => open(images[0].key, opener)} />}
    {extras && <span className="img-extras">{extras(src)}</span>}</span> : image;
}

export const markdownImageComponents = { img: MarkdownImage, a: MarkdownImageLink };
