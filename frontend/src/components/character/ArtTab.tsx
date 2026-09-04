import { useRef, useState } from "react";
import { api, type Appearance, type EntityScope } from "../../api/client";
import { errorText } from "../../api/errors";
import { ImageDescriptionField } from "../ImageDescriptionField";
import { avatarSrc, withToken } from "./shared";

/** The names a batch of newly-picked files lands under.
 *
 *  Named in ONE pass over the shelf as it stands rather than re-read between
 *  uploads, which is what makes picking several files at once safe: every name
 *  is decided before the first upload lands, so nothing in the batch can be
 *  handed a `gallery_N` another file in the same batch is already taking.
 *
 *  An empty avatar slot takes the first file — the rule the PC and entity
 *  shelves use too — and the rest queue after the highest number on the shelf.
 *  That is a high-water mark and not a count: removing `gallery_2` from a shelf
 *  that also has `gallery_3` must not make the next upload overwrite it.
 */
export function nextImageNames(count: number, hasAvatar: boolean, gallery: string[]): string[] {
  let top = gallery.reduce((m, n) => Math.max(m, Number(n.slice("gallery_".length))), 0);
  return Array.from({ length: count },
                    (_, i) => (i === 0 && !hasAvatar ? "avatar" : `gallery_${++top}`));
}

/** Every image this version has, plus the greeting art it could borrow.
 *
 *  The shelf is a `repeat(auto-fill, minmax(…))` grid rather than the row it
 *  used to be — in a 433px pane a gallery was a horizontal scroll of tiles you
 *  could see two of at a time, and the page it lives on is now wide enough for
 *  the grid to be worth having.
 */
