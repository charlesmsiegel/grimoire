import { useEffect, useState } from "react";
import { api, type Style } from "../api/client";

export function StyleConfig({ cid }: { cid: string }) {
  const [styleId, setStyleId] = useState("");
  const [styles, setStyles] = useState<Style[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getCampaignStyle(cid).then((r) => setStyleId(r.style_id)).catch(() => setStyleId(""));
    api.listStyles().then(setStyles).catch(() => setStyles([]));
  }, [cid]);

  async function save() {
    setError(null);
    try {
      await api.setCampaignStyle(cid, styleId);
      setSaved(true);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="style-config">
      {error && <div className="banner">{error}</div>}
      <label>
        Prose style
        <select aria-label="Prose style" value={styleId}
                onChange={(e) => { setStyleId(e.target.value); setSaved(false); }}>
          <option value="">— use global default —</option>
          {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      <button className="primary" onClick={save}>Save</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
