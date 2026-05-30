import type { NarratorResponseMode } from "../../../api/campaign/types";
import { SaveIndicator } from "./SaveIndicator";
import { useAutoSavedResource } from "./shared";

interface NarratorValue {
  response_mode: NarratorResponseMode;
}

export function NarratorTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<NarratorValue>(
    campaignId,
    "/narrator",
    { response_mode: "all_at_once" },
  );

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        How the narrator addresses the present cast on each beat. Individual scenes
        can override this default from the scene side panel.
      </p>
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Default response mode</span>
        <select
          value={value.response_mode}
          onChange={(e) =>
            setValue({
              response_mode: e.target.value as NarratorValue["response_mode"],
            })
          }
          disabled={!ready}
        >
          <option value="all_at_once">All at once — 1 LLM call, one combined post</option>
          <option value="per_character">
            Per character — 1 LLM call, split into per-character posts
          </option>
          <option value="per_character_multi_call">
            Per character, multi-call — one LLM call each, interactive (pick next speaker)
          </option>
        </select>
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
