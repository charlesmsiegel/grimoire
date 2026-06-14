import { useCallback } from "react";

import { viewsApi } from "../../../api/views";
import { useResource } from "../../../api/useResource";
import { SaveIndicator } from "./SaveIndicator";
import { useAutoSavedResource } from "./shared";

interface ExpressionsConfig {
  enabled_characters: string[];
}

export function ExpressionsTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<ExpressionsConfig>(
    campaignId,
    "/expressions",
    { enabled_characters: [] },
  );

  const charactersState = useResource(
    useCallback(() => viewsApi.listCharacters(campaignId), [campaignId]),
  );
  const characters = charactersState.data ?? [];
  const loadError = charactersState.error?.message ?? null;

  const enabledSet = new Set(value.enabled_characters);

  const toggle = useCallback(
    (charId: string) => {
      setValue((prev) => {
        const s = new Set(prev.enabled_characters);
        if (s.has(charId)) {
          s.delete(charId);
        } else {
          s.add(charId);
        }
        return { enabled_characters: [...s] };
      });
    },
    [setValue],
  );

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Enable expression sprites per character. Characters with expressions disabled will show
        their name instead of a sprite. Expressions are off by default.
      </p>

      {loadError && (
        <p className="wizard-error" role="alert">
          Failed to load characters: {loadError}
        </p>
      )}

      {characters.length === 0 && !loadError && (
        <p className="wizard-meta">No characters found in this campaign.</p>
      )}

      <ul className="expressions-character-list" style={{ listStyle: "none", padding: 0 }}>
        {characters.map((rc) => (
          <li key={rc.character.id}>
            <label className="form-field wizard-field wizard-field-inline">
              <input
                type="checkbox"
                checked={enabledSet.has(rc.character.id)}
                onChange={() => toggle(rc.character.id)}
                disabled={!ready}
              />
              <span>
                {rc.character.name}
                <small style={{ opacity: 0.6, marginLeft: "0.5em" }}>{rc.character.role}</small>
              </span>
            </label>
          </li>
        ))}
      </ul>

      <SaveIndicator status={status} error={error} />
    </div>
  );
}