export function ArtTab(
  { scope, wid, cid, vid, hasAvatar, galleryImages, imageTokens, descriptions, appearances,
    worldScope, localizeProg, localizeMsg, onLocalize, onRefresh, onError, onOpenGreeting }: {
    scope: EntityScope;
    wid: string;
    cid: string;
    vid: string;
    hasAvatar: boolean;
    galleryImages: string[];
    imageTokens: Record<string, string>;
    /** Absent key = never reviewed, `""` = reviewed and deliberately
     *  undescribed. Only the first belongs in the describe queue. */
    descriptions: Record<string, string>;
    appearances: Appearance[];
    worldScope: boolean;
    localizeProg: { done: number; total: number } | null;
    localizeMsg: string | null;
    onLocalize: () => void;
    onRefresh: () => Promise<void>;
    onError: (err: unknown) => void;
    onOpenGreeting: (gid: string) => void;
  },
) {
  const shelfFileRef = useRef<HTMLInputElement>(null);
  const [adding, setAdding] = useState<{ done: number; total: number } | null>(null);

  async function guard(run: () => Promise<unknown>) {
    try { await run(); await onRefresh(); } catch (err: unknown) { onError(err); }
  }

  /** Store every picked file, then re-read the character once.
   *
   *  Sequential and all-attempted, the shape the card import already uses: a
   *  rejected upload is one picture missing rather than a batch abandoned
   *  half-way, and the report names the files that did not land so the reader
   *  knows which to pick again. One refresh at the end rather than one per
   *  file — the tiles are worth redrawing when the batch is in, not thirty
   *  times on the way there.
   */
  async function onAdd(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";   // the same file twice in a row must still fire onChange
    if (!files.length) return;
    const names = nextImageNames(files.length, hasAvatar, galleryImages);
    const failures: string[] = [];
    setAdding({ done: 0, total: files.length });
    for (let i = 0; i < files.length; i++) {
      try {
        await api.putImage(scope, cid, vid, names[i], files[i]);
      } catch (err: unknown) {
        failures.push(`${files[i].name}: ${errorText(err)}`);
      }
      setAdding({ done: i + 1, total: files.length });
    }
    setAdding(null);
    await onRefresh().catch((err: unknown) => onError(err));
    // Last, so it is the failure the banner ends up showing: a refresh that
    // also failed is the lesser news when three of five pictures are missing.
    if (failures.length) {
      onError(`Couldn’t add ${failures.length} of ${files.length} — ${failures.join("; ")}`);
    }
  }

  return <>
    <div className="card-field">
      <div className="card-field-head"><span className="data-label">Images</span></div>
      <div className="images-shelf">
        {hasAvatar ? (
          <figure className="shelf-tile avatar-tile">
            <a href={avatarSrc(scope, cid, vid, imageTokens.avatar)} target="_blank" rel="noreferrer">
              <img alt="avatar" src={avatarSrc(scope, cid, vid, imageTokens.avatar)} />
            </a>
            <figcaption>avatar</figcaption>
            <ImageDescriptionField key={`${vid}:avatar`} name="avatar" value={descriptions.avatar}
                                   onSave={(d) => guard(() =>
                                     api.setCharacterImageDescription(scope, cid, vid, "avatar", d))}
                                   onDraft={worldScope
                                     ? () => api.draftCharacterImageDescription(wid, cid, vid, "avatar")
                                         .then((r) => r.description)
                                     : undefined} />
            <button className="shelf-promote" type="button"
                    onClick={() => void guard(() => api.deleteImage(scope, cid, vid, "avatar"))}>
              Remove
            </button>
          </figure>
        ) : (
          <div className="shelf-tile shelf-empty">no avatar</div>
        )}
        {galleryImages.map((imgName) => {
          const src = withToken(
            api.actorImageUrl(scope, "characters", cid, vid, imgName), imageTokens[imgName]);
          return (
            <div className="shelf-tile" key={imgName}>
              <a href={src} target="_blank" rel="noreferrer"><img alt={imgName} src={src} /></a>
              <button className="shelf-promote" type="button"
                      onClick={() => void guard(() => api.promoteImage(scope, cid, vid, imgName))}>
                Set as avatar
              </button>
              <ImageDescriptionField key={`${vid}:${imgName}`} name={imgName}
                                     value={descriptions[imgName]}
                                     onSave={(d) => guard(() =>
                                       api.setCharacterImageDescription(scope, cid, vid, imgName, d))}
                                     onDraft={worldScope
                                       ? () => api.draftCharacterImageDescription(wid, cid, vid, imgName)
                                           .then((r) => r.description)
                                       : undefined} />
            </div>
          );
        })}
        <button className="shelf-add" disabled={!!adding}
                onClick={() => shelfFileRef.current?.click()}>
          {adding ? `adding ${adding.done}/${adding.total}…` : "+ add"}
        </button>
        <input ref={shelfFileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp"
               multiple hidden aria-label="Add images" onChange={(e) => void onAdd(e)} />
      </div>
    </div>

    {appearances.length > 0 && (
      <div className="card-field">
        <div className="card-field-head"><span className="data-label">Appears in</span></div>
        <div className="images-shelf">
          {appearances.map((a) => (
            <div className="shelf-tile" key={`${a.gid}/${a.name}`}>
              <a href={a.url} target="_blank" rel="noreferrer">
                <img alt={`${a.greeting_name} art`} src={a.thumb ?? a.url} />
              </a>
              <button className="shelf-promote" onClick={() => void guard(() =>
                api.copyGreetingImage(scope, cid, vid, { gid: a.gid, name: a.name, slot: "avatar" }))}>
                Set as avatar
              </button>
              <button className="shelf-promote" onClick={() => void guard(() =>
                api.copyGreetingImage(scope, cid, vid, { gid: a.gid, name: a.name, slot: "gallery" }))}>
                Add to gallery
              </button>
              <button className="shelf-promote" onClick={() => onOpenGreeting(a.gid)}>
                {a.greeting_name}
              </button>
            </div>
          ))}
        </div>
      </div>
    )}

    {worldScope && (
      <div className="localize-block">
        <button className="subtle" type="button" disabled={!!localizeProg} onClick={onLocalize}>
          {localizeProg ? "Localizing…" : "Localize images"}
        </button>
        {localizeProg && (
          <div className="localize-progress">
            <progress value={localizeProg.done} max={localizeProg.total || 1} />
            <span className="field-hint">{localizeProg.done}/{localizeProg.total}</span>
          </div>
        )}
        {localizeMsg && <span className="field-hint">{localizeMsg}</span>}
      </div>
    )}
  </>;
}

export default ArtTab;
