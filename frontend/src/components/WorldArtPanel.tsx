import { useCallback, useEffect, useRef, useState } from "react";
import { api, type WorldImage } from "../api/client";
import { CoverPanel } from "./CoverPanel";
import { ImageDescriptionField } from "./ImageDescriptionField";

/** What went wrong, in the shape `api.request` rejects with (`{detail}`) —
 *  falling back to the value itself for anything that is not ours. Narrowed
 *  rather than typed `any`, so a rejection shape that changes shows up here. */
function reason(e: unknown): string {
  const detail = (e as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" ? detail : String(e);
}

/** A world's own art: its cover, and the image library every campaign on it
 *  reads through to.
 *
 *  Its own panel rather than controls bolted onto the gallery beside it, and
 *  that is a rule this component exists to respect rather than an arrangement:
 *  `ImagesView` says in as many words that the gallery is a *browser* and that
 *  "the two sidecars it reports are written in the editors that own them". This
 *  is that editor for the one base the gallery reports that no other editor
 *  owns — a library image hangs off no record, so there is no record page to
 *  put it on.
 *
 *  Rendered whether or not a campaign is open. The rail appends `&for=<cid>` to
 *  the Images row whenever one is (`shell/rail.ts`), so hiding this under that
 *  flag would make the world's only art editor unreachable by the app's own
 *  navigation for as long as a campaign is being played. The reader is on
 *  `/worlds/<wid>`, and the controls say they are the world's. */
export function WorldArtPanel({ wid }: { wid: string }) {
  const [images, setImages] = useState<WorldImage[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // Which world this panel is showing. The Images tab is keyed by `wid` so a
  // world switch remounts, but an upload in flight can still settle after the
  // reader has moved on -- the guard `CoverPanel` documents, for its reason.
  const live = useRef(wid);

  const load = useCallback(async () => {
    const mine = wid;
    try {
      const got = await api.listWorldLibrary(wid);
      if (live.current === mine) { setImages(got); setErr(null); }
    } catch (e: unknown) {
      if (live.current === mine) { setImages([]); setErr(reason(e)); }
    }
  }, [wid]);

  useEffect(() => { live.current = wid; setImages(null); void load(); }, [wid, load]);

  async function mutate(fn: () => Promise<unknown>) {
    const mine = wid;
    setBusy(true);
    setErr(null);
    try {
      await fn();
      if (live.current === mine) await load();
    } catch (e: unknown) {
      if (live.current === mine) setErr(reason(e));
    } finally {
      if (live.current === mine) {
        setBusy(false);
        if (input.current) input.current.value = "";   // re-picking the same file re-fires
      }
    }
  }

  /** A file's name, minus its extension, reduced to something a markdown link
   *  can carry -- the server's rule (`store/image_library.py`), applied here so
   *  the reader gets a working upload rather than a 400 on a name they never
   *  chose. */
  function nameFor(file: File, taken: string[]): string {
    const stem = file.name.replace(/\.[^.]+$/, "");
    const safe = stem.replace(/[()<>#?%"'`\\[\].\s]+/g, "-")
      .replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "image";
    if (!taken.includes(safe)) return safe;
    for (let n = 2; ; n += 1) if (!taken.includes(`${safe}-${n}`)) return `${safe}-${n}`;
  }

  function upload(file: File | undefined) {
    if (!file || images === null) return;
    void mutate(() => api.putWorldImage(wid, nameFor(file, images.map((i) => i.name)), file));
  }

  function remove(name: string) {
    // Confirmed, and the confirmation says the part that is not obvious: these
    // are read through by every campaign on the world, so this is not a local
    // tidy-up. Campaigns that had hidden it have their hidden entry cleared.
    if (!window.confirm(
      `Delete "${name}" from this world? Every campaign on it loses the picture, `
      + "and posts that already link to it will show a broken image.")) return;
    void mutate(() => api.deleteWorldImage(wid, name));
  }

  return (
    <div className="world-art">
      <div className="side-section">
        <h4>World cover</h4>
        <CoverPanel scope={{ kind: "world", id: wid }} />
      </div>

      <div className="side-section">
        <h4>World images</h4>
        <p className="field-hint">
          Art that belongs to the world and to none of its records — a map, a
          banner, a piece of establishing art. Every campaign on this world can
          put these in a post, and describing one here describes it once for all
          of them.
        </p>
        {err && <div className="banner" role="alert">{err}</div>}
        {images === null && <p className="field-hint">Reading the world’s art…</p>}
        {images !== null && images.length === 0 && !err && (
          <p className="field-hint">No world images yet.</p>
        )}
        {images !== null && images.length > 0 && (
          <div className="image-picker-grid">
            {images.map((img) => (
              <div className="image-picker-tile" key={img.name}>
                <img src={api.worldImageUrl(wid, img.name, { w: 154, v: img.v })} alt="" />
                <span>{img.name}</span>
                <ImageDescriptionField
                  key={img.name} name={img.name}
                  value={img.described ? (img.description ?? "") : undefined}
                  onSave={async (d) => {
                    await api.setWorldImageDescription(wid, img.name, d);
                    await load();
                  }}
                  onDraft={async () =>
                    (await api.draftWorldImageDescription(wid, img.name)).description} />
                <button className="subtle image-picker-remove" type="button"
                        disabled={busy} aria-label={`Delete ${img.name}`}
                        title={`Delete ${img.name} from this world`}
                        onClick={() => remove(img.name)}>✕</button>
              </div>
            ))}
          </div>
        )}
        <label className="field-hint" htmlFor="world-image-file">Add an image</label>
        <input id="world-image-file" ref={input} type="file" disabled={busy}
               accept="image/png,image/jpeg,image/gif,image/webp"
               onChange={(e) => upload(e.target.files?.[0])} />
      </div>
    </div>
  );
}
