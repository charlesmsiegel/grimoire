import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { CampaignImage } from "../api/types";

/** Who is speaking in the post the picker was opened from (#376).
 *
 *  A character or PC post offers that actor's art; a narrator post ("Grimoire"
 *  speaks for nobody — `store/scenes/write.py` records speaker `None`) offers
 *  the campaign's own image library, which exists precisely because a narrator
 *  has no record to hang art off. `version` is the version the roster has
 *  locked for this actor — the one that spoke — and is the group offered first. */
export type PickerTarget =
  | { kind: "characters" | "pcs"; id: string; version: string; name: string }
  | { kind: "campaign"; name: string };

type Group = { key: string; label: string; images: { name: string; url: string }[] };

/** A stored name derived from an uploaded file's own name.
 *
 *  Deliberately narrower than what the server accepts: anything outside letters,
 *  digits, `_` and `-` becomes a hyphen, which is a strict subset of
 *  `store.campaign_images.addressable` and so can never be refused for its
 *  characters. Case and script survive, because the name is also the alt text —
 *  "Coast-at-Dusk" reads as something in a text-only export, and `image-3` does
 *  not. */
export function nameFromFile(filename: string): string {
  const stem = filename.replace(/\.[^.]*$/, "");
  const slug = stem.replace(/[^\p{L}\p{N}_-]+/gu, "-").replace(/-{2,}/g, "-")
                   .replace(/^-+|-+$/g, "");
  return slug || "image";
}

/** `base`, or the first `base-N` no existing name has taken. */
export function freeName(base: string, taken: string[]): string {
  if (!taken.includes(base)) return base;
  let n = 2;
  while (taken.includes(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

/** The markdown one pick inserts.
 *
 *  The alt text defaults to the image's NAME rather than being left empty, which
 *  is the whole difference between a text-only reader seeing something and
 *  seeing nothing: a plain-text export, and a model sent the transcript as text,
 *  get the alt text and only the alt text. */
export function insertion(name: string, url: string): string {
  return `![${name}](${url})`;
}

const THUMB = 160;

export function PostImagePicker({ cid, target, onInsert, onClose }: {
  cid: string; target: PickerTarget;
  onInsert: (markdown: string) => void; onClose: () => void;
}) {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [library, setLibrary] = useState<CampaignImage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Bumped after every upload and removal so the effect below re-reads the
  // library rather than this component keeping a second, divergent copy of it.
  const [revision, setRevision] = useState(0);

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
            images: images.map((i) => ({ name: i.name, url: api.campaignImageUrl(cid, i.name) })),
          }]);
          return;
        }
        const scope = { kind: "campaign", id: cid } as const;
        const { kind, id, version } = target;
        const detail: { versions: { id: string; name: string; images?: string[] }[] } =
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
            })),
          })));
      } catch (err: any) {
        if (live) { setGroups([]); setError(err?.detail ?? String(err)); }
      }
    })();
    return () => { live = false; };
  }, [cid, targetKey, revision]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await api.putCampaignImage(
        cid, freeName(nameFromFile(file.name), library.map((i) => i.name)), file);
      setRevision((r) => r + 1);
    } catch (err: any) {
      setError(err?.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteCampaignImage(cid, name);
      setRevision((r) => r + 1);
    } catch (err: any) {
      setError(err?.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  const empty = groups !== null && groups.every((g) => g.images.length === 0);
  return (
    <div className="image-picker-backdrop" role="dialog"
         aria-label={`Insert an image — ${target.name}`}
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
        {empty && (
          <p className="field-hint">
            {isLibrary
              ? "No campaign images yet. Add one below."
              : `Nothing stored for ${target.name} yet — art is added in the library editor.`}
          </p>
        )}
        {(groups ?? []).map((g) => (
          <div className="side-section" key={g.key}>
            <h4>{g.label}</h4>
            <div className="image-picker-grid">
              {g.images.map((img) => (
                <div className="image-picker-tile" key={img.name}>
                  <button type="button" title={`Insert ${img.name}`}
                          aria-label={`Insert ${img.name}`}
                          onClick={() => onInsert(insertion(img.name, img.url))}>
                    <img src={`${img.url}?w=${THUMB}`} alt="" />
                    <span>{img.name}</span>
                  </button>
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
            <label className="btn-chrome image-picker-add">
              {busy ? "Working…" : "Add image…"}
              <input type="file" accept="image/*" aria-label="Add a campaign image"
                     disabled={busy}
                     onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
          )}
          <button className="subtle" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
