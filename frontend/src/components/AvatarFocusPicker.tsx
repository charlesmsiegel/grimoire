import { useRef, useState, type ReactNode, type CSSProperties, type PointerEvent, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { focusWindow } from "./art/viewerGeometry";
import { useArtModal } from "./art/useArtModal";

/** Focus is 0-100 along the axis overflowing the target crop. One scalar
 *  still suffices for square actor previews and 16:10 entity previews;
 *  choosing the crop never modifies the retained image's pixels. */
export function AvatarFocusPicker({ src, initial, onSave, onClose, aspect = 1, label = "Avatar",
  value, onChange, busy = false, saveDisabled = false, error, recovery }: {
  src: string; initial: number; onSave: (focus: number) => void; onClose: () => void;
  aspect?: number; label?: string; value?: number; onChange?: (focus: number) => void; busy?: boolean; saveDisabled?: boolean; error?: string | null; recovery?: ReactNode;
}) {
  const [localFocus, setLocalFocus] = useState(initial);
  const [natural, setNatural] = useState({ width: 1, height: 1 });
  const focus = value ?? localFocus, vertical = natural.width / natural.height < aspect;
  const boxRef = useRef<HTMLDivElement>(null), modal = useArtModal();
  const change = (next: number) => { setLocalFocus(next); onChange?.(next); };
  useHotkeys([
    { keys: "escape", label: "Cancel the crop", group: "THIS PANEL", whileTyping: true, enabled: !busy, run: onClose },
    { keys: "tab", whileTyping: true, run: () => modal.trap(false) },
    { keys: "shift+tab", whileTyping: true, run: () => modal.trap(true) },
  ], { modal: true });
  function onLoad(event: SyntheticEvent<HTMLImageElement>) {
    const image = event.currentTarget;
    if (image.naturalWidth > 0 && image.naturalHeight > 0) setNatural({ width: image.naturalWidth, height: image.naturalHeight });
  }
  function fromPointer(event: PointerEvent) {
    if (busy) return;
    const rect = boxRef.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return;
    const frac = vertical ? (event.clientY - rect.top) / rect.height : (event.clientX - rect.left) / rect.width;
    change(Math.round(Math.max(0, Math.min(1, frac)) * 100));
  }
  const crop = focusWindow(natural, aspect, focus);
  const windowStyle: CSSProperties = { top: crop.y / natural.height * 100 + "%", left: crop.x / natural.width * 100 + "%",
    width: crop.width / natural.width * 100 + "%", height: crop.height / natural.height * 100 + "%" };
  return createPortal(<div ref={modal.root} className="tagline-modal-backdrop art-focus-backdrop" role="dialog" aria-modal="true" aria-label={"Adjust " + label.toLowerCase() + " crop"}>
    <div className="tagline-modal focus-modal">
      <h3>{label} crop</h3><p className="field-hint">Drag on the image or use the slider to choose the preview crop.</p>
      {error && <p role="alert">{error}</p>}
      {recovery}
      <div className="focus-scroll"><div className="focus-preview" ref={boxRef} onPointerDown={fromPointer}
        onPointerMove={(event) => { if (event.buttons) fromPointer(event); }}>
        <img src={src} alt="avatar full view" draggable={false} onLoad={onLoad} />
        <div className="focus-window" style={windowStyle} />
      </div></div>
      <input type="range" min={0} max={100} value={focus} aria-label="Crop position" disabled={busy}
        onChange={(event) => change(Number(event.target.value))} />
      <div className="form-actions"><button className="primary" disabled={busy || saveDisabled} onClick={() => onSave(focus)}>Save</button>
        <button className="subtle" disabled={busy} onClick={onClose}>Cancel</button></div>
    </div>
  </div>, modal.portal);
}
