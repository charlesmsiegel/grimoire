import { PRESET_CLEAR } from "../../api/client";

/** A sampler preset for a role, a fallback or a route.
 *
 *  `""` is "no preset of its own": for a role or a fallback, that means the
 *  provider's defaults; for a route, it inherits. `allowClear` (routes) adds
 *  the sentinel that says "no preset at this scope", which stops a route
 *  inheriting one -- a different answer from `""`, so it is its own option.
 *
 *  A stored id no preset has any more is shown as missing rather than letting
 *  the select fall back to its first option, which would display a choice
 *  nobody made. */
export function PresetSelect({ value, onChange, presets, allowClear = false,
                               label = "Preset", emptyLabel = "No preset", disabled }:
  { value: string; onChange: (preset: string) => void;
    presets: { id: string; name: string }[]; allowClear?: boolean;
    /** The select's accessible name. */
    label?: string;
    /** What `""` means where this is used ("Inherit", "Provider defaults"). */
    emptyLabel?: string;
    disabled?: boolean }) {
  const dangling = value && value !== PRESET_CLEAR && !presets.some((p) => p.id === value);
  return (
    <select aria-label={label} value={value} disabled={disabled}
            onChange={(e) => onChange(e.target.value)}>
      <option value="">{emptyLabel}</option>
      {allowClear && <option value={PRESET_CLEAR}>No preset (stop inheriting)</option>}
      {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      {dangling && <option value={value}>{value} (missing preset)</option>}
    </select>
  );
}
