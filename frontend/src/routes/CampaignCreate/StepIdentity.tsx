import { useState } from "react";

import type { WizardDraft } from "./types";
import { slugify } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  idEdited: boolean;
  setIdEdited: (edited: boolean) => void;
}

export function StepIdentity({ draft, update, idEdited, setIdEdited }: Props) {
  // Raw input string owned locally so commas and trailing spaces survive each
  // keystroke. Deriving the displayed value from draft.tags.join(", ") would
  // snap the cursor back the moment the user types ",".
  const [tagsInput, setTagsInput] = useState(() => draft.tags.join(", "));
  return (
    <div className="wizard-step">
      <h3>Step 1 — Identity</h3>
      <p className="wizard-step-help">Name your campaign. The id derives from the name.</p>

      <label className="wizard-field">
        <span>Name</span>
        <input
          type="text"
          value={draft.name}
          onChange={(e) => {
            const name = e.target.value;
            const patch: Partial<WizardDraft> = { name };
            if (!idEdited) patch.id = slugify(name);
            update(patch);
          }}
          placeholder="By Night, London"
          autoFocus
        />
      </label>

      <label className="wizard-field">
        <span>Id</span>
        <input
          type="text"
          value={draft.id}
          onChange={(e) => {
            setIdEdited(true);
            update({ id: slugify(e.target.value) });
          }}
          placeholder="by-night-london"
          aria-describedby="wizard-id-help"
        />
        <small id="wizard-id-help">Lowercase, hyphen-separated. Used in URLs and file paths.</small>
      </label>

      <label className="wizard-field">
        <span>Description</span>
        <textarea
          value={draft.description}
          onChange={(e) => update({ description: e.target.value })}
          rows={4}
          placeholder="An optional summary of the chronicle."
        />
      </label>

      <label className="wizard-field">
        <span>Tags</span>
        <input
          type="text"
          value={tagsInput}
          onChange={(e) => {
            const next = e.target.value;
            setTagsInput(next);
            update({
              tags: next
                .split(",")
                .map((t) => t.trim())
                .filter(Boolean),
            });
          }}
          placeholder="vampire, gothic, london"
        />
        <small>Comma-separated.</small>
      </label>
    </div>
  );
}
