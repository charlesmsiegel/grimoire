import { useEffect, useRef, useState, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { useHotkeys, type Hotkey } from "../../shortcuts/useHotkeys";
import { ImageUsage } from "../ImageUsage";
import { CHANGED_NOT_REMOVED, REMOVE_FAILED } from "./removal";
import { useArtModal } from "./useArtModal";
import { clampPan, fitScale, type Pan, type Size } from "./viewerGeometry";
import type { ImageDescriptor, ImageViewerCloseReason, RemoveOutcome } from "./types";

export type ImageViewerProps = {
  images: ImageDescriptor[]; initialKey: string; opener?: HTMLElement | null;
  fallbackFocus?: HTMLElement | null; onSelectionChange?: (key: string) => void; onClose: (reason?: ImageViewerCloseReason) => void; onOpenRecord?: (destination: string) => void;
  /** Removal is the opening surface's capability, never the viewer's: drawn
   *  only with this prop AND a descriptor carrying `removal`. Resolves once the
   *  request settles; rejects with an Error whose message is shown. */
  onRemove?: (key: string) => Promise<RemoveOutcome>;
};

/** The picture a confirmed removal is about, held until the request settles.
 *  A refresh may drop it from `images` before then; the viewer keeps drawing
 *  it -- its keys, its focus and its "Removing…" -- rather than reading that
 *  as the picture vanishing under it. */
type Removing = { key: string; index: number; image: ImageDescriptor };

/** One modal scope owns its keys. A portal makes all underlying drawers inert
 *  without making the viewer itself a descendant of an inert application. */
export function ImageViewer({ images, initialKey, opener, fallbackFocus, onClose, onOpenRecord, onSelectionChange, onRemove }: ImageViewerProps) {
  const [key, setKey] = useState(initialKey);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Removing | null>(null);
  const pending = removing !== null;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  // Read after an await: the selection a settled removal moves to is chosen
  // from the list as it is THEN, whichever order the refresh landed in.
  const latest = useRef(images);
  latest.current = images;
  const refocus = useRef(false);
  const closeButton = useRef<HTMLButtonElement>(null);
  const { portal, root, trap } = useArtModal(opener, fallbackFocus, 'button[aria-label="Close image viewer"]');
  const index = images.findIndex((image) => image.key === key);
  const listed = images[index];
  const image = removing?.image ?? listed;
  // While the removed key is pending it has not become unavailable: it is the
  // one the reader asked to go, and the move after it settles is ours.
  useEffect(() => { if (!listed && removing?.key !== key) onClose("unavailable"); }, [listed, removing, key, onClose]);
  const selectedKey = listed?.key;
  useEffect(() => { if (selectedKey) onSelectionChange?.(selectedKey); }, [selectedKey, onSelectionChange]);
  // After a settled removal swaps the image out, the control that held focus
  // may have left the document with it.
  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    const active = document.activeElement;
    if (!active?.isConnected || !root.current?.contains(active)) closeButton.current?.focus();
  });
  const position = index >= 0 ? index : removing ? removing.index : -1;
  const count = index < 0 && removing ? images.length + 1 : images.length;
  const still = !confirming && !pending;
  const canRemove = !!onRemove && !!image?.removal;
  function select(next: string) { setKey(next); setConfirming(false); setMessage(null); }
  function step(delta: number) { const next = images[index + delta]; if (next && still) select(next.key); }
  function ask() { if (canRemove && still) { setMessage(null); setConfirming(true); } }
  function cancel() { if (!pending) setConfirming(false); }
  async function confirm() {
    if (!onRemove || !listed?.removal || pending) return;
    const target: Removing = { key: listed.key, index, image: listed };
    setRemoving(target);
    let outcome: RemoveOutcome;
    try {
      outcome = await onRemove(target.key);
    } catch (error) {
      if (!mounted.current) return;
      refocus.current = true;
      setRemoving(null); setConfirming(false);
      setMessage(error instanceof Error && error.message ? error.message : REMOVE_FAILED);
      return;
    }
    if (!mounted.current) return;
    const now = latest.current;
    const at = now.findIndex((candidate) => candidate.key === target.key);
    refocus.current = true;
    if (outcome === "changed" && at >= 0) {
      setRemoving(null); setConfirming(false); setMessage(CHANGED_NOT_REMOVED);
      return;
    }
    const next = at >= 0 ? now[at + 1] ?? now[at - 1] : now[Math.min(target.index, now.length - 1)];
    // The only picture went: a plain close. `removing` stays set, so a refresh
    // that lands before the surface unmounts us is not read as "unavailable".
    if (!next) { onClose(); return; }
    select(next.key);
    setRemoving(null);
  }
  const bindings: Hotkey[] = [
    { keys: "escape", label: "Close image viewer", group: "IMAGE VIEWER", whileTyping: true, enabled: !confirming, run: onClose },
    { keys: "escape", label: "Cancel removal", group: "IMAGE VIEWER", enabled: confirming && !pending, run: cancel },
    { keys: "arrowleft", label: "Previous image", group: "IMAGE VIEWER", enabled: still && index > 0, run: () => step(-1) },
    { keys: "arrowright", label: "Next image", group: "IMAGE VIEWER", enabled: still && index >= 0 && index < images.length - 1, run: () => step(1) },
    // A Mac's Delete key sends Backspace. Never while typing: there is no
    // field in the viewer, and one that ever appears keeps its own Backspace.
    { keys: "delete", label: "Remove image", group: "IMAGE VIEWER", enabled: canRemove && still, run: ask },
    { keys: "backspace", label: "Remove image", group: "IMAGE VIEWER", enabled: canRemove && still, run: ask },
    { keys: "tab", whileTyping: true, run: () => trap(false) },
    { keys: "shift+tab", whileTyping: true, run: () => trap(true) },
  ];
  return createPortal(<div ref={root} className="art-viewer" role="dialog" aria-modal="true" aria-label="Image viewer">
    <header className="art-viewer-header">
      <strong>{image?.recordLabel ? image.recordLabel + " / " + (image.role === "primary" ? image.primaryLabel ?? image.label : image.label) : image?.label}</strong>
      {count > 1 && <div className="art-viewer-steps">
        <button aria-label="Previous image" disabled={!still || position <= 0} onClick={() => step(-1)}>Previous</button>
        <span role="status" aria-label="Image position" aria-live="polite" aria-atomic="true">{position + 1} / {count}</span>
        <button aria-label="Next image" disabled={!still || index < 0 || index >= images.length - 1} onClick={() => step(1)}>Next</button>
      </div>}
      <button ref={closeButton} aria-label="Close image viewer" onClick={() => onClose()}>Close</button>
    </header>
    {image && <ActiveImage key={`${image.key}/${image.version}`} image={image} bindings={bindings} onOpenRecord={onOpenRecord}
      removal={{ available: canRemove, confirming, pending, message, onAsk: ask, onConfirm: () => void confirm(), onCancel: cancel }} />}
  </div>, portal);
}

