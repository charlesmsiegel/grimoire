import { useState } from "react";

export interface ProfileFieldValues {
  description: string;
  goals: string[];
  playerNotes: string;
}

interface Props {
  values: ProfileFieldValues;
  onChange: (values: ProfileFieldValues) => void;
  defaultExpanded?: boolean;
}

export function PCProfileFields({ values, onChange, defaultExpanded = false }: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const updateGoal = (index: number, text: string) => {
    const next = [...values.goals];
    next[index] = text;
    onChange({ ...values, goals: next });
  };

  const addGoal = () => {
    onChange({ ...values, goals: [...values.goals, ""] });
  };

  const removeGoal = (index: number) => {
    onChange({ ...values, goals: values.goals.filter((_, i) => i !== index) });
  };

  return (
    <details
      className="pc-profile-fields"
      open={expanded}
      onToggle={(e) => setExpanded((e.target as HTMLDetailsElement).open)}
    >
      <summary>PC Profile</summary>
      <div className="pc-profile-fields-body">
        <label htmlFor="pc-profile-desc">Description</label>
        <textarea
          id="pc-profile-desc"
          value={values.description}
          onChange={(e) => onChange({ ...values, description: e.target.value })}
          placeholder="Appearance, personality, backstory..."
          rows={4}
        />

        <label>Goals</label>
        {values.goals.map((goal, i) => (
          <div key={i} className="pc-profile-goal-row">
            <input
              type="text"
              value={goal}
              onChange={(e) => updateGoal(i, e.target.value)}
              placeholder="What does this character want?"
            />
            <button type="button" onClick={() => removeGoal(i)}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" onClick={addGoal}>
          Add goal
        </button>

        <label htmlFor="pc-profile-notes">Player Notes</label>
        <textarea
          id="pc-profile-notes"
          value={values.playerNotes}
          onChange={(e) => onChange({ ...values, playerNotes: e.target.value })}
          placeholder="Guidance for the narrator — tone, themes, things to avoid..."
          rows={3}
        />
      </div>
    </details>
  );
}
