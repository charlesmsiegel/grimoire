import { useState } from "react";
import type { ImageDescriptor } from "./types";

export function ImageTile({ image, onOpen, className = "", selected, disabled }: { image: ImageDescriptor; onOpen: (opener: HTMLElement) => void; className?: string; selected?: boolean; disabled?: boolean }) {
  // A replaced thumbnail gets another attempt without losing its occurrence.
  const [failed, setFailed] = useState<string | null>(null);
  return <button type="button" className={"art-image-tile " + className} disabled={disabled} aria-pressed={selected} aria-label={`View ${image.label}`}
    onClick={(event) => onOpen(event.currentTarget)}>
    <span className="art-image-tile-preview">
      {failed === image.thumbnailUrl ? <span>Preview unavailable</span> :
        <img src={image.thumbnailUrl} alt={image.alt} loading="lazy" decoding="async"
          onError={() => setFailed(image.thumbnailUrl)} />}
    </span>
    <span className="art-image-tile-label">{image.label}</span>
    {image.role === "primary" && <span className="chip">{image.primaryLabel ?? "Primary"}</span>}
    {image.provenance && <span className="chip">{image.provenance.replace(/_/g, " ")}</span>}
  </button>;
}
