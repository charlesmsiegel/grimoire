/**
 * Shared greeting form fields — used by both the create form in
 * EntityListView and the editor in EntityEditorView. A greeting is more
 * than a name: per spec 18 §Greetings it bundles the opening narration
 * (`body`, seeded as the first scene post), the NPCs present at the
 * opening (`present_characters`), the POV character, plus location,
 * time, mood, and tags.
 */

import { useEffect, useState } from "react";

import { type LibraryEntity, libraryApi } from "../../api/library";
import type { GreetingFormValue } from "./greeting-form";

interface CharacterOption {
  assetId: string;
  name: string;
}

interface Props {
  worldId: string;
  value: GreetingFormValue;
  onChange: (next: GreetingFormValue) => void;
  /** When true, suppresses the Name field (caller renders it separately, e.g.
   *  alongside an ID input on the create form). */
  hideName?: boolean;
}

export function GreetingFormFields({ worldId, value, onChange, hideName = false }: Props) {
  const [characters, setCharacters] = useState<CharacterOption[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadErr(null);
    libraryApi
      .listEntities(worldId, "characters")
      .then((rows) => {
        if (cancelled) return;
        const opts = (rows as LibraryEntity[])
          .map((c) => ({ assetId: c.asset_id, name: c.name || c.asset_id }))
          .sort((a, b) => a.name.localeCompare(b.name));
        setCharacters(opts);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadErr(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [worldId]);

  function patch<K extends keyof GreetingFormValue>(key: K, next: GreetingFormValue[K]) {
    onChange({ ...value, [key]: next });
  }

  function toggleCharacter(assetId: string) {
    const has = value.presentCharacters.includes(assetId);
    const next = has
      ? value.presentCharacters.filter((c) => c !== assetId)
      : [...value.presentCharacters, assetId];
    onChange({ ...value, presentCharacters: next });
  }

  return (
    <>
      {!hideName && (
        <label>
          <span>Name</span>
          <input required value={value.name} onChange={(e) => patch("name", e.target.value)} />
        </label>
      )}
      <label>
        <span>Tags</span>
        <input
          value={value.tagsText}
          onChange={(e) => patch("tagsText", e.target.value)}
          placeholder="comma, separated"
        />
      </label>
      <label>
        <span>Body</span>
        <textarea
          required
          value={value.body}
          rows={10}
          onChange={(e) => patch("body", e.target.value)}
          placeholder="Opening narration. Seeded verbatim as the first post when this greeting starts a campaign."
        />
        <small>Markdown — becomes the first post of scene 1.</small>
      </label>
      <fieldset className="greeting-character-picker">
        <legend>Present characters</legend>
        <small>NPCs and PCs present when the scene opens.</small>
        {loadErr && (
          <p className="library-error" role="alert">
            {loadErr}
          </p>
        )}
        {characters.length === 0 && !loadErr && (
          <p className="library-empty">
            <em>This world has no characters yet. Create some under the Characters tab.</em>
          </p>
        )}
        {characters.length > 0 && (
          <ul className="greeting-character-list">
            {characters.map((c) => (
              <li key={c.assetId}>
                <label>
                  <input
                    type="checkbox"
                    checked={value.presentCharacters.includes(c.assetId)}
                    onChange={() => toggleCharacter(c.assetId)}
                  />
                  <span>{c.name}</span>
                  <small>{c.assetId}</small>
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>
      <label>
        <span>POV character</span>
        <select value={value.povCharacter} onChange={(e) => patch("povCharacter", e.target.value)}>
          <option value="">(none)</option>
          {characters.map((c) => (
            <option key={c.assetId} value={c.assetId}>
              {c.name} ({c.assetId})
            </option>
          ))}
        </select>
        <small>Whose viewpoint the opening narration is written from. Optional.</small>
      </label>
      <label>
        <span>Starting location</span>
        <input
          value={value.startingLocation}
          onChange={(e) => patch("startingLocation", e.target.value)}
          placeholder="location asset id, e.g. classroom-2-b"
        />
      </label>
      <label>
        <span>Starting time</span>
        <input
          value={value.startingTime}
          onChange={(e) => patch("startingTime", e.target.value)}
          placeholder="ISO 8601 in the world's calendar, e.g. 2025-04-15T08:32:00"
        />
      </label>
      <label>
        <span>Mood</span>
        <input
          value={value.mood}
          onChange={(e) => patch("mood", e.target.value)}
          placeholder="atmospheric note woven into the opening prompt"
        />
      </label>
    </>
  );
}
