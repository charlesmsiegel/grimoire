import { useEffect, useRef, useState } from "react";
import {
  api, type CampaignPlayDecisions as Setting, type PlayDecisionValues,
} from "../api/client";
import { errorText } from "../api/errors";
import { Field } from "./Field";

type Key = keyof PlayDecisionValues;

/** Each switch, its label, and the values a campaign may choose besides
 *  Inherit (roadmap 02 §3.6). */
const SWITCHES: { key: Key; label: string; cost: string; options: [string, string][] }[] = [
  { key: "speaker_sampling", label: "Speaker draw",
    cost: "No extra call: the pick is asked anyway.",
    options: [["on", "On"], ["off", "Off"]] },
  { key: "turn_plan", label: "Turn decision",
    cost: "One extra call before every character's reply, on the Turn planning route.",
    options: [["off", "Off"], ["intent", "Intent"], ["plan", "Plan"]] },
  { key: "play_tool", label: "Decide tool",
    cost: "Its definition rides every character's reply; a call adds a Decision call and a "
      + "second scene turn.",
    options: [["on", "On"], ["off", "Off"]] },
];

/** The campaign's own play-decision switches. "Inherit" follows the global
 *  setting, so what each one comes to is shown beside it, as the tracker's
 *  switch does. */
export function CampaignPlayDecisions({ cid }: { cid: string }) {
  const [state, setState] = useState<Setting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const live = useRef(cid);
  live.current = cid;

  useEffect(() => {
    setState(null); setError(null);
    let ok = true;
    api.getCampaignPlayDecisions(cid)
      .then((s) => { if (ok) setState(s); })
      .catch((err) => { if (ok) setError(errorText(err)); });
    return () => { ok = false; };
  }, [cid]);

  async function choose(key: Key, value: string) {
    const mine = cid;
    setSaving(true); setError(null);
    try {
      const next = await api.setCampaignPlayDecisions(cid, { [key]: value });
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
      <p className="field-hint">
        Each decision is answered by the Decision model, and reads the replying character's own
        card summary, which goes to that model's provider. Settings, Play decisions says more.
      </p>
      {SWITCHES.map(({ key, label, cost, options }) => (
        <Field key={key} label={label}
               hint={(state ? `Currently ${state.effective[key]}${state[key] === "" ? " (following the global setting)" : ""}. ` : "") + cost}>
          <select aria-label={label} value={state?.[key] ?? ""} disabled={!state || saving}
                  onChange={(e) => void choose(key, e.target.value)}>
            <option value="">Inherit</option>
            {options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
          </select>
        </Field>
      ))}
    </div>
  );
}
