import { thumbSet } from "../api/thumbs";
import { initialsOf } from "./Portrait";

/** The context column is a phone sheet, so a portrait and Art action must live
 * in main as well. The action is offered even before the first upload. */
export function MobileArtEntry({ name, image, onOpenArt }: {
  name: string; image: ((w?: number) => string) | null; onOpenArt: () => void;
}) {
  return <div className="mobile-art-entry">
    <button type="button" className="mobile-art-preview" aria-label={`View ${name} art`}
            onClick={onOpenArt}>
      {image ? <img alt={`${name} art preview`} {...thumbSet(image, "240px")} />
        : <span aria-hidden>{initialsOf(name)}</span>}
    </button>
    <button type="button" className="subtle mobile-art-action" onClick={onOpenArt}>View art</button>
  </div>;
}
