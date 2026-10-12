import { Link, useNavigate } from "react-router-dom";
import type { Appearance, BaseVersion, EntityScope } from "../../api/types";
import { artRecordHref } from "../../worldPaths";
import { ImageTile } from "../art/ImageTile";
import { ImageViewer } from "../art/ImageViewer";
import { localImageDownloadUrl } from "../art/recordArtAdapter";
import type { ImageDescriptor } from "../art/types";
import { useImageViewer } from "../art/useImageViewer";
import { characterImage } from "./shared";

export type CharacterArtRelatedProps = {
  scope: EntityScope; wid: string; record: string; version: string;
  appearances: Appearance[]; baseVersions: BaseVersion[];
  manage: boolean; busy: boolean; fallbackFocus: HTMLElement | null;
  onView?: (action: () => void) => void;
  onCopy: (appearance: Appearance, slot: "avatar" | "gallery") => void;
  greetingHref: (gid: string) => string;
  localizeProg: { done: number; total: number } | null; localizeMsg: string | null;
  onLocalize: () => void;
};
/** Greeting appearances keep their own viewing/copy contract. A purged world
 * version is display-only here: serving it through the world route also avoids
 * accidentally looking at a campaign's same-named replacement. */
export function CharacterArtRelated(props: CharacterArtRelatedProps) {
  const { scope, wid, record, version, appearances, manage, busy } = props;
  const navigate = useNavigate();
  const viewer = useImageViewer(JSON.stringify([scope.kind, scope.id, record, version]));
  const images: ImageDescriptor[] = scope.kind === "world" ? [] : props.baseVersions
    .filter((base) => base.id !== version)
    .flatMap((base) => base.images.map((name) => {
      const originalUrl = characterImage({ kind: "world", id: wid }, record, base.id, name, base.image_v[name])();
      const label = name + " · " + (base.name === base.id ? base.name : base.name + " (" + base.id + ")");
      return { key: JSON.stringify([base.id, name]), originalUrl,
        thumbnailUrl: characterImage({ kind: "world", id: wid }, record, base.id, name, base.image_v[name])(160),
        downloadUrl: localImageDownloadUrl(originalUrl), label, alt: label, version: base.image_v[name],
        imageId: base.image_ids?.[name], description: base.image_descriptions[name],
        described: name in base.image_descriptions, role: "related", provenance: "world-original",
        recordDestination: artRecordHref({ scope: { kind: "world", id: wid }, kind: "characters", record_id: record, version: base.id }) };
    }));
  return <>
    {viewer.notice && <p role="status">{viewer.notice}</p>}
    {images.length > 0 && <section className="card-field" aria-label="Other world versions">
      <div className="card-field-head"><span className="data-label">Other world versions</span></div>
      <div className="art-image-grid">{images.map((image) => <article key={image.key}>
        <ImageTile image={image} disabled={busy} onOpen={(opener) => {
          const open = () => viewer.open(images, image.key, opener);
          if (!busy) { if (props.onView) props.onView(open); else open(); }
        }} />
        <p>{image.description ?? "Not described"}</p>
      </article>)}</div>
    </section>}
    {viewer.viewerProps && <ImageViewer {...viewer.viewerProps} images={images} fallbackFocus={props.fallbackFocus} onOpenRecord={(destination) => { viewer.close(); navigate(destination); }} />}
    {appearances.length > 0 && <div className="card-field">
      <div className="card-field-head"><span className="data-label">Appears in</span></div>
      <div className="images-shelf">{appearances.map((a) => <div className="shelf-tile" key={a.gid + "/" + a.name}>
        <a href={a.url} target="_blank" rel="noreferrer">
          <img alt={a.greeting_name + " art"} loading="lazy" decoding="async" src={a.thumb ?? a.url} />
        </a>
        {manage && a.copyable !== false && <>
          <button className="shelf-promote" disabled={busy} onClick={() => props.onCopy(a, "avatar")}>Set as avatar</button>
          <button className="shelf-promote" disabled={busy} onClick={() => props.onCopy(a, "gallery")}>Add to gallery</button>
        </>}
        <Link className="shelf-promote" to={props.greetingHref(a.gid)}>{a.greeting_name}</Link>
      </div>)}</div>
    </div>}
    {scope.kind === "world" && <div className="localize-block">
      {manage && <button className="subtle" type="button" disabled={busy || !!props.localizeProg} onClick={props.onLocalize}>
        {props.localizeProg ? "Localizing…" : "Localize images"}
      </button>}
      {props.localizeProg && <div className="localize-progress">
        <progress value={props.localizeProg.done} max={props.localizeProg.total || 1} />
        <span className="field-hint">{props.localizeProg.done}/{props.localizeProg.total}</span>
      </div>}
      {props.localizeMsg && <span className="field-hint">{props.localizeMsg}</span>}
    </div>}
  </>;
}
