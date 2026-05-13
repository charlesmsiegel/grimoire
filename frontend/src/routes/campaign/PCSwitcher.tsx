import type { PCEntry } from "../../api/campaign";

interface Props {
  pcs: PCEntry[];
  activePcRef: string | null;
  onChange: (ref: string) => void;
}

export function PCSwitcher({ pcs, activePcRef, onChange }: Props) {
  if (pcs.length === 0) {
    return (
      <div className="pc-switcher pc-switcher-empty" aria-live="polite">
        No PCs in this campaign yet.
      </div>
    );
  }

  return (
    <label className="pc-switcher">
      <span className="pc-switcher-label">Active PC</span>
      <select
        value={activePcRef ?? ""}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Active PC"
      >
        {pcs.map((pc) => (
          <option key={pc.character_ref} value={pc.character_ref}>
            {pc.name}
          </option>
        ))}
      </select>
    </label>
  );
}
