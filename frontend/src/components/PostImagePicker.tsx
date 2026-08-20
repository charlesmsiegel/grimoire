import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { CampaignImage } from "../api/types";
import { ImageDescriptionField } from "./ImageDescriptionField";

/** Who is speaking in the post the picker was opened from (#376).
 *
 *  A character or PC post offers that actor's art; a narrator post ("Grimoire"
 *  speaks for nobody — `store/scenes/write.py` records speaker `None`) offers
 *  the campaign's own image library, which exists precisely because a narrator
 *  has no record to hang art off. `version` is the version the roster has
 *  locked for this actor — the one that spoke — and is the group offered first.
 *
 *  Greeting art is deliberately NOT a third scope, though `image_subjects`
 *  records which characters appear in each greeting image and could answer
 *  "greeting art you appear in". Two reasons. Assembling it means listing every
 *  greeting in the world and reading each one's subjects map, to find pictures
 *  the reader can already reach; and the URL it would insert is world-scoped,
 *  so a post would carry a reference into the world rather than into the
 *  campaign the post belongs to — the one shape here that does not follow a
 *  campaign that later diverges. The supported route is the one that already
 *  exists: `copy-from-greeting` puts the greeting's image on the character's
 *  own version, campaign-side, and it turns up in this picker. */
export type PickerTarget =
  | { kind: "characters" | "pcs"; id: string; version: string; name: string }
  | { kind: "campaign"; name: string };

type Group = { key: string; label: string;
               images: { name: string; url: string; description?: string }[] };

/** A stored name derived from an uploaded file's own name.
 *
 *  Deliberately narrower than what the server accepts: anything outside letters,
 *  digits, `_` and `-` becomes a hyphen. Case and script survive, because the
 *  name is also the alt text — "Coast-at-Dusk" reads as something in a
 *  text-only export, and `image-3` does not.
 *
 *  This does not try to be the server's rule. It cannot be: `assets` reserves
 *  names of its own (`promote-tmp`), and a client copy of that list is a rule
 *  in two places, which is how the two come to disagree. The server has the
 *  last word and the picker shows what it says. */
export function nameFromFile(filename: string): string {
  const stem = filename.replace(/\.[^.]*$/, "");
  const slug = stem.replace(/[^\p{L}\p{N}_-]+/gu, "-").replace(/-{2,}/g, "-")
                   .replace(/^-+|-+$/g, "");
  return slug || "image";
}

/** `base`, or the first `base-N` no existing name has taken.
 *
 *  Occupancy is CASE-FOLDED, for the reason `assets._free_gallery` folds its
 *  own: on Windows and macOS an existing `Coast.png` *is* `coast.png`, so a
 *  case-sensitive comparison hands out a name that cannot be claimed without
 *  replacing somebody's image. Skipping a case variant on a case-sensitive
 *  filesystem too is merely conservative — the next suffix is equally good. */
export function freeName(base: string, taken: string[]): string {
  const used = new Set(taken.map((t) => t.toLowerCase()));
  if (!used.has(base.toLowerCase())) return base;
  let n = 2;
  while (used.has(`${base}-${n}`.toLowerCase())) n += 1;
  return `${base}-${n}`;
}

/** The markdown one pick inserts.
 *
 *  The alt text defaults to the image's NAME rather than being left empty, which
 *  is the whole difference between a text-only reader seeing something and
 *  seeing nothing: a plain-text export, and a model sent the transcript as text,
 *  get the alt text and only the alt text. */
