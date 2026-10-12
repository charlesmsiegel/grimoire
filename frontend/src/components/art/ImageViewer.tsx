import { useEffect, useRef, useState, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { useHotkeys, type Hotkey } from "../../shortcuts/useHotkeys";
import { ImageUsage } from "../ImageUsage";
import { useArtModal } from "./useArtModal";
import { clampPan, fitScale, type Pan, type Size } from "./viewerGeometry";
import type { ImageDescriptor, ImageViewerCloseReason } from "./types";

export type ImageViewerProps = {
  images: ImageDescriptor[]; initialKey: string; opener?: HTMLElement | null;
  fallbackFocus?: HTMLElement | null; onSelectionChange?: (key: string) => void; onClose: (reason?: ImageViewerCloseReason) => void; onOpenRecord?: (destination: string) => void;
};
/** One modal scope owns its keys. A portal makes all underlying drawers inert
 *  without making the viewer itself a descendant of an inert application. */
export function ImageViewer({ images, initialKey, opener, fallbackFocus, onClose, onOpenRecord, onSelectionChange }: ImageViewerProps) {
  const [key, setKey] = useState(initialKey);
  const { portal, root, trap } = useArtModal(opener, fallbackFocus, 'button[aria-label="Close image viewer"]');
  const index = images.findIndex((image) => image.key === key);
  const image = images[index];
  useEffect(() => { if (!image) onClose("unavailable"); }, [image, onClose]);
  const selectedKey = image?.key;
  useEffect(() => { if (selectedKey) onSelectionChange?.(selectedKey); }, [selectedKey, onSelectionChange]);
  function step(delta: number) { const next = images[index + delta]; if (next) setKey(next.key); }
  const bindings: Hotkey[] = [
    { keys: "escape", label: "Close image viewer", group: "IMAGE VIEWER", whileTyping: true, run: onClose },
    { keys: "arrowleft", label: "Previous image", group: "IMAGE VIEWER", enabled: index > 0, run: () => step(-1) },
    { keys: "arrowright", label: "Next image", group: "IMAGE VIEWER", enabled: index >= 0 && index < images.length - 1, run: () => step(1) },
    { keys: "tab", whileTyping: true, run: () => trap(false) },
    { keys: "shift+tab", whileTyping: true, run: () => trap(true) },
  ];
  return createPortal(<div ref={root} className="art-viewer" role="dialog" aria-modal="true" aria-label="Image viewer">
    <header className="art-viewer-header">
      <strong>{image?.recordLabel ? image.recordLabel + " / " + (image.role === "primary" ? image.primaryLabel ?? image.label : image.label) : image?.label}</strong>
      {images.length > 1 && <div className="art-viewer-steps">
        <button aria-label="Previous image" disabled={index <= 0} onClick={() => step(-1)}>Previous</button>
        <span role="status" aria-label="Image position" aria-live="polite" aria-atomic="true">{index + 1} / {images.length}</span>
        <button aria-label="Next image" disabled={index >= images.length - 1} onClick={() => step(1)}>Next</button>
      </div>}
      <button aria-label="Close image viewer" onClick={() => onClose()}>Close</button>
    </header>
    {image && <ActiveImage key={`${image.key}/${image.version}`} image={image} bindings={bindings} onOpenRecord={onOpenRecord} />}
  </div>, portal);
}

type View = { scale: number; pan: Pan; fit: boolean };
const midpoint = (points: Pan[]): Pan => ({ x: points.reduce((n, p) => n + p.x, 0) / points.length,
  y: points.reduce((n, p) => n + p.y, 0) / points.length });
const distance = (points: Pan[]) => points.length < 2 ? 0 : Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);

/** The keyed child is a load generation: a late load/error from an old image
 *  cannot reset the next image. Retry also keys the img, retaining its URL. */
function ActiveImage({ image, bindings, onOpenRecord }: { image: ImageDescriptor; bindings: Hotkey[]; onOpenRecord?: (destination: string) => void }) {
  const stage = useRef<HTMLDivElement>(null);
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
    { keys: "f", label: "Fit image", group: "IMAGE VIEWER", run: reset },
    { keys: "1", label: "Image at 100%", group: "IMAGE VIEWER", run: () => zoom(1) },
    { keys: "+", label: "Zoom in", group: "IMAGE VIEWER", enabled: scale < 4, run: () => zoom(scale * 1.25) },
    { keys: "=", label: "Zoom in", group: "IMAGE VIEWER", enabled: scale < 4, run: () => zoom(scale * 1.25) },
    { keys: "-", label: "Zoom out", group: "IMAGE VIEWER", enabled: scale > fit, run: () => zoom(scale / 1.25) },
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
      <button onClick={reset}>Fit</button><button onClick={() => zoom(1)}>100%</button>
      <button aria-label="Zoom out" disabled={scale <= fit} onClick={() => zoom(scale / 1.25)}>−</button>
      <output aria-label="Zoom">{Math.round(scale * 100)}%</output>
      <button aria-label="Zoom in" disabled={scale >= 4} onClick={() => zoom(scale * 1.25)}>+</button>
      <a href={image.originalUrl} target="_blank" rel="noreferrer">Open original</a>
      {image.downloadUrl && <a href={image.downloadUrl} download>Download original</a>}
      {image.recordDestination && onOpenRecord && <button onClick={() => onOpenRecord(image.recordDestination!)}>{image.destinationLabel ?? "Open record"}</button>}
    </div>
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
