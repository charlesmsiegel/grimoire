import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

/** The campaign's cover image: shown on the campaigns list and used as the
 *  cover of the exported EPUB. A settings panel (the CalendarConfig shape),
 *  not a list/detail editor — there is one image or none. */
export function CampaignCover({ cid }: { cid: string }) {
  const [version, setVersion] = useState<string | null>(null);  // null = loading
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The version whose image failed to load, so a cover removed in another tab
  // falls back to the placeholder instead of the browser's broken-image glyph
  // sitting next to a "Remove cover" button. Keyed by version, not a bare
  // boolean, for the reason CampaignsView's `broken` map is: a replacement
  // uploaded afterwards must not inherit the failure.
  const [broken, setBroken] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  // Which campaign this panel is currently showing. CampaignView reuses one
  // CampaignCover across campaign navigation, so every await below can resolve
  // after the reader has moved on — and applying campaign A's result to B
  // would show B `/campaigns/B/cover?v=<A's version>`, clear B's cover UI, or
  // leave B's controls disabled until A's mutation settles. Same guard shape
  // as NewSceneChooser's `live` ref: ignore any resolution whose cid is no
  // longer current, and reset the panel's own state when cid changes.
  const live = useRef(cid);

  useEffect(() => {
    live.current = cid;
    setVersion(null);
    // Reset the rest of the panel too, not just `version`. `busy` is the one
    // that bites: an upload or a remove in flight for the abandoned campaign
    // owns the only `setBusy(false)` for it, and that call is now suppressed
    // by the guard below — so without this the NEW campaign's freshly loaded
    // panel would sit with its file input and Remove button disabled forever.
    setBusy(false);
    setError(null);
    setBroken(null);
    const mine = cid;
    api.getCampaign(cid)
      .then((r) => { if (live.current === mine) setVersion(r.meta.cover ?? ""); })
      .catch(() => { if (live.current === mine) setVersion(""); });
  }, [cid]);

  async function upload(file: File) {
    const mine = cid;
    setError(null);
    setBusy(true);
    try {
      const r = await api.putCampaignCover(cid, file);
      if (live.current !== mine) return;   // the reader switched campaigns mid-upload
      setBroken(null);
      setVersion(r.v);
    } catch (err: any) {
      if (live.current !== mine) return;
      setError(err.detail ?? String(err));
    } finally {
      // Both of these are the abandoned campaign's state, not the new one's:
      // the cid-change effect has already reset them for whoever is on screen.
      if (live.current === mine) {
        setBusy(false);
        if (input.current) input.current.value = "";  // re-picking the same file re-fires
      }
    }
  }

  async function remove() {
    const mine = cid;
    setError(null);
    setBusy(true);
    try {
      await api.deleteCampaignCover(cid);
      if (live.current !== mine) return;
      setVersion("");
    } catch (err: any) {
      if (live.current !== mine) return;
      // The backend confirms the unlink, so a failure means the cover is
      // genuinely still there — leave it on screen.
      setError(err.detail ?? String(err));
    } finally {
      if (live.current === mine) setBusy(false);
    }
  }

  if (version === null) return <div className="field-hint">Loading cover…</div>;

  // A cover whose image would not load is treated as no cover by the WHOLE
  // panel, not just the <img>: the case this covers is one removed in another
  // tab, and a placeholder sitting beside a live "Remove cover" button would
  // be the same contradiction as the broken-image glyph was. Uploading a
  // replacement is the recovery either way, and a genuinely-present cover that
  // failed to load transiently comes back on the next mount.
  const hasCover = Boolean(version) && broken !== version;

  return (
    <div className="campaign-cover">
      {error && <div className="banner">{error}</div>}
      {hasCover
        ? <img className="cover-preview" src={api.campaignCoverUrl(cid, { v: version })}
               alt="Campaign cover" onError={() => setBroken(version)} />
        : <p className="field-hint">No cover set. It is used on the campaigns list and as the cover of the exported EPUB.</p>}
      <label className="field-hint" htmlFor="campaign-cover-file">
        {hasCover ? "Replace cover image" : "Cover image"}
      </label>
      <input id="campaign-cover-file" ref={input} type="file" disabled={busy}
             accept="image/png,image/jpeg,image/gif,image/webp"
             onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
      {hasCover && (
        <button className="subtle" disabled={busy} onClick={remove}>Remove cover</button>
      )}
    </div>
  );
}
