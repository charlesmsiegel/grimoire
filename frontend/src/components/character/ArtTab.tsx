import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Appearance, type BaseVersion, type EntityScope, type ShadowedImage }
  from "../../api/client";
import { errorText } from "../../api/errors";
import { ImageDescriptionField } from "../ImageDescriptionField";
import { thumbSet } from "../../api/thumbs";
import { characterImage } from "./shared";

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

/** One tile of the shelf: the picture, and whatever this page may do to it.
 *
 *  The same markup for every picture on the page, wherever its bytes live,
 *  which is what keeps the campaign's shelf and the world's looking like one
 *  page: a 96px tile, the promote and remove controls when the picture can be
 *  written to from here, and the description in the same clamped box whether
 *  it can be edited (`onSave` given, the field) or only read (a plain box).
 */
function Tile({ name, url, avatar, caption, description, descKey,
                onSave, onDraft, onPromote, onRemove }: {
  name: string;
  /** The image's URL at a `?w=`, or the original with none: the tile draws a
   *  96px downscale and links to the original, which is where the reader goes
   *  to look at the picture itself. */
  url: (w?: number) => string;
  avatar?: boolean;
  caption?: string;
  description: string | undefined;
  descKey: string;
  onSave?: (d: string) => Promise<void>;
  onDraft?: () => Promise<string>;
  onPromote?: () => void;
  onRemove?: () => void;
}) {
  return (
    <figure className={"shelf-tile" + (avatar ? " avatar-tile" : "")}>
      <a href={url()} target="_blank" rel="noreferrer">
        <img alt={name} loading="lazy" decoding="async" {...thumbSet(url, "96px")} />
      </a>
      {caption && <figcaption title={caption}>{caption}</figcaption>}
      {onPromote && (
        <button className="shelf-promote" type="button" onClick={onPromote}>Set as avatar</button>
      )}
      {onSave
        ? <ImageDescriptionField key={descKey} name={name} value={description}
                                 onSave={onSave} onDraft={onDraft} />
        : <div className={"image-description" + (description ? "" : " image-description-empty")}
               title={description || undefined}>
            {description || "No description"}
          </div>}
      {onRemove && (
        <button className="shelf-promote" type="button" onClick={onRemove}>Remove</button>
      )}
    </figure>
  );
}

/** Every image this version has, plus the greeting art it could borrow.
 *
 *  The shelf is a `repeat(auto-fill, minmax(…))` grid rather than the row it
 *  used to be — in a 433px pane a gallery was a horizontal scroll of tiles you
 *  could see two of at a time, and the page it lives on is now wide enough for
 *  the grid to be worth having.
 *
 *  In campaign scope the page has two shelves. **Images** holds what the
 *  campaign has a file of its own for; **World images** holds everything it
 *  reads from the world: the pictures of this version it inherits (still
 *  writable through the campaign routes, which handle an inherited picture),
 *  the world's copies its own same-named files hide, and the versions a pick
 *  purged. The last two are read-only -- their bytes and captions are the
 *  world's -- and say so by carrying no control.
 */
