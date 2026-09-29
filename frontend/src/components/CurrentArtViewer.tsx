import { useEffect, useState } from "react";
import { thumbSet } from "../api/thumbs";

export type ArtImage = { name: string; url: (w?: number) => string };

/** A large reading surface for the selected version, separate from the shelf's
 * write controls. Keep the chosen name only while that image still exists. */
export function CurrentArtViewer({ images, label }: { images: ArtImage[]; label: string }) {
  const [selected, setSelected] = useState("");
  const names = images.map((image) => image.name).join("\u0000");
  useEffect(() => {
    setSelected((old) => images.some((image) => image.name === old) ? old : images[0]?.name ?? "");
  }, [names]); // eslint-disable-line react-hooks/exhaustive-deps
  const current = images.find((image) => image.name === selected) ?? images[0];
  if (!current) return <div className="art-viewer-empty">No art for this version yet.</div>;
  return <div className="art-viewer">
    <a className="art-viewer-main" href={current.url()} target="_blank" rel="noreferrer"
       aria-label={`Open original ${label} ${current.name}`}>
      <img alt={`${label} ${current.name}`} {...thumbSet(current.url, "min(100vw, 720px)")} />
    </a>
    {images.length > 1 && <div className="art-viewer-picks" aria-label="Choose art">
      {images.map((image) => <button key={image.name} type="button"
        className={image.name === current.name ? "active" : ""}
        aria-label={`View ${image.name}`} aria-pressed={image.name === current.name}
        onClick={() => setSelected(image.name)}>
        <img alt="" {...thumbSet(image.url, "72px")} />
      </button>)}
    </div>}
  </div>;
}
