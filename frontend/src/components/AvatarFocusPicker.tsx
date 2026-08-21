import { useRef, useState } from "react";
import { useHotkeys } from "../shortcuts/useHotkeys";

/** Modal for choosing which square of the avatar shows in square crops.
 *  Focus is 0-100 along the image's long axis; object-fit: cover has no
 *  slack on the short axis, so one number fully describes the crop. */
export function AvatarFocusPicker({ src, initial, onSave, onClose }:
  { src: string; initial: number; onSave: (focus: number) => void; onClose: () => void }) {
  const [focus, setFocus] = useState(initial);
  const [portrait, setPortrait] = useState(true);
  const [squareFrac, setSquareFrac] = useState(1); // short side / long side
  const boxRef = useRef<HTMLDivElement>(null);

  // Escape is Cancel: the crop is not saved until Save, so leaving by the key
  // discards exactly what leaving by the button does.
  useHotkeys(
    [{ keys: "escape", label: "Cancel the crop", group: "THIS PANEL",
       whileTyping: true, run: onClose }],
    { modal: true },
  );

  function onLoad(e: React.SyntheticEvent<HTMLImageElement>) {
    const img = e.currentTarget;
    if (img.naturalWidth > 0 && img.naturalHeight > 0) {
      setPortrait(img.naturalHeight >= img.naturalWidth);
      setSquareFrac(Math.min(img.naturalWidth, img.naturalHeight) /
                    Math.max(img.naturalWidth, img.naturalHeight));
    }
  }

  function fromPointer(e: React.PointerEvent) {
    const rect = boxRef.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return;
    const frac = portrait
      ? (e.clientY - rect.top) / rect.height
      : (e.clientX - rect.left) / rect.width;
    setFocus(Math.round(Math.max(0, Math.min(1, frac)) * 100));
  }

  // the window's leading edge sits at focus% of the long-axis slack
  const lead = (focus / 100) * (1 - squareFrac) * 100;
  const windowStyle: React.CSSProperties = portrait
    ? { top: `${lead}%`, left: 0, width: "100%", height: `${squareFrac * 100}%` }
    : { left: `${lead}%`, top: 0, height: "100%", width: `${squareFrac * 100}%` };

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Adjust avatar crop">
      <div className="tagline-modal focus-modal">
        <h3>Avatar crop</h3>
        <p className="field-hint">Drag on the image (or use the slider) to choose which square is shown.</p>
        <div className="focus-scroll">
          <div className="focus-preview" ref={boxRef}
               onPointerDown={fromPointer}
               onPointerMove={(e) => { if (e.buttons) fromPointer(e); }}>
            <img src={src} alt="avatar full view" draggable={false} onLoad={onLoad} />
            <div className="focus-window" style={windowStyle} />
          </div>
        </div>
        <input type="range" min={0} max={100} value={focus} aria-label="Crop position"
               onChange={(e) => setFocus(Number(e.target.value))} />
        <div className="form-actions">
          <button className="primary" type="button" onClick={() => onSave(focus)}>Save</button>
          <button className="subtle" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
