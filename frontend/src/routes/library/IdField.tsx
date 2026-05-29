import { useState } from "react";

import { slugify } from "./slugify";

/**
 * Name input with a derived, editable id. The id auto-follows the name until
 * the user edits the id directly, after which it "sticks".
 */
export function IdField({
  nameLabel,
  name,
  id,
  onNameChange,
  onIdChange,
}: {
  nameLabel: string;
  name: string;
  id: string;
  onNameChange: (next: string) => void;
  onIdChange: (next: string) => void;
}) {
  const [touched, setTouched] = useState(false);
  return (
    <>
      <label>
        <span>{nameLabel}</span>
        <input
          required
          value={name}
          onChange={(e) => {
            onNameChange(e.target.value);
            if (!touched) onIdChange(slugify(e.target.value));
          }}
        />
      </label>
      <label>
        <span>ID</span>
        <input
          required
          value={id}
          pattern="[a-z0-9][a-z0-9-]*"
          title="lowercase letters, digits, and hyphens"
          onChange={(e) => {
            setTouched(true);
            onIdChange(e.target.value);
          }}
        />
      </label>
    </>
  );
}