type RemovalControls = {
  available: boolean; confirming: boolean; pending: boolean; message: string | null;
  onAsk: () => void; onConfirm: () => void; onCancel: () => void;
};

type View = { scale: number; pan: Pan; fit: boolean };
const midpoint = (points: Pan[]): Pan => ({ x: points.reduce((n, p) => n + p.x, 0) / points.length,
  y: points.reduce((n, p) => n + p.y, 0) / points.length });
const distance = (points: Pan[]) => points.length < 2 ? 0 : Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);

/** The keyed child is a load generation: a late load/error from an old image
 *  cannot reset the next image. Retry also keys the img, retaining its URL. */
function ActiveImage({ image, bindings, onOpenRecord, removal }: {
  image: ImageDescriptor; bindings: Hotkey[]; onOpenRecord?: (destination: string) => void; removal: RemovalControls;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const { confirming, pending } = removal;
  const askButton = useRef<HTMLButtonElement>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const confirmation = useRef<HTMLDivElement>(null);
  const wasConfirming = useRef(false);
  // Opening puts focus on the confirming Remove, so Enter -- the button's own
  // activation, never a hotkey -- confirms. Closing without a move hands it
  // back to the Remove that opened it, if the focused control went with it.
  useEffect(() => {
    if (confirming && !wasConfirming.current) confirmButton.current?.focus();
    else if (!confirming && wasConfirming.current) {
      const active = document.activeElement;
      if (!active?.isConnected || active === document.body) askButton.current?.focus();
    }
    wasConfirming.current = confirming;
  }, [confirming]);
  // Every control but Close is disabled while pending, so focus waits on the
  // confirmation itself rather than on a disabled button a browser may blur.
  useEffect(() => { if (pending) confirmation.current?.focus(); }, [pending]);
  const [natural, setNatural] = useState<Size>({ width: image.width || 1, height: image.height || 1 });
  const [viewport, setViewport] = useState<Size>({ width: Math.max(1, window.innerWidth - 48), height: Math.max(1, window.innerHeight - 220) });
  const fit = fitScale(natural, viewport);
  const [view, setView] = useState<View>(() => ({ scale: fit, pan: { x: 0, y: 0 }, fit: true }));
  const [load, setLoad] = useState<"loading" | "ready" | "failed">("loading");
  const [retry, setRetry] = useState(0);
  const pointers = useRef(new Map<number, Pan>());
  const gesture = useRef<{ points: Pan[]; view: View } | null>(null);
  const scale = view.fit ? fit : Math.max(fit, Math.min(4, view.scale));
  useEffect(() => {
    setView(previous => {
      if (previous.fit) return previous;
      const scale = Math.max(fit, Math.min(4, previous.scale));
      const pan = clampPan(natural, viewport, scale, previous.pan);
      return scale === previous.scale && pan.x === previous.pan.x && pan.y === previous.pan.y
        ? previous : { ...previous, scale, pan };
    });
  }, [fit, natural, viewport]);
  const pan = clampPan(natural, viewport, scale, view.pan);
  function zoom(next: number) {
    const bounded = Math.max(fit, Math.min(4, next));
    setView({ scale: bounded, pan: clampPan(natural, viewport, bounded, pan), fit: false });
  }
  function reset() { setView({ scale: fit, pan: { x: 0, y: 0 }, fit: true }); }
  // All bindings share one modal scope, so Escape cannot reach a drawer.
  useHotkeys([...bindings,
    { keys: "f", label: "Fit image", group: "IMAGE VIEWER", enabled: !pending, run: reset },
    { keys: "1", label: "Image at 100%", group: "IMAGE VIEWER", enabled: !pending, run: () => zoom(1) },
    { keys: "+", label: "Zoom in", group: "IMAGE VIEWER", enabled: !pending && scale < 4, run: () => zoom(scale * 1.25) },
    { keys: "=", label: "Zoom in", group: "IMAGE VIEWER", enabled: !pending && scale < 4, run: () => zoom(scale * 1.25) },
    { keys: "-", label: "Zoom out", group: "IMAGE VIEWER", enabled: !pending && scale > fit, run: () => zoom(scale / 1.25) },
  ], { modal: true });
  const zoomActions = useRef({ zoom, reset, scale, fit });
  zoomActions.current = { zoom, reset, scale, fit };
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) setViewport({ width: rect.width, height: rect.height });
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element); window.addEventListener("resize", measure);
    // A nonpassive wheel listener is needed for WebView/browser pinch zoom;
    // React's delegated wheel listener cannot prevent the page gesture.
    const wheel = (event: WheelEvent) => {
      event.preventDefault(); const current = zoomActions.current;
      current.zoom(current.scale * Math.exp(-event.deltaY * .002));
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); element.removeEventListener("wheel", wheel); };
  }, []);
  function start(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    gesture.current = { points: Array.from(pointers.current.values()), view: { scale, pan, fit: false } };
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const before = gesture.current;
    if (!before || !pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = Array.from(pointers.current.values());
    const origin = midpoint(before.points), now = midpoint(points);
    const oldDistance = distance(before.points);
    const nextScale = Math.max(fit, Math.min(4, oldDistance > 0 ? before.view.scale * distance(points) / oldDistance : before.view.scale));
    const rect = stage.current?.getBoundingClientRect();
    const center = { x: (rect?.left ?? 0) + viewport.width / 2, y: (rect?.top ?? 0) + viewport.height / 2 };
    const factor = nextScale / before.view.scale;
    const nextPan = { x: before.view.pan.x * factor + now.x - center.x - (origin.x - center.x) * factor,
      y: before.view.pan.y * factor + now.y - center.y - (origin.y - center.y) * factor };
    setView({ scale: nextScale, pan: clampPan(natural, viewport, nextScale, nextPan), fit: false });
  }
  function end(event: PointerEvent<HTMLDivElement>) {
    pointers.current.delete(event.pointerId);
    gesture.current = pointers.current.size ? { points: Array.from(pointers.current.values()), view: { scale, pan, fit: false } } : null;
  }
  return <>
    <div className="art-viewer-tools">
      <button disabled={pending} onClick={reset}>Fit</button><button disabled={pending} onClick={() => zoom(1)}>100%</button>
      <button aria-label="Zoom out" disabled={pending || scale <= fit} onClick={() => zoom(scale / 1.25)}>−</button>
      <output aria-label="Zoom">{Math.round(scale * 100)}%</output>
      <button aria-label="Zoom in" disabled={pending || scale >= 4} onClick={() => zoom(scale * 1.25)}>+</button>
      <a href={image.originalUrl} target="_blank" rel="noreferrer">Open original</a>
      {image.downloadUrl && <a href={image.downloadUrl} download>Download original</a>}
      {image.recordDestination && onOpenRecord && <button disabled={pending} onClick={() => onOpenRecord(image.recordDestination!)}>{image.destinationLabel ?? "Open record"}</button>}
      {removal.available && (confirming
        ? <div ref={confirmation} role="group" aria-label="Confirm removal" className="art-viewer-confirm" tabIndex={-1}>
          <span aria-live="polite">{pending ? "Removing…" : image.removal?.question}</span>
          <button ref={confirmButton} disabled={pending} onClick={removal.onConfirm}>Remove</button>
          <button disabled={pending} onClick={removal.onCancel}>Cancel</button>
        </div>
        : <button ref={askButton} onClick={removal.onAsk}>Remove</button>)}
    </div>
    {removal.message && <p role="alert" className="art-viewer-message">{removal.message}</p>}
    <div ref={stage} className="art-viewer-stage" data-testid="viewer-stage" onPointerDown={start}
      onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
      {/* Absolute centering keeps natural pixels out of grid track sizing.
          Scaling a natural-size grid item alone centers an oversized track. */}
      <img key={retry} data-testid="viewer-image" data-scale={scale} data-pan={`${pan.x},${pan.y}`}
        src={image.originalUrl} alt={image.alt} draggable={false} width={natural.width} height={natural.height}
        style={{ position: "absolute", left: "50%", top: "50%", marginLeft: -natural.width / 2, marginTop: -natural.height / 2,
          width: natural.width, height: natural.height, transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`, visibility: load === "ready" ? "visible" : "hidden" }}
        onLoad={(event) => {
          const img = event.currentTarget;
          if (img.naturalWidth > 0 && img.naturalHeight > 0) setNatural({ width: img.naturalWidth, height: img.naturalHeight });
          // Dimensions update Fit and clamp any explicit view in the effect above.
          // A slow original or retry must retain zoom/pan chosen while it loads.
          setLoad("ready");
        }} onError={() => setLoad("failed")} />
      {load === "loading" && <p role="status">Loading original…</p>}
      {load === "failed" && <div role="alert"><p>Original unavailable</p><button onClick={() => { setLoad("loading"); setRetry((n) => n + 1); }}>Retry original</button></div>}
    </div>
    <details className="art-viewer-information"><summary>Image information</summary>
      <p>{image.descriptionAvailable === false ? "Description metadata unavailable" : image.description || (image.described ? "No description (reviewed)" : "No description")}</p>
      <p>{image.role === "primary" ? image.primaryLabel ?? "Primary image" : image.role}{image.provenance && ` · ${image.provenance.replace(/_/g, " ")}`}
        {image.width && image.height && ` · ${image.width} × ${image.height}`}{image.animated && " · Animated"}</p>
      {image.imageId && <ImageUsage imageId={image.imageId} navigable={false} />}
    </details>
  </>;
}
