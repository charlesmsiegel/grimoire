import { useEffect, useRef, useState } from "react";
import { api, type CampaignTrackerSetting, type TrackerSetting } from "../../api/client";
import { errorText } from "../../api/errors";
import { Field } from "../Field";

/** The campaign's own tracker switch. "Inherit" follows the global setting, so
 *  the resulting state is shown beside it: a reader choosing between the three
 *  should not have to go and find out what Inherit currently means. */
export function CampaignTrackerSwitch({ cid }: { cid: string }) {
  const [state, setState] = useState<CampaignTrackerSetting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const live = useRef(cid);
  live.current = cid;

  useEffect(() => {
    setState(null); setError(null);
    let ok = true;
    api.getCampaignTracker(cid)
      .then((s) => { if (ok) setState(s); })
      .catch((err) => { if (ok) setError(errorText(err)); });
    return () => { ok = false; };
  }, [cid]);

  async function choose(setting: TrackerSetting) {
    const mine = cid;
    setSaving(true); setError(null);
    try {
      const next = await api.setCampaignTracker(cid, setting);
      if (live.current === mine) setState(next);
    } catch (err) {
      if (live.current === mine) setError(errorText(err));
    } finally {
      if (live.current === mine) setSaving(false);
    }
  }

  return (
    <div className="tracker-switch">
      {error && <div className="banner" role="alert">{error}</div>}
      <Field label="Tracker"
             hint={state ? `Currently ${state.enabled ? "on" : "off"}${state.setting === "" ? " (following the global setting)" : ""}.` : undefined}>
        <select aria-label="Tracker" value={state?.setting ?? ""} disabled={!state || saving}
                onChange={(e) => void choose(e.target.value as TrackerSetting)}>
          <option value="">Inherit</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
      </Field>
    </div>
  );
}
