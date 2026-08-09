import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

/** The campaign's cover image: shown on the campaigns list and used as the
 *  cover of the exported EPUB. A settings panel (the CalendarConfig shape),
 *  not a list/detail editor — there is one image or none. */
export function CampaignCover({ cid }: { cid: string }) {
  const [version, setVersion] = useState<string | null>(null);  // null = loading
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setVersion(null);
    api.getCampaign(cid)
      .then((r) => setVersion(r.meta.cover ?? ""))
      .catch(() => setVersion(""));
  }, [cid]);

  async function upload(file: File) {
    setError(null);
    setBusy(true);
    try {
      const r = await api.putCampaignCover(cid, file);
      setVersion(r.v);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";  // re-picking the same file re-fires
    }
  }

  async function remove() {
    setError(null);
    setBusy(true);
    try {
      await api.deleteCampaignCover(cid);
      setVersion("");
    } catch (err: any) {
      // The backend confirms the unlink, so a failure means the cover is
      // genuinely still there — leave it on screen.
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  if (version === null) return <div className="field-hint">Loading cover…</div>;

  return (
    <div className="campaign-cover">
      {error && <div className="banner">{error}</div>}
      {version
        ? <img className="cover-preview" src={api.campaignCoverUrl(cid, { v: version })} alt="Campaign cover" />
        : <p className="field-hint">No cover set. It is used on the campaigns list and as the cover of the exported EPUB.</p>}
      <label className="field-hint" htmlFor="campaign-cover-file">
        {version ? "Replace cover image" : "Cover image"}
      </label>
      <input id="campaign-cover-file" ref={input} type="file" disabled={busy}
             accept="image/png,image/jpeg,image/gif,image/webp"
             onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
      {version && (
        <button className="subtle" disabled={busy} onClick={remove}>Remove cover</button>
      )}
    </div>
  );
}