export function insertion(name: string, url: string, description?: string): string {
  // A BARE url, with no `?v=` token, even though the picker has one in hand and
  // uses it for the thumbnails. A `?v=` URL is answered `immutable, max-age=1y`,
  // and this one is about to be written into a transcript that outlives every
  // cache: replacing the image under the same name would then leave the post
  // pinned to bytes that are gone for a year. Bare revalidates, which an ETag
  // answers with a 304.
  //
  // Neither half needs escaping. `]` would close the alt text and `)` the
  // destination, and a name can contain neither: `store.campaign_images
  // .addressable` refuses both the link punctuation and (through
  // `assets.storable`) the glob metacharacters `[` and `]`.
  //
  // The DESCRIPTION is the alt text when there is one, and the name only when
  // there is not. That is the same choice `context/art.resolve_handles` makes
  // when the model inserts a picture, and the two paths writing different alt
  // text for the same image would be a difference with no reason behind it.
  //
  // A description, unlike a name, can contain the link punctuation -- so it is
  // the one half that has to be escaped.
  const alt = description?.trim()
    ? description.trim().replace(/\[/g, "(").replace(/\]/g, ")").replace(/\s+/g, " ")
    : name;
  return `![${alt}](${url})`;
}

const THUMB = 160;

export function PostImagePicker({ cid, target, onInsert, onClose }: {
  cid: string; target: PickerTarget;
  onInsert: (markdown: string) => void; onClose: () => void;
}) {
  const [groups, setGroups] = useState<Group[] | null>(null);
  // `null` until a listing has actually come back. An upload names itself by
  // stepping around the names already there, so uploading against a listing
  // that FAILED would propose a name that is taken and quietly replace somebody
  // else's image — an empty array cannot tell "no images" from "did not ask".
  const [library, setLibrary] = useState<CampaignImage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Bumped after every upload and removal so the effect below re-reads the
  // library rather than this component keeping a second, divergent copy of it.
  const [revision, setRevision] = useState(0);
  const backdrop = useRef<HTMLDivElement>(null);
  // Focus the backdrop ONCE, on mount, so Escape has somewhere to land. As a
  // ref callback this would re-run on every render and pull focus back off
  // whatever the reader had just tabbed to.
  useEffect(() => { backdrop.current?.focus(); }, []);

  const isLibrary = target.kind === "campaign";
  // One dependency for "which images is this picker showing", so switching to a
  // different actor — or to a different locked version of the same one —
  // re-reads, while a re-render that merely rebuilt the `target` object does not.
  const targetKey = target.kind === "campaign" ? "campaign"
    : `${target.kind}/${target.id}/${target.version}`;

  useEffect(() => {
    let live = true;
    setError(null);
    (async () => {
      try {
        if (target.kind === "campaign") {
          const images = await api.listCampaignImages(cid);
          if (!live) return;
          setLibrary(images);
          setGroups([{
            key: "library", label: "Campaign images",
            images: images.map((i) => ({
              name: i.name, url: api.campaignImageUrl(cid, i.name),
              description: i.described ? (i.description ?? "") : undefined,
            })),
          }]);
          return;
        }
        const scope = { kind: "campaign", id: cid } as const;
        const { kind, id, version } = target;
        const detail: { versions: { id: string; name: string; images?: string[];
                                    image_descriptions?: Record<string, string> }[] } =
          kind === "characters" ? await api.readCharacter(scope, id)
                                : await api.readPC(scope, id);
        if (!live) return;
        // The version that spoke first, then the rest. A post records no version
        // of its own, so "the version that spoke" is the one the campaign has
        // locked for this actor — the same one the speaker plate draws its
        // portrait from. The others are still offered: a character's art often
        // belongs to an era the roster is not currently in.
        const ordered = [...detail.versions].sort(
          (a, b) => Number(b.id === version) - Number(a.id === version));
        setGroups(ordered
          .filter((v) => (v.images ?? []).length > 0)
          .map((v) => ({
            key: v.id,
            label: v.id === version ? `${v.name} — spoke here` : v.name,
            images: (v.images ?? []).map((name) => ({
              name, url: api.actorImageUrl(scope, kind, id, v.id, name),
              description: v.image_descriptions?.[name],
            })),
          })));
      } catch (err: any) {
        // `library` is deliberately left as it was — null on a first failure,
        // which is what closes the Add control (see the state above).
        if (live) { setGroups([]); setError(err?.detail ?? String(err)); }
      }
    })();
    return () => { live = false; };
  }, [cid, targetKey, revision]);   // eslint-disable-line react-hooks/exhaustive-deps

  /** Run one library mutation: busy while it is in flight, its reason shown if
   *  it fails, and the listing re-read if it lands — so the grid is always the
   *  server's answer rather than a second copy kept here. */
  async function mutate(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setRevision((r) => r + 1);
    } catch (err: any) {
      setError(err?.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  function upload(file: File | undefined) {
    if (!file) return;
    const name = freeName(nameFromFile(file.name), (library ?? []).map((i) => i.name));
    void mutate(() => api.putCampaignImage(cid, name, file));
  }

  function remove(name: string) {
    // Confirmed, unlike the cover's Remove: a cover is referenced by nothing,
    // and one of these can already be linked from forty posts, which would then
    // render as broken images with no way back but re-uploading under the very
    // same name. Same posture the cut and the fork take.
    if (!window.confirm(
      `Remove "${name}" from this campaign? Posts that already link to it will `
      + "show a broken image.")) return;
    void mutate(() => api.deleteCampaignImage(cid, name));
  }

  const empty = groups !== null && groups.every((g) => g.images.length === 0);
  return (
    // Escape closes, handled on the backdrop rather than on `document`: the
    // backdrop covers the page and takes focus on mount, so a keystroke aimed
    // at whatever is behind the picker can never reach this.
    <div className="image-picker-backdrop" role="dialog"
         aria-label={`Insert an image — ${target.name}`}
         tabIndex={-1} ref={backdrop}
         onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
         onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="image-picker">
        <h3>Insert an image</h3>
        <p className="field-hint">
          {isLibrary
            ? "The campaign's own images — what the narrator has to draw on."
            : `${target.name}'s images.`}
        </p>
        {error && <div className="banner">{error}</div>}
        {groups === null && <p className="field-hint">Loading…</p>}
        {/* Withheld when anything went wrong: on a failed listing `groups` is
            empty because the read never landed, and telling the reader there
            are no images — under a disabled Add — would be the banner's
            opposite. */}
        {empty && error === null && (
          <p className="field-hint">
            {isLibrary
              ? "No campaign images yet."
              : `Nothing stored for ${target.name} yet — art is added in the library editor.`}
          </p>
        )}
        {/* An empty group is dropped rather than rendered as a heading over
            nothing — which is what the library's single group would be before
            the first upload, under the hint that already says so. */}
        {(groups ?? []).filter((g) => g.images.length > 0).map((g) => (
          <div className="side-section" key={g.key}>
            <h4>{g.label}</h4>
            <div className="image-picker-grid">
              {g.images.map((img) => (
                <div className="image-picker-tile" key={img.name}>
                  <button type="button" title={`Insert ${img.name}`}
                          aria-label={`Insert ${img.name}`}
                          onClick={() => onInsert(insertion(img.name, img.url, img.description))}>
                    <img src={`${img.url}?w=${THUMB}`} alt="" />
                    <span>{img.name}</span>
                  </button>
                  {isLibrary && (
                    <ImageDescriptionField
                      name={img.name} value={img.description}
                      onSave={async (d) => {
                        await api.setCampaignImageDescription(cid, img.name, d);
                        setRevision((n) => n + 1);
                      }} />
                  )}
                  {isLibrary && (
                    <button className="subtle image-picker-remove" type="button"
                            disabled={busy} aria-label={`Remove ${img.name}`}
                            title={`Remove ${img.name} from the campaign`}
                            onClick={() => remove(img.name)}>✕</button>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
        <div className="form-actions">
          {isLibrary && (
            // A plain, visible file input with a label, the way `CampaignCover`
            // does it. A `display: none` input inside a styled label looks
            // tidier and is unreachable by keyboard, which is worse than tidy.
            // `accept` lists what the store will actually keep rather than
            // `image/*`, which offers AVIF and BMP the server refuses.
            <>
              <label className="field-hint" htmlFor="campaign-library-file">
                {busy ? "Working…" : library === null ? "Add an image (unavailable)"
                                                      : "Add an image"}
              </label>
              <input id="campaign-library-file" type="file" disabled={busy || library === null}
                     accept="image/png,image/jpeg,image/gif,image/webp"
                     onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
            </>
          )}
          <button className="subtle" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
