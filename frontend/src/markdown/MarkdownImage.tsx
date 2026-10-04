import { createContext, useContext, useEffect, useState, type ComponentPropsWithoutRef, type ReactNode } from "react";

/** An occurrence owns its selection; the manifest owns only the alternatives.
 * Keeping the renderer and extras context stable lets a streamed paragraph
 * become a closed block without remounting its image or choosing again. */
export const ImageExtrasContext = createContext<((src: string) => ReactNode) | undefined>(undefined);
const COLLECTION = /^\/api\/worlds\/([^/]+)\/image-collections\/([0-9a-f]{32})\/image$/;
type ImageProps = ComponentPropsWithoutRef<"img"> & { node?: unknown };

function collectionPath(src: string): RegExpMatchArray | null {
  try {
    const url = new URL(src, window.location.origin);
    return url.origin === window.location.origin ? url.pathname.match(COLLECTION) : null;
  } catch { return null; }
}

function localMembers(raw: unknown, world: string, id: string): string[] {
  if (!raw || typeof raw !== "object") throw new Error("Invalid image collection");
  const data = raw as { format?: unknown; id?: unknown; members?: unknown };
  if (data.format !== 1 || data.id !== id || !Array.isArray(data.members) || !data.members.length || data.members.length > 10000) {
    throw new Error("Invalid image collection");
  }
  const prefix = `/api/worlds/${world}/images/collection-image-`;
  const members = data.members.map((member: unknown) => {
    if (typeof member !== "string") throw new Error("Invalid image collection member");
    const url = new URL(member, window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith(prefix)
        || !/^[0-9a-f]{64}$/.test(url.pathname.slice(prefix.length))) {
      throw new Error("Invalid image collection member");
    }
    return member;
  });
  if (new Set(members).size !== members.length) throw new Error("Duplicate image collection member");
  return members;
}

function CollectionImage({ src, alt = "", extras, ...props }:
    ImageProps & { src: string; extras?: (src: string) => ReactNode }) {
  const [members, setMembers] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [failed, setFailed] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<"loading" | "ready" | "fallback">("loading");
  const [fallbackFailed, setFallbackFailed] = useState(false);
  useEffect(() => {
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
        setMembers(items);
        setSelected(items[Math.floor(Math.random() * items.length)]);
        setStatus("ready");
      })
      .catch(() => { if (!cancelled) setStatus("fallback"); });
    return () => { cancelled = true; controller.abort(); };
  }, [src]);

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
  if (status === "loading") return <span className="img-block image-collection" role="status">Loading images…</span>;
  if (status === "fallback" || !current) {
    return fallbackFailed
      ? <span className="img-block image-collection"><span role="img" aria-label={alt || "Image unavailable"}>{alt}</span><span>Image collection unavailable</span></span>
      : <span className="img-block image-collection"><img {...props} src={src} alt={alt} onError={() => setFallbackFailed(true)} /></span>;
  }
  return (
    <span className="img-block image-collection">
      <img {...props} src={current} alt={alt} onError={() => setFailed((previous) => new Set([...previous, current]))} />
      <span className="image-collection-controls">
        <span aria-live="polite">Image {index + 1} of {usable.length}</span>
        {usable.length > 1 && <>
          <button type="button" aria-label="Previous image" onClick={(event) => { event.preventDefault(); event.stopPropagation(); move(-1); }}>Previous</button>
          <button type="button" aria-label="Next image" onClick={(event) => { event.preventDefault(); event.stopPropagation(); move(1); }}>Next</button>
          <button type="button" aria-label="Reroll image" onClick={(event) => { event.preventDefault(); event.stopPropagation(); reroll(); }}>Reroll</button>
        </>}
      </span>
      {extras && <span className="img-extras">{extras(current)}</span>}
    </span>
  );
}

export function MarkdownImage({ src = "", alt = "", node: _node, ...props }: ImageProps) {
  const extras = useContext(ImageExtrasContext);
  if (collectionPath(src)) return <CollectionImage key={src} {...props} src={src} alt={alt} extras={extras} />;
  const image = <img {...props} src={src} alt={alt} />;
  return extras ? <span className="img-block">{image}<span className="img-extras">{extras(src)}</span></span> : image;
}

export const markdownImageComponents = { img: MarkdownImage };