export function ArtTab(
  { scope, wid, cid, vid, hasAvatar, galleryImages, imageTokens, descriptions, appearances,
    worldScope, inherited, baseVersions, shadowed, localizeProg, localizeMsg, onLocalize,
    onRefresh, onError, greetingHref }: {
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
    /** Campaign scope: which of this version's names are the world's. */
    inherited?: string[];
    /** Campaign scope: the world versions the campaign no longer holds, each
     *  shown read-only beside this version's own. See `BaseVersion`. */
    baseVersions?: BaseVersion[];
    /** Campaign scope: the world's copies this version's own files hide,
     *  shown read-only beside them. See `ShadowedImage`. */
    shadowed?: ShadowedImage[];
    localizeProg: { done: number; total: number } | null;
    localizeMsg: string | null;
    onLocalize: () => void;
    onRefresh: () => Promise<void>;
    onError: (err: unknown) => void;
    greetingHref: (gid: string) => string;
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

  // The world's side of this version, and the version's own. In world scope
  // nothing is inherited, so the split is the whole shelf and nothing.
  const fromWorld = new Set(worldScope ? [] : inherited ?? []);
  const ownAvatar = hasAvatar && !fromWorld.has("avatar");
  const ownGallery = galleryImages.filter((n) => !fromWorld.has(n));
  const worldGallery = galleryImages.filter((n) => fromWorld.has(n));
  const bases = worldScope ? [] : (baseVersions ?? []).filter((b) => b.id !== vid);
  const originals = worldScope ? [] : shadowed ?? [];
  const anyWorld = fromWorld.size > 0 || originals.length > 0 || bases.length > 0;

  /** A tile of THIS version, whichever root holds it: the campaign routes
   *  resolve the name the way the shelf read it, so the same controls apply. */
  const versionTile = (name: string, avatar: boolean) => (
    <Tile key={name} name={name} avatar={avatar} caption={avatar ? "avatar" : undefined}
          url={characterImage(scope, cid, vid, name, imageTokens[name])}
          description={descriptions[name]} descKey={`${vid}:${name}`}
          onSave={(d) => guard(() => api.setCharacterImageDescription(scope, cid, vid, name, d))}
          onDraft={worldScope
            ? () => api.draftCharacterImageDescription(wid, cid, vid, name).then((r) => r.description)
            : undefined}
          onPromote={avatar ? undefined
            : () => void guard(() => api.promoteImage(scope, cid, vid, name))}
          onRemove={avatar ? () => void guard(() => api.deleteImage(scope, cid, vid, "avatar"))
                           : undefined} />
  );

  return <>
    <div className="card-field">
      <div className="card-field-head"><span className="data-label">Images</span></div>
      <div className="images-shelf">
        {ownAvatar
          ? versionTile("avatar", true)
          : <div className="shelf-tile shelf-empty">{hasAvatar ? "world’s avatar" : "no avatar"}</div>}
        {ownGallery.map((name) => versionTile(name, false))}
        <button className="shelf-add" disabled={!!adding}
                onClick={() => shelfFileRef.current?.click()}>
          {adding ? `adding ${adding.done}/${adding.total}…` : "+ add"}
        </button>
        <input ref={shelfFileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp"
               multiple hidden aria-label="Add images" onChange={(e) => void onAdd(e)} />
      </div>
    </div>

    {anyWorld && (
      <div className="card-field">
        <div className="card-field-head"><span className="data-label">World images</span></div>
        <div className="images-shelf">
          {hasAvatar && fromWorld.has("avatar") && versionTile("avatar", true)}
          {worldGallery.map((name) => versionTile(name, false))}
          {/* The world's copy under a name this version replaced: served from
              the WORLD route, the only one that resolves that name to the
              world's bytes. */}
          {originals.map((img) => (
            <Tile key={`shadowed:${img.name}`} name={img.name} caption={`${img.name} · world’s copy`}
                  url={characterImage({ kind: "world", id: wid }, cid, vid, img.name, img.v)}
                  description={img.description} descKey={`shadowed:${vid}:${img.name}`} />
          ))}
          {/* A version the campaign no longer holds: the campaign route falls
              through to the world for it. A label that is not the id carries
              the id too, because a card's own `character_version` can name two
              versions the same thing. */}
          {bases.map((base) => base.images.map((name) => (
            <Tile key={`${base.id}:${name}`} name={name}
                  caption={`${name} · ${base.name === base.id ? base.name : `${base.name} (${base.id})`}`}
                  url={characterImage(scope, cid, base.id, name, base.image_v[name])}
                  description={base.image_descriptions[name]} descKey={`${base.id}:${name}`} />
          )))}
        </div>
      </div>
    )}

    {appearances.length > 0 && (
      <div className="card-field">
        <div className="card-field-head"><span className="data-label">Appears in</span></div>
        <div className="images-shelf">
          {appearances.map((a) => (
            <div className="shelf-tile" key={`${a.gid}/${a.name}`}>
              <a href={a.url} target="_blank" rel="noreferrer">
                <img alt={`${a.greeting_name} art`} loading="lazy" decoding="async"
                     src={a.thumb ?? a.url} />
              </a>
              <button className="shelf-promote" onClick={() => void guard(() =>
                api.copyGreetingImage(scope, cid, vid, { gid: a.gid, name: a.name, slot: "avatar" }))}>
                Set as avatar
              </button>
              <button className="shelf-promote" onClick={() => void guard(() =>
                api.copyGreetingImage(scope, cid, vid, { gid: a.gid, name: a.name, slot: "gallery" }))}>
                Add to gallery
              </button>
              <Link className="shelf-promote" to={greetingHref(a.gid)}>
                {a.greeting_name}
              </Link>
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
