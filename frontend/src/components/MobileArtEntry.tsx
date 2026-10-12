import { thumbSet } from "../api/thumbs";
import { initialsOf } from "./Portrait";

/** The context column is a phone sheet, so a portrait and Art action must live
 * in main as well. The action is offered even before the first upload. */
export function MobileArtEntry({ name, image, focus, onOpenArt, onOpenPreview }: {
  onOpenPreview?: (opener: HTMLElement) => void;
  name: string; image: ((w?: number) => string) | null; focus?: number | null; onOpenArt: () => void;
}) {
  return <div className="mobile-art-entry">
    <button type="button" className="mobile-art-preview" aria-label={`View ${name} art`}
            onClick={(event) => { if (image && onOpenPreview) onOpenPreview(event.currentTarget); else onOpenArt(); }}>
      {image ? <img alt={`${name} art preview`} {...thumbSet(image, "240px")}
        style={{ objectPosition: (focus ?? 50) + "% " + (focus ?? 50) + "%" }} />
        : <span aria-hidden>{initialsOf(name)}</span>}
    </button>
    <button type="button" className="subtle mobile-art-action" onClick={onOpenArt}>View art</button>
  </div>;
}
