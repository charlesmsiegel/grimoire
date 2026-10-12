import { useNavigate } from "react-router-dom";
import { MarkdownArtProvider } from "../markdown/MarkdownArtProvider";
import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { markdownImageComponents } from "../markdown/MarkdownImage";
import remarkGfm from "remark-gfm";
import { api, type CastDetail, type CastSource } from "../api/client";
import { onArtChanged } from "../appEvents";
import type { RecordArtImage, RecordArtTarget } from "../api/types";
import { ImageViewer } from "./art/ImageViewer";
import { toImageDescriptor } from "./art/recordArtAdapter";
import { useImageViewer } from "./art/useImageViewer";
import { useHotkeys } from "../shortcuts/useHotkeys";

export type DrawerTarget =
  | { type: "actor"; kind: "characters" | "pcs"; id: string }
  | { type: "location"; id: string };

/** The three provenance badges (#99), and what each one is claiming. The
 *  wording is the reason a reader would care: "library" is the promise that
 *  nothing here has been touched, "override" withdraws it, and "emergent"
 *  says there is no library record to compare against in the first place.
 *
 *  Keyed rather than switched so an unrecognized value — a store written by a
 *  newer build, read by an older one — renders no chip at all rather than an
 *  empty box with a border round it. */
const SOURCE: Record<CastSource, { label: string; hint: string }> = {
  library: { label: "Library", hint: "From the world library, unedited in this campaign." },
  override: { label: "Override", hint: "The library's record, with this campaign's edits on top." },
  emergent: { label: "Emergent", hint: "This campaign's own — no library record behind it." },
};

export function RecordDrawer({ cid, sid, target, onClose }:
  { cid: string; sid: string; target: DrawerTarget; onClose: () => void }) {
  const owner = JSON.stringify([cid, sid, target.type, target.type === "actor" ? target.kind : "locations", target.id]);
  return <DrawerBody key={owner} cid={cid} sid={sid} target={target} onClose={onClose} owner={owner} />;
}
function DrawerBody({ cid, sid, target, onClose, owner }:
  { cid: string; sid: string; target: DrawerTarget; onClose: () => void; owner: string }) {
  const navigate = useNavigate();
  const targetKind = target.type === "actor" ? target.kind : null, targetId = target.id;
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [images, setImages] = useState<RecordArtImage[] | null>(null);
  const [artError, setArtError] = useState<string | null>(null);
  const [bodyError, setBodyError] = useState<string | null>(null);
  const reload = useRef<(() => void) | null>(null), entry = useRef<HTMLButtonElement>(null);
  const viewer = useImageViewer(owner);
  const [source, setSource] = useState<CastSource | null>(null);

  // The drawer covers the transcript, so while it is up it owns the keyboard:
  // Escape closes it, and the scene's own bindings are inert underneath. Until
  // #193 this was the one overlay in the app with no key handling at all — the
  // close button was the only way out of it, which the chooser beside it had
  // never asked of anyone.
  useHotkeys(
    [{ keys: "escape", label: "Close this record", group: "THIS PANEL",
       whileTyping: true, run: onClose }],
    { modal: true },
  );

  useEffect(() => {
    // A new drawer owner remounts all state: neither a stale badge nor a late
    // portrait can stand under its successor's title (#99). Art is a separate
    // read, after the scene has resolved the actor version (#219), so an
    // unavailable sidecar cannot hide the prose the reader came to inspect.
    let live = true, serial = 0;
    let abort: AbortController | null = null;
    let addressed: RecordArtTarget | null = null;
    async function readArt(fresh: boolean) {
      if (!addressed) return;
      const mine = ++serial;
      abort?.abort(); abort = new AbortController();
      try {
        const data = await api.readRecordArt(addressed, { fresh, signal: abort.signal });
        if (live && mine === serial) { setImages(data.images); setArtError(null); }
      } catch {
        if (live && mine === serial) setArtError("Art could not be read");
      }
    }
    reload.current = () => { void readArt(true); };
    const unwatch = onArtChanged(() => { void readArt(true); });
    void (async () => {
      try {
        if (targetKind) {
          const d: CastDetail = await api.getCastDetail(cid, sid, targetKind, targetId);
          if (!live) return;
          setTitle(d.name); setBody(d.body); setSource(d.source);
          addressed = { scope: { kind: "campaign", id: cid }, kind: d.kind, record_id: d.id, version: d.version };
        } else {
          const entity = await api.readEntity({ kind: "campaign", id: cid }, "locations", targetId);
          if (!live) return;
          setTitle(entity.meta.name); setBody(entity.body);
          addressed = { scope: { kind: "campaign", id: cid }, kind: "locations", record_id: targetId };
        }
      } catch {
        if (live) setBodyError("Record could not be read");
        return;
      }
      await readArt(false);
    })();
    return () => { live = false; serial += 1; abort?.abort(); unwatch(); reload.current = null; };
  }, [cid, sid, targetKind, targetId]);

  // Annotated rather than inferred: `SOURCE` is keyed by `CastSource` so that
  // adding a fourth badge is a compile error here, but the value arrives over
  // HTTP and is not checked by anything — the miss below is real at runtime
  // even though the index signature says it cannot be.
  const badge: { label: string; hint: string } | undefined = source ? SOURCE[source] : undefined;
  const preview = images?.find((image) => image.name === "avatar") ?? images?.[0];
  const descriptors = (images ?? []).map(image => toImageDescriptor(image, { name: title }));
  const openArt = (opener: HTMLElement) => { if (preview) viewer.open(descriptors, toImageDescriptor(preview).key, opener); };
  return (<>
    {viewer.notice && <p role="status">{viewer.notice}</p>}
    {viewer.viewerProps && <ImageViewer {...viewer.viewerProps} images={descriptors} fallbackFocus={entry.current} onOpenRecord={destination => { viewer.close(); navigate(destination); }} />}
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        <h3>{title}</h3>
        {badge && (
          <span className={`role-chip cast-source ${source}`} title={badge.hint}>{badge.label}</span>
        )}
        {preview && <button className="drawer-art-preview" aria-label={"View " + title + " art"}
          onClick={(event) => openArt(event.currentTarget)}>
          <img className="drawer-avatar" alt={title + (target.type === "actor" ? " avatar" : " art preview")}
            src={preview.thumb} loading="lazy" decoding="async" />
        </button>}
        {artError ? <p role="alert">{artError} <button onClick={() => reload.current?.()}>Retry art</button></p>
          : images === null && !bodyError ? <p role="status">Loading art…</p>
          : images?.length === 0 ? <p>No art yet</p> : null}
        <button ref={entry} disabled={!preview} onClick={(event) => openArt(event.currentTarget)}>View art</button>
        {bodyError && <p role="alert">{bodyError}</p>}
        <div className="detail-rendered"><MarkdownArtProvider enabled contextKey={owner}><Markdown components={markdownImageComponents} remarkPlugins={[remarkGfm]}>{body}</Markdown></MarkdownArtProvider></div>
      </aside>
    </div>
  </>);
}
